"""Bounded timeout contract for ``md.connect`` / ``md.test`` / ``md.test_no_persist``.

These tests use fakes that block on a ``threading.Event`` so the Marivo-side
wall-clock deadline is exercised without a real hanging gateway. Each blocking
fake runs on the daemon worker thread spawned by ``_run_with_deadline``; the
event is released in a ``finally`` so the worker is not left parked at the end
of the test.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import marivo.datasource as md
from marivo.datasource import manage as manage_mod
from marivo.datasource.errors import DatasourceConnectionTimeoutError
from marivo.datasource.manage import DEFAULT_CONNECTION_TIMEOUT_SECONDS


def _patch_load_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``_store.load_one`` resolve a synthetic datasource (never None)."""
    monkeypatch.setattr(
        manage_mod._store,
        "load_one",
        lambda name, project_root=None: SimpleNamespace(name=name),
    )


def _patch_blocking_build(monkeypatch: pytest.MonkeyPatch, block_event: threading.Event) -> None:
    """Make backend build block until *block_event* is set."""

    def blocking_build(datasource):  # type: ignore[no-untyped-def]
        block_event.wait()
        raise AssertionError("unreachable: blocking build was released")

    monkeypatch.setattr(manage_mod._backends, "build_backend_with_secrets", blocking_build)


class BlockingSelectBackend:
    """Fake backend whose ``raw_sql`` blocks until released."""

    def __init__(self, block_event: threading.Event) -> None:
        self._block_event = block_event
        self.queries: list[str] = []
        self.disconnect_calls = 0

    def raw_sql(self, sql: str) -> None:
        self.queries.append(sql)
        self._block_event.wait()

    def disconnect(self) -> None:
        self.disconnect_calls += 1


def _patch_blocking_select_backend(
    monkeypatch: pytest.MonkeyPatch, backend: BlockingSelectBackend
) -> None:
    monkeypatch.setattr(
        manage_mod._backends,
        "build_backend_with_secrets",
        lambda datasource: SimpleNamespace(backend=backend, env_sourced_secrets=()),
    )


def test_connect_rejects_non_positive_timeout() -> None:
    with pytest.raises(ValueError, match="timeout_seconds must be positive"):
        md.connect("warehouse", timeout_seconds=0)


def test_test_rejects_non_positive_timeout() -> None:
    with pytest.raises(ValueError, match="timeout_seconds must be positive"):
        md.test("warehouse", timeout_seconds=0)


def test_test_no_persist_rejects_non_positive_timeout() -> None:
    with pytest.raises(ValueError, match="timeout_seconds must be positive"):
        manage_mod.test_no_persist("warehouse", timeout_seconds=-1)


def test_connect_raises_typed_timeout_when_handshake_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_load_one(monkeypatch)
    block_event = threading.Event()
    _patch_blocking_build(monkeypatch, block_event)
    try:
        started = time.monotonic()
        with pytest.raises(DatasourceConnectionTimeoutError) as exc_info:
            md.connect("warehouse", timeout_seconds=1)
        elapsed = time.monotonic() - started
    finally:
        block_event.set()

    assert exc_info.value.stage == "connection_timeout"
    assert exc_info.value.timeout_seconds == 1
    assert exc_info.value.datasource_name == "warehouse"
    assert 0 < exc_info.value.elapsed_ms < 5000
    assert elapsed < 5
    assert exc_info.value.repair is not None
    assert exc_info.value.repair.kind == "reconnect"
    assert "timeout_seconds" in exc_info.value.repair.action
    assert "md.connect" in exc_info.value.location


def test_test_returns_connection_timeout_when_handshake_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_load_one(monkeypatch)
    block_event = threading.Event()
    _patch_blocking_build(monkeypatch, block_event)
    try:
        result = md.test("warehouse", timeout_seconds=1)
    finally:
        block_event.set()

    assert result.ok is False
    assert result.failure is not None
    assert result.failure.code == "connection_timeout"
    assert result.failure.timeout_seconds == 1
    assert result.latency_ms is not None
    assert result.repair is not None
    assert "timeout_seconds" in result.repair.action


def test_test_returns_roundtrip_timeout_when_select_1_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_load_one(monkeypatch)
    block_event = threading.Event()
    backend = BlockingSelectBackend(block_event)
    _patch_blocking_select_backend(monkeypatch, backend)
    try:
        result = md.test("warehouse", timeout_seconds=1)
    finally:
        block_event.set()

    assert result.ok is False
    assert result.failure is not None
    assert result.failure.code == "connection_roundtrip_timeout"
    assert result.failure.timeout_seconds == 1
    assert result.latency_ms is not None
    assert result.repair is not None
    assert "SELECT 1 round-trip" in result.repair.action
    assert backend.queries == ["SELECT 1"]
    # The caller disconnects on timeout; the abandoned worker's own `finally`
    # may disconnect again before it is descheduled, so require at least one.
    assert backend.disconnect_calls >= 1


def test_test_no_persist_returns_roundtrip_timeout_when_select_1_blocks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_load_one(monkeypatch)
    block_event = threading.Event()
    backend = BlockingSelectBackend(block_event)
    _patch_blocking_select_backend(monkeypatch, backend)
    try:
        result = manage_mod.test_no_persist("warehouse", timeout_seconds=1, project_root=tmp_path)
    finally:
        block_event.set()

    assert result.ok is False
    assert result.failure is not None
    assert result.failure.code == "connection_roundtrip_timeout"
    # Caller disconnect races with the abandoned worker's `finally` disconnect;
    # at least one is guaranteed by the timeout path.
    assert backend.disconnect_calls >= 1


def test_test_reports_normal_backend_error_as_open_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-timeout connect failure still surfaces as ``connection_open_failed``."""
    _patch_load_one(monkeypatch)

    def failing_build(datasource):  # type: ignore[no-untyped-def]
        raise RuntimeError("gateway refused connection")

    monkeypatch.setattr(manage_mod._backends, "build_backend_with_secrets", failing_build)

    result = md.test("warehouse", timeout_seconds=1)

    assert result.ok is False
    assert result.failure is not None
    assert result.failure.code == "connection_open_failed"
    assert result.failure.timeout_seconds is None


def test_default_timeout_seconds_is_documented_int() -> None:
    assert DEFAULT_CONNECTION_TIMEOUT_SECONDS == 30
    assert isinstance(DEFAULT_CONNECTION_TIMEOUT_SECONDS, int)
