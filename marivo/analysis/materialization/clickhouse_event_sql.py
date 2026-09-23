"""Native ClickHouse Event identities, assignment and source control packets."""

from __future__ import annotations

from datetime import datetime, timezone

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.types as ir
from ibis.backends.clickhouse import Backend
from ibis.backends.sql.compilers.clickhouse import ClickHouseCompiler
from sqlglot import expressions as sge

from marivo.analysis.compiler.event_time import _localize_utc
from marivo.analysis.compiler.nodes import (
    CompiledDataset,
    CompiledRelationFence,
    CompiledValidation,
)
from marivo.analysis.materialization.event_bundle import EventBundleSQL

_LOCALIZE_UTC = type(_localize_utc("UTC", ibis.timestamp("2000-01-01")).op())


class _EventCompiler(ClickHouseCompiler):  # type: ignore[misc]  # Ibis compiler lacks typing.
    def visit_IsNull(self, op: ops.IsNull, *, arg: sge.Expression) -> sge.Expression:  # noqa: N802
        if isinstance(op.arg.dtype, dt.Struct):
            return sge.Paren(
                this=sge.and_(
                    *(
                        sge.Is(this=self.f.tupleElement(arg.copy(), name), expression=sge.Null())
                        for name in op.arg.dtype.names
                    )
                )
            )
        result: sge.Expression = super().visit_IsNull(op, arg=arg)
        return result

    def visit_NotNull(self, op: ops.NotNull, *, arg: sge.Expression) -> sge.Expression:  # noqa: N802
        if isinstance(op.arg.dtype, dt.Struct):
            return sge.Not(
                this=sge.Paren(
                    this=sge.and_(
                        *(
                            sge.Is(
                                this=self.f.tupleElement(arg.copy(), name), expression=sge.Null()
                            )
                            for name in op.arg.dtype.names
                        )
                    )
                )
            )
        result: sge.Expression = super().visit_NotNull(op, arg=arg)
        return result

    def visit_Lead(  # noqa: N802
        self,
        op: ops.Lead,
        *,
        arg: sge.Expression,
        offset: sge.Expression | None,
        default: sge.Expression | None,
    ) -> sge.Expression:
        # ClickHouse otherwise returns the non-null scalar type's default value
        # beyond the window; Lifecycle exit references must remain SQL NULL.
        if default is None and not isinstance(op.arg.dtype, (dt.Struct, dt.Array, dt.Map)):
            arg = self.f.toNullable(arg)
        result: sge.Expression = super().visit_Lead(op, arg=arg, offset=offset, default=default)
        return result

    def visit_InValues(  # noqa: N802
        self, op: ops.InValues, *, value: sge.Expression, options: tuple[sge.Expression, ...]
    ) -> sge.Expression:
        return sge.Paren(this=value.isin(*options))

    def visit_CountStar(  # noqa: N802
        self, op: ops.CountStar, *, arg: sge.Expression, where: sge.Expression | None
    ) -> sge.Expression:
        result: sge.Expression = super().visit_CountStar(op, arg=arg, where=where)
        result = self.cast(result, dt.int64)
        return result

    def visit_NonNullLiteral(  # noqa: N802
        self, op: ops.Literal, *, value: object, dtype: dt.DataType
    ) -> sge.Expression:
        if isinstance(dtype, dt.Timestamp):
            if not isinstance(value, datetime):
                raise ValueError("Event timestamp literal requires a datetime")
            if value.tzinfo is not None:
                value = value.astimezone(timezone.utc).replace(tzinfo=None)
            result: sge.Expression = self.f.toDateTime64(value.isoformat(sep=" "), 6, "UTC")
            return result
        standard: sge.Expression = super().visit_NonNullLiteral(op, value=value, dtype=dtype)
        return standard

    def visit_StructColumn(  # noqa: N802 - Ibis visitor names are fixed.
        self, op: ops.StructColumn, *, names: tuple[str, ...], values: tuple[sge.Expression, ...]
    ) -> sge.Expression:
        result: sge.Expression = self.cast(self.f.tuple(*values), op.dtype)
        return result

    def visit_Cast(  # noqa: N802
        self, op: ops.Cast, *, arg: sge.Expression, to: dt.DataType
    ) -> sge.Expression:
        if to.is_json() and op.arg.dtype.is_struct():
            result: sge.Expression = self.f.toJSONString(arg)
            return result
        if to.is_struct():
            return arg
        if isinstance(to, dt.Timestamp) and to.scale is None:
            to = to.copy(scale=6)
        standard: sge.Expression = super().visit_Cast(op, arg=arg, to=to)
        return standard

    def visit_HexDigest(  # noqa: N802
        self, op: ops.HexDigest, *, arg: sge.Expression, how: str
    ) -> sge.Expression:
        if how != "sha256":
            raise NotImplementedError(f"ClickHouse Event digest: {how}")
        result: sge.Expression = self.f.lower(self.f.hex(self.f.SHA256(arg)))
        return result

    def visit_ScalarUDF(  # noqa: N802
        self, op: ops.ScalarUDF, **kwargs: sge.Expression
    ) -> sge.Expression:
        if isinstance(op, _LOCALIZE_UTC):
            values = tuple(kwargs.values())
            result: sge.Expression = self.f.toDateTime64(values[1], 6, "UTC")
            return result
        standard: sge.Expression = super().visit_ScalarUDF(op, **kwargs)
        return standard


