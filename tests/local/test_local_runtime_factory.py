from __future__ import annotations

from pathlib import Path

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
    state = runtime.create_session(goal="test")
    assert state is not None
    assert state.session_id.startswith("sess-")


def test_explicit_local_overrides_server_deployment(tmp_path: Path):
    _init_marivo_dir(tmp_path)
    config = LocalConfig(workspace_root=tmp_path)
    runtime = create_local_runtime(config, explicit_local=True)
    assert runtime is not None


def _init_marivo_dir(root: Path) -> None:
    marivo = root / ".marivo"
    marivo.mkdir(exist_ok=True)
    (marivo / "models").mkdir(exist_ok=True)
    (marivo / "evidence").mkdir(exist_ok=True)
    (marivo / "VERSION").write_text("1")
    (marivo / "marivo.toml").write_text(
        '[profile]\nmode = "local"\n\n[datasource]\ntype = "duckdb"\n\n[telemetry]\nsink = "none"\n'
    )
