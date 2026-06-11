"""User-global datasource secret cache tests."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from marivo.analysis.errors import (
    DatasourceEnvVarMissingError,
    DatasourceSecretStorePermissionsError,
)
from marivo.datasource import secrets


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("MARIVO_PERSIST_SECRETS", raising=False)
    return home


def _store_path(home: Path) -> Path:
    return home / ".marivo" / "secrets.toml"


def _write_store(home: Path, text: str, mode: int = 0o600) -> Path:
    path = _store_path(home)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(mode)
    return path


def test_env_provider_wins_over_cached_value(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_store(fake_home, '"TRINO_PASSWORD" = "cached"\n')
    monkeypatch.setenv("TRINO_PASSWORD", "fresh")

    resolved = secrets.resolve("TRINO_PASSWORD")

    assert resolved.value == "fresh"
    assert isinstance(resolved.provider, secrets.EnvProvider)


def test_cache_supplies_value_when_env_is_unset(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_store(fake_home, '"TRINO_PASSWORD" = "cached"\n')
    monkeypatch.delenv("TRINO_PASSWORD", raising=False)

    resolved = secrets.resolve("TRINO_PASSWORD")

    assert resolved.value == "cached"
    assert isinstance(resolved.provider, secrets.LocalPlaintextCache)


def test_missing_env_and_cache_raises_datasource_env_var_missing(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TRINO_PASSWORD", raising=False)

    with pytest.raises(DatasourceEnvVarMissingError) as exc_info:
        secrets.resolve("TRINO_PASSWORD", datasource="wh", field="password")

    assert exc_info.value.details == {
        "datasource": "wh",
        "field": "password",
        "env_var": "TRINO_PASSWORD",
    }


def test_persist_writes_owner_only_secret_file(fake_home: Path) -> None:
    cache = secrets.LocalPlaintextCache.default()

    cache.persist("TRINO_PASSWORD", "stored-secret")

    path = _store_path(fake_home)
    assert path.read_text() == '"TRINO_PASSWORD" = "stored-secret"\n'
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_loose_permissions_refuse_read(fake_home: Path) -> None:
    path = _write_store(fake_home, '"TRINO_PASSWORD" = "cached"\n', mode=0o644)

    with pytest.raises(DatasourceSecretStorePermissionsError) as exc_info:
        secrets.LocalPlaintextCache.default().get("TRINO_PASSWORD")

    assert exc_info.value.details["path"] == str(path)
    assert exc_info.value.details["mode"] == 0o644


def test_path_guard_rejects_store_inside_git_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_home = tmp_path / "repo"
    repo_home.mkdir()
    (repo_home / ".git").mkdir()
    monkeypatch.setattr(Path, "home", lambda: repo_home)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("MARIVO_PERSIST_SECRETS", raising=False)

    with pytest.raises(DatasourceSecretStorePermissionsError) as exc_info:
        secrets.LocalPlaintextCache.default().persist("TRINO_PASSWORD", "secret")

    assert "inside a git repository" in exc_info.value.message
    assert exc_info.value.details["path"] == str(repo_home / ".marivo" / "secrets.toml")


def test_persistence_disabled_by_env_skips_writes_but_keeps_reads(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write_store(fake_home, '"TRINO_PASSWORD" = "cached"\n')
    monkeypatch.setenv("MARIVO_PERSIST_SECRETS", "0")

    cache = secrets.LocalPlaintextCache.default()
    cache.persist("TRINO_PASSWORD", "new-secret")

    assert path.read_text() == '"TRINO_PASSWORD" = "cached"\n'
    assert cache.get("TRINO_PASSWORD") == "cached"


def test_persistence_disabled_by_ci_skips_writes_but_keeps_reads(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write_store(fake_home, '"TRINO_PASSWORD" = "cached"\n')
    monkeypatch.setenv("CI", "1")

    cache = secrets.LocalPlaintextCache.default()
    cache.persist("TRINO_PASSWORD", "new-secret")

    assert path.read_text() == '"TRINO_PASSWORD" = "cached"\n'
    assert cache.get("TRINO_PASSWORD") == "cached"
