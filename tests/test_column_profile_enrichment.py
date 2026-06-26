"""Tests for enriched ColumnProfile fields and coarse type-family detection."""

from __future__ import annotations

from pathlib import Path

import ibis
import pytest

import marivo.datasource as md
from marivo.datasource.authoring import _DuckDBSpec
from marivo.datasource.manage import inspect_columns as _inspect_columns
from marivo.datasource.scan import ColumnProfile, _coarse_type_family


def test_coarse_type_family_orders_timestamp_before_date() -> None:
    assert _coarse_type_family("TIMESTAMP") == "timestamp"
    assert _coarse_type_family("DATETIME") == "timestamp"
    assert _coarse_type_family("DATE") == "date"
    assert _coarse_type_family("BOOLEAN") == "boolean"
    assert _coarse_type_family("BIGINT") == "integer"
    assert _coarse_type_family("DOUBLE") == "numeric"
    assert _coarse_type_family("VARCHAR") == "string"
    assert _coarse_type_family("BLOB") == "unknown"


def test_new_column_profile_fields_default_safe() -> None:
    profile = ColumnProfile(
        name="x",
        data_type="INTEGER",
        nullable=False,
        comment=None,
        null_count=0,
        empty_count=0,
        distinct_count=0,
        top_values=(),
        sample_values=(),
        min_value=None,
        max_value=None,
    )
    assert profile.non_null_count == 0
    assert profile.distinct_ratio is None
    assert profile.top_value_concentration is None
    assert profile.negative_count == 0
    assert profile.zero_count == 0
    assert profile.min_length is None
    assert profile.max_length is None
    assert profile.avg_length is None
    assert profile.type_family == "unknown"


@pytest.fixture
def project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _create_orders_duckdb(path: Path) -> None:
    con = ibis.duckdb.connect(str(path))
    con.raw_sql("CREATE TABLE orders (order_id INTEGER NOT NULL, amount DOUBLE, region VARCHAR)")
    con.raw_sql(
        "INSERT INTO orders VALUES (1, 10.0, 'us'), (2, -5.0, 'us'), (3, 0.0, 'eu'), (4, 7.5, NULL)"
    )
    con.disconnect()


def test_inspect_columns_populates_enriched_fields(project_root: Path) -> None:
    db_path = project_root / "warehouse.duckdb"
    _create_orders_duckdb(db_path)
    md.register(_DuckDBSpec(name="wh", path=str(db_path)))

    inspection = _inspect_columns(
        "wh",
        md.table("orders"),
        scope=md.ScanScope(partition=None, max_rows=100),
    )
    by_name = {p.name: p for p in inspection.profiles}

    amount = by_name["amount"]
    assert amount.type_family == "numeric"
    assert amount.negative_count == 1
    assert amount.zero_count == 1
    assert amount.non_null_count == 4

    order_id = by_name["order_id"]
    assert order_id.type_family == "integer"
    assert order_id.non_null_count == 4
    assert order_id.distinct_count == 4
    assert order_id.distinct_ratio == 1.0

    region = by_name["region"]
    assert region.type_family == "string"
    assert region.null_count == 1
    assert region.non_null_count == 3
    assert region.min_length == 2
    assert region.max_length == 2
    assert region.avg_length == 2.0
    assert region.top_value_concentration is not None
    assert region.top_value_concentration > 0.0
