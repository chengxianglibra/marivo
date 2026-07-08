"""Regression tests for base observe projection pruning."""

from __future__ import annotations

import ibis
import pytest

import marivo.analysis as mv
import marivo.analysis.session as session_attach
from marivo.semantic.catalog import SemanticKind
from marivo.semantic.refs import make_ref
from tests.conftest import bootstrap_sales_project


@pytest.fixture(autouse=True)
def _isolated_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TZ", "UTC")
    session_attach._reset_process_state()
    bootstrap_sales_project(tmp_path)
    yield
    session_attach._reset_process_state()


def _seed_orders_with_unused_columns(con) -> None:
    con.raw_sql(
        "CREATE TABLE orders ("
        "order_id INTEGER, created_at DATE, amount DOUBLE, region VARCHAR, "
        "user_id INTEGER, unused_metric DOUBLE, unused_text VARCHAR)"
    )
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2026-07-01', 10.0, 'north', 100, 999.0, 'ignore-a'),"
        "(2, DATE '2026-07-02', 20.0, 'south', 200, 888.0, 'ignore-b'),"
        "(3, DATE '2026-07-02', 30.0, 'north', 300, 777.0, 'ignore-c')"
    )


def _session_with_unused_columns():
    con = ibis.duckdb.connect(":memory:")
    _seed_orders_with_unused_columns(con)
    return mv.session.get_or_create(
        name="projection-pruning",
        question="projection pruning",
        backends={"warehouse": lambda: con},
    )


def test_panel_observe_prunes_unused_source_columns_from_query_sql() -> None:
    session = _session_with_unused_columns()

    frame = session.observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-07-01", "end": "2026-07-03"},
        grain="day",
        dimensions=[make_ref("region", SemanticKind.DIMENSION)],
    )

    job = session.job(frame.meta.produced_by_job)
    sql = job["queries"][0]["sql"]

    assert '"t0"."amount"' in sql
    assert '"t0"."created_at"' in sql
    assert '"t0"."region"' in sql
    assert '"t0"."user_id"' not in sql
    assert '"t0"."unused_metric"' not in sql
    assert '"t0"."unused_text"' not in sql


def test_segmented_observe_keeps_metric_results_after_projection_pruning() -> None:
    session = _session_with_unused_columns()

    frame = session.observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-07-01", "end": "2026-07-03"},
        dimensions=[make_ref("region", SemanticKind.DIMENSION)],
    )

    rows = {
        (row["region"], row["value"])
        for row in frame.to_pandas()[["region", "value"]].to_dict("records")
    }
    assert rows == {("NORTH", 40.0), ("SOUTH", 20.0)}


def test_segmented_observe_keeps_derived_dimension_after_projection_pruning(
    tmp_path,
) -> None:
    semantic_file = tmp_path / "models" / "semantic" / "sales" / "datasets.py"
    semantic_file.write_text(
        semantic_file.read_text()
        + "\n"
        + "@ms.dimension(name='market', entity=orders)\n"
        + "def market(orders):\n"
        + "    return (orders.region == 'north').ifelse('core', 'expansion')\n"
    )
    session = _session_with_unused_columns()

    frame = session.observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-07-01", "end": "2026-07-03"},
        dimensions=[make_ref("sales.orders.market", SemanticKind.DIMENSION)],
    )

    rows = {
        (row["market"], row["value"])
        for row in frame.to_pandas()[["market", "value"]].to_dict("records")
    }
    assert rows == {("core", 40.0), ("expansion", 20.0)}

    job = session.job(frame.meta.produced_by_job)
    sql = job["queries"][0]["sql"]
    assert '"t0"."amount"' in sql
    assert '"t0"."region"' in sql
    assert '"t0"."unused_metric"' not in sql
    assert '"t0"."unused_text"' not in sql
