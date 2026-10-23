"""Native timeout configuration, unsupported reads and independent cleanup."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from threading import get_ident
from unittest.mock import Mock

import pytest
from ibis.backends import BaseBackend

import marivo.datasource as md
from marivo.datasource import adapters, backends, manage, runtime
from marivo.datasource._lifetime import BackendLease
from marivo.datasource.adapters import (
    SourceSession,
    provider_for,
)
from marivo.datasource.engines import mysql
from marivo.datasource.errors import DatasourceSourceCapabilityError
from tests.datasource.source_cases import datasource


def test_provider_session_reuses_the_built_backend_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = Mock(spec=BaseBackend)
    backend.name = "duckdb"
    built = backends.BuiltDatasourceBackend(backend, ())
    monkeypatch.setattr(runtime, "open_backend", lambda *_args, **_kwargs: built)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))

    session = provider_for("duckdb").open(datasource("duckdb", {}))
    assert session._backend_lease is built.lease
    built.disconnect()
    session.close()
    session.close()
    assert session._backend_disconnected
    backend.disconnect.assert_called_once_with()


@pytest.mark.parametrize("borrowed", (False, True))
def test_source_session_rejects_borrowed_or_mismatched_lease(
    borrowed: bool, tmp_path: Path
) -> None:
    backend = Mock(spec=BaseBackend)
    backend.name = "duckdb"
    lease = BackendLease(backend if borrowed else Mock(spec=BaseBackend))
    with pytest.raises(DatasourceSourceCapabilityError, match="borrowed or mismatched lease"):
        SourceSession(
            provider_for("duckdb"),
            datasource("duckdb", {}),
            backend,
            owns_backend=not borrowed,
            backend_lease=lease,
            project_root=tmp_path,
        )
    backend.disconnect.assert_not_called()
    assert not lease.closed


@pytest.mark.parametrize("failed", (False, True))
@pytest.mark.parametrize("persist", (False, True))
def test_probe_releases_the_owned_backend_and_caches_only_success(
    failed: bool, persist: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = Mock(spec=BaseBackend)
    backend.name = "trino"
    backend._marivo_terminal_timeout_seconds = 7
    backend.con = Mock()
    backend.con.session_properties = {"query_max_run_time": "7s"}
    built = backends.BuiltDatasourceBackend(backend, ())
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(manage, "load_datasource", lambda *_args: datasource("trino", {}))
    monkeypatch.setattr(manage, "open_backend", lambda *_args, **_kwargs: built)
    probe = Mock(side_effect=RuntimeError("probe failed") if failed else None)
    monkeypatch.setattr(adapters, "_probe_backend", probe)
    cache = Mock()
    monkeypatch.setattr(manage._secrets, "try_persist_backend_env_sourced", cache)

    result = (
        md.load().test("fixture", timeout_seconds=7)
        if persist
        else manage.test_no_persist("fixture", timeout_seconds=7, project_root=tmp_path)
    )
    assert result.ok is not failed
    probe.assert_called_once_with("trino", backend)
    if failed:
        assert result.failure is not None
        assert result.failure.code == "connection_roundtrip_failed"
    if persist and not failed:
        cache.assert_called_once_with(backend)
    else:
        cache.assert_not_called()
    built.disconnect()
    backend.disconnect.assert_called_once_with()


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
    statement = Mock(
        side_effect=[
            RuntimeError(code, "native configuration error"),
            ({"server_version": "8.0.36"},),
        ]
    )
    monkeypatch.setattr(mysql, "execute_provider_statement", statement)
    entered: list[bool] = []
    if code == 1193:
        with mysql.native_query_timeout(backend, 7):
            entered.append(True)
        assert entered == [True]
    else:
        with pytest.raises(DatasourceSourceCapabilityError), mysql.native_query_timeout(backend, 7):
            pytest.fail("Denied configuration must not enter the read")
    assert statement.call_count == (2 if code == 1193 else 1)


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
