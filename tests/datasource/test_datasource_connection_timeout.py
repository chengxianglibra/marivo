"""Bounded timeout contract for internal connect, ``md.test``, and doctor.

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

import pytest

import marivo.datasource as md
from marivo.datasource import backends
from marivo.datasource import manage as manage_mod
from marivo.datasource.backends import BuiltDatasourceBackend
from marivo.datasource.errors import DatasourceConnectionTimeoutError
from marivo.datasource.ir import DatasourceIR
from marivo.datasource.manage import DEFAULT_CONNECTION_TIMEOUT_SECONDS


def _patch_load_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``_store.load_one`` resolve a synthetic datasource (never None)."""
    monkeypatch.setattr(
        manage_mod._store,
        "load_one",
        lambda name, project_root=None: DatasourceIR(
            name=name,
            semantic_id=name,
            backend_type="postgres",
            fields={},
            env_refs={},
            ai_context=None,
            python_symbol=name,
            location=None,
        ),
    )


def _patch_blocking_build(monkeypatch: pytest.MonkeyPatch, block_event: threading.Event) -> None:
    """Make backend build block until *block_event* is set."""

    def blocking_build(datasource, **kwargs):  # type: ignore[no-untyped-def]
        block_event.wait()
        raise AssertionError("unreachable: blocking build was released")

    monkeypatch.setattr(backends, "build_backend_with_secrets", blocking_build)


class BlockingSelectBackend:
    """Fake backend whose compiled literal submission blocks until released."""

    name = "postgres"

    def __init__(self, block_event: threading.Event) -> None:
        self._block_event = block_event
        self.queries: list[str] = []
        self.disconnect_calls = 0
        self.disconnect_threads: list[int] = []
        self.con = self

    def cursor(self) -> object:
        owner = self

        class BlockingCursor:
            def execute(self, sql: str) -> None:
                owner.queries.append(sql)
                owner._block_event.wait()

            def fetchmany(self, _size: int) -> list[tuple[int]]:
                return [(1,)]

            def close(self) -> None:
                return None

        return BlockingCursor()

    def compile(self, _expression: object, *, limit: None) -> str:
        return "SELECT 1"

    def disconnect(self) -> None:
        self.disconnect_calls += 1
        self.disconnect_threads.append(threading.get_ident())


def _patch_blocking_select_backend(
    monkeypatch: pytest.MonkeyPatch, backend: BlockingSelectBackend
) -> None:
    monkeypatch.setattr(
        backends,
        "build_backend_with_secrets",
        lambda datasource, **kwargs: BuiltDatasourceBackend(
            backend=backend, env_sourced_secrets=()
        ),
    )


def test_connect_rejects_non_positive_timeout() -> None:
    with pytest.raises(ValueError, match="timeout_seconds must be positive"):
        manage_mod._connect("warehouse", timeout_seconds=0)


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
            manage_mod._connect("warehouse", timeout_seconds=1)
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
    assert "md.test" in exc_info.value.location


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
    assert "Ibis literal round-trip" in result.repair.action
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


@pytest.mark.parametrize("phase", ["connection", "roundtrip"])
@pytest.mark.parametrize("thread_affine", [False, True])
def test_timed_out_roundtrip_releases_late_connections_once_without_persisting(
    monkeypatch: pytest.MonkeyPatch, phase: str, thread_affine: bool
) -> None:
    _patch_load_one(monkeypatch)
    release = threading.Event()
    worker_finished = threading.Event()
    select_gate = release if phase == "roundtrip" else threading.Event()
    if phase == "connection":
        select_gate.set()
    backend = BlockingSelectBackend(select_gate)
    owner_threads: list[int] = []
    persisted: list[manage_mod._DatasourceConnection] = []

    def build(_datasource: DatasourceIR, **_kwargs: object) -> BuiltDatasourceBackend:
        owner_threads.append(threading.get_ident())
        if phase == "connection":
            assert release.wait(5)
        return BuiltDatasourceBackend(
            backend=backend, env_sourced_secrets=(), thread_affine=thread_affine
        )

    original_release = manage_mod._release_connection

    def track_release(connection: manage_mod._DatasourceConnection | None) -> None:
        original_release(connection)
        if owner_threads and threading.get_ident() == owner_threads[0]:
            worker_finished.set()

    monkeypatch.setattr(backends, "build_backend_with_secrets", build)
    monkeypatch.setattr(manage_mod, "_release_connection", track_release)
    monkeypatch.setattr(manage_mod._secrets, "try_persist_backend_env_sourced", persisted.append)
    try:
        result = md.test("warehouse", timeout_seconds=1)
        assert result.ok is False
        assert result.failure is not None
        assert result.failure.code == (
            "connection_timeout" if phase == "connection" else "connection_roundtrip_timeout"
        )
        if thread_affine:
            assert backend.disconnect_calls == 0
    finally:
        release.set()
        assert worker_finished.wait(5)

    assert backend.disconnect_calls == 1
    if thread_affine:
        assert backend.disconnect_threads == owner_threads
    assert persisted == []


def test_test_reports_normal_backend_error_as_open_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-timeout connect failure still surfaces as ``connection_open_failed``."""
    _patch_load_one(monkeypatch)

    def failing_build(datasource, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("gateway refused connection")

    monkeypatch.setattr(backends, "build_backend_with_secrets", failing_build)

    result = md.test("warehouse", timeout_seconds=1)

    assert result.ok is False
    assert result.failure is not None
    assert result.failure.code == "connection_open_failed"
    assert result.failure.timeout_seconds is None


def test_default_timeout_seconds_is_documented_int() -> None:
    assert DEFAULT_CONNECTION_TIMEOUT_SECONDS == 30
    assert isinstance(DEFAULT_CONNECTION_TIMEOUT_SECONDS, int)
