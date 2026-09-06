"""Connection ownership across real datasource, semantic and analysis operations."""

from __future__ import annotations

import pickle
import threading
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path

import ibis
import pytest
from ibis.backends import BaseBackend

import marivo.analysis as mv
import marivo.datasource as md
import marivo.semantic as ms
from marivo.analysis.errors import BackendError
from marivo.datasource import backends, credentials, runtime, secrets
from marivo.datasource._lifetime import BackendLease
from marivo.datasource.engines import ENGINE_PROFILES
from marivo.datasource.errors import DatasourceConnectionError, DatasourceConnectionTimeoutError
from marivo.datasource.ir import DatasourceIR


@pytest.fixture
def opened_backends(monkeypatch: pytest.MonkeyPatch) -> list[backends.BuiltDatasourceBackend]:
    opened: list[backends.BuiltDatasourceBackend] = []
    original = backends.build_backend

    def build(
        datasource: DatasourceIR, *, read_only: bool = False
    ) -> backends.BuiltDatasourceBackend:
        built = original(datasource, read_only=read_only)
        opened.append(built)
        return built

    monkeypatch.setattr(backends, "build_backend", build)
    return opened


@pytest.mark.parametrize("operation", ["preview", "preview_many", "source_health", "parity"])
def test_semantic_operation_releases_one_shared_backend(
    authoring_evidence_project: Path,
    opened_backends: list[backends.BuiltDatasourceBackend],
    operation: str,
) -> None:
    model = authoring_evidence_project / "models/semantic/sales/models.py"
    with model.open("a") as stream:
        stream.write(
            '\n@ms.metric(entities=[orders], additivity="additive", '
            'provenance=ms.from_sql(sql="SELECT SUM(amount) FROM orders", dialect="duckdb"))\n'
            "def parity_revenue(table):\n    return table.amount.sum()\n"
        )
    catalog = ms.load(workspace_dir=authoring_evidence_project)
    entity = ms.ref.entity("sales.orders")
    scope = md.unpruned(max_rows=10, timeout_seconds=5)
    if operation == "preview":
        assert catalog.preview(entity, scope=scope).rows
    elif operation == "preview_many":
        catalog.preview_many([entity, ms.ref.dimension("sales.orders.region")], scope=scope)
    elif operation == "source_health":
        assert catalog.source_health([entity]).status == "current"
    else:
        assert catalog._project.parity_check("sales.parity_revenue").ok
    assert len(opened_backends) == 1
    assert opened_backends[0].lease.closed
    assert not hasattr(catalog, "close")
    result = md.raw_sql(ms.ref.datasource("warehouse"), "SELECT 1 AS value", reason="lifetime test")
    assert result.row_count == 1
    inspection = md.inspect(ms.ref.datasource("warehouse"), md.table("orders"))
    inspection.sample(scope=scope, columns=("amount",))
    assert all(built.lease.closed for built in opened_backends)


