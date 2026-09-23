"""One read-only ClickHouse statement for Lifecycle proofs and complete parts."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import suppress
from typing import TYPE_CHECKING

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.types as ir
import pyarrow as pa
from sqlglot import expressions as sge

from marivo.analysis.compiler.nodes import (
    CompiledDataset,
    CompiledRelationFence,
    CompiledValidation,
    RetainedRelationSpec,
)
from marivo.analysis.domains.lifecycle import ROLES, LifecycleSemantics
from marivo.analysis.materialization.event_bundle import _record
from marivo.analysis.materialization.lifecycle_codec import LifecycleEvidenceSummary
from marivo.analysis.materialization.lifecycle_integrity import integrity_sql

if TYPE_CHECKING:
    from marivo.analysis.materialization.clickhouse_execution import ClickHouseExecutionAdapter


def _quote(value: str) -> str:
    return sge.to_identifier(value, quoted=True).sql(dialect="clickhouse")


def _payload(table: ir.Table) -> str:
    values: list[str] = []
    for index, name in enumerate(table.columns):
        key = ("{" if index == 0 else ",") + '"' + name + '":'
        value = f"coalesce(toJSONString({_quote(name)}),'null')"
        dtype = table.schema()[name]
        if dtype.is_boolean():
            value = f"coalesce(toJSONString(CAST({_quote(name)} AS Nullable(Bool))),'null')"
        if isinstance(dtype, dt.Struct):
            absent = " AND ".join(
                f"isNull(tupleElement({_quote(name)},{sge.Literal.string(key).sql(dialect='clickhouse')}))"
                for key in dtype.names
            )
            value = f"if({absent},'null',{value})"
        values.extend(
            (
                sge.Literal.string(key).sql(dialect="clickhouse"),
                value,
            )
        )
    return "concat(" + ",".join((*values, "'}'")) + ")"


class LifecycleBundle:
    """Own ordered control and output packets until every part is consumed."""

    def __init__(
        self,
        adapter: ClickHouseExecutionAdapter,
        recipe: CompiledDataset,
        semantics: LifecycleSemantics,
    ) -> None:
        self.adapter = adapter
        self.tables = (
            recipe.expression,
            *(p.expression for p in recipe.retained_parts if isinstance(p, RetainedRelationSpec)),
        )
        self._next = 0
        self._pending: tuple[object, ...] | None = None
        self._closed = False
        parts = {
            p.role: p.expression
            for p in recipe.retained_parts
            if isinstance(p, RetainedRelationSpec)
        }
        if tuple(parts) != ROLES or len(self.tables) != 4 or recipe.lifecycle_coverage is None:
            raise ValueError("Lifecycle bundle requires all canonical retained parts and coverage")
        preparations = recipe.preparations or recipe.validations
        checks = tuple(p for p in preparations if isinstance(p, CompiledValidation))
        ctes = [
            f"{_quote(p.relation_name)} AS ({adapter.compile(p.expression)})"
            for p in preparations
            if isinstance(p, CompiledRelationFence)
        ]
        ctes.extend(
            f"_mv_lifecycle_output_{i} AS ({adapter.compile(table)})"
            for i, table in enumerate(self.tables)
        )
        frozen = tuple(
            ibis.table(t.schema(), name=f"_mv_lifecycle_output_{i}")
            for i, t in enumerate(self.tables)
        )
        # The exact output relations are shared by assertions, evidence and rows.
        proof = integrity_sql(
            adapter,
            frozen[0],
            dict(zip(parts, frozen[1:], strict=True)),
            semantics,
            dialect="clickhouse",
        )
        ctes.append(f"_mv_lifecycle_integrity AS ({proof})")
        branches = [
            f"SELECT 0 kind,{i} ordinal,toInt64(violations) violations,CAST(NULL AS Nullable(String)) payload FROM ({adapter.compile(check.expression)})"
            for i, check in enumerate(checks)
        ]
        branches.append(
            f"SELECT 0 kind,{len(checks)} ordinal,toInt64(violations) violations,CAST(NULL AS Nullable(String)) payload FROM _mv_lifecycle_integrity"
        )
        from marivo.analysis.compiler.lifecycle import known_through

        through = known_through(semantics, recipe.lifecycle_coverage)
        ledger = frozen[2]
        boundary = (
            ibis.literal(through, type=ledger.known_through.type())
            if through is not None
            else ibis.null().cast(ledger.known_through.type())
        )
        coverage_check = ledger.filter(~ledger.known_through.identical_to(boundary)).aggregate(
            violations=lambda t: t.count()
        )
        branches.append(
            f"SELECT 0 kind,{len(checks) + 1} ordinal,toInt64(violations) violations,CAST(NULL AS Nullable(String)) payload FROM ({adapter.compile(coverage_check)})"
        )
        h, t, c, v = (f"_mv_lifecycle_output_{i}" for i in range(4))
        counts = (
            f"SELECT count(*) FROM {h}",
            f"SELECT count(*) FROM {c}",
            f"SELECT count(*) FROM {c} WHERE classification='seeded'",
            f"SELECT count(*) FROM {c} WHERE classification='not_incepted'",
            f"SELECT count(*) FROM {c} WHERE classification='coverage_censored'",
            f"SELECT count(*) FROM {t}",
            f"SELECT count(*) FROM {v}",
            f"SELECT count(*) FROM {h} WHERE left_clipped",
        )
        branches.extend(
            f"SELECT 1 kind,{i} ordinal,toInt64(({query})) violations,CAST(NULL AS Nullable(String)) payload"
            for i, query in enumerate(counts)
        )
        orders = (
            "entity_identity,valid_from",
            "entity_identity,transition_ordinal",
            "entity_identity",
            "trigger_event_ref,trigger_event_identity",
        )
        branches.extend(
            f"SELECT {i + 2} kind,row_number() OVER (ORDER BY {orders[i]}) ordinal,CAST(NULL AS Nullable(Int64)) violations,{_payload(table)} payload FROM _mv_lifecycle_output_{i}"
            for i, table in enumerate(self.tables)
        )
        sql = (
            "WITH "
            + ",".join(ctes)
            + " SELECT * FROM ("
            + " UNION ALL ".join(branches)
            + ") ORDER BY kind,ordinal SETTINGS output_format_json_quote_64bit_integers=0,output_format_json_named_tuples_as_objects=1"
        )
        self._cursor = adapter.cursor(stream=True)
        try:
            with adapter.submission("lifecycle_bundle", sql) as receipt:
                self._receipt = receipt
                self._cursor.execute(sql)
            accepted: list[tuple[str, int]] = []
            for i, name in enumerate(
                (
                    *[x.name for x in checks],
                    "lifecycle.history_integrity",
                    "lifecycle.coverage_ledger",
                )
            ):
                if self._cursor.fetchmany(1) != [(0, i, 0, None)]:
                    raise adapter.error(
                        "zero Lifecycle assertion violations",
                        f"Lifecycle validation failed: {name}",
                        "Repair the modeled history and governed occurrence order.",
                        stage="output_validation",
                    )
                accepted.append((name, 0))
            values: list[int] = []
            for i in range(8):
                rows = self._cursor.fetchmany(1)
                if (
                    len(rows) != 1
                    or rows[0][:2] != (1, i)
                    or type(rows[0][2]) is not int
                    or rows[0][2] < 0
                ):
                    raise ValueError("invalid Lifecycle evidence packet")
                values.append(rows[0][2])
            if recipe.lifecycle_coverage is None:
                raise ValueError("missing Lifecycle coverage authority")
            self.evidence = LifecycleEvidenceSummary(recipe.lifecycle_coverage, *values)
            self.validations = tuple(accepted)
        except BaseException as error:
            if hasattr(self, "_receipt"):
                self._receipt.fail(error)
            with suppress(BaseException):
                self.close()
            raise

    def certifies(self, table: ir.Table) -> bool:
        return any(table.op() == item.op() for item in self.tables)

    def stream(self, table: ir.Table) -> LifecyclePartStream:
        index = next(i for i, item in enumerate(self.tables) if item.op() == table.op())
        return LifecyclePartStream(self, index)

    def rows(self, index: int) -> Iterator[pa.RecordBatch]:
        if self._closed or index != self._next:
            raise ValueError("Lifecycle parts must be read once in declared order")
        schema = self.tables[index].schema().to_pyarrow()
        records: list[dict[str, object]] = []
        count = 0
        try:
            while True:
                if self._pending is None:
                    packets = self._cursor.fetchmany(1)
                    self._pending = packets[0] if packets else None
                packet = self._pending
                if packet is None or packet[0] != index + 2:
                    if packet is not None and (
                        type(packet[0]) is not int or packet[0] < index + 2 or packet[0] > 5
                    ):
                        raise ValueError("invalid Lifecycle packet order")
                    break
                if len(packet) != 4 or packet[2] is not None:
                    raise ValueError("invalid Lifecycle output packet")
                count += 1
                if packet[1] != count:
                    raise ValueError("Lifecycle packet ordinal differs from its complete sequence")
                records.append(_record(packet[3], schema))
                self._pending = None
                if len(records) == 1024:
                    yield pa.RecordBatch.from_pylist(records, schema=schema)
                    records = []
            expected = (
                self.evidence.row_count,
                self.evidence.transition_count,
                self.evidence.subject_count,
                self.evidence.violation_count,
            )[index]
            if count != expected:
                raise ValueError("Lifecycle packet row count differs from its source Evidence")
            if records:
                yield pa.RecordBatch.from_pylist(records, schema=schema)
            self._next += 1
            if self._next == len(self.tables):
                if self._pending is not None:
                    raise ValueError("unexpected trailing Lifecycle packet")
                self.adapter.validate_result()
                self.close()
        except BaseException as error:
            self._receipt.fail(error)
            with suppress(BaseException):
                self.close()
            raise

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._cursor.close()


class LifecyclePartStream:
    """Stream one declared complete output without buffering another part locally."""

    def __init__(self, bundle: LifecycleBundle, index: int) -> None:
        self.bundle = bundle
        self.index = index
        self._finished = False

    @property
    def schema(self) -> pa.Schema:
        return self.bundle.tables[self.index].schema().to_pyarrow()

    def __iter__(self) -> Iterator[pa.RecordBatch]:
        yield from self.bundle.rows(self.index)
        self._finished = True

    def close(self) -> None:
        if not self._finished:
            self.bundle.close()
