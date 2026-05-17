"""Tests for Phase 4h-1: replay determinism and crash recovery.

Verifies:
 1. TestDeterministicAssessmentId  — assessment IDs are deterministic (no uuid4)
 2. TestRecoverFromAssessmentCrash — partial recovery resumes from proposal refresh + publish
 3. TestRecoverPublishCrash        — recovery is idempotent (safe to call twice)
 4. TestRecoverNothingCommitted    — full pipeline runs when no assessment exists yet
"""

from __future__ import annotations

import json
import unittest
from typing import Any

from marivo.adapters.local.sqlite_metadata import SQLiteMetadataStore
from marivo.adapters.server.evidence_repositories import (
    ActionProposalRepository,
    AssessmentRepository,
    EvidenceGapRepository,
    FindingRepository,
    InferenceRecordRepository,
    PropositionRepository,
)
from marivo.runtime.evidence.assessment_recompute import make_assessment_id
from marivo.runtime.evidence.canonical_pipeline import run_canonical_downstream
from marivo.runtime.evidence.replay_recovery import (
    RECOVERY_CHECKPOINT_SCHEMA_VERSION,
    get_proposition_checkpoint,
    recover_proposition_pipeline,
)
from tests.shared_fixtures import make_temp_metadata_store

# ---------------------------------------------------------------------------
# Shared fixtures (mirror test_canonical_downstream.py)
# ---------------------------------------------------------------------------

_SESSION = "sess_rr_001"
_ARTIFACT_ID = "art_rr_001"
_FINDING_ID = "fnd_rr_001"

_LEFT_WIN = {"field": "time", "start": "2024-01-01", "end": "2024-01-07"}
_RIGHT_WIN = {"field": "time", "start": "2024-01-08", "end": "2024-01-14"}

_COMPARE_ARTIFACT_CONTENT: dict[str, Any] = {
    "comparison_type": "scalar_delta",
    "metric": "dau",
    "direction": "increase",
    "resolved_input_summary": {
        "current_scope": {},
        "current_time_scope": _LEFT_WIN,
        "baseline_time_scope": _RIGHT_WIN,
    },
}

_DELTA_PAYLOAD: dict[str, Any] = {
    "delta_kind": "scalar_delta",
    "current_ref": {
        "artifact_id": _ARTIFACT_ID,
        "item_ref": {"collection": "result", "index": None, "key": None},
    },
    "baseline_ref": {
        "artifact_id": _ARTIFACT_ID,
        "item_ref": {"collection": "result", "index": None, "key": None},
    },
    "current_value": 900.0,
    "baseline_value": 1000.0,
    "absolute_delta": 100.0,
    "relative_delta": 0.111,
    "direction": "increase",
    "presence": "both",
    "unit": "users",
}


def _make_store() -> SQLiteMetadataStore:
    return make_temp_metadata_store()