class _EventBackend(Backend):  # type: ignore[misc]  # Ibis backend lacks typing.
    compiler = _EventCompiler()


def compile_event_expression(expression: ir.Expr) -> str:
    from marivo.analysis.materialization.temporal_sql import lower_temporal

    result: str = _EventBackend().compile(lower_temporal(expression, "clickhouse"))
    return result


def compile_event_bundle(recipe: CompiledDataset, *, step_keys: tuple[str, ...]) -> EventBundleSQL:
    proof = recipe.event_proof
    if proof is None or not step_keys:
        raise ValueError("Event journey bundle requires its source proof and Pattern steps")
    compiler = _EventBackend()

    def compile_table(expression: ir.Table) -> str:
        from marivo.analysis.materialization.temporal_sql import lower_temporal

        result: str = compiler.compile(lower_temporal(expression, "clickhouse"))
        return result

    preparations = recipe.preparations or recipe.validations
    fences = tuple(item for item in preparations if isinstance(item, CompiledRelationFence))
    checks = tuple(
        item
        for item in preparations
        if isinstance(item, CompiledValidation) and item.name != "event.journey.output"
    )

    def quote(value: str) -> str:
        return sge.to_identifier(value, quoted=True).sql(dialect="clickhouse")

    def payload(expression: ir.Table, alias: str) -> str:
        schema = expression.schema()
        parts: list[str] = []
        for index, name in enumerate(schema.names):
            key = ("{" if index == 0 else ",") + '"' + name + '":'
            parts.extend(
                (
                    sge.Literal.string(key).sql(dialect="clickhouse"),
                    f"coalesce(toJSONString({alias}.{quote(name)}), 'null')",
                )
            )
        parts.append("'}'")
        return "concat(" + ", ".join(parts) + ")"

    ctes = []
    materialized = "MATERIALIZED "
    for item in fences:
        ctes.append(
            f"{quote(item.relation_name)} AS {materialized}({compile_table(item.expression)})"
        )
    ctes.append(f"_mv_primary AS {materialized}({compile_table(recipe.expression)})")
    ctes.extend(
        f"_mv_validation_{index} AS ({compile_table(check.expression)})"
        for index, check in enumerate(checks)
    )
    ctes.append(f"_mv_proof AS ({compile_table(proof)})")
    branches = [
        f"SELECT 0 AS kind, {index} AS ordinal, toInt64(violations) AS violations, "
        f"CAST(NULL AS Nullable(String)) AS payload FROM _mv_validation_{index}"
        for index in range(len(checks))
    ]
    branches.append(
        "SELECT 1 AS kind, 0 AS ordinal, CAST(NULL AS Nullable(Int64)) AS violations, "
        f"{payload(proof, 'proof')} AS payload FROM _mv_proof AS proof"
    )
    order = (
        "CASE "
        + " ".join(
            f"WHEN primary_row.step_key = {sge.Literal.string(key).sql(dialect='clickhouse')} THEN {index}"
            for index, key in enumerate(step_keys)
        )
        + f" ELSE {len(step_keys)} END"
    )
    primary = (
        "SELECT 2 AS kind, row_number() OVER (ORDER BY primary_row.entity_identity, "
        f"anchor.occurred_at, anchor.event_identity, {order}) AS ordinal, "
        f"CAST(NULL AS Nullable(Int64)) AS violations, {payload(recipe.expression, 'primary_row')} AS payload "
        "FROM _mv_primary AS primary_row JOIN _mv_primary AS anchor "
        "ON primary_row.journey_id = anchor.journey_id AND anchor.step_key = "
        f"{sge.Literal.string(step_keys[0]).sql(dialect='clickhouse')}"
    )
    branches.append(primary)
    sql = "WITH " + ", ".join(ctes) + " SELECT * FROM (" + " UNION ALL ".join(branches) + ")"
    sql += " ORDER BY kind, ordinal SETTINGS output_format_json_quote_64bit_integers=0"
    sql += ", enable_materialized_cte=1"
    return EventBundleSQL(
        sql,
        checks,
        recipe.expression.schema().to_pyarrow(),
        proof.schema().to_pyarrow(),
    )
