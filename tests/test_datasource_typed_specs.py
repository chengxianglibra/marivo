"""Contract tests for datasource convenience functions and internal spec classes."""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path

import pytest

import marivo.datasource as md
from marivo.datasource.authoring import (
    _ClickHouseSpec,
    _DuckDBSpec,
    _ir_from_spec,
    _MySQLSpec,
    _PostgresSpec,
    _TrinoSpec,
)
from marivo.datasource.errors import (
    DatasourceFieldInvalidError,
    DatasourceSecretInPlaintextError,
)
from marivo.datasource.ir import DatasourceIR, DatasourceSourceLocation


def _ir(
    spec: _DuckDBSpec | _TrinoSpec | _MySQLSpec | _PostgresSpec | _ClickHouseSpec,
) -> DatasourceIR:
    return _ir_from_spec(
        spec,
        location=DatasourceSourceLocation(file="<test>", line=1),
    )


# -- Internal spec class tests (validation, serialization, IR mapping) --


def test_duckdb_spec_defaults_to_memory_path() -> None:
    spec = _DuckDBSpec(name="local")
    ir = _ir(spec)

    assert spec.backend_type == "duckdb"
    assert ir.backend_type == "duckdb"
    assert ir.fields == {"path": ":memory:", "read_only": False}
    assert ir.env_refs == {}


def test_trino_spec_maps_declared_fields_and_named_secret_env_refs() -> None:
    spec = _TrinoSpec(
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
    assert _ir(_MySQLSpec(name="mysql_wh", host="mysql.example", database="mart")).fields == {
        "host": "mysql.example",
        "database": "mart",
    }
    assert _ir(_PostgresSpec(name="pg_wh", host="pg.example", database="mart")).fields == {
        "host": "pg.example",
        "database": "mart",
    }
    assert _ir(_ClickHouseSpec(name="ch_wh", host="ch.example", secure=True)).fields == {
        "host": "ch.example",
        "secure": True,
    }


def test_missing_required_field_raises_native_type_error() -> None:
    with pytest.raises(TypeError, match="catalog"):
        _TrinoSpec(name="warehouse", host="trino.example")  # type: ignore[call-arg]


def test_unknown_field_raises_native_type_error() -> None:
    with pytest.raises(TypeError, match="prot"):
        _TrinoSpec(  # type: ignore[call-arg]
            name="warehouse",
            host="trino.example",
            catalog="hive",
            prot=8080,
        )


def test_empty_required_string_raises_teaching_error() -> None:
    with pytest.raises(DatasourceFieldInvalidError) as exc_info:
        _TrinoSpec(name="warehouse", host="", catalog="hive")

    assert exc_info.value.details["datasource"] == "warehouse"
    assert exc_info.value.details["field"] == "host"
    assert "non-empty string" in str(exc_info.value)


def test_extra_merges_json_safe_passthrough_fields() -> None:
    spec = _ClickHouseSpec(
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
        _TrinoSpec(
            name="warehouse",
            host="trino.example",
            catalog="hive",
            extra={"password": "literal-secret"},
        )

    assert exc_info.value.details["field"] == "password"
    assert "password_env" in str(exc_info.value)


def test_extra_rejects_non_json_values() -> None:
    with pytest.raises(DatasourceFieldInvalidError) as exc_info:
        _TrinoSpec(
            name="warehouse",
            host="trino.example",
            catalog="hive",
            extra={"custom_option": object()},
        )

    assert exc_info.value.details["field"] == "custom_option"


# -- Help surface tests (public convenience functions) --


def test_trino_help_has_signature_and_description() -> None:
    result = md.help("trino", format="json", print=False)

    assert result["kind"] == "callable"
    assert result["symbol"] == "trino"
    assert "host" in result["signature"]
    assert "catalog" in result["signature"]
    assert result["summary"]


def test_duckdb_help_has_signature_and_description() -> None:
    result = md.help("duckdb", format="json", print=False)

    assert result["kind"] == "callable"
    assert result["symbol"] == "duckdb"
    assert "name" in result["signature"]
    assert "path" in result["signature"]
    assert result["summary"]


# -- Store persistence tests --


def test_store_writes_convenience_function_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    md.register(
        _TrinoSpec(name="warehouse", host="trino.example", catalog="hive", auth_env="TRINO_AUTH")
    )

    datasource_file = tmp_path / "models" / "datasources" / "warehouse.py"
    text = datasource_file.read_text(encoding="utf-8")
    assert "md.trino(" in text
    assert "backend_type" not in text
    assert "auth_env='TRINO_AUTH'" in text or 'auth_env="TRINO_AUTH"' in text
    assert md.describe("warehouse").env_refs == {"auth": "TRINO_AUTH"}


# -- Internal spec field visibility --


def test_declared_spec_fields_are_visible_to_dataclasses_help() -> None:
    trino_field_names = {field.name for field in fields(_TrinoSpec)}

    assert {"name", "host", "catalog", "port", "user_env", "auth_env", "extra"} <= trino_field_names
    assert "backend_type" not in trino_field_names
