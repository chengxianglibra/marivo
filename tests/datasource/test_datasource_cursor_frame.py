from __future__ import annotations

from marivo.datasource.engines.base import decode_cursor_frame


class _DbApiCursor:
    description = (("id", "INTEGER"), ("name", "VARCHAR"))

    def __init__(self) -> None:
        self.fetchmany_calls: list[int] = []

    def fetchall(self) -> list[tuple[object, ...]]:
        return [(1, "a"), (2, "b"), (3, "c")]

    def fetchmany(self, size: int) -> list[tuple[object, ...]]:
        self.fetchmany_calls.append(size)
        return [(1, "a"), (2, "b"), (3, "c")]


class _ClickHouseCursor:
    column_names = ("id", "name")
    result_rows = ((1, "a"), (2, "b"), (3, "c"))


class _UnknownCursor:
    pass


def test_decode_cursor_frame_dbapi_fetchall_with_types() -> None:
    frame = decode_cursor_frame(_DbApiCursor(), include_types=True, max_rows=None)
    assert frame.columns == ("id", "name")
    assert frame.rows == ({"id": 1, "name": "a"}, {"id": 2, "name": "b"}, {"id": 3, "name": "c"})
    assert frame.types == {"id": "INTEGER", "name": "VARCHAR"}


def test_decode_cursor_frame_dbapi_uses_limit_plus_one_probe() -> None:
    cursor = _DbApiCursor()
    frame = decode_cursor_frame(cursor, include_types=False, max_rows=2)
    assert cursor.fetchmany_calls == [3]
    assert len(frame.rows) == 3
    assert frame.types == {}


def test_decode_cursor_frame_clickhouse_protocol() -> None:
    frame = decode_cursor_frame(_ClickHouseCursor(), include_types=True, max_rows=2)
    assert frame.columns == ("id", "name")
    assert frame.rows == ({"id": 1, "name": "a"}, {"id": 2, "name": "b"}, {"id": 3, "name": "c"})
    assert frame.types == {}


def test_decode_cursor_frame_unknown_protocol_returns_empty_frame() -> None:
    frame = decode_cursor_frame(_UnknownCursor(), include_types=True, max_rows=10)
    assert frame.columns == ()
    assert frame.rows == ()
    assert frame.types == {}
