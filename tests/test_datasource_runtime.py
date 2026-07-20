import textwrap
from pathlib import Path

import pytest

import marivo.datasource as md
from marivo.datasource import runtime, store


class FakeBackend:
    def __init__(self) -> None:
        self.disconnect_calls = 0

    def disconnect(self) -> None:
        self.disconnect_calls += 1


def test_use_backend_disconnects_after_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend = FakeBackend()
    monkeypatch.setattr(
        runtime, "_build_backend_from_store", lambda name, project_root, read_only=False: backend
    )

    service = runtime.DatasourceConnectionService(project_root=tmp_path)
    with service.use_backend("warehouse") as received:
        assert received is backend
        assert backend.disconnect_calls == 0

    assert backend.disconnect_calls == 1


def test_use_backend_disconnects_after_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend = FakeBackend()
    monkeypatch.setattr(
        runtime, "_build_backend_from_store", lambda name, project_root, read_only=False: backend
    )
    service = runtime.DatasourceConnectionService(project_root=tmp_path)

    with pytest.raises(RuntimeError, match="boom"), service.use_backend("warehouse"):
        raise RuntimeError("boom")

    assert backend.disconnect_calls == 1


def test_session_backend_is_reused_until_close(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    created: list[FakeBackend] = []

    def build(name: str, project_root: Path | None) -> FakeBackend:
        backend = FakeBackend()
        created.append(backend)
        return backend

    monkeypatch.setattr(runtime, "_build_backend_from_store", build)
    service = runtime.DatasourceConnectionService(project_root=tmp_path)

    first = service.session_backend("warehouse")
    second = service.session_backend("warehouse")

    assert first is second
    assert len(created) == 1
    service.close_all()
    assert created[0].disconnect_calls == 1


def test_session_backend_rejects_legacy_kind_prefixed_string(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[str] = []

    def build(name: str, project_root: Path | None) -> FakeBackend:
        seen.append(name)
        return FakeBackend()

    monkeypatch.setattr(runtime, "_build_backend_from_store", build)
    service = runtime.DatasourceConnectionService(project_root=tmp_path)

    with pytest.raises(Exception, match="valid datasource name"):
        service.session_backend("datasource.warehouse")
    assert seen == []


def test_datasource_module_exposes_runtime_service() -> None:
    # DatasourceConnectionService is internal; importable from the submodule.
    assert runtime.DatasourceConnectionService is not None


def test_py_file_datasource_visible_via_list(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Datasources authored as .py files in models/datasources/ are
    discoverable via md.list() without calling md.register()."""
    (tmp_path / "marivo.toml").touch()
    ds_dir = tmp_path / "models" / "datasources"
    ds_dir.mkdir(parents=True)
    (ds_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )

    monkeypatch.setattr("marivo.project.Path.cwd", lambda: tmp_path)
    monkeypatch.delenv("MARIVO_PROJECT_ROOT", raising=False)

    summaries = md.list()
    names = [s.name for s in summaries]
    assert "warehouse" in names


def _write_layered_project(tmp_path: Path, *, duplicate_local: bool = False) -> tuple[Path, Path]:
    project_root = tmp_path / "project"
    external_models = tmp_path / "external" / "models"
    project_root.mkdir()
    (project_root / "marivo.toml").write_text(
        textwrap.dedent(
            """
            [project]
            name = "demo"

            [semantic]
            layer_paths = ["../external/models"]
            """
        ),
        encoding="utf-8",
    )
    external_ds = external_models / "datasources"
    external_ds.mkdir(parents=True)
    (external_models / "semantic").mkdir(parents=True)
    (external_ds / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n",
        encoding="utf-8",
    )
    if duplicate_local:
        local_ds = project_root / "models" / "datasources"
        local_ds.mkdir(parents=True)
        (local_ds / "warehouse.py").write_text(
            "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n",
            encoding="utf-8",
        )
    return project_root, external_models


def test_session_backend_can_include_configured_semantic_layer_datasources(
    tmp_path: Path,
) -> None:
    project_root, _ = _write_layered_project(tmp_path)

    local_only = runtime.DatasourceConnectionService(project_root=project_root)
    with pytest.raises(Exception, match="warehouse"):
        local_only.session_backend("warehouse")

    layered = runtime.DatasourceConnectionService(
        project_root=project_root,
        include_semantic_layers=True,
    )
    backend = layered.session_backend("warehouse")

    assert backend is layered.session_backend("warehouse")
    layered.close_all()


def test_layered_datasource_loading_rejects_duplicate_names_with_paths(tmp_path: Path) -> None:
    project_root, external_models = _write_layered_project(tmp_path, duplicate_local=True)

    with pytest.raises(Exception) as exc_info:
        store.load_all_layered(project_root)

    message = str(exc_info.value)
    assert "Duplicate datasource name: 'warehouse'" in message
    assert str(project_root / "models" / "datasources" / "warehouse.py") in message
    assert str(external_models / "datasources" / "warehouse.py") in message
