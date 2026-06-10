"""discover wired through commit_result."""

from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.analysis.session.attach as session_attach
from marivo.analysis.frames.metric import MetricFrame


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _metric(session, df: pd.DataFrame, *, semantic_kind: str = "time_series") -> MetricFrame:
    return MetricFrame.from_dataframe(
        df,
        metric_id="sales.revenue",
        axes={},
        measure={"name": "revenue"},
        semantic_kind=semantic_kind,
        semantic_model="sales",
        session=session,
    )


def test_discover_point_anomalies_populates_surface1_and_anomaly_findings() -> None:
    session = session_attach.create(name="discover_evidence")
    frame = _metric(
        session,
        pd.DataFrame({"bucket": ["a", "b", "c", "d"], "value": [-100.0, 0.0, 0.0, 100.0]}),
    )

    candidates = session.discover(
        frame,
        objective="point_anomalies",
        strategy="zscore",
        threshold=1.0,
    )

    assert candidates.meta.artifact_id is not None
    assert candidates.meta.ref == candidates.meta.artifact_id
    assert candidates.meta.evidence_status == "complete"
    assert isinstance(candidates.meta.recommended_followups, list)

    with sqlite3.connect(session._layout.session_dir / "judgment.db") as conn:
        artifact_rows = conn.execute(
            "SELECT step_type, artifact_type, evidence_status FROM artifacts WHERE artifact_id=?",
            (candidates.meta.artifact_id,),
        ).fetchall()
        finding_types = conn.execute(
            "SELECT finding_type FROM findings WHERE artifact_id=? ORDER BY finding_id",
            (candidates.meta.artifact_id,),
        ).fetchall()

    assert artifact_rows == [("discover", "candidate_set", "complete")]
    assert finding_types == [("anomaly_candidate",), ("anomaly_candidate",)]


def test_discover_non_anomaly_objective_commits_without_seeding() -> None:
    session = session_attach.create(name="discover_other_evidence")
    frame = _metric(
        session,
        pd.DataFrame({"country": ["US", "CA", "JP"], "value": [100.0, 1.0, 1.0]}),
        semantic_kind="segmented",
    )

    candidates = session.discover(
        frame,
        objective="interesting_slices",
        search_space=[mv.DimensionRef("country")],
        threshold=1.0,
    )

    assert candidates.meta.artifact_id is not None
    assert candidates.meta.evidence_status == "complete"

    with sqlite3.connect(session._layout.session_dir / "judgment.db") as conn:
        finding_count = conn.execute(
            "SELECT count(*) FROM findings WHERE artifact_id=?",
            (candidates.meta.artifact_id,),
        ).fetchone()[0]
        proposition_count = conn.execute("SELECT count(*) FROM propositions").fetchone()[0]

    assert finding_count == 0
    assert proposition_count == 0
