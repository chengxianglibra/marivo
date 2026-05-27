"""SessionKnowledge minimal projection: change facts + next_steps."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from marivo.analysis_py.evidence.identity import make_assessment_id, to_microseconds_utc
from marivo.analysis_py.evidence.knowledge import SessionKnowledge, build_session_knowledge
from marivo.analysis_py.evidence.store import open_judgment_store
from marivo.analysis_py.evidence.types import ChangeFact


def _seed_session(db_path: Path, *, evidence_status: str = "complete") -> None:
    """Insert one artifact + finding + change proposition + latest assessment + followup."""
    store = open_judgment_store(db_path)
    try:
        committed = to_microseconds_utc(datetime(2026, 5, 27, 12, tzinfo=UTC))
        with store.transaction() as tx:
            tx.execute(
                "INSERT INTO artifacts(artifact_id, session_id, step_type, artifact_type, "
                "artifact_schema_version, subject_payload, lineage_payload, confidence_scope, "
                "quality_summary, evidence_status, frame_path, committed_at_us) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                ("art_1", "sess_1", "compare", "delta_frame", "v1",
                 json.dumps({"metric": "sales.revenue", "analysis_axis": "change"}),
                 "{}", "{}", "{}", evidence_status, "/tmp/data.parquet", committed),
            )
            tx.execute(
                "INSERT INTO findings(finding_id, session_id, artifact_id, finding_type, "
                "canonical_item_key, subject_axis, subject_payload, payload, committed_at_us) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                ("fnd_1", "sess_1", "art_1", "delta", "value", "change",
                 json.dumps({"metric": "sales.revenue", "analysis_axis": "change"}),
                 json.dumps({
                     "direction": "increase",
                     "magnitude": 20.0,
                     "delta_kind": "scalar_delta",
                 }),
                 committed),
            )
            tx.execute(
                "INSERT INTO propositions(proposition_id, session_id, proposition_type, "
                "origin_kind, derivation_version, subject_key, payload, seed_finding_refs, "
                "created_at_us) VALUES (?,?,?,?,?,?,?,?,?)",
                ("prop_1", "sess_1", "change", "system_seeded", "v1", "subjkey",
                 json.dumps({
                     "change_kind": "scalar_change",
                     "comparison_basis": "left_vs_right",
                 }),
                 json.dumps(["fnd_1"]),
                 committed),
            )
            tx.execute(
                "INSERT INTO assessment_snapshots(snapshot_id, proposition_id, session_id, "
                "status, confidence, confidence_basis, payload, created_at_us, is_latest) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                ("ass_1", "prop_1", "sess_1", "validated", 0.9,
                 "seed_delta_direction_matches", "{}", committed, 1),
            )
            tx.execute(
                "INSERT INTO followups(followup_id, session_id, source_artifact_id, "
                "category, source_issue_id, operator, payload, created_at_us) "
                "VALUES (?,?,?,?,?,?,?,?)",
                ("act_a", "sess_1", "art_1", "dag_continuation", None, "assess_quality",
                 json.dumps({
                     "action_id": "act_a",
                     "kind": "submit_step",
                     "operator": "assess_quality",
                     "input_refs": ["art_1"],
                     "params": {},
                     "category": "dag_continuation",
                     "source_issue_id": None,
                 }),
                 committed),
            )
            tx.execute(
                "INSERT INTO followups(followup_id, session_id, source_artifact_id, "
                "category, source_issue_id, operator, payload, executed_step_id, "
                "created_at_us) VALUES (?,?,?,?,?,?,?,?,?)",
                ("act_b", "sess_1", "art_1", "dag_continuation", None, "discover",
                 json.dumps({
                     "action_id": "act_b",
                     "kind": "submit_step",
                     "operator": "discover",
                     "input_refs": ["art_1"],
                     "params": {"objective": "driver_axes"},
                     "category": "dag_continuation",
                     "source_issue_id": None,
                 }),
                 "step_already_done",
                 committed),
            )
    finally:
        store.close()


def test_evidence_completeness_complete(tmp_path: Path) -> None:
    db_path = tmp_path / "judgment.db"
    _seed_session(db_path, evidence_status="complete")
    knowledge = build_session_knowledge(db_path=db_path, session_id="sess_1")
    assert knowledge.evidence_completeness == "complete"


def test_evidence_completeness_partial(tmp_path: Path) -> None:
    db_path = tmp_path / "judgment.db"
    _seed_session(db_path, evidence_status="partial")
    knowledge = build_session_knowledge(db_path=db_path, session_id="sess_1")
    assert knowledge.evidence_completeness == "partial"


def test_facts_change_returns_change_fact(tmp_path: Path) -> None:
    db_path = tmp_path / "judgment.db"
    _seed_session(db_path)
    knowledge = build_session_knowledge(db_path=db_path, session_id="sess_1")
    facts = knowledge.facts(kind="change")
    assert len(facts) == 1
    fact = facts[0]
    assert isinstance(fact, ChangeFact)
    assert fact.id == "prop_1"
    assert fact.status == "validated"
    assert fact.confidence == 0.9
    assert fact.direction == "increase"
    assert fact.magnitude == 20.0
    assert fact.latest_assessment_id == "ass_1"


def test_next_steps_filters_executed_actions(tmp_path: Path) -> None:
    db_path = tmp_path / "judgment.db"
    _seed_session(db_path)
    knowledge = build_session_knowledge(db_path=db_path, session_id="sess_1")
    actions = knowledge.next_steps(top=5)
    operators = [a.operator for a in actions]
    assert operators == ["assess_quality"]


def test_next_steps_top_caps_results(tmp_path: Path) -> None:
    db_path = tmp_path / "judgment.db"
    _seed_session(db_path)
    knowledge = build_session_knowledge(db_path=db_path, session_id="sess_1")
    actions = knowledge.next_steps(top=0)
    assert actions == []
