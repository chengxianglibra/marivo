"""Host credential injection through public APIs and existing runtime owners."""

from __future__ import annotations

import copy
import os
import pickle
import subprocess
import sys
import threading
import traceback
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType

import ibis
import pytest
from ibis.backends import BaseBackend
from ibis.backends.duckdb import Backend
from typing_extensions import override

import marivo
import marivo.analysis as mv
import marivo.datasource as md
import marivo.semantic as ms
from marivo._help.render import help as show_help
from marivo.datasource import credentials
from marivo.datasource.credentials import secrets as secrets
from marivo.datasource.engines import require_profile_for_backend_type
from marivo.datasource.engines.trino import PROFILE
from marivo.datasource.errors import (
    CredentialFailureReason,
    DatasourceConnectionError,
    DatasourceMetadataError,
    DatasourceRawSqlError,
)
from marivo.datasource.inspection import manage as manage
from marivo.datasource.runtime import backends as backends
from marivo.datasource.runtime.connections import (
    DatasourceConnectionService,
    load_datasource,
    open_backend,
)


class HostResolver:
    def __init__(self, value: str = "fixture-only-bearer-4291") -> None:
        self.value = value
        self.requests: list[md.CredentialRequest] = []

    def resolve(self, request: md.CredentialRequest) -> md.SecretValue:
        self.requests.append(request)
        return md.SecretValue(self.value)


def register_project(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(project))
    md.register(
        md.duckdb(
            name="warehouse",
            path=":memory:",
            secrets=[
                md.http_bearer_secret(scope="http://127.0.0.1/", token=md.env("FIXTURE_TOKEN"))
            ],
        )
    )


@pytest.fixture
def process_resolver_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    module = ModuleType("fixture_process_credentials")
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(credentials, "_DEFAULT_RESOLVER", credentials._ProcessResolver())
    monkeypatch.setenv("MARIVO_CREDENTIAL_RESOLVER", f"{module.__name__}:resolver")
    return module


def test_process_resolver_is_lazy_and_shared_by_sessions_and_threads(
    authoring_evidence_project: Path,
    process_resolver_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = authoring_evidence_project
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(project))
    bearer = md.http_bearer_secret(scope="http://127.0.0.1/", token=md.env("FIXTURE_TOKEN"))
    md.register(md.duckdb(name="probe", path=":memory:", secrets=[bearer]))
    md.register(
        md.duckdb(
            name="warehouse",
            path=str(project / "warehouse.duckdb"),
            wall_clock_timezone="UTC",
            secrets=[bearer],
        )
    )
    instances: list[HostResolver] = []

    def factory() -> HostResolver:
        resolver = HostResolver()
        instances.append(resolver)
        # Let competing first requests reach the loader during factory execution.
        threading.Event().wait(0.1)
        return resolver

    monkeypatch.setattr(process_resolver_module, "resolver", factory, raising=False)
    catalog = ms.load(workspace_dir=project)
    session = mv.session.get_or_create("process-resolver", timezone="UTC")
    datasources = md.load(workspace_dir=project)
    assert datasources.test("default").ok
    scoped = HostResolver("explicit-token")
    with md.credential_scope(resolver=scoped):
        assert datasources.test("probe").ok
    assert len(scoped.requests) == 1
    assert instances == []

    barrier = threading.Barrier(2)

    def connect() -> bool:
        barrier.wait(timeout=5)
        return datasources.test("probe").ok

    with ThreadPoolExecutor(max_workers=2) as workers:
        first, second = workers.submit(connect), workers.submit(connect)
        assert first.result(timeout=15) and second.result(timeout=15)
        (resolver,) = instances
        assert [request.datasource for request in resolver.requests] == ["probe", "probe"]

        monkeypatch.delenv("MARIVO_CREDENTIAL_RESOLVER")
        assert datasources.test("probe").ok
        assert [request.datasource for request in resolver.requests] == ["probe"] * 3
        with md.credential_scope(resolver=resolver):
            entity = session.catalog.get("entity:sales.orders")
        assert entity.kind == "entity"
        query = session.observe({"orders": session.members(entity).count()})
        frame = workers.submit(query.compute).result(timeout=15)
        assert frame.to_pandas()["orders"].tolist() == [4]

    preview = catalog.preview(
        catalog.get("entity:sales.orders"),
        scope=md.unpruned(max_rows=10, timeout_seconds=10),
    )
    assert {row["query_id"] for row in preview.rows} == {1, 2, 3, 4}
    assert len(instances) == 1
    assert {request.datasource for request in resolver.requests} == {"probe", "warehouse"}


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires fork")
def test_forked_process_initializes_its_own_resolver() -> None:
    script = """
import os
import signal
from pathlib import Path

import marivo.datasource as md
from marivo.datasource import credentials

calls = []

class Resolver:
    def __init__(self):
        self.owner = os.getpid()

    def resolve(self, request):
        return md.SecretValue(str(self.owner))

def factory():
    calls.append(os.getpid())
    return Resolver()

binding = credentials.current_resolver()
operation = credentials.CredentialOperation(Path.cwd())

def owner():
    return int(credentials.resolve_reference(
        binding, operation, reference="TEST", datasource="warehouse", fields=("user",)
    ).reveal())

parent = os.getpid()
assert owner() == parent
child = os.fork()
if child == 0:
    signal.alarm(5)
    pid = os.getpid()
    os._exit(0 if owner() == owner() == pid and calls == [parent, pid] else 1)
assert os.waitpid(child, 0)[1] == 0
assert owner() == parent and calls == [parent]
"""
    subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "MARIVO_CREDENTIAL_RESOLVER": "__main__:factory"},
        check=True,
        timeout=20,
    )


