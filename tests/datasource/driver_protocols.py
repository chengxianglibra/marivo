"""Typed native-driver hooks shared by cancellation and deadline tests."""

from typing import Protocol, runtime_checkable

from marivo.datasource.adapters import _ClickHouseNativeStream


class _NativeClickHouseRead(Protocol):
    def __call__(
        self, query: str, *, settings: dict[str, str | float | int] | None = None
    ) -> _ClickHouseNativeStream: ...


@runtime_checkable
class _ClickHouseReadClient(Protocol):
    def query_rows_stream(
        self, query: str, *, settings: dict[str, str | float | int] | None = None
    ) -> _ClickHouseNativeStream: ...


@runtime_checkable
class _ClickHouseServerError(Protocol):
    code: int | None


class _MysqlConnection(Protocol):
    def thread_id(self) -> int: ...


class _MysqlCursor(Protocol):
    connection: _MysqlConnection | None


class _DriverExecute(Protocol):
    def __call__(
        self, cursor: _MysqlCursor, query: str | bytes, args: object | None = None
    ) -> int | None: ...


class _DriverClose(Protocol):
    def __call__(self, cursor: _MysqlCursor) -> None: ...
