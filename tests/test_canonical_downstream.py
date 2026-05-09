"""Regression tests for Phase 4g-3: canonical downstream pipeline independence.

These tests verify that the canonical pipeline
    finding → proposition → assessment → action proposal → publish switch
runs end-to-end without any claim or recommendation objects in the database.

Acceptance criteria:
 1. TestDownstreamRunsAfterFindings     — run_canonical_downstream creates propositions,
                                          assessments, and a published bundle
 2. TestDownstreamIdempotent            — calling twice with the same findings does not
                                          double propositions or assessments
 3. TestNoClaimsRequired               — claims table is empty; canonical outputs are
                                          produced without synthesize_findings
 4. TestPublishedBundleAssembly         — assemble_externally_visible_bundle returns a
                                          non-None bundle after a successful run
"""

from __future__ import annotations

import json
import unittest
from typing import Any

from marivo.evidence_engine.canonical_pipeline_runtime import (
    CANONICAL_DOWNSTREAM_SCHEMA_VERSION,
    CanonicalDownstreamResult,
    run_canonical_downstream,
)
from marivo.evidence_engine.publish_switch import assemble_externally_visible_bundle
from marivo.storage.evidence_repositories import (
    ActionProposalRepository,
    AssessmentRepository,
    EvidenceGapRepository,
    FindingRepository,
    InferenceRecordRepository,
    PropositionRepository,
)
from marivo.storage.sqlite_metadata import SQLiteMetadataStore
from tests.shared_fixtures import make_temp_metadata_store

# ---------------------------------------------------------------------------
# Store factory
# ---------------------------------------------------------------------------


def _make_store() -> SQLiteMetadataStore:
    return make_temp_metadata_store()


# ---------------------------------------------------------------------------
# Standard artifact + finding fixtures (T1: delta → change proposition)
# ---------------------------------------------------------------------------

_SESSION = "sess_cd_001"
_ARTIFACT_ID = "art_cd_001"
_FINDING_ID = "fnd_cd_001"

_LEFT_WIN = {"kind": "range", "start": "2024-01-01", "end": "2024-01-07"}
_RIGHT_WIN = {"kind": "range", "start": "2024-01-08", "end": "2024-01-14"}

# Compare artifact content with resolved_input_summary required by T1 materializer.
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

# Delta finding payload: direction=increase, delta_kind=scalar_delta
# T1 creation condition: direction != "flat" and delta_kind is valid → creates proposition.
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


def _insert_session(store: SQLiteMetadataStore, session_id: str = _SESSION) -> None:
    store.execute(
        "INSERT INTO sessions "
        "(session_id, goal, constraints_json, budget_json, status) "
        "VALUES (?, ?, ?, ?, ?)",
        [session_id, "test downstream", "{}", "{}", "open"],
    )


def _insert_artifact(
    store: SQLiteMetadataStore,
    artifact_id: str = _ARTIFACT_ID,
    session_id: str = _SESSION,
    content: dict[str, Any] | None = None,
) -> None:
    store.execute(
        "INSERT INTO artifacts "
        "(artifact_id, session_id, step_id, artifact_type, name, content_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            artifact_id,
            session_id,
            "step_cd_001",
            "compare_artifact",
            artifact_id,
            json.dumps(content if content is not None else _COMPARE_ARTIFACT_CONTENT),
        ],
    )


