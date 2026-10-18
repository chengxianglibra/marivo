"""Source string identity and Query numeric missing-value predicates."""

from __future__ import annotations

from collections.abc import Callable

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir


def _postgres_json(value: str) -> object:
    raise NotImplementedError("native PostgreSQL to_json")


def _postgres_encode(value: str, encoding: str) -> bytes:
    raise NotImplementedError("native PostgreSQL convert_to")


def _duckdb_encode(value: str) -> bytes:
    raise NotImplementedError("native DuckDB encode")


def _duckdb_decode(value: bytes) -> str:
    raise NotImplementedError("native DuckDB decode")


def _trino_encode(value: str) -> bytes:
    raise NotImplementedError("native Trino to_utf8")


def _mysql_quote(value: str) -> str:
    raise NotImplementedError("native MySQL JSON_QUOTE")


def _mysql_unquote(value: str) -> str:
    raise NotImplementedError("native MySQL JSON_UNQUOTE")


def _mysql_hex(value: str) -> str:
    raise NotImplementedError("native MySQL HEX")


def _mysql_unhex(value: str) -> bytes:
    raise NotImplementedError("native MySQL UNHEX")


_POSTGRES_JSON: Callable[[ir.Value], ir.Value] = ibis.udf.scalar.builtin(
    name="to_json", signature=(("string",), "json")
)(_postgres_json)
_POSTGRES_ENCODE: Callable[[ir.Value, str], ir.Value] = ibis.udf.scalar.builtin(
    name="convert_to", signature=(("string", "string"), "binary")
)(_postgres_encode)
_DUCKDB_ENCODE: Callable[[ir.Value], ir.Value] = ibis.udf.scalar.builtin(
    name="encode", signature=(("string",), "binary")
)(_duckdb_encode)
_DUCKDB_DECODE: Callable[[ir.Value], ir.Value] = ibis.udf.scalar.builtin(
    name="decode", signature=(("binary",), "string")
)(_duckdb_decode)
_TRINO_ENCODE: Callable[[ir.Value], ir.Value] = ibis.udf.scalar.builtin(
    name="to_utf8", signature=(("string",), "binary")
)(_trino_encode)
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


def string_bytes(value: ir.Value, provider: str) -> ir.Value:
    if provider == "duckdb":
        return _DUCKDB_ENCODE(value)
    if provider == "trino":
        return _TRINO_ENCODE(value)
    if provider == "postgres":
        # JSON preserves CHAR padding as returned to the reader; CHAR -> text does not.
        if not isinstance(value.op(), ops.Literal):
            encoded = _POSTGRES_JSON(value)
            assert isinstance(encoded, ir.JSONValue)
            value = encoded.unwrap_as("str")
        result = _POSTGRES_ENCODE(value, "UTF8")
        assert isinstance(result, ir.Value)
        return result
    if provider == "mysql":
        # JSON strings normalize source character sets before byte comparison.
        result = _MYSQL_UNHEX(_MYSQL_HEX(_MYSQL_UNQUOTE(_MYSQL_QUOTE(value))))
        assert isinstance(result, ir.Value)
        return result
    return value


def string_without_collation(value: ir.Value, provider: str) -> ir.Value:
    """Preserve returned DuckDB strings while removing inherited SQL collations."""
    return _DUCKDB_DECODE(string_bytes(value, provider)) if provider == "duckdb" else value


def parameter_string_comparisons(expression: ir.Table, name: str, provider: str) -> ir.Table:
    """Match qualified membership keys using their exact returned string bytes."""
    if provider not in ("duckdb", "postgres", "mysql", "trino", "clickhouse"):
        return expression

    def rewrite(node: ops.Node, kwargs: dict[str, object] | None) -> ops.Node:
        # Rebuild operands first: a nested membership may already have changed
        # their relation parents, including JoinReferences in an enclosing join.
        comparison = node.__recreate__(kwargs) if kwargs else node
        assert isinstance(comparison, ops.Node)
        if (
            isinstance(comparison, ops.IdenticalTo)
            and comparison.left.dtype.is_string()
            and comparison.right.dtype.is_string()
            and any(relation.name == name for relation in comparison.find(ops.UnboundTable))
        ):
            return (
                string_bytes(comparison.left.to_expr(), provider)
                .identical_to(string_bytes(comparison.right.to_expr(), provider))
                .op()
            )
        return comparison

    result = expression.op().replace(rewrite).to_expr()
    assert isinstance(result, ir.Table)
    return result


def source_parameter_comparisons(expression: ir.Table, name: str, provider: str) -> ir.Table:
    """Retain native membership equality, including native Metric group collations."""
    return (
        parameter_string_comparisons(expression, name, provider)
        if provider == "postgres"
        else expression
    )


def query_numeric_value(value: ir.Value, provider: str) -> ir.Value:
    """Treat native floating NaN as missing before Query predicates and ordering."""
    if provider in ("duckdb", "trino", "clickhouse") and isinstance(value, ir.FloatingValue):
        return value.isnan().ifelse(ibis.null().cast(value.type().copy(nullable=True)), value)
    return value
