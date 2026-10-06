"""Governed native submission capture for source projection acceptance."""

from collections.abc import Callable

import pytest

from tests.lazy_scalar_source_fixtures import capture_submissions


def capture_source_sql(monkeypatch: pytest.MonkeyPatch) -> Callable[[], tuple[str, ...]]:
    submitted = capture_submissions(monkeypatch)

    def snapshot() -> tuple[str, ...]:
        result: list[str] = []
        for entry in submitted:
            sql = entry["sql"]
            assert isinstance(sql, str)
            result.append(sql)
        return tuple(result)

    return snapshot
