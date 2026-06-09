"""Slice-1 end-to-end demo: observe -> compare -> knowledge -> next_steps."""

from __future__ import annotations

import pytest

import marivo.analysis as mv
import marivo.analysis.session.attach as session_attach
from marivo.analysis.intents.compare import compare
from marivo.analysis.intents.observe import observe
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
    session = mv.session.attach.create(
        name="t", backends=sales_backends(con), use_datasources=False
    )

    cur = observe(
        mv.MetricRef("sales.revenue"),
        timescope={"start": "2026-09-01", "end": "2026-09-30"},
        session=session,
    )
    base = observe(
        mv.MetricRef("sales.revenue"),
        timescope={"start": "2026-08-01", "end": "2026-08-31"},
        session=session,
    )
    delta = compare(cur, base, session=session)

    # Surface 1
    assert delta.meta.evidence_status == "complete"
    assert delta.meta.artifact_id is not None
    assert delta.meta.recommended_followups
    assert all(
        a.category in ("dag_continuation", "quality_remediation")
        for a in delta.meta.recommended_followups
    )

    # Surface 2
    knowledge = session.knowledge()
    assert knowledge.evidence_completeness == "complete"
    facts = knowledge.facts(kind="change")
    assert len(facts) == 1
    fact = facts[0]
    assert fact.direction == "increase"
    assert fact.status == "validated"
    assert fact.confidence == 0.9

    # next_steps
    actions = knowledge.next_steps(top=5)
    assert any(a.operator == "assess_quality" for a in actions)


def test_e2e_replay_artifact_id_stability(tmp_path) -> None:
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    session = mv.session.attach.create(
        name="t", backends=sales_backends(con), use_datasources=False
    )
    cur = observe(
        mv.MetricRef("sales.revenue"),
        timescope={"start": "2026-07-01", "end": "2026-07-31"},
        session=session,
    )
    cur2 = observe(
        mv.MetricRef("sales.revenue"),
        timescope={"start": "2026-07-01", "end": "2026-07-31"},
        session=session,
    )
    assert cur.meta.artifact_id == cur2.meta.artifact_id


def test_e2e_observe_populates_quality_and_confidence_scope(tmp_path) -> None:
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    session = mv.session.attach.create(
        name="t", backends=sales_backends(con), use_datasources=False
    )

    cur = observe(
        mv.MetricRef("sales.revenue"),
        timescope={"start": "2026-09-01", "end": "2026-09-30"},
        session=session,
    )

    # meta.quality is populated by pipeline step 4c
    assert cur.meta.quality is not None
    assert cur.meta.quality.sample_size == cur.meta.row_count
    assert cur.meta.quality.null_rate is not None
    assert cur.meta.quality.metric_definition_compatibility == "unknown"

    # meta.confidence_scope is populated by pipeline step 4c
    assert cur.meta.confidence_scope is not None
    assert cur.meta.confidence_scope.metric_ids == ["sales.revenue"]
    assert cur.meta.confidence_scope.window is not None


def test_e2e_compare_populates_quality_and_confidence_scope(tmp_path) -> None:
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    session = mv.session.attach.create(
        name="t", backends=sales_backends(con), use_datasources=False
    )

    cur = observe(
        mv.MetricRef("sales.revenue"),
        timescope={"start": "2026-09-01", "end": "2026-09-30"},
        session=session,
    )
    base = observe(
        mv.MetricRef("sales.revenue"),
        timescope={"start": "2026-08-01", "end": "2026-08-31"},
        session=session,
    )
    delta = compare(cur, base, session=session)

    assert delta.meta.quality is not None
    assert delta.meta.quality.sample_size == delta.meta.row_count
    assert delta.meta.confidence_scope is not None
    assert "sales.revenue" in delta.meta.confidence_scope.metric_ids


def test_e2e_observe_time_series_coverage(tmp_path) -> None:
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    session = mv.session.attach.create(
        name="t", backends=sales_backends(con), use_datasources=False
    )

    series = observe(
        mv.MetricRef("sales.revenue"),
        timescope={"start": "2026-07-01", "end": "2026-07-31"},
        grain="month",
        session=session,
    )

    assert series.meta.quality is not None
    # time_series shape should compute coverage
    if series.meta.semantic_kind == "time_series":
        assert series.meta.quality.coverage is not None
