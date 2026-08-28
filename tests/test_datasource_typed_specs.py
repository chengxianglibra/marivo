"""Contract tests for datasource convenience functions and public spec classes."""

from __future__ import annotations

import inspect
from dataclasses import fields
from pathlib import Path

import pytest

import marivo.datasource as md
import marivo.datasource.authoring as authoring_module
import marivo.semantic as ms
from marivo.datasource.authoring import (
    ClickHouseSpec,
    DuckDBSpec,
    MySQLSpec,
    PostgresSpec,
    SQLiteSpec,
    TrinoSpec,
    _ir_from_spec,
    validate_datasource_name,
)
from marivo.datasource.errors import (
    DatasourceFieldInvalidError,
    DatasourceSecretInPlaintextError,
)
from marivo.datasource.ir import DatasourceIR, DatasourceSourceLocation
from tests.shared_fixtures import rendered_help
from tests.test_agent_result_protocol import assert_conforms


def _ir(
    spec: DuckDBSpec | SQLiteSpec | TrinoSpec | MySQLSpec | PostgresSpec | ClickHouseSpec,
) -> DatasourceIR:
    return _ir_from_spec(
        spec,
        location=DatasourceSourceLocation(file="<test>", line=1),
    )


# -- Public spec class tests (validation, serialization, IR mapping) --


def test_duckdb_spec_defaults_to_memory_path() -> None:
    spec = DuckDBSpec(name="local")
    ir = _ir(spec)

    assert spec.backend_type == "duckdb"
    assert ir.backend_type == "duckdb"
    assert ir.fields == {"path": ":memory:", "read_only": False}
    assert ir.env_refs == {}


def test_duckdb_http_auth_is_scoped_and_environment_backed() -> None:
    bearer = _ir(
        DuckDBSpec(
            name="hawkeye",
            http_scope="http://hawkeye.example/report/api/",
            http_bearer_token_env="HAWKEYE_TOKEN",
        )
    )
    custom = _ir(
        DuckDBSpec(
            name="custom",
            http_scope="https://api.example/v1/",
            http_headers_env={
                "x-secretid": "CHANGE_FOCUS_SECRET_ID",
                "x-signature": "CHANGE_FOCUS_SIGNATURE",
            },
        )
    )

    assert bearer.fields == {
        "path": ":memory:",
        "read_only": False,
        "http_scope": "http://hawkeye.example/report/api/",
    }
    assert bearer.env_refs == {"http_bearer_token": "HAWKEYE_TOKEN"}
    assert custom.env_refs == {
        "http_header:x-secretid": "CHANGE_FOCUS_SECRET_ID",
        "http_header:x-signature": "CHANGE_FOCUS_SIGNATURE",
    }


