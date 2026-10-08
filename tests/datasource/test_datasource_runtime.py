import textwrap
from pathlib import Path

import pytest

import marivo.datasource as md
from marivo.datasource import runtime, store
from marivo.datasource.backends import BuiltDatasourceBackend


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
        runtime,
        "_build_backend_from_store",
        lambda name, project_root, read_only=False, terminal_timeout_seconds=None: (
            BuiltDatasourceBackend(backend=backend, env_sourced_secrets=())
        ),
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
        runtime,
        "_build_backend_from_store",
        lambda name, project_root, read_only=False, terminal_timeout_seconds=None: (
            BuiltDatasourceBackend(backend=backend, env_sourced_secrets=())
        ),
    )
    service = runtime.DatasourceConnectionService(project_root=tmp_path)

    with pytest.raises(RuntimeError, match="boom"), service.use_backend("warehouse"):
        raise RuntimeError("boom")

    assert backend.disconnect_calls == 1


@pytest.mark.parametrize("fails", [False, True])
def test_scoped_disconnect_observation_reports_actual_outcome(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fails: bool
) -> None:
    class ObservedBackend(FakeBackend):
        def disconnect(self) -> None:
            super().disconnect()
            if fails:
                raise RuntimeError("connection release failed")

    backend = ObservedBackend()
    monkeypatch.setattr(
        runtime,
        "_build_backend_from_store",
        lambda *_args, **_kwargs: BuiltDatasourceBackend(backend=backend, env_sourced_secrets=()),
    )
    seen: list[bool] = []
    service = runtime.DatasourceConnectionService(project_root=tmp_path)
    with service.use_backend("warehouse", on_disconnect=seen.append):
        assert seen == []
    assert backend.disconnect_calls == 1
    assert seen == [not fails]


def test_session_backend_is_reused_until_close(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    created: list[FakeBackend] = []

    def build(name: str, project_root: Path | None, **kwargs: object) -> BuiltDatasourceBackend:
        backend = FakeBackend()
        created.append(backend)
        return BuiltDatasourceBackend(backend=backend, env_sourced_secrets=())

    monkeypatch.setattr(runtime, "_build_backend_from_store", build)
    service = runtime.DatasourceConnectionService(project_root=tmp_path)

    first = service.session_backend("warehouse")
    second = service.session_backend("warehouse")

    assert first is second
    assert len(created) == 1
    service.close_all()
    assert created[0].disconnect_calls == 1


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

    layered = runtime.DatasourceConnectionService(
        project_root=project_root,
    )
    backend = layered.session_backend("warehouse")

    assert backend is layered.session_backend("warehouse")
    layered.close_all()


def test_layered_datasource_loading_rejects_duplicate_names_with_paths(tmp_path: Path) -> None:
    project_root, external_models = _write_layered_project(tmp_path, duplicate_local=True)

    with pytest.raises(Exception) as exc_info:
        store.load_all(project_root)

    message = str(exc_info.value)
    assert "Duplicate datasource name: 'warehouse'" in message
    assert str(project_root / "models" / "datasources" / "warehouse.py") in message
    assert str(external_models / "datasources" / "warehouse.py") in message


@pytest.mark.parametrize("fails", [False, True])
def test_terminal_scope_replaces_unbounded_cache_and_restores_it_after_exit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fails: bool,
) -> None:
    created: list[FakeBackend] = []
    options: list[tuple[bool, int | None]] = []

    def build(
        name: str,
        project_root: Path | None,
        *,
        read_only: bool = False,
        terminal_timeout_seconds: int | None = None,
    ) -> BuiltDatasourceBackend:
        backend = FakeBackend()
        created.append(backend)
        options.append((read_only, terminal_timeout_seconds))
        return BuiltDatasourceBackend(backend=backend, env_sourced_secrets=())

    monkeypatch.setattr(runtime, "_build_backend_from_store", build)
    service = runtime.DatasourceConnectionService(project_root=tmp_path)
    original = service.session_backend("warehouse")
    try:
        with service.terminal_scope(17):
            bounded = service.session_backend("warehouse")
            assert bounded is not original
            assert bounded is service.session_backend("warehouse")
            assert options[-1] == (True, 17)
            if fails:
                raise ValueError("certification failed")
    except ValueError as exc:
        assert fails and str(exc) == "certification failed"
    assert created[1].disconnect_calls == 1
    assert created[0].disconnect_calls == 0
    assert service.session_backend("warehouse") is original
    service.close_all()
    assert created[0].disconnect_calls == 1
