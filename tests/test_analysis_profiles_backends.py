"""Backend dispatch tests for marivo.analysis.datasources."""

from __future__ import annotations

from pathlib import Path

import pytest

import marivo.analysis as mv
from marivo.analysis.datasources import backends as datasource_backends
from marivo.analysis.datasources import store as datasource_store
from marivo.analysis.errors import (
    DatasourceBackendTypeUnsupportedError,
    DatasourceEnvVarMissingError,
    DatasourceFieldInvalidError,
    DatasourceMissingError,
)
from marivo.semantic.ir import AiContextIR, DatasourceIR, SourceLocation


@pytest.fixture
def project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_build_duckdb_in_memory(project_root: Path) -> None:
    mv.datasources.register("local", backend_type="duckdb", path=":memory:")
    backend = mv.datasources.build_backend("local")
    # ibis DuckDB backend exposes list_tables(); empty for a fresh in-memory db.
    assert backend.list_tables() == []


def test_env_ref_resolution(project_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRINO_PASSWORD", "shhh")
    datasource = datasource_store.save_one(
        name="wh",
        backend_type="trino",
        fields={"host": "h", "catalog": "c", "password_env": "TRINO_PASSWORD"},
    )
    resolved = datasource_backends._effective_kwargs(datasource)
    assert resolved["password"] == "shhh"
    assert resolved["host"] == "h"
    assert "password_env" not in resolved


def test_env_ref_missing_var(project_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRINO_PASSWORD", raising=False)
    datasource = datasource_store.save_one(
        name="wh",
        backend_type="trino",
        fields={"host": "h", "catalog": "c", "password_env": "TRINO_PASSWORD"},
    )
    with pytest.raises(DatasourceEnvVarMissingError) as exc_info:
        datasource_backends._effective_kwargs(datasource)
    assert exc_info.value.details["env_var"] == "TRINO_PASSWORD"
    assert exc_info.value.details["field"] == "password"


def test_unsupported_backend_type(project_root: Path) -> None:
    datasource = DatasourceIR(
        semantic_id="wh",
        name="wh",
        backend_type="wat-backend",
        fields={"path": ":memory:"},
        env_refs={},
        description=None,
        ai_context=AiContextIR(),
        python_symbol="wh",
        location=SourceLocation(file="<test>", line=1),
    )
    with pytest.raises(DatasourceBackendTypeUnsupportedError) as exc_info:
        datasource_backends.build_backend(datasource)
    assert exc_info.value.details["backend_type"] == "wat-backend"


def test_trino_required_field_missing(project_root: Path) -> None:
    datasource = datasource_store.save_one(name="wh", backend_type="trino", fields={"host": "h"})
    with pytest.raises(DatasourceFieldInvalidError) as exc_info:
        datasource_backends.build_backend(datasource)
    assert exc_info.value.details["field"] == "catalog"


def test_trino_session_properties_pass_through(
    project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    class _FakeTrino:
        @staticmethod
        def connect(**kwargs: object) -> object:
            captured.update(kwargs)
            return object()

    class _FakeIbis:
        trino = _FakeTrino()

    monkeypatch.setitem(__import__("sys").modules, "ibis", _FakeIbis())
    datasource = datasource_store.save_one(
        name="wh",
        backend_type="trino",
        fields={
            "host": "h",
            "catalog": "c",
            "session_properties": {"query_max_run_time": "5m"},
        },
    )

    datasource_backends.build_backend(datasource)

    assert captured["session_properties"] == {"query_max_run_time": "5m"}


def test_clickhouse_dispatch_with_host(monkeypatch: pytest.MonkeyPatch, project_root: Path) -> None:
    captured: dict[str, object] = {}

    class _FakeClickhouse:
        @staticmethod
        def connect(**kwargs: object) -> object:
            captured.update(kwargs)
            return object()

    class _FakeIbis:
        clickhouse = _FakeClickhouse()

    monkeypatch.setitem(__import__("sys").modules, "ibis", _FakeIbis())
    datasource = datasource_store.save_one(
        name="ch_ds",
        backend_type="clickhouse",
        fields={"host": "ch.example.com"},
    )

    datasource_backends.build_backend(datasource)

    assert captured["host"] == "ch.example.com"
    assert captured["database"] == "default"
    assert captured["user"] == "default"


def test_clickhouse_required_field_missing(project_root: Path) -> None:
    datasource = datasource_store.save_one(name="ch_ds", backend_type="clickhouse", fields={})
    with pytest.raises(DatasourceFieldInvalidError) as exc_info:
        datasource_backends.build_backend(datasource)
    assert exc_info.value.details["field"] == "host"


def test_clickhouse_optional_fields_pass_through(
    monkeypatch: pytest.MonkeyPatch, project_root: Path
) -> None:
    captured: dict[str, object] = {}

    class _FakeClickhouse:
        @staticmethod
        def connect(**kwargs: object) -> object:
            captured.update(kwargs)
            return object()

    class _FakeIbis:
        clickhouse = _FakeClickhouse()

    monkeypatch.setitem(__import__("sys").modules, "ibis", _FakeIbis())
    datasource = datasource_store.save_one(
        name="ch_ds",
        backend_type="clickhouse",
        fields={
            "host": "ch.example.com",
            "port": 9440,
            "database": "analytics",
            "user_env": "CLICKHOUSE_USER",
            "password_env": "CLICKHOUSE_PASSWORD",
            "client_name": "marivo",
            "secure": True,
            "compression": "lz4",
            "settings": {"max_execution_time": 60},
        },
    )
    monkeypatch.setenv("CLICKHOUSE_USER", "reader")
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "secret123")

    datasource_backends.build_backend(datasource)

    assert captured["host"] == "ch.example.com"
    assert captured["port"] == 9440
    assert captured["database"] == "analytics"
    assert captured["user"] == "reader"
    assert captured["password"] == "secret123"
    assert captured["client_name"] == "marivo"
    assert captured["secure"] is True
    assert captured["compression"] == "lz4"
    assert captured["settings"] == {"max_execution_time": 60}


# --- Task 1: _validate_fdn tests ---


def test_validate_fdn_trino_rejects_short_name() -> None:
    with pytest.raises(DatasourceFieldInvalidError, match="fully-distinguished"):
        datasource_backends._validate_fdn("orders", "trino", "warehouse")


def test_validate_fdn_trino_accepts_fdn() -> None:
    datasource_backends._validate_fdn("hive.sales.orders", "trino", "warehouse")


def test_validate_fdn_mysql_rejects_short_name() -> None:
    with pytest.raises(DatasourceFieldInvalidError, match="fully-distinguished"):
        datasource_backends._validate_fdn("orders", "mysql", "warehouse")


def test_validate_fdn_mysql_accepts_fdn() -> None:
    datasource_backends._validate_fdn("sales_db.orders", "mysql", "warehouse")


def test_validate_fdn_postgres_rejects_short_name() -> None:
    with pytest.raises(DatasourceFieldInvalidError, match="fully-distinguished"):
        datasource_backends._validate_fdn("orders", "postgres", "warehouse")


def test_validate_fdn_postgres_accepts_fdn() -> None:
    datasource_backends._validate_fdn("sales_db.orders", "postgres", "warehouse")


def test_validate_fdn_clickhouse_rejects_short_name() -> None:
    with pytest.raises(DatasourceFieldInvalidError, match="fully-distinguished"):
        datasource_backends._validate_fdn("orders", "clickhouse", "warehouse")


def test_validate_fdn_clickhouse_accepts_fdn() -> None:
    datasource_backends._validate_fdn("analytics_db.orders", "clickhouse", "warehouse")


def test_validate_fdn_duckdb_exempt() -> None:
    datasource_backends._validate_fdn("orders", "duckdb", "local")


def test_validate_fdn_trino_extra_dots_ok() -> None:
    datasource_backends._validate_fdn("hive.sales.raw.orders", "trino", "warehouse")


def test_validate_fdn_empty_name_raises() -> None:
    with pytest.raises(DatasourceFieldInvalidError, match="fully-distinguished"):
        datasource_backends._validate_fdn("", "trino", "warehouse")


def test_validate_fdn_unknown_backend_type_exempt() -> None:
    datasource_backends._validate_fdn("orders", "some_future_engine", "ds")


# --- Task 2: _ValidatingBackend tests ---


def test_validating_backend_table_passes_fdn() -> None:
    class _FakeBackend:
        def table(self, name: str, /) -> str:
            return name

    wrapped = datasource_backends._ValidatingBackend(_FakeBackend(), "trino", "warehouse")
    assert wrapped.table("hive.sales.orders") == "hive.sales.orders"


def test_validating_backend_table_rejects_short_name() -> None:
    class _FakeBackend:
        def table(self, name: str, /) -> str:
            return name

    wrapped = datasource_backends._ValidatingBackend(_FakeBackend(), "trino", "warehouse")
    with pytest.raises(DatasourceFieldInvalidError, match="fully-distinguished"):
        wrapped.table("orders")


def test_validating_backend_sql_delegates() -> None:
    class _FakeBackend:
        def sql(self, query: str, /) -> str:
            return query

    wrapped = datasource_backends._ValidatingBackend(_FakeBackend(), "trino", "warehouse")
    assert wrapped.sql("SELECT 1") == "SELECT 1"


def test_validating_backend_getattr_delegates() -> None:
    class _FakeBackend:
        def list_tables(self) -> list[str]:
            return ["a", "b"]

    wrapped = datasource_backends._ValidatingBackend(_FakeBackend(), "trino", "warehouse")
    assert wrapped.list_tables() == ["a", "b"]


def test_validating_backend_duckdb_no_validation() -> None:
    class _FakeBackend:
        def table(self, name: str, /) -> str:
            return name

    wrapped = datasource_backends._ValidatingBackend(_FakeBackend(), "duckdb", "local")
    assert wrapped.table("orders") == "orders"


# --- Task 3: build_validating_backend tests ---


def test_build_validating_backend_wraps_result(project_root: Path) -> None:
    mv.datasources.register("local", backend_type="duckdb", path=":memory:")
    wrapped = datasource_backends.build_validating_backend("local")
    assert isinstance(wrapped, datasource_backends._ValidatingBackend)
    assert wrapped._backend_type == "duckdb"
    assert wrapped.list_tables() == []


def test_build_validating_backend_unknown_datasource(project_root: Path) -> None:
    with pytest.raises(DatasourceMissingError):
        datasource_backends.build_validating_backend("nonexistent")
