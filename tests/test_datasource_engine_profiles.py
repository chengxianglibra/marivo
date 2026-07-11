from __future__ import annotations

from dataclasses import replace
from typing import cast

import ibis
import pytest
from ibis.backends import BaseBackend

import marivo.datasource as md
from marivo.datasource.authoring import (
    ClickHouseSpec,
    DuckDBSpec,
    MySQLSpec,
    PostgresSpec,
    TrinoSpec,
)
from marivo.datasource.backends import SUPPORTED_BACKEND_TYPES
from marivo.datasource.engines import (
    ENGINE_PROFILES,
    GENERIC_PROFILE,
    profile_for_backend_name,
    profile_for_backend_type,
)


def test_engine_registry_keys_match_supported_backend_types() -> None:
    assert tuple(ENGINE_PROFILES) == SUPPORTED_BACKEND_TYPES
    assert set(ENGINE_PROFILES) == {"duckdb", "trino", "mysql", "postgres", "clickhouse"}


def test_profiles_are_internal_to_datasource_public_api() -> None:
    assert "EngineProfile" not in md.__all__
    assert "ENGINE_PROFILES" not in md.__all__
    assert "profile_for_backend_type" not in md.__all__


def test_every_profile_populates_required_fields() -> None:
    for backend_type, profile in ENGINE_PROFILES.items():
        assert profile.name == backend_type
        assert profile.authoring_func
        assert profile.required_modules
        assert callable(profile.connect)
        assert callable(profile.apply_read_only_kwargs)
        assert profile.identifier_quote in {'"', "`"}
        assert callable(profile.table_name_parts)
        assert profile.metadata.inspect_table is not None
        assert callable(profile.translate_strptime_format)
        assert callable(profile.postprocess_sql)
        assert profile.datetime_decode_policy in {"local_naive_label", "utc_naive_instant"}
        assert callable(profile.authoring_timeout)


def test_every_profile_declares_real_authoring_capabilities() -> None:
    expected = {
        "duckdb": (True, False, True, False),
        "trino": (True, False, True, True),
        "mysql": (True, False, True, True),
        "postgres": (True, False, True, True),
        "clickhouse": (True, False, True, True),
    }

    for backend_type, profile in ENGINE_PROFILES.items():
        capabilities = profile.authoring_capabilities
        assert (
            capabilities.partition_predicate_supported,
            capabilities.transformed_partition_supported,
            capabilities.timeout_enforced,
            capabilities.byte_estimate_supported,
        ) == expected[backend_type]

    generic = GENERIC_PROFILE.authoring_capabilities
    assert (
        generic.partition_predicate_supported,
        generic.transformed_partition_supported,
        generic.timeout_enforced,
        generic.byte_estimate_supported,
    ) == (False, False, False, False)


def test_profile_rejects_timeout_capability_without_matching_hook() -> None:
    with pytest.raises(ValueError, match="timeout_enforced"):
        replace(ENGINE_PROFILES["duckdb"], authoring_timeout=None)


class _Cursor:
    def __init__(self, row: tuple[object, ...] | None = None) -> None:
        self._row = row

    def fetchone(self) -> tuple[object, ...] | None:
        return self._row


class _RawBackend:
    def __init__(self, events: list[str], *, fail_on: str | None = None) -> None:
        self.events = events
        self.calls: list[str] = []
        self.fail_on = fail_on

    def raw_sql(self, sql: str) -> _Cursor:
        self.calls.append(sql)
        if sql in {
            "SET SESSION query_max_run_time = '2s'",
            "SET SESSION MAX_EXECUTION_TIME = 2000",
            "SET LOCAL statement_timeout = '2000ms'",
        }:
            self.events.append("setup")
        elif sql in {
            "SET SESSION query_max_run_time = '5m'",
            "SET SESSION MAX_EXECUTION_TIME = 2500",
            "ROLLBACK",
        }:
            self.events.append("cleanup")
        if self.fail_on is not None and self.fail_on in sql:
            raise RuntimeError("setup failed")
        if sql.startswith("SHOW SESSION"):
            return _Cursor(("query_max_run_time", "5m"))
        if "@@SESSION.MAX_EXECUTION_TIME" in sql:
            return _Cursor((2500,))
        return _Cursor()


class _ClickHouseParams(dict[str, str]):
    def __init__(self, events: list[str], *, fail_setup: bool = False) -> None:
        super().__init__({"max_execution_time": "60"})
        self.events = events
        self.fail_setup = fail_setup

    def __setitem__(self, key: str, value: str) -> None:
        if key == "max_execution_time":
            self.events.append("setup" if value == "2" else "cleanup")
            if value == "2" and self.fail_setup:
                raise RuntimeError("setup failed")
        super().__setitem__(key, value)


class _ClickHouseConnection:
    def __init__(self, events: list[str], *, fail_setup: bool = False) -> None:
        self.params = _ClickHouseParams(events, fail_setup=fail_setup)


class _ClickHouseBackend:
    def __init__(self, events: list[str], *, fail_setup: bool = False) -> None:
        self.con = _ClickHouseConnection(events, fail_setup=fail_setup)


