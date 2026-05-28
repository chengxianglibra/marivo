"""Public API tests for marivo.analysis.datasources registry."""

from __future__ import annotations

from pathlib import Path

import pytest

import marivo.analysis as mv
from marivo.analysis.errors import DatasourceFieldInvalidError, DatasourceMissingError


@pytest.fixture
def project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_set_returns_summary(project_root: Path) -> None:
    summary = mv.datasources.register("wh", backend_type="duckdb", path=":memory:")
    assert summary.name == "wh"
    assert summary.backend_type == "duckdb"
    assert (project_root / ".marivo" / "datasource" / "wh.py").is_file()


def test_set_rejects_model_qualified_name(project_root: Path) -> None:
    with pytest.raises(DatasourceFieldInvalidError) as exc_info:
        mv.datasources.register("sales.warehouse", backend_type="duckdb", path=":memory:")
    assert exc_info.value.details["field"] == "<name>"
    assert "global datasource name" in str(exc_info.value)


def test_list_returns_sorted_summaries(project_root: Path) -> None:
    mv.datasources.register("b", backend_type="duckdb", path=":memory:")
    mv.datasources.register("a", backend_type="duckdb", path=":memory:")
    names = [p.name for p in mv.datasources.all()]
    assert names == ["a", "b"]


def test_describe_redacts_secrets(project_root: Path) -> None:
    mv.datasources.register(
        "wh",
        backend_type="trino",
        host="trino.example",
        port=8080,
        catalog="hive",
        password_env="WAREHOUSE_PWD",
    )
    desc = mv.datasources.describe("wh")
    assert desc.literal_fields == {"host": "trino.example", "port": 8080, "catalog": "hive"}
    assert desc.env_refs == {"password": "WAREHOUSE_PWD"}


def test_describe_missing_raises_with_hint(project_root: Path) -> None:
    with pytest.raises(DatasourceMissingError) as exc_info:
        mv.datasources.describe("nope")
    rendered = str(exc_info.value)
    assert "mv.datasources.register" in rendered
    assert "'nope'" in rendered


def test_remove_returns_bool(project_root: Path) -> None:
    mv.datasources.register("wh", backend_type="duckdb", path=":memory:")
    assert mv.datasources.remove("wh") is True
    assert mv.datasources.remove("wh") is False
