"""session.run_followup typed dispatch + triggered_by_followup lineage."""

from __future__ import annotations

import contextlib
import json
import sqlite3

import ibis
import pytest

import marivo.analysis as mv
import marivo.analysis.session.attach as session_attach
from marivo.analysis.intents.compare import compare
from marivo.analysis.intents.observe import observe
from tests.conftest import bootstrap_sales_project
from tests.shared_fixtures import seeded_time_series_metric_frame


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
        timescope={"start": "2026-05-01", "end": "2026-05-07"},
        session=session,
    )
    base = observe(
        mv.MetricRef("sales.revenue"),
        timescope={"start": "2026-04-24", "end": "2026-04-30"},
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


def test_run_followup_dispatches_decompose(tmp_path) -> None:
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    session = _session(tmp_path, con)

    cur = observe(
        mv.MetricRef("sales.revenue"),
        timescope={"start": "2026-05-01", "end": "2026-05-07"},
        dimensions=[mv.DimensionRef("region")],
        session=session,
    )
    base = observe(
        mv.MetricRef("sales.revenue"),
        timescope={"start": "2026-04-24", "end": "2026-04-30"},
        dimensions=[mv.DimensionRef("region")],
        session=session,
    )
    delta = compare(cur, base, session=session)
    assert delta.meta.artifact_id is not None
    action = mv.FollowupAction(
        action_id="act_decompose_region",
        kind="submit_step",
        operator="decompose",
        category="dag_continuation",
        input_refs=[delta.meta.artifact_id],
        params={"axis": "region"},
        expected_output_family="attribution_frame",
    )

    result = session.run_followup(action)

    assert result.meta.kind == "attribution_frame"
    assert result.meta.artifact_id is not None
    db_path = session.layout.session_dir / "judgment.db"
    with sqlite3.connect(db_path) as conn:
        triggered = conn.execute(
            "SELECT triggered_by_followup FROM artifacts WHERE artifact_id=?",
            (result.meta.artifact_id,),
        ).fetchone()
    assert triggered is not None
    assert triggered[0] is not None
    assert action.action_id in triggered[0]


def test_run_followup_dispatches_discover(tmp_path) -> None:
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    session = _session(tmp_path, con)

    series = observe(
        mv.MetricRef("sales.revenue"),
        timescope={"start": "2026-04-24", "end": "2026-05-07"},
        grain="day",
        session=session,
    )
    assert series.meta.semantic_kind == "time_series"
    discover_action = next(
        (
            action
            for action in session.knowledge().next_steps(top=10)
            if action.operator == "discover" and action.params.get("objective") == "point_anomalies"
        ),
        None,
    )
    assert discover_action is not None

    result = session.run_followup(discover_action)

    assert result.meta.kind == "candidate_set"
    assert result.meta.artifact_id is not None
    db_path = session.layout.session_dir / "judgment.db"
    with sqlite3.connect(db_path) as conn:
        executed = conn.execute(
            "SELECT executed_step_id FROM followups WHERE followup_id=?",
            (discover_action.action_id,),
        ).fetchone()
    assert executed is not None
    assert executed[0] == result.meta.artifact_id


def test_run_followup_dispatches_forecast(tmp_path) -> None:
    session_attach._reset_process_state()
    session = session_attach.create(name="forecast")
    frame = seeded_time_series_metric_frame(
        session=session,
        n_buckets=10,
        value_pattern="linear",
    )
    action = mv.FollowupAction(
        action_id="act_forecast_default",
        kind="submit_step",
        operator="forecast",
        category="dag_continuation",
        input_refs=[frame.ref],
        params={"horizon": "default"},
        expected_output_family="forecast_frame",
    )

    result = session.run_followup(action)

    assert result.meta.kind == "forecast_frame"
    assert result.meta.horizon == 7
    assert result.meta.artifact_id is not None


def test_run_followup_dispatches_transform(tmp_path) -> None:
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    session = _session(tmp_path, con)
    frame = observe(
        mv.MetricRef("sales.revenue"),
        timescope={"start": "2026-04-24", "end": "2026-05-07"},
        grain="day",
        session=session,
    )
    action = mv.FollowupAction(
        action_id="act_transform_window",
        kind="submit_step",
        operator="transform",
        category="quality_remediation",
        input_refs=[frame.ref],
        params={"op": "window", "window": {"start": "2026-04-25", "end": "2026-05-03"}},
        expected_output_family="metric_frame",
    )

    result = session.run_followup(action)

    assert result.meta.kind == "metric_frame"
    assert result.meta.artifact_id is not None
    assert result.to_pandas()["bucket_start"].astype(str).tolist() == [
        "2026-04-25",
        "2026-05-01",
        "2026-05-02",
    ]


def test_run_followup_retry_evidence_pipeline_no_ops(tmp_path) -> None:
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    session = _session(tmp_path, con)
    series = observe(
        mv.MetricRef("sales.revenue"),
        timescope={"start": "2026-04-24", "end": "2026-05-07"},
        grain="day",
        session=session,
    )
    assert series.meta.artifact_id is not None
    action = mv.FollowupAction(
        action_id="act_retry_evidence",
        kind="adjust_policy",
        operator=None,
        category="quality_remediation",
        input_refs=[series.meta.artifact_id],
        params={"action": "retry_evidence_pipeline"},
    )

    result = session.run_followup(action)

    assert result is None


def test_run_followup_failure_keeps_action_unexecuted(tmp_path) -> None:
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    session = _session(tmp_path, con)
    cur = observe(
        mv.MetricRef("sales.revenue"),
        timescope={"start": "2026-05-01", "end": "2026-05-07"},
        dimensions=[mv.DimensionRef("region")],
        session=session,
    )
    base = observe(
        mv.MetricRef("sales.revenue"),
        timescope={"start": "2026-04-24", "end": "2026-04-30"},
        dimensions=[mv.DimensionRef("region")],
        session=session,
    )
    delta = compare(cur, base, session=session)
    assert delta.meta.artifact_id is not None
    action = mv.FollowupAction(
        action_id="act_bad_decompose_axis",
        kind="submit_step",
        operator="decompose",
        category="dag_continuation",
        input_refs=[delta.meta.artifact_id],
        params={"axis": "missing_region"},
        expected_output_family="attribution_frame",
    )
    db_path = session.layout.session_dir / "judgment.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO followups(followup_id, session_id, source_artifact_id, "
            "category, source_issue_id, operator, payload, executed_step_id, created_at_us) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                action.action_id,
                session.id,
                delta.meta.artifact_id,
                action.category,
                None,
                action.operator,
                json.dumps(action.model_dump(mode="json")),
                None,
                1,
            ),
        )
        conn.commit()

    with pytest.raises(mv.errors.SemanticKindMismatchError):
        session.run_followup(action)

    with sqlite3.connect(db_path) as conn:
        executed = conn.execute(
            "SELECT executed_step_id FROM followups WHERE followup_id=?",
            (action.action_id,),
        ).fetchone()
    assert executed == (None,)


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
        timescope={"start": "2026-05-01", "end": "2026-05-07"},
        session=session,
    )
    base = observe(
        mv.MetricRef("sales.revenue"),
        timescope={"start": "2026-04-24", "end": "2026-04-30"},
        session=session,
    )
    delta = compare(cur, base, session=session)

    actions_before = session.knowledge().next_steps(top=10)
    target = next(a for a in actions_before if a.operator == "assess_quality")
    with contextlib.suppress(NotImplementedError):
        session.run_followup(target)
    actions_after = session.knowledge().next_steps(top=10)
    assert target.action_id not in {a.action_id for a in actions_after}
