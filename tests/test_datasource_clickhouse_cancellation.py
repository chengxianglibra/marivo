"""Exact reader-owned ClickHouse control and private-pool cleanup boundaries."""

import json
import os
import signal
import time
from base64 import b64encode
from collections.abc import Callable, Mapping, Sequence
from contextlib import closing
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pyarrow as pa
import pytest
from ibis.backends import BaseBackend

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource import adapters
from marivo.datasource.adapters import (
    PhysicalRequirement,
    SourceBatchStream,
    SourceSession,
    SourceSubmission,
    provider_for,
)
from marivo.datasource.engines.base import EngineProfile
from marivo.datasource.errors import DatasourceSourceCapabilityError
from marivo.semantic.reader import SemanticProject
from tests.r9_source_cases import datasource, source_case


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


@pytest.mark.parametrize("fault", ["none", "reused", "expired", "connect", "pool_close"])
def test_control_preparation_uses_connected_credentials_and_bounds_private_pool(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    backend = Mock(spec=BaseBackend)
    backend.con = Mock()
    backend.con.headers = {
        "Authorization": "Basic " + b64encode(b"connected_reader:unit-test-only-secret").decode()
    }
    backend._con_kwargs = {
        "host": "127.0.0.1",
        "user": "connected_reader",
        "password": "unit-test-only-secret",
        "settings": {"readonly": 1},
        "session_id": "data-session-only",
    }
    control = Mock(spec=BaseBackend)
    control.name = "clickhouse"
    control.con = backend.con if fault == "reused" else Mock()
    pool = Mock()
    if fault == "pool_close":
        pool.clear.side_effect = RuntimeError("pool close unconfirmed")
    monkeypatch.setattr("urllib3.PoolManager", lambda: pool)
    calls: list[dict[str, object]] = []

    def connect(_name: str, kwargs: Mapping[str, object]) -> BaseBackend:
        calls.append(dict(kwargs))
        if fault in {"connect", "pool_close"}:
            raise RuntimeError("connect failed")
        return control

    profile = replace(provider_for("clickhouse"), connect=connect)
    source = reader_session(backend, profile)
    source._checkpoint = Mock(
        side_effect=[None, RuntimeError("expired")] if fault == "expired" else None
    )
    try:
        if fault == "none":
            source._prepare_interrupt()
            source._prepare_interrupt()
            assert source._cancel_control is control
            assert source._clickhouse_reader == "connected_reader"
            assert not source._cancel_control_released
        else:
            with pytest.raises((RuntimeError, DatasourceSourceCapabilityError)):
                source._prepare_interrupt()
            assert source._cancel_control is None
            assert source._cancel_control_released is (fault != "pool_close")
        assert len(calls) == 1
        assert calls[0]["user"] == "connected_reader"
        assert calls[0]["password"] == "unit-test-only-secret"
        assert calls[0]["connect_timeout"] == calls[0]["send_receive_timeout"] == 1
        assert calls[0]["query_retries"] == 0
        assert calls[0]["pool_mgr"] is pool
        assert calls[0]["settings"] == {"readonly": 1, "max_execution_time": 1}
        assert "session_id" not in calls[0]
        assert source.submissions == []
    finally:
        if fault == "pool_close":
            pool.clear.side_effect = None
        source.close()
    if fault == "none":
        control.disconnect.assert_called_once_with()
        pool.clear.assert_called_once_with()
        assert source._cancel_control_released


@pytest.mark.parametrize("state", ["submitted", "failed", "closed_early", "succeeded"])
def test_owned_active_query_is_cancelled_once_with_exact_reader_parameters(
    monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    source = reader_session(Mock())
    control = Mock(spec=BaseBackend)
    source._cancel_control = control
    source._clickhouse_reader = "connected_reader"
    submission = SourceSubmission("analysis.graph.stage", "facts", 1, "compiled read")
    if state == "failed":
        submission.state = "failed"
    elif state == "closed_early":
        submission.state = "closed_early"
    elif state == "succeeded":
        submission.state = "succeeded"
    source.submissions.append(submission)
    source._clickhouse_active = (submission, "owned-native-id")
    captured: list[Mapping[str, str] | Sequence[object] | None] = []

    def execute(
        _backend: BaseBackend,
        profile: EngineProfile,
        statement_id: str,
        *,
        parameters: Mapping[str, str] | Sequence[object] | None,
        purpose: str,
    ) -> tuple[dict[str, object], ...]:
        assert profile.name == "clickhouse"
        assert statement_id == "clickhouse.analysis.cancel_owned_query"
        assert purpose == "analysis.cancel_owned_query"
        captured.append(parameters)
        return ()

    monkeypatch.setattr(adapters, "execute_provider_statement", execute)
    monkeypatch.setattr(adapters, "provider_statement_log", lambda _backend: ())
    source._request_interrupt()
    source._request_interrupt()
    source._release_clickhouse_query(submission)
    source._request_interrupt()
    source.close()
    assert captured == (
        [] if state == "succeeded" else [{"id": "owned-native-id", "user": "connected_reader"}]
    )
    if state != "succeeded":
        assert submission.termination == "remote_unknown"


def test_missing_permissions_teach_repair_after_native_cursor_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = reader_session(Mock())
    source._cancel_control = Mock(spec=BaseBackend)
    source._clickhouse_reader = "connected_reader"
    source._cancel_pool = Mock()
    source._cancel_control_released = False
    submission = SourceSubmission("analysis.graph.stage", "facts", 1, "compiled read")
    source.submissions.append(submission)
    source._clickhouse_active = (submission, "owned-native-id")
    cursor = Mock()
    stream = SourceBatchStream(source, cursor, pa.schema([("value", pa.int64())]), 1, submission)
    source._streams.add(stream)
    monkeypatch.setattr(
        adapters,
        "execute_provider_statement",
        Mock(side_effect=RuntimeError("Code: 497. Missing SELECT ON system.processes")),
    )
    monkeypatch.setattr(adapters, "provider_statement_log", lambda _backend: ())
    with pytest.raises(DatasourceSourceCapabilityError) as refused:
        stream.close()
    assert (
        refused.value.expected is not None
        and "SELECT(query, query_id, user)" in refused.value.expected
    )
    assert refused.value.received is not None and "497" in refused.value.received
    assert refused.value.repair is not None
    assert "connected_reader" in refused.value.repair.action
    assert "unconfirmed" in refused.value.repair.action
    cursor.close.assert_called_once_with()
    assert submission.cursor_state == "closed"
    assert source._clickhouse_active is None
    source.close()
    assert source._cancel_control_released
    assert submission.termination == "remote_unknown"


def test_control_transport_failure_teaches_connection_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = reader_session(Mock())
    source._cancel_control = Mock(spec=BaseBackend)
    source._clickhouse_reader = "connected_reader"
    submission = SourceSubmission("analysis.graph.stage", "facts", 1, "compiled read")
    source.submissions.append(submission)
    source._clickhouse_active = (submission, "owned-native-id")
    monkeypatch.setattr(
        adapters, "execute_provider_statement", Mock(side_effect=TimeoutError("control timed out"))
    )
    monkeypatch.setattr(adapters, "provider_statement_log", lambda _backend: ())
    source._request_interrupt()
    with pytest.raises(DatasourceSourceCapabilityError) as unavailable:
        source._synchronize_interrupt()
    assert unavailable.value.expected is not None
    assert "bounded control connection" in unavailable.value.expected
    assert "SELECT" not in unavailable.value.expected
    assert unavailable.value.repair is not None
    assert "Repair the bounded control connection" in unavailable.value.repair.action
    assert "remote termination is unconfirmed" in unavailable.value.repair.action
    with pytest.raises(DatasourceSourceCapabilityError):
        source._release_cursor(Mock(), submission)
    source.close()
    assert submission.cursor_state == "closed" and submission.termination == "remote_unknown"


@pytest.mark.parametrize("fault", ["control", "pool"])
def test_unacknowledged_control_close_releases_both_and_stays_unconfirmed(fault: str) -> None:
    source = reader_session(Mock())
    control = Mock(spec=BaseBackend)
    pool = Mock()
    source._cancel_control = control
    source._cancel_pool = pool
    source._cancel_control_released = False
    if fault == "control":
        control.disconnect.side_effect = RuntimeError("control close unconfirmed")
    else:
        pool.clear.side_effect = RuntimeError("pool close unconfirmed")
    with pytest.raises(RuntimeError, match="close unconfirmed"):
        source.close()
    control.disconnect.assert_called_once_with()
    pool.clear.assert_called_once_with()
    assert not source._cancel_control_released
    assert source._closed


@pytest.mark.runtime
def test_real_clickhouse_control_close_failure_keeps_run_obligation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    from marivo.analysis.materialization.errors import MaterializationError, RecoveryPendingError
    from marivo.analysis.materialization.graph_publication import _reconcile_graph
    from marivo.analysis.materialization.writer_guard import session_writer_guard
    from tests.test_r93_capability_consumers import _author_c05_project
    from tests.test_r93_reference_consumers import _data

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    with source_case("clickhouse", "mergetree", tmp_path, monkeypatch, _data("clickhouse")) as case:
        _author_c05_project("clickhouse", case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("r94-clickhouse-close", report_timezone="UTC")
        logical = session.members(ms.ref.entity("sales.facts")).read(
            ms.ref.measure("sales.facts.amount")
        )
        previous = logical.execute()
        saved = previous.to_pandas()
        runtime = session._runtime
        original = SourceSession._prepare_interrupt
        owners: list[SourceSession] = []

        def prepare(source: SourceSession) -> None:
            assert any(
                item.cleanup_capability_id == "clickhouse_owned_control_close@v1"
                for item in runtime.store.resources(runtime.session_ref)
            )
            original(source)
            owners.append(source)
            assert source._cancel_control is not None
            close = source._cancel_control.disconnect

            def unconfirmed() -> None:
                close()
                raise RuntimeError("control close acknowledgement unavailable")

            monkeypatch.setattr(source._cancel_control, "disconnect", unconfirmed)

        monkeypatch.setattr(SourceSession, "_prepare_interrupt", prepare)
        with pytest.raises(MaterializationError, match="source execution failed"):
            logical.execute()
        assert owners and not owners[0]._cancel_control_released
        assert runtime.last_run_ref is not None
        run = runtime.store._graph_run(runtime.last_run_ref)
        assert run is not None and run.lifecycle == "incomplete"
        resources = runtime.store.resources(runtime.session_ref)
        assert len(resources) == 1
        assert resources[0].cleanup_capability_id == "clickhouse_owned_control_close@v1"
        with (
            session_writer_guard(
                runtime.store.layout.lock_path(runtime.session_ref), session_ref=runtime.session_ref
            ),
            pytest.raises(
                RecoveryPendingError, match="original control close acknowledgement is unavailable"
            ),
        ):
            _reconcile_graph(runtime.store, runtime.session_ref, runtime._event)
        assert runtime.store.resources(runtime.session_ref) == resources
        assert previous.to_pandas().equals(saved)


@pytest.mark.runtime
def test_real_clickhouse_fetch_sigint_uses_owned_reader_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ibis

    from marivo.analysis.materialization.execute_deadline import (
        CURRENT,
        ExecuteDeadline,
        check,
        remaining,
    )
    from marivo.datasource.interrupts import owned_sigint
    from tests.multisource_environment import clickhouse_analysis as ch

    with source_case("clickhouse", "mergetree", tmp_path, monkeypatch) as case:
        source = case.session
        source._prepare_interrupt()
        source._checkpoint = check
        source._seconds_remaining = remaining
        bound = source.bind(case.source, source_identity="r94.owned_fetch")
        expression = bound.relation
        for _ in range(18):
            expression = expression.cross_join(bound.relation.view()).select(expression)
        expression = expression.mutate(noise=ibis.random())
        qualified = source.qualify(
            bound, PhysicalRequirement("r94.owned_fetch", 1, frozenset({"scan", "join", "project"}))
        )
        read = source.compile(
            qualified,
            expression,
            purpose="r94.owned_fetch",
            expected_schema=expression.schema().to_pyarrow(),
        )
        identity: str | None = None
        started = time.monotonic()
        token = CURRENT.set(ExecuteDeadline(started))
        try:
            with pytest.raises(KeyboardInterrupt), owned_sigint(source._request_interrupt):
                stream = source.batches(read, chunk_size=1)
                with closing(stream):
                    for _batch in stream:
                        assert source._clickhouse_active is not None
                        identity = source._clickhouse_active[1]
                        with ch.connection(admin=True) as observer:
                            active = observer.query(
                                "SELECT query FROM system.processes WHERE query_id={id:String}",
                                parameters={"id": identity},
                            ).result_rows
                        assert len(active) == 1 and str(active[0][0]).startswith(read.sql)
                        os.kill(os.getpid(), signal.SIGINT)
        finally:
            CURRENT.reset(token)
        elapsed = time.monotonic() - started
        assert identity is not None
        assert elapsed < 3
        assert len(source.cancel_submissions) == 1
        assert source.cancel_submissions[0].state == "succeeded"
        assert source.submissions[-1].state == "closed_early"
        assert source.submissions[-1].cursor_state == "closed"
        assert source._clickhouse_active is None
        with ch.connection(admin=True) as observer:
            events: list[list[object]] = []
            until = time.monotonic() + 3
            while not events and time.monotonic() < until:
                observer.command("SYSTEM FLUSH LOGS")
                events = [
                    list(row)
                    for row in observer.query(
                        "SELECT type, exception_code, query FROM system.query_log WHERE query_id={id:String} AND type IN ('ExceptionBeforeStart','ExceptionWhileProcessing')",
                        parameters={"id": identity},
                    ).result_rows
                ]
                if not events:
                    time.sleep(0.02)
            assert len(events) == 1 and events[0][0] in {
                "ExceptionBeforeStart",
                "ExceptionWhileProcessing",
            }
            assert events[0][1] == 394
            assert str(events[0][2]).startswith(read.sql)
            assert observer.query(
                "SELECT count() FROM system.processes WHERE query_id={id:String}",
                parameters={"id": identity},
            ).first_row == (0,)
        source.close()
        assert source._cancel_control_released and source._cancel_pool is None
        assert source.submissions[-1].termination == "remote_unknown"
        if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
            Path(directory, "r94-clickhouse-owned-fetch.json").write_text(
                json.dumps(
                    {
                        "query_id": identity,
                        "elapsed_seconds": elapsed,
                        "events": events,
                        "active_queries": 0,
                        "control_submissions": [asdict(item) for item in source.cancel_submissions],
                        "control_resources_closed": True,
                        "boundary": "Direct owned source fetch SIGINT; public graph publication is separately tested.",
                    },
                    sort_keys=True,
                )
            )


@pytest.mark.runtime
def test_real_reader_permission_refusal_has_structured_repair_and_closed_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.multisource_environment import clickhouse_analysis as ch
    from tests.multisource_environment.credentials import password

    reader = "r94_cancel_refusal_" + uuid4().hex
    with source_case("clickhouse", "mergetree", tmp_path, monkeypatch) as case:
        created = False
        try:
            with ch.connection(admin=True) as admin:
                admin.command(
                    "CREATE USER " + reader + " IDENTIFIED BY {password:String}",
                    parameters={"password": password()},
                )
                created = True
                admin.command("GRANT SELECT ON qualification.* TO " + reader)
                admin.command(
                    "ALTER USER " + reader + " SETTINGS readonly=1, join_use_nulls=1, "
                    "max_execution_time=0 CHANGEABLE_IN_READONLY"
                )
            declaration = replace(
                case.session.datasource,
                fields={**case.session.datasource.fields, "user": reader},
            )
            with provider_for("clickhouse").open(declaration) as source:
                source._prepare_interrupt()
                assert source._clickhouse_reader == reader
                bound = source.bind(case.source, source_identity="r94.permission_refusal")
                qualified = source.qualify(
                    bound, PhysicalRequirement("r94.permission_refusal", 1, frozenset({"scan"}))
                )
                read = source.compile(
                    qualified,
                    bound.relation,
                    purpose="r94.permission_refusal",
                    expected_schema=bound.relation.schema().to_pyarrow(),
                )
                stream = source.batches(read, chunk_size=1)
                with pytest.raises(DatasourceSourceCapabilityError) as refused:
                    stream.close()
                assert refused.value.expected is not None
                assert "SELECT(query, query_id, user)" in refused.value.expected
                assert refused.value.received is not None and "497" in refused.value.received
                assert refused.value.repair is not None and reader in refused.value.repair.action
                assert source.submissions[-1].cursor_state == "closed"
                assert source._clickhouse_active is None
                assert len(source.cancel_submissions) == 1
                assert source.cancel_submissions[0].state == "failed"
                source.close()
                assert source._cancel_control_released and source._cancel_pool is None
                assert source.submissions[-1].termination == "remote_unknown"
        finally:
            if created:
                with ch.connection(admin=True) as admin:
                    admin.command("DROP USER IF EXISTS " + reader)
