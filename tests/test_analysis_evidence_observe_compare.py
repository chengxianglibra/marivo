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


def test_observe_emits_persisted_observation_digest(tmp_path) -> None:
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    session = mv.session.get_or_create(name="t", backends=_backends(con), use_datasources=False)

    frame = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-05-01", "end": "2026-05-07"},
        session=session,
        analysis_purpose="check current revenue level",
    )

    db_path = session._layout.session_dir / "judgment.db"
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT canonical_item_key FROM findings WHERE finding_type='observation'"
        ).fetchall()
    assert rows == [("digest",)]

    digest = session.evidence.digest(frame.ref)
    assert digest == frame.evidence_digest
    assert len(digest.items) == 1
    observation = digest.items[0]
    assert observation.kind == "observation"
    assert observation.subject.metric == "sales.revenue"
    assert observation.value.shape == "scalar"
    assert observation.value.value == 220.0


def test_observe_segmented_emits_bounded_digest(tmp_path) -> None:
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    session = mv.session.get_or_create(name="t", backends=_backends(con), use_datasources=False)

    frame = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-05-01", "end": "2026-05-07"},
        dimensions=[make_ref("region", SemanticKind.DIMENSION)],
        session=session,
    )
    assert frame.meta.additivity == "additive"

    db_path = session._layout.session_dir / "judgment.db"
    with sqlite3.connect(db_path) as conn:
        counts = dict(
            conn.execute("SELECT finding_type, count(*) FROM findings GROUP BY finding_type")
        )
    assert counts.get("metric_value") == 1
    assert counts.get("observation") == 1

    digest = session.evidence.digest(frame.ref)
    observation = digest.items[0]
    assert observation.kind == "observation"
    assert observation.value.shape == "segmented"
    assert observation.value.segment_count == 1
    assert observation.value.top_segments[0].keys == {"region": "US"}
    assert observation.value.top_segments[0].value == 220.0
    assert observation.value.total_value == 220.0
    assert observation.value.top_segments[0].share == 1.0
    assert "top_segments=region=US:value=220,share=1" in digest.render()


def test_compare_emits_change_without_judgment_tables(tmp_path) -> None:
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
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "propositions" not in tables
    assert "followups" not in tables
    assert delta.evidence_digest is not None
    assert delta.evidence_digest.items[0].kind == "change"


def test_session_direct_digest_returns_computed_change_without_status(tmp_path) -> None:
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

    digest = session.evidence.digest(delta.ref)
    change = digest.items[0]
    assert change.kind == "change"
    assert change.direction == "increase"
    assert not hasattr(change, "status")
    assert not hasattr(session, "knowledge")
