"""forecast wired through commit_result."""

from __future__ import annotations

import sqlite3

import pytest

import marivo.analysis.session as session_attach
from tests.shared_fixtures import seeded_time_series_metric_frame


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def test_forecast_populates_surface1_and_forecast_findings() -> None:
    session = session_attach.get_or_create(name="forecast_evidence")
    history = seeded_time_series_metric_frame(
        session=session,
        n_buckets=10,
        value_pattern="linear",
    )

    forecast = session.forecast(history, horizon=3, model="naive")

    assert forecast.meta.artifact_id is not None
    assert forecast.meta.ref == forecast.meta.artifact_id
    assert forecast.meta.evidence_status == "complete"
    assert isinstance(forecast.meta.recommended_followups, list)

    with sqlite3.connect(session._layout.session_dir / "judgment.db") as conn:
        artifact_rows = conn.execute(
            "SELECT step_type, artifact_type, evidence_status FROM artifacts WHERE artifact_id=?",
            (forecast.meta.artifact_id,),
        ).fetchall()
        finding_types = conn.execute(
            "SELECT finding_type FROM findings WHERE artifact_id=? ORDER BY finding_id",
            (forecast.meta.artifact_id,),
        ).fetchall()
        proposition_types = conn.execute(
            "SELECT proposition_type FROM propositions ORDER BY proposition_id"
        ).fetchall()

    assert artifact_rows == [("forecast", "forecast_frame", "complete")]
    assert finding_types == [("forecast_point",), ("forecast_point",), ("forecast_point",)]
    assert proposition_types == [("forecast",), ("forecast",), ("forecast",)]