@pytest.mark.parametrize(
    "kwargs",
    [
        {"http_scope": "https://api.example/"},
        {"http_bearer_token_env": "TOKEN"},
        {
            "http_scope": "https://api.example/",
            "http_headers_env": {},
        },
        {
            "http_scope": "https://api.example/",
            "http_headers_env": {"bad header": "API_KEY"},
        },
        {
            "http_scope": "https://api.example/",
            "http_headers_env": {"X-API-Key": ""},
        },
        {
            "http_scope": "https://api.example/",
            "http_bearer_token_env": "TOKEN",
            "http_headers_env": {"X-API-Key": "API_KEY"},
        },
    ],
)
def test_duckdb_http_auth_rejects_ambiguous_or_incomplete_declarations(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(DatasourceFieldInvalidError):
        DuckDBSpec(name="hawkeye", **kwargs)  # type: ignore[arg-type]


def test_sqlite_spec_maps_path_read_only_and_type_map() -> None:
    spec = SQLiteSpec(
        name="app",
        path="data/app.sqlite",
        read_only=True,
        type_map={"money": "float64"},
    )
    ir = _ir(spec)

    assert spec.backend_type == "sqlite"
    assert ir.backend_type == "sqlite"
    assert ir.fields == {
        "path": "data/app.sqlite",
        "read_only": True,
        "type_map": {"money": "float64"},
    }
    assert ir.env_refs == {}


@pytest.mark.parametrize(
    ("name", "suggested"),
    [
        ("prod-mysql", "prod_mysql"),
        ("Warehouse", "warehouse"),
        ("1warehouse", "ds_1warehouse"),
    ],
)
def test_datasource_name_grammar_rejects_legacy_shapes_with_valid_rename(
    name: str,
    suggested: str,
) -> None:
    with pytest.raises(DatasourceFieldInvalidError) as exc_info:
        validate_datasource_name(name)

    assert exc_info.value.expected == "[a-z][a-z0-9_]*"
    assert suggested in str(exc_info.value)
    assert "Update references to use the new identity" in str(exc_info.value)


def test_datasource_name_validation_uses_shared_ref_segment_grammar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, str]] = []
    shared_validator = authoring_module._validate_segment

    def recording_validator(value: object, *, role: str) -> str:
        calls.append((value, role))
        return shared_validator(value, role=role)

    monkeypatch.setattr(authoring_module, "_validate_segment", recording_validator)

    validate_datasource_name("warehouse")

    assert calls == [("warehouse", "datasource name")]


def test_trino_spec_maps_declared_fields_and_named_secret_env_refs() -> None:
    spec = TrinoSpec(
        name="warehouse",
        host="trino.example",
        catalog="hive",
        port=8443,
        timezone="Asia/Shanghai",
        client_tags=("agent", "semantic-authoring"),
        session_properties={"query_max_run_time": "5m"},
        user_env="TRINO_USER",
        auth_env="TRINO_AUTH",
    )
    ir = _ir(spec)

    assert spec.backend_type == "trino"
    assert ir.fields == {
        "host": "trino.example",
        "catalog": "hive",
        "port": 8443,
        "timezone": "Asia/Shanghai",
        "client_tags": ["agent", "semantic-authoring"],
        "session_properties": {"query_max_run_time": "5m"},
    }
    assert ir.env_refs == {"user": "TRINO_USER", "auth": "TRINO_AUTH"}


def test_trino_user_env_is_required_by_spec_and_helper() -> None:
    assert inspect.signature(TrinoSpec).parameters["user_env"].default is inspect.Parameter.empty
    assert inspect.signature(md.trino).parameters["user_env"].default is inspect.Parameter.empty

    with pytest.raises(TypeError, match="user_env"):
        TrinoSpec(  # type: ignore[call-arg]
            name="warehouse",
            host="trino.example",
            catalog="hive",
        )

    with pytest.raises(TypeError, match="user_env"):
        md.trino(  # type: ignore[call-arg]
            name="warehouse",
            host="trino.example",
            catalog="hive",
        )

    for invalid_user_env in ("", None):
        with pytest.raises(DatasourceFieldInvalidError) as exc_info:
            TrinoSpec(
                name="warehouse",
                host="trino.example",
                catalog="hive",
                user_env=invalid_user_env,  # type: ignore[arg-type]
            )

        assert exc_info.value.expected == "a non-empty environment variable name"
        assert exc_info.value.location.endswith("field 'user_env'")
        assert exc_info.value.repair.help_target.canonical_id == "trino"

    with pytest.raises(DatasourceFieldInvalidError) as exc_info:
        md.trino(
            name="warehouse",
            host="trino.example",
            catalog="hive",
            user_env=None,  # type: ignore[arg-type]
        )

    assert exc_info.value.repair.help_target.canonical_id == "trino"


def test_mysql_postgres_and_clickhouse_required_shapes() -> None:
    assert _ir(MySQLSpec(name="mysql_wh", host="mysql.example", database="mart")).fields == {
        "host": "mysql.example",
        "database": "mart",
    }
    assert _ir(PostgresSpec(name="pg_wh", host="pg.example", database="mart")).fields == {
        "host": "pg.example",
        "database": "mart",
    }
    assert _ir(ClickHouseSpec(name="ch_wh", host="ch.example", secure=True)).fields == {
        "host": "ch.example",
        "secure": True,
    }