@pytest.mark.parametrize(
    "spec",
    [
        "",
        "fixture_process_credentials",
        "missing_process_credentials:resolver",
        "fixture_process_credentials:missing",
        "fixture_process_credentials:noncallable",
        "fixture_process_credentials:invalid",
        "fixture_process_credentials:failing",
    ],
)
def test_process_resolver_load_failure_is_sticky_and_bypasses_fallback(
    spec: str,
    tmp_path: Path,
    process_resolver_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    register_project(tmp_path, monkeypatch)
    monkeypatch.setenv("MARIVO_CREDENTIAL_RESOLVER", spec)
    monkeypatch.setenv("FIXTURE_TOKEN", "env-fallback-canary")
    monkeypatch.setattr(process_resolver_module, "noncallable", object(), raising=False)
    monkeypatch.setattr(process_resolver_module, "invalid", object, raising=False)
    monkeypatch.setattr(process_resolver_module, "resolver", HostResolver, raising=False)

    def failing() -> HostResolver:
        raise RuntimeError("factory-error-canary")

    def forbidden_cache() -> secrets.LocalPlaintextCache:
        pytest.fail("failed process resolver used the default cache")

    monkeypatch.setattr(process_resolver_module, "failing", failing, raising=False)
    monkeypatch.setattr(secrets.LocalPlaintextCache, "default", forbidden_cache)
    datasource = load_datasource("warehouse", tmp_path)
    with pytest.raises(md.DatasourceCredentialError) as captured:
        open_backend(datasource, project_root=tmp_path)
    failure = captured.value
    assert failure.reason == "unavailable"
    assert failure.reference == "FIXTURE_TOKEN"
    assert failure.datasource == "warehouse"
    assert failure.fields == ("http@http://127.0.0.1/.token",)
    assert failure.repair is not None and "MARIVO_CREDENTIAL_RESOLVER" in failure.repair.action
    assert "factory-error-canary" not in "".join(traceback.format_exception(failure))

    monkeypatch.setenv("MARIVO_CREDENTIAL_RESOLVER", f"{process_resolver_module.__name__}:resolver")
    result = md.load(workspace_dir=tmp_path).test("warehouse")
    assert result.failure is not None and result.failure.code == "credential_unavailable"
    with md.credential_scope(resolver=HostResolver()):
        assert md.load(workspace_dir=tmp_path).test("warehouse").ok


def test_doctor_cli_imports_process_resolver_only_for_connect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_project(tmp_path, monkeypatch)
    monkeypatch.delenv("FIXTURE_TOKEN", raising=False)
    monkeypatch.setenv("MARIVO_CREDENTIAL_RESOLVER", "fixture_cli_credentials:resolver")
    events = tmp_path / "resolver-events.txt"
    (tmp_path / "fixture_cli_credentials.py").write_text(
        "from pathlib import Path\n"
        "import marivo.datasource as md\n"
        f"events = Path({str(events)!r})\n"
        "events.write_text('imported\\n')\n"
        "class Resolver:\n"
        "    def resolve(self, request):\n"
        "        with events.open('a') as handle:\n"
        "            handle.write(request.reference + '\\n')\n"
        "        return md.SecretValue('cli-fixture-token')\n"
        "def resolver():\n"
        "    with events.open('a') as handle:\n"
        "        handle.write('created\\n')\n"
        "    return Resolver()\n"
    )
    command = [sys.executable, "-m", "marivo", "doctor", "--datasource", "warehouse"]
    static = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert static.returncode == 0, static.stdout + static.stderr
    assert not events.exists()
    live = subprocess.run(
        [*command, "--connect"], cwd=tmp_path, capture_output=True, text=True, timeout=20
    )
    assert live.returncode == 0, live.stdout + live.stderr
    assert events.read_text().splitlines() == ["imported", "created", "FIXTURE_TOKEN"]


def test_owned_backend_probes_and_no_default_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    register_project(tmp_path, monkeypatch)
    resolver = HostResolver()

    def forbidden() -> secrets.LocalPlaintextCache:
        pytest.fail("explicit resolver touched the default cache")

    monkeypatch.setattr(secrets.LocalPlaintextCache, "default", forbidden)
    monkeypatch.setenv("FIXTURE_TOKEN", "wrong-default-value")
    with md.credential_scope(resolver=resolver):
        assert md.load().test("warehouse").ok
        assert manage.test_no_persist("warehouse", project_root=tmp_path).ok
        connection = open_backend(load_datasource("warehouse", tmp_path), project_root=tmp_path)
    try:
        backend = connection.backend
        assert backend.raw_sql("SELECT 1").fetchall() == [(1,)]
        with md.credential_scope(resolver=HostResolver("other-value")):
            assert backend.raw_sql("SELECT 2").fetchall() == [(2,)]
    finally:
        connection.disconnect()
    assert len(resolver.requests) == 3
    for request in resolver.requests:
        assert request.project_root == tmp_path
        assert request.fields == ("http@http://127.0.0.1/.token",)


def test_datasource_catalog_and_inspection_use_operation_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_project(tmp_path, monkeypatch)
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text("order_id\n1\n", encoding="utf-8")
    original = HostResolver("original-token")
    current = HostResolver("current-token")

    with md.credential_scope(resolver=original):
        catalog = md.load(workspace_dir=tmp_path)
        inspection = md.inspect(
            ms.ref.datasource("warehouse"), md.csv(str(csv_path)), workspace_dir=tmp_path
        )
    assert len(original.requests) == 1

    with md.credential_scope(resolver=current):
        assert catalog.test("warehouse").ok
        snapshot = inspection.sample(
            scope=md.unpruned(max_rows=10, timeout_seconds=30),
            columns=("order_id",),
            refresh=True,
        )

    assert snapshot.coverage.retained_row_count == 1
    assert len(original.requests) == 1
    assert len(current.requests) == 2

    cached_resolver = HostResolver("unused-token")
    with md.credential_scope(resolver=cached_resolver):
        cached = inspection.sample(
            scope=md.unpruned(max_rows=10, timeout_seconds=30),
            columns=("order_id",),
        )
    assert cached.id == snapshot.id
    assert cached.cache_status == "cached"
    assert cached_resolver.requests == []


@pytest.mark.parametrize(
    "reason", ["missing", "denied", "unavailable", "expired", "timeout", "invalid-response"]
)
def test_typed_host_failures_never_fallback(
    reason: CredentialFailureReason,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    register_project(tmp_path, monkeypatch)
    monkeypatch.setenv("FIXTURE_TOKEN", "fallback-canary")

    class Resolver:
        def resolve(self, request: md.CredentialRequest) -> md.SecretValue:
            raise md.DatasourceCredentialError(
                reason=reason,
                reference=request.reference,
                datasource=request.datasource,
                fields=request.fields,
            ) from RuntimeError("provider-cause-canary")

    with md.credential_scope(resolver=Resolver()):
        with pytest.raises(md.DatasourceCredentialError) as captured:
            open_backend(load_datasource("warehouse", tmp_path), project_root=tmp_path)
        result = md.load().test("warehouse")
    assert captured.value.reason == reason
    assert result.failure is not None
    assert result.failure.code == "credential_" + reason.replace("-", "_")
    assert result.repair is not None
    assert result.repair.help_target.canonical_id == "credential_scope"


@pytest.mark.parametrize("response", [None, "", "unwrapped-value", {"token": "dictionary-canary"}])
def test_invalid_resolver_response_is_safe(
    response: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    register_project(tmp_path, monkeypatch)
    resolver = HostResolver()
    monkeypatch.setattr(resolver, "resolve", lambda request: response)
    with md.credential_scope(resolver=resolver):
        result = md.load().test("warehouse")
    assert result.failure is not None
    assert result.failure.code == "credential_invalid_response"


def test_raw_sdk_failure_is_sanitized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    register_project(tmp_path, monkeypatch)

    class Resolver:
        def resolve(self, request: md.CredentialRequest) -> md.SecretValue:
            raise RuntimeError("sdk-payload-canary")

    with (
        md.credential_scope(resolver=Resolver()),
        pytest.raises(md.DatasourceCredentialError) as captured,
    ):
        open_backend(load_datasource("warehouse", tmp_path), project_root=tmp_path)
    assert captured.value.reason == "unavailable"


@pytest.mark.parametrize("source", [md.csv("orders.csv"), md.json("orders.json")])
def test_file_inspection_preserves_denied_credential_repair(
    source: md.TableSource, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_project(tmp_path, monkeypatch)

    class DeniedResolver:
        def resolve(self, request: md.CredentialRequest) -> md.SecretValue:
            raise md.DatasourceCredentialError(
                reason="denied",
                reference=request.reference,
                datasource=request.datasource,
                fields=request.fields,
            )

    with (
        md.credential_scope(resolver=DeniedResolver()),
        pytest.raises(md.DatasourceCredentialError) as captured,
    ):
        md.inspect(ms.ref.datasource("warehouse"), source, workspace_dir=tmp_path)
    assert captured.value.reason == "denied"
    assert captured.value.reference == "FIXTURE_TOKEN"
    assert captured.value.repair is not None
    assert "grant access" in captured.value.repair.action


def test_grouped_reference_and_runtime_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_project(tmp_path, monkeypatch)
    monkeypatch.setenv("MARIVO_CREDENTIAL_RESOLVER", "missing_process_credentials:resolver")
    md.register(
        md.trino(
            name="grouped",
            host="example.invalid",
            catalog="hive",
            user=md.env("SHARED"),
            auth=md.env("SHARED"),
        )
    )
    resolver = HostResolver()
    profile = require_profile_for_backend_type("trino")
    seen: list[Mapping[str, object]] = []

    def connect(name: str, kwargs: Mapping[str, object]) -> BaseBackend:
        seen.append(kwargs)
        return ibis.duckdb.connect()

    original = require_profile_for_backend_type
    monkeypatch.setattr(
        backends,
        "require_profile_for_backend_type",
        lambda kind: replace(profile, connect=connect) if kind == "trino" else original(kind),
    )
    with md.credential_scope(resolver=resolver):
        service = DatasourceConnectionService(tmp_path)
    try:
        first = service.session_backend("grouped")
        assert len(resolver.requests) == 1
        assert resolver.requests[0].fields == ("auth", "user")
        assert seen[0]["user"] == seen[0]["auth"] == resolver.value
        with md.credential_scope(resolver=resolver):
            assert service.session_backend("grouped") is first
            with (
                md.credential_scope(resolver=HostResolver()),
                pytest.raises(md.DatasourceCredentialScopeError),
            ):
                service.session_backend("grouped")
            assert service.session_backend("grouped") is first
        service.close_all()
        resolver.value = "rotated-fixture-value"
        service.session_backend("grouped")
        assert len(resolver.requests) == 2
        assert seen[1]["auth"] == resolver.value
    finally:
        service.close_all()


def test_default_runtime_and_external_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_project(tmp_path, monkeypatch)
    default = DatasourceConnectionService(tmp_path)
    local = ibis.duckdb.connect()
    resolver = HostResolver()
    with md.credential_scope(resolver=resolver):
        service = DatasourceConnectionService(tmp_path, backends={"local": lambda: local})
        factory_service = DatasourceConnectionService(tmp_path, backend_factory=lambda name: local)
        with pytest.raises(md.DatasourceCredentialScopeError):
            default.session_backend("warehouse")
        assert service.session_backend("warehouse").raw_sql("SELECT 1").fetchall() == [(1,)]
        with md.credential_scope(resolver=HostResolver()):
            assert service.session_backend("local") is local
            assert factory_service.session_backend("anything") is local
    service.close_all()
    factory_service.close_all()
    assert len(resolver.requests) == 1


def test_host_owns_credential_service_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_project(tmp_path, monkeypatch)

    class Resolver:
        def resolve(self, request: md.CredentialRequest) -> md.SecretValue:
            raise md.DatasourceCredentialError(
                reason="timeout",
                reference=request.reference,
                datasource=request.datasource,
                fields=request.fields,
            )

    with md.credential_scope(resolver=Resolver()):
        result = md.load().test("warehouse", timeout_seconds=1)
    assert result.failure is not None and result.failure.code == "credential_timeout"


def test_secret_display_copy_serialization_and_help() -> None:
    assert marivo.help is show_help
    secret = md.SecretValue("display-canary")
    for operation in (pickle.dumps, copy.copy, copy.deepcopy):
        with pytest.raises(TypeError):
            operation(secret)


def test_observe_source_materialization_failure_redacts_credentials(
    authoring_evidence_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:

    project = authoring_evidence_project
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(project))
    md.register(
        md.duckdb(
            name="warehouse",
            path=str(project / "warehouse.duckdb"),
            secrets=[
                md.http_bearer_secret(scope="http://127.0.0.1/", token=md.env("FIXTURE_TOKEN"))
            ],
        ),
    )
    resolver = HostResolver()
    with md.credential_scope(resolver=resolver):
        session = mv.session.get_or_create("credential-materialization-failure")
    original_table = Backend.table

    def fail_table(self: Backend, name: str, **kwargs: object) -> object:
        if name == "orders":
            raise RuntimeError("metadata driver echoed " + resolver.value)
        return original_table(self, name, **kwargs)

    monkeypatch.setattr(Backend, "table", fail_table)
    revenue = session.catalog.get("metric:sales.revenue")
    assert revenue.kind == "metric"
    try:
        with pytest.raises(DatasourceConnectionError):
            session.observe(
                {"revenue": revenue},
                where=session.time_scope(start="2026-07-01", end="2026-08-01"),
            ).compute()
    finally:
        pass


@pytest.mark.parametrize("injected", [False, True])
def test_raw_sql_failure_keeps_contract_and_redacts_credentials(
    authoring_evidence_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    injected: bool,
) -> None:

    project = authoring_evidence_project
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(project))
    resolver = HostResolver()
    if injected:
        md.register(
            md.duckdb(
                name="warehouse",
                path=str(project / "warehouse.duckdb"),
                secrets=[
                    md.http_bearer_secret(scope="http://127.0.0.1/", token=md.env("FIXTURE_TOKEN"))
                ],
            ),
        )
    original_raw_sql = Backend.raw_sql

    def fail_query(self: Backend, query: str, **kwargs: object) -> object:
        if "missing_column" in query:
            raise RuntimeError("query driver echoed " + resolver.value)
        return original_raw_sql(self, query, **kwargs)

    monkeypatch.setattr(Backend, "raw_sql", fail_query)
    with (
        md.credential_scope(resolver=resolver) if injected else nullcontext(),
        pytest.raises(DatasourceRawSqlError) as captured,
    ):
        md.raw_sql(
            ms.ref.datasource("warehouse"),
            "SELECT missing_column FROM orders",
            reason="Verify the query failure contract",
        )
    error = captured.value
    assert error.effect_observed is not None
    assert error.effect_observed.query_executed is True
    assert error.repair is not None
    assert error.repair.help_target.canonical_id == "raw_sql"


@pytest.mark.parametrize("injected", [False, True])
@pytest.mark.parametrize("error_name", ["PERMISSION_DENIED", "TABLE_NOT_FOUND"])
def test_injected_metadata_denial_keeps_projected_schema_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, injected: bool, error_name: str
) -> None:

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("FIXTURE_USER", "fixture-default-user")
    md.register(
        md.trino(
            name="warehouse", host="example.invalid", catalog="hive", user=md.env("FIXTURE_USER")
        )
    )
    resolver = HostResolver()

    class DeniedError(Exception):
        def __init__(self) -> None:
            self.error_name = error_name
            super().__init__("metadata driver echoed " + resolver.value)

    class Backend:
        disconnected = False

        def table(self, name: str, **kwargs: object) -> object:
            raise DeniedError()

        def disconnect(self) -> None:
            self.disconnected = True

    backend = Backend()
    profile = replace(PROFILE, connect=lambda name, kwargs: backend)
    monkeypatch.setattr(backends, "require_profile_for_backend_type", lambda kind: profile)
    source = md.table("orders", columns={"amount": "amount"})
    with md.credential_scope(resolver=resolver) if injected else nullcontext():
        if error_name == "PERMISSION_DENIED":
            inspection = md.inspect(ms.ref.datasource("warehouse"), source)
            assert inspection.physical_extent.source == "metadata_unavailable"
            assert inspection.partitioning.state == "unknown"
        with pytest.raises(DatasourceMetadataError):
            md.inspect(
                ms.ref.datasource("warehouse"),
                md.table("orders") if error_name == "PERMISSION_DENIED" else source,
            )
    assert backend.disconnected


def test_subprocess_resolver_uses_stdin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    register_project(tmp_path, monkeypatch)
    code = """
import sys
import marivo.datasource as md
class Resolver:
    def __init__(self):
        self.value = sys.stdin.read()
    def resolve(self, request):
        return md.SecretValue(self.value)
with md.credential_scope(resolver=Resolver()):
    result = md.load().test('warehouse')
assert result.ok, result.failure
print('credential-subprocess-ok')
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        input="pipe-only-fixture-canary",
        text=True,
        capture_output=True,
        timeout=15,
        check=True,
    )
    assert result.stdout.strip() == "credential-subprocess-ok"


@pytest.mark.slow
def test_public_semantic_analysis_and_resume(
    authoring_evidence_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    project = authoring_evidence_project
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(project))
    backend = ibis.duckdb.connect(project / "warehouse.duckdb")
    backend.raw_sql("ALTER TABLE orders ALTER query_id TYPE BIGINT")
    backend.disconnect()
    declaration = project / "models/datasources/warehouse.py"
    declaration.write_text(
        declaration.read_text().replace(
            ")\n",
            ", secrets=[md.http_bearer_secret(scope='http://127.0.0.1/', token=md.env('FIXTURE_TOKEN'))])\n",
        )
    )
    resolver = HostResolver()
    with md.credential_scope(resolver=resolver):
        catalog = ms.load(workspace_dir=project)
        session = mv.session.get_or_create("credentials-e2e")
        inspection = md.inspect(ms.ref.datasource("warehouse"), md.table("orders"))
        inspection.sample(scope=md.unpruned(max_rows=10, timeout_seconds=10), columns=("amount",))
    try:
        preview = catalog.preview(
            catalog.get("entity:sales.orders"), scope=md.unpruned(max_rows=10, timeout_seconds=10)
        )
        assert preview.rows
        assert catalog.validate([catalog.get("entity:sales.orders")]).status == "current"
        revenue = catalog.get("metric:sales.revenue")
        entity = session.catalog.get("entity:sales.orders")
        assert revenue.kind == "metric"
        assert entity.kind == "entity"
        query = session.observe(
            {"revenue": revenue},
            where=mv.in_population(session.members(entity)),
        )
        frame = query.compute()
        assert not frame.to_pandas().empty
        with (
            md.credential_scope(resolver=HostResolver()),
            pytest.raises(md.DatasourceCredentialScopeError) as scope_failure,
        ):
            query.compute()
        assert (
            scope_failure.value.expected == "the runtime's captured resolver or no explicit scope"
        )
        assert scope_failure.value.received == "a different explicit credential resolver"
        assert scope_failure.value.location == "datasource connection acquisition"
        assert scope_failure.value.repair is not None
        assert scope_failure.value.repair.help_target.canonical_id == "credential_scope"
        session_id = session.id

        def fail_query(self: Backend, expr: object, **kwargs: object) -> object:
            raise RuntimeError("query echoed " + resolver.value)

        with monkeypatch.context() as patched:
            patched.setattr(Backend, "raw_sql", fail_query)
            with pytest.raises(DatasourceConnectionError) as failed:
                query.compute()
    finally:
        pass
    other = HostResolver("resumed-fixture-token")
    with md.credential_scope(resolver=other):
        resumed = mv.session.resume(session_id)
    try:
        resumed_revenue = resumed.catalog.get("metric:sales.revenue")
        resumed_entity = resumed.catalog.get("entity:sales.orders")
        assert resumed_revenue.kind == "metric"
        assert resumed_entity.kind == "entity"
        assert (
            not resumed.observe(
                {"revenue": resumed_revenue},
                where=mv.in_population(resumed.members(resumed_entity)),
            )
            .compute()
            .to_pandas()
            .empty
        )
    finally:
        pass
    cold_code = "\nimport sys\nimport marivo.analysis as mv\nimport marivo.datasource as md\nclass Resolver:\n    def __init__(self):\n        self.value = sys.stdin.read()\n    def resolve(self, request):\n        return md.SecretValue(self.value)\nwith md.credential_scope(resolver=Resolver()):\n    session = mv.session.resume(sys.argv[1])\ntry:\n    members = session.members(session.catalog.get('entity:sales.orders'))\n    frame = session.observe(\n        {'revenue': session.catalog.get('metric:sales.revenue')},\n        where=mv.in_population(members),\n    ).compute()\n    assert not frame.to_pandas().empty\nfinally:\n    pass\nprint('cold-resume-ok')\n"
    cold = subprocess.run(
        [sys.executable, "-c", cold_code, session_id],
        env=dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2])),
        input="cold-fixture-token",
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert cold.returncode == 0, cold.stderr
    assert cold.stdout.strip() == "cold-resume-ok"
    assert resolver.requests and other.requests
    assert all(request.project_root == project for request in (*resolver.requests, *other.requests))
    for path in (project / ".marivo").rglob("*"):
        if path.is_file():
            contents = path.read_bytes()


def test_real_http_authentication(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:

    for key in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.delenv(key, raising=False)
    resolver = HostResolver()
    accepted: list[str] = []
    body = b'[{"amount":10},{"amount":20}]'

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.headers.get("Authorization") != "Bearer " + resolver.value:
                self.send_error(403)
                return
            accepted.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_HEAD(self) -> None:
            self.do_GET()

        @override
        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    scope = f"http://127.0.0.1:{server.server_port}/"
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    md.register(
        md.duckdb(
            name="warehouse",
            path=":memory:",
            secrets=[md.http_bearer_secret(scope=scope, token=md.env("FIXTURE_TOKEN"))],
        )
    )
    try:
        with (
            md.credential_scope(resolver=resolver),
            DatasourceConnectionService(tmp_path).use_backend("warehouse") as backend,
        ):
            assert backend.raw_sql(
                f"SELECT sum(amount) FROM read_json_auto('{scope}data.json')"
            ).fetchall() == [(30,)]
        assert accepted
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_concurrent_contexts_keep_resolvers_separate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    register_project(tmp_path, monkeypatch)
    barrier = threading.Barrier(2)

    class Resolver(HostResolver):
        @override
        def resolve(self, request: md.CredentialRequest) -> md.SecretValue:
            barrier.wait(timeout=5)
            return super().resolve(request)

    resolvers = (Resolver("first-fixture-token"), Resolver("second-fixture-token"))

    def run(resolver: HostResolver) -> bool:
        with (
            md.credential_scope(resolver=resolver),
            DatasourceConnectionService(tmp_path).use_backend("warehouse") as backend,
        ):
            return credentials.injected_values(backend)[0].reveal() == resolver.value

    with ThreadPoolExecutor(max_workers=2) as workers:
        assert list(workers.map(run, resolvers)) == [True, True]
    assert all(len(resolver.requests) == 1 for resolver in resolvers)


def test_no_refs_or_static_reads_do_not_resolve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_project(tmp_path, monkeypatch)
    md.register(md.duckdb(name="public", path=":memory:"))
    resolver = HostResolver()
    with md.credential_scope(resolver=resolver):
        md.load().describe("warehouse")
        md.load().list()
        show_help("datasource.credential_scope")
        assert md.load().test("public").ok


def test_bound_project_survives_ambient_project_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "owned"
    project.mkdir()
    register_project(project, monkeypatch)
    catalog = md.load(workspace_dir=project)
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(other))
    resolver = HostResolver()
    with md.credential_scope(resolver=resolver):
        assert catalog.test("warehouse").ok
        with DatasourceConnectionService(project).use_backend("warehouse") as backend:
            assert backend.raw_sql("SELECT 1").fetchall() == [(1,)]
        assert manage.test_no_persist("warehouse", project_root=project).ok
    assert len(resolver.requests) == 3
    assert all(request.project_root == project for request in resolver.requests)


def test_driver_error_redaction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    register_project(tmp_path, monkeypatch)
    profile = require_profile_for_backend_type("duckdb")
    resolver = HostResolver()

    def fail(name: str, kwargs: Mapping[str, object]) -> BaseBackend:
        raise RuntimeError("driver echoed " + resolver.value)

    monkeypatch.setattr(
        backends, "require_profile_for_backend_type", lambda kind: replace(profile, connect=fail)
    )
    with (
        md.credential_scope(resolver=resolver),
        pytest.raises(DatasourceConnectionError) as captured,
    ):
        open_backend(load_datasource("warehouse", tmp_path), project_root=tmp_path)
    assert resolver.value not in "".join(traceback.format_exception(captured.value))
