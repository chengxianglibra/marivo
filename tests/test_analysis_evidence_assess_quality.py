"""Quality reports emit canonical predicate findings before digest construction."""

from __future__ import annotations

import json
import sqlite3

import pytest

import marivo.analysis.session as session_attach
from tests.shared_fixtures import seeded_time_series_metric_frame


@pytest.fixture(autouse=True)
def _reset(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield
    session_attach._reset_process_state()


def test_assess_quality_persists_findings_and_bounded_digest() -> None:
    session = session_attach.get_or_create(name="quality_evidence")
    frame = seeded_time_series_metric_frame(session=session, n_buckets=5)

    quality = session.assess_quality(frame)

    assert quality.evidence_status == "complete"
    assert quality.evidence_digest is not None
    assert {item.kind for item in quality.evidence_digest.items} == {"quality_check"}
    assert all(item.epistemic_kind == "tested" for item in quality.evidence_digest.items)
    assert quality.evidence_digest.quality is not None
    assert quality.evidence_digest.quality.evaluated_check_count == 3
    assert quality.evidence_digest.quality.failed_check_count == 0
    assert "evaluated_check_count=3" in quality.evidence_digest.render()
    with sqlite3.connect(session._layout.session_dir / "judgment.db") as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT value_payload FROM findings WHERE artifact_id = ? ORDER BY finding_id",
            (quality.ref,),
        ).fetchall()
        legacy_tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
            "('propositions', 'assessment_snapshots', 'followups')"
        ).fetchall()
    values = [json.loads(row["value_payload"]) for row in rows]
    assert {value["kind"] for value in values} == {"quality_check"}
    assert {value["check_id"] for value in values} == {
        "row_count",
        "null_ratio:value",
        "time_coverage",
    }
    assert legacy_tables == []
