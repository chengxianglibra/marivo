"""test intent wired through commit_result."""

from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

import marivo.analysis.session.attach as session_attach
from marivo.analysis.frames.metric import MetricFrame


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _metric(session, values: list[float]) -> MetricFrame:
    times = pd.date_range("2026-01-01", periods=len(values), freq="D")
    return MetricFrame.from_dataframe(
        pd.DataFrame({"time": times, "value": values}),
        metric_id="sales.revenue",
        axes={"time": {"role": "time", "field": "time", "grain": "day"}},
        measure={"field": "value", "aggregation": "sum"},
        semantic_kind="time_series",
        semantic_model="sales",
        window={
            "start": "2026-01-01",
            "end": "2026-01-06",
            "grain": "day",
            "time_dimension": "time",
        },
        session=session,
    )


def test_hypothesis_test_populates_surface1_and_test_finding() -> None:
    session = session_attach.create(name="test_evidence")
    current = _metric(session, [20.0, 21.0, 22.0, 23.0, 24.0, 25.0])
    baseline = _metric(session, [10.0, 10.2, 10.4, 10.6, 10.8, 11.0])

    result = session.hypothesis_test(current, baseline)

    assert result.meta.artifact_id is not None
    assert result.meta.ref == result.meta.artifact_id
    assert result.meta.evidence_status == "complete"
    assert isinstance(result.meta.recommended_followups, list)

    with sqlite3.connect(session.layout.session_dir / "judgment.db") as conn:
        artifact_rows = conn.execute(
            "SELECT step_type, artifact_type, evidence_status FROM artifacts WHERE artifact_id=?",
            (result.meta.artifact_id,),
        ).fetchall()
        finding_types = conn.execute(
            "SELECT finding_type FROM findings WHERE artifact_id=?",
            (result.meta.artifact_id,),
        ).fetchall()
        proposition_types = conn.execute("SELECT proposition_type FROM propositions").fetchall()

    assert artifact_rows == [("test", "hypothesis_test_result", "complete")]
    assert finding_types == [("test_result",)]
    assert proposition_types == [("tested_hypothesis",)]