def test_missing_required_field_raises_native_type_error() -> None:
    with pytest.raises(TypeError, match="catalog"):
        TrinoSpec(name="warehouse", host="trino.example")  # type: ignore[call-arg]


def test_unknown_field_raises_native_type_error() -> None:
    with pytest.raises(TypeError, match="prot"):
        TrinoSpec(  # type: ignore[call-arg]
            name="warehouse",
            host="trino.example",
            catalog="hive",
            user_env="TRINO_USER",
            prot=8080,
        )


def test_empty_required_string_raises_teaching_error() -> None:
    with pytest.raises(DatasourceFieldInvalidError) as exc_info:
        TrinoSpec(name="warehouse", host="", catalog="hive", user_env="TRINO_USER")

    assert exc_info.value.location == "models/datasources/ entry 'warehouse' field 'host'"
    assert "non-empty string" in str(exc_info.value)


def test_extra_merges_json_safe_passthrough_fields() -> None:
    spec = ClickHouseSpec(
        name="ch_wh",
        host="ch.example",
        extra={"compression": "lz4", "connect_timeout": 10},
    )

    assert _ir(spec).fields == {
        "host": "ch.example",
        "compression": "lz4",
        "connect_timeout": 10,
    }


def test_extra_rejects_plaintext_sensitive_stems() -> None:
    with pytest.raises(DatasourceSecretInPlaintextError) as exc_info:
        TrinoSpec(
            name="warehouse",
            host="trino.example",
            catalog="hive",
            user_env="TRINO_USER",
            extra={"password": "literal-secret"},
        )

    assert exc_info.value.received == "password"
    assert "password_env" in str(exc_info.value)


def test_extra_rejects_non_json_values() -> None:
    with pytest.raises(DatasourceFieldInvalidError) as exc_info:
        TrinoSpec(
            name="warehouse",
            host="trino.example",
            catalog="hive",
            user_env="TRINO_USER",
            extra={"custom_option": object()},
        )

    assert exc_info.value.location.endswith("field 'custom_option'")


def test_datasource_specs_do_not_accept_description() -> None:
    with pytest.raises(TypeError, match="description"):
        DuckDBSpec(name="local", description="Local warehouse")  # type: ignore[call-arg]


def test_datasource_helpers_do_not_accept_description() -> None:
    for helper in (md.duckdb, md.sqlite, md.trino, md.mysql, md.postgres, md.clickhouse):
        assert "description" not in inspect.signature(helper).parameters

    with pytest.raises(TypeError, match="description"):
        md.duckdb(name="warehouse", description="Local warehouse")  # type: ignore[call-arg]


def test_datasource_helper_returns_public_spec_and_ref() -> None:
    spec = md.duckdb(name="warehouse", path="warehouse.duckdb")

    assert isinstance(spec, md.DuckDBSpec)
    assert spec.ref == ms.ref.datasource("warehouse")
    assert spec.ref.path == "warehouse"
    assert spec.ref.name == "warehouse"


def test_spec_ai_context_maps_to_ir() -> None:
    spec = DuckDBSpec(
        name="warehouse",
        ai_context=ms.ai_context(
            business_definition="Local analytical warehouse.",
            guardrails=["Do not use for production freshness checks."],
        ),
    )

    ir = _ir(spec)

    assert ir.ai_context.business_definition == "Local analytical warehouse."
    assert ir.ai_context.guardrails == ("Do not use for production freshness checks.",)


# -- Help surface tests (public convenience functions) --


def test_trino_help_has_signature_without_description() -> None:
    signature = inspect.signature(md.trino)
    result = rendered_help("trino", owner="datasource")

    assert "host" in signature.parameters
    assert "catalog" in signature.parameters
    assert "description" not in signature.parameters
    assert "ai_context" in signature.parameters
    assert "trino" in result
    assert "Signature:" in result


