from __future__ import annotations

import pytest

import marivo.analysis as mv
import marivo.analysis.session as session_attach
import marivo.semantic as ms
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
    revenue = session.catalog.require(ms.ref.metric("sales.revenue")).ref
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
    revenue = session.catalog.require(ms.ref.metric("sales.revenue")).ref
    region = session.catalog.require(ms.ref.dimension("sales.orders.region")).ref
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
    revenue = session.catalog.require(ms.ref.metric("sales.revenue")).ref
    region = session.catalog.require(ms.ref.dimension("sales.orders.region")).ref
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
    from tests.shared_fixtures import rendered_help

    for topic in ("observe",):
        text = rendered_help(topic, owner="analysis")
        assert "analysis_purpose" in text, topic


def test_observe_repeated_call_records_each_invocation_purpose(tmp_path) -> None:
    """Repeated observe with identical inputs but different purposes must keep
    one job record per invocation (issue #38).

    Previously the cache-hit path returned the old frame and recorded no new
    job, so the second purpose was silently dropped and could not be recovered
    through the session job/history API.
    """
    session = _sales_session(tmp_path)
    revenue = session.catalog.require(ms.ref.metric("sales.revenue")).ref

    first = session.observe(
        revenue,
        time_scope={"start": "2026-09-01", "end": "2026-10-01"},
        analysis_purpose="confirm September revenue exceeds August",
    )
    second = session.observe(
        revenue,
        time_scope={"start": "2026-09-01", "end": "2026-10-01"},
        analysis_purpose="audit the same window for reporting",
    )

    # Artifact identity dedups, but the frame keeps its original producer.
    assert second.ref == first.ref
    assert second.meta.analysis_purpose == first.meta.analysis_purpose

    # Every invocation must be recoverable through the job history API.
    purposes = {
        session.job(job.id).get("analysis_purpose")
        for job in session.jobs()
        if session.job(job.id).get("intent") == "observe"
    }
    assert "confirm September revenue exceeds August" in purposes
    assert "audit the same window for reporting" in purposes


def test_attribute_repeated_call_records_reused_invocation_job(tmp_path) -> None:
    """Repeated attribute on the same delta with different purposes must record
    an independent job per invocation, marking the reuse so job and frame
    lineage do not contradict (issue #38).
    """
    session = _sales_session(tmp_path)
    revenue = session.catalog.require(ms.ref.metric("sales.revenue")).ref
    region = session.catalog.require(ms.ref.dimension("sales.orders.region")).ref
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
    delta = session.compare(cur, base)

    first = session.attribute(delta, axes=[region], analysis_purpose="explain revenue change")
    second = session.attribute(delta, axes=[region], analysis_purpose="re-attribute for reporting")

    # Artifact identity dedups; the frame keeps its original producer.
    assert second.ref == first.ref
    assert second.meta.analysis_purpose == first.meta.analysis_purpose
    assert second.meta.produced_by_job == first.meta.produced_by_job

    # Both invocations are recoverable, and the reused one is marked.
    attribute_jobs = [
        session.job(job.id)
        for job in session.jobs()
        if session.job(job.id).get("intent") == "attribute"
    ]
    assert {job.get("analysis_purpose") for job in attribute_jobs} >= {
        "explain revenue change",
        "re-attribute for reporting",
    }
    reused = [job for job in attribute_jobs if job.get("reused_artifact") is True]
    assert len(reused) == 1
    assert reused[0]["analysis_purpose"] == "re-attribute for reporting"
    # The reused job must reference the shared artifact, never a different one.
    assert {job.get("output_frame_ref") for job in attribute_jobs} == {second.ref}


def test_compare_repeated_call_records_reused_invocation_job(tmp_path) -> None:
    """Repeated compare on the same frames with different purposes must record
    an independent job per invocation, marking the reuse so job and frame
    lineage do not contradict (issue #38).
    """
    session = _sales_session(tmp_path)
    revenue = session.catalog.require(ms.ref.metric("sales.revenue")).ref
    region = session.catalog.require(ms.ref.dimension("sales.orders.region")).ref
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

    first = session.compare(cur, base, analysis_purpose="quantify September change")
    second = session.compare(cur, base, analysis_purpose="re-compare for audit")

    assert second.ref == first.ref
    assert second.meta.analysis_purpose == first.meta.analysis_purpose
    assert second.meta.produced_by_job == first.meta.produced_by_job

    compare_jobs = [
        session.job(job.id)
        for job in session.jobs()
        if session.job(job.id).get("intent") == "compare"
    ]
    assert {job.get("analysis_purpose") for job in compare_jobs} >= {
        "quantify September change",
        "re-compare for audit",
    }
    reused = [job for job in compare_jobs if job.get("reused_artifact") is True]
    assert len(reused) == 1
    assert reused[0]["analysis_purpose"] == "re-compare for audit"
    assert {job.get("output_frame_ref") for job in compare_jobs} == {second.ref}


def test_observe_multi_metric_repeated_call_records_each_purpose(tmp_path) -> None:
    """Multi-metric observe (forest path) must also keep one job per invocation
    with its own purpose on artifact reuse (issue #38, P2-1)."""
    import ibis

    from tests.shared_fixtures import (
        bootstrap_multi_metric_sales_project,
        seed_multi_metric_tables,
    )

    bootstrap_multi_metric_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    seed_multi_metric_tables(con)
    session = mv.session.get_or_create(
        name="multi_purpose",
        backends=sales_backends(con),
        use_datasources=False,
    )
    revenue = session.catalog.require(ms.ref.metric("sales.revenue")).ref
    order_count = session.catalog.require(ms.ref.metric("sales.order_count")).ref

    first = session.observe(
        [revenue, order_count],
        time_scope={"start": "2026-07-01", "end": "2026-07-04"},
        grain="day",
        analysis_purpose="compare revenue and orders",
    )
    second = session.observe(
        [revenue, order_count],
        time_scope={"start": "2026-07-01", "end": "2026-07-04"},
        grain="day",
        analysis_purpose="re-audit multi-metric report",
    )

    assert second.ref == first.ref
    observe_purposes = {
        session.job(job.id).get("analysis_purpose")
        for job in session.jobs()
        if session.job(job.id).get("intent") == "observe"
    }
    assert "compare revenue and orders" in observe_purposes
    assert "re-audit multi-metric report" in observe_purposes
    reused = [
        session.job(job.id)
        for job in session.jobs()
        if session.job(job.id).get("intent") == "observe"
        and session.job(job.id).get("reused_artifact") is True
    ]
    assert len(reused) == 1
    assert reused[0]["analysis_purpose"] == "re-audit multi-metric report"
    # Every observe job reports reused_artifact explicitly.
    assert all(
        "reused_artifact" in session.job(job.id)
        for job in session.jobs()
        if session.job(job.id).get("intent") == "observe"
    )
