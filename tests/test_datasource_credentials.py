"""Host credential injection through public APIs and existing runtime owners."""

from __future__ import annotations

import copy
import pickle
import subprocess
import sys
import threading
import traceback
from collections.abc import Mapping
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path

import ibis
import pytest
from ibis.backends import BaseBackend

import marivo
import marivo.datasource as md
from marivo.datasource import backends, credentials, manage, secrets
from marivo.datasource.errors import CredentialFailureReason, DatasourceConnectionError
from marivo.datasource.runtime import DatasourceConnectionService


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
            http_scope="http://127.0.0.1/",
            http_bearer_token_env="FIXTURE_TOKEN",
        )
    )


def test_public_connect_test_and_no_default_cache(
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
        assert md.test("warehouse").ok
        assert manage.test_no_persist("warehouse", project_root=tmp_path).ok
        connection = md.connect("warehouse")
    try:
        with connection as backend:
            assert backend is connection.backend
            assert backend.raw_sql("SELECT 1").fetchall() == [(1,)]
            with md.credential_scope(resolver=HostResolver("other-value")):
                assert backend.raw_sql("SELECT 2").fetchall() == [(2,)]
    finally:
        connection.disconnect()
    assert len(resolver.requests) == 3
    for request in resolver.requests:
        assert request.project_root == tmp_path
        assert request.fields == ("http_bearer_token",)
        assert request.deadline_monotonic is not None
        assert not request.cancelled
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert resolver.value.encode() not in path.read_bytes()


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
            md.connect("warehouse")
        result = md.test("warehouse")
    assert captured.value.reason == reason
    assert "provider-cause-canary" not in "".join(traceback.format_exception(captured.value))
    assert result.failure is not None
    assert result.failure.code == "credential_" + reason.replace("-", "_")
    assert result.repair is not None
    assert result.repair.help_target.canonical_id == "credential_scope"
    assert "export " not in result.repair.action


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
        result = md.test("warehouse")
    assert result.failure is not None
    assert result.failure.code == "credential_invalid_response"
    assert "canary" not in repr(result.failure)


def test_raw_sdk_failure_is_sanitized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    register_project(tmp_path, monkeypatch)

    class Resolver:
        def resolve(self, request: md.CredentialRequest) -> md.SecretValue:
            raise RuntimeError("sdk-payload-canary")

    with (
        md.credential_scope(resolver=Resolver()),
        pytest.raises(md.DatasourceCredentialError) as captured,
    ):
        md.connect("warehouse")
    assert "sdk-payload-canary" not in "".join(traceback.format_exception(captured.value))
    assert captured.value.reason == "unavailable"


def test_grouped_reference_and_runtime_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_project(tmp_path, monkeypatch)
    md.register(
        md.trino(
            name="grouped",
            host="example.invalid",
            catalog="hive",
            user_env="SHARED",
            auth_env="SHARED",
        )
    )
    resolver = HostResolver()
    profile = backends.require_profile_for_backend_type("trino")
    seen: list[Mapping[str, object]] = []

    def connect(name: str, kwargs: Mapping[str, object]) -> BaseBackend:
        seen.append(kwargs)
        return ibis.duckdb.connect()

    original = backends.require_profile_for_backend_type
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
            with md.credential_scope(resolver=HostResolver()):
                with pytest.raises(md.DatasourceCredentialScopeError):
                    service.session_backend("grouped")
                with pytest.raises(md.DatasourceCredentialScopeError):
                    service.engine_timezone("grouped")
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


def test_timeout_discards_late_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    register_project(tmp_path, monkeypatch)
    release, finished = threading.Event(), threading.Event()
    requests: list[md.CredentialRequest] = []

    class Resolver:
        def resolve(self, request: md.CredentialRequest) -> md.SecretValue:
            requests.append(request)
            try:
                assert release.wait(5)
                assert request.cancelled
                return md.SecretValue("late-result-canary")
            finally:
                finished.set()

    try:
        with md.credential_scope(resolver=Resolver()):
            result = md.test("warehouse", timeout_seconds=1)
        assert result.failure is not None
        assert result.failure.code == "credential_timeout"
        assert len(requests) == 1
    finally:
        release.set()
        assert finished.wait(5)


def test_late_connection_is_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    register_project(tmp_path, monkeypatch)
    release, closed = threading.Event(), threading.Event()

    class Backend:
        def disconnect(self) -> None:
            closed.set()

    def build(datasource: object) -> backends.BuiltDatasourceBackend:
        assert release.wait(5)
        return backends.BuiltDatasourceBackend(Backend(), ())

    monkeypatch.setattr(backends, "build_backend_with_secrets", build)
    try:
        with (
            md.credential_scope(resolver=HostResolver()),
            pytest.raises(md.errors.DatasourceConnectionTimeoutError),
        ):
            md.connect("warehouse", timeout_seconds=1)
    finally:
        release.set()
        assert closed.wait(5)


def test_secret_display_copy_serialization_and_help(capsys: pytest.CaptureFixture[str]) -> None:
    secret = md.SecretValue("display-canary")
    assert repr(secret) == str(secret) == "SecretValue(<redacted>)"
    for operation in (pickle.dumps, copy.copy, copy.deepcopy):
        with pytest.raises(TypeError):
            operation(secret)
    for target in (
        "credential_scope",
        "CredentialRequest",
        "CredentialResolver",
        "SecretValue",
        "DatasourceCredentialError",
        "DatasourceCredentialScopeError",
    ):
        marivo.help("datasource." + target)
    output = capsys.readouterr().out
    assert "display-canary" not in output
    assert "invalid-response" in output
    assert "reference:" in output
    assert "including cache hits" in output


def test_driver_error_redaction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    register_project(tmp_path, monkeypatch)
    profile = backends.require_profile_for_backend_type("duckdb")
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
        md.connect("warehouse")
    assert resolver.value not in "".join(traceback.format_exception(captured.value))


def test_observe_source_materialization_failure_redacts_credentials(
    authoring_evidence_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from ibis.backends.duckdb import Backend

    import marivo.analysis as mv
    import marivo.semantic as ms
    from marivo.semantic.errors import SemanticRuntimeError

    project = authoring_evidence_project
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(project))
    md.register(
        md.duckdb(
            name="warehouse",
            path=str(project / "warehouse.duckdb"),
            http_scope="http://127.0.0.1/",
            http_bearer_token_env="FIXTURE_TOKEN",
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
    try:
        with pytest.raises(SemanticRuntimeError) as captured:
            session.observe(
                ms.ref.metric("sales.revenue"),
                time_scope=mv.time_scope(start="2026-07-01", end="2026-08-01"),
            )
        assert captured.value.kind == "materialize_failed"
        assert captured.value.semantic_refs == ("sales.orders",)
        assert "<redacted>" in str(captured.value)
        assert resolver.value not in "".join(traceback.format_exception(captured.value))
        assert resolver.value not in caplog.text
    finally:
        session.close()
    for path in (project / ".marivo").rglob("*"):
        if path.is_file():
            assert resolver.value.encode() not in path.read_bytes()


@pytest.mark.parametrize("injected", [False, True])
def test_raw_sql_failure_keeps_contract_and_redacts_credentials(
    authoring_evidence_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    injected: bool,
) -> None:
    from ibis.backends.duckdb import Backend

    import marivo.semantic as ms
    from marivo.datasource.errors import DatasourceRawSqlError

    project = authoring_evidence_project
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(project))
    resolver = HostResolver()
    if injected:
        md.register(
            md.duckdb(
                name="warehouse",
                path=str(project / "warehouse.duckdb"),
                http_scope="http://127.0.0.1/",
                http_bearer_token_env="FIXTURE_TOKEN",
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
    if injected:
        assert "<redacted>" in str(error)
        assert resolver.value not in "".join(traceback.format_exception(error))


@pytest.mark.parametrize("injected", [False, True])
@pytest.mark.parametrize("error_name", ["PERMISSION_DENIED", "TABLE_NOT_FOUND"])
def test_injected_metadata_denial_keeps_projected_schema_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, injected: bool, error_name: str
) -> None:
    import marivo.semantic as ms
    from marivo.datasource.engines.trino import PROFILE
    from marivo.datasource.errors import DatasourceMetadataError

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("FIXTURE_USER", "fixture-default-user")
    md.register(
        md.trino(name="warehouse", host="example.invalid", catalog="hive", user_env="FIXTURE_USER")
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
    source = md.table("orders", columns={"amount": md.source_column("amount", data_type="float64")})
    with md.credential_scope(resolver=resolver) if injected else nullcontext():
        if error_name == "PERMISSION_DENIED":
            inspection = md.inspect(ms.ref.datasource("warehouse"), source)
            assert inspection.physical_extent.source == "metadata_unavailable"
            assert inspection.partitioning.state == "unknown"
            if injected:
                assert resolver.value not in "\n".join(inspection.warnings)
        with pytest.raises(DatasourceMetadataError) as captured:
            md.inspect(
                ms.ref.datasource("warehouse"),
                md.table("orders") if error_name == "PERMISSION_DENIED" else source,
            )
        if injected:
            assert resolver.value not in "".join(traceback.format_exception(captured.value))
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
    result = md.test('warehouse')
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
    assert "pipe-only-fixture-canary" not in result.stderr


def test_public_semantic_analysis_and_resume(
    authoring_evidence_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import marivo.analysis as mv
    import marivo.semantic as ms

    project = authoring_evidence_project
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(project))
    declaration = project / "models/datasources/warehouse.py"
    declaration.write_text(
        declaration.read_text().replace(
            ")\n", ", http_scope='http://127.0.0.1/', http_bearer_token_env='FIXTURE_TOKEN')\n"
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
            ms.ref.entity("sales.orders"), scope=md.unpruned(max_rows=10, timeout_seconds=10)
        )
        assert preview.rows
        assert catalog.source_health([ms.ref.entity("sales.orders")]).status == "current"
        frame = session.observe(
            ms.ref.metric("sales.revenue"),
            time_scope=mv.time_scope(start="2026-07-01", end="2026-08-01"),
        )
        assert not frame.to_pandas().empty
        with (
            md.credential_scope(resolver=HostResolver()),
            pytest.raises(md.DatasourceCredentialScopeError),
        ):
            session.observe(
                ms.ref.metric("sales.revenue"),
                time_scope=mv.time_scope(start="2026-07-01", end="2026-08-01"),
            )
        session_id = session.id
        from ibis.backends.duckdb import Backend

        from marivo.analysis.errors import BackendError

        def fail_query(self: Backend, expr: object, **kwargs: object) -> object:
            raise RuntimeError("query echoed " + resolver.value)

        with monkeypatch.context() as patched:
            patched.setattr(Backend, "execute", fail_query)
            with pytest.raises(BackendError) as failed:
                session.observe(
                    ms.ref.metric("sales.revenue"),
                    time_scope=mv.time_scope(start="2026-07-01", end="2026-08-01"),
                )
            assert resolver.value not in "".join(traceback.format_exception(failed.value))
    finally:
        session.close()
        catalog._project._connection_service().close_all()
    other = HostResolver("resumed-fixture-token")
    with md.credential_scope(resolver=other):
        resumed = mv.session.resume(session_id)
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
    cold_code = """
import sys
import marivo.analysis as mv
import marivo.datasource as md
import marivo.semantic as ms
class Resolver:
    def __init__(self):
        self.value = sys.stdin.read()
    def resolve(self, request):
        return md.SecretValue(self.value)
with md.credential_scope(resolver=Resolver()):
    session = mv.session.resume(sys.argv[1])
try:
    frame = session.observe(ms.ref.metric('sales.revenue'), time_scope=mv.time_scope(start='2026-07-01', end='2026-08-01'))
    assert not frame.to_pandas().empty
finally:
    session.close()
print('cold-resume-ok')
"""
    cold = subprocess.run(
        [sys.executable, "-c", cold_code, session_id],
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
            assert resolver.value.encode() not in path.read_bytes()
            assert other.value.encode() not in path.read_bytes()


def test_real_http_authentication(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    scope = f"http://127.0.0.1:{server.server_port}/"
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    md.register(
        md.duckdb(
            name="warehouse",
            path=":memory:",
            http_scope=scope,
            http_bearer_token_env="FIXTURE_TOKEN",
        )
    )
    try:
        with md.credential_scope(resolver=resolver), md.connect("warehouse") as backend:
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
    from concurrent.futures import ThreadPoolExecutor

    register_project(tmp_path, monkeypatch)
    barrier = threading.Barrier(2)

    class Resolver(HostResolver):
        def resolve(self, request: md.CredentialRequest) -> md.SecretValue:
            barrier.wait(timeout=5)
            return super().resolve(request)

    resolvers = (Resolver("first-fixture-token"), Resolver("second-fixture-token"))

    def run(resolver: HostResolver) -> bool:
        with md.credential_scope(resolver=resolver), md.connect("warehouse") as backend:
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
        md.describe("warehouse")
        md.load().list()
        marivo.help("datasource.credential_scope")
        assert md.test("public").ok
    assert not resolver.requests


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
        with catalog.connect("warehouse") as backend:
            assert backend.raw_sql("SELECT 1").fetchall() == [(1,)]
        assert manage.test_no_persist("warehouse", project_root=project).ok
    assert len(resolver.requests) == 3
    assert all(request.project_root == project for request in resolver.requests)
