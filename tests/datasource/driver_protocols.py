"""Typed native-driver hooks shared by cancellation and deadline tests."""

from typing import Protocol


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
