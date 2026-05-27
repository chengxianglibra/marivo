"""Slice-1 end-to-end demo: observe -> compare -> knowledge -> run_followup."""

from __future__ import annotations

import ibis
import pytest

import marivo.analysis_py as mv
import marivo.analysis_py.session.attach as session_attach
from marivo.analysis_py.intents.compare import compare
from marivo.analysis_py.intents.observe import observe
from tests.conftest import bootstrap_sales_project


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _seed(con):
    con.raw_sql(
        "CREATE TABLE orders (order_id INTEGER, created_at DATE, "
        "amount DOUBLE, region VARCHAR, user_id INTEGER)"
    )
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2026-05-01', 100.0, 'us', 1),"
        "(2, DATE '2026-05-02', 120.0, 'us', 2),"
        "(3, DATE '2026-04-24', 90.0, 'us', 1),"
        "(4, DATE '2026-04-25', 80.0, 'us', 2)"
    )


def test_e2e_change_fact_walkthrough(tmp_path) -> None:
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    session = mv.session.attach.create(
        name="t", backends={"warehouse": lambda: con}, use_datasources=False
    )

    cur = observe(
        mv.MetricRef("sales.revenue"),
        window={"start": "2026-05-01", "end": "2026-05-07"},
        session=session,
    )
    base = observe(
        mv.MetricRef("sales.revenue"),
        window={"start": "2026-04-24", "end": "2026-04-30"},
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
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    session = mv.session.attach.create(
        name="t", backends={"warehouse": lambda: con}, use_datasources=False
    )
    cur = observe(
        mv.MetricRef("sales.revenue"),
        window={"start": "2026-05-01", "end": "2026-05-07"},
        session=session,
    )
    cur2 = observe(
        mv.MetricRef("sales.revenue"),
        window={"start": "2026-05-01", "end": "2026-05-07"},
        session=session,
    )
    assert cur.meta.artifact_id == cur2.meta.artifact_id
