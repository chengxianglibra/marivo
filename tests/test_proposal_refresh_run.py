"""Tests for the action proposal refresh runtime (Phase 4g-1).

Test domains:
 1. TestProposalRefreshBasic            — blocking gap → investigate proposal, noop=False
 2. TestProposalRefreshStableIdentity   — same inputs → same proposal_ids, noop=True on replay
 3. TestProposalRefreshNewAssessment    — new assessment snapshot → new proposals
 4. TestProposalRefreshNoAssessment     — no latest_assessment → raises ValueError
 5. TestProposalRefreshEmptySet         — supported + no blocking gaps → empty proposals
 6. TestProposalRefreshNoWriteOnNoop    — noop run writes zero rows
 7. TestProposalPriorityAxesTotal       — all 4 priority axes present and non-null
 8. TestProposalNoRewriteJudgment       — refresh does not mutate assessment row
 9. TestPublishReadyBundleAssembly      — bundle fields present and consistent
10. TestPublishReadyBundleNullAssessment — no latest_assessment → raises ValueError
11. TestProposalContextRequired         — missing policy_profile → raises ValueError
12. TestMultiplePropositionIsolation    — prop A refresh does not write proposals for prop B
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
from marivo.evidence_engine.assessment_evaluation_context import build_assessment_evaluation_context
from marivo.evidence_engine.assessment_recompute import (
    make_assessment_id,
    recompute_proposition_assessment,
)
from marivo.evidence_engine.proposal_refresh_run import (
    BUNDLE_SCHEMA_VERSION,
    REFRESH_SCHEMA_VERSION,
    ProposalRefreshResult,
    PublishReadyBundle,
    assemble_publish_ready_bundle,
    run_action_proposal_refresh,
)
from tests.shared_fixtures import make_temp_metadata_store

# ---------------------------------------------------------------------------
# Store factory
# ---------------------------------------------------------------------------


def _make_store() -> SQLiteMetadataStore:
    return make_temp_metadata_store()


# ---------------------------------------------------------------------------
# Low-level insert helpers
# ---------------------------------------------------------------------------


def _insert_session(store: SQLiteMetadataStore, session_id: str = "sess_001") -> None:
    store.execute(
        "INSERT INTO sessions (session_id, goal, constraints_json, budget_json, status) "
        "VALUES (?, ?, ?, ?, ?)",
        [session_id, "test goal", "{}", "{}", "open"],
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
# Recompute helper — produces a committed assessment and returns its id
# ---------------------------------------------------------------------------


def _run_recompute(
    store: SQLiteMetadataStore,
    proposition_id: str = "prop_001",
    session_id: str = "sess_001",
    trigger_finding_ids: list[str] | None = None,
) -> str:
    """Run assessment recompute and return the committed assessment_id.

    Raises AssertionError if the recompute produced a no-op (created=False).
    """
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


# ---------------------------------------------------------------------------
# Default proposal context
# ---------------------------------------------------------------------------

_DEFAULT_PROPOSAL_CONTEXT: dict[str, Any] = {
    "session_goal": "explain_change",
    "risk_budget": "low",
    "policy_profile": "default_v1",
}


# ---------------------------------------------------------------------------
# Base test class
# ---------------------------------------------------------------------------


class _RefreshBase(unittest.TestCase):
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
        """Run recompute and return the committed assessment_id."""
        return _run_recompute(
            self.store,
            proposition_id=proposition_id or self.PROP_ID,
            session_id=self.SESSION_ID,
            trigger_finding_ids=trigger_finding_ids,
        )

    def _refresh(
        self,
        assessment_id: str,
        *,
        proposition_id: str | None = None,
        proposal_context: dict[str, Any] | None = None,
    ) -> ProposalRefreshResult:
        return run_action_proposal_refresh(
            session_id=self.SESSION_ID,
            proposition_id=proposition_id or self.PROP_ID,
            latest_assessment_id=assessment_id,
            proposal_context=proposal_context or _DEFAULT_PROPOSAL_CONTEXT,
            proposal_repo=self.proposal_repo,
            assessment_repo=self.assessment_repo,
            gap_repo=self.gap_repo,
        )

    def _bundle(self, proposition_id: str | None = None) -> PublishReadyBundle:
        return assemble_publish_ready_bundle(
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
# 1. TestProposalRefreshBasic
# ---------------------------------------------------------------------------


class TestProposalRefreshBasic(_RefreshBase):
    """Precondition-miss assessment → blocking gap → investigate proposal generated."""

    def setUp(self) -> None:
        super().setUp()
        # No findings: recompute produces insufficient + blocking gap
        self.assessment_id = self._commit_assessment()

    def test_noop_false_on_first_run(self) -> None:
        result = self._refresh(self.assessment_id)
        self.assertFalse(result["noop"])

    def test_proposals_written(self) -> None:
        result = self._refresh(self.assessment_id)
        self.assertGreater(len(result["proposal_ids"]), 0)

    def test_investigate_proposal_generated(self) -> None:
        result = self._refresh(self.assessment_id)
        proposals = self.proposal_repo.list_by_assessment(self.SESSION_ID, self.assessment_id)
        action_kinds = {p["action_kind"] for p in proposals}
        self.assertIn("investigate", action_kinds)

    def test_schema_version(self) -> None:
        result = self._refresh(self.assessment_id)
        self.assertEqual(result["schema_version"], REFRESH_SCHEMA_VERSION)

    def test_primary_assessment_id_in_result(self) -> None:
        result = self._refresh(self.assessment_id)
        self.assertEqual(result["primary_assessment_id"], self.assessment_id)


# ---------------------------------------------------------------------------
# 2. TestProposalRefreshStableIdentity
# ---------------------------------------------------------------------------


class TestProposalRefreshStableIdentity(_RefreshBase):
    """Same inputs produce the same proposal_ids; second run is a no-op."""

    def setUp(self) -> None:
        super().setUp()
        self.assessment_id = self._commit_assessment()

    def test_proposal_ids_stable(self) -> None:
        r1 = self._refresh(self.assessment_id)
        r2 = self._refresh(self.assessment_id)
        self.assertEqual(sorted(r1["proposal_ids"]), sorted(r2["proposal_ids"]))

    def test_second_run_is_noop(self) -> None:
        self._refresh(self.assessment_id)
        r2 = self._refresh(self.assessment_id)
        self.assertTrue(r2["noop"])

    def test_noop_materialized_count_zero(self) -> None:
        self._refresh(self.assessment_id)
        r2 = self._refresh(self.assessment_id)
        self.assertEqual(r2["materialized_count"], 0)


# ---------------------------------------------------------------------------
# 3. TestProposalRefreshNewAssessment
# ---------------------------------------------------------------------------


class TestProposalRefreshNewAssessment(_RefreshBase):
    """When the assessment snapshot changes, new proposals are generated."""

    def setUp(self) -> None:
        super().setUp()
        # First assessment: no findings → insufficient + blocking gap
        self.assessment_id_1 = self._commit_assessment()
        # Add a delta finding, then second recompute → supported + no gap
        self.finding_repo.create(_make_finding_row("fnd_001", finding_type="delta", metric="dau"))
        self.assessment_id_2 = self._commit_assessment(
            trigger_finding_ids=["fnd_001"],
        )

    def test_different_assessments_have_different_proposals(self) -> None:
        r1 = self._refresh(self.assessment_id_1)
        r2 = self._refresh(self.assessment_id_2)
        # The proposal ids differ because primary_assessment_ref differs
        self.assertNotEqual(sorted(r1["proposal_ids"]), sorted(r2["proposal_ids"]))

    def test_second_assessment_has_lower_or_zero_proposals(self) -> None:
        """Supported assessment with no blocking gaps → empty proposal set."""
        assessment = self.assessment_repo.get(self.assessment_id_2)
        assert assessment is not None
        # Delta finding + change_assessment → directional precondition satisfied → supported.
        self.assertEqual(assessment["status"], "supported")
        r2 = self._refresh(self.assessment_id_2)
        # supported + no blocking gaps → empty
        self.assertEqual(len(r2["proposal_ids"]), 0)

    def test_new_assessment_refresh_distinct_from_first(self) -> None:
        # First assessment (insufficient + blocking gap) → non-empty proposals
        r1 = self._refresh(self.assessment_id_1)
        self.assertGreater(len(r1["proposal_ids"]), 0)
        self.assertFalse(r1["noop"])
        # Second assessment (supported, no gaps) → empty proposals; empty→empty is a valid noop
        r2 = self._refresh(self.assessment_id_2)
        # proposals differ: r1 is non-empty, r2 is empty (different primary_assessment_ref)
        self.assertNotEqual(sorted(r1["proposal_ids"]), sorted(r2["proposal_ids"]))


# ---------------------------------------------------------------------------
# 4. TestProposalRefreshNoAssessment
# ---------------------------------------------------------------------------


class TestProposalRefreshNoAssessment(_RefreshBase):
    """Passing a non-existent assessment_id raises ValueError."""

    def test_raises_on_missing_assessment(self) -> None:
        with self.assertRaises(ValueError):
            self._refresh("nonexistent_assessment_id")


# ---------------------------------------------------------------------------
# 5. TestProposalRefreshEmptySet
# ---------------------------------------------------------------------------


class TestProposalRefreshEmptySet(_RefreshBase):
    """Supported assessment with no blocking gaps produces an empty proposal set."""

    def setUp(self) -> None:
        super().setUp()
        # Add a delta finding → supported assessment
        self.finding_repo.create(_make_finding_row("fnd_001", finding_type="delta", metric="dau"))
        self.assessment_id = self._commit_assessment(
            trigger_finding_ids=["fnd_001"],
        )

    def test_empty_proposals_when_supported(self) -> None:
        assessment = self.assessment_repo.get(self.assessment_id)
        assert assessment is not None
        # Delta finding + change_assessment → directional precondition satisfied → supported.
        # Supported with no blocking gaps → empty proposal set.
        self.assertEqual(assessment["status"], "supported")
        result = self._refresh(self.assessment_id)
        self.assertEqual(result["proposal_ids"], [])

    def test_empty_set_is_valid_canonical_result(self) -> None:
        """Empty proposal set must not raise; result is valid."""
        result = self._refresh(self.assessment_id)
        self.assertIsInstance(result["proposal_ids"], list)
        self.assertFalse(result["noop"] and result["materialized_count"] != 0)

    def test_schema_version_present_on_empty(self) -> None:
        result = self._refresh(self.assessment_id)
        self.assertEqual(result["schema_version"], REFRESH_SCHEMA_VERSION)


# ---------------------------------------------------------------------------
# 6. TestProposalRefreshNoWriteOnNoop
# ---------------------------------------------------------------------------


class TestProposalRefreshNoWriteOnNoop(_RefreshBase):
    """A no-op run must not write any new action_proposal rows."""

    def setUp(self) -> None:
        super().setUp()
        self.assessment_id = self._commit_assessment()

    def test_row_count_unchanged_on_noop(self) -> None:
        # First run: seeds proposals
        r1 = self._refresh(self.assessment_id)
        count_after_first = len(
            self.proposal_repo.list_by_assessment(self.SESSION_ID, self.assessment_id)
        )

        # Second run: noop
        r2 = self._refresh(self.assessment_id)
        count_after_second = len(
            self.proposal_repo.list_by_assessment(self.SESSION_ID, self.assessment_id)
        )

        self.assertTrue(r2["noop"])
        self.assertEqual(count_after_first, count_after_second)


# ---------------------------------------------------------------------------
# 7. TestProposalPriorityAxesTotal
# ---------------------------------------------------------------------------


class TestProposalPriorityAxesTotal(_RefreshBase):
    """Every committed proposal has all 4 priority_axes dimensions."""

    _REQUIRED_AXES = {"information_gain", "execution_cost", "urgency", "expected_impact"}

    def setUp(self) -> None:
        super().setUp()
        self.assessment_id = self._commit_assessment()
        self._refresh(self.assessment_id)

    def test_all_axes_present(self) -> None:
        proposals = self.proposal_repo.list_by_assessment(self.SESSION_ID, self.assessment_id)
        for proposal in proposals:
            axes = proposal.get("priority_axes_json") or {}
            self.assertEqual(
                set(axes.keys()),
                self._REQUIRED_AXES,
                msg=f"proposal {proposal['action_proposal_id']} missing axes",
            )

    def test_no_axis_is_null(self) -> None:
        proposals = self.proposal_repo.list_by_assessment(self.SESSION_ID, self.assessment_id)
        for proposal in proposals:
            axes = proposal.get("priority_axes_json") or {}
            for axis_name, axis_val in axes.items():
                self.assertIsNotNone(
                    axis_val,
                    msg=f"axis {axis_name!r} is null in {proposal['action_proposal_id']}",
                )


# ---------------------------------------------------------------------------
# 8. TestProposalNoRewriteJudgment
# ---------------------------------------------------------------------------


class TestProposalNoRewriteJudgment(_RefreshBase):
    """Running proposal refresh must not mutate the assessment row."""

    def setUp(self) -> None:
        super().setUp()
        self.assessment_id = self._commit_assessment()
        self.assessment_before = self.assessment_repo.get(self.assessment_id)

    def test_assessment_status_unchanged(self) -> None:
        self._refresh(self.assessment_id)
        assessment_after = self.assessment_repo.get(self.assessment_id)
        self.assertEqual(self.assessment_before["status"], assessment_after["status"])

    def test_assessment_confidence_unchanged(self) -> None:
        self._refresh(self.assessment_id)
        assessment_after = self.assessment_repo.get(self.assessment_id)
        self.assertEqual(
            self.assessment_before["confidence_grade"],
            assessment_after["confidence_grade"],
        )

    def test_assessment_gap_memberships_unchanged(self) -> None:
        self._refresh(self.assessment_id)
        assessment_after = self.assessment_repo.get(self.assessment_id)
        self.assertEqual(
            self.assessment_before.get("gap_memberships_json"),
            assessment_after.get("gap_memberships_json"),
        )


# ---------------------------------------------------------------------------
# 9. TestPublishReadyBundleAssembly
# ---------------------------------------------------------------------------


class TestPublishReadyBundleAssembly(_RefreshBase):
    """Bundle assembles all required fields and is internally consistent."""

    def setUp(self) -> None:
        super().setUp()
        assessment_id = self._commit_assessment()
        self._refresh(assessment_id)

    def test_bundle_session_id(self) -> None:
        bundle = self._bundle()
        self.assertEqual(bundle["session_id"], self.SESSION_ID)

    def test_bundle_proposition_id(self) -> None:
        bundle = self._bundle()
        self.assertEqual(bundle["proposition_id"], self.PROP_ID)

    def test_bundle_proposition_present(self) -> None:
        bundle = self._bundle()
        self.assertIsNotNone(bundle["proposition"])
        self.assertEqual(bundle["proposition"]["proposition_id"], self.PROP_ID)

    def test_bundle_latest_assessment_present(self) -> None:
        bundle = self._bundle()
        self.assertIsNotNone(bundle["latest_assessment"])
        self.assertEqual(bundle["latest_assessment"]["proposition_id"], self.PROP_ID)

    def test_bundle_live_closure_keys(self) -> None:
        bundle = self._bundle()
        closure = bundle["live_closure"]
        self.assertIn("supporting_findings", closure)
        self.assertIn("opposing_findings", closure)
        self.assertIn("open_gaps", closure)
        self.assertIn("applied_inference_records", closure)

    def test_bundle_action_proposals_list(self) -> None:
        bundle = self._bundle()
        self.assertIsInstance(bundle["action_proposals"], list)

    def test_bundle_schema_version(self) -> None:
        bundle = self._bundle()
        self.assertEqual(bundle["schema_version"], BUNDLE_SCHEMA_VERSION)

    def test_bundle_proposals_match_assessment(self) -> None:
        """Proposals in bundle all reference the latest assessment."""
        bundle = self._bundle()
        assessment_id = bundle["latest_assessment"]["assessment_id"]
        for proposal in bundle["action_proposals"]:
            ref = proposal.get("primary_assessment_ref_json") or {}
            self.assertEqual(ref.get("assessment_id"), assessment_id)

    def test_bundle_inference_records_match_assessment(self) -> None:
        bundle = self._bundle()
        assessment_id = bundle["latest_assessment"]["assessment_id"]
        for rec in bundle["live_closure"]["applied_inference_records"]:
            self.assertEqual(rec["assessment_id"], assessment_id)


# ---------------------------------------------------------------------------
# 10. TestPublishReadyBundleNullAssessment
# ---------------------------------------------------------------------------


class TestPublishReadyBundleNullAssessment(_RefreshBase):
    """No committed latest_assessment → assemble_publish_ready_bundle raises."""

    def test_raises_without_assessment(self) -> None:
        # Proposition exists but no recompute has been run
        with self.assertRaises(ValueError):
            self._bundle()


# ---------------------------------------------------------------------------
# 11. TestProposalContextRequired
# ---------------------------------------------------------------------------


class TestProposalContextRequired(_RefreshBase):
    """Empty policy_profile raises ValueError."""

    def setUp(self) -> None:
        super().setUp()
        self.assessment_id = self._commit_assessment()

    def test_raises_empty_policy_profile(self) -> None:
        with self.assertRaises(ValueError):
            run_action_proposal_refresh(
                session_id=self.SESSION_ID,
                proposition_id=self.PROP_ID,
                latest_assessment_id=self.assessment_id,
                proposal_context={"session_goal": None, "risk_budget": None, "policy_profile": ""},
                proposal_repo=self.proposal_repo,
                assessment_repo=self.assessment_repo,
                gap_repo=self.gap_repo,
            )

    def test_raises_missing_policy_profile(self) -> None:
        with self.assertRaises(ValueError):
            run_action_proposal_refresh(
                session_id=self.SESSION_ID,
                proposition_id=self.PROP_ID,
                latest_assessment_id=self.assessment_id,
                proposal_context={"session_goal": None, "risk_budget": None},
                proposal_repo=self.proposal_repo,
                assessment_repo=self.assessment_repo,
                gap_repo=self.gap_repo,
            )


# ---------------------------------------------------------------------------
# 12. TestMultiplePropositionIsolation
# ---------------------------------------------------------------------------


class TestMultiplePropositionIsolation(_RefreshBase):
    """Refreshing prop A must not write proposals tagged to prop B."""

    PROP_B_ID = "prop_002"

    def setUp(self) -> None:
        super().setUp()
        _insert_proposition(
            self.store,
            proposition_id=self.PROP_B_ID,
            identity_key="ik_002",
        )
        self.assessment_id_a = self._commit_assessment(
            proposition_id=self.PROP_ID,
        )
        self.assessment_id_b = self._commit_assessment(
            proposition_id=self.PROP_B_ID,
        )

    def test_refresh_a_does_not_write_b_proposals(self) -> None:
        self._refresh(self.assessment_id_a, proposition_id=self.PROP_ID)

        # Proposals for prop B assessment should be empty (not touched)
        b_proposals = self.proposal_repo.list_by_assessment(self.SESSION_ID, self.assessment_id_b)
        self.assertEqual(b_proposals, [])

    def test_refresh_b_does_not_overwrite_a_proposals(self) -> None:
        self._refresh(self.assessment_id_a, proposition_id=self.PROP_ID)
        a_proposals_before = self.proposal_repo.list_by_assessment(
            self.SESSION_ID, self.assessment_id_a
        )

        self._refresh(self.assessment_id_b, proposition_id=self.PROP_B_ID)
        a_proposals_after = self.proposal_repo.list_by_assessment(
            self.SESSION_ID, self.assessment_id_a
        )

        self.assertEqual(
            [p["action_proposal_id"] for p in a_proposals_before],
            [p["action_proposal_id"] for p in a_proposals_after],
        )

    def test_proposal_target_proposition_ref_correct(self) -> None:
        self._refresh(self.assessment_id_a, proposition_id=self.PROP_ID)
        proposals = self.proposal_repo.list_by_assessment(self.SESSION_ID, self.assessment_id_a)
        for p in proposals:
            ref = p.get("target_proposition_ref_json") or {}
            self.assertEqual(ref.get("proposition_id"), self.PROP_ID)


# ---------------------------------------------------------------------------
# 13. TestProposalRefreshSessionOwnership
# ---------------------------------------------------------------------------


class TestProposalRefreshSessionOwnership(_RefreshBase):
    """Guards on assessment ownership are enforced."""

    OTHER_SESSION = "sess_other"

    def setUp(self) -> None:
        super().setUp()
        # Insert a second session and a proposition + assessment in it
        self.store.execute(
            "INSERT INTO sessions (session_id, goal, constraints_json, budget_json, status) "
            "VALUES (?, ?, ?, ?, ?)",
            [self.OTHER_SESSION, "other goal", "{}", "{}", "open"],
        )
        _insert_artifact(self.store, artifact_id="art_other", session_id=self.OTHER_SESSION)
        _insert_proposition(
            self.store,
            proposition_id="prop_other",
            session_id=self.OTHER_SESSION,
            identity_key="ik_other",
        )
        # Commit an assessment in the OTHER session
        self.other_assessment_id = _run_recompute(
            self.store,
            proposition_id="prop_other",
            session_id=self.OTHER_SESSION,
        )

    def test_raises_when_assessment_belongs_to_different_session(self) -> None:
        """Passing an assessment from a different session must raise ValueError."""
        with self.assertRaises(ValueError):
            run_action_proposal_refresh(
                session_id=self.SESSION_ID,  # sess_001
                proposition_id="prop_other",
                latest_assessment_id=self.other_assessment_id,  # belongs to sess_other
                proposal_context=_DEFAULT_PROPOSAL_CONTEXT,
                proposal_repo=self.proposal_repo,
                assessment_repo=self.assessment_repo,
                gap_repo=self.gap_repo,
            )

    def test_raises_when_assessment_belongs_to_different_proposition(self) -> None:
        """Passing an assessment_id for a different proposition must raise ValueError."""
        # Commit an assessment in the default session under PROP_ID
        assessment_id_a = self._commit_assessment(
            proposition_id=self.PROP_ID,
        )
        # Use assessment_id_a but claim it belongs to prop_other (mismatch)
        with self.assertRaises(ValueError):
            run_action_proposal_refresh(
                session_id=self.SESSION_ID,
                proposition_id="prop_002",  # does not match assessment's proposition_id
                latest_assessment_id=assessment_id_a,
                proposal_context=_DEFAULT_PROPOSAL_CONTEXT,
                proposal_repo=self.proposal_repo,
                assessment_repo=self.assessment_repo,
                gap_repo=self.gap_repo,
            )
