"""Read-only, single-submission PostgreSQL Event journey compilation."""

from __future__ import annotations

from dataclasses import dataclass

import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import pyarrow as pa
from ibis.backends.postgres import Backend
from ibis.backends.sql.compilers.postgres import PostgresCompiler
from sqlglot import expressions as sge

from marivo.analysis.compiler.nodes import (
    CompiledDataset,
    CompiledRelationFence,
    CompiledValidation,
)


class _EventCompiler(PostgresCompiler):  # type: ignore[misc]  # Ibis compiler lacks typing.
    """Compile governed records and exact journey digests on PostgreSQL."""

    def _named_json(self, node: ops.Value, compiled: sge.Expression) -> sge.Expression:
        if not isinstance(node, ops.StructColumn):
            return compiled
        fields = [
            sge.alias_(self._named_json(value, part), name, quoted=True)
            for name, value, part in zip(node.names, node.values, compiled.expressions, strict=True)
        ]
        selected = sge.select(*fields).subquery(alias="_mv_json")
        return sge.Subquery(
            this=sge.select(self.f.row_to_json(sge.to_identifier("_mv_json"))).from_(selected)
        )

    def visit_StructColumn(  # noqa: N802 - Ibis visitor names are fixed.
        self, op: ops.StructColumn, *, names: tuple[str, ...], values: tuple[sge.Expression, ...]
    ) -> sge.Expression:
        result: sge.Expression = self.f.row(*values)
        return result

    def visit_HexDigest(  # noqa: N802 - Ibis visitor names are fixed.
        self, op: ops.HexDigest, *, arg: sge.Expression, how: str
    ) -> sge.Expression:
        if how != "sha256":
            raise NotImplementedError(f"PostgreSQL Event digest: {how}")
        result: sge.Expression = self.f.encode(self.f.sha256(self.f.convert_to(arg, "UTF8")), "hex")
        return result

    def visit_StructField(  # noqa: N802 - Ibis visitor names are fixed.
        self, op: ops.StructField, *, arg: sge.Expression, field: str
    ) -> sge.Expression:
        if op.dtype != dt.int64:
            standard: sge.Expression = super().visit_StructField(op, arg=arg, field=field)
            return standard
        index = op.arg.dtype.names.index(field) + 1
        raw = self.f.jsonb_extract_path(self.f.to_jsonb(arg), f"f{index}")
        nullable = self.f.nullif(self.cast(raw, dt.string), "null")
        result: sge.Expression = self.cast(nullable, op.dtype)
        return result

    def visit_Cast(  # noqa: N802 - Ibis visitor names are fixed.
        self, op: ops.Cast, *, arg: sge.Expression, to: dt.DataType
    ) -> sge.Expression:
        if to.is_struct():
            return arg
        if to.is_json() and op.arg.dtype.is_struct():
            return self._named_json(op.arg, arg)
        if op.arg.dtype.is_boolean() and to.is_integer():
            result: sge.Expression = self.cast(self.cast(arg, dt.int32), to)
            return result
        standard: sge.Expression = super().visit_Cast(op, arg=arg, to=to)
        return standard


class _EventBackend(Backend):  # type: ignore[misc]  # Ibis backend lacks typing.
    compiler = _EventCompiler()


@dataclass(frozen=True, slots=True)
class EventBundleSQL:
    """One source statement with ordered validation, proof and primary packets."""

    sql: str
    validations: tuple[CompiledValidation, ...]
    primary_schema: pa.Schema
    proof_schema: pa.Schema


def compile_event_bundle(recipe: CompiledDataset, *, step_keys: tuple[str, ...]) -> EventBundleSQL:
    """Keep every Event fence materialized once within one read-only SELECT."""
    if recipe.event_proof is None or not step_keys:
        raise ValueError("Event journey bundle requires its source proof and Pattern steps")
    compiler = _EventBackend()
    preparations = recipe.preparations or recipe.validations
    fences = tuple(item for item in preparations if isinstance(item, CompiledRelationFence))
    checks = tuple(
        item
        for item in preparations
        if isinstance(item, CompiledValidation) and item.name != "event.journey.output"
    )

    def quote(name: str) -> str:
        return sge.to_identifier(name, quoted=True).sql(dialect="postgres")

    ctes = [
        f"{quote(item.relation_name)} AS MATERIALIZED ({compiler.compile(item.expression)})"
        for item in fences
    ]
    ctes.append(f"_mv_primary AS MATERIALIZED ({compiler.compile(recipe.expression)})")
    ctes.extend(
        f"_mv_validation_{index} AS ({compiler.compile(check.expression)})"
        for index, check in enumerate(checks)
    )
    ctes.append(f"_mv_proof AS ({compiler.compile(recipe.event_proof)})")
    branches = [
        f"SELECT 0 AS kind, {index} AS ordinal, violations::bigint, NULL::text AS payload "
        f"FROM _mv_validation_{index}"
        for index in range(len(checks))
    ]
    branches.append(
        "SELECT 1 AS kind, 0 AS ordinal, NULL::bigint AS violations, "
        "row_to_json(proof)::text AS payload FROM _mv_proof AS proof"
    )
    step_order = (
        "CASE "
        + " ".join(
            f"WHEN primary_row.step_key = {sge.Literal.string(key).sql(dialect='postgres')} "
            f"THEN {index}"
            for index, key in enumerate(step_keys)
        )
        + f" ELSE {len(step_keys)} END"
    )
    branches.append(
        "SELECT 2 AS kind, row_number() OVER (ORDER BY primary_row.entity_identity, "
        f"anchor.occurred_at, anchor.event_identity, {step_order}) AS ordinal, "
        "NULL::bigint AS violations, row_to_json(primary_row)::text AS payload "
        "FROM _mv_primary AS primary_row JOIN _mv_primary AS anchor "
        "ON primary_row.journey_id = anchor.journey_id AND anchor.step_key = "
        f"{sge.Literal.string(step_keys[0]).sql(dialect='postgres')}"
    )
    sql = "WITH " + ", ".join(ctes) + " " + " UNION ALL ".join(branches)
    sql += " ORDER BY kind, ordinal"
    return EventBundleSQL(
        sql,
        checks,
        recipe.expression.schema().to_pyarrow(),
        recipe.event_proof.schema().to_pyarrow(),
    )
