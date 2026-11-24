"""The Ibis compiler and SQL backend interfaces extended by Datasource."""

from __future__ import annotations

# Ruff's naming rule would reject Ibis's required visitor method names.
# ruff: noqa: N802
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from typing import ClassVar, Literal, NoReturn, Protocol

    import ibis.expr.datatypes as dt
    import ibis.expr.operations as ops
    import ibis.expr.schema as sch
    import ibis.expr.types as ir
    import sqlglot as sg
    import sqlglot.expressions as sge
    from typing_extensions import Self

    class RelationalOperation:
        @property
        def schema(self) -> sch.Schema: ...

        def to_expr(self) -> ir.Table: ...

    class _Functions(Protocol):
        def __getattr__(self, name: str) -> Callable[..., sge.Expr]: ...

    class _Variables(Protocol):
        def __getattr__(self, name: str) -> sge.Var: ...

    class _TypeMapper(Protocol):
        def from_string(self, text: str, *, nullable: bool) -> dt.DataType: ...

    class _SQLCompiler:
        UNSUPPORTED_OPS: ClassVar[tuple[type[ops.Node], ...]]
        SIMPLE_OPS: ClassVar[dict[type[ops.Node], str]]
        f: _Functions
        v: _Variables
        quoted: bool
        type_mapper: _TypeMapper

        def cast(self, expr: sge.Expr, dtype: dt.DataType) -> sge.Expr: ...
        def to_sqlglot(
            self,
            expr: ir.Expr,
            *,
            limit: Literal["default"] | int | None = None,
            params: Mapping[ir.Expr, object] | None = None,
        ) -> sge.Expr: ...
        def visit_Undefined(self, op: ops.Node, **kwargs: object) -> NoReturn: ...

    class PostgresCompiler(_SQLCompiler): ...

    class MySQLCompiler(_SQLCompiler):
        def visit_DateTimestampTruncate(
            self, op: ops.DateTruncate | ops.TimestampTruncate, *, arg: sge.Expr, unit: object
        ) -> sge.Expr: ...

    class TrinoCompiler(_SQLCompiler):
        def visit_IntervalFromInteger(
            self, op: ops.IntervalFromInteger, *, arg: sge.Expr, unit: object
        ) -> sge.Expr: ...
        def visit_NonNullLiteral(
            self, op: ops.Literal, *, value: object, dtype: dt.DataType
        ) -> sge.Expr | None: ...

    class ClickHouseCompiler(_SQLCompiler):
        def visit_JoinChain(
            self,
            op: ops.JoinChain,
            *,
            first: sge.Expr,
            rest: Sequence[sge.Expr],
            values: Mapping[str, sge.Expr],
        ) -> sge.Expr: ...
        def visit_JoinLink(
            self,
            op: ops.JoinLink,
            *,
            how: str,
            table: sge.Expr,
            predicates: Sequence[sge.Expr],
        ) -> sge.Join: ...
        def visit_InSubquery(
            self, op: ops.InSubquery, *, needle: sge.Expr, rel: sge.Expr
        ) -> sge.Expr: ...

    class DuckDBCompiler(_SQLCompiler):
        def translate(self, op: ops.Node, *, params: Mapping[ir.Expr, object]) -> sge.Expr: ...

    class SQLBackend:
        name: ClassVar[str]
        compiler: _SQLCompiler
        dialect: sg.Dialect

        def connect(self, *args: object, **kwargs: object) -> Self: ...
        def disconnect(self) -> None: ...
else:
    from ibis.backends.sql import SQLBackend as SQLBackend
    from ibis.backends.sql.compilers.clickhouse import ClickHouseCompiler as ClickHouseCompiler
    from ibis.backends.sql.compilers.duckdb import DuckDBCompiler as DuckDBCompiler
    from ibis.backends.sql.compilers.mysql import MySQLCompiler as MySQLCompiler
    from ibis.backends.sql.compilers.postgres import PostgresCompiler as PostgresCompiler
    from ibis.backends.sql.compilers.trino import TrinoCompiler as TrinoCompiler
    from ibis.expr import operations as _ops

    RelationalOperation = _ops.Relation
