import textwrap
from pathlib import Path

import pytest
from ibis.backends.duckdb import Backend

import marivo.datasource as md
from marivo.datasource import runtime, store
from marivo.datasource.adapters import SourceSubmission
from marivo.datasource.authoring import _ir_from_spec
from marivo.datasource.backends import BuiltDatasourceBackend
from marivo.datasource.ir import DatasourceIR, DatasourceSourceLocation


class FakeBackend:
    def __init__(self) -> None:
        self.disconnect_calls = 0

    def disconnect(self) -> None:
        self.disconnect_calls += 1


def _warehouse_datasource() -> DatasourceIR:
    return _ir_from_spec(
        md.duckdb(name="warehouse"),
        location=DatasourceSourceLocation(file="<test>", line=1),
    )


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


@pytest.mark.parametrize("route", ["backend_for", "override", "factory", "build"])
def test_each_cached_backend_has_one_matching_lease(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, route: str
) -> None:
    backend = FakeBackend()
    built = BuiltDatasourceBackend(backend=backend, env_sourced_secrets=())
    monkeypatch.setattr(runtime, "open_backend", lambda *_args, **_kwargs: built)
    monkeypatch.setattr(runtime, "_build_backend_from_store", lambda *_args, **_kwargs: built)
    service = runtime.DatasourceConnectionService(
        project_root=tmp_path,
        backends={"warehouse": lambda: backend} if route == "override" else None,
        backend_factory=(lambda _name: backend) if route == "factory" else None,
    )
    datasource = _warehouse_datasource()

    first = (
        service.backend_for(datasource)
        if route == "backend_for"
        else service.session_backend("warehouse")
    )
    second = (
        service.backend_for(datasource)
        if route == "backend_for"
        else service.session_backend("warehouse")
    )

    assert first is second is backend
    assert tuple(service._session_backends) == tuple(service._leases) == ("warehouse",)
    lease = service._leases["warehouse"]
    assert lease.backend is backend
    assert lease.closed is False
    service.close_all()
    service.close_all()
    assert backend.disconnect_calls == 1
    assert lease.closed is True
    assert service._session_backends == service._leases == {}


@pytest.mark.parametrize("route", ["backend_for", "override", "factory", "build"])
def test_failed_backend_build_leaves_no_cache_or_lease(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, route: str
) -> None:
    def fail(*_args: object, **_kwargs: object) -> BuiltDatasourceBackend:
        raise RuntimeError("backend build failed")

    monkeypatch.setattr(runtime, "open_backend", fail)
    monkeypatch.setattr(runtime, "_build_backend_from_store", fail)
    service = runtime.DatasourceConnectionService(
        project_root=tmp_path,
        backends={"warehouse": fail} if route == "override" else None,
        backend_factory=fail if route == "factory" else None,
    )
    with pytest.raises(RuntimeError, match="backend build failed"):
        if route == "backend_for":
            service.backend_for(_warehouse_datasource())
        else:
            service.session_backend("warehouse")

    assert service._session_backends == service._leases == {}
    service.close_all()


@pytest.mark.parametrize("fails", [False, True])
def test_cached_backend_release_records_only_successful_disconnect(
    tmp_path: Path, fails: bool
) -> None:
    class ObservedBackend(Backend):
        def __init__(self) -> None:
            super().__init__()
            self.disconnect_calls = 0

        def disconnect(self) -> None:
            self.disconnect_calls += 1
            if fails:
                raise RuntimeError("connection release failed")

    backend = ObservedBackend()
    service = runtime.DatasourceConnectionService(
        project_root=tmp_path, backends={"warehouse": lambda: backend}
    )
    source = service.source_session("warehouse", _warehouse_datasource())
    submission = SourceSubmission(
        purpose="preview",
        source_identity="warehouse.orders",
        expression_identity=1,
        sql="SELECT 1",
        state="succeeded",
        cursor_state="closed",
    )
    source.submissions.append(submission)
    lease = service._leases["warehouse"]

    service.close_all()
    service.close_all()

    assert backend.disconnect_calls == 1
    assert lease.closed is True
    assert submission.connection_disconnected is not fails
    assert service._session_backends == service._leases == service._source_sessions == {}


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


@pytest.mark.parametrize("failure_scope", [None, "inner", "outer"])
def test_nested_terminal_scopes_restore_each_matching_lease_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure_scope: str | None
) -> None:
    created: list[FakeBackend] = []

    def build(*_args: object, **_kwargs: object) -> BuiltDatasourceBackend:
        backend = FakeBackend()
        created.append(backend)
        return BuiltDatasourceBackend(backend=backend, env_sourced_secrets=())

    monkeypatch.setattr(runtime, "_build_backend_from_store", build)
    service = runtime.DatasourceConnectionService(project_root=tmp_path)
    original = service.session_backend("warehouse")
    original_leases = service._leases
    original_backends = service._session_backends
    try:
        with service.terminal_scope(17):
            outer = service.session_backend("warehouse")
            outer_leases = service._leases
            outer_backends = service._session_backends
            try:
                with service.terminal_scope(9):
                    inner = service.session_backend("warehouse")
                    assert inner is not outer
                    assert service._leases["warehouse"].backend is inner
                    if failure_scope == "inner":
                        raise ValueError("inner failed")
            except ValueError as exc:
                assert failure_scope == "inner" and str(exc) == "inner failed"
            assert service._leases is outer_leases
            assert service._session_backends is outer_backends
            assert service.session_backend("warehouse") is outer
            assert service._leases["warehouse"].backend is outer
            assert created[2].disconnect_calls == 1
            assert created[1].disconnect_calls == 0
            if failure_scope == "outer":
                raise ValueError("outer failed")
    except ValueError as exc:
        assert failure_scope == "outer" and str(exc) == "outer failed"

    assert service._leases is original_leases
    assert service._session_backends is original_backends
    assert service.session_backend("warehouse") is original
    assert service._leases["warehouse"].backend is original
    assert created[1].disconnect_calls == 1
    assert created[0].disconnect_calls == 0
    service.close_all()
    assert created[0].disconnect_calls == 1
