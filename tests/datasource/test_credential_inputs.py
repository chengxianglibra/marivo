"""Credential declaration, persistence and operation-owned secret installation."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

import marivo.datasource as md
from marivo.datasource import backends, secrets, store
from marivo.datasource.authoring import _ir_from_spec
from marivo.datasource.capabilities import (
    ProviderStatement,
    provider_statement_log,
    render_provider_statement,
)
from marivo.datasource.engines import ENGINE_PROFILES
from marivo.datasource.errors import DatasourceConnectionError, DatasourceFieldInvalidError
from marivo.datasource.ir import DatasourceSourceLocation
from marivo.datasource.runtime import load_datasource, open_backend
from marivo.doctor import _static_datasources_from_file


def s3(**changes: object) -> md.S3Secret:
    kwargs: dict[str, object] = {
        "scope": "s3://bucket/path.with.dots/",
        "region": "us-east-1",
        "key_id": md.env("KEY"),
        "secret": md.env("SECRET"),
    }
    kwargs.update(changes)
    return md.s3_secret(**kwargs)


@pytest.mark.parametrize(
    "changes",
    [
        {"scope": "s3://"},
        {"scope": "s3://:123/"},
        {"scope": "s3://key:literal-canary@bucket/"},
        {"scope": "s3://bucket/?credential=literal-canary"},
        {"scope": "https://bucket/"},
        {"region": ""},
        {"key_id": "literal-canary"},
        {"secret": "literal-canary"},
        {"session_token": "literal-canary"},
        {"url_style": "invalid"},
        {"use_ssl": "false"},
    ],
)
def test_s3_rejects_invalid_declarations_without_echoing_values(changes: dict[str, object]) -> None:
    with pytest.raises(DatasourceFieldInvalidError) as caught:
        s3(**changes)
    assert caught.value.repair is not None
    assert "literal-canary" not in str(caught.value)


@pytest.mark.parametrize("name", ["", " ", None, 42])
def test_env_requires_nonempty_name(name: object) -> None:
    with pytest.raises(DatasourceFieldInvalidError):
        md.env(name)


def test_secret_values_are_immutable_and_disclose_references() -> None:
    ref = md.env("KEY")
    with pytest.raises(FrozenInstanceError):
        ref.name = "OTHER"
    headers = {"x.api.key": ref}
    secret = md.http_headers_secret(scope="https://api.example/v1/", headers=headers)
    headers["x.api.key"] = md.env("CHANGED")
    assert secret.headers["x.api.key"] == ref
    with pytest.raises(TypeError):
        secret.headers["x.api.key"] = md.env("OTHER")
    for value in (
        ref,
        s3(),
        secret,
        md.http_bearer_secret(scope="https://api.example/", token=ref),
    ):
        assert "\n" not in repr(value)
        assert ".show()" in repr(value)
        assert value.render()
    assert "refs=1" in repr(secret)


def test_duplicate_scopes_and_extra_credentials_are_rejected() -> None:
    bearer = md.http_bearer_secret(scope="https://api.example/", token=md.env("TOKEN"))
    headers = md.http_headers_secret(scope=bearer.scope, headers={"x-key": md.env("KEY")})
    for values in ([s3(), s3()], [bearer, headers], ["literal-canary"]):
        with pytest.raises(DatasourceFieldInvalidError):
            md.duckdb(name="lake", secrets=values)
    for key in (
        "secrets",
        "key_id",
        "session_token",
        "password",
        "password_env",
        "http_headers_env",
        "S3_ACCESS_KEY_ID",
        "s3_secret_access_key",
        "s3_session_token",
        "http_proxy_password",
    ):
        with pytest.raises(DatasourceFieldInvalidError):
            md.duckdb(name="lake", extra={key: "literal-canary"})


def test_register_and_doctor_round_trip_all_variants(tmp_path: Path) -> None:
    spec = md.duckdb(
        name="lake",
        secrets=[
            s3(session_token=md.env("SESSION")),
            md.http_bearer_secret(scope="https://api.example/v1/", token=md.env("TOKEN")),
            md.http_headers_secret(
                scope="https://api.example/v2.0/", headers={"x.api.key": md.env("HEADER")}
            ),
        ],
    )
    md.register(spec, project_root=tmp_path)
    restored = store.load_one("lake", tmp_path)
    assert restored is not None
    assert restored.fields == spec.fields
    assert restored.env_refs == spec.env_refs
    path = tmp_path / "models/datasources/lake.py"
    parsed = _static_datasources_from_file(path)
    assert not parsed.diagnostics
    assert parsed.datasources[0].fields == spec.fields
    assert parsed.datasources[0].env_refs == spec.env_refs
    assert "http@https://api.example/v2.0/.header:x.api.key" in spec.env_refs
    assert "credentials: http@https://api.example/v1/" in spec.render()


@pytest.mark.parametrize(
    "expression",
    [
        "md.duckdb(name='lake', http_bearer_token_env='TOKEN')",
        "md.postgres(name='pg', host='db', database='db', password_env='PW')",
        "md.duckdb(name='lake', extra=['invalid'])",
        "md.duckdb(name='lake', name='shadow')",
        "md.duckdb(name='lake', secrets=[md.http_bearer_secret(scope='https://api/', token=md.env('ONE'), token=md.env('TWO'))])",
        "md.duckdb(name='lake', secrets=[md.http_bearer_secret(scope='https://api/', token=md.env(dynamic()))])",
    ],
)
def test_doctor_rejects_old_or_executable_credential_syntax(
    tmp_path: Path, expression: str
) -> None:
    path = tmp_path / "lake.py"
    path.write_text("import marivo.datasource as md\n" + expression + "\n")
    parsed = _static_datasources_from_file(path)
    assert not parsed.datasources


def test_multiple_secrets_resolver_deduplication_rotation_and_http_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden() -> secrets.LocalPlaintextCache:
        pytest.fail("host credentials must not use the default cache")

    monkeypatch.setattr(secrets.LocalPlaintextCache, "default", forbidden)
    spec = md.duckdb(
        name="lake",
        secrets=[
            s3(session_token=md.env("SESSION")),
            md.http_bearer_secret(scope="https://api.example////////", token=md.env("KEY")),
            md.http_headers_secret(
                scope="https://api.example/v1/", headers={"x.key": md.env("KEY")}
            ),
        ],
    )
    md.register(spec, project_root=tmp_path)
    requests: list[md.CredentialRequest] = []

    class Resolver:
        value = "initial-key"

        def resolve(self, request: md.CredentialRequest) -> md.SecretValue:
            requests.append(request)
            return md.SecretValue(self.value if request.reference == "KEY" else "private-value")

    resolver = Resolver()
    for key in ("initial-key", "rotated-key"):
        resolver.value = key
        requests.clear()
        with md.credential_scope(resolver=resolver):
            built = open_backend(load_datasource("lake", tmp_path), project_root=tmp_path)
        try:
            rows = built.backend.raw_sql(
                "SELECT name, secret_string FROM duckdb_secrets() ORDER BY name"
            ).fetchall()
            assert [row[0] for row in rows] == ["marivo_lake_0", "marivo_lake_1", "marivo_lake_2"]
            assert key in rows[2][1]
            assert "endpoint=s3.amazonaws.com" in rows[2][1]
            assert "url_style=vhost" in rows[2][1]
            assert "use_ssl=true" in rows[2][1]
            auth = built.backend._marivo_duckdb_http_auth
            assert auth.headers_for("https://api.example/v1/orders") == {"x.key": key}
            assert auth.headers_for("https://api.example/v2/orders") == {
                "Authorization": f"Bearer {key}"
            }
            assert auth.headers_for("https://elsewhere.example/") == {}
            log = provider_statement_log(built.backend)
            assert len(log) == 3
            assert all("{name}" in row.sql for row in log)
            assert key not in repr(log) and "private-value" not in repr(log)
        finally:
            built.disconnect()
        assert sorted(request.reference for request in requests) == ["KEY", "SECRET", "SESSION"]
        key_request = next(request for request in requests if request.reference == "KEY")
        assert key_request.fields == (
            "http@https://api.example////////.token",
            "http@https://api.example/v1/.header:x.key",
            "s3@s3://bucket/path.with.dots/.key_id",
        )
    assert not (tmp_path / ".marivo/secrets.toml").exists()


def test_env_credentials_cache_after_probe_and_reopen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = secrets.LocalPlaintextCache(tmp_path / "user/secrets.toml")
    monkeypatch.setattr(secrets.LocalPlaintextCache, "default", classmethod(lambda cls: cache))
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("MARIVO_PERSIST_CREDENTIALS", "1")
    monkeypatch.setenv("KEY", "cache-key")
    monkeypatch.setenv("SECRET", "cache-secret")
    md.register(md.duckdb(name="lake", secrets=[s3()]), project_root=tmp_path)
    assert md.load(workspace_dir=tmp_path).test("lake").ok
    assert cache.path.exists()
    monkeypatch.delenv("KEY")
    monkeypatch.delenv("SECRET")
    assert md.load(workspace_dir=tmp_path).test("lake").ok


def test_secret_install_failure_disconnects_and_sanitizes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ibis

    backend = ibis.duckdb.connect()
    disconnected = False
    disconnect = backend.disconnect

    def close() -> None:
        nonlocal disconnected
        disconnected = True
        disconnect()

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("private-install-canary")

    monkeypatch.setattr(backend, "disconnect", close)
    monkeypatch.setattr(backend, "raw_sql", fail)
    profile = replace(ENGINE_PROFILES["duckdb"], connect=lambda name, kwargs: backend)
    monkeypatch.setattr(backends, "require_profile_for_backend_type", lambda kind: profile)
    monkeypatch.setenv("KEY", "private-install-canary")
    monkeypatch.setenv("SECRET", "private-install-canary")
    ir = _ir_from_spec(
        md.duckdb(name="lake", secrets=[s3()]),
        location=DatasourceSourceLocation(file="<test>", line=1),
    )
    with pytest.raises(DatasourceConnectionError) as caught:
        backends.build_backend(ir)
    assert disconnected
    assert "s3" in str(caught.value) and "s3://bucket/path.with.dots/" in str(caught.value)
    assert "private-install-canary" not in str(caught.value)
    assert caught.value.__suppress_context__


def test_parameterized_statements_quote_identifiers_and_reject_literal_slots() -> None:
    statement = ProviderStatement(
        statement_id="duckdb.test",
        template="SELECT ? AS {name}",
        identifier_slots=frozenset({"name"}),
        parameterized=True,
    )
    assert (
        render_provider_statement(
            statement, ENGINE_PROFILES["duckdb"], identifiers={"name": 'quoted"name'}
        )
        == 'SELECT ? AS "quoted""name"'
    )
    with pytest.raises(ValueError, match="literal slots"):
        ProviderStatement(
            statement_id="duckdb.test",
            template="SELECT {value}",
            literal_slots=frozenset({"value"}),
            parameterized=True,
        )


def test_file_secrets_exclude_other_managed_connections_and_are_removed_on_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KEY", "fixture-key")
    monkeypatch.setenv("SECRET", "fixture-secret")
    path = tmp_path / "shared.duckdb"
    location = DatasourceSourceLocation(file="<test>", line=1)
    scoped = _ir_from_spec(
        md.duckdb(name="first", path=str(path), secrets=[s3()]), location=location
    )
    plain = _ir_from_spec(md.duckdb(name="second", path=str(path)), location=location)
    observer = ENGINE_PROFILES["duckdb"].connect("observer", {"path": str(path)})
    try:
        built = backends.build_backend_with_secrets(scoped)
        try:
            with pytest.raises(DatasourceConnectionError, match="exclusive Marivo access"):
                backends.build_backend_with_secrets(plain)
            assert observer.raw_sql(
                "SELECT name FROM duckdb_secrets() WHERE name = 'marivo_first_0'"
            ).fetchall() == [("marivo_first_0",)]
            built.backend.raw_sql("BEGIN")
        finally:
            built.disconnect()
        assert (
            observer.raw_sql(
                "SELECT name FROM duckdb_secrets() WHERE name = 'marivo_first_0'"
            ).fetchall()
            == []
        )

        built = backends.build_backend_with_secrets(plain)
        try:
            with pytest.raises(DatasourceConnectionError, match="exclusive Marivo access"):
                backends.build_backend_with_secrets(scoped)
        finally:
            built.disconnect()

        spec = md.duckdb(name="first", path=str(path), secrets=[s3()])
        md.register(spec, project_root=tmp_path)
        md.raw_sql(spec.ref, "SELECT 1 AS ok", reason="Check cleanup", project_root=tmp_path)
        assert (
            observer.raw_sql(
                "SELECT name FROM duckdb_secrets() WHERE name = 'marivo_first_0'"
            ).fetchall()
            == []
        )
    finally:
        observer.disconnect()


def test_partial_install_cleans_up_a_shared_native_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.datasource.engines import duckdb

    path = tmp_path / "partial.duckdb"
    observer = duckdb.connect("observer", {"path": str(path)})
    monkeypatch.setenv("KEY", "fixture-key")
    monkeypatch.setenv("SECRET", "fixture-secret")
    execute = duckdb.execute_provider_statement

    def fail_second(backend, profile, statement_id, **kwargs):
        if statement_id == "duckdb.secret_s3" and kwargs["identifiers"]["name"] == "marivo_lake_1":
            raise RuntimeError("private-install-canary")
        return execute(backend, profile, statement_id, **kwargs)

    monkeypatch.setattr(duckdb, "execute_provider_statement", fail_second)
    spec = md.duckdb(name="lake", path=str(path), secrets=[s3(), s3(scope="s3://other/")])
    location = DatasourceSourceLocation(file="<test>", line=1)
    try:
        with pytest.raises(DatasourceConnectionError, match="s3://other/") as caught:
            backends.build_backend_with_secrets(_ir_from_spec(spec, location=location))
        assert "private-install-canary" not in str(caught.value)
        assert (
            observer.raw_sql(
                "SELECT name FROM duckdb_secrets() WHERE name LIKE 'marivo_lake_%'"
            ).fetchall()
            == []
        )
        plain = _ir_from_spec(md.duckdb(name="lake", path=str(path)), location=location)
        backends.build_backend_with_secrets(plain).disconnect()
    finally:
        observer.disconnect()


def test_persistent_secret_with_longer_scope_remains_native(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ibis

    directory = tmp_path / "native-secrets"
    initial = ibis.duckdb.connect(secret_directory=str(directory))
    try:
        initial.raw_sql(
            "CREATE PERSISTENT SECRET persistent_probe (TYPE S3, KEY_ID 'fixture-key', SECRET 'fixture-secret', REGION 'us-east-1', SCOPE 's3://bucket/private/')"
        )
    finally:
        initial.disconnect()
    monkeypatch.setenv("KEY", "declared-key")
    monkeypatch.setenv("SECRET", "declared-secret")
    spec = md.duckdb(
        name="lake", secrets=[s3(scope="s3://bucket/")], extra={"secret_directory": str(directory)}
    )
    ir = _ir_from_spec(spec, location=DatasourceSourceLocation(file="<test>", line=1))
    built = backends.build_backend_with_secrets(ir)
    try:
        assert built.backend.raw_sql(
            "SELECT name FROM which_secret('s3://bucket/private/data.parquet', 's3')"
        ).fetchall() == [("persistent_probe",)]
        assert built.backend.raw_sql(
            "SELECT name FROM which_secret('s3://bucket/other/data.parquet', 's3')"
        ).fetchall() == [("marivo_lake_0",)]
    finally:
        built.disconnect()


def test_constructor_signatures_remove_old_keywords() -> None:
    import inspect

    for constructor in (md.duckdb, md.trino, md.postgres, md.mysql, md.clickhouse):
        assert not any(name.endswith("_env") for name in inspect.signature(constructor).parameters)
    assert tuple(inspect.signature(md.duckdb).parameters) == (
        "name",
        "path",
        "read_only",
        "secrets",
        "wall_clock_timezone",
        "ai_context",
        "extra",
    )


@pytest.mark.parametrize("backend", ["postgres", "mysql", "clickhouse", "trino"])
def test_relational_ir_and_generated_fixture_declarations_preserve_identity(
    backend: str, tmp_path: Path
) -> None:
    from tests.shared_fixtures import datasource_arguments_source

    arguments: dict[str, object] = {
        "name": "warehouse",
        "host": "db.example",
        "user": md.env("USER"),
    }
    fields: dict[str, object] = {"host": "db.example"}
    refs = {"user": "USER"}
    if backend == "trino":
        arguments.update(catalog="hive", auth=md.env("AUTH"))
        fields["catalog"] = "hive"
        refs["auth"] = "AUTH"
    else:
        arguments.update(database="app", password=md.env("PASSWORD"))
        fields["database"] = "app"
        refs["password"] = "PASSWORD"
    directory = tmp_path / "models/datasources"
    directory.mkdir(parents=True)
    (directory / "warehouse.py").write_text(
        "import marivo.datasource as md\n"
        + f"md.{backend}({datasource_arguments_source(arguments)})\n"
    )
    restored = store.load_one("warehouse", tmp_path)
    assert restored is not None
    # These are the pre-change relational IR inputs: fingerprint inputs are unchanged.
    assert restored.fields == fields
    assert restored.env_refs == refs
