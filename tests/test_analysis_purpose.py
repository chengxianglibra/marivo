from __future__ import annotations

import pytest

import marivo.analysis as mv
import marivo.analysis.session as session_attach
from tests.conftest import bootstrap_sales_project
from tests.shared_fixtures import (
    connect_sales_orders,
    sales_backends,
    seeded_time_series_metric_frame,
)


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield
    session_attach._reset_process_state()


def _sales_session(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    return mv.session.get_or_create(
        name="analysis_purpose",
        backends=sales_backends(con),
        use_datasources=False,
    )


def test_observe_analysis_purpose_round_trips_through_session_recovery(tmp_path) -> None:
    session = _sales_session(tmp_path)
    revenue = session.catalog.get("metric.sales.revenue").ref
    purpose = "confirm whether September revenue exceeds August"

    frame = session.observe(
        revenue,
        time_scope={"start": "2026-09-01", "end": "2026-10-01"},
        analysis_purpose=purpose,
    )

    assert frame.meta.analysis_purpose == purpose
    assert frame.lineage.steps[-1].analysis_purpose == purpose
    assert frame.lineage.steps[-1].params.get("analysis_purpose") is None
    assert "analysis_purpose" not in frame.lineage.steps[-1].params
    assert session.get_frame(frame.ref).meta.analysis_purpose == purpose

    summaries = session.frame_summaries()
    assert len(summaries) == 1
    component_summaries = session.frame_summaries(kind="component_frame")
    assert len(component_summaries) == 1
    assert component_summaries[0].kind == "component_frame"
    assert summaries[0].ref == frame.ref
    assert summaries[0].analysis_purpose == purpose
    assert purpose in summaries[0].render()
    assert purpose in frame.render()

    job = session.job(frame.meta.produced_by_job or "")
    assert job["analysis_purpose"] == purpose
    assert "analysis_purpose" not in job["params"]


def test_analysis_purpose_propagates_to_core_discover_and_transform(tmp_path) -> None:
    session = _sales_session(tmp_path)
    revenue = session.catalog.get("metric.sales.revenue").ref
    region = session.catalog.get("dimension.sales.orders.region").ref
    cur = session.observe(
        revenue,
        time_scope={"start": "2026-09-01", "end": "2026-10-01"},
        dimensions=[region],
    )
    base = session.observe(
        revenue,
        time_scope={"start": "2026-08-01", "end": "2026-09-01"},
        dimensions=[region],
    )

    delta = session.compare(
        cur, base, analysis_purpose="quantify September revenue change vs August"
    )
    assert delta.meta.analysis_purpose == "quantify September revenue change vs August"
    assert delta.lineage.steps[-1].analysis_purpose == "quantify September revenue change vs August"
    assert session.job(delta.meta.produced_by_job or "")["analysis_purpose"] == (
        "quantify September revenue change vs August"
    )

    candidates = session.discover.driver_axes(
        delta,
        search_space=[region],
        value="delta",
        analysis_purpose="find driver dimensions for revenue change",
    )
    assert candidates.meta.analysis_purpose == "find driver dimensions for revenue change"
    assert (
        candidates.lineage.steps[-1].analysis_purpose == "find driver dimensions for revenue change"
    )

    top_delta = delta.transform.topk(
        by="delta",
        limit=1,
        analysis_purpose="keep top regions by revenue change",
    )
    assert top_delta.meta.analysis_purpose == "keep top regions by revenue change"
    assert top_delta.lineage.steps[-1].analysis_purpose == "keep top regions by revenue change"

    history = seeded_time_series_metric_frame(session=session, n_buckets=8, value_pattern="linear")
    forecast = session.forecast(
        history,
        horizon=2,
        model="naive",
        analysis_purpose="forecast revenue trend for next two days",
    )
    assert forecast.meta.analysis_purpose == "forecast revenue trend for next two days"
    assert forecast.lineage.steps[-1].analysis_purpose == "forecast revenue trend for next two days"


def test_transform_without_analysis_purpose_does_not_inherit_parent_purpose(tmp_path) -> None:
    session = _sales_session(tmp_path)
    revenue = session.catalog.get("metric.sales.revenue").ref
    region = session.catalog.get("dimension.sales.orders.region").ref
    parent = session.observe(
        revenue,
        dimensions=[region],
        analysis_purpose="generate revenue breakdown by region",
    )

    transformed = parent.transform.topk(by="value", limit=1)

    assert parent.meta.analysis_purpose == "generate revenue breakdown by region"
    assert transformed.meta.analysis_purpose is None
    assert transformed.lineage.steps[-1].analysis_purpose is None


def test_help_examples_teach_analysis_purpose() -> None:
    for topic in ("observe",):
        text = mv.help_text(topic)
        assert "analysis_purpose" in text, topic