@pytest.mark.parametrize("backend_type", tuple(ENGINE_PROFILES))
def test_authoring_timeout_orders_setup_execute_cleanup_on_error(
    backend_type: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = ENGINE_PROFILES[backend_type]
    hook = profile.authoring_timeout
    assert hook is not None
    events: list[str] = []

    if backend_type == "duckdb":

        class _Timer:
            def start(self) -> None:
                events.append("setup")

            def cancel(self) -> None:
                events.append("cleanup")

        monkeypatch.setattr(
            "marivo.datasource.engines.duckdb.Timer",
            lambda _seconds, _interrupt: _Timer(),
        )
        backend_object = ibis.duckdb.connect()
    elif backend_type == "clickhouse":
        backend_object = _ClickHouseBackend(events)
    else:
        backend_object = _RawBackend(events)
    backend = cast("BaseBackend", backend_object)

    try:
        with pytest.raises(RuntimeError, match="execution failed"), hook(backend, 2):
            events.append("execute")
            raise RuntimeError("execution failed")
    finally:
        disconnect = getattr(backend_object, "disconnect", None)
        if callable(disconnect):
            disconnect()

    assert events == ["setup", "execute", "cleanup"]
    if backend_type == "trino":
        assert isinstance(backend_object, _RawBackend)
        assert backend_object.calls == [
            "SHOW SESSION LIKE 'query_max_run_time'",
            "SET SESSION query_max_run_time = '2s'",
            "SET SESSION query_max_run_time = '5m'",
        ]
    elif backend_type == "mysql":
        assert isinstance(backend_object, _RawBackend)
        assert backend_object.calls == [
            "SELECT @@SESSION.MAX_EXECUTION_TIME",
            "SET SESSION MAX_EXECUTION_TIME = 2000",
            "SET SESSION MAX_EXECUTION_TIME = 2500",
        ]
    elif backend_type == "postgres":
        assert isinstance(backend_object, _RawBackend)
        assert backend_object.calls == [
            "BEGIN READ ONLY",
            "SET LOCAL statement_timeout = '2000ms'",
            "ROLLBACK",
        ]
    elif backend_type == "clickhouse":
        assert isinstance(backend_object, _ClickHouseBackend)
        assert backend_object.con.params["max_execution_time"] == "60"


@pytest.mark.parametrize(
    ("backend_type", "failure_statement"),
    [
        ("duckdb", None),
        ("trino", "SET SESSION query_max_run_time = '2s'"),
        ("mysql", "SET SESSION MAX_EXECUTION_TIME = 2000"),
        ("postgres", "SET LOCAL statement_timeout = '2000ms'"),
        ("clickhouse", None),
    ],
)
def test_authoring_timeout_setup_failure_never_enters_execution(
    backend_type: str,
    failure_statement: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = ENGINE_PROFILES[backend_type]
    hook = profile.authoring_timeout
    assert hook is not None
    events: list[str] = []
    if backend_type == "duckdb":

        class _FailingTimer:
            def start(self) -> None:
                events.append("setup")
                raise RuntimeError("setup failed")

            def cancel(self) -> None:
                events.append("cleanup")

        monkeypatch.setattr(
            "marivo.datasource.engines.duckdb.Timer",
            lambda _seconds, _interrupt: _FailingTimer(),
        )
        backend_object = ibis.duckdb.connect()
    elif backend_type == "clickhouse":
        backend_object = _ClickHouseBackend(events, fail_setup=True)
    else:
        backend_object = _RawBackend(events, fail_on=failure_statement)
    executions = 0

    try:
        with (
            pytest.raises(RuntimeError, match="setup failed"),
            hook(cast("BaseBackend", backend_object), 2),
        ):
            executions += 1
    finally:
        disconnect = getattr(backend_object, "disconnect", None)
        if callable(disconnect):
            disconnect()

    assert executions == 0
    assert events == ["setup", "cleanup"]
    if not isinstance(backend_object, _RawBackend):
        return
    if backend_type == "trino":
        assert backend_object.calls[-1] == "SET SESSION query_max_run_time = '5m'"
    elif backend_type == "mysql":
        assert backend_object.calls[-1] == "SET SESSION MAX_EXECUTION_TIME = 2500"
    elif backend_type == "postgres":
        assert backend_object.calls[-1] == "ROLLBACK"


def test_aliases_are_unique_and_resolve_to_profiles() -> None:
    seen: dict[str, str] = {}
    for profile in ENGINE_PROFILES.values():
        for alias in profile.aliases:
            assert alias not in seen
            seen[alias] = profile.name
            assert profile_for_backend_name(alias) is profile
    assert profile_for_backend_name("presto").name == "trino"
    assert profile_for_backend_name("postgresql").name == "postgres"
    assert profile_for_backend_name("redshift").name == "postgres"


def test_unknown_backend_name_resolves_to_generic_profile() -> None:
    assert profile_for_backend_name("snowflake") is GENERIC_PROFILE
    assert profile_for_backend_name(None) is GENERIC_PROFILE


def test_registered_profiles_do_not_use_generic_metadata_inspector() -> None:
    from marivo.datasource.engines.base import generic_metadata_inspect

    for profile in ENGINE_PROFILES.values():
        assert profile.metadata.inspect_table is not generic_metadata_inspect


def test_authoring_specs_resolve_to_profiles() -> None:
    specs = (
        DuckDBSpec(name="duck"),
        TrinoSpec(name="tri", host="h", catalog="c"),
        MySQLSpec(name="my", host="h", database="d"),
        PostgresSpec(name="pg", host="h", database="d"),
        ClickHouseSpec(name="ch", host="h"),
    )
    assert {profile_for_backend_type(spec.backend_type).name for spec in specs} == {
        "duckdb",
        "trino",
        "mysql",
        "postgres",
        "clickhouse",
    }
