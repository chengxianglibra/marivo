"""Persisted v3 corruption is rejected at its owning Store or descriptor seam."""

import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest

from marivo.analysis.materialization.contracts import (
    MaterializationIssue,
    canonical_json,
    decode_descriptor,
    descriptor_payload,
    encode_descriptor,
    evidence_for,
)
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.session._lazy_runtime_reads import get_run
from tests.lazy_materialization_fixtures import descriptor
from tests.lazy_runtime_read_fixtures import failure, input_value


def test_failed_run_with_persisted_output_is_rejected_before_projection(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    store.create_session("test", session_ref="session")
    store.admit("session", "failed-key", input_value(), run_ref="failed")
    store.fail("failed", failure())
    # Deliberately bypass SQLite's defenses to exercise the independent Store decoder.
    with sqlite3.connect(store.db_path) as corrupt:
        corrupt.execute("PRAGMA foreign_keys=OFF")
        corrupt.execute("PRAGMA ignore_check_constraints=ON")
        corrupt.execute(
            "UPDATE analysis_action_run_terminals SET output_artifact_ref='stray' WHERE run_ref='failed'"
        )
    with store._read() as conn:
        terminal = conn.execute(
            "SELECT outcome,output_artifact_ref,failure_payload IS NOT NULL "
            "FROM analysis_action_run_terminals WHERE run_ref='failed'"
        ).fetchone()
        assert tuple(terminal) == ("failed", "stray", 1)
    with pytest.raises(IntegrityError, match="contradictory Run terminal"):
        store.run("failed")
    with pytest.raises(IntegrityError, match="contradictory Run terminal"):
        get_run(store, "session", "failed")


@pytest.mark.parametrize("severity", ["warning", "blocking"])
def test_exact_issue_severity_roundtrips_and_legacy_nonempty_shape_is_rejected(
    severity: Literal["warning", "blocking"],
) -> None:
    value = replace(
        descriptor(),
        typed_issues=(
            MaterializationIssue(severity, "test_issue", "expected", "received", "inspect"),
        ),
    )
    restored = decode_descriptor(encode_descriptor(value))
    assert restored.typed_issues == value.typed_issues
    # Findings and typed issues are independent envelope facts.
    assert evidence_for(restored).finding_count == 0
    assert restored.typed_issues
    payload = descriptor_payload(value)
    issues = payload["typed_issues"]
    assert isinstance(issues, list) and len(issues) == 1
    issue = issues[0]
    assert isinstance(issue, dict)
    del issue["severity"]
    assert set(issue) == {"kind", "expected", "received", "repair"}
    with pytest.raises(IntegrityError, match="unknown or missing metadata members"):
        decode_descriptor(canonical_json(payload))
