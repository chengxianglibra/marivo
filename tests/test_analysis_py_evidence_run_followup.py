"""session.run_followup typed dispatch + triggered_by_followup lineage."""

from __future__ import annotations

import contextlib
import sqlite3

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


def _session(tmp_path, con):
    bootstrap_sales_project(tmp_path)
    return mv.session.attach.create(
        name="t", backends={"warehouse": lambda: con}, use_datasources=False
    )


def test_run_followup_for_assess_quality_dispatches_to_operator(tmp_path) -> None:
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    session = _session(tmp_path, con)

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

    next_steps = session.knowledge().next_steps(top=10)
    assess_action = next((a for a in next_steps if a.operator == "assess_quality"), None)
    assert assess_action is not None

    with contextlib.suppress(NotImplementedError):
        session.run_followup(assess_action)

    db_path = session.layout.session_dir / "judgment.db"
    with sqlite3.connect(db_path) as conn:
        executed = conn.execute(
            "SELECT followup_id, executed_step_id FROM followups WHERE followup_id=?",
            (assess_action.action_id,),
        ).fetchone()
    assert executed is not None
    assert executed[1] is not None


def test_run_followup_unknown_operator_raises(tmp_path) -> None:
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    session = _session(tmp_path, con)
    bad_action = mv.FollowupAction(
        action_id="act_unknown",
        kind="submit_step",
        operator="not_a_real_operator",
        category="dag_continuation",
        input_refs=[],
        params={},
    )
    with pytest.raises(NotImplementedError):
        session.run_followup(bad_action)


def test_next_steps_filters_executed(tmp_path) -> None:
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    session = _session(tmp_path, con)
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

    actions_before = session.knowledge().next_steps(top=10)
    target = next(a for a in actions_before if a.operator == "assess_quality")
    with contextlib.suppress(NotImplementedError):
        session.run_followup(target)
    actions_after = session.knowledge().next_steps(top=10)
    assert target.action_id not in {a.action_id for a in actions_after}
