"""Instance-scoped Ibis compiler corrections for native Marivo source aggregates."""

from __future__ import annotations

# Ibis visitor names include the operation class name.
# ruff: noqa: N802
from typing import ClassVar, NoReturn

import ibis.expr.operations as ops
import sqlglot.expressions as sge
from ibis.backends import BaseBackend
from ibis.backends.sql import SQLBackend
from ibis.backends.sql.compilers.clickhouse import ClickHouseCompiler
from ibis.backends.sql.compilers.mysql import MySQLCompiler
from ibis.backends.sql.compilers.postgres import PostgresCompiler
from ibis.backends.sql.compilers.trino import TrinoCompiler
from ibis.common.exceptions import OperationNotDefinedError

from marivo._source_aggregates import aggregate_repair_action, native_aggregate
from marivo.datasource.errors import DatasourceSourceCapabilityError, repair

_OPERATIONS = {
    ops.CountDistinct: "count_distinct",
    ops.ApproxCountDistinct: "approx_nunique",
    ops.Median: "median",
    ops.Quantile: "quantile",
    ops.MultiQuantile: "quantile",
    ops.ApproxMedian: "approx_median",
    ops.ApproxQuantile: "approx_quantile",
    ops.ApproxMultiQuantile: "approx_quantile",
}


def _unsupported(backend: str) -> tuple[type[ops.Node], ...]:
    return tuple(op for op, name in _OPERATIONS.items() if native_aggregate(backend, name) is None)


def _reject(backend: str, node: ops.Node) -> NoReturn:
    if isinstance(
        node,
        (
            ops.CountDistinct,
            ops.ApproxCountDistinct,
            ops.Median,
            ops.Quantile,
            ops.MultiQuantile,
            ops.ApproxMedian,
            ops.ApproxQuantile,
            ops.ApproxMultiQuantile,
        ),
    ):
        reducer = _OPERATIONS[type(node)]
        dtype = node.arg.dtype
        raise DatasourceSourceCapabilityError(
            message="The source aggregate has no qualified native compilation.",
            expected="a native algorithm preserving the declared accuracy and input type",
            received=f"{backend}: {reducer}({dtype})",
            repair=repair(
                kind="configure",
                canonical_id=backend,
                action=aggregate_repair_action(backend, reducer, dtype),
            ),
        )
    raise OperationNotDefinedError(f"Compilation rule for {type(node).__name__!r} is not defined")


# Ibis compiler classes are untyped. These subclasses retain its complete relational
# compiler and override only aggregate mappings/admission on Marivo-owned instances.
class _PostgresCompiler(PostgresCompiler):  # type: ignore[misc]
    UNSUPPORTED_OPS = (*PostgresCompiler.UNSUPPORTED_OPS, *_unsupported("postgres"))

    def visit_Undefined(self, op: ops.Node, **kwargs: object) -> NoReturn:
        _reject("postgres", op)


class _MySQLCompiler(MySQLCompiler):  # type: ignore[misc]
    UNSUPPORTED_OPS = (*MySQLCompiler.UNSUPPORTED_OPS, *_unsupported("mysql"))

    def visit_Undefined(self, op: ops.Node, **kwargs: object) -> NoReturn:
        _reject("mysql", op)


class _TrinoCompiler(TrinoCompiler):  # type: ignore[misc]
    UNSUPPORTED_OPS = (*TrinoCompiler.UNSUPPORTED_OPS, *_unsupported("trino"))

    def visit_Undefined(self, op: ops.Node, **kwargs: object) -> NoReturn:
        _reject("trino", op)


def _native_name(backend: str, reducer: str) -> str:
    name = native_aggregate(backend, reducer)
    assert name is not None
    return name


class _ClickHouseCompiler(ClickHouseCompiler):  # type: ignore[misc]
    # Ibis generates SIMPLE_OPS visitors after class creation, overriding explicit
    # methods. Remove median entries before defining the parameterized visitors.
    SIMPLE_OPS: ClassVar[dict[type[ops.Node], str]] = {
        **{
            op: name
            for op, name in ClickHouseCompiler.SIMPLE_OPS.items()
            if op not in (ops.Median, ops.ApproxMedian)
        },
        ops.CountDistinct: _native_name("clickhouse", "count_distinct"),
    }

    def _quantile(
        self,
        op: ops.Median | ops.Quantile | ops.MultiQuantile | ops.ApproxMedian,
        arg: sge.Expr,
        quantile: sge.Expr,
        where: sge.Expr | None,
        *,
        approximate: bool = False,
        multiple: bool = False,
    ) -> sge.Expr:
        reducer = "approx_quantile" if approximate else "quantile"
        name: str | None = native_aggregate("clickhouse", reducer, op.arg.dtype)
        if name is None:
            _reject("clickhouse", op)
        if multiple:
            name = "quantilesExactInclusive"
        return sge.ParameterizedAgg(
            this=f"{name}If" if where is not None else name,
            expressions=list(quantile.expressions) if multiple else [quantile],
            params=[arg, where] if where is not None else [arg],
        )

    def visit_Quantile(
        self,
        op: ops.Quantile,
        *,
        arg: sge.Expr,
        quantile: sge.Expr,
        where: sge.Expr | None,
    ) -> sge.Expr:
        return self._quantile(op, arg, quantile, where)

    def visit_MultiQuantile(
        self,
        op: ops.MultiQuantile,
        *,
        arg: sge.Expr,
        quantile: sge.Expr,
        where: sge.Expr | None,
    ) -> sge.Expr:
        return self._quantile(op, arg, quantile, where, multiple=True)

    def visit_Median(
        self,
        op: ops.Median,
        *,
        arg: sge.Expr,
        where: sge.Expr | None,
    ) -> sge.Expr:
        return self._quantile(op, arg, sge.convert(0.5), where)

    def visit_ApproxMedian(
        self,
        op: ops.ApproxMedian,
        *,
        arg: sge.Expr,
        where: sge.Expr | None,
    ) -> sge.Expr:
        return self._quantile(op, arg, sge.convert(0.5), where, approximate=True)


def configure_aggregate_compiler(backend: BaseBackend) -> None:
    """Configure a backend instance without changing Ibis's global compilers."""
    if not isinstance(backend, SQLBackend):
        return
    if backend.name == "postgres":
        backend.compiler = _PostgresCompiler()
    elif backend.name == "mysql":
        backend.compiler = _MySQLCompiler()
    elif backend.name == "trino":
        backend.compiler = _TrinoCompiler()
    elif backend.name == "clickhouse":
        backend.compiler = _ClickHouseCompiler()
