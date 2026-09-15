"""Test-only multi-source feasibility probes; never registered with Analysis."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir
import pyarrow as pa
import sqlglot as sg
from ibis.backends import BaseBackend
from ibis.backends.sql.compilers.trino import TrinoCompiler
from ibis.backends.sql.datatypes import ClickHouseType

from marivo.analysis.compiler import compile_dataset
from marivo.analysis.compiler.nodes import CompiledDataset, RetainedPartSpec
from marivo.analysis.compiler.normalize import required_entities
from marivo.analysis.observation.predicates import gt
from marivo.analysis.session._lazy_sources import make_lazy_sources
from marivo.refs import ref
from tests.lazy_execution_fixtures import make_execution_registry
from tests.lazy_observation_fixtures import NoIoActionPort


# Ibis ships no compiler typing metadata; the extension's own boundary is typed below.
class SnapshotCompiler(TrinoCompiler):  # type: ignore[misc]
    """Qualify one exact physical table at the Ibis relation visitor seam."""

    def __init__(self, table: ir.Table, snapshot_id: int) -> None:
        super().__init__()
        if type(snapshot_id) is not int or not 0 < snapshot_id < 2**63:
            raise ValueError("Expected a positive signed BIGINT snapshot ID")
        operation = table.op()
        if not isinstance(operation, (ops.UnboundTable, ops.DatabaseTable)):
            raise ValueError("Expected one physical Ibis table")
        self.table = operation
        self.snapshot_id = snapshot_id

    def _version(self, operation: ops.Node, table: sg.exp.Table) -> sg.exp.Table:
        if operation != self.table:
            raise ValueError("A second physical source requires separate qualification")
        table.set(
            "version",
            sg.exp.Version(
                this="VERSION",
                kind="AS OF",
                expression=sg.exp.Cast(
                    this=sg.exp.Literal.number(self.snapshot_id), to=sg.exp.DataType.build("BIGINT")
                ),
            ),
        )
        return table

    def visit_UnboundTable(
        self, op: ops.UnboundTable, *, name: str, schema: ibis.Schema, namespace: ops.Namespace
    ) -> sg.exp.Table:
        return self._version(
            op, super().visit_UnboundTable(op, name=name, schema=schema, namespace=namespace)
        )

    def visit_DatabaseTable(
        self,
        op: ops.DatabaseTable,
        *,
        name: str,
        schema: ibis.Schema,
        source: BaseBackend,
        namespace: ops.Namespace,
    ) -> sg.exp.Table:
        return self._version(
            op,
            super().visit_DatabaseTable(
                op, name=name, schema=schema, source=source, namespace=namespace
            ),
        )

    def statement(self, expression: ir.Table) -> str:
        ast: object = self.to_sqlglot(expression, limit=None)
        if not isinstance(ast, sg.exp.Expression):
            raise ValueError("Expected a SQL expression")
        return ast.sql(dialect="trino")


def qualification_recipe(
    *, catalog: str | None = None, database: str = "qualification", positive_only: bool = False
) -> tuple[ir.Table, CompiledDataset]:
    """Lower real unversioned sum/count contracts without connecting a source."""
    registry, sidecar = make_execution_registry(Path("/nonexistent/qualification.duckdb"))
    sources = make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id="qualification",
        store_id="qualification",
    )
    logical = sources.observe((ref.metric("sales.revenue"), ref.metric("sales.order_count")))
    if positive_only:
        logical = logical.where(gt(ref.metric("sales.revenue"), 0))
    logical = logical.aggregate()
    entities = required_entities(logical)
    if len(entities) != 1:
        raise ValueError("Probe requires exactly one source Entity")
    entity = entities[0]
    table = ibis.table(dict(entity.columns), name="orders", catalog=catalog, database=database)
    return table, compile_dataset(logical, {entity.ref.path: table})


def assertion_envelope(recipe: CompiledDataset, *, empty_primary: bool = False) -> ir.Table:
    """Probe post-transfer validation only; no assertion-ordering admission is implied."""
    if recipe.preparations or any(
        not isinstance(part, RetainedPartSpec) for part in recipe.retained_parts
    ):
        raise ValueError("Fences and independent private relations are outside this probe")
    schema = recipe.expression.schema()
    primary = recipe.expression.filter(ibis.literal(False)) if empty_primary else recipe.expression
    rows = primary.select(
        __q_kind=ibis.literal("primary"),
        __q_check=ibis.null().cast("int64"),
        __q_violations=ibis.null().cast("int64"),
        **{name: primary[name] for name in schema},
    )
    assertions = tuple(
        check.expression.select(
            __q_kind=ibis.literal("assertion"),
            __q_check=ibis.literal(index, type="int64"),
            __q_violations=check.expression.violations.cast("int64"),
            **{name: ibis.null().cast(kind) for name, kind in schema.items()},
        )
        for index, check in enumerate(recipe.validations)
    )
    if not assertions:
        raise ValueError("A qualification envelope must carry its source assertions")
    return ibis.union(rows, *assertions, distinct=False)


def clickhouse_envelope_sql(recipe: CompiledDataset, *, empty_primary: bool = False) -> str:
    """Pin each envelope branch's physical types before UNION type inference.

    Ibis treats count as int64 while ClickHouse returns UInt64. An Ibis no-op
    cast disappears, allowing the server to infer Variant(Int64, UInt64).
    This private AST projection makes the bounded probe's wire schema explicit.
    """
    envelope = assertion_envelope(recipe, empty_primary=empty_primary)
    schema = envelope.schema()
    ast = sg.parse_one(str(ibis.to_sql(envelope, dialect="clickhouse")), read="clickhouse")
    branches = 0
    for select in ast.find_all(sg.exp.Select):
        if tuple(select.named_selects) != schema.names:
            continue
        select.set(
            "expressions",
            [
                sg.exp.alias_(
                    sg.exp.Cast(this=column.unalias(), to=ClickHouseType.from_ibis(schema[name])),
                    name,
                    quoted=True,
                )
                for name, column in zip(schema.names, select.expressions, strict=True)
            ],
        )
        branches += 1
    if branches != 1 + len(recipe.validations):
        raise ValueError("Expected exactly one typed projection per envelope branch")
    return ast.sql(dialect="clickhouse")


@dataclass(frozen=True)
class DecodedEnvelope:
    primary: pa.Table
    parts: tuple[tuple[str, pa.Table], ...]


def decode_envelope(table: pa.Table, recipe: CompiledDataset) -> DecodedEnvelope:
    """Consume a bounded probe envelope completely before exposing any result."""
    expected = assertion_envelope(recipe).schema().to_pyarrow()
    if table.num_rows > 1024 or table.nbytes > 1_048_576 or table.schema != expected:
        raise ValueError("Unexpected envelope schema or probe budget exceeded")
    seen: set[int] = set()
    primary_indices: list[int] = []
    for index, row in enumerate(table.to_pylist()):
        if row["__q_kind"] == "assertion":
            ordinal = row["__q_check"]
            if (
                type(ordinal) is not int
                or not 0 <= ordinal < len(recipe.validations)
                or ordinal in seen
                or type(row["__q_violations"]) is not int
                or row["__q_violations"] != 0
                or any(row[name] is not None for name in recipe.expression.columns)
            ):
                raise ValueError("Invalid, duplicated, or failing source assertion")
            seen.add(ordinal)
        elif row["__q_kind"] == "primary":
            if row["__q_check"] is not None or row["__q_violations"] is not None:
                raise ValueError("Primary record contains assertion fields")
            primary_indices.append(index)
        else:
            raise ValueError("Unknown envelope record kind")
    if seen != set(range(len(recipe.validations))):
        raise ValueError("Missing source assertion records")
    complete = table.take(pa.array(primary_indices, type=pa.int64()))
    parts: list[tuple[str, pa.Table]] = []
    for part in recipe.retained_parts:
        if not isinstance(part, RetainedPartSpec):
            raise ValueError("Independent private relations are outside this probe")
        parts.append((part.role, complete.select(part.column_names)))
    return DecodedEnvelope(complete.select(recipe.primary_columns), tuple(parts))
