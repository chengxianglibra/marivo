"""observe + compare wired through commit_result."""

from __future__ import annotations

import sqlite3

import ibis
import pytest

import marivo.analysis as mv
import marivo.analysis.session as session_attach
from marivo.analysis.intents.compare import compare
from marivo.analysis.intents.observe import observe
from marivo.semantic.catalog import SemanticKind
from marivo.semantic.refs import make_ref
from tests.conftest import bootstrap_sales_project


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _seed(con) -> None:
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


def _backends(con):
    return {"warehouse": lambda: con}


def test_observe_writes_artifact_metadata(tmp_path) -> None:
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    session = mv.session.get_or_create(name="t", backends=_backends(con), use_datasources=False)

    frame = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-05-01", "end": "2026-05-07"},
        session=session,
    )
    assert frame.meta.artifact_id is not None
    assert frame.meta.evidence_status == "complete"
    assert frame.meta.ref == frame.meta.artifact_id
    db_path = session._layout.session_dir / "judgment.db"
    assert db_path.exists()
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT step_type, evidence_status FROM artifacts").fetchall()
    assert rows == [("observe", "complete")]


def test_compare_seeds_change_proposition_and_emits_followups(tmp_path) -> None:
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    session = mv.session.get_or_create(name="t", backends=_backends(con), use_datasources=False)

    current = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-05-01", "end": "2026-05-07"},
        session=session,
    )
    baseline = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-04-24", "end": "2026-04-30"},
        session=session,
    )
    delta = compare(current, baseline, session=session)

    assert delta.meta.evidence_status == "complete"
    db_path = session._layout.session_dir / "judgment.db"
    with sqlite3.connect(db_path) as conn:
        prop_count = conn.execute(
            "SELECT count(*) FROM propositions WHERE proposition_type='change'"
        ).fetchone()[0]
    assert prop_count == 1


def test_session_knowledge_returns_change_fact(tmp_path) -> None:
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    session = mv.session.get_or_create(name="t", backends=_backends(con), use_datasources=False)
    current = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-05-01", "end": "2026-05-07"},
        session=session,
    )
    baseline = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-04-24", "end": "2026-04-30"},
        session=session,
    )
    compare(current, baseline, session=session)

    knowledge = session.knowledge()
    facts = knowledge.facts(kind="change")
    assert len(facts) == 1
    assert facts[0].direction == "increase"
    assert facts[0].status == "validated"
    next_steps = knowledge.next_steps(top=5)
    assert any(action.operator == "assess_quality" for action in next_steps)
