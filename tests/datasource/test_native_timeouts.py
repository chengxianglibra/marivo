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
from marivo.datasource import secrets as datasource_secrets
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
    monkeypatch.setattr(datasource_secrets, "try_persist_backend_env_sourced", cache)

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
        backends.EffectiveDatasourceKwargs(kwargs=kwargs, injected=(), env_sourced_secrets=()),
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
    finally:
        built.disconnect()
    backend.disconnect.assert_called_once_with()


@pytest.mark.parametrize("code", (1193, 1227))
def test_mysql_unknown_timeout_variable_fails_closed_like_permission_denial(
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
    with pytest.raises(DatasourceSourceCapabilityError), mysql.native_query_timeout(backend, 7):
        pytest.fail("Unconfirmed configuration must not enter the read")
    assert statement.call_count == (2 if code == 1193 else 1)
    assert getattr(backend, "_marivo_native_timeout_unsupported", False) is not True
    assert getattr(backend, "_marivo_configured_query_timeout_seconds", None) is None


@pytest.mark.parametrize(
    ("version", "failed_installs"),
    (
        ("8.0.36", ("install_select_deadline",)),
        ("10.11.6-MariaDB", ("install_select_deadline", "install_mariadb_deadline")),
    ),
)
def test_missing_native_timeout_is_not_cached_as_success(
    version: str, failed_installs: tuple[str, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = Mock(spec=BaseBackend)
    backend._marivo_terminal_timeout_seconds = 7
    calls: list[str] = []

    def execute(
        _backend: BaseBackend,
        _profile: object,
        statement_id: str,
        **_kwargs: object,
    ) -> tuple[dict[str, object], ...]:
        calls.append(statement_id)
        if statement_id.endswith("server_version"):
            return ({"server_version": version},)
        if any(statement_id.endswith(name) for name in failed_installs):
            raise RuntimeError(1193, "Unknown system variable")
        raise AssertionError(statement_id)

    monkeypatch.setattr(mysql, "execute_provider_statement", execute)
    per_entry = 2 if len(failed_installs) == 1 else 3
    for _entry in range(2):
        before = len(calls)
        with (
            pytest.raises(DatasourceSourceCapabilityError) as failure,
            mysql.native_query_timeout(backend, 7),
        ):
            pytest.fail("A missing native timeout must not enter the read")
        assert len(calls) - before == per_entry
        assert isinstance(failure.value.__cause__, RuntimeError)
        assert failure.value.__cause__.args[0] == 1193
    assert getattr(backend, "_marivo_native_timeout_unsupported", False) is not True
    assert getattr(backend, "_marivo_configured_query_timeout_seconds", None) is None


@pytest.mark.parametrize("server", ("mysql", "mariadb"))
def test_confirmed_native_timeout_is_reused_for_the_same_seconds(
    server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = Mock(spec=BaseBackend)
    backend._marivo_terminal_timeout_seconds = 7
    calls: list[str] = []

    def execute(
        _backend: BaseBackend,
        _profile: object,
        statement_id: str,
        **_kwargs: object,
    ) -> tuple[dict[str, object], ...]:
        calls.append(statement_id)
        if statement_id.endswith("install_select_deadline"):
            if server == "mariadb":
                raise RuntimeError(1193, "Unknown system variable 'max_execution_time'")
            return ()
        if statement_id.endswith("server_version"):
            return ({"server_version": "10.11.6-MariaDB"},)
        if statement_id.endswith("install_mariadb_deadline"):
            return ()
        if statement_id.endswith("read_select_deadline"):
            return ({"@@session.max_execution_time": 7000},)
        if statement_id.endswith("read_mariadb_deadline"):
            return ({"@@session.max_statement_time": 7},)
        raise AssertionError(statement_id)

    monkeypatch.setattr(mysql, "execute_provider_statement", execute)
    for _entry in range(2):
        entered = False
        with mysql.native_query_timeout(backend, 7):
            entered = True
        assert entered is True
    expected = (
        [
            "mysql.authoring.install_select_deadline",
            "mysql.authoring.server_version",
            "mysql.authoring.install_mariadb_deadline",
            "mysql.authoring.read_mariadb_deadline",
        ]
        if server == "mariadb"
        else [
            "mysql.authoring.install_select_deadline",
            "mysql.authoring.read_select_deadline",
        ]
    )
    assert calls == expected
    assert backend._marivo_configured_query_timeout_seconds == 7


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


def test_connection_service_closes_other_owners_after_source_cleanup_failure(
    tmp_path: Path,
) -> None:
    from marivo.datasource._lifetime import BackendLease

    service = runtime.DatasourceConnectionService(tmp_path)
    owners = [Mock(spec=SourceSession), Mock(spec=SourceSession)]
    for owner in owners:
        owner._closed = False
    owners[0].close.side_effect = OSError("first source cleanup failed")
    backends = [Mock(spec=BaseBackend), Mock(spec=BaseBackend)]
    service._source_sessions = dict(zip((("first", None), ("second", None)), owners, strict=True))
    service._session_backends = dict(zip(("first", "second"), backends, strict=True))
    service._leases = {
        name: BackendLease(backend) for name, backend in service._session_backends.items()
    }
    with pytest.raises(OSError):
        service.close_all()
    for owner, backend in zip(owners, backends, strict=True):
        owner.close.assert_called_once_with()
        backend.disconnect.assert_called_once_with()
        owner.mark_backend_disconnected.assert_called_once_with()
    assert not service._leases and not service._source_sessions


@pytest.mark.parametrize("borrowed", (False, True))
def test_source_session_rejects_borrowed_or_mismatched_lease(
    borrowed: bool, tmp_path: Path
) -> None:
    backend = Mock(spec=BaseBackend)
    backend.name = "duckdb"
    lease = BackendLease(backend if borrowed else Mock(spec=BaseBackend))
    with pytest.raises(DatasourceSourceCapabilityError):
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


def test_failed_disconnect_cannot_confirm_cleanup(tmp_path: Path) -> None:
    backend = Mock(spec=BaseBackend)
    backend.name = "mysql"
    backend.disconnect.side_effect = OSError("disconnect unconfirmed")
    session = SourceSession(
        provider_for("mysql"), datasource("mysql", {}), backend, project_root=tmp_path
    )
    with pytest.raises(OSError):
        session.close()
    assert not session._backend_disconnected