def _setup(store: SQLiteMetadataStore) -> None:
    """Insert session, artifact, and finding into *store*."""
    store.execute(
        "INSERT INTO sessions "
        "(session_id, goal, constraints_json, budget_json, status) "
        "VALUES (?, ?, ?, ?, ?)",
        [_SESSION, "test replay recovery", "{}", "{}", "open"],
    )
    store.execute(
        "INSERT INTO artifacts "
        "(artifact_id, session_id, step_id, artifact_type, name, content_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            _ARTIFACT_ID,
            _SESSION,
            "step_rr_001",
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
    step_ref = {"session_id": _SESSION, "step_id": "step_rr_001", "step_type": "compare"}
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
                {"field": "time", "start": "2024-01-08", "end": "2024-01-14"}
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
# Test 1: Deterministic assessment ID
# ---------------------------------------------------------------------------


class TestDeterministicAssessmentId(unittest.TestCase):
    """After Phase 4h-1, assessment IDs are derived from (session, proposition, seq)."""

    def setUp(self) -> None:
        self.store = _make_store()
        _setup(self.store)
        self.repos = _make_repos(self.store)

    def test_assessment_id_matches_make_assessment_id(self) -> None:
        """The assessment_id stored in the DB must equal make_assessment_id(session, prop, 1)."""
        result = run_canonical_downstream(
            session_id=_SESSION,
            trigger_finding_ids=[_FINDING_ID],
            metadata_store=self.store,
            **self.repos,
        )
        errors = [s["error"] for s in result["proposition_results"] if s["error"]]
        self.assertEqual(errors, [])

        proposition_results = result["proposition_results"]
        self.assertGreater(len(proposition_results), 0)

        for slot in proposition_results:
            proposition_id = slot["proposition_id"]
            recompute = slot["recompute_result"]
            self.assertIsNotNone(recompute)
            self.assertTrue(recompute["created"])

            expected_id = make_assessment_id(_SESSION, proposition_id, 1)
            self.assertEqual(
                recompute["assessment_id"],
                expected_id,
                f"assessment_id for proposition {proposition_id} is not deterministic",
            )

    def test_second_run_produces_same_assessment_id_and_is_noop(self) -> None:
        """Replay with the same inputs must not create a new assessment snapshot."""
        run_canonical_downstream(
            session_id=_SESSION,
            trigger_finding_ids=[_FINDING_ID],
            metadata_store=self.store,
            **self.repos,
        )
        # Second run with same inputs.
        result2 = run_canonical_downstream(
            session_id=_SESSION,
            trigger_finding_ids=[_FINDING_ID],
            metadata_store=self.store,
            **self.repos,
        )
        for slot in result2["proposition_results"]:
            # canonical-diff gate: no new snapshot created on identical input
            recompute = slot["recompute_result"]
            self.assertIsNotNone(recompute)
            self.assertFalse(recompute["created"], "second run must be a no-op (created=False)")

    def test_assessment_id_not_based_on_random_uuid(self) -> None:
        """Two independent fresh runs with identical inputs must produce the same assessment_id."""
        # First run.
        store1 = _make_store()
        _setup(store1)
        repos1 = _make_repos(store1)
        result1 = run_canonical_downstream(
            session_id=_SESSION,
            trigger_finding_ids=[_FINDING_ID],
            metadata_store=store1,
            **repos1,
        )
        # Second run on a separate DB.
        store2 = _make_store()
        _setup(store2)
        repos2 = _make_repos(store2)
        result2 = run_canonical_downstream(
            session_id=_SESSION,
            trigger_finding_ids=[_FINDING_ID],
            metadata_store=store2,
            **repos2,
        )

        ids1 = {s["recompute_result"]["assessment_id"] for s in result1["proposition_results"]}
        ids2 = {s["recompute_result"]["assessment_id"] for s in result2["proposition_results"]}
        self.assertEqual(ids1, ids2, "assessment IDs must be identical across independent runs")


# ---------------------------------------------------------------------------
# Test 2: Recover from assessment crash (committed, not published)
# ---------------------------------------------------------------------------


class TestRecoverFromAssessmentCrash(unittest.TestCase):
    """recover_proposition_pipeline resumes from proposal refresh when assessment is committed."""

    def setUp(self) -> None:
        self.store = _make_store()
        _setup(self.store)
        self.repos = _make_repos(self.store)
        result = run_canonical_downstream(
            session_id=_SESSION,
            trigger_finding_ids=[_FINDING_ID],
            metadata_store=self.store,
            **self.repos,
        )
        errors = [s["error"] for s in result["proposition_results"] if s["error"]]
        self.assertEqual(errors, [])
        propositions = self.repos["proposition_repo"].list_by_session(_SESSION)
        self.assertGreater(len(propositions), 0)
        self.proposition_id = propositions[0]["proposition_id"]

        # Simulate crash: clear the externally_visible_assessment_id pointer.
        self.store.execute(
            "UPDATE propositions SET externally_visible_assessment_id = NULL WHERE proposition_id = ?",
            [self.proposition_id],
        )

    def test_checkpoint_reports_assessment_committed_not_visible(self) -> None:
        cp = get_proposition_checkpoint(
            proposition_id=self.proposition_id,
            assessment_repo=self.repos["assessment_repo"],
            proposition_repo=self.repos["proposition_repo"],
        )
        self.assertEqual(cp["schema_version"], RECOVERY_CHECKPOINT_SCHEMA_VERSION)
        self.assertTrue(cp["assessment_committed"])
        self.assertIsNotNone(cp["assessment_id"])
        self.assertFalse(cp["externally_visible"])

    def test_recovery_publishes_the_bundle(self) -> None:
        recover_proposition_pipeline(
            session_id=_SESSION,
            proposition_id=self.proposition_id,
            trigger_finding_ids=[_FINDING_ID],
            **self.repos,
        )
        proposition = self.repos["proposition_repo"].get(self.proposition_id)
        self.assertIsNotNone(proposition["externally_visible_assessment_id"])

    def test_recovery_result_has_no_error(self) -> None:
        result = recover_proposition_pipeline(
            session_id=_SESSION,
            proposition_id=self.proposition_id,
            trigger_finding_ids=[_FINDING_ID],
            **self.repos,
        )
        self.assertIsNone(result["error"])

    def test_recovery_skips_recompute(self) -> None:
        """Partial recovery must not run a new recompute (assessment already committed)."""
        result = recover_proposition_pipeline(
            session_id=_SESSION,
            proposition_id=self.proposition_id,
            trigger_finding_ids=[_FINDING_ID],
            **self.repos,
        )
        # recompute_result is None on the partial path
        self.assertIsNone(result["recompute_result"])


# ---------------------------------------------------------------------------
# Test 3: Idempotency (publish crash retried twice)
# ---------------------------------------------------------------------------


class TestRecoverPublishCrash(unittest.TestCase):
    """Calling recover twice is idempotent — the second call is a noop."""

    def setUp(self) -> None:
        self.store = _make_store()
        _setup(self.store)
        self.repos = _make_repos(self.store)
        run_canonical_downstream(
            session_id=_SESSION,
            trigger_finding_ids=[_FINDING_ID],
            metadata_store=self.store,
            **self.repos,
        )
        propositions = self.repos["proposition_repo"].list_by_session(_SESSION)
        self.proposition_id = propositions[0]["proposition_id"]
        # Simulate publish crash: clear the pointer.
        self.store.execute(
            "UPDATE propositions SET externally_visible_assessment_id = NULL WHERE proposition_id = ?",
            [self.proposition_id],
        )

    def test_first_recovery_publishes(self) -> None:
        result = recover_proposition_pipeline(
            session_id=_SESSION,
            proposition_id=self.proposition_id,
            trigger_finding_ids=[_FINDING_ID],
            **self.repos,
        )
        self.assertIsNone(result["error"])
        proposition = self.repos["proposition_repo"].get(self.proposition_id)
        self.assertIsNotNone(proposition["externally_visible_assessment_id"])

    def test_second_recovery_is_noop(self) -> None:
        """After the first recovery, the second call must detect already-visible state."""
        recover_proposition_pipeline(
            session_id=_SESSION,
            proposition_id=self.proposition_id,
            trigger_finding_ids=[_FINDING_ID],
            **self.repos,
        )
        result2 = recover_proposition_pipeline(
            session_id=_SESSION,
            proposition_id=self.proposition_id,
            trigger_finding_ids=[_FINDING_ID],
            **self.repos,
        )
        self.assertIsNone(result2["error"])
        # On noop path: all result fields are None (already externally visible).
        self.assertIsNone(result2["recompute_result"])
        self.assertIsNone(result2["proposal_result"])
        self.assertIsNone(result2["publish_result"])


# ---------------------------------------------------------------------------
# Test 4: Full pipeline runs when nothing is committed yet
# ---------------------------------------------------------------------------


class TestRecoverNothingCommitted(unittest.TestCase):
    """recover_proposition_pipeline runs the full pipeline when no assessment exists."""

    def setUp(self) -> None:
        self.store = _make_store()
        _setup(self.store)
        self.repos = _make_repos(self.store)
        # Run only the seeding stage — no assessment committed yet.
        from marivo.runtime.evidence.proposition_seeding import (
            SimpleMaterializationContext,
            run_system_seeded_propositions,
        )

        mat_ctx = SimpleMaterializationContext(self.repos["finding_repo"], self.store)
        seeding = run_system_seeded_propositions(
            session_id=_SESSION,
            trigger_finding_ids=[_FINDING_ID],
            proposition_repo=self.repos["proposition_repo"],
            finding_repo=self.repos["finding_repo"],
            ctx=mat_ctx,
        )
        self.assertGreater(len(seeding["affected_proposition_ids"]), 0)
        self.proposition_id = seeding["affected_proposition_ids"][0]

    def test_checkpoint_reports_no_assessment(self) -> None:
        cp = get_proposition_checkpoint(
            proposition_id=self.proposition_id,
            assessment_repo=self.repos["assessment_repo"],
            proposition_repo=self.repos["proposition_repo"],
        )
        self.assertFalse(cp["assessment_committed"])
        self.assertIsNone(cp["assessment_id"])
        self.assertFalse(cp["externally_visible"])

    def test_recovery_creates_assessment_and_publishes(self) -> None:
        result = recover_proposition_pipeline(
            session_id=_SESSION,
            proposition_id=self.proposition_id,
            trigger_finding_ids=[_FINDING_ID],
            **self.repos,
        )
        self.assertIsNone(result["error"])
        proposition = self.repos["proposition_repo"].get(self.proposition_id)
        self.assertIsNotNone(proposition["externally_visible_assessment_id"])

    def test_recovery_runs_full_pipeline(self) -> None:
        """Full pipeline path: recompute_result must be set and created=True."""
        result = recover_proposition_pipeline(
            session_id=_SESSION,
            proposition_id=self.proposition_id,
            trigger_finding_ids=[_FINDING_ID],
            **self.repos,
        )
        self.assertIsNotNone(result["recompute_result"])
        self.assertTrue(result["recompute_result"]["created"])


if __name__ == "__main__":
    unittest.main()
