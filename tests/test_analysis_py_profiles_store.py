"""Storage-layer tests for marivo.analysis_py.profiles."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from marivo.analysis_py.errors import (
    ProfileFieldInvalidError,
    ProfileSchemaVersionError,
    ProfileSecretInPlaintextError,
)
from marivo.analysis_py.profiles import store as profile_store


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``$MARIVO_HOME`` to a temp dir to isolate the user registry."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("MARIVO_HOME", str(home))
    return home


def test_profiles_path_uses_marivo_home(fake_home: Path) -> None:
    assert profile_store.profiles_path() == fake_home / "profiles" / "profiles.json"


def test_load_all_empty_when_no_file(fake_home: Path) -> None:
    assert profile_store.load_all() == {}


def test_save_roundtrip(fake_home: Path) -> None:
    profile_store.save_one(
        name="warehouse",
        backend_type="trino",
        fields={
            "host": "trino.example",
            "port": 8080,
            "user": "analytics",
            "catalog": "hive",
            "password_env": "WAREHOUSE_PWD",
        },
    )
    profiles = profile_store.load_all()
    assert set(profiles) == {"warehouse"}
    assert profiles["warehouse"].backend_type == "trino"
    assert profiles["warehouse"].fields["host"] == "trino.example"
    assert profiles["warehouse"].fields["password_env"] == "WAREHOUSE_PWD"


def test_save_overwrites_same_name(fake_home: Path) -> None:
    profile_store.save_one(name="wh", backend_type="duckdb", fields={"path": ":memory:"})
    profile_store.save_one(name="wh", backend_type="duckdb", fields={"path": "/tmp/foo.ddb"})
    assert profile_store.load_one("wh") is not None
    assert profile_store.load_one("wh").fields["path"] == "/tmp/foo.ddb"  # type: ignore[union-attr]


def test_save_rejects_plaintext_sensitive_field(fake_home: Path) -> None:
    with pytest.raises(ProfileSecretInPlaintextError) as exc_info:
        profile_store.save_one(
            name="wh",
            backend_type="trino",
            fields={"host": "h", "catalog": "c", "password": "literal-secret"},
        )
    assert exc_info.value.details["field"] == "password"
    assert exc_info.value.details["datasource"] == "wh"
    # error must include a pasteable fix snippet showing the *_env form
    assert "password_env" in str(exc_info.value)


def test_save_rejects_empty_backend_type(fake_home: Path) -> None:
    with pytest.raises(ProfileFieldInvalidError) as exc_info:
        profile_store.save_one(name="wh", backend_type="", fields={"path": ":memory:"})
    assert exc_info.value.details["field"] == "backend_type"


def test_save_rejects_non_scalar_value(fake_home: Path) -> None:
    with pytest.raises(ProfileFieldInvalidError):
        profile_store.save_one(
            name="wh",
            backend_type="trino",
            fields={"host": "h", "catalog": "c", "extras": {"nested": "dict"}},
        )


def test_save_rejects_env_ref_non_string(fake_home: Path) -> None:
    with pytest.raises(ProfileFieldInvalidError) as exc_info:
        profile_store.save_one(
            name="wh",
            backend_type="trino",
            fields={"host": "h", "catalog": "c", "password_env": ""},
        )
    assert exc_info.value.details["field"] == "password_env"


def test_delete_one_returns_true_when_removed(fake_home: Path) -> None:
    profile_store.save_one(name="wh", backend_type="duckdb", fields={"path": ":memory:"})
    assert profile_store.delete_one("wh") is True
    assert profile_store.load_one("wh") is None


def test_delete_one_idempotent(fake_home: Path) -> None:
    assert profile_store.delete_one("missing") is False


def test_list_names_sorted(fake_home: Path) -> None:
    profile_store.save_one(name="b", backend_type="duckdb", fields={"path": ":memory:"})
    profile_store.save_one(name="a", backend_type="duckdb", fields={"path": ":memory:"})
    assert profile_store.list_names() == ["a", "b"]


def test_save_sets_file_mode_0600(fake_home: Path) -> None:
    profile_store.save_one(name="wh", backend_type="duckdb", fields={"path": ":memory:"})
    if os.name == "nt":
        pytest.skip("POSIX permission bits not meaningful on Windows")
    path = profile_store.profiles_path()
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600


def test_save_sets_dir_mode_0700(fake_home: Path) -> None:
    profile_store.save_one(name="wh", backend_type="duckdb", fields={"path": ":memory:"})
    if os.name == "nt":
        pytest.skip("POSIX permission bits not meaningful on Windows")
    parent = profile_store.profiles_path().parent
    mode = stat.S_IMODE(parent.stat().st_mode)
    assert mode == 0o700


def test_schema_version_mismatch_raises(fake_home: Path) -> None:
    path = profile_store.profiles_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 99, "profiles": {}}))
    with pytest.raises(ProfileSchemaVersionError):
        profile_store.load_all()


def test_invalid_json_raises_field_invalid(fake_home: Path) -> None:
    path = profile_store.profiles_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json")
    with pytest.raises(ProfileFieldInvalidError):
        profile_store.load_all()


def test_non_object_top_level_raises(fake_home: Path) -> None:
    path = profile_store.profiles_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[1,2,3]")
    with pytest.raises(ProfileFieldInvalidError):
        profile_store.load_all()
