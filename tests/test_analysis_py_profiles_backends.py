"""Backend dispatch tests for marivo.analysis_py.profiles."""

from __future__ import annotations

from pathlib import Path

import pytest

import marivo.analysis_py as mv
from marivo.analysis_py.errors import (
    ProfileBackendTypeUnsupportedError,
    ProfileEnvVarMissingError,
    ProfileFieldInvalidError,
)
from marivo.analysis_py.profiles import backends as profile_backends
from marivo.analysis_py.profiles import store as profile_store


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("MARIVO_HOME", str(home))
    return home


def test_build_duckdb_in_memory(fake_home: Path) -> None:
    mv.profiles.set("local", backend_type="duckdb", path=":memory:")
    backend = mv.profiles.build_backend("local")
    # ibis DuckDB backend exposes list_tables(); empty for a fresh in-memory db.
    assert backend.list_tables() == []


def test_env_ref_resolution(fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_PWD", "shhh")
    profile = profile_store.save_one(
        name="wh",
        backend_type="trino",
        fields={"host": "h", "catalog": "c", "password_env": "MY_PWD"},
    )
    resolved = profile_backends._resolve_env_refs(profile)
    assert resolved["password"] == "shhh"
    assert resolved["host"] == "h"
    assert "password_env" not in resolved


def test_env_ref_missing_var(fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MY_PWD", raising=False)
    profile = profile_store.save_one(
        name="wh",
        backend_type="trino",
        fields={"host": "h", "catalog": "c", "password_env": "MY_PWD"},
    )
    with pytest.raises(ProfileEnvVarMissingError) as exc_info:
        profile_backends._resolve_env_refs(profile)
    assert exc_info.value.details["env_var"] == "MY_PWD"
    assert exc_info.value.details["field"] == "password"


def test_unsupported_backend_type(fake_home: Path) -> None:
    # Inject a raw entry directly via store (bypassing registry guard) to test dispatch.
    profile_store.save_one(name="wh", backend_type="duckdb", fields={"path": ":memory:"})
    profile = profile_store.load_one("wh")
    assert profile is not None
    bogus = profile_store.StoredProfile(
        name="wh", backend_type="wat-backend", fields=profile.fields
    )
    with pytest.raises(ProfileBackendTypeUnsupportedError) as exc_info:
        profile_backends.build_backend(bogus)
    assert exc_info.value.details["backend_type"] == "wat-backend"


def test_trino_required_field_missing(fake_home: Path) -> None:
    profile = profile_store.save_one(name="wh", backend_type="trino", fields={"host": "h"})
    with pytest.raises(ProfileFieldInvalidError) as exc_info:
        profile_backends.build_backend(profile)
    assert exc_info.value.details["field"] == "catalog"
