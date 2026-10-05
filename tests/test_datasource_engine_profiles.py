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
    SQLiteSpec,
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
    assert set(ENGINE_PROFILES) == {
        "duckdb",
        "sqlite",
        "trino",
        "mysql",
        "postgres",
        "clickhouse",
    }


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
        assert not hasattr(profile, "postprocess_sql")
        assert profile.datetime_decode_policy in {"local_naive_label", "utc_naive_instant"}
        assert callable(profile.authoring_timeout)


def test_every_profile_declares_real_authoring_capabilities() -> None:
    expected = {
        "duckdb": (True, False, True, False),
        "sqlite": (True, False, True, False),
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


class _Connection:
    def __init__(self) -> None:
        self.read_only = True
        self.autocommit = False
        self.session_properties = {"query_max_run_time": "2s"}
        self.params = {"max_execution_time": "60"}
        self.rollbacks = 0

    def rollback(self) -> None:
        self.rollbacks += 1


class _Backend:
    def __init__(self) -> None:
        self.con = _Connection()
        self._marivo_terminal_timeout_seconds = 2


def test_mysql_timeout_is_unavailable_without_driver_control() -> None:
    profile = ENGINE_PROFILES["mysql"]
    hook = profile.authoring_timeout
    assert hook is not None
    assert profile.authoring_capabilities.timeout_enforced is True
    with (
        pytest.raises(RuntimeError, match="isolated owned reader"),
        hook(cast("BaseBackend", _Backend()), 2),
    ):
        pytest.fail("Missing MySQL owner/control reached authoring execution")


@pytest.mark.parametrize("backend_type", ("postgres", "trino", "clickhouse"))
def test_remote_timeout_uses_driver_state_without_control_sql(backend_type: str) -> None:
    backend = _Backend()
    hook = ENGINE_PROFILES[backend_type].authoring_timeout
    assert hook is not None
    with hook(cast("BaseBackend", backend), 2):
        assert not hasattr(backend, "raw_sql")
    if backend_type == "postgres":
        assert backend.con.rollbacks == 1
    else:
        assert backend.con.rollbacks == 0
    assert backend.con.params["max_execution_time"] == "60"


@pytest.mark.parametrize("backend_type", ("postgres", "trino"))
def test_remote_timeout_rejects_missing_connection_configuration(backend_type: str) -> None:
    backend = _Backend()
    backend._marivo_terminal_timeout_seconds = 3
    hook = ENGINE_PROFILES[backend_type].authoring_timeout
    assert hook is not None
    with pytest.raises(RuntimeError, match="no configured"), hook(cast("BaseBackend", backend), 2):
        pytest.fail("execution started without timeout")


@pytest.mark.parametrize("backend_type", ("duckdb", "sqlite"))
def test_local_timeout_uses_driver_interrupt(
    backend_type: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []

    class Timer:
        def __init__(self, _seconds: int, _interrupt: object) -> None:
            pass

        def start(self) -> None:
            events.append("start")

        def cancel(self) -> None:
            events.append("cancel")

    monkeypatch.setattr(f"marivo.datasource.engines.{backend_type}.Timer", Timer)
    backend = ibis.duckdb.connect() if backend_type == "duckdb" else ibis.sqlite.connect()
    hook = ENGINE_PROFILES[backend_type].authoring_timeout
    assert hook is not None
    try:
        with hook(backend, 2):
            events.append("execute")
    finally:
        backend.disconnect()
    assert events == ["start", "execute", "cancel"]


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
    assert profile_for_backend_name("sqlite3").name == "sqlite"


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
        SQLiteSpec(name="lite"),
        TrinoSpec(name="tri", host="h", catalog="c", user_env="TRINO_USER"),
        MySQLSpec(name="my", host="h", database="d"),
        PostgresSpec(name="pg", host="h", database="d"),
        ClickHouseSpec(name="ch", host="h"),
    )
    assert {profile_for_backend_type(spec.backend_type).name for spec in specs} == {
        "duckdb",
        "sqlite",
        "trino",
        "mysql",
        "postgres",
        "clickhouse",
    }
