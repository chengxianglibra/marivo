"""MySQL and MariaDB expression lowering for governed Query reads."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.schema as sch
import ibis.expr.types as ir
import pandas as pd
import sqlglot
from sqlglot import expressions as sge
from sqlglot.optimizer.pushdown_projections import pushdown_projections

from marivo.datasource.engines.query import KeyRows

KEY_TYPES = frozenset({dt.int64, dt.string, dt.boolean, dt.date})


def _mysql_quote(value: str) -> str:
    raise NotImplementedError("native MySQL JSON_QUOTE")


def _mysql_unquote(value: str) -> str:
    raise NotImplementedError("native MySQL JSON_UNQUOTE")


def _mysql_hex(value: str) -> str:
    raise NotImplementedError("native MySQL HEX")


def _mysql_unhex(value: str) -> bytes:
    raise NotImplementedError("native MySQL UNHEX")


_MYSQL_QUOTE: Callable[[ir.Value], ir.Value] = ibis.udf.scalar.builtin(
    name="JSON_QUOTE", signature=(("string",), "string")
)(_mysql_quote)
_MYSQL_UNQUOTE: Callable[[ir.Value], ir.Value] = ibis.udf.scalar.builtin(
    name="JSON_UNQUOTE", signature=(("string",), "string")
)(_mysql_unquote)
_MYSQL_HEX: Callable[[ir.Value], ir.Value] = ibis.udf.scalar.builtin(
    name="HEX", signature=(("string",), "string")
)(_mysql_hex)
_MYSQL_UNHEX: Callable[[ir.Value], ir.Value] = ibis.udf.scalar.builtin(
    name="UNHEX", signature=(("string",), "binary")
)(_mysql_unhex)


def string_bytes(value: ir.Value) -> ir.Value:
    """Normalize native character sets before comparing returned bytes."""
    return _MYSQL_UNHEX(_MYSQL_HEX(_MYSQL_UNQUOTE(_MYSQL_QUOTE(value))))


def _hex(value: bytes) -> str:
    raise NotImplementedError("native MySQL HEX")


_HEX: Callable[[ir.Value], ir.StringValue] = ibis.udf.scalar.builtin(
    name="HEX", signature=(("binary",), "string")
)(_hex)


def bind_complete_key_membership(
    schema: sch.Schema,
    records: KeyRows,
    values: Mapping[str, ir.Value],
    capacity: Callable[[], int] | None,
) -> ir.BooleanValue | None:
    """Use one JSON member lookup, with no per-key SQL expression expansion."""
    if not schema or any(dtype.copy(nullable=True) not in KEY_TYPES for dtype in schema.types):
        return None
    if not records:
        return ibis.literal(False)

    def encode(value: object, dtype: dt.DataType) -> str:
        if value is None or value is pd.NA or value is pd.NaT:
            return "n"
        if dtype.is_string():
            assert isinstance(value, str)
            return "s" + value.encode("utf-8").hex().upper()
        if dtype.is_boolean():
            return "b1" if value else "b0"
        return ("i" if dtype.is_int64() else "d") + str(value)

    # Only digits, hex, minus and the component separators enter the path. A
    # distinct prefix and NULL token preserve empty strings and complete tuples.
    members = {
        "_".join(encode(value, dtype) for dtype, value in zip(schema.types, row, strict=True)): 1
        for row in records
    }
    longest = max(map(len, members))
    if longest > 65535:
        raise ValueError(
            f"MySQL JSON complete keys require at most 65535 encoded bytes; received {longest}. "
            "Use shorter string keys."
        )
    payload = json.dumps(members, separators=(",", ":"))
    # All member names are ASCII. A large binary JSON object has an 8-byte
    # header, 6-byte key entries and 5-byte value entries; small integer 1 is
    # inline. Include the outer type byte when bounding its native storage.
    binary_bytes = 9 + sum(11 + len(key) for key in members)
    if capacity is not None:
        limit = capacity()
        if max(len(payload), binary_bytes) > limit:
            raise ValueError(
                f"MySQL JSON keys require at most max_allowed_packet={limit} bytes; "
                f"received {len(payload)} text and at most {binary_bytes} binary bytes. "
                "Reduce the selected key domain or increase the datasource packet limit."
            )
    parts: list[ir.StringValue] = []
    for name, dtype in schema.items():
        value = values[name]
        if dtype.is_string():
            part = ibis.literal("s") + _HEX(string_bytes(value))
        elif dtype.is_boolean():
            part = value.ifelse("b1", "b0")
        else:
            part = ibis.literal("i" if dtype.is_int64() else "d") + value.cast("string")
        assert isinstance(part, ir.StringValue)
        parts.append(value.isnull().ifelse("n", part))
    key = ibis.literal("_").join(parts)
    document = ibis.literal(payload).cast("json")
    assert isinstance(document, ir.JSONValue)
    # Ibis MySQL JSONGetItem prepends '$.' but does not quote its string index.
    # The closed alphabet above needs quoting, and cannot contain quotes/escapes.
    return document[ibis.literal('"') + key + '"'].notnull()


def lower_query_numeric(expression: ir.Table) -> ir.Table:
    # MySQL has no IS_NAN / IS_INF functions. Native finite comparisons
    # retain the check without requiring routines or source-side casts.
    def mysql_rewrite(
        node: ops.Node, _results: Mapping[ops.Node, ops.Node], **kwargs: object
    ) -> ops.Node:
        value = node.copy(**kwargs)
        if (
            isinstance(value, ops.Cast)
            and isinstance(value.arg, ops.Sum)
            and value.to.is_decimal()
            and value.arg.dtype == value.to
        ):
            # MySQL widens native SUM, but a redundant result CAST clips an
            # overflowing Decimal to its endpoint with only a warning. Keep
            # the widened value until safe conversion to the bound carrier.
            return value.arg
        if isinstance(value, (ops.IsNan, ops.IsInf)):
            argument = value.arg.to_expr()
            return (
                argument != argument
                if isinstance(value, ops.IsNan)
                else argument.abs() > float.fromhex("0x1.fffffffffffffp+1023")
            ).op()
        return value

    rewritten = expression.op().map(mysql_rewrite)[expression.op()].to_expr()
    assert isinstance(rewritten, ir.Table)
    return rewritten


def source_timezones(sql: str) -> tuple[str, ...]:
    """Inspect required native conversion zones without changing issued SQL."""
    if "CONVERT_TZ(" not in sql.upper():
        return ()
    statement = sqlglot.parse_one(sql, read="mysql")
    # Projection pruning does not resolve positional grouping references.
    # Expand those references while the original SELECT list is still intact.
    for select in statement.find_all(sge.Select):
        group = select.args.get("group")
        if not isinstance(group, sge.Group):
            continue
        values = select.expressions
        references: list[sge.Expression] = []
        for item in group.expressions:
            if isinstance(item, sge.Literal) and item.is_int and 1 <= int(item.this) <= len(values):
                selected = values[int(item.this) - 1]
                references.append(
                    (selected.this if isinstance(selected, sge.Alias) else selected).copy()
                )
            else:
                references.append(item)
        group.set("expressions", references)
    # Ibis can retain unused values in inner SELECTs. Inspect only the required
    # projection closure; raw-only membership needs no named zone tables.
    statement = pushdown_projections(statement)
    return tuple(
        sorted(
            {
                zone.this
                for conversion in statement.find_all(sge.ConvertTimezone)
                for name in ("source_tz", "target_tz")
                for zone in (conversion.args.get(name),)
                if isinstance(zone, sge.Literal) and zone.is_string
            }
        )
    )


def preserve_string_equality(comparison: ops.Equals) -> ops.Value:
    # Singleton IN retains the column collation and SQL UNKNOWN for NULL.
    return ops.InValues(comparison.left, (comparison.right,))
