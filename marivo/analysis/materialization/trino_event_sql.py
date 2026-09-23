"""Exact Trino Event identity and microsecond lowering."""

from __future__ import annotations

from datetime import datetime, timezone

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.types as ir
import sqlglot
from ibis.backends.sql.compilers.trino import TrinoCompiler
from ibis.backends.trino import Backend
from sqlglot import expressions as sge

from marivo.analysis.compiler.event_time import _localize_utc

_LOCALIZE_UTC = type(_localize_utc("UTC", ibis.timestamp("2000-01-01")).op())


class _EventCompiler(TrinoCompiler):  # type: ignore[misc]  # Ibis compiler lacks typing.
    def visit_InValues(  # noqa: N802
        self, op: ops.InValues, *, value: sge.Expression, options: tuple[sge.Expression, ...]
    ) -> sge.Expression:
        return sge.Paren(this=value.isin(*options))

    def visit_Divide(  # noqa: N802
        self, op: ops.Divide, *, left: sge.Expression, right: sge.Expression
    ) -> sge.Expression:
        if op.dtype.is_floating():
            left = self.cast(left, dt.float64)
        return sge.Div(this=left, expression=right)

    def visit_HexDigest(  # noqa: N802
        self, op: ops.HexDigest, *, arg: sge.Expression, how: str
    ) -> sge.Expression:
        if how != "sha256":
            raise NotImplementedError(f"Trino Event digest: {how}")
        result: sge.Expression = self.f.lower(self.f.to_hex(self.f.sha256(self.f.to_utf8(arg))))
        return result

    def visit_Cast(  # noqa: N802
        self, op: ops.Cast, *, arg: sge.Expression, to: dt.DataType
    ) -> sge.Expression:
        if op.arg.dtype.is_json() and to.is_string():
            result: sge.Expression = self.f.json_format(arg)
            return result
        if isinstance(to, dt.Timestamp) and to.scale is None:
            to = to.copy(scale=6)
        standard: sge.Expression = super().visit_Cast(op, arg=arg, to=to)
        return standard

    def visit_NonNullLiteral(  # noqa: N802
        self, op: ops.Literal, *, value: object, dtype: dt.DataType
    ) -> sge.Expression:
        if isinstance(dtype, dt.Timestamp):
            if not isinstance(value, datetime):
                raise ValueError("Event timestamp literal requires a datetime")
            if value.tzinfo is not None:
                value = value.astimezone(timezone.utc)
            result: sge.Expression = self.cast(
                sge.Literal.string(value.isoformat(sep=" ")), dtype.copy(scale=6)
            )
            return result
        standard: sge.Expression = super().visit_NonNullLiteral(op, value=value, dtype=dtype)
        return standard

    def visit_ScalarUDF(  # noqa: N802
        self, op: ops.ScalarUDF, **kwargs: sge.Expression
    ) -> sge.Expression:
        if isinstance(op, _LOCALIZE_UTC):
            values = tuple(kwargs.values())
            result: sge.Expression = self.f.with_timezone(values[1], "UTC")
            return result
        standard: sge.Expression = super().visit_ScalarUDF(op, **kwargs)
        return standard

    def visit_TimestampDelta(  # noqa: N802
        self,
        op: ops.TimestampDelta,
        *,
        part: sge.Expression,
        left: sge.Expression,
        right: sge.Expression,
    ) -> sge.Expression:
        if not isinstance(op.part, ops.Literal) or op.part.value != "microsecond":
            standard: sge.Expression = super().visit_TimestampDelta(
                op, part=part, left=left, right=right
            )
            return standard

        def fraction(value: sge.Expression) -> sge.Expression:
            digits = self.f.coalesce(
                self.f.regexp_extract(self.cast(value, dt.string), r"\.([0-9]+)", 1), ""
            )
            result: sge.Expression = self.cast(
                self.f.substr(self.f.rpad(digits, 6, "0"), 1, 6), dt.int64
            )
            return result

        seconds = self.f.date_diff(
            "second", self.f.date_trunc("second", right), self.f.date_trunc("second", left)
        )
        return sge.Sub(
            this=sge.Add(
                this=sge.Mul(this=seconds, expression=sge.Literal.number(1000000)),
                expression=fraction(left),
            ),
            expression=fraction(right),
        )


class _EventBackend(Backend):  # type: ignore[misc]  # Ibis backend lacks typing.
    compiler = _EventCompiler()


def compile_event_expression(expression: ir.Expr) -> str:
    result: str = _EventBackend().compile(expression)
    query = sqlglot.parse_one(result, read="trino")
    for window in query.find_all(sge.Window):
        filtered = window.this
        if isinstance(filtered, sge.Filter) and isinstance(
            filtered.this, (sge.Max, sge.Min, sge.Sum)
        ):
            aggregate = filtered.this.copy()
            aggregate.set(
                "this",
                sge.If(
                    this=filtered.expression.this.copy(),
                    true=aggregate.this.copy(),
                    false=sge.Null(),
                ),
            )
            window.set("this", aggregate)
    # Trino cannot decorrelate ROW dereferences in EXISTS. Membership uses
    # equality keys only, so a noncorrelated IN preserves its filter semantics.
    for exists in tuple(query.find_all(sge.Exists)):
        if isinstance(exists.parent, sge.Not):
            continue
        select = exists.this
        if not isinstance(select, sge.Select):
            continue
        where = select.args.get("where")
        source = select.args.get("from_")
        if where is None or source is None:
            continue
        alias = source.this.alias_or_name
        terms = tuple(where.this.flatten()) if isinstance(where.this, sge.And) else (where.this,)
        outer: list[sge.Expression] = []
        inner: list[sge.Expression] = []
        for term in terms:
            if not isinstance(term, sge.EQ):
                break
            left, right = term.this, term.expression
            if isinstance(left, sge.Column) and left.table == alias:
                left, right = right, left
            if not isinstance(right, sge.Column) or right.table != alias:
                break
            outer.append(left.copy())
            inner.append(right.copy())
        else:
            if outer:
                key = outer[0] if len(outer) == 1 else sge.Anonymous(this="ROW", expressions=outer)
                selected = (
                    inner[0] if len(inner) == 1 else sge.Anonymous(this="ROW", expressions=inner)
                )
                subquery = select.copy()
                subquery.set("expressions", [selected])
                subquery.set("where", None)
                exists.replace(sge.In(this=key, query=subquery.subquery()))
    return query.sql(dialect="trino")
