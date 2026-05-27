"""correlate wired through commit_result."""

from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

import marivo.analysis_py as mv
import marivo.analysis_py.session.attach as session_attach
from marivo.analysis_py.frames.metric import MetricFrame


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _metric(session, df: pd.DataFrame, *, metric_id: str) -> MetricFrame:
    return MetricFrame.from_dataframe(
        df,
        metric_id=metric_id,
        axes={},
        measure={"name": metric_id.rsplit(".", 1)[-1]},
        semantic_kind="time_series",
        semantic_model="sales",
        session=session,
    )


def test_correlate_populates_surface1_and_correlation_finding() -> None:
    session = session_attach.create(name="correlate_evidence")
    revenue = _metric(
        session,
        pd.DataFrame(
            {"bucket": ["2026-01-01", "2026-01-02", "2026-01-03"], "value": [1.0, 2.0, 3.0]}
        ),
        metric_id="sales.revenue",
    )
    orders = _metric(
        session,
        pd.DataFrame(
            {"bucket": ["2026-01-01", "2026-01-02", "2026-01-03"], "value": [2.0, 4.0, 6.0]}
        ),
        metric_id="sales.orders",
    )

    result = mv.correlate(revenue, orders, method="pearson", session=session)

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

    assert artifact_rows == [("correlate", "association_result", "complete")]
    assert finding_types == [("correlation_result",)]
    assert proposition_types == [("association",)]
