"""Exact reader-owned ClickHouse control and private-pool cleanup boundaries."""

from unittest.mock import Mock

import pytest

from marivo.datasource import adapters
from marivo.datasource.adapters import (
    SourceSession,
    provider_for,
)
from marivo.datasource.engines.base import EngineProfile
from tests.datasource.source_cases import datasource


def test_initial_clickhouse_stream_context_failure_closes_native_response() -> None:
    native = Mock()
    native.__enter__ = Mock(side_effect=RuntimeError("initial context failed"))
    native.__exit__ = Mock()
    with pytest.raises(RuntimeError, match="initial context failed"):
        adapters._ClickHouseCursor(native)
    native.__exit__.assert_called_once_with(None, None, None)


def reader_session(backend: Mock, profile: EngineProfile | None = None) -> SourceSession:
    backend.name = "clickhouse"
    return SourceSession(
        profile or provider_for("clickhouse"),
        datasource("clickhouse", {"user": "unconnected_identity"}),
        backend,
    )
