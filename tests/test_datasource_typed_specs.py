"""Contract tests for datasource convenience functions and public spec classes."""

from __future__ import annotations

import inspect
from dataclasses import fields
from pathlib import Path
from typing import cast

import pytest

import marivo.datasource as md
import marivo.semantic as ms
from marivo.datasource.authoring import (
    ClickHouseSpec,
    DuckDBSpec,
    MySQLSpec,
    PostgresSpec,
    TrinoSpec,
    _ir_from_spec,
)
from marivo.datasource.errors import (
    DatasourceFieldInvalidError,
    DatasourceSecretInPlaintextError,
)
from marivo.datasource.help import _surface as datasource_surface
from marivo.datasource.ir import DatasourceIR, DatasourceSourceLocation
from marivo.introspection.surface import render as surface_render
from tests.test_agent_result_protocol import assert_conforms


def _help_json(symbol: str) -> dict[str, object]:
    return cast("dict[str, object]", surface_render(datasource_surface(), symbol, "json"))


def _ir(
    spec: DuckDBSpec | TrinoSpec | MySQLSpec | PostgresSpec | ClickHouseSpec,
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

    assert exc_info.value.details["datasource"] == "warehouse"
    assert exc_info.value.details["field"] == "host"
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

    assert exc_info.value.details["field"] == "password"
    assert "password_env" in str(exc_info.value)


def test_extra_rejects_non_json_values() -> None:
    with pytest.raises(DatasourceFieldInvalidError) as exc_info:
        TrinoSpec(
            name="warehouse",
            host="trino.example",
            catalog="hive",
            extra={"custom_option": object()},
        )

    assert exc_info.value.details["field"] == "custom_option"


def test_datasource_specs_do_not_accept_description() -> None:
    with pytest.raises(TypeError, match="description"):
        DuckDBSpec(name="local", description="Local warehouse")  # type: ignore[call-arg]


def test_datasource_helpers_do_not_accept_description() -> None:
    for helper in (md.duckdb, md.trino, md.mysql, md.postgres, md.clickhouse):
        assert "description" not in inspect.signature(helper).parameters

    with pytest.raises(TypeError, match="description"):
        md.duckdb(name="warehouse", description="Local warehouse")  # type: ignore[call-arg]


def test_datasource_helper_returns_public_spec_and_ref() -> None:
    spec = md.duckdb(name="warehouse", path="warehouse.duckdb")

    assert isinstance(spec, md.DuckDBSpec)
    assert spec.ref == md.ref("datasource.warehouse")
    assert spec.ref.id == "datasource.warehouse"
    assert not hasattr(spec.ref, "name")


def test_spec_ai_context_maps_to_ir() -> None:
    spec = DuckDBSpec(
        name="warehouse",
        ai_context=ms.ai_context(
            business_definition="Local analytical warehouse.",
            guardrails=["Do not use for production freshness checks."],
            synonyms=["local wh"],
            examples=["Inspect local fixture tables."],
            instructions="Prefer bounded previews.",
            owner_notes="Analytics platform owns this datasource.",
        ),
    )

    ir = _ir(spec)

    assert ir.ai_context.business_definition == "Local analytical warehouse."
    assert ir.ai_context.guardrails == ("Do not use for production freshness checks.",)
    assert ir.ai_context.synonyms == ("local wh",)
    assert ir.ai_context.examples == ("Inspect local fixture tables.",)
    assert ir.ai_context.instructions == "Prefer bounded previews."
    assert ir.ai_context.owner_notes == "Analytics platform owns this datasource."


# -- Help surface tests (public convenience functions) --


def test_trino_help_has_signature_without_description() -> None:
    result = _help_json("trino")

    assert result["kind"] == "callable"
    assert result["symbol"] == "trino"
    assert "host" in result["signature"]
    assert "catalog" in result["signature"]
    assert "description" not in result["signature"]
    assert "ai_context" in result["signature"]
    assert result["summary"]


def test_duckdb_help_has_signature_without_description() -> None:
    result = _help_json("duckdb")

    assert result["kind"] == "callable"
    assert result["symbol"] == "duckdb"
    assert "name" in result["signature"]
    assert "path" in result["signature"]
    assert "description" not in result["signature"]
    assert "ai_context" in result["signature"]
    assert result["summary"]


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
                synonyms=["wh"],
                examples=["Preview orders before analysis."],
                instructions="Prefer latest dt partitions.",
                owner_notes="Data platform.",
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
    assert "synonyms: wh" in rendered
    assert "examples: Preview orders before analysis." in rendered
    assert "instructions: Prefer latest dt partitions." in rendered
    assert "owner_notes: Data platform." in rendered
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
