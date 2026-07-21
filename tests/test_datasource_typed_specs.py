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
    assert "secrets.toml" in str(exc_info.value)


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
            prot=8080,
        )


def test_empty_required_string_raises_teaching_error() -> None:
    with pytest.raises(DatasourceFieldInvalidError) as exc_info:
        TrinoSpec(name="warehouse", host="", catalog="hive")

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
    result = md.help_text("trino")

    assert "host" in signature.parameters
    assert "catalog" in signature.parameters
    assert "description" not in signature.parameters
    assert "ai_context" in signature.parameters
    assert "trino" in result
    assert "Signature:" in result


def test_duckdb_help_has_signature_without_description() -> None:
    signature = inspect.signature(md.duckdb)
    result = md.help_text("duckdb")

    assert "name" in signature.parameters
    assert "path" in signature.parameters
    assert "description" not in signature.parameters
    assert "ai_context" in signature.parameters
    assert "duckdb" in result
    assert "Signature:" in result


def test_sqlite_help_exposes_typed_connection_fields() -> None:
    signature = inspect.signature(md.sqlite)
    result = md.help_text("sqlite")

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
        TrinoSpec(name="warehouse", host="trino.example", catalog="hive", auth_env="TRINO_AUTH")
    )

    datasource_file = tmp_path / "models" / "datasources" / "warehouse.py"
    text = datasource_file.read_text(encoding="utf-8")
    assert "md.trino(" in text
    assert "backend_type" not in text
    assert "description" not in text
    assert "auth_env='TRINO_AUTH'" in text or 'auth_env="TRINO_AUTH"' in text
    assert md.describe("warehouse").env_refs == {"auth": "TRINO_AUTH"}


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
    monkeypatch.setenv("TRINO_AUTH", "super-secret-token")
    md.register(
        TrinoSpec(
            name="warehouse",
            host="trino.example",
            catalog="hive",
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
    assert "env_refs=auth_env=TRINO_AUTH" in rendered
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