def _insert_finding(
    store: SQLiteMetadataStore,
    finding_id: str = _FINDING_ID,
    session_id: str = _SESSION,
    artifact_id: str = _ARTIFACT_ID,
    payload: dict[str, Any] | None = None,
) -> None:
    subject = {
        "metric": "dau",
        "entity": None,
        "slice": {},
        "grain": "day",
        "analysis_axis": "scalar",
    }
    step_ref = {
        "session_id": session_id,
        "step_id": "step_cd_001",
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
        "canonical_item_key": finding_id,
        "artifact_item_ref": {"collection": "result", "index": None, "key": None},
        "projection_ref": None,
    }
    FindingRepository(store).create(
        {
            "finding_id": finding_id,
            "session_id": session_id,
            "artifact_id": artifact_id,
            "step_ref_json": json.dumps(step_ref),
            "finding_type": "delta",
            "canonical_item_key": finding_id,
            "subject_json": json.dumps(subject),
            "observed_window_json": json.dumps(
                {"kind": "range", "start": "2024-01-08", "end": "2024-01-14"}
            ),
            "quality_json": json.dumps(quality),
            "provenance_json": json.dumps(provenance),
            "payload_json": json.dumps(payload if payload is not None else _DELTA_PAYLOAD),
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


def _assert_no_slot_errors(test_case: unittest.TestCase, result: CanonicalDownstreamResult) -> None:
    """Assert that every proposition slot in *result* has error=None."""
    errors = [
        f"{slot['proposition_id']}: {slot['error']}"
        for slot in result["proposition_results"]
        if slot["error"]
    ]
    test_case.assertEqual(errors, [], f"Unexpected per-slot errors: {errors}")


# ---------------------------------------------------------------------------
# Test 1: full pipeline runs end-to-end
# ---------------------------------------------------------------------------


class TestDownstreamRunsAfterFindings(unittest.TestCase):
    """run_canonical_downstream creates proposition, assessment, and published bundle."""

    def setUp(self) -> None:
        self.store = _make_store()
        _insert_session(self.store)
        _insert_artifact(self.store)
        _insert_finding(self.store)
        self.repos = _make_repos(self.store)

    def test_returns_correct_schema_version(self) -> None:
        result = run_canonical_downstream(
            session_id=_SESSION,
            trigger_finding_ids=[_FINDING_ID],
            metadata_store=self.store,
            **self.repos,
        )
        _assert_no_slot_errors(self, result)
        self.assertEqual(result["schema_version"], CANONICAL_DOWNSTREAM_SCHEMA_VERSION)

    def test_proposition_created(self) -> None:
        run_canonical_downstream(
            session_id=_SESSION,
            trigger_finding_ids=[_FINDING_ID],
            metadata_store=self.store,
            **self.repos,
        )
        propositions = self.repos["proposition_repo"].list_by_session(_SESSION)
        self.assertGreater(len(propositions), 0, "At least one proposition should be created")
        self.assertEqual(propositions[0]["proposition_type"], "change")

    def test_assessment_created(self) -> None:
        result = run_canonical_downstream(
            session_id=_SESSION,
            trigger_finding_ids=[_FINDING_ID],
            metadata_store=self.store,
            **self.repos,
        )
        _assert_no_slot_errors(self, result)
        # At least one proposition slot should have a committed assessment.
        created = [
            slot
            for slot in result["proposition_results"]
            if slot.get("recompute_result") is not None
            and slot["recompute_result"].get("created") is True  # type: ignore[index]
        ]
        self.assertGreater(len(created), 0, "At least one assessment should be committed")

    def test_publish_switch_executed(self) -> None:
        result = run_canonical_downstream(
            session_id=_SESSION,
            trigger_finding_ids=[_FINDING_ID],
            metadata_store=self.store,
            **self.repos,
        )
        _assert_no_slot_errors(self, result)
        published = [
            slot
            for slot in result["proposition_results"]
            if slot.get("publish_result") is not None
            and slot["publish_result"].get("created") is True  # type: ignore[index]
        ]
        self.assertGreater(len(published), 0, "At least one proposition should be published")

    def test_no_errors_in_proposition_results(self) -> None:
        result = run_canonical_downstream(
            session_id=_SESSION,
            trigger_finding_ids=[_FINDING_ID],
            metadata_store=self.store,
            **self.repos,
        )
        errors = [slot["error"] for slot in result["proposition_results"] if slot["error"]]
        self.assertEqual(errors, [], f"Unexpected errors: {errors}")

    def test_seeding_result_present(self) -> None:
        result = run_canonical_downstream(
            session_id=_SESSION,
            trigger_finding_ids=[_FINDING_ID],
            metadata_store=self.store,
            **self.repos,
        )
        _assert_no_slot_errors(self, result)
        self.assertGreater(len(result["seeding_result"]["created_proposition_ids"]), 0)


# ---------------------------------------------------------------------------
# Test 2: idempotency
# ---------------------------------------------------------------------------


class TestDownstreamIdempotent(unittest.TestCase):
    """Calling run_canonical_downstream twice with the same inputs is a no-op on the second call."""

    def setUp(self) -> None:
        self.store = _make_store()
        _insert_session(self.store)
        _insert_artifact(self.store)
        _insert_finding(self.store)
        self.repos = _make_repos(self.store)

    def test_proposition_count_stable(self) -> None:
        for _ in range(2):
            run_canonical_downstream(
                session_id=_SESSION,
                trigger_finding_ids=[_FINDING_ID],
                metadata_store=self.store,
                **self.repos,
            )
        propositions = self.repos["proposition_repo"].list_by_session(_SESSION)
        # identity_key deduplication means the same finding → same proposition,
        # so the proposition count should remain 1 after two runs.
        self.assertEqual(len(propositions), 1)

    def test_second_run_seeding_no_new_creations(self) -> None:
        run_canonical_downstream(
            session_id=_SESSION,
            trigger_finding_ids=[_FINDING_ID],
            metadata_store=self.store,
            **self.repos,
        )
        second = run_canonical_downstream(
            session_id=_SESSION,
            trigger_finding_ids=[_FINDING_ID],
            metadata_store=self.store,
            **self.repos,
        )
        # The second run should produce no newly created propositions.
        self.assertEqual(second["seeding_result"]["created_proposition_ids"], [])
        # But affected_proposition_ids still lists the existing one.
        self.assertGreater(len(second["seeding_result"]["affected_proposition_ids"]), 0)

    def test_assessment_not_doubled(self) -> None:
        """Second run is a canonical-diff no-op when findings didn't change."""
        for _ in range(2):
            run_canonical_downstream(
                session_id=_SESSION,
                trigger_finding_ids=[_FINDING_ID],
                metadata_store=self.store,
                **self.repos,
            )
        propositions = self.repos["proposition_repo"].list_by_session(_SESSION)
        for prop in propositions:
            assessments = self.repos["assessment_repo"].list_by_proposition(prop["proposition_id"])
            self.assertEqual(
                len(assessments),
                1,
                f"Expected exactly 1 assessment per proposition, got {len(assessments)}",
            )


# ---------------------------------------------------------------------------
# Test 3: no claims required
# ---------------------------------------------------------------------------


class TestNoClaimsRequired(unittest.TestCase):
    """Canonical pipeline produces propositions without any legacy claim tables."""

    def setUp(self) -> None:
        self.store = _make_store()
        _insert_session(self.store)
        _insert_artifact(self.store)
        _insert_finding(self.store)
        self.repos = _make_repos(self.store)

    def test_propositions_exist_without_claims(self) -> None:
        run_canonical_downstream(
            session_id=_SESSION,
            trigger_finding_ids=[_FINDING_ID],
            metadata_store=self.store,
            **self.repos,
        )
        propositions = self.repos["proposition_repo"].list_by_session(_SESSION)
        self.assertGreater(len(propositions), 0, "Propositions should exist")


# ---------------------------------------------------------------------------
# Test 4: externally visible bundle assembly
# ---------------------------------------------------------------------------


class TestPublishedBundleAssembly(unittest.TestCase):
    """assemble_externally_visible_bundle returns a non-None bundle after a publish switch."""

    def setUp(self) -> None:
        self.store = _make_store()
        _insert_session(self.store)
        _insert_artifact(self.store)
        _insert_finding(self.store)
        self.repos = _make_repos(self.store)

    def test_bundle_not_none_after_publish(self) -> None:
        run_canonical_downstream(
            session_id=_SESSION,
            trigger_finding_ids=[_FINDING_ID],
            metadata_store=self.store,
            **self.repos,
        )
        propositions = self.repos["proposition_repo"].list_by_session(_SESSION)
        self.assertGreater(len(propositions), 0)

        prop = propositions[0]
        # externally_visible_assessment_id should be set after a successful publish switch.
        self.assertIsNotNone(
            prop.get("externally_visible_assessment_id"),
            "externally_visible_assessment_id should be set after publish switch",
        )
        bundle = assemble_externally_visible_bundle(
            session_id=_SESSION,
            proposition_id=prop["proposition_id"],
            assessment_repo=self.repos["assessment_repo"],
            gap_repo=self.repos["gap_repo"],
            finding_repo=self.repos["finding_repo"],
            proposal_repo=self.repos["proposal_repo"],
            inference_record_repo=self.repos["inference_record_repo"],
            proposition_repo=self.repos["proposition_repo"],
        )
        self.assertIsNotNone(bundle, "assemble_externally_visible_bundle must return a bundle")

    def test_bundle_schema_version(self) -> None:
        run_canonical_downstream(
            session_id=_SESSION,
            trigger_finding_ids=[_FINDING_ID],
            metadata_store=self.store,
            **self.repos,
        )
        propositions = self.repos["proposition_repo"].list_by_session(_SESSION)
        prop = propositions[0]
        bundle = assemble_externally_visible_bundle(
            session_id=_SESSION,
            proposition_id=prop["proposition_id"],
            assessment_repo=self.repos["assessment_repo"],
            gap_repo=self.repos["gap_repo"],
            finding_repo=self.repos["finding_repo"],
            proposal_repo=self.repos["proposal_repo"],
            inference_record_repo=self.repos["inference_record_repo"],
            proposition_repo=self.repos["proposition_repo"],
        )
        self.assertIsNotNone(bundle)
        self.assertIn("schema_version", bundle)  # type: ignore[operator]

    def test_empty_trigger_ids_noop(self) -> None:
        """Empty trigger_finding_ids should return a result with no proposition_results."""
        result = run_canonical_downstream(
            session_id=_SESSION,
            trigger_finding_ids=[],
            metadata_store=self.store,
            **self.repos,
        )
        self.assertEqual(result["proposition_results"], [])
        self.assertEqual(result["seeding_result"]["affected_proposition_ids"], [])
