"""Tests for the proposition-local publish switch runtime (Phase 4g-2).

Acceptance criteria (12 test classes):

 1. TestPublishSwitchBasic              — first call sets pointer, created=True, noop=False
 2. TestPublishSwitchIdempotent         — same assessment_id → noop=True, created=False
 3. TestPublishSwitchNoAssessment       — non-existent candidate_assessment_id → ValueError
 4. TestPublishSwitchWrongProposition   — assessment belongs to another proposition → ValueError
 5. TestPublishSwitchWrongSession       — assessment belongs to another session → ValueError
 6. TestPublishSwitchDowngradeRejected  — candidate snapshot_seq ≤ current → ValueError
 7. TestExternallyVisibleBundleNone     — no publish switch yet → None
 8. TestExternallyVisibleBundleUsesPublished — seq=1 published, seq=2 committed;
                                              bundle uses seq=1 proposals
 9. TestPublishSwitchAdvance            — publish seq=1 then seq=2; bundle switches
10. TestBundleAtomicNoleak              — bundle.action_proposals all reference published assessment
11. TestAssemblePublishReadyUnaffected  — assemble_publish_ready_bundle still uses latest
12. TestMultiPropositionIsolation       — prop A publish does not affect prop B pointer
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from app.evidence_engine.assessment_evaluation_context import build_assessment_evaluation_context
from app.evidence_engine.assessment_recompute import (
    make_assessment_id,
    recompute_proposition_assessment,
)
from app.evidence_engine.proposal_refresh_run import (
    assemble_publish_ready_bundle,
    run_action_proposal_refresh,
)
from app.evidence_engine.publish_switch import (
    PUBLISH_SWITCH_SCHEMA_VERSION,
    PublishSwitchResult,
    assemble_externally_visible_bundle,
    execute_publish_switch,
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
# Store factory
# ---------------------------------------------------------------------------


def _make_store() -> SQLiteMetadataStore:
    tmp = tempfile.mkdtemp()
    store = SQLiteMetadataStore(Path(tmp) / "meta.sqlite")
    store.initialize()
    return store


# ---------------------------------------------------------------------------
# Low-level insert helpers (mirrors test_proposal_refresh_run.py)
# ---------------------------------------------------------------------------


def _insert_session(store: SQLiteMetadataStore, session_id: str = "sess_001") -> None:
    store.execute(
        "INSERT INTO sessions (session_id, goal, constraints_json, budget_json, policy_json, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [session_id, "test goal", "{}", "{}", "{}", "open"],
    )


def _insert_artifact(
    store: SQLiteMetadataStore,
    artifact_id: str = "art_001",
    session_id: str = "sess_001",
) -> None:
    store.execute(
        "INSERT INTO artifacts "
        "(artifact_id, session_id, step_id, artifact_type, name, content_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [artifact_id, session_id, "step_001", "observation_artifact", "obs", "{}"],
    )


def _insert_proposition(
    store: SQLiteMetadataStore,
    proposition_id: str = "prop_001",
    session_id: str = "sess_001",
    assessment_type: str = "change_assessment",
    proposition_type: str = "change",
    metric: str | None = "dau",
    identity_key: str = "ik_001",
) -> None:
    subject = {
        "metric": metric,
        "entity": None,
        "slice": {},
        "grain": None,
        "analysis_axis": "change",
    }
    origin = {"kind": "system_seeded", "template_id": "t1", "template_version": "v1"}
    assessment_anchor = {"assessment_type": assessment_type}
    lineage = {
        "creation_mode": "seeded",
        "source_artifact_lineages": [],
        "source_step_refs": [],
        "derived_from_proposition_ref": None,
        "derivation_version": "v1",
    }
    store.execute(
        "INSERT INTO propositions "
        "(proposition_id, session_id, proposition_type, subject_json, origin_json, "
        "assessment_anchor_json, lineage_json, seed_finding_refs_json, payload_json, identity_key) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            proposition_id,
            session_id,
            proposition_type,
            json.dumps(subject),
            json.dumps(origin),
            json.dumps(assessment_anchor),
            json.dumps(lineage),
            "[]",
            "{}",
            identity_key,
        ],
    )


def _make_finding_row(
    finding_id: str,
    session_id: str = "sess_001",
    artifact_id: str = "art_001",
    finding_type: str = "delta",
    metric: str | None = "dau",
) -> dict[str, Any]:
    subject = {
        "metric": metric,
        "entity": None,
        "slice": {},
        "grain": None,
        "analysis_axis": "change",
    }
    return {
        "finding_id": finding_id,
        "session_id": session_id,
        "artifact_id": artifact_id,
        "step_ref_json": json.dumps(
            {"session_id": session_id, "step_id": "step_001", "step_type": "compare"}
        ),
        "finding_type": finding_type,
        "canonical_item_key": finding_id,
        "subject_json": json.dumps(subject),
        "observed_window_json": json.dumps(
            {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"}
        ),
        "quality_json": json.dumps(
            {
                "data_complete": True,
                "sample_size": None,
                "row_count": 1,
                "null_rate": None,
                "quality_status": "ready",
                "quality_warnings": [],
            }
        ),
        "provenance_json": json.dumps(
            {
                "source_step_type": "compare",
                "extractor_name": "compare_extractor",
                "extractor_version": "v1",
                "artifact_schema_version": "v1",
                "canonical_item_key": finding_id,
                "artifact_item_ref": {"collection": "result", "index": None, "key": None},
                "projection_ref": None,
            }
        ),
        "payload_json": json.dumps({"delta_pct": 5.0}),
        "schema_version": "v1",
    }


# ---------------------------------------------------------------------------
# Pipeline helpers
# ---------------------------------------------------------------------------

_DEFAULT_PROPOSAL_CONTEXT: dict[str, Any] = {
    "session_goal": "explain_change",
    "risk_budget": "low",
    "policy_profile": "default_v1",
}


def _commit_assessment(
    store: SQLiteMetadataStore,
    proposition_id: str = "prop_001",
    session_id: str = "sess_001",
    trigger_finding_ids: list[str] | None = None,
) -> str:
    """Run assessment recompute and return the committed assessment_id."""
    finding_repo = FindingRepository(store)
    proposition_repo = PropositionRepository(store)
    assessment_repo = AssessmentRepository(store)
    gap_repo = EvidenceGapRepository(store)
    ir_repo = InferenceRecordRepository(store)

    prop = proposition_repo.get(proposition_id)
    assert prop is not None, f"proposition {proposition_id!r} not found"

    candidate_id = make_assessment_id(
        session_id, proposition_id, assessment_repo.next_snapshot_seq(proposition_id)
    )
    ctx = build_assessment_evaluation_context(
        session_id=session_id,
        proposition_id=proposition_id,
        proposition=prop,
        candidate_assessment_id=candidate_id,
        trigger_finding_ids=trigger_finding_ids or [],
        assessment_repo=assessment_repo,
        gap_repo=gap_repo,
        finding_repo=finding_repo,
        inference_record_repo=ir_repo,
    )
    result = recompute_proposition_assessment(
        ctx=ctx,
        assessment_repo=assessment_repo,
        gap_repo=gap_repo,
        inference_record_repo=ir_repo,
        finding_repo=finding_repo,
    )
    assert result["created"], "expected recompute to create a snapshot"
    return result["assessment_id"]


def _run_refresh(
    store: SQLiteMetadataStore,
    assessment_id: str,
    proposition_id: str = "prop_001",
    session_id: str = "sess_001",
) -> None:
    """Run proposal refresh against a committed assessment (result discarded)."""
    run_action_proposal_refresh(
        session_id=session_id,
        proposition_id=proposition_id,
        latest_assessment_id=assessment_id,
        proposal_context=_DEFAULT_PROPOSAL_CONTEXT,
        proposal_repo=ActionProposalRepository(store),
        assessment_repo=AssessmentRepository(store),
        gap_repo=EvidenceGapRepository(store),
    )


# ---------------------------------------------------------------------------
# Base test class
# ---------------------------------------------------------------------------


class _SwitchBase(unittest.TestCase):
    """Fresh store + repos; default session/artifact/proposition."""

    SESSION_ID = "sess_001"
    PROP_ID = "prop_001"

    def setUp(self) -> None:
        self.store = _make_store()
        _insert_session(self.store)
        _insert_artifact(self.store)
        _insert_proposition(self.store, proposition_id=self.PROP_ID)

        self.finding_repo = FindingRepository(self.store)
        self.proposition_repo = PropositionRepository(self.store)
        self.assessment_repo = AssessmentRepository(self.store)
        self.gap_repo = EvidenceGapRepository(self.store)
        self.ir_repo = InferenceRecordRepository(self.store)
        self.proposal_repo = ActionProposalRepository(self.store)

    def _commit_assessment(
        self,
        proposition_id: str | None = None,
        trigger_finding_ids: list[str] | None = None,
    ) -> str:
        return _commit_assessment(
            self.store,
            proposition_id=proposition_id or self.PROP_ID,
            session_id=self.SESSION_ID,
            trigger_finding_ids=trigger_finding_ids,
        )

    def _run_refresh(self, assessment_id: str, proposition_id: str | None = None) -> None:
        _run_refresh(
            self.store,
            assessment_id=assessment_id,
            proposition_id=proposition_id or self.PROP_ID,
            session_id=self.SESSION_ID,
        )

    def _add_finding(self, finding_id: str, finding_type: str = "delta") -> None:
        """Insert a finding row so subsequent recompute has different evidence."""
        self.finding_repo.create(_make_finding_row(finding_id, finding_type=finding_type))

    def _switch(
        self,
        assessment_id: str,
        *,
        proposition_id: str | None = None,
        session_id: str | None = None,
    ) -> PublishSwitchResult:
        return execute_publish_switch(
            session_id=session_id or self.SESSION_ID,
            proposition_id=proposition_id or self.PROP_ID,
            candidate_assessment_id=assessment_id,
            assessment_repo=self.assessment_repo,
            proposition_repo=self.proposition_repo,
        )

    def _visible_bundle(self, proposition_id: str | None = None):
        return assemble_externally_visible_bundle(
            session_id=self.SESSION_ID,
            proposition_id=proposition_id or self.PROP_ID,
            assessment_repo=self.assessment_repo,
            gap_repo=self.gap_repo,
            finding_repo=self.finding_repo,
            proposal_repo=self.proposal_repo,
            inference_record_repo=self.ir_repo,
            proposition_repo=self.proposition_repo,
        )


# ---------------------------------------------------------------------------
# Test 1: basic publish switch sets pointer
# ---------------------------------------------------------------------------


class TestPublishSwitchBasic(_SwitchBase):
    def test_first_switch_sets_pointer(self) -> None:
        aid = self._commit_assessment()
        self._run_refresh(aid)

        result = self._switch(aid)

        self.assertTrue(result["created"])
        self.assertFalse(result["noop"])
        self.assertEqual(result["assessment_id"], aid)
        self.assertEqual(result["proposition_id"], self.PROP_ID)
        self.assertEqual(result["schema_version"], PUBLISH_SWITCH_SCHEMA_VERSION)

        # Pointer persisted in proposition row
        prop = self.proposition_repo.get(self.PROP_ID)
        self.assertIsNotNone(prop)
        self.assertEqual(prop["externally_visible_assessment_id"], aid)  # type: ignore[index]


# ---------------------------------------------------------------------------
# Test 2: idempotency — same assessment_id → noop
# ---------------------------------------------------------------------------


class TestPublishSwitchIdempotent(_SwitchBase):
    def test_same_assessment_id_is_noop(self) -> None:
        aid = self._commit_assessment()
        self._run_refresh(aid)

        first = self._switch(aid)
        self.assertTrue(first["created"])

        second = self._switch(aid)
        self.assertFalse(second["created"])
        self.assertTrue(second["noop"])
        self.assertEqual(second["assessment_id"], aid)

        # Pointer still correct
        prop = self.proposition_repo.get(self.PROP_ID)
        self.assertEqual(prop["externally_visible_assessment_id"], aid)  # type: ignore[index]


# ---------------------------------------------------------------------------
# Test 3: non-existent candidate → ValueError
# ---------------------------------------------------------------------------


class TestPublishSwitchNoAssessment(_SwitchBase):
    def test_nonexistent_assessment_raises(self) -> None:
        with self.assertRaises(ValueError):
            self._switch("does_not_exist")


# ---------------------------------------------------------------------------
# Test 4: assessment belongs to another proposition → ValueError
# ---------------------------------------------------------------------------


class TestPublishSwitchWrongProposition(_SwitchBase):
    OTHER_PROP = "prop_002"

    def setUp(self) -> None:
        super().setUp()
        _insert_proposition(
            self.store,
            proposition_id=self.OTHER_PROP,
            identity_key="ik_002",
        )

    def test_assessment_from_other_proposition_raises(self) -> None:
        other_aid = _commit_assessment(
            self.store,
            proposition_id=self.OTHER_PROP,
            session_id=self.SESSION_ID,
        )
        with self.assertRaises(ValueError):
            execute_publish_switch(
                session_id=self.SESSION_ID,
                proposition_id=self.PROP_ID,
                candidate_assessment_id=other_aid,
                assessment_repo=self.assessment_repo,
                proposition_repo=self.proposition_repo,
            )


# ---------------------------------------------------------------------------
# Test 5: assessment belongs to another session → ValueError
# ---------------------------------------------------------------------------


class TestPublishSwitchWrongSession(_SwitchBase):
    OTHER_SESSION = "sess_002"
    OTHER_PROP = "prop_s2"

    def setUp(self) -> None:
        super().setUp()
        _insert_session(self.store, self.OTHER_SESSION)
        _insert_artifact(self.store, artifact_id="art_s2", session_id=self.OTHER_SESSION)
        _insert_proposition(
            self.store,
            proposition_id=self.OTHER_PROP,
            session_id=self.OTHER_SESSION,
            identity_key="ik_s2",
        )

    def test_assessment_from_other_session_raises(self) -> None:
        other_aid = _commit_assessment(
            self.store,
            proposition_id=self.OTHER_PROP,
            session_id=self.OTHER_SESSION,
        )
        with self.assertRaises(ValueError):
            execute_publish_switch(
                session_id=self.SESSION_ID,
                proposition_id=self.PROP_ID,
                candidate_assessment_id=other_aid,
                assessment_repo=self.assessment_repo,
                proposition_repo=self.proposition_repo,
            )


# ---------------------------------------------------------------------------
# Test 6: downgrade rejected — candidate snapshot_seq ≤ current
# ---------------------------------------------------------------------------


class TestPublishSwitchDowngradeRejected(_SwitchBase):
    def test_lower_seq_raises(self) -> None:
        aid1 = self._commit_assessment()
        # Add a finding so the second recompute produces different content
        self._add_finding("fnd_001")
        aid2 = self._commit_assessment(trigger_finding_ids=["fnd_001"])
        self._run_refresh(aid2)

        # Publish the second (higher snapshot_seq) first
        self._switch(aid2)

        # Attempt to switch back to the first → rejected
        with self.assertRaises(ValueError) as ctx:
            self._switch(aid1)
        self.assertIn("downgrade", str(ctx.exception).lower())


# ---------------------------------------------------------------------------
# Test 7: no publish switch yet → assemble_externally_visible_bundle returns None
# ---------------------------------------------------------------------------


class TestExternallyVisibleBundleNone(_SwitchBase):
    def test_no_switch_returns_none(self) -> None:
        result = self._visible_bundle()
        self.assertIsNone(result)

    def test_no_switch_even_after_assessment_committed(self) -> None:
        self._commit_assessment()
        result = self._visible_bundle()
        # assessment committed but publish switch not called → still None
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# Test 8: bundle uses published assessment (not latest)
# ---------------------------------------------------------------------------


class TestExternallyVisibleBundleUsesPublished(_SwitchBase):
    def test_bundle_uses_published_not_latest(self) -> None:
        # Commit and publish seq=1 (no findings → insufficient + gap → has proposals)
        aid1 = self._commit_assessment()
        self._run_refresh(aid1)
        self._switch(aid1)

        # Add a finding so seq=2 is genuinely different; do NOT publish
        self._add_finding("fnd_001")
        aid2 = self._commit_assessment(trigger_finding_ids=["fnd_001"])
        self._run_refresh(aid2)

        bundle = self._visible_bundle()
        self.assertIsNotNone(bundle)
        assert bundle is not None  # narrowing

        # Bundle's latest_assessment must be seq=1, not seq=2
        self.assertEqual(bundle["latest_assessment"]["assessment_id"], aid1)

        # Proposals in bundle all reference aid1
        for p in bundle["action_proposals"]:
            ref = p["primary_assessment_ref_json"]
            self.assertEqual(ref["assessment_id"], aid1)

        # assemble_publish_ready_bundle returns seq=2 (latest)
        ready = assemble_publish_ready_bundle(
            session_id=self.SESSION_ID,
            proposition_id=self.PROP_ID,
            assessment_repo=self.assessment_repo,
            gap_repo=self.gap_repo,
            finding_repo=self.finding_repo,
            proposal_repo=self.proposal_repo,
            inference_record_repo=self.ir_repo,
            proposition_repo=self.proposition_repo,
        )
        self.assertEqual(ready["latest_assessment"]["assessment_id"], aid2)


# ---------------------------------------------------------------------------
# Test 9: publish switch advance — bundle switches after second publish
# ---------------------------------------------------------------------------


class TestPublishSwitchAdvance(_SwitchBase):
    def test_bundle_switches_after_second_publish(self) -> None:
        aid1 = self._commit_assessment()
        self._run_refresh(aid1)
        self._switch(aid1)

        # Add a finding so seq=2 is genuinely different, then publish it
        self._add_finding("fnd_001")
        aid2 = self._commit_assessment(trigger_finding_ids=["fnd_001"])
        self._run_refresh(aid2)
        result = self._switch(aid2)

        self.assertTrue(result["created"])
        self.assertFalse(result["noop"])
        self.assertEqual(result["assessment_id"], aid2)

        bundle = self._visible_bundle()
        self.assertIsNotNone(bundle)
        assert bundle is not None
        self.assertEqual(bundle["latest_assessment"]["assessment_id"], aid2)


# ---------------------------------------------------------------------------
# Test 10: atomic no-leak — bundle proposals all reference published assessment
# ---------------------------------------------------------------------------


class TestBundleAtomicNoleak(_SwitchBase):
    def test_proposals_reference_published_assessment(self) -> None:
        aid1 = self._commit_assessment()
        self._run_refresh(aid1)
        self._switch(aid1)

        # Add a finding so seq=2 is genuinely different; commit and refresh but do NOT publish
        self._add_finding("fnd_001")
        aid2 = self._commit_assessment(trigger_finding_ids=["fnd_001"])
        self._run_refresh(aid2)

        bundle = self._visible_bundle()
        self.assertIsNotNone(bundle)
        assert bundle is not None

        # All proposals must reference aid1 (published), not aid2 (unpublished)
        for proposal in bundle["action_proposals"]:
            ref = proposal["primary_assessment_ref_json"]
            self.assertEqual(
                ref["assessment_id"],
                aid1,
                msg="Bundle must not leak proposals from unpublished assessment",
            )

        # The bundle's latest_assessment is aid1
        self.assertEqual(bundle["latest_assessment"]["assessment_id"], aid1)


# ---------------------------------------------------------------------------
# Test 11: assemble_publish_ready_bundle unaffected by publish pointer
# ---------------------------------------------------------------------------


class TestAssemblePublishReadyUnaffected(_SwitchBase):
    def test_publish_ready_always_uses_latest(self) -> None:
        aid1 = self._commit_assessment()
        self._run_refresh(aid1)
        self._switch(aid1)

        # Add a finding so seq=2 is genuinely different
        self._add_finding("fnd_001")
        aid2 = self._commit_assessment(trigger_finding_ids=["fnd_001"])
        self._run_refresh(aid2)

        # Publish pointer still on aid1 — publish_ready still uses latest (aid2)
        ready = assemble_publish_ready_bundle(
            session_id=self.SESSION_ID,
            proposition_id=self.PROP_ID,
            assessment_repo=self.assessment_repo,
            gap_repo=self.gap_repo,
            finding_repo=self.finding_repo,
            proposal_repo=self.proposal_repo,
            inference_record_repo=self.ir_repo,
            proposition_repo=self.proposition_repo,
        )
        self.assertEqual(ready["latest_assessment"]["assessment_id"], aid2)


# ---------------------------------------------------------------------------
# Test 12: multi-proposition isolation
# ---------------------------------------------------------------------------


class TestMultiPropositionIsolation(_SwitchBase):
    OTHER_PROP = "prop_002"

    def setUp(self) -> None:
        super().setUp()
        _insert_proposition(
            self.store,
            proposition_id=self.OTHER_PROP,
            identity_key="ik_002",
        )

    def test_prop_a_publish_does_not_affect_prop_b(self) -> None:
        # Commit and publish for prop_001
        aid_a = _commit_assessment(
            self.store,
            proposition_id=self.PROP_ID,
            session_id=self.SESSION_ID,
        )
        _run_refresh(self.store, aid_a, proposition_id=self.PROP_ID)
        execute_publish_switch(
            session_id=self.SESSION_ID,
            proposition_id=self.PROP_ID,
            candidate_assessment_id=aid_a,
            assessment_repo=self.assessment_repo,
            proposition_repo=self.proposition_repo,
        )

        # prop_002 pointer must still be NULL
        prop_b = self.proposition_repo.get(self.OTHER_PROP)
        self.assertIsNotNone(prop_b)
        self.assertIsNone(prop_b["externally_visible_assessment_id"])  # type: ignore[index]

        # assemble_externally_visible_bundle for prop_002 must return None
        bundle_b = assemble_externally_visible_bundle(
            session_id=self.SESSION_ID,
            proposition_id=self.OTHER_PROP,
            assessment_repo=self.assessment_repo,
            gap_repo=self.gap_repo,
            finding_repo=self.finding_repo,
            proposal_repo=self.proposal_repo,
            inference_record_repo=self.ir_repo,
            proposition_repo=self.proposition_repo,
        )
        self.assertIsNone(bundle_b)


# ---------------------------------------------------------------------------
# Test 13: gap anchoring — externally visible bundle uses assessment membership
# ---------------------------------------------------------------------------


class TestExternallyVisibleBundleGapAnchoring(_SwitchBase):
    """open_gaps in the externally visible bundle must be anchored to the
    published assessment's ``gap_memberships_json``, not to a live
    proposition-wide query.

    A gap that is open in the DB but NOT in the published assessment's
    ``gap_memberships_json`` must not appear in the bundle.
    """

    def test_gap_outside_published_membership_does_not_leak(self) -> None:
        # Commit seq=1 with no findings → recompute opens G1 (missing precondition)
        aid1 = self._commit_assessment()
        self._run_refresh(aid1)
        self._switch(aid1)

        # Confirm seq=1 has exactly one gap in its membership
        assessment1 = self.assessment_repo.get(aid1)
        assert assessment1 is not None
        gap_memberships = assessment1.get("gap_memberships_json") or []
        self.assertEqual(len(gap_memberships), 1, "seq=1 should have exactly one gap")
        canonical_gap_id = gap_memberships[0]["gap_ref"]["gap_id"]

        # Obtain a valid inference_record_id to satisfy the NOT NULL FK
        ir_rows = self.store.query_rows(
            "SELECT inference_record_id FROM inference_records WHERE assessment_id = ?",
            [aid1],
        )
        self.assertTrue(ir_rows, "seq=1 recompute must produce at least one inference record")
        valid_ir_id = ir_rows[0]["inference_record_id"]

        # Insert a fake gap G2 that is open for the same proposition but is NOT
        # part of seq=1's gap_memberships_json.  This simulates a gap that would
        # be created by an unpublished seq=2 assessment.
        fake_gap_id = "gap_FAKE_NOT_IN_SEQ1_MEMBERSHIP"
        self.store.execute(
            """
            INSERT INTO evidence_gaps (
                gap_id, session_id, proposition_id, gap_kind,
                status, missing_requirement_json, satisfiable_by_json,
                opened_by_inference_record_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                fake_gap_id,
                self.SESSION_ID,
                self.PROP_ID,
                "missing_rule_precondition",
                "open",
                "{}",
                "[]",
                valid_ir_id,
            ],
        )

        # Verify the live proposition-wide query returns BOTH gaps.
        # This proves that the old (live-query) implementation would have leaked G2.
        all_open = self.gap_repo.list_by_proposition(self.PROP_ID, status="open")
        open_ids = {g["gap_id"] for g in all_open}
        self.assertIn(canonical_gap_id, open_ids, "G1 must be open")
        self.assertIn(fake_gap_id, open_ids, "G2 must be open (proving live query returns both)")

        # The externally visible bundle must only include G1 (from seq=1's membership).
        bundle = self._visible_bundle()
        self.assertIsNotNone(bundle)
        assert bundle is not None
        bundle_gap_ids = {g["gap_id"] for g in bundle["live_closure"]["open_gaps"]}

        self.assertIn(
            canonical_gap_id,
            bundle_gap_ids,
            msg="G1 (in seq=1 membership) must appear in the externally visible bundle",
        )
        self.assertNotIn(
            fake_gap_id,
            bundle_gap_ids,
            msg="G2 (outside seq=1 membership) must NOT appear in the externally visible bundle",
        )


if __name__ == "__main__":
    unittest.main()