def test_preview_failure_releases_backend(
    authoring_evidence_project: Path,
    opened_backends: list[backends.BuiltDatasourceBackend],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ibis.backends.duckdb import Backend

    catalog = ms.load(workspace_dir=authoring_evidence_project)

    def fail(self: Backend, expr: object, **kwargs: object) -> object:
        raise RuntimeError("synthetic execution failure")

    with monkeypatch.context() as patched:
        patched.setattr(Backend, "execute", fail)
        with pytest.raises(Exception, match="synthetic execution failure"):
            catalog.preview(
                ms.ref.entity("sales.orders"), scope=md.unpruned(max_rows=10, timeout_seconds=5)
            )
    assert len(opened_backends) == 1
    assert opened_backends[0].lease.closed
    assert (
        md.raw_sql(ms.ref.datasource("warehouse"), "SELECT 1", reason="after failure").row_count
        == 1
    )


def test_active_session_conflict_does_not_close_its_connection(
    authoring_evidence_project: Path,
) -> None:
    session = mv.session.get_or_create("connection-conflict")
    try:
        frame = session.observe(
            ms.ref.metric("sales.revenue"),
            time_scope=mv.time_scope(start="2026-07-01", end="2026-08-01"),
        )
        assert not frame.to_pandas().empty
        with pytest.raises(DatasourceConnectionError, match="incompatible open mode") as failed:
            md.raw_sql(ms.ref.datasource("warehouse"), "SELECT 1", reason="active session")
        assert "Close the Session" in str(failed.value)
        backend = session._connection_runtime.session_backend("warehouse")
        assert backend.raw_sql("SELECT 1").fetchall() == [(1,)]
    finally:
        session.close()
    assert (
        md.raw_sql(ms.ref.datasource("warehouse"), "SELECT 1", reason="closed session").row_count
        == 1
    )


@pytest.fixture
def credential_cache(
    authoring_evidence_project: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> secrets.LocalPlaintextCache:
    path = tmp_path_factory.mktemp("lifecycle-user") / ".marivo/secrets.toml"
    cache = secrets.LocalPlaintextCache(path)
    monkeypatch.setattr(secrets.LocalPlaintextCache, "default", classmethod(lambda cls: cache))
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("MARIVO_PERSIST_CREDENTIALS", raising=False)
    monkeypatch.setenv("LIFECYCLE_TOKEN", "synthetic-lifecycle-token")
    declaration = authoring_evidence_project / "models/datasources/warehouse.py"
    declaration.write_text(
        declaration.read_text().replace(
            ")\n", ", http_scope='http://127.0.0.1/', http_bearer_token_env='LIFECYCLE_TOKEN')\n"
        )
    )
    return cache


@pytest.mark.parametrize("cache_failure", [False, True])
def test_successful_session_retains_env_provenance_and_resumes(
    credential_cache: secrets.LocalPlaintextCache,
    monkeypatch: pytest.MonkeyPatch,
    cache_failure: bool,
) -> None:
    if cache_failure:

        def fail(self: secrets.LocalPlaintextCache, name: str, value: str) -> None:
            raise PermissionError("synthetic cache failure")

        monkeypatch.setattr(secrets.LocalPlaintextCache, "persist", fail)
    session = mv.session.get_or_create("credential-lifecycle")
    try:
        frame = session.observe(
            ms.ref.metric("sales.revenue"),
            time_scope=mv.time_scope(start="2026-07-01", end="2026-08-01"),
        )
        assert not frame.to_pandas().empty
        identity = session.id
    finally:
        session.close()
    assert credential_cache.path.exists() is not cache_failure
    if not cache_failure:
        assert credential_cache.get("LIFECYCLE_TOKEN") == "synthetic-lifecycle-token"
        monkeypatch.delenv("LIFECYCLE_TOKEN")
    resumed = mv.session.resume(identity)
    try:
        assert (
            not resumed.observe(
                ms.ref.metric("sales.revenue"),
                time_scope=mv.time_scope(start="2026-07-01", end="2026-08-01"),
            )
            .to_pandas()
            .empty
        )
    finally:
        resumed.close()


def test_failed_session_does_not_cache_credentials(
    credential_cache: secrets.LocalPlaintextCache,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ibis.backends.duckdb import Backend

    def fail(self: Backend, expr: object, **kwargs: object) -> object:
        raise RuntimeError("synthetic query failure")

    monkeypatch.setattr(Backend, "execute", fail)
    session = mv.session.get_or_create("failed-credential-lifecycle")
    try:
        with pytest.raises(BackendError):
            session.observe(
                ms.ref.metric("sales.revenue"),
                time_scope=mv.time_scope(start="2026-07-01", end="2026-08-01"),
            )
    finally:
        session.close()
    assert not credential_cache.path.exists()


def test_declared_file_schemas_do_not_open_backends(
    authoring_evidence_project: Path,
    opened_backends: list[backends.BuiltDatasourceBackend],
) -> None:
    ref = ms.ref.datasource("warehouse")
    md.inspect(ref, md.csv("not-present.csv", schema={"id": "int64"}))
    md.inspect(ref, md.json("not-present.json", schema={"id": "int64"}))
    assert not opened_backends


@pytest.mark.parametrize("kind", tuple(ENGINE_PROFILES))
def test_engine_handshake_retains_owned_build_result(
    kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = ibis.duckdb.connect(":memory:")
    profile = replace(ENGINE_PROFILES[kind], connect=lambda name, kwargs: backend)
    monkeypatch.setattr(backends, "require_profile_for_backend_type", lambda name: profile)
    monkeypatch.setattr(runtime, "require_profile_for_backend_type", lambda name: profile)
    from marivo.datasource.ir import AiContextIR, DatasourceSourceLocation

    declaration = DatasourceIR(
        semantic_id="test",
        name="test",
        backend_type=kind,
        fields={},
        env_refs={},
        ai_context=AiContextIR(),
        python_symbol="test",
        location=DatasourceSourceLocation(file="<fixture>", line=1),
    )
    built = runtime.open_backend(declaration, project_root=tmp_path)
    try:
        assert built.backend is backend
        assert "Backend" not in repr(built).removeprefix("BuiltDatasourceBackend")
        with pytest.raises(TypeError, match="cannot be serialized"):
            pickle.dumps(built)
    finally:
        built.disconnect()
        built.disconnect()
    assert built.lease.closed


def test_inner_operation_does_not_release_parent_backend(tmp_path: Path) -> None:
    service = runtime.DatasourceConnectionService(
        tmp_path, backends={"local": lambda: ibis.duckdb.connect(":memory:")}
    )
    parent = service.session_backend("local")
    try:
        with service.operation() as child:
            assert child.session_backend("local") is not parent
        assert parent.raw_sql("SELECT 1").fetchall() == [(1,)]
    finally:
        service.close_all()


def test_lease_close_failure_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = ibis.duckdb.connect(":memory:")
    original = backend.disconnect
    calls: list[bool] = []

    def fail() -> None:
        calls.append(True)
        original()
        raise RuntimeError("synthetic close failure")

    monkeypatch.setattr(backend, "disconnect", fail)
    lease = BackendLease(backend)
    with pytest.raises(RuntimeError, match="synthetic close failure"):
        lease.close(suppress_errors=False)
    lease.close()
    assert calls == [True]


def test_scoped_handshake_timeout_does_not_cache_late_backend(
    authoring_evidence_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()
    closed = threading.Event()
    profile = ENGINE_PROFILES["duckdb"]

    def connect(name: str, kwargs: object) -> BaseBackend:
        assert release.wait(5)
        backend = ibis.duckdb.connect(":memory:")
        original = backend.disconnect

        def close() -> None:
            original()
            closed.set()

        backend.disconnect = close
        return backend

    monkeypatch.setattr(
        backends, "require_profile_for_backend_type", lambda name: replace(profile, connect=connect)
    )
    service = runtime.DatasourceConnectionService(authoring_evidence_project)
    try:
        with (
            credentials.operation_context(timeout_seconds=1),
            pytest.raises(DatasourceConnectionTimeoutError),
        ):
            service.session_backend("warehouse")
        assert not service._session_backends
    finally:
        release.set()
        assert closed.wait(5)
        service.close_all()


@pytest.mark.parametrize("completed", [False, True], ids=["building", "awaiting-handoff"])
def test_interrupted_handshake_releases_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    completed: bool,
) -> None:
    entered, finish_build = threading.Event(), threading.Event()
    finish_close, closed = threading.Event(), threading.Event()
    cleanup_released: list[bool] = []
    cleanup: list[Callable[[], None]] = []
    interruption = KeyboardInterrupt("interrupted handshake")
    profile = ENGINE_PROFILES["duckdb"]

    def connect(name: str, kwargs: Mapping[str, object]) -> BaseBackend:
        entered.set()
        assert finish_build.wait(5)
        backend = profile.connect(name, kwargs)
        disconnect = backend.disconnect
        cleanup.append(disconnect)

        def close() -> None:
            cleanup_released.append(finish_close.wait(5))
            disconnect()
            closed.set()
            raise RuntimeError("synthetic close failure")

        backend.disconnect = close
        return backend

    class InterruptedWait(threading.Thread):
        def join(self, timeout: float | None = None) -> None:
            assert entered.wait(5)
            if completed:
                super().join(5)
                assert not self.is_alive()
            raise interruption

    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    md.register(md.duckdb(name="local", path=str(tmp_path / "local.duckdb")))
    service = runtime.DatasourceConnectionService(tmp_path)
    if completed:
        finish_build.set()
    try:
        with monkeypatch.context() as patched:
            patched.setattr(runtime, "Thread", InterruptedWait)
            patched.setattr(
                backends,
                "require_profile_for_backend_type",
                lambda name: replace(profile, connect=connect),
            )
            with pytest.raises(KeyboardInterrupt) as failed:
                service.session_backend("local")
            assert failed.value is interruption
            assert not service._session_backends
            assert not service._leases
            finish_build.set()
            finish_close.set()
            assert closed.wait(5)
        assert cleanup_released == [True]
        with service.use_backend("local", read_only=True) as backend:
            assert backend.raw_sql("SELECT 1").fetchall() == [(1,)]
    finally:
        finish_build.set()
        finish_close.set()
        service.close_all()
        for disconnect in cleanup:
            disconnect()


def test_timed_out_sqlite_roundtrip_closes_on_its_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release, closed = threading.Event(), threading.Event()
    created_threads: list[int] = []
    closed_threads: list[int] = []
    original_connect = ENGINE_PROFILES["sqlite"].connect

    def connect(name: str, kwargs: object) -> BaseBackend:
        backend = original_connect(name, kwargs)
        created_threads.append(threading.get_ident())
        raw_sql, disconnect = backend.raw_sql, backend.disconnect

        def query(sql: str, **options: object) -> object:
            if sql == "SELECT 1":
                assert release.wait(5)
            return raw_sql(sql, **options)

        def close() -> None:
            closed_threads.append(threading.get_ident())
            disconnect()
            closed.set()

        backend.raw_sql = query
        backend.disconnect = close
        return backend

    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    md.register(md.sqlite(name="local", path=":memory:"))
    profile = replace(ENGINE_PROFILES["sqlite"], connect=connect)
    monkeypatch.setattr(backends, "require_profile_for_backend_type", lambda name: profile)
    try:
        result = md.test("local", timeout_seconds=1)
        assert result.failure is not None
        assert result.failure.code == "connection_roundtrip_timeout"
    finally:
        release.set()
        assert closed.wait(5)
    assert closed_threads == created_threads
    assert closed_threads != [threading.get_ident()]


def test_execution_budget_does_not_shorten_handshake_budget(
    credential_cache: secrets.LocalPlaintextCache,
) -> None:
    from time import monotonic

    budgets: list[float] = []

    class Resolver:
        def resolve(self, request: md.CredentialRequest) -> md.SecretValue:
            assert request.deadline_monotonic is not None
            budgets.append(request.deadline_monotonic - monotonic())
            return md.SecretValue("synthetic-host-token")

    with md.credential_scope(resolver=Resolver()):
        catalog = ms.load()
        scope = md.unpruned(max_rows=10, timeout_seconds=1)
        catalog.preview(ms.ref.entity("sales.orders"), scope=scope)
        md.raw_sql(
            ms.ref.datasource("warehouse"), "SELECT 1", reason="separate budgets", timeout_seconds=1
        )
        inspection = md.inspect(ms.ref.datasource("warehouse"), md.table("orders"))
        inspection.sample(scope=scope, columns=("amount",))
    assert len(budgets) == 4
    assert all(25 < budget <= 30 for budget in budgets)
    assert not credential_cache.path.exists()


def test_roundtrip_uses_one_worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[int] = []
    executed: list[int] = []
    profile = ENGINE_PROFILES["duckdb"]

    def connect(name: str, kwargs: object) -> BaseBackend:
        backend = profile.connect(name, kwargs)
        created.append(threading.get_ident())
        original = backend.raw_sql

        def query(sql: str, **options: object) -> object:
            executed.append(threading.get_ident())
            return original(sql, **options)

        backend.raw_sql = query
        return backend

    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    md.register(md.duckdb(name="local", path=":memory:"))
    monkeypatch.setattr(
        backends, "require_profile_for_backend_type", lambda name: replace(profile, connect=connect)
    )
    assert md.test("local").ok
    assert executed == created
    assert created != [threading.get_ident()]


def test_public_sqlite_close_preserves_driver_thread_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sqlite3

    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    md.register(md.sqlite(name="local", path=":memory:"))
    connection = md.connect("local")
    errors: list[Exception] = []

    def close() -> None:
        try:
            connection.disconnect()
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=close)
    try:
        worker.start()
        worker.join(5)
        assert not worker.is_alive()
        assert len(errors) == 1
        assert isinstance(errors[0], sqlite3.ProgrammingError)
    finally:
        connection.backend.disconnect()
