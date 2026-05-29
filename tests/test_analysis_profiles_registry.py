"""Public API tests for marivo.analysis.datasources registry."""

from __future__ import annotations

from pathlib import Path

import ibis
import pytest

import marivo.analysis as mv
from marivo.analysis.errors import (
    DatasourceFieldInvalidError,
    DatasourceMissingError,
    DatasourcePreviewError,
)
from marivo.preview import PreviewResult


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


def _create_preview_duckdb(path: Path) -> None:
    con = ibis.duckdb.connect(str(path))
    con.con.execute(
        "CREATE TABLE orders ("
        "order_id INT, amount DOUBLE, region TEXT, customer_email TEXT, created_at TIMESTAMP)"
    )
    con.con.execute(
        "INSERT INTO orders VALUES "
        "(1, 100.0, 'US', 'alice@example.com', '2026-01-01'), "
        "(2, 200.0, 'EU', 'bob@example.com', '2026-01-02'), "
        "(3, 300.0, 'US', 'cara@example.com', '2026-01-03')"
    )
    con.disconnect()


def test_preview_table_returns_bounded_result(project_root: Path) -> None:
    db_path = project_root / "warehouse.duckdb"
    _create_preview_duckdb(db_path)
    mv.datasources.register("wh", backend_type="duckdb", path=str(db_path))

    preview = mv.datasources.preview(
        "wh",
        table="orders",
        columns=["order_id", "amount"],
        limit=2,
    )

    assert isinstance(preview, PreviewResult)
    assert preview.kind == "datasource_table"
    assert preview.ref == "wh.orders"
    assert preview.columns == ("order_id", "amount")
    assert preview.types == {"order_id": "int32", "amount": "float64"}
    assert preview.rows == ({"order_id": 1, "amount": 100.0}, {"order_id": 2, "amount": 200.0})
    assert preview.requested_limit == 2
    assert preview.returned_row_count == 2
    assert preview.is_truncated is True


def test_preview_table_supports_structured_filter_and_order(project_root: Path) -> None:
    db_path = project_root / "warehouse.duckdb"
    _create_preview_duckdb(db_path)
    mv.datasources.register("wh", backend_type="duckdb", path=str(db_path))

    preview = mv.datasources.preview(
        "wh",
        table="orders",
        columns=["order_id", "region", "amount"],
        where=[{"column": "region", "op": "=", "value": "US"}],
        order_by=[{"column": "amount", "direction": "desc"}],
        limit=10,
    )

    assert preview.sample_policy.method == "ordered_limit"
    assert preview.sample_policy.filters == ({"column": "region", "op": "=", "value": "US"},)
    assert preview.sample_policy.order_by == ("amount desc",)
    assert preview.rows == (
        {"order_id": 3, "region": "US", "amount": 300.0},
        {"order_id": 1, "region": "US", "amount": 100.0},
    )
    assert preview.is_truncated is False


def test_preview_table_redacts_by_default(project_root: Path) -> None:
    db_path = project_root / "warehouse.duckdb"
    _create_preview_duckdb(db_path)
    mv.datasources.register("wh", backend_type="duckdb", path=str(db_path))

    preview = mv.datasources.preview(
        "wh",
        table="orders",
        columns=["order_id", "customer_email"],
        limit=1,
    )

    assert preview.rows == ({"order_id": 1, "customer_email": "[redacted]"},)
    assert [(warning.kind, warning.columns) for warning in preview.warnings] == [
        ("redacted_column", ("customer_email",))
    ]


def test_preview_table_can_disable_redaction(project_root: Path) -> None:
    db_path = project_root / "warehouse.duckdb"
    _create_preview_duckdb(db_path)
    mv.datasources.register("wh", backend_type="duckdb", path=str(db_path))

    preview = mv.datasources.preview(
        "wh",
        table="orders",
        columns=["customer_email"],
        limit=1,
        redact=False,
    )

    assert preview.rows == ({"customer_email": "alice@example.com"},)
    assert preview.warnings == ()


def test_preview_table_rejects_raw_sql_filter(project_root: Path) -> None:
    db_path = project_root / "warehouse.duckdb"
    _create_preview_duckdb(db_path)
    mv.datasources.register("wh", backend_type="duckdb", path=str(db_path))

    with pytest.raises(DatasourcePreviewError) as exc_info:
        mv.datasources.preview("wh", table="orders", where=["region = 'US'"])  # type: ignore[list-item]

    assert exc_info.value.details["field"] == "where"
    assert "structured preview filter" in str(exc_info.value)


def test_preview_exports_from_analysis_namespace() -> None:
    assert mv.PreviewResult is PreviewResult
