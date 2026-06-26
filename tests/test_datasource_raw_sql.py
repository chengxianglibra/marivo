"""Tests for the public datasource raw SQL escape hatch."""

from __future__ import annotations

from pathlib import Path

import ibis
import pytest

import marivo.datasource as md
from marivo.datasource.authoring import _DuckDBSpec


def _register_raw_sql_fixture(project_root: Path) -> None:
    db_path = project_root / "warehouse.duckdb"
    con = ibis.duckdb.connect(db_path)
    con.create_table("orders", {"id": [1, 2], "amount": [10.0, 20.0]})
    con.disconnect()
    md.register(_DuckDBSpec(name="warehouse", path=str(db_path)), project_root=project_root)


def test_raw_sql_requires_reason_before_connecting(tmp_path: Path) -> None:
    _register_raw_sql_fixture(tmp_path)

    with pytest.raises(ValueError, match="reason must be non-empty"):
        md.raw_sql(md.ref("warehouse"), "SELECT 1", reason="", project_root=tmp_path)


def test_raw_sql_rejects_multi_statement_input(tmp_path: Path) -> None:
    _register_raw_sql_fixture(tmp_path)

    with pytest.raises(ValueError, match="single read-only statement"):
        md.raw_sql(
            md.ref("warehouse"),
            "SELECT 1; SELECT 2",
            reason="diagnose duplicate keys",
            project_root=tmp_path,
        )


def test_raw_sql_returns_bounded_escape_hatch_result(tmp_path: Path) -> None:
    _register_raw_sql_fixture(tmp_path)

    result = md.raw_sql(
        md.ref("warehouse"),
        "SELECT id, amount FROM orders ORDER BY id",
        limit=1,
        reason="diagnose order amount sample",
        project_root=tmp_path,
    )

    assert isinstance(result, md.RawSqlResult)
    assert result.datasource == md.ref("warehouse")
    assert result.reason == "diagnose order amount sample"
    assert result.returned_row_count == 1
    assert result.is_truncated is True
    rendered = result.render()
    assert "escape_hatch" in rendered
    assert "diagnose order amount sample" in rendered
