"""test intent wired through commit_result."""

from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

import marivo.analysis.session as session_attach
from marivo.analysis.frames.metric import MetricFrame
from tests.shared_fixtures import make_metric_frame


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _metric(session, values: list[float]) -> MetricFrame:
    times = pd.date_range("2026-01-01", periods=len(values), freq="D")
    return make_metric_frame(
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
    session = session_attach.get_or_create(name="test_evidence")
    current = _metric(session, [20.0, 21.0, 22.0, 23.0, 24.0, 25.0])
    baseline = _metric(session, [10.0, 10.2, 10.4, 10.6, 10.8, 11.0])

    result = session.hypothesis_test(current, baseline)

    assert result.meta.artifact_id is not None
    assert result.meta.ref == result.meta.artifact_id
    assert result.meta.evidence_status == "complete"

    with sqlite3.connect(session._layout.session_dir / "judgment.db") as conn:
        artifact_rows = conn.execute(
            "SELECT step_type, artifact_type, evidence_status FROM artifacts WHERE artifact_id=?",
            (result.meta.artifact_id,),
        ).fetchall()
        finding_types = conn.execute(
            "SELECT finding_type FROM findings WHERE artifact_id=?",
            (result.meta.artifact_id,),
        ).fetchall()
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }

    assert artifact_rows == [("hypothesis_test", "hypothesis_test_result", "complete")]
    assert finding_types == [("test_result",)]
    assert "propositions" not in tables
    assert result.evidence_digest is not None
    decision = result.evidence_digest.items[0]
    assert decision.kind == "test_decision"
    assert isinstance(decision.reject_null, bool)
    assert not hasattr(decision, "status")
