"""Native timeout configuration, unsupported reads and independent cleanup."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from threading import get_ident
from unittest.mock import Mock

import pyarrow as pa
import pytest
from ibis.backends import BaseBackend

from marivo.datasource import backends, runtime
from marivo.datasource.adapters import (
    SourceBatchStream,
    SourceSession,
    SourceSubmission,
    provider_for,
)
from marivo.datasource.engines import mysql
from marivo.datasource.errors import DatasourceSourceCapabilityError
from tests.datasource.source_cases import datasource


@pytest.mark.parametrize("backend_kind", ("postgres", "trino", "clickhouse", "mysql"))
def test_native_configuration_precedes_reader_use_without_control_connection(
    backend_kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = Mock(spec=BaseBackend)
    backend.name = backend_kind
    backend.con = Mock()
    connections: list[dict[str, object]] = []

    def connect(_name: str, kwargs: Mapping[str, object]) -> BaseBackend:
        connections.append(dict(kwargs))
        return backend

    profile = replace(provider_for(backend_kind), connect=connect)
    monkeypatch.setattr(backends, "require_profile_for_backend_type", lambda _kind: profile)
    statements = Mock(return_value=({"@@session.max_execution_time": 7000},))
    monkeypatch.setattr(mysql, "execute_provider_statement", statements)
    kwargs: dict[str, object] = {"host": "localhost", "database": "fixture"}
    if backend_kind == "postgres":
        kwargs["options"] = "-c application_name=native_timeout_fixture"
    elif backend_kind == "trino":
        kwargs["session_properties"] = {
            "query_max_run_time": "99s",
            "join_distribution_type": "BROADCAST",
        }
    built = backends._build_backend_from_effective(
        datasource(backend_kind, {}),
        backends.EffectiveDatasourceKwargs(kwargs=kwargs, injected={}, env_sourced_secrets=()),
        read_only=True,
        terminal_timeout_seconds=7,
        connection_timeout_seconds=3,
    )
    try:
        assert len(connections) == 1
        actual = connections[0]
        if backend_kind == "postgres":
            assert "application_name=native_timeout_fixture" in str(actual["options"])
            assert "statement_timeout=7000" in str(actual["options"])
            assert actual["connect_timeout"] == 3
            assert backend.con.read_only
        elif backend_kind == "trino":
            assert actual["session_properties"] == {
                "query_max_run_time": "7s",
                "join_distribution_type": "BROADCAST",
            }
            assert actual["request_timeout"] == 3
        elif backend_kind == "mysql":
            assert (
                actual["connect_timeout"] == actual["read_timeout"] == actual["write_timeout"] == 3
            )
            assert [call.args[2] for call in statements.call_args_list] == [
                "mysql.authoring.install_select_deadline",
                "mysql.authoring.read_select_deadline",
            ]
            assert backend._marivo_configured_query_timeout_seconds == 7
        else:
            assert actual["connect_timeout"] == actual["send_receive_timeout"] == 3
            assert actual["settings"]["timeout_overflow_mode"] == "throw"
        assert not hasattr(backend, "_marivo_authoring_cancel_control")
    finally:
        built.disconnect()
    backend.disconnect.assert_called_once_with()


@pytest.mark.parametrize("code", (1193, 1227))
def test_mysql_unsupported_native_setting_is_distinct_from_permission_denial(
    code: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = Mock(spec=BaseBackend)
    backend._marivo_terminal_timeout_seconds = 7
    statement = Mock(side_effect=RuntimeError(code, "native configuration error"))
    monkeypatch.setattr(mysql, "execute_provider_statement", statement)
    entered: list[bool] = []
    if code == 1193:
        with mysql.native_query_timeout(backend, 7):
            entered.append(True)
        assert entered == [True]
    else:
        with pytest.raises(DatasourceSourceCapabilityError), mysql.native_query_timeout(backend, 7):
            pytest.fail("Denied configuration must not enter the read")
    assert statement.call_count == 1


def test_connection_is_synchronous_and_has_no_elapsed_time_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = Mock(spec=BaseBackend)
    owner = get_ident()
    calls: list[int] = []

    def build(*_args: object, **kwargs: object) -> backends.BuiltDatasourceBackend:
        calls.append(get_ident())
        assert kwargs["connection_timeout_seconds"] == 1
        return backends.BuiltDatasourceBackend(backend, ())

    monkeypatch.setattr(backends, "build_backend_with_secrets", build)
    built = runtime.open_backend(datasource("mysql", {}), project_root=tmp_path, timeout_seconds=1)
    assert calls == [owner]
    assert not hasattr(runtime, "run_with_deadline")
    built.disconnect()
    backend.disconnect.assert_called_once_with()


def test_unscoped_connection_preserves_declared_native_transport_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = Mock(spec=BaseBackend)
    backend.name = "mysql"
    calls: list[dict[str, object]] = []

    def connect(_name: str, kwargs: Mapping[str, object]) -> BaseBackend:
        calls.append(dict(kwargs))
        return backend

    profile = replace(provider_for("mysql"), connect=connect)
    monkeypatch.setattr(backends, "require_profile_for_backend_type", lambda _kind: profile)
    declared = {
        "host": "localhost",
        "database": "fixture",
        "connect_timeout": 4,
        "read_timeout": 11,
    }
    built = runtime.open_backend(datasource("mysql", declared), project_root=tmp_path)
    try:
        assert calls == [declared]
        assert not hasattr(backend, "_marivo_terminal_timeout_seconds")
    finally:
        built.disconnect()


@pytest.mark.parametrize("native", (False, True))
def test_probe_timeout_requires_native_evidence_and_retains_backend_diagnostics(
    native: bool, tmp_path: Path
) -> None:
    from marivo.datasource import manage

    def fail(_state: manage._ProbeState) -> manage.DatasourceTestResult:
        if native:
            raise RuntimeError(3024, "native SELECT timeout")
        raise RuntimeError("Code: 3024; elapsed time by itself is not native timeout evidence")

    result = manage._run_roundtrip(
        fail, timeout_seconds=7, datasource_name="fixture", project_root=tmp_path
    )
    assert result.failure is not None
    assert result.failure.backend_code == "3024"
    assert result.failure.exception_type == "RuntimeError"
    assert result.failure.code == ("connection_timeout" if native else "connection_open_failed")
    assert result.failure.timeout_seconds == (7 if native else None)


@pytest.mark.parametrize("backend_kind", ("mysql", "clickhouse"))
@pytest.mark.parametrize("owns_backend", (False, True))
def test_cleanup_visits_remaining_resources_after_cursor_failure(
    backend_kind: str, owns_backend: bool, tmp_path: Path
) -> None:
    backend = Mock(spec=BaseBackend)
    backend.name = backend_kind
    session = SourceSession(
        provider_for(backend_kind),
        datasource(backend_kind, {}),
        backend,
        owns_backend=owns_backend,
        project_root=tmp_path,
    )
    cursors = [Mock(), Mock()]
    cursors[0].close.side_effect = OSError("native cursor release failed")
    submissions = []
    for cursor in cursors:
        submission = SourceSubmission("fixture", "facts", 1, "SELECT value FROM facts")
        submissions.append(submission)
        session.submissions.append(submission)
        session._streams.add(
            SourceBatchStream(session, cursor, pa.schema([("value", pa.int64())]), 1, submission)
        )
    with pytest.raises(OSError, match="native cursor release failed"):
        session.close()
    for cursor in cursors:
        cursor.close.assert_called_once_with()
    assert not session._streams
    if owns_backend:
        backend.disconnect.assert_called_once_with()
        assert all(submission.connection_disconnected for submission in submissions)
    else:
        backend.disconnect.assert_not_called()
        assert submissions[0].cursor_state == "close_failed"
        backend.disconnect()
        session.mark_backend_disconnected()
        assert all(submission.connection_disconnected for submission in submissions)
    assert all(submission.termination == "remote_unknown" for submission in submissions)
    assert not hasattr(session, "_cancel_control")


def test_failed_disconnect_cannot_confirm_cleanup(tmp_path: Path) -> None:
    backend = Mock(spec=BaseBackend)
    backend.name = "mysql"
    backend.disconnect.side_effect = OSError("disconnect unconfirmed")
    session = SourceSession(
        provider_for("mysql"), datasource("mysql", {}), backend, project_root=tmp_path
    )
    with pytest.raises(OSError, match="disconnect unconfirmed"):
        session.close()
    assert not session._backend_disconnected


def test_connection_service_closes_other_owners_after_source_cleanup_failure(
    tmp_path: Path,
) -> None:
    from marivo.datasource._lifetime import BackendLease

    service = runtime.DatasourceConnectionService(tmp_path)
    owners = [Mock(spec=SourceSession), Mock(spec=SourceSession)]
    owners[0].close.side_effect = OSError("first source cleanup failed")
    backends = [Mock(spec=BaseBackend), Mock(spec=BaseBackend)]
    service._source_sessions = dict(zip(("first", "second"), owners, strict=True))
    service._session_backends = dict(zip(("first", "second"), backends, strict=True))
    service._leases = {
        name: BackendLease(backend) for name, backend in service._session_backends.items()
    }
    with pytest.raises(OSError, match="first source cleanup failed"):
        service.close_all()
    for owner, backend in zip(owners, backends, strict=True):
        owner.close.assert_called_once_with()
        backend.disconnect.assert_called_once_with()
        owner.mark_backend_disconnected.assert_called_once_with()
    assert not service._leases and not service._source_sessions
