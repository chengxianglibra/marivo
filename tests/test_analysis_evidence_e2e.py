"""Slice-1 end-to-end demo: observe -> compare -> knowledge -> run_followup."""

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

    # next_steps + run_followup
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
