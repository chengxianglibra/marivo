"""discover wired through commit_result."""

from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

import marivo.analysis.session as session_attach
from marivo.analysis.frames.metric import MetricFrame
from marivo.semantic.catalog import SemanticKind
from marivo.semantic.refs import make_ref
from tests.shared_fixtures import make_metric_frame


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _metric(session, df: pd.DataFrame, *, semantic_kind: str = "time_series") -> MetricFrame:
    return make_metric_frame(
        df,
        metric_id="sales.revenue",
        axes={},
        measure={"name": "revenue"},
        semantic_kind=semantic_kind,
        semantic_model="sales",
        session=session,
    )


def test_discover_point_anomalies_populates_surface1_and_anomaly_findings() -> None:
    session = session_attach.get_or_create(name="discover_evidence")
    frame = _metric(
        session,
        pd.DataFrame({"bucket": ["a", "b", "c", "d"], "value": [-100.0, 0.0, 0.0, 100.0]}),
    )

    candidates = session.discover.point_anomalies(
        frame,
        threshold=1.0,
    )

    assert candidates.meta.artifact_id is not None
    assert candidates.meta.ref == candidates.meta.artifact_id
    assert candidates.meta.evidence_status == "complete"
    assert not hasattr(candidates.meta, "affordances")

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
    session = session_attach.get_or_create(name="discover_other_evidence")
    frame = _metric(
        session,
        pd.DataFrame({"country": ["US", "CA", "JP"], "value": [100.0, 1.0, 1.0]}),
        semantic_kind="segmented",
    )

    candidates = session.discover.interesting_slices(
        frame,
        search_space=[make_ref("country", SemanticKind.DIMENSION)],
        threshold=1.0,
    )

    assert candidates.meta.artifact_id is not None
    assert candidates.meta.evidence_status == "complete"

    with sqlite3.connect(session._layout.session_dir / "judgment.db") as conn:
        finding_count = conn.execute(
            "SELECT count(*) FROM findings WHERE artifact_id=?",
            (candidates.meta.artifact_id,),
        ).fetchone()[0]
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }

    assert finding_count == 0
    assert "propositions" not in tables