def test_duckdb_help_has_signature_without_description() -> None:
    signature = inspect.signature(md.duckdb)
    result = rendered_help("duckdb", owner="datasource")

    assert "name" in signature.parameters
    assert "path" in signature.parameters
    assert "description" not in signature.parameters
    assert "ai_context" in signature.parameters
    assert "duckdb" in result
    assert "Signature:" in result


def test_sqlite_help_exposes_typed_connection_fields() -> None:
    signature = inspect.signature(md.sqlite)
    result = rendered_help("sqlite", owner="datasource")

    assert {"name", "path", "read_only", "type_map", "ai_context"} <= set(signature.parameters)
    assert "SQLite" in result
    assert "percentile" in result
    assert "strptime" in result
    assert "Signature:" in result


# -- Store persistence tests --


def test_store_writes_convenience_function_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    md.register(
        TrinoSpec(
            name="warehouse",
            host="trino.example",
            catalog="hive",
            user_env="MARIVO_WAREHOUSE_USER",
            auth_env="TRINO_AUTH",
        )
    )

    datasource_file = tmp_path / "models" / "datasources" / "warehouse.py"
    text = datasource_file.read_text(encoding="utf-8")
    assert "md.trino(" in text
    assert "backend_type" not in text
    assert "description" not in text
    assert "user_env='MARIVO_WAREHOUSE_USER'" in text or (
        'user_env="MARIVO_WAREHOUSE_USER"' in text
    )
    assert "auth_env='TRINO_AUTH'" in text or 'auth_env="TRINO_AUTH"' in text
    assert md.describe("warehouse").env_refs == {
        "user": "MARIVO_WAREHOUSE_USER",
        "auth": "TRINO_AUTH",
    }


def test_store_persists_ai_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    md.register(
        DuckDBSpec(
            name="warehouse",
            path=":memory:",
            ai_context=ms.ai_context(
                business_definition="Local analytical warehouse.",
                guardrails=["Use for tests only."],
            ),
        )
    )

    datasource_file = tmp_path / "models" / "datasources" / "warehouse.py"
    text = datasource_file.read_text(encoding="utf-8")
    assert "ai_context=" in text
    assert "business_definition" in text
    assert "description" not in text
    assert md.describe("warehouse").literal_fields == {"path": ":memory:", "read_only": False}


def test_md_list_returns_displayable_datasource_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    md.register(DuckDBSpec(name="warehouse", path=":memory:"))

    result = md.list()

    assert_conforms(result)
    assert len(result) == 1
    assert result.ids() == ["warehouse"]
    assert result.items[0].name == "warehouse"
    assert result[0].backend_type == "duckdb"
    assert [item.name for item in result] == ["warehouse"]
    assert result.show() is None
    assert "warehouse" in capsys.readouterr().out


def test_catalog_list_returns_same_displayable_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    md.register(DuckDBSpec(name="warehouse", path=":memory:"))

    result = md.load().list()

    assert isinstance(result, type(md.list()))
    assert result.ids() == ["warehouse"]


def test_catalog_show_renders_full_datasource_model_without_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TRINO_USER", "warehouse-user")
    monkeypatch.setenv("TRINO_AUTH", "super-secret-token")
    md.register(
        TrinoSpec(
            name="warehouse",
            host="trino.example",
            catalog="hive",
            user_env="TRINO_USER",
            auth_env="TRINO_AUTH",
            ai_context=ms.ai_context(
                business_definition="Curated warehouse tables.",
                guardrails=["Use partition filters."],
            ),
        )
    )

    catalog = md.load()
    rendered = catalog.render()

    assert "DatasourceCatalog datasources=1" in rendered
    assert "warehouse:" in rendered
    assert "backend_type=trino" in rendered
    assert "fields=catalog: hive, host: trino.example" in rendered
    assert "env_refs=auth_env=TRINO_AUTH, user_env=TRINO_USER" in rendered
    assert "business_definition: Curated warehouse tables." in rendered
    assert "guardrails: Use partition filters." in rendered
    assert ".connect(name)" in rendered
    assert "super-secret-token" not in rendered

    assert catalog.show() is None
    out = capsys.readouterr().out
    assert "warehouse" in out
    assert "super-secret-token" not in out


