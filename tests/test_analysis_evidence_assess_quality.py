"""assess_quality wired through commit_result."""

from __future__ import annotations

import sqlite3

import pytest

import marivo.analysis.session.attach as session_attach
from tests.shared_fixtures import seeded_time_series_metric_frame


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def test_assess_quality_populates_surface1_without_findings_or_followups() -> None:
    session = session_attach.create(name="quality_evidence")
    frame = seeded_time_series_metric_frame(session=session, n_buckets=5)

    quality = session.assess_quality(frame)

    assert quality.meta.artifact_id is not None
    assert quality.meta.ref == quality.meta.artifact_id
    assert quality.meta.evidence_status == "complete"
    assert quality.meta.recommended_followups == []

    with sqlite3.connect(session._layout.session_dir / "judgment.db") as conn:
        artifact_rows = conn.execute(
            "SELECT step_type, artifact_type, evidence_status FROM artifacts WHERE artifact_id=?",
            (quality.meta.artifact_id,),
        ).fetchall()
        finding_count = conn.execute(
            "SELECT count(*) FROM findings WHERE artifact_id=?",
            (quality.meta.artifact_id,),
        ).fetchone()[0]
        proposition_count = conn.execute("SELECT count(*) FROM propositions").fetchone()[0]

    assert artifact_rows == [("assess_quality", "quality_report", "complete")]
    assert finding_count == 0
    assert proposition_count == 0
