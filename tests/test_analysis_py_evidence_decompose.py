"""decompose wired through commit_result."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pandas as pd
import pytest

import marivo.analysis_py as mv
import marivo.analysis_py.session.attach as session_attach
from marivo.analysis_py.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis_py.lineage import Lineage, LineageStep


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _delta(session) -> DeltaFrame:
    df = pd.DataFrame(
        {
            "country": ["US", "US", "CA"],
            "delta": [12.0, 8.0, -5.0],
        }
    )
    meta = DeltaFrameMeta(
        kind="delta_frame",
        ref="frame_delta",
        session_id=session.id,
        project_root=str(session.project_root),
        produced_by_job="job_compare",
        created_at=datetime(2026, 5, 27, 9, 0, tzinfo=UTC),
        row_count=len(df),
        byte_size=0,
        lineage=Lineage(
            steps=[
                LineageStep(
                    intent="compare",
                    job_ref="job_compare",
                    inputs=["frame_current", "frame_baseline"],
                    params_digest="sha256:compare",
                )
            ]
        ),
        metric_id="sales.revenue",
        source_current_ref="frame_current",
        source_baseline_ref="frame_baseline",
        alignment={"kind": "window_bucket"},
        semantic_kind="segmented",
        semantic_model="sales",
    )
    return DeltaFrame(_df=df, meta=meta)


def test_decompose_populates_surface1_and_decomposition_findings() -> None:
    session = session_attach.create(name="decompose_evidence")

    attribution = session.decompose(
        _delta(session),
        axis=mv.DimensionRef("country"),
    )

    assert attribution.meta.artifact_id is not None
    assert attribution.meta.ref == attribution.meta.artifact_id
    assert attribution.meta.evidence_status == "complete"
    assert isinstance(attribution.meta.recommended_followups, list)

    with sqlite3.connect(session.layout.session_dir / "judgment.db") as conn:
        artifact_rows = conn.execute(
            "SELECT step_type, artifact_type, evidence_status FROM artifacts WHERE artifact_id=?",
            (attribution.meta.artifact_id,),
        ).fetchall()
        finding_types = conn.execute(
            "SELECT finding_type FROM findings WHERE artifact_id=? ORDER BY finding_id",
            (attribution.meta.artifact_id,),
        ).fetchall()
        proposition_types = conn.execute(
            "SELECT proposition_type FROM propositions ORDER BY proposition_id"
        ).fetchall()

    assert artifact_rows == [("decompose", "attribution_frame", "complete")]
    assert finding_types == [("decomposition_item",), ("decomposition_item",)]
    assert proposition_types == [("driver",), ("driver",)]
