"""Tests for Phase 4h-1: soft invalidation and tombstone-first baseline."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from app.evidence_engine.canonical_pipeline_runtime import run_canonical_downstream
from app.evidence_engine.invalidation import (
    INVALIDATION_SCHEMA_VERSION,
    InvalidationResult,
    soft_invalidate_finding,
)
from app.storage.evidence_repositories import (
    ActionProposalRepository,
    AssessmentRepository,
    EvidenceGapRepository,
    FindingRepository,
    InferenceRecordRepository,
    PropositionRepository,
)
from app.storage.sqlite_metadata import SQLiteMetadataStore

# ---------------------------------------------------------------------------
# Store factory and shared fixtures
# ---------------------------------------------------------------------------

_SESSION = "sess_si_001"
_ARTIFACT_ID = "art_si_001"
_FINDING_ID = "fnd_si_001"
_PROPOSITION_ID = "prop_si_001"

_LEFT_WIN = {"kind": "range", "start": "2024-01-01", "end": "2024-01-07"}
_RIGHT_WIN = {"kind": "range", "start": "2024-01-08", "end": "2024-01-14"}

_COMPARE_ARTIFACT_CONTENT: dict[str, Any] = {
    "comparison_type": "scalar_delta",
    "metric": "dau",
    "direction": "increase",
    "resolved_input_summary": {
        "left_scope": {},
        "left_time_scope": _LEFT_WIN,
        "right_time_scope": _RIGHT_WIN,
    },
}

_DELTA_PAYLOAD: dict[str, Any] = {
    "delta_kind": "scalar_delta",
    "left_ref": {
        "artifact_id": _ARTIFACT_ID,
        "item_ref": {"collection": "result", "index": None, "key": None},
    },
    "right_ref": {
        "artifact_id": _ARTIFACT_ID,
        "item_ref": {"collection": "result", "index": None, "key": None},
    },
    "left_value": 900.0,
    "right_value": 1000.0,
    "absolute_delta": 100.0,
    "relative_delta": 0.111,
    "direction": "increase",
    "presence": "both",
    "unit": "users",
}


def _make_store() -> SQLiteMetadataStore:
    tmp = tempfile.mkdtemp()
    store = SQLiteMetadataStore(Path(tmp) / "meta.sqlite")
    store.initialize()
    return store


def _setup_session_artifact_finding(store: SQLiteMetadataStore) -> None:
    store.execute(
        "INSERT INTO sessions "
        "(session_id, goal, constraints_json, budget_json, policy_json, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [_SESSION, "test soft invalidation", "{}", "{}", "{}", "open"],
    )
    store.execute(
        "INSERT INTO artifacts "
        "(artifact_id, session_id, step_id, artifact_type, name, content_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            _ARTIFACT_ID,
            _SESSION,
            "step_si_001",
            "compare_artifact",
            _ARTIFACT_ID,
            json.dumps(_COMPARE_ARTIFACT_CONTENT),
        ],
    )
    subject = {
        "metric": "dau",
        "entity": None,
        "slice": {},
        "grain": "day",
        "analysis_axis": "scalar",
    }
    step_ref = {
        "session_id": _SESSION,
        "step_id": "step_si_001",
        "step_type": "compare",
    }
    quality = {
        "data_complete": True,
        "sample_size": None,
        "row_count": 1,
        "null_rate": None,
        "quality_status": "ready",
        "quality_warnings": [],
    }
    provenance = {
        "source_step_type": "compare",
        "extractor_name": "compare_extractor",
        "extractor_version": "v1",
        "artifact_schema_version": "v1",
        "canonical_item_key": _FINDING_ID,
        "artifact_item_ref": {"collection": "result", "index": None, "key": None},
        "projection_ref": None,
    }
    FindingRepository(store).create(
        {
            "finding_id": _FINDING_ID,
            "session_id": _SESSION,
            "artifact_id": _ARTIFACT_ID,
            "step_ref_json": json.dumps(step_ref),
            "finding_type": "delta",
            "canonical_item_key": _FINDING_ID,
            "subject_json": json.dumps(subject),
            "observed_window_json": json.dumps(
                {"kind": "range", "start": "2024-01-08", "end": "2024-01-14"}
            ),
            "quality_json": json.dumps(quality),
            "provenance_json": json.dumps(provenance),
            "payload_json": json.dumps(_DELTA_PAYLOAD),
            "schema_version": "v1",
        }
    )


def _make_repos(store: SQLiteMetadataStore) -> dict[str, Any]:
    return {
        "finding_repo": FindingRepository(store),
        "proposition_repo": PropositionRepository(store),
        "assessment_repo": AssessmentRepository(store),
        "gap_repo": EvidenceGapRepository(store),
        "inference_record_repo": InferenceRecordRepository(store),
        "proposal_repo": ActionProposalRepository(store),
    }


# ---------------------------------------------------------------------------
# FindingRepository.soft_invalidate + is_invalidated tests
# ---------------------------------------------------------------------------


class TestSoftInvalidateFindingRepo(unittest.TestCase):
    """FindingRepository.soft_invalidate marks a finding without deleting it."""

    def setUp(self) -> None:
        self.store = _make_store()
        _setup_session_artifact_finding(self.store)
        self.finding_repo = FindingRepository(self.store)

    def test_soft_invalidate_sets_invalidated_at(self) -> None:
        self.finding_repo.soft_invalidate(_FINDING_ID, "test_reason")
        row = self.finding_repo.get(_FINDING_ID)
        self.assertIsNotNone(row)
        self.assertIsNotNone(row["invalidated_at"])

    def test_soft_invalidate_sets_reason(self) -> None:
        self.finding_repo.soft_invalidate(_FINDING_ID, "upstream_retracted")
        row = self.finding_repo.get(_FINDING_ID)
        self.assertEqual(row["invalidation_reason"], "upstream_retracted")

    def test_is_invalidated_true_after_invalidation(self) -> None:
        self.finding_repo.soft_invalidate(_FINDING_ID, "test_reason")
        self.assertTrue(self.finding_repo.is_invalidated(_FINDING_ID))

    def test_is_invalidated_false_before_invalidation(self) -> None:
        self.assertFalse(self.finding_repo.is_invalidated(_FINDING_ID))

    def test_is_invalidated_false_for_unknown_id(self) -> None:
        self.assertFalse(self.finding_repo.is_invalidated("fnd_nonexistent"))

    def test_soft_invalidate_raises_for_unknown_id(self) -> None:
        with self.assertRaises(ValueError):
            self.finding_repo.soft_invalidate("fnd_nonexistent", "reason")

    def test_no_hard_delete_finding_still_readable(self) -> None:
        """After soft-invalidation, finding row must still be readable (tombstone-first)."""
        self.finding_repo.soft_invalidate(_FINDING_ID, "test_reason")
        row = self.finding_repo.get(_FINDING_ID)
        self.assertIsNotNone(row, "finding must remain readable after soft-invalidation")
        self.assertEqual(row["finding_id"], _FINDING_ID)


# ---------------------------------------------------------------------------
# PropositionRepository.soft_invalidate + is_invalidated tests
# ---------------------------------------------------------------------------


class TestSoftInvalidatePropositionRepo(unittest.TestCase):
    """PropositionRepository.soft_invalidate marks a proposition without deleting it."""

    def setUp(self) -> None:
        self.store = _make_store()
        _setup_session_artifact_finding(self.store)
        self.repos = _make_repos(self.store)
        # Run downstream to create a proposition.
        run_canonical_downstream(
            session_id=_SESSION,
            trigger_finding_ids=[_FINDING_ID],
            metadata_store=self.store,
            **self.repos,
        )
        propositions = self.repos["proposition_repo"].list_by_session(_SESSION)
        self.assertGreater(len(propositions), 0)
        self.proposition_id = propositions[0]["proposition_id"]

    def test_soft_invalidate_sets_invalidated_at(self) -> None:
        self.repos["proposition_repo"].soft_invalidate(self.proposition_id, "stale")
        row = self.repos["proposition_repo"].get(self.proposition_id)
        self.assertIsNotNone(row["invalidated_at"])

    def test_is_invalidated_true_after_invalidation(self) -> None:
        self.repos["proposition_repo"].soft_invalidate(self.proposition_id, "stale")
        self.assertTrue(self.repos["proposition_repo"].is_invalidated(self.proposition_id))

    def test_is_invalidated_false_before_invalidation(self) -> None:
        self.assertFalse(self.repos["proposition_repo"].is_invalidated(self.proposition_id))

    def test_soft_invalidate_raises_for_unknown_id(self) -> None:
        with self.assertRaises(ValueError):
            self.repos["proposition_repo"].soft_invalidate("prop_nonexistent", "reason")

    def test_no_hard_delete_proposition_still_readable(self) -> None:
        """After soft-invalidation, proposition row must still be readable."""
        self.repos["proposition_repo"].soft_invalidate(self.proposition_id, "stale")
        row = self.repos["proposition_repo"].get(self.proposition_id)
        self.assertIsNotNone(row, "proposition must remain readable after soft-invalidation")


# ---------------------------------------------------------------------------
# soft_invalidate_finding (module-level function) tests
# ---------------------------------------------------------------------------


class TestSoftInvalidateFindingFunction(unittest.TestCase):
    """soft_invalidate_finding marks the finding and returns a repair plan."""

    def setUp(self) -> None:
        self.store = _make_store()
        _setup_session_artifact_finding(self.store)
        self.repos = _make_repos(self.store)
        # Run the full downstream pipeline to create proposition + published bundle.
        run_canonical_downstream(
            session_id=_SESSION,
            trigger_finding_ids=[_FINDING_ID],
            metadata_store=self.store,
            **self.repos,
        )

    def _invalidate(self) -> InvalidationResult:
        return soft_invalidate_finding(
            session_id=_SESSION,
            finding_id=_FINDING_ID,
            reason="test_invalidation",
            finding_repo=self.repos["finding_repo"],
            proposition_repo=self.repos["proposition_repo"],
            gap_repo=self.repos["gap_repo"],
            proposal_repo=self.repos["proposal_repo"],
            assessment_repo=self.repos["assessment_repo"],
        )

    def test_returns_correct_schema_version(self) -> None:
        result = self._invalidate()
        self.assertEqual(result["schema_version"], INVALIDATION_SCHEMA_VERSION)

    def test_invalidated_id_matches_finding(self) -> None:
        result = self._invalidate()
        self.assertEqual(result["invalidated_id"], _FINDING_ID)

    def test_object_type_is_finding(self) -> None:
        result = self._invalidate()
        self.assertEqual(result["object_type"], "finding")

    def test_finding_marked_as_invalidated_after_call(self) -> None:
        self._invalidate()
        self.assertTrue(self.repos["finding_repo"].is_invalidated(_FINDING_ID))

    def test_finding_still_readable_after_call(self) -> None:
        """Tombstone-first: finding row must remain readable."""
        self._invalidate()
        row = self.repos["finding_repo"].get(_FINDING_ID)
        self.assertIsNotNone(row)
        self.assertEqual(row["finding_id"], _FINDING_ID)

    def test_repair_actions_not_empty_when_proposition_published(self) -> None:
        """When the proposition has a published bundle, repair actions must be generated."""
        propositions = self.repos["proposition_repo"].list_by_session(_SESSION)
        # Verify at least one proposition has a published bundle.
        published = [p for p in propositions if p.get("externally_visible_assessment_id")]
        self.assertGreater(
            len(published), 0, "need at least one published proposition for this test"
        )

        result = self._invalidate()
        self.assertGreater(len(result["downstream_repair_actions"]), 0)

    def test_repair_actions_include_recompute_assessment(self) -> None:
        result = self._invalidate()
        actions = {a["action"] for a in result["downstream_repair_actions"]}
        self.assertIn("recompute_assessment", actions)

    def test_repair_actions_include_bundle_rollback(self) -> None:
        result = self._invalidate()
        actions = {a["action"] for a in result["downstream_repair_actions"]}
        self.assertIn("bundle_rollback", actions)

    def test_repair_actions_target_ids_are_strings(self) -> None:
        result = self._invalidate()
        for action in result["downstream_repair_actions"]:
            with self.subTest(action=action["action"]):
                self.assertIsInstance(action["target_id"], str)
                self.assertTrue(action["target_id"])

    def test_raises_for_unknown_finding_id(self) -> None:
        with self.assertRaises(ValueError):
            soft_invalidate_finding(
                session_id=_SESSION,
                finding_id="fnd_nonexistent",
                reason="test",
                finding_repo=self.repos["finding_repo"],
                proposition_repo=self.repos["proposition_repo"],
                gap_repo=self.repos["gap_repo"],
                proposal_repo=self.repos["proposal_repo"],
                assessment_repo=self.repos["assessment_repo"],
            )

    def _insert_inference_record(
        self, record_id: str, proposition_id: str, assessment_id: str
    ) -> None:
        """Insert a minimal inference record row to satisfy FK constraints for gap creation."""
        self.repos["inference_record_repo"].create(
            {
                "inference_record_id": record_id,
                "session_id": _SESSION,
                "proposition_id": proposition_id,
                "assessment_id": assessment_id,
                "rule_id": "test_rule",
                "rule_version": "v1",
                "result": "pass",
                "input_finding_ids_json": "[]",
                "input_assessment_ids_json": "[]",
                "opened_gap_ids_json": "[]",
                "resolved_gap_ids_json": "[]",
            }
        )

    def test_reopen_gap_action_targets_resolved_gaps(self) -> None:
        """reopen_gap repair actions must target resolved gaps, not open gaps."""
        propositions = self.repos["proposition_repo"].list_by_session(_SESSION)
        self.assertGreater(len(propositions), 0)
        proposition_id = propositions[0]["proposition_id"]

        # Retrieve an existing assessment to satisfy the inference_record FK.
        assessments = self.repos["assessment_repo"].list_by_proposition(proposition_id)
        self.assertGreater(len(assessments), 0)
        assessment_id = assessments[0]["assessment_id"]

        # Insert inference records required by gap FK constraints.
        self._insert_inference_record("irec_fake_open_001", proposition_id, assessment_id)
        self._insert_inference_record("irec_fake_resolve_001", proposition_id, assessment_id)

        # Create a resolved gap for the proposition.
        gap_id = "gap_test_resolved_001"
        self.repos["gap_repo"].create(
            {
                "gap_id": gap_id,
                "session_id": _SESSION,
                "proposition_id": proposition_id,
                "gap_kind": "missing_rule_precondition",
                "title": "test resolved gap",
                "description": "gap created by test",
                "status": "resolved",
                "missing_requirement_json": "{}",
                "satisfiable_by_json": "[]",
                "related_finding_ids_json": "[]",
                "opened_by_inference_record_id": "irec_fake_open_001",
                "resolved_by_inference_record_id": "irec_fake_resolve_001",
                "resolved_at": "2024-01-10T00:00:00+00:00",
            }
        )

        result = self._invalidate()
        reopen_targets = {
            a["target_id"]
            for a in result["downstream_repair_actions"]
            if a["action"] == "reopen_gap"
        }
        self.assertIn(gap_id, reopen_targets, "resolved gap must appear in reopen_gap actions")

    def test_no_reopen_gap_for_open_gaps(self) -> None:
        """Open gaps must NOT generate reopen_gap repair actions."""
        propositions = self.repos["proposition_repo"].list_by_session(_SESSION)
        self.assertGreater(len(propositions), 0)
        proposition_id = propositions[0]["proposition_id"]

        # Retrieve an existing assessment to satisfy the inference_record FK.
        assessments = self.repos["assessment_repo"].list_by_proposition(proposition_id)
        self.assertGreater(len(assessments), 0)
        assessment_id = assessments[0]["assessment_id"]

        # Insert an inference record for the gap's opened_by FK.
        self._insert_inference_record("irec_fake_open_002", proposition_id, assessment_id)

        # Create an open gap for the proposition.
        open_gap_id = "gap_test_open_001"
        self.repos["gap_repo"].create(
            {
                "gap_id": open_gap_id,
                "session_id": _SESSION,
                "proposition_id": proposition_id,
                "gap_kind": "missing_rule_precondition",
                "title": "test open gap",
                "description": "open gap created by test",
                "status": "open",
                "missing_requirement_json": "{}",
                "satisfiable_by_json": "[]",
                "related_finding_ids_json": "[]",
                "opened_by_inference_record_id": "irec_fake_open_002",
            }
        )

        result = self._invalidate()
        reopen_targets = {
            a["target_id"]
            for a in result["downstream_repair_actions"]
            if a["action"] == "reopen_gap"
        }
        self.assertNotIn(
            open_gap_id, reopen_targets, "open gap must NOT appear in reopen_gap actions"
        )

    def test_double_invalidation_is_idempotent(self) -> None:
        """Calling soft_invalidate_finding twice must not raise and finding stays readable."""
        self._invalidate()
        # Second call: should succeed (plain UPDATE, not a guard against re-invalidation)
        result2 = self._invalidate()
        self.assertEqual(result2["invalidated_id"], _FINDING_ID)
        row = self.repos["finding_repo"].get(_FINDING_ID)
        self.assertIsNotNone(row, "finding must still be readable after double invalidation")
        self.assertTrue(self.repos["finding_repo"].is_invalidated(_FINDING_ID))

    def test_no_repair_actions_for_unpublished_proposition(self) -> None:
        """When no proposition has a published bundle, only finding is invalidated."""
        store2 = _make_store()
        _setup_session_artifact_finding(store2)
        repos2 = _make_repos(store2)
        # Only run seeding, not the full downstream (no assessment published).
        from app.evidence_engine.proposition_seeding_run import (
            SimpleMaterializationContext,
            run_system_seeded_propositions,
        )

        mat_ctx = SimpleMaterializationContext(repos2["finding_repo"], store2)
        run_system_seeded_propositions(
            session_id=_SESSION,
            trigger_finding_ids=[_FINDING_ID],
            proposition_repo=repos2["proposition_repo"],
            finding_repo=repos2["finding_repo"],
            ctx=mat_ctx,
        )
        result = soft_invalidate_finding(
            session_id=_SESSION,
            finding_id=_FINDING_ID,
            reason="test",
            finding_repo=repos2["finding_repo"],
            proposition_repo=repos2["proposition_repo"],
            gap_repo=repos2["gap_repo"],
            proposal_repo=repos2["proposal_repo"],
            assessment_repo=repos2["assessment_repo"],
        )
        # No published bundle → no recompute_assessment or bundle_rollback actions
        actions = {a["action"] for a in result["downstream_repair_actions"]}
        self.assertNotIn("recompute_assessment", actions)
        self.assertNotIn("bundle_rollback", actions)


if __name__ == "__main__":
    unittest.main()