# -- Public spec field visibility --


def test_declared_spec_fields_are_visible_to_dataclasses_help() -> None:
    trino_field_names = {field.name for field in fields(TrinoSpec)}

    assert {"name", "host", "catalog", "port", "user_env", "auth_env", "extra"} <= trino_field_names
    assert "description" not in trino_field_names
    assert "backend_type" not in trino_field_names


@pytest.mark.parametrize(
    ("spec", "target"),
    [
        (DuckDBSpec(name="local"), "target: path=':memory:'"),
        (
            SQLiteSpec(name="app", path="data/app.sqlite", read_only=True),
            "target: path='data/app.sqlite'",
        ),
        (
            TrinoSpec(
                name="warehouse",
                host="trino.example",
                catalog="hive",
                user_env="TRINO_USER",
                port=8443,
                schema="sales",
                http_scheme="https",
            ),
            (
                "target: host='trino.example' | catalog='hive' | port=8443 | "
                "schema='sales' | http_scheme='https'"
            ),
        ),
        (
            MySQLSpec(name="mysql_wh", host="mysql.example", database="sales", port=3307),
            "target: host='mysql.example' | database='sales' | port=3307",
        ),
        (
            PostgresSpec(
                name="pg_wh",
                host="postgres.example",
                database="sales",
                port=5433,
                schema="mart",
            ),
            ("target: host='postgres.example' | database='sales' | port=5433 | schema='mart'"),
        ),
        (
            ClickHouseSpec(
                name="clickhouse_wh",
                host="clickhouse.example",
                port=9440,
                database="sales",
                secure=True,
            ),
            ("target: host='clickhouse.example' | port=9440 | database='sales' | secure=True"),
        ),
    ],
)
def test_datasource_specs_render_only_agent_relevant_state(
    spec: DuckDBSpec | SQLiteSpec | TrinoSpec | MySQLSpec | PostgresSpec | ClickHouseSpec,
    target: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert_conforms(spec)
    capsys.readouterr()

    rendered = spec.render()

    assert "status: valid declaration" in rendered
    assert f"ref: datasource:{spec.name}" in rendered
    assert target in rendered
    assert "ai_context" not in rendered
    assert "session_properties" not in rendered
    assert "settings" not in rendered
    assert "extra" not in rendered
    assert repr(spec) == (
        f"<{type(spec).__name__} name={spec.name} backend={spec.backend_type}; "
        "call .show() to inspect>"
    )

    assert spec.show() is None
    assert capsys.readouterr().out == rendered + "\n"


def test_datasource_spec_render_summarizes_hidden_configuration_and_credentials() -> None:
    spec = TrinoSpec(
        name="warehouse",
        host="trino.example",
        catalog="hive",
        user_env="WAREHOUSE_USER",
        auth_env="WAREHOUSE_AUTH",
        session_properties={f"property_{index}": "x" * 100 for index in range(100)},
        extra={"custom_options": {f"option_{index}": "y" * 100 for index in range(100)}},
    )

    rendered = spec.render()

    assert "credential fields: auth_env | user_env (2 refs)" in rendered
    assert "additional configuration: 2 fields; inspect .fields" in rendered
    assert "WAREHOUSE_USER" not in rendered
    assert "WAREHOUSE_AUTH" not in rendered
    assert "property_99" not in rendered
    assert "option_99" not in rendered
    assert len(rendered.encode("utf-8")) < 1024
    assert len(repr(spec)) < 200
    assert spec.env_refs == {"user": "WAREHOUSE_USER", "auth": "WAREHOUSE_AUTH"}
