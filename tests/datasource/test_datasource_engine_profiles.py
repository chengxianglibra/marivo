from __future__ import annotations

from dataclasses import replace
from typing import cast

import pytest
from ibis.backends import BaseBackend

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
    assert set(ENGINE_PROFILES) == {
        "duckdb",
        "trino",
        "mysql",
        "postgres",
        "clickhouse",
    }


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
        assert (
            profile.native_query_timeout is not None
        ) == profile.authoring_capabilities.timeout_enforced


def test_every_profile_declares_real_authoring_capabilities() -> None:
    expected = {
        "duckdb": (True, False, False, False),
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
        replace(ENGINE_PROFILES["postgres"], native_query_timeout=None)


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


@pytest.mark.parametrize("backend_type", ("postgres", "trino", "clickhouse"))
def test_remote_timeout_uses_driver_state_without_control_sql(backend_type: str) -> None:
    backend = _Backend()
    hook = ENGINE_PROFILES[backend_type].native_query_timeout
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
    hook = ENGINE_PROFILES[backend_type].native_query_timeout
    assert hook is not None
    with pytest.raises(RuntimeError, match="no configured"), hook(cast("BaseBackend", backend), 2):
        pytest.fail("execution started without timeout")


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
        TrinoSpec(name="tri", host="h", catalog="c", user_env="TRINO_USER"),
        MySQLSpec(name="my", host="h", database="d"),
        PostgresSpec(name="pg", host="h", database="d"),
        ClickHouseSpec(name="ch", host="h"),
    )
    names = set()
    for spec in specs:
        profile = profile_for_backend_type(spec.backend_type)
        assert profile is not None
        names.add(profile.name)
    assert names == {
        "duckdb",
        "trino",
        "mysql",
        "postgres",
        "clickhouse",
    }
