"""Read-only MergeTree execution with owned Native streams."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Sequence
from contextlib import ExitStack
from itertools import islice
from math import isfinite
from typing import TYPE_CHECKING, Protocol
from zoneinfo import ZoneInfo

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.execution import Parameter
from marivo.analysis.materialization.scalar_sql_execution import ScalarExecutionAdapter
from marivo.analysis.operators.clickhouse_support import supported_type
from marivo.datasource.timezone import DatasourceEngineTimezone

if TYPE_CHECKING:
    from ibis.backends.clickhouse import Backend


class NativeStream(Protocol):
    def __enter__(self) -> Iterator[Sequence[object]]: ...
    def __exit__(self, *args: object) -> object: ...


class ClickHouseCursor:
    """Own the stream, including a response returned after interruption."""

    def __init__(self, adapter: ClickHouseExecutionAdapter) -> None:
        self._adapter = adapter
        self._native: NativeStream | None = None
        self._rows: Iterator[Sequence[object]] = iter(())
        self._closed = False

    def _check(self) -> None:
        self._adapter._check()
        if self._closed:
            raise self._adapter.error(
                "an open ClickHouse cursor",
                "cursor is closed",
                "Create a cursor in an open execution context.",
            )

    def execute(self, query: str, parameters: tuple[Parameter, ...] = ()) -> object:
        self._check()
        native: NativeStream = self._adapter._clickhouse.con.query_rows_stream(
            query, parameters=parameters or None
        )
        self._native = native
        # Submission may have been interrupted before returning its response.
        if self._closed or self._adapter._closed:
            native.__exit__(None, None, None)
            self._native = None
            self._adapter._cursors.discard(self)
            self._check()
        self._rows = native.__enter__()
        return None

    def fetchmany(self, size: int) -> Sequence[tuple[object, ...]]:
        self._check()
        rows = [tuple(row) for row in islice(self._rows, size)]
        self._check()
        if any(isinstance(value, float) and not isfinite(value) for row in rows for value in row):
            raise self._adapter.error(
                "finite ClickHouse scalar results",
                "non-finite floating result",
                "Correct non-finite values or overflowing aggregates before retrying.",
                stage="output_validation",
            )
        return rows

    def close(self) -> None:
        if self._closed and self._native is None:
            return
        if self._native is not None:
            self._native.__exit__(None, None, None)
            self._native = None
        self._closed = True
        self._adapter._cursors.discard(self)


def _identifier(value: str) -> str:
    return "`" + value.replace("\\", "\\\\").replace("`", "\\`") + "`"


class ClickHouseExecutionAdapter(ScalarExecutionAdapter):
    engine = "clickhouse"

    def __init__(self, backend: Backend, *, run_ref: str | None = None) -> None:
        super().__init__(backend, run_ref=run_ref)
        self._clickhouse = backend
        self._cursors: set[ClickHouseCursor] = set()

    def cursor(self, *, stream: bool) -> ClickHouseCursor:
        self._check()
        result = ClickHouseCursor(self)
        self._cursors.add(result)
        return result

    def _lower(self, expression: ir.Expr) -> ir.Expr:
        def widen(node: ops.Node, results: dict[ops.Node, ops.Node], **kwargs: object) -> ops.Node:
            if isinstance(node, (ops.Count, ops.CountStar)):
                return ops.Cast(ops.Cast(node.copy(**kwargs), dt.Decimal(76, 0)), node.dtype)
            if isinstance(node, ops.Sum) and (
                node.arg.dtype.is_integer() or node.arg.dtype.is_decimal()
            ):
                arg = kwargs["arg"]
                assert isinstance(arg, ops.Value)
                scale = node.arg.dtype.scale if isinstance(node.arg.dtype, dt.Decimal) else 0
                wide = node.copy(arg=ops.Cast(arg, dt.Decimal(76, scale)), where=kwargs["where"])
                return ops.Cast(wide, node.dtype)
            return node.copy(**kwargs)

        return expression.op().map(widen)[expression.op()].to_expr()

    def get_schema(
        self,
        name: str,
        *,
        database: str | None = None,
        catalog: str | None = None,
        record: Callable[[str, str], None] | None = None,
    ) -> ibis.Schema:
        database = database or self._clickhouse.con.database
        if not isinstance(database, str) or catalog is not None:
            raise self.unsupported("ClickHouse requires a database and table without a catalog")
        settings = self.read_scalar(
            self.statement(
                "SELECT value FROM system.settings WHERE name = 'join_use_nulls'",
                role="engine_check.clickhouse_settings",
            ),
            record=record,
        )
        if settings != "1":
            raise self.error(
                "effective join_use_nulls=1",
                f"join_use_nulls={settings!r}",
                "Configure the read-only ClickHouse account with join_use_nulls=1.",
                stage="source_schema",
            )
        kind = self.read_scalar(
            self.statement(
                "SELECT engine FROM system.tables WHERE database = %s AND name = %s",
                parameters=(database, name),
                role="source_schema",
            ),
            record=record,
        )
        if kind != "MergeTree":
            raise self.unsupported(
                f"ClickHouse engine {kind!r}; qualified methods require ordinary MergeTree"
            )
        query = f"DESCRIBE TABLE {_identifier(database)}.{_identifier(name)}"
        if record:
            record("source_schema", query)
        rows = self.submit(self.statement(query, role="source_schema"))
        fields: dict[str, dt.DataType] = {}
        finite: list[str] = []
        while (row := rows.fetchone()) is not None:
            column, kind = row[:2]
            if not isinstance(column, str) or not isinstance(kind, str):
                raise self.unsupported("malformed ClickHouse column metadata")
            if self._declared_columns is not None and column not in self._declared_columns:
                continue
            physical = (
                kind.removeprefix("Nullable(").removesuffix(")")
                if kind.startswith("Nullable(")
                else kind
            )
            datatype = self._clickhouse.compiler.type_mapper.from_string(kind)
            if (
                not supported_type(str(datatype.copy(nullable=True)))
                or re.fullmatch(
                    r"(?:Int(?:8|16|32|64)|Float(?:32|64)|String|Date|Decimal\([0-9]+,\s*[0-9]+\))",
                    physical,
                )
                is None
            ):
                raise self.unsupported(f"ClickHouse physical type {kind!r} for column {column!r}")
            fields[column] = datatype
            if datatype.is_floating():
                finite.append(f"NOT isFinite({_identifier(column)})")
        if finite:
            violations = self.read_scalar(
                self.statement(
                    f"SELECT count(*) FROM {_identifier(database)}.{_identifier(name)} WHERE "
                    + " OR ".join(finite),
                    role="engine_check.clickhouse_finite",
                ),
                record=record,
            )
            if violations != 0:
                raise self.error(
                    "finite declared ClickHouse floating values or SQL NULL",
                    f"{violations} rows contain non-finite values",
                    "Correct NaN or infinity values before retrying.",
                    stage="output_validation",
                )
        return ibis.schema(fields)

    def timezone(self) -> DatasourceEngineTimezone:
        value = self.read_scalar(self.statement("SELECT timezone()", role="timezone"))
        if not isinstance(value, str):
            raise self.unsupported("ClickHouse returned a non-string timezone")
        return DatasourceEngineTimezone(value, ZoneInfo(value), "iana", "engine")

    def disconnect(self) -> None:
        if self._closed:
            return
        try:
            with ExitStack() as stack:
                stack.callback(self._clickhouse.disconnect)
                for cursor in tuple(self._cursors):
                    stack.callback(cursor.close)
                for stream in tuple(self._streams):
                    stack.callback(stream.close)
        finally:
            self._closed = True

    def interrupt(self) -> None:
        self.disconnect()


def bind_clickhouse(
    candidate: object,
    *,
    reserve: Callable[[str], None],
    run_ref: str,
) -> ClickHouseExecutionAdapter:
    from ibis.backends.clickhouse import Backend

    if not isinstance(candidate, Backend):
        raise MaterializationError(
            expected="an Ibis ClickHouse backend",
            received=type(candidate).__name__,
            repair="Resolve the declared ClickHouse datasource.",
            stage="execution_boundary",
            run_ref=run_ref,
        )
    return ClickHouseExecutionAdapter(candidate, run_ref=run_ref)


def admit_dataset(dataset: LogicalDataset) -> None:
    from marivo.analysis.operators.clickhouse_support import unsupported_reason

    reason = unsupported_reason(dataset)
    if reason is not None:
        raise MaterializationError(
            expected="an individually qualified ClickHouse scalar method closure",
            received=reason,
            repair="Use qualified scalar methods, native civil-date axes and declared MergeTree sources.",
            stage="implementation_registration",
        )
