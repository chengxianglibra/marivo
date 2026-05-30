"""Backend dispatch tests for marivo.analysis.datasources."""

from __future__ import annotations

from pathlib import Path

import pytest

import marivo.analysis as mv
import marivo.datasource as md
from marivo.analysis.datasources import backends as datasource_backends
from marivo.analysis.datasources import secrets as datasource_secrets
from marivo.analysis.datasources import store as datasource_store
from marivo.analysis.errors import (
    DatasourceBackendTypeUnsupportedError,
    DatasourceEnvVarMissingError,
    DatasourceFieldInvalidError,
)
from marivo.semantic.ir import AiContextIR, DatasourceIR, SourceLocation


@pytest.fixture
def project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _spec(name: str, *, backend_type: str, **fields: object) -> md.DatasourceSpec:
    return md.DatasourceSpec(name=name, backend_type=backend_type, **fields)


def test_build_duckdb_in_memory(project_root: Path) -> None:
    mv.datasources.register(_spec("local", backend_type="duckdb", path=":memory:"))
    backend = mv.datasources.build_backend("local")
    # ibis DuckDB backend exposes list_tables(); empty for a fresh in-memory db.
    assert backend.list_tables() == []


def test_env_ref_resolution(project_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRINO_PASSWORD", "shhh")
    datasource = datasource_store.save_one(
        _spec("wh", backend_type="trino", host="h", catalog="c", password_env="TRINO_PASSWORD")
    )
    effective = datasource_backends._effective_kwargs(datasource)
    assert effective.kwargs["password"] == "shhh"
    assert effective.kwargs["host"] == "h"
    assert "password_env" not in effective.kwargs
    assert [secret.name for secret in effective.env_sourced_secrets] == ["TRINO_PASSWORD"]


def test_env_ref_resolution_uses_cache_when_env_is_unset(
    project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TRINO_PASSWORD", raising=False)

    class _CacheProvider:
        def get(self, name: str) -> str | None:
            return "cached-secret" if name == "TRINO_PASSWORD" else None

    monkeypatch.setattr(
        datasource_secrets,
        "default_chain",
        lambda: (_CacheProvider(),),
    )
    datasource = datasource_store.save_one(
        _spec("wh", backend_type="trino", host="h", catalog="c", password_env="TRINO_PASSWORD")
    )

    effective = datasource_backends._effective_kwargs(datasource)

    assert effective.kwargs["password"] == "cached-secret"
    assert effective.env_sourced_secrets == ()


def test_env_ref_missing_var(project_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRINO_PASSWORD", raising=False)
    monkeypatch.setattr(
        datasource_secrets, "default_chain", lambda: (datasource_secrets.EnvProvider(),)
    )
    datasource = datasource_store.save_one(
        _spec("wh", backend_type="trino", host="h", catalog="c", password_env="TRINO_PASSWORD")
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
    datasource = datasource_store.save_one(_spec("wh", backend_type="trino", host="h"))
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
        _spec(
            "wh",
            backend_type="trino",
            host="h",
            catalog="c",
            session_properties={"query_max_run_time": "5m"},
        )
    )

    datasource_backends.build_backend(datasource)

    assert captured["session_properties"] == {"query_max_run_time": "5m"}


def test_trino_catalog_maps_to_ibis_database_and_optional_kwargs_pass_through(
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
    monkeypatch.setenv("TRINO_USER", "reader")
    monkeypatch.setenv("TRINO_AUTH", "token")
    datasource = datasource_store.save_one(
        _spec(
            "wh",
            backend_type="trino",
            host="trino.example",
            catalog="hive",
            user_env="TRINO_USER",
            auth_env="TRINO_AUTH",
            timezone="Asia/Shanghai",
            client_tags="agent, semantic-authoring",
        )
    )

    datasource_backends.build_backend(datasource)

    assert captured["host"] == "trino.example"
    assert captured["database"] == "hive"
    assert "catalog" not in captured
    assert captured["user"] == "reader"
    assert captured["auth"] == "token"
    assert captured["timezone"] == "Asia/Shanghai"
    assert captured["client_tags"] == ["agent", "semantic-authoring"]


def test_mysql_user_is_optional(monkeypatch: pytest.MonkeyPatch, project_root: Path) -> None:
    captured: dict[str, object] = {}

    class _FakeMysql:
        @staticmethod
        def connect(**kwargs: object) -> object:
            captured.update(kwargs)
            return object()

    class _FakeIbis:
        mysql = _FakeMysql()

    monkeypatch.setitem(__import__("sys").modules, "ibis", _FakeIbis())
    datasource = datasource_store.save_one(
        _spec("mysql_wh", backend_type="mysql", host="mysql.example", database="mart", port=3307)
    )

    datasource_backends.build_backend(datasource)

    assert captured == {"host": "mysql.example", "database": "mart", "port": 3307}


def test_postgres_user_is_optional(monkeypatch: pytest.MonkeyPatch, project_root: Path) -> None:
    captured: dict[str, object] = {}

    class _FakePostgres:
        @staticmethod
        def connect(**kwargs: object) -> object:
            captured.update(kwargs)
            return object()

    class _FakeIbis:
        postgres = _FakePostgres()

    monkeypatch.setitem(__import__("sys").modules, "ibis", _FakeIbis())
    datasource = datasource_store.save_one(
        _spec(
            "pg_wh", backend_type="postgres", host="pg.example", database="mart", sslmode="require"
        )
    )

    datasource_backends.build_backend(datasource)

    assert captured == {"host": "pg.example", "database": "mart", "sslmode": "require"}


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
        _spec("ch_ds", backend_type="clickhouse", host="ch.example.com")
    )

    datasource_backends.build_backend(datasource)

    assert captured["host"] == "ch.example.com"
    assert captured["database"] == "default"
    assert captured["user"] == "default"


def test_clickhouse_required_field_missing(project_root: Path) -> None:
    datasource = datasource_store.save_one(_spec("ch_ds", backend_type="clickhouse"))
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
        _spec(
            "ch_ds",
            backend_type="clickhouse",
            host="ch.example.com",
            port=9440,
            database="analytics",
            user_env="CLICKHOUSE_USER",
            password_env="CLICKHOUSE_PASSWORD",
            client_name="marivo",
            secure=True,
            compression="lz4",
            settings={"max_execution_time": 60},
        )
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
