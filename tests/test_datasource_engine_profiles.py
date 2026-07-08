from __future__ import annotations

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
