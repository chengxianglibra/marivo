"""End-to-end typed digest persistence and reload."""

from __future__ import annotations

import pytest

import marivo.analysis as mv
import marivo.analysis.session as session_attach
from marivo.analysis.intents.compare import compare
from marivo.analysis.intents.observe import observe
from marivo.semantic.catalog import SemanticKind
from marivo.semantic.refs import make_ref
from tests.conftest import bootstrap_sales_project
from tests.shared_fixtures import connect_sales_orders, sales_backends


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def test_e2e_change_fact_walkthrough(tmp_path) -> None:
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    session = mv.session.get_or_create(
        name="t", backends=sales_backends(con), use_datasources=False
    )

    cur = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-09-01", "end": "2026-09-30"},
        session=session,
    )
    base = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-08-01", "end": "2026-08-31"},
        session=session,
    )
    delta = compare(cur, base, session=session)

    # Surface 1
    assert delta.meta.evidence_status == "complete"
    assert delta.meta.artifact_id is not None

    digest = session.evidence.digest(delta.ref)
    assert digest == delta.evidence_digest
    assert len(digest.items) == 1
    fact = digest.items[0]
    assert fact.kind == "change"
    assert fact.direction == "increase"
    assert not hasattr(fact, "status")
    assert not hasattr(fact, "confidence")
    assert not hasattr(session, "knowledge")

    assert delta.evidence_digest is not None
    loaded = session.get_frame(delta.ref)
    assert loaded.evidence_digest == delta.evidence_digest
    assert loaded.render() == delta.render()


def test_e2e_replay_artifact_id_stability(tmp_path) -> None:
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    session = mv.session.get_or_create(
        name="t", backends=sales_backends(con), use_datasources=False
    )
    cur = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-07-01", "end": "2026-07-31"},
        session=session,
    )
    cur2 = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-07-01", "end": "2026-07-31"},
        session=session,
    )
    assert cur.meta.artifact_id == cur2.meta.artifact_id


def test_e2e_observe_populates_quality_and_analysis_scope(tmp_path) -> None:
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    session = mv.session.get_or_create(
        name="t", backends=sales_backends(con), use_datasources=False
    )

    cur = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-09-01", "end": "2026-09-30"},
        session=session,
    )

    # meta.quality_summary is populated by pipeline step 4c
    assert cur.meta.quality_summary is not None
    assert cur.meta.quality_summary.sample_size == cur.meta.row_count
    assert cur.meta.quality_summary.null_rate is not None
    assert cur.meta.quality_summary.metric_definition_compatibility == "unknown"

    assert cur.meta.analysis_scope is not None
    assert cur.meta.analysis_scope.metric_ids == ("sales.revenue",)
    assert cur.meta.analysis_scope.window is not None


def test_e2e_compare_populates_quality_and_analysis_scope(tmp_path) -> None:
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    session = mv.session.get_or_create(
        name="t", backends=sales_backends(con), use_datasources=False
    )

    cur = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-09-01", "end": "2026-09-30"},
        session=session,
    )
    base = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-08-01", "end": "2026-08-31"},
        session=session,
    )
    delta = compare(cur, base, session=session)

    assert delta.meta.quality_summary is not None
    assert delta.meta.quality_summary.sample_size == delta.meta.row_count
    assert delta.meta.analysis_scope is not None
    assert "sales.revenue" in delta.meta.analysis_scope.metric_ids


def test_e2e_observe_time_series_coverage(tmp_path) -> None:
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    session = mv.session.get_or_create(
        name="t", backends=sales_backends(con), use_datasources=False
    )

    series = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-07-01", "end": "2026-07-31"},
        grain="month",
        session=session,
    )

    assert series.meta.quality_summary is not None
    # time_series shape should compute coverage
    if series.meta.semantic_kind == "time_series":
        assert series.meta.quality_summary.coverage is not None
