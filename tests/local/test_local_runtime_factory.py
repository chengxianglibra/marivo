from __future__ import annotations

from pathlib import Path

import pytest

from app.profiles.local import LocalConfig, create_local_runtime


def test_creates_runtime_with_all_ports(tmp_path: Path):
    _init_marivo_dir(tmp_path)
    config = LocalConfig(workspace_root=tmp_path)
    runtime = create_local_runtime(config)
    assert runtime is not None
    assert runtime._ports is not None
    assert runtime._ports.model_store is not None
    assert runtime._ports.session_store is not None
    assert runtime._ports.evidence_store is not None
    assert runtime._ports.data_source is not None


def test_runtime_creates_session(tmp_path: Path):
    _init_marivo_dir(tmp_path)
    config = LocalConfig(workspace_root=tmp_path)
    runtime = create_local_runtime(config)
    session_id = runtime.create_session(goal="test")
    assert session_id is not None


def test_explicit_local_at_local_entry_succeeds(tmp_path: Path):
    _init_marivo_dir(tmp_path)
    config = LocalConfig(workspace_root=tmp_path)
    runtime = create_local_runtime(config, explicit="local")
    assert runtime is not None


def test_marivo_profile_server_at_local_entry_raises(tmp_path, monkeypatch) -> None:
    from app.profiles.resolver import ProfileResolutionError

    _init_marivo_dir(tmp_path)
    monkeypatch.setenv("MARIVO_PROFILE", "server")
    config = LocalConfig(workspace_root=tmp_path)
    with pytest.raises(ProfileResolutionError):
        create_local_runtime(config)


def test_marivo_profile_local_at_local_entry_succeeds(tmp_path, monkeypatch) -> None:
    _init_marivo_dir(tmp_path)
    monkeypatch.setenv("MARIVO_PROFILE", "local")
    config = LocalConfig(workspace_root=tmp_path)
    runtime = create_local_runtime(config)
    assert runtime is not None


def test_workspace_toml_profile_server_raises(tmp_path, monkeypatch) -> None:
    from app.profiles.resolver import ProfileResolutionError

    monkeypatch.delenv("MARIVO_PROFILE", raising=False)
    marivo_dir = tmp_path / ".marivo"
    marivo_dir.mkdir()
    (marivo_dir / "models").mkdir(exist_ok=True)
    (marivo_dir / "evidence").mkdir(exist_ok=True)
    (marivo_dir / "VERSION").write_text("1")
    (marivo_dir / "marivo.toml").write_text('profile = "server"\n')
    config = LocalConfig(workspace_root=tmp_path)
    with pytest.raises(ProfileResolutionError):
        create_local_runtime(config)


def _init_marivo_dir(root: Path) -> None:
    marivo = root / ".marivo"
    marivo.mkdir(exist_ok=True)
    (marivo / "models").mkdir(exist_ok=True)
    (marivo / "evidence").mkdir(exist_ok=True)
    (marivo / "VERSION").write_text("1")
    (marivo / "marivo.toml").write_text(
        'profile = "local"\n\n[datasource]\ntype = "duckdb"\n\n[telemetry]\nsink = "none"\n'
    )
