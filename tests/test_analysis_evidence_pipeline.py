"""commit_result pipeline: complete, partial, unavailable paths."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from marivo.analysis.evidence.pipeline import (
    CommitInputs,
    CommitParams,
    CommitSemanticAnchors,
    commit_result,
)
from marivo.analysis.evidence.store import open_judgment_store
from marivo.analysis.evidence.types import Subject
from marivo.analysis.frames.attribution import AttributionFrame, AttributionFrameMeta
from marivo.analysis.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.lineage import Lineage


def _now() -> datetime:
    return datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC)


def _metric_frame(
    *, session_id: str, project_root: Path, metric: str = "sales.revenue"
) -> MetricFrame:
    df = pd.DataFrame({"revenue": [100.0]})
    meta = MetricFrameMeta(
        kind="metric_frame",
        ref="placeholder",
        session_id=session_id,
        project_root=str(project_root),
        produced_by_job=None,
        created_at=_now(),
        row_count=1,
        byte_size=0,
        lineage=Lineage(),
        metric_id=metric,
        axes={},
        measure={"name": "revenue"},
        window=None,
        where={},
        semantic_kind="scalar",
        semantic_model="sales",
    )
    return MetricFrame(_df=df, meta=meta)


def _delta_frame(
    *, session_id: str, project_root: Path, source_current: str, source_baseline: str
) -> DeltaFrame:
    df = pd.DataFrame(
        {"current": [120.0], "baseline": [100.0], "delta": [20.0], "pct_change": [0.2]}
    )
    meta = DeltaFrameMeta(
        kind="delta_frame",
        ref="placeholder",
        session_id=session_id,
        project_root=str(project_root),
        produced_by_job=None,
        created_at=_now(),
        row_count=1,
        byte_size=0,
        lineage=Lineage(),
        metric_id="sales.revenue",
        source_current_ref=source_current,
        source_baseline_ref=source_baseline,
        alignment={"kind": "window_bucket"},
        semantic_kind="scalar",
        semantic_model="sales",
    )
    return DeltaFrame(_df=df, meta=meta)


def _attribution_frame(
    *, session_id: str, project_root: Path, scope_delta_ref: str
) -> AttributionFrame:
    df = pd.DataFrame(
        [
            {
                "dimension": "country",
                "country": "us",
                "contribution_value": 12.0,
                "contribution_share": 0.6,
                "direction": "increase",
            },
            {
                "dimension": "country",
                "country": "jp",
                "contribution_value": -4.0,
                "contribution_share": -0.2,
                "direction": "decrease",
            },
        ]
    )
    meta = AttributionFrameMeta(
        kind="attribution_frame",
        ref="placeholder",
        session_id=session_id,
        project_root=str(project_root),
        produced_by_job=None,
        created_at=_now(),
        row_count=len(df),
        byte_size=0,
        lineage=Lineage(),
        metric_ids=["sales.revenue"],
        source_refs=[scope_delta_ref],
        attribution_kind="decomposition",
        driver_field="country",
        value_column="contribution_value",
        contribution_column="contribution_share",
        method="simple_contribution",
        params={"axis": "country"},
        semantic_kind="segmented",
        semantic_model="sales",
    )
    return AttributionFrame(_df=df, meta=meta)


@pytest.fixture
def tmp_session(tmp_path: Path) -> tuple[str, Path, Path, Path]:
    session_id = "sess_1"
    session_dir = tmp_path / ".marivo" / "analysis" / "sessions" / session_id
    frames_dir = session_dir / "frames"
    db_path = session_dir / "judgment.db"
    frames_dir.mkdir(parents=True, exist_ok=True)
    return session_id, session_dir, frames_dir, db_path


def test_commit_observe_writes_artifact_and_metric_value_findings(tmp_session) -> None:
    session_id, session_dir, frames_dir, db_path = tmp_session
    store = open_judgment_store(db_path)
    try:
        frame = _metric_frame(session_id=session_id, project_root=session_dir)
        result = commit_result(
            store=store,
            frames_dir=frames_dir,
            frame=frame,
            step_type="observe",
            inputs=CommitInputs(input_refs=[]),
            params=CommitParams(values={"metric": "sales.revenue", "window": None}),
            semantic_anchors=CommitSemanticAnchors(values={"metric": "sales.revenue@v1"}),
            subject=Subject(metric="sales.revenue", slice={}, analysis_axis="scalar"),
            extractor_family="metric_frame",
        )
    finally:
        store.close()

    assert result.meta.evidence_status == "complete"
    assert result.meta.artifact_id is not None
    assert (frames_dir / result.meta.artifact_id / "data.parquet").exists()
    with sqlite3.connect(db_path) as conn:
        artifacts = conn.execute("SELECT artifact_id, step_type FROM artifacts").fetchall()
        findings = conn.execute("SELECT finding_type FROM findings").fetchall()
    assert len(artifacts) == 1
    assert artifacts[0][1] == "observe"
    assert findings == [("metric_value",)]


def test_commit_compare_seeds_change_proposition(tmp_session) -> None:
    session_id, session_dir, frames_dir, db_path = tmp_session
    store = open_judgment_store(db_path)
    try:
        a = commit_result(
            store=store,
            frames_dir=frames_dir,
            frame=_metric_frame(session_id=session_id, project_root=session_dir),
            step_type="observe",
            inputs=CommitInputs(input_refs=[]),
            params=CommitParams(values={"metric": "sales.revenue", "window": "current"}),
            semantic_anchors=CommitSemanticAnchors(values={"metric": "sales.revenue@v1"}),
            subject=Subject(metric="sales.revenue", slice={}, analysis_axis="scalar"),
            extractor_family="metric_frame",
        )
        b = commit_result(
            store=store,
            frames_dir=frames_dir,
            frame=_metric_frame(session_id=session_id, project_root=session_dir),
            step_type="observe",
            inputs=CommitInputs(input_refs=[]),
            params=CommitParams(values={"metric": "sales.revenue", "window": "baseline"}),
            semantic_anchors=CommitSemanticAnchors(values={"metric": "sales.revenue@v1"}),
            subject=Subject(metric="sales.revenue", slice={}, analysis_axis="scalar"),
            extractor_family="metric_frame",
        )
        delta = _delta_frame(
            session_id=session_id,
            project_root=session_dir,
            source_current=a.meta.artifact_id,  # type: ignore[arg-type]
            source_baseline=b.meta.artifact_id,  # type: ignore[arg-type]
        )
        result = commit_result(
            store=store,
            frames_dir=frames_dir,
            frame=delta,
            step_type="compare",
            inputs=CommitInputs(input_refs=[a.meta.artifact_id, b.meta.artifact_id]),  # type: ignore[list-item]
            params=CommitParams(values={"alignment": "window_bucket"}),
            semantic_anchors=CommitSemanticAnchors(values={"metric": "sales.revenue@v1"}),
            subject=Subject(metric="sales.revenue", slice={}, analysis_axis="change"),
            extractor_family="delta_frame",
            comparison_window={
                "current": {"field": "order_date", "start": "2026-05-01", "end": "2026-05-07"},
                "baseline": {"field": "order_date", "start": "2026-04-24", "end": "2026-04-30"},
            },
            comparison_basis="left_vs_right",
        )
    finally:
        store.close()

    assert result.meta.evidence_status == "complete"
    with sqlite3.connect(db_path) as conn:
        props = conn.execute("SELECT proposition_type, payload FROM propositions").fetchall()
    assert len(props) == 1
    prop_type, payload_json = props[0]
    assert prop_type == "change"
    payload = json.loads(payload_json)
    assert payload["change_kind"] == "scalar_change"
    assert payload["direction_of_interest"] == "increase"
    operators = sorted(a.operator for a in result.meta.recommended_followups)
    assert "assess_quality" in operators


def test_pipeline_dispatches_decomposition_family(tmp_session) -> None:
    session_id, session_dir, frames_dir, db_path = tmp_session
    store = open_judgment_store(db_path)
    try:
        result = commit_result(
            store=store,
            frames_dir=frames_dir,
            frame=_attribution_frame(
                session_id=session_id,
                project_root=session_dir,
                scope_delta_ref="art_delta_parent",
            ),
            step_type="decompose",
            inputs=CommitInputs(input_refs=["art_delta_parent"]),
            params=CommitParams(values={"axis": "country"}),
            semantic_anchors=CommitSemanticAnchors(values={"metric": "sales.revenue@v1"}),
            subject=Subject(metric="sales.revenue", analysis_axis="decomposition"),
            extractor_family="attribution_frame",
        )
    finally:
        store.close()

    assert result.meta.evidence_status == "complete"
    with sqlite3.connect(db_path) as conn:
        proposition_types = {
            row[0]
            for row in conn.execute(
                "SELECT proposition_type FROM propositions WHERE session_id=?",
                (session_id,),
            ).fetchall()
        }
    assert "driver" in proposition_types


def test_commit_partial_when_seeding_fails(tmp_session, monkeypatch) -> None:
    session_id, session_dir, frames_dir, db_path = tmp_session
    store = open_judgment_store(db_path)
    try:
        a = commit_result(
            store=store,
            frames_dir=frames_dir,
            frame=_metric_frame(session_id=session_id, project_root=session_dir),
            step_type="observe",
            inputs=CommitInputs(input_refs=[]),
            params=CommitParams(values={"metric": "sales.revenue", "window": "current"}),
            semantic_anchors=CommitSemanticAnchors(values={"metric": "sales.revenue@v1"}),
            subject=Subject(metric="sales.revenue", slice={}, analysis_axis="scalar"),
            extractor_family="metric_frame",
        )
        b = commit_result(
            store=store,
            frames_dir=frames_dir,
            frame=_metric_frame(session_id=session_id, project_root=session_dir),
            step_type="observe",
            inputs=CommitInputs(input_refs=[]),
            params=CommitParams(values={"metric": "sales.revenue", "window": "baseline"}),
            semantic_anchors=CommitSemanticAnchors(values={"metric": "sales.revenue@v1"}),
            subject=Subject(metric="sales.revenue", slice={}, analysis_axis="scalar"),
            extractor_family="metric_frame",
        )

        from marivo.analysis.evidence import pipeline as pipeline_mod

        def _boom(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("simulated seeding failure")

        monkeypatch.setattr(pipeline_mod, "seed_change_proposition", _boom)

        delta = _delta_frame(
            session_id=session_id,
            project_root=session_dir,
            source_current=a.meta.artifact_id,  # type: ignore[arg-type]
            source_baseline=b.meta.artifact_id,  # type: ignore[arg-type]
        )
        result = commit_result(
            store=store,
            frames_dir=frames_dir,
            frame=delta,
            step_type="compare",
            inputs=CommitInputs(input_refs=[a.meta.artifact_id, b.meta.artifact_id]),  # type: ignore[list-item]
            params=CommitParams(values={"alignment": "window_bucket"}),
            semantic_anchors=CommitSemanticAnchors(values={"metric": "sales.revenue@v1"}),
            subject=Subject(metric="sales.revenue", slice={}, analysis_axis="change"),
            extractor_family="delta_frame",
            comparison_window={
                "current": {"field": "order_date", "start": "2026-05-01", "end": "2026-05-07"},
                "baseline": {"field": "order_date", "start": "2026-04-24", "end": "2026-04-30"},
            },
            comparison_basis="left_vs_right",
        )
    finally:
        store.close()

    assert result.meta.evidence_status == "partial"
    issue_kinds = {issue.kind for issue in result.meta.blocking_issues}
    assert "evidence_partial" in issue_kinds
    with sqlite3.connect(db_path) as conn:
        artifacts = conn.execute("SELECT count(*) FROM artifacts").fetchone()[0]
        findings = conn.execute("SELECT count(*) FROM findings").fetchone()[0]
        propositions = conn.execute("SELECT count(*) FROM propositions").fetchone()[0]
    assert artifacts == 3
    assert findings >= 2
    assert propositions == 0


def test_commit_unavailable_when_store_is_none(tmp_session) -> None:
    session_id, session_dir, frames_dir, _ = tmp_session
    frame = _metric_frame(session_id=session_id, project_root=session_dir)
    result = commit_result(
        store=None,
        frames_dir=frames_dir,
        frame=frame,
        step_type="observe",
        inputs=CommitInputs(input_refs=[]),
        params=CommitParams(values={"metric": "sales.revenue", "window": None}),
        semantic_anchors=CommitSemanticAnchors(values={"metric": "sales.revenue@v1"}),
        subject=Subject(metric="sales.revenue", slice={}, analysis_axis="scalar"),
        extractor_family="metric_frame",
    )
    assert result.meta.evidence_status == "unavailable"
    assert result.meta.artifact_id is not None
    issue_kinds = {issue.kind for issue in result.meta.blocking_issues}
    assert "evidence_store_unavailable" in issue_kinds
    assert result.meta.recommended_followups == []


def test_commit_artifact_id_is_replay_stable(tmp_session) -> None:
    session_id, session_dir, frames_dir, db_path = tmp_session
    store_a = open_judgment_store(db_path)
    try:
        result_a = commit_result(
            store=store_a,
            frames_dir=frames_dir,
            frame=_metric_frame(session_id=session_id, project_root=session_dir),
            step_type="observe",
            inputs=CommitInputs(input_refs=[]),
            params=CommitParams(values={"metric": "sales.revenue", "window": None}),
            semantic_anchors=CommitSemanticAnchors(values={"metric": "sales.revenue@v1"}),
            subject=Subject(metric="sales.revenue", slice={}, analysis_axis="scalar"),
            extractor_family="metric_frame",
        )
    finally:
        store_a.close()

    db_path_2 = db_path.parent.parent / "sess_2" / "judgment.db"
    frames_dir_2 = db_path_2.parent / "frames"
    frames_dir_2.mkdir(parents=True, exist_ok=True)
    store_b = open_judgment_store(db_path_2)
    try:
        store_b.close()
        store_a = open_judgment_store(db_path)
        result_b = commit_result(
            store=store_a,
            frames_dir=frames_dir,
            frame=_metric_frame(session_id=session_id, project_root=session_dir),
            step_type="observe",
            inputs=CommitInputs(input_refs=[]),
            params=CommitParams(values={"metric": "sales.revenue", "window": None}),
            semantic_anchors=CommitSemanticAnchors(values={"metric": "sales.revenue@v1"}),
            subject=Subject(metric="sales.revenue", slice={}, analysis_axis="scalar"),
            extractor_family="metric_frame",
        )
    finally:
        store_a.close()

    assert result_a.meta.artifact_id == result_b.meta.artifact_id
