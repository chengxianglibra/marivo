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
    monkeypatch.setenv("MY_PWD", "shhh")
    datasource = datasource_store.save_one(
        name="wh",
        backend_type="trino",
        fields={"host": "h", "catalog": "c", "password_env": "MY_PWD"},
    )
    resolved = datasource_backends._effective_kwargs(datasource)
    assert resolved["password"] == "shhh"
    assert resolved["host"] == "h"
    assert "password_env" not in resolved


def test_env_ref_missing_var(project_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MY_PWD", raising=False)
    datasource = datasource_store.save_one(
        name="wh",
        backend_type="trino",
        fields={"host": "h", "catalog": "c", "password_env": "MY_PWD"},
    )
    with pytest.raises(DatasourceEnvVarMissingError) as exc_info:
        datasource_backends._effective_kwargs(datasource)
    assert exc_info.value.details["env_var"] == "MY_PWD"
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
            "user_env": "CH_USER",
            "password_env": "CH_PASSWORD",
            "client_name": "marivo",
            "secure": True,
            "compression": "lz4",
            "settings": {"max_execution_time": 60},
        },
    )
    monkeypatch.setenv("CH_USER", "reader")
    monkeypatch.setenv("CH_PASSWORD", "secret123")

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
