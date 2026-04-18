"""Tests for the assessment recompute runtime (Phase 4f-2).

Test domains:
 1. TestFirstSnapshot             — first recompute creates a snapshot
 2. TestNoOpOnSameInputs          — identical inputs → created=False
 3. TestSupersedingOnDiff         — changed output → new superseding snapshot
 4. TestImmutableSnapshots        — old snapshot fields unchanged after supersede
 5. TestSnapshotSeqMonotonic      — snapshot_seq increments correctly
 6. TestNoIsLatestFlag            — no is_latest mutation; latest resolved via get_latest()
 7. TestSupersededChain           — supersedes_assessment_id chain is linear
 8. TestGapOpenOnPreconditionMiss — empty findings → gap opened (missing_rule_precondition)
 9. TestGapResolveOnPreconditionHit — adding findings resolves the gap
10. TestGapKeepAcrossSnapshots    — persistent precondition miss → no-op (gap kept open)
11. TestGapReopenCreatesNewGapId  — resolve then re-miss → new gap_id
12. TestInferenceRecordsCreated   — committed snapshot has inference records
13. TestInferenceRecordFkBinding  — all records' assessment_id matches the committed assessment
14. TestCandidateDiscardNoRecords — no-op run does NOT write inference_records
15. TestRecomputeResultTypedDict  — result shape and schema_version constant
16. TestConfidenceGrade           — very_low on insufficient; low on supported
17. TestConfidenceRationaleFields — all 4 required rationale dimensions present
18. TestGapMembershipBlocking     — gap opened by precondition miss has blocking=True
19. TestStatusResolutionDirectional — delta finding + change_assessment → supported
20. TestMultiplePropositionsIsolation — recompute on prop A doesn't affect prop B
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC
from pathlib import Path
from typing import Any

from app.evidence_engine.assessment_evaluation_context import build_assessment_evaluation_context
from app.evidence_engine.assessment_recompute import (
    RECOMPUTE_SCHEMA_VERSION,
    AssessmentRecomputeResult,
    make_assessment_id,
    recompute_proposition_assessment,
)
from app.storage.evidence_repositories import (
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
# Low-level insert helpers
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
# Base test class
# ---------------------------------------------------------------------------


class _RecomputeBase(unittest.TestCase):
    """Fresh store + repositories; default session/artifact/proposition."""

    SESSION_ID = "sess_001"
    PROP_ID = "prop_001"

    def setUp(self) -> None:
        self.store = _make_store()
        _insert_session(self.store)
        _insert_artifact(self.store)
        self.finding_repo = FindingRepository(self.store)
        self.proposition_repo = PropositionRepository(self.store)
        self.assessment_repo = AssessmentRepository(self.store)
        self.gap_repo = EvidenceGapRepository(self.store)
        self.ir_repo = InferenceRecordRepository(self.store)
        # Insert a default change_assessment proposition
        _insert_proposition(self.store, proposition_id=self.PROP_ID)

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def _insert_delta_finding(self, finding_id: str = "fnd_delta_001") -> None:
        self.finding_repo.create(_make_finding_row(finding_id, finding_type="delta", metric="dau"))

    def _insert_compare_delta_finding(
        self,
        finding_id: str,
        *,
        comparability_status: str,
        issues: list[dict[str, Any]],
        include_calendar_alignment: bool = False,
        aligned_ratio: float = 1.0,
        unpaired_bucket_count: int = 0,
        comparability_warnings: list[str] | None = None,
        calendar_alignment_overrides: dict[str, Any] | None = None,
    ) -> None:
        row = _make_finding_row(finding_id, finding_type="delta", metric="dau")
        payload = {
            "delta_kind": "scalar_delta",
            "left_ref": {
                "artifact_id": "",
                "item_ref": {"collection": "value", "index": None, "key": None},
            },
            "right_ref": {
                "artifact_id": "",
                "item_ref": {"collection": "value", "index": None, "key": None},
            },
            "left_value": 10.0,
            "right_value": 8.0,
            "absolute_delta": 2.0,
            "relative_delta": 0.25,
            "direction": "increase",
            "presence": "both",
            "unit": None,
            "comparability": {"status": comparability_status, "issues": issues},
        }
        if include_calendar_alignment:
            coverage = {
                "aligned_bucket_count": 7,
                "unpaired_bucket_count": unpaired_bucket_count,
                "aligned_ratio": aligned_ratio,
            }
            bucket_pairing = [
                {
                    "current_bucket": "2026-04-01",
                    "baseline_bucket": "2025-04-01",
                    "pairing_reason": "holiday_cluster",
                }
            ]
            calendar_alignment_payload: dict[str, Any] = {
                "reuse_source": "observation_resolved_policy_summary",
                "policy_ref": "calendar_policy.holiday_yoy",
                "comparison_basis": "yoy",
                "resolved_calendar_source": "calendar.cn_holidays",
                "resolved_calendar_version": "2026.01",
                "resolved_baseline_generation_rule": "previous_year",
                "current_window": {"start": "2026-04-01", "end": "2026-04-07", "grain": "day"},
                "baseline_window": {"start": "2025-04-01", "end": "2025-04-07", "grain": "day"},
                "bucket_pairing": bucket_pairing,
                "coverage_summary": dict(coverage),
                "comparability_warnings": list(comparability_warnings or []),
                "left_coverage_summary": dict(coverage),
                "right_coverage_summary": dict(coverage),
                "effective_coverage_summary": dict(coverage),
            }
            if calendar_alignment_overrides:
                calendar_alignment_payload.update(calendar_alignment_overrides)
            payload["calendar_alignment"] = calendar_alignment_payload
        row["payload_json"] = json.dumps(payload)
        self.finding_repo.create(row)

    def _insert_test_result_finding(
        self,
        finding_id: str,
        *,
        comparability_status: str,
        issues: list[dict[str, Any]],
        include_calendar_alignment: bool = False,
        aligned_ratio: float = 1.0,
        unpaired_bucket_count: int = 0,
        comparability_warnings: list[str] | None = None,
        calendar_alignment_overrides: dict[str, Any] | None = None,
        payload_is_none: bool = False,
    ) -> None:
        row = _make_finding_row(finding_id, finding_type="test_result", metric="dau")
        if payload_is_none:
            row["payload_json"] = None
            self.finding_repo.create(row)
            return

        payload = {
            "left_ref": {
                "artifact_id": "art_left",
                "item_ref": {"collection": "result", "index": None, "key": None},
            },
            "right_ref": {
                "artifact_id": "art_right",
                "item_ref": {"collection": "result", "index": None, "key": None},
            },
            "method": "welch_t",
            "estimate_value": 2.0,
            "statistic_name": "t",
            "statistic_value": 3.0,
            "p_value": 0.01,
            "reject_null": True,
            "alpha": 0.05,
            "comparability": {"status": comparability_status, "issues": issues},
        }
        if include_calendar_alignment:
            coverage = {
                "aligned_bucket_count": 7,
                "unpaired_bucket_count": unpaired_bucket_count,
                "aligned_ratio": aligned_ratio,
            }
            bucket_pairing = [
                {
                    "current_bucket": "2026-04-01",
                    "baseline_bucket": "2025-04-01",
                    "pairing_reason": "holiday_cluster",
                }
            ]
            calendar_alignment_payload: dict[str, Any] = {
                "reuse_source": "observation_resolved_policy_summary",
                "policy_ref": "calendar_policy.holiday_yoy",
                "comparison_basis": "yoy",
                "resolved_calendar_source": "calendar.cn_holidays",
                "resolved_calendar_version": "2026.01",
                "resolved_baseline_generation_rule": "previous_year",
                "current_window": {"start": "2026-04-01", "end": "2026-04-07", "grain": "day"},
                "baseline_window": {"start": "2025-04-01", "end": "2025-04-07", "grain": "day"},
                "bucket_pairing": bucket_pairing,
                "coverage_summary": dict(coverage),
                "comparability_warnings": list(comparability_warnings or []),
                "left_coverage_summary": dict(coverage),
                "right_coverage_summary": dict(coverage),
                "effective_coverage_summary": dict(coverage),
            }
            if calendar_alignment_overrides:
                calendar_alignment_payload.update(calendar_alignment_overrides)
            payload["calendar_alignment"] = calendar_alignment_payload
        row["payload_json"] = json.dumps(payload)
        self.finding_repo.create(row)

    def _recompute(
        self,
        *,
        proposition_id: str | None = None,
        trigger_ids: list[str] | None = None,
    ) -> AssessmentRecomputeResult:
        pid = proposition_id or self.PROP_ID
        prop = self.proposition_repo.get(pid)
        assert prop is not None
        candidate_id = make_assessment_id(
            self.SESSION_ID, pid, self.assessment_repo.next_snapshot_seq(pid)
        )
        ctx = build_assessment_evaluation_context(
            session_id=self.SESSION_ID,
            proposition_id=pid,
            proposition=prop,
            candidate_assessment_id=candidate_id,
            trigger_finding_ids=trigger_ids or [],
            assessment_repo=self.assessment_repo,
            gap_repo=self.gap_repo,
            finding_repo=self.finding_repo,
            inference_record_repo=self.ir_repo,
        )
        return recompute_proposition_assessment(
            ctx=ctx,
            assessment_repo=self.assessment_repo,
            gap_repo=self.gap_repo,
            inference_record_repo=self.ir_repo,
            finding_repo=self.finding_repo,
        )


# ---------------------------------------------------------------------------
# 1. TestFirstSnapshot
# ---------------------------------------------------------------------------


class TestFirstSnapshot(_RecomputeBase):
    """First recompute creates an assessment snapshot in the DB."""

    def test_created_true(self) -> None:
        result = self._recompute()
        self.assertTrue(result["created"])

    def test_assessment_id_set(self) -> None:
        result = self._recompute()
        self.assertIsNotNone(result["assessment_id"])
        self.assertEqual(result["assessment_id"], result["candidate_assessment_id"])

    def test_snapshot_seq_one(self) -> None:
        result = self._recompute()
        self.assertEqual(result["snapshot_seq"], 1)

    def test_status_insufficient_without_findings(self) -> None:
        result = self._recompute()
        self.assertEqual(result["status"], "insufficient")

    def test_assessment_persisted(self) -> None:
        result = self._recompute()
        row = self.assessment_repo.get(result["assessment_id"])
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "insufficient")


# ---------------------------------------------------------------------------
# 2. TestNoOpOnSameInputs
# ---------------------------------------------------------------------------


class TestNoOpOnSameInputs(_RecomputeBase):
    """Re-running with identical candidate finding set → no-op."""

    def test_no_op_created_false(self) -> None:
        # First run commits a snapshot
        r1 = self._recompute()
        self.assertTrue(r1["created"])
        # Second run with same empty inputs (no findings)
        r2 = self._recompute()
        self.assertFalse(r2["created"])

    def test_no_op_assessment_id_none(self) -> None:
        self._recompute()
        r2 = self._recompute()
        self.assertIsNone(r2["assessment_id"])

    def test_no_op_status_none(self) -> None:
        self._recompute()
        r2 = self._recompute()
        self.assertIsNone(r2["status"])

    def test_no_new_snapshot_in_db(self) -> None:
        self._recompute()
        self._recompute()
        snapshots = self.assessment_repo.list_by_proposition(self.PROP_ID)
        self.assertEqual(len(snapshots), 1)

    def test_candidate_assessment_id_always_returned(self) -> None:
        self._recompute()
        r2 = self._recompute()
        expected = make_assessment_id(self.SESSION_ID, self.PROP_ID, 2)
        self.assertEqual(r2["candidate_assessment_id"], expected)


# ---------------------------------------------------------------------------
# 3. TestSupersedingOnDiff
# ---------------------------------------------------------------------------


class TestSupersedingOnDiff(_RecomputeBase):
    """Changed output → second snapshot with supersede link."""

    def setUp(self) -> None:
        super().setUp()
        self._insert_delta_finding()

    def test_second_snapshot_created(self) -> None:
        # First: no findings → insufficient
        self._recompute()
        # Second: with delta finding → supported
        r2 = self._recompute(trigger_ids=["fnd_delta_001"])
        self.assertTrue(r2["created"])

    def test_snapshot_seq_increments(self) -> None:
        self._recompute()
        r2 = self._recompute(trigger_ids=["fnd_delta_001"])
        self.assertEqual(r2["snapshot_seq"], 2)

    def test_supersedes_id_set(self) -> None:
        r1 = self._recompute()
        r2 = self._recompute(trigger_ids=["fnd_delta_001"])
        row2 = self.assessment_repo.get(r2["assessment_id"])
        self.assertEqual(row2["supersedes_assessment_id"], r1["assessment_id"])

    def test_second_status_supported(self) -> None:
        self._recompute()
        r2 = self._recompute(trigger_ids=["fnd_delta_001"])
        self.assertEqual(r2["status"], "supported")


# ---------------------------------------------------------------------------
# 4. TestImmutableSnapshots
# ---------------------------------------------------------------------------


class TestImmutableSnapshots(_RecomputeBase):
    """After a supersede, the original snapshot row is unchanged."""

    def setUp(self) -> None:
        super().setUp()
        self._insert_delta_finding()

    def test_original_status_preserved(self) -> None:
        r1 = self._recompute()
        self._recompute(trigger_ids=["fnd_delta_001"])
        row1 = self.assessment_repo.get(r1["assessment_id"])
        self.assertEqual(row1["status"], "insufficient")

    def test_original_snapshot_seq_preserved(self) -> None:
        r1 = self._recompute()
        self._recompute(trigger_ids=["fnd_delta_001"])
        row1 = self.assessment_repo.get(r1["assessment_id"])
        self.assertEqual(row1["snapshot_seq"], 1)

    def test_two_snapshots_in_db(self) -> None:
        self._recompute()
        self._recompute(trigger_ids=["fnd_delta_001"])
        snapshots = self.assessment_repo.list_by_proposition(self.PROP_ID)
        self.assertEqual(len(snapshots), 2)


# ---------------------------------------------------------------------------
# 5. TestSnapshotSeqMonotonic
# ---------------------------------------------------------------------------


class TestSnapshotSeqMonotonic(_RecomputeBase):
    """Three distinct snapshots have snapshot_seq 1, 2, 3."""

    def setUp(self) -> None:
        super().setUp()
        self._insert_delta_finding()
        # A second delta finding for creating a third distinct state
        self.finding_repo.create(
            _make_finding_row("fnd_delta_002", finding_type="delta", metric="dau")
        )

    def test_three_snapshots_monotonic(self) -> None:
        # 1: no triggers → insufficient (no findings in candidate set yet)
        r1 = self._recompute()
        # 2: first delta finding → supported with {fnd_delta_001}
        r2 = self._recompute(trigger_ids=["fnd_delta_001"])
        # 3: add second delta finding → supported with {fnd_delta_001, fnd_delta_002} → diff
        r3 = self._recompute(trigger_ids=["fnd_delta_001", "fnd_delta_002"])
        seqs = [r1["snapshot_seq"], r2["snapshot_seq"], r3["snapshot_seq"]]
        self.assertEqual(seqs, [1, 2, 3])


# ---------------------------------------------------------------------------
# 6. TestNoIsLatestFlag
# ---------------------------------------------------------------------------


class TestNoIsLatestFlag(_RecomputeBase):
    """The assessment row has no mutable is_latest flag; latest is by max snapshot_seq."""

    def test_row_has_no_is_latest_column(self) -> None:
        result = self._recompute()
        row = self.assessment_repo.get(result["assessment_id"])
        self.assertNotIn("is_latest", row)

    def test_get_latest_reflects_highest_seq(self) -> None:
        self._insert_delta_finding()
        r1 = self._recompute()
        r2 = self._recompute(trigger_ids=["fnd_delta_001"])
        latest = self.assessment_repo.get_latest(self.PROP_ID)
        self.assertEqual(latest["assessment_id"], r2["assessment_id"])

    def test_older_snapshot_not_modified(self) -> None:
        self._insert_delta_finding()
        r1 = self._recompute()
        self._recompute(trigger_ids=["fnd_delta_001"])
        # Re-read r1 and verify it is still unchanged
        row1_after = self.assessment_repo.get(r1["assessment_id"])
        self.assertEqual(row1_after["snapshot_seq"], 1)
        self.assertEqual(row1_after["status"], "insufficient")


# ---------------------------------------------------------------------------
# 7. TestSupersededChain
# ---------------------------------------------------------------------------


class TestSupersededChain(_RecomputeBase):
    """supersedes_assessment_id forms a linear chain."""

    def setUp(self) -> None:
        super().setUp()
        self._insert_delta_finding()
        self.finding_repo.create(
            _make_finding_row("fnd_delta_002", finding_type="delta", metric="dau")
        )

    def test_first_snapshot_supersedes_null(self) -> None:
        r1 = self._recompute()
        row1 = self.assessment_repo.get(r1["assessment_id"])
        self.assertIsNone(row1["supersedes_assessment_id"])

    def test_second_supersedes_first(self) -> None:
        r1 = self._recompute()
        r2 = self._recompute(trigger_ids=["fnd_delta_001"])
        row2 = self.assessment_repo.get(r2["assessment_id"])
        self.assertEqual(row2["supersedes_assessment_id"], r1["assessment_id"])

    def test_third_supersedes_second(self) -> None:
        self._recompute()
        r2 = self._recompute(trigger_ids=["fnd_delta_001"])
        # Add second finding → diff from snapshot 2 (different supporting_finding_ids)
        r3 = self._recompute(trigger_ids=["fnd_delta_001", "fnd_delta_002"])
        row3 = self.assessment_repo.get(r3["assessment_id"])
        self.assertEqual(row3["supersedes_assessment_id"], r2["assessment_id"])


# ---------------------------------------------------------------------------
# 8. TestGapOpenOnPreconditionMiss
# ---------------------------------------------------------------------------


class TestGapOpenOnPreconditionMiss(_RecomputeBase):
    """No candidate findings → a missing_rule_precondition gap is created."""

    def test_gap_created_in_db(self) -> None:
        result = self._recompute()
        self.assertTrue(result["created"])
        row = self.assessment_repo.get(result["assessment_id"])
        gap_memberships = row["gap_memberships_json"]
        self.assertEqual(len(gap_memberships), 1)
        gap_id = gap_memberships[0]["gap_ref"]["gap_id"]
        gap = self.gap_repo.get(gap_id)
        self.assertIsNotNone(gap)
        self.assertEqual(gap["status"], "open")

    def test_gap_kind_correct(self) -> None:
        result = self._recompute()
        row = self.assessment_repo.get(result["assessment_id"])
        gap_id = row["gap_memberships_json"][0]["gap_ref"]["gap_id"]
        gap = self.gap_repo.get(gap_id)
        self.assertEqual(gap["gap_kind"], "missing_rule_precondition")

    def test_gap_opened_by_valid_inference_record(self) -> None:
        result = self._recompute()
        row = self.assessment_repo.get(result["assessment_id"])
        gap_id = row["gap_memberships_json"][0]["gap_ref"]["gap_id"]
        gap = self.gap_repo.get(gap_id)
        irec_id = gap["opened_by_inference_record_id"]
        irec = self.ir_repo.get(irec_id)
        self.assertIsNotNone(irec)
        self.assertEqual(irec["rule_id"], "gap_management.v1.precondition_gaps")


# ---------------------------------------------------------------------------
# 9. TestGapResolveOnPreconditionHit
# ---------------------------------------------------------------------------


class TestGapResolveOnPreconditionHit(_RecomputeBase):
    """Adding a directional finding resolves the precondition gap."""

    def setUp(self) -> None:
        super().setUp()
        self._insert_delta_finding()

    def test_gap_resolved_after_findings_added(self) -> None:
        # First recompute: no findings → gap opened
        r1 = self._recompute()
        row1 = self.assessment_repo.get(r1["assessment_id"])
        gap_id = row1["gap_memberships_json"][0]["gap_ref"]["gap_id"]
        gap_before = self.gap_repo.get(gap_id)
        self.assertEqual(gap_before["status"], "open")
        # Second recompute: with finding → gap should resolve
        r2 = self._recompute(trigger_ids=["fnd_delta_001"])
        self.assertTrue(r2["created"])
        gap_after = self.gap_repo.get(gap_id)
        self.assertEqual(gap_after["status"], "resolved")

    def test_gap_resolved_by_set(self) -> None:
        r1 = self._recompute()
        row1 = self.assessment_repo.get(r1["assessment_id"])
        gap_id = row1["gap_memberships_json"][0]["gap_ref"]["gap_id"]
        self._recompute(trigger_ids=["fnd_delta_001"])
        gap_after = self.gap_repo.get(gap_id)
        self.assertIsNotNone(gap_after["resolved_by_inference_record_id"])

    def test_gap_not_in_new_snapshot_memberships(self) -> None:
        r1 = self._recompute()
        r2 = self._recompute(trigger_ids=["fnd_delta_001"])
        row2 = self.assessment_repo.get(r2["assessment_id"])
        # Resolved gap should not appear in gap_memberships of new snapshot
        self.assertEqual(len(row2["gap_memberships_json"]), 0)


# ---------------------------------------------------------------------------
# 10. TestGapKeepAcrossSnapshots
# ---------------------------------------------------------------------------


class TestGapKeepAcrossSnapshots(_RecomputeBase):
    """Persistent precondition miss with same inputs → no-op; gap stays open."""

    def test_gap_open_after_no_op(self) -> None:
        r1 = self._recompute()
        row1 = self.assessment_repo.get(r1["assessment_id"])
        gap_id = row1["gap_memberships_json"][0]["gap_ref"]["gap_id"]
        # Second run (same inputs, no findings) → no-op
        r2 = self._recompute()
        self.assertFalse(r2["created"])
        # Gap must still be open
        gap = self.gap_repo.get(gap_id)
        self.assertEqual(gap["status"], "open")

    def test_only_one_snapshot_exists(self) -> None:
        self._recompute()
        self._recompute()
        snapshots = self.assessment_repo.list_by_proposition(self.PROP_ID)
        self.assertEqual(len(snapshots), 1)


# ---------------------------------------------------------------------------
# 11. TestGapReopenCreatesNewGapId
# ---------------------------------------------------------------------------


class TestGapReopenCreatesNewGapId(_RecomputeBase):
    """After resolve + re-miss, a new gap_id is created (not reusing the resolved gap).

    Carry-forward prevents a real recompute run from going back to "miss" once
    a finding has been committed.  We simulate the re-miss scenario by manually
    constructing a context with empty candidate_finding_ids and no open gaps —
    this isolates the gap-ID-uniqueness contract from the context-building logic.
    """

    def setUp(self) -> None:
        super().setUp()
        self._insert_delta_finding()

    def _recompute_with_explicit_ctx(
        self,
        candidate_finding_ids: list[str],
        open_gap_ids: list[str],
        current_latest_assessment_id: str | None,
        prior_assessment_ids: list[str],
    ) -> AssessmentRecomputeResult:
        """Call recompute with a manually-assembled context (bypasses carry-forward)."""
        from app.evidence_engine.assessment_evaluation_context import (
            EVALUATION_CONTEXT_SCHEMA_VERSION,
            AssessmentEvaluationContext,
        )

        prop = self.proposition_repo.get(self.PROP_ID)
        candidate_id = make_assessment_id(
            self.SESSION_ID, self.PROP_ID, self.assessment_repo.next_snapshot_seq(self.PROP_ID)
        )
        ctx: AssessmentEvaluationContext = {
            "session_id": self.SESSION_ID,
            "proposition": prop,
            "assessment_type": prop["assessment_anchor_json"]["assessment_type"],
            "candidate_assessment_id": candidate_id,
            "current_latest_assessment_id": current_latest_assessment_id,
            "prior_assessment_ids": prior_assessment_ids,
            "open_gap_ids": open_gap_ids,
            "resolved_seed_finding_ids": [],
            "trigger_finding_ids": [],
            "candidate_finding_ids": candidate_finding_ids,
            "schema_version": EVALUATION_CONTEXT_SCHEMA_VERSION,
        }
        return recompute_proposition_assessment(
            ctx=ctx,
            assessment_repo=self.assessment_repo,
            gap_repo=self.gap_repo,
            inference_record_repo=self.ir_repo,
            finding_repo=self.finding_repo,
        )

    def test_reopen_creates_new_gap_id(self) -> None:
        # Run 1: no findings → gap_A opened (snapshot_seq=1)
        r1 = self._recompute_with_explicit_ctx(
            candidate_finding_ids=[],
            open_gap_ids=[],
            current_latest_assessment_id=None,
            prior_assessment_ids=[],
        )
        self.assertTrue(r1["created"])
        row1 = self.assessment_repo.get(r1["assessment_id"])
        gap_id_a = row1["gap_memberships_json"][0]["gap_ref"]["gap_id"]
        gap_a = self.gap_repo.get(gap_id_a)
        self.assertEqual(gap_a["status"], "open")

        # Simulate gap resolved: directly resolve gap_A by marking it resolved
        # using one of the inference records from run 1 as the resolver.
        resolver_irec_id = row1["applied_inference_record_ids_json"][0]
        from datetime import datetime

        self.gap_repo.resolve(
            gap_id_a,
            resolved_by_inference_record_id=resolver_irec_id,
            resolved_at=datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        gap_a_after = self.gap_repo.get(gap_id_a)
        self.assertEqual(gap_a_after["status"], "resolved")

        # Run 2: same proposition, no findings, no open gaps (gap_A was resolved)
        # gap_B is opened with a different gap_id because candidate_id for seq=2 differs from seq=1
        r2 = self._recompute_with_explicit_ctx(
            candidate_finding_ids=[],
            open_gap_ids=[],  # gap_A resolved → not in open_gap_ids
            current_latest_assessment_id=r1["assessment_id"],
            prior_assessment_ids=[r1["assessment_id"]],
        )
        self.assertTrue(r2["created"])
        row2 = self.assessment_repo.get(r2["assessment_id"])
        self.assertEqual(len(row2["gap_memberships_json"]), 1)
        gap_id_b = row2["gap_memberships_json"][0]["gap_ref"]["gap_id"]

        self.assertNotEqual(gap_id_a, gap_id_b)

    def test_old_gap_stays_resolved_after_reopen(self) -> None:
        """Resolved gap_A must remain resolved even after a new gap_B is opened."""
        from datetime import datetime

        r1 = self._recompute_with_explicit_ctx([], [], None, [])
        row1 = self.assessment_repo.get(r1["assessment_id"])
        gap_id_a = row1["gap_memberships_json"][0]["gap_ref"]["gap_id"]

        resolver_irec_id = row1["applied_inference_record_ids_json"][0]
        self.gap_repo.resolve(
            gap_id_a,
            resolved_by_inference_record_id=resolver_irec_id,
            resolved_at=datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

        self._recompute_with_explicit_ctx([], [], r1["assessment_id"], [r1["assessment_id"]])

        gap_a_final = self.gap_repo.get(gap_id_a)
        self.assertEqual(gap_a_final["status"], "resolved")


# ---------------------------------------------------------------------------
# 12. TestInferenceRecordsCreated
# ---------------------------------------------------------------------------


class TestInferenceRecordsCreated(_RecomputeBase):
    """Committed snapshot has inference records in applied_inference_record_ids."""

    def test_inference_records_exist_in_db(self) -> None:
        result = self._recompute()
        row = self.assessment_repo.get(result["assessment_id"])
        irec_ids = row["applied_inference_record_ids_json"]
        self.assertGreater(len(irec_ids), 0)
        for irec_id in irec_ids:
            irec = self.ir_repo.get(irec_id)
            self.assertIsNotNone(irec)

    def test_expected_rule_families_present(self) -> None:
        result = self._recompute()
        row = self.assessment_repo.get(result["assessment_id"])
        irec_ids = row["applied_inference_record_ids_json"]
        rule_ids = set()
        for irec_id in irec_ids:
            irec = self.ir_repo.get(irec_id)
            rule_ids.add(irec["rule_id"])
        # v1 must include all 9 rule families
        expected_prefixes = {
            "precondition_gate",
            "quality_gate",
            "comparability_gate",
            "support_evidence",
            "oppose_evidence",
            "status_resolution",
            "gap_management",
            "confidence_shaping",
            "assessment_transition",
        }
        actual_prefixes = {r.split(".")[0] for r in rule_ids}
        self.assertTrue(expected_prefixes.issubset(actual_prefixes))


# ---------------------------------------------------------------------------
# 13. TestInferenceRecordFkBinding
# ---------------------------------------------------------------------------


class TestInferenceRecordFkBinding(_RecomputeBase):
    """All inference records' assessment_id matches the committed assessment."""

    def test_all_irecs_bound_to_correct_assessment(self) -> None:
        result = self._recompute()
        row = self.assessment_repo.get(result["assessment_id"])
        irec_ids = row["applied_inference_record_ids_json"]
        for irec_id in irec_ids:
            irec = self.ir_repo.get(irec_id)
            self.assertEqual(irec["assessment_id"], result["assessment_id"])

    def test_irec_proposition_id_correct(self) -> None:
        result = self._recompute()
        row = self.assessment_repo.get(result["assessment_id"])
        irec_ids = row["applied_inference_record_ids_json"]
        for irec_id in irec_ids:
            irec = self.ir_repo.get(irec_id)
            self.assertEqual(irec["proposition_id"], self.PROP_ID)


# ---------------------------------------------------------------------------
# 14. TestCandidateDiscardNoRecords
# ---------------------------------------------------------------------------


class TestCandidateDiscardNoRecords(_RecomputeBase):
    """A no-op run must not commit inference_records to the DB."""

    def test_no_irecs_for_no_op_candidate(self) -> None:
        # First run: commit
        self._recompute()
        # Second run: no-op
        r2 = self._recompute()
        self.assertFalse(r2["created"])
        # No inference records should reference the no-op candidate_id
        rows = self.store.query_rows(
            "SELECT * FROM inference_records WHERE assessment_id = ?",
            [r2["candidate_assessment_id"]],
        )
        self.assertEqual(len(rows), 0)

    def test_total_irec_count_unchanged_after_no_op(self) -> None:
        r1 = self._recompute()
        row1 = self.assessment_repo.get(r1["assessment_id"])
        count_before = len(row1["applied_inference_record_ids_json"])
        self._recompute()
        total_irecs = len(self.store.query_rows("SELECT * FROM inference_records", []))
        self.assertEqual(total_irecs, count_before)


# ---------------------------------------------------------------------------
# 15. TestRecomputeResultTypedDict
# ---------------------------------------------------------------------------


class TestRecomputeResultTypedDict(_RecomputeBase):
    """Result TypedDict has the correct shape and schema_version."""

    def test_schema_version_constant(self) -> None:
        result = self._recompute()
        self.assertEqual(result["schema_version"], RECOMPUTE_SCHEMA_VERSION)
        self.assertEqual(RECOMPUTE_SCHEMA_VERSION, "assessment_recompute_result.v1")

    def test_result_has_required_keys(self) -> None:
        result = self._recompute()
        for key in (
            "assessment_id",
            "created",
            "snapshot_seq",
            "status",
            "candidate_assessment_id",
            "schema_version",
        ):
            self.assertIn(key, result)

    def test_no_op_result_shape(self) -> None:
        self._recompute()
        r2 = self._recompute()
        self.assertFalse(r2["created"])
        self.assertIsNone(r2["assessment_id"])
        self.assertIsNone(r2["snapshot_seq"])
        self.assertIsNone(r2["status"])
        self.assertIsNotNone(r2["candidate_assessment_id"])


# ---------------------------------------------------------------------------
# 16. TestConfidenceGrade
# ---------------------------------------------------------------------------


class TestConfidenceGrade(_RecomputeBase):
    """Confidence grade follows global guardrails."""

    def test_insufficient_very_low_grade(self) -> None:
        result = self._recompute()
        row = self.assessment_repo.get(result["assessment_id"])
        self.assertEqual(row["confidence_grade"], "very_low")

    def test_supported_at_least_low_grade(self) -> None:
        self._insert_delta_finding()
        self._recompute()  # first: insufficient
        r2 = self._recompute(trigger_ids=["fnd_delta_001"])
        row2 = self.assessment_repo.get(r2["assessment_id"])
        grade_order = ["very_low", "low", "medium", "high", "very_high"]
        self.assertGreaterEqual(
            grade_order.index(row2["confidence_grade"]),
            grade_order.index("low"),
        )


# ---------------------------------------------------------------------------
# 17. TestConfidenceRationaleFields
# ---------------------------------------------------------------------------


class TestConfidenceRationaleFields(_RecomputeBase):
    """Confidence rationale must have all 4 required structural dimensions."""

    def _get_rationale(self, assessment_id: str) -> dict:
        row = self.assessment_repo.get(assessment_id)
        return row["confidence_rationale_json"]

    def test_all_four_dimensions_present(self) -> None:
        result = self._recompute()
        rationale = self._get_rationale(result["assessment_id"])
        for dim in (
            "evidence_sufficiency",
            "evidence_consistency",
            "rule_coverage",
            "data_quality_impact",
        ):
            self.assertIn(dim, rationale)

    def test_rationale_notes_list(self) -> None:
        result = self._recompute()
        rationale = self._get_rationale(result["assessment_id"])
        self.assertIsInstance(rationale.get("rationale_notes"), list)

    def test_evidence_sufficiency_very_weak_without_findings(self) -> None:
        result = self._recompute()
        rationale = self._get_rationale(result["assessment_id"])
        self.assertEqual(rationale["evidence_sufficiency"], "very_weak")


# ---------------------------------------------------------------------------
# 18. TestGapMembershipBlocking
# ---------------------------------------------------------------------------


class TestGapMembershipBlocking(_RecomputeBase):
    """Gap opened by precondition miss has blocking=True, severity=critical."""

    def test_blocking_true(self) -> None:
        result = self._recompute()
        row = self.assessment_repo.get(result["assessment_id"])
        memberships = row["gap_memberships_json"]
        self.assertEqual(len(memberships), 1)
        self.assertTrue(memberships[0]["blocking"])

    def test_severity_critical(self) -> None:
        result = self._recompute()
        row = self.assessment_repo.get(result["assessment_id"])
        memberships = row["gap_memberships_json"]
        self.assertEqual(memberships[0]["severity"], "critical")

    def test_gap_ref_has_gap_id_and_proposition_id(self) -> None:
        result = self._recompute()
        row = self.assessment_repo.get(result["assessment_id"])
        gap_ref = row["gap_memberships_json"][0]["gap_ref"]
        self.assertIn("gap_id", gap_ref)
        self.assertEqual(gap_ref["proposition_id"], self.PROP_ID)


# ---------------------------------------------------------------------------
# 19. TestStatusResolutionDirectional
# ---------------------------------------------------------------------------


class TestStatusResolutionDirectional(_RecomputeBase):
    """A delta finding + change_assessment proposition → supported."""

    def setUp(self) -> None:
        super().setUp()
        self._insert_delta_finding()

    def test_supported_with_delta_finding(self) -> None:
        self._recompute()  # first: insufficient (no triggers yet)
        r2 = self._recompute(trigger_ids=["fnd_delta_001"])
        self.assertEqual(r2["status"], "supported")

    def test_supporting_finding_ids_populated(self) -> None:
        self._recompute()
        r2 = self._recompute(trigger_ids=["fnd_delta_001"])
        row2 = self.assessment_repo.get(r2["assessment_id"])
        self.assertIn("fnd_delta_001", row2["supporting_finding_ids_json"])

    def test_opposing_finding_ids_empty(self) -> None:
        self._recompute()
        r2 = self._recompute(trigger_ids=["fnd_delta_001"])
        row2 = self.assessment_repo.get(r2["assessment_id"])
        self.assertEqual(row2["opposing_finding_ids_json"], [])

    def test_no_gap_when_supported(self) -> None:
        self._recompute()
        r2 = self._recompute(trigger_ids=["fnd_delta_001"])
        row2 = self.assessment_repo.get(r2["assessment_id"])
        self.assertEqual(row2["gap_memberships_json"], [])


class TestComparabilityGateIntegration(_RecomputeBase):
    """Comparability gate consumes finding-level comparability summaries."""

    def _recompute_with_explicit_ctx(
        self,
        candidate_finding_ids: list[str],
        open_gap_ids: list[str],
        current_latest_assessment_id: str | None,
        prior_assessment_ids: list[str],
    ) -> AssessmentRecomputeResult:
        from app.evidence_engine.assessment_evaluation_context import (
            EVALUATION_CONTEXT_SCHEMA_VERSION,
            AssessmentEvaluationContext,
        )

        prop = self.proposition_repo.get(self.PROP_ID)
        assert prop is not None
        candidate_id = make_assessment_id(
            self.SESSION_ID, self.PROP_ID, self.assessment_repo.next_snapshot_seq(self.PROP_ID)
        )
        ctx: AssessmentEvaluationContext = {
            "session_id": self.SESSION_ID,
            "proposition": prop,
            "assessment_type": prop["assessment_anchor_json"]["assessment_type"],
            "candidate_assessment_id": candidate_id,
            "current_latest_assessment_id": current_latest_assessment_id,
            "prior_assessment_ids": prior_assessment_ids,
            "open_gap_ids": open_gap_ids,
            "resolved_seed_finding_ids": [],
            "trigger_finding_ids": [],
            "candidate_finding_ids": candidate_finding_ids,
            "schema_version": EVALUATION_CONTEXT_SCHEMA_VERSION,
        }
        return recompute_proposition_assessment(
            ctx=ctx,
            assessment_repo=self.assessment_repo,
            gap_repo=self.gap_repo,
            inference_record_repo=self.ir_repo,
            finding_repo=self.finding_repo,
        )

    def test_needs_attention_alignment_records_partial_gate(self) -> None:
        self._insert_compare_delta_finding(
            "fnd_cmp_partial",
            comparability_status="needs_attention",
            issues=[
                {
                    "code": "alignment_coverage_insufficient",
                    "severity": "warning",
                    "message": "coverage warning",
                }
            ],
            include_calendar_alignment=True,
            aligned_ratio=0.8,
            unpaired_bucket_count=1,
        )
        result = self._recompute(trigger_ids=["fnd_cmp_partial"])
        row = self.assessment_repo.get(result["assessment_id"])
        compare_irec_id = next(
            irec_id
            for irec_id in row["applied_inference_record_ids_json"]
            if self.ir_repo.get(irec_id)["rule_id"] == "comparability_gate.v1.baseline"
        )
        compare_irec = self.ir_repo.get(compare_irec_id)
        self.assertEqual(compare_irec["result"], "partial")
        self.assertEqual(compare_irec["input_finding_ids_json"], ["fnd_cmp_partial"])
        matched = compare_irec["justification_json"]["matched_conditions"]
        self.assertIn("comparability_requirement:baseline_calendar_policy_resolved:met", matched)
        self.assertIn("comparability_requirement:holiday_cluster_alignment_complete:met", matched)
        self.assertIn("comparability_requirement:event_cluster_alignment_complete:met", matched)
        self.assertIn("comparability_requirement:weekday_pairing_compatible:met", matched)
        self.assertIn("comparability_requirement:alignment_tie_breaker_resolved:met", matched)
        self.assertNotIn("comparability_requirement:calendar_coverage_sufficient:met", matched)
        self.assertIn("comparability_signal:window_alignment:needs_attention", matched)
        unmatched = compare_irec["justification_json"]["unmatched_conditions"]
        self.assertEqual(
            unmatched,
            ["comparability_requirement:calendar_coverage_sufficient:failed"],
        )

    def test_comparability_error_blocks_supported_status(self) -> None:
        self._insert_compare_delta_finding(
            "fnd_cmp_error",
            comparability_status="needs_attention",
            issues=[
                {
                    "code": "calendar_policy_mismatch",
                    "severity": "error",
                    "message": "fatal mismatch",
                }
            ],
        )
        result = self._recompute(trigger_ids=["fnd_cmp_error"])
        row = self.assessment_repo.get(result["assessment_id"])
        self.assertEqual(result["status"], "insufficient")
        resolution_irec_id = next(
            irec_id
            for irec_id in row["applied_inference_record_ids_json"]
            if self.ir_repo.get(irec_id)["rule_id"] == "status_resolution.v1.threshold_algorithm"
        )
        resolution_irec = self.ir_repo.get(resolution_irec_id)
        self.assertIn(
            "comparability_guardrail_blocked",
            resolution_irec["justification_json"]["matched_conditions"],
        )

    def test_missing_calendar_alignment_summary_fails_baseline_requirement(self) -> None:
        self._insert_compare_delta_finding(
            "fnd_cmp_missing_alignment",
            comparability_status="needs_attention",
            issues=[],
            include_calendar_alignment=False,
        )
        result = self._recompute(trigger_ids=["fnd_cmp_missing_alignment"])
        row = self.assessment_repo.get(result["assessment_id"])
        compare_irec_id = next(
            irec_id
            for irec_id in row["applied_inference_record_ids_json"]
            if self.ir_repo.get(irec_id)["rule_id"] == "comparability_gate.v1.baseline"
        )
        compare_irec = self.ir_repo.get(compare_irec_id)
        self.assertEqual(compare_irec["result"], "partial")
        self.assertIn(
            "comparability_requirement:baseline_calendar_policy_resolved:failed",
            compare_irec["justification_json"]["unmatched_conditions"],
        )

    def test_missing_baseline_generation_rule_fails_baseline_requirement(self) -> None:
        self._insert_compare_delta_finding(
            "fnd_cmp_missing_generation_rule",
            comparability_status="needs_attention",
            issues=[],
            include_calendar_alignment=True,
            calendar_alignment_overrides={"resolved_baseline_generation_rule": None},
        )
        result = self._recompute(trigger_ids=["fnd_cmp_missing_generation_rule"])
        row = self.assessment_repo.get(result["assessment_id"])
        compare_irec_id = next(
            irec_id
            for irec_id in row["applied_inference_record_ids_json"]
            if self.ir_repo.get(irec_id)["rule_id"] == "comparability_gate.v1.baseline"
        )
        compare_irec = self.ir_repo.get(compare_irec_id)
        self.assertIn(
            "comparability_requirement:baseline_calendar_policy_resolved:failed",
            compare_irec["justification_json"]["unmatched_conditions"],
        )

    def test_holiday_cluster_unmapped_blocks_gate(self) -> None:
        self._insert_compare_delta_finding(
            "fnd_cmp_holiday_unmapped",
            comparability_status="needs_attention",
            issues=[
                {
                    "code": "holiday_cluster_unmapped",
                    "severity": "error",
                    "message": "holiday mapping missing",
                }
            ],
            include_calendar_alignment=True,
        )
        result = self._recompute(trigger_ids=["fnd_cmp_holiday_unmapped"])
        row = self.assessment_repo.get(result["assessment_id"])
        compare_irec_id = next(
            irec_id
            for irec_id in row["applied_inference_record_ids_json"]
            if self.ir_repo.get(irec_id)["rule_id"] == "comparability_gate.v1.baseline"
        )
        compare_irec = self.ir_repo.get(compare_irec_id)
        self.assertEqual(compare_irec["result"], "miss")
        self.assertIn(
            "comparability_requirement:holiday_cluster_alignment_complete:failed",
            compare_irec["justification_json"]["unmatched_conditions"],
        )
        self.assertEqual(len(row["gap_memberships_json"]), 1)
        gap_id = row["gap_memberships_json"][0]["gap_ref"]["gap_id"]
        gap = self.gap_repo.get(gap_id)
        self.assertEqual(gap["gap_kind"], "comparability_risk")
        self.assertEqual(
            gap["missing_requirement_json"]["requirement_key"],
            "holiday_cluster_alignment_complete",
        )

    def test_event_cluster_unmapped_blocks_gate(self) -> None:
        self._insert_compare_delta_finding(
            "fnd_cmp_event_unmapped",
            comparability_status="needs_attention",
            issues=[
                {
                    "code": "event_cluster_unmapped",
                    "severity": "error",
                    "message": "event mapping missing",
                }
            ],
            include_calendar_alignment=True,
        )
        result = self._recompute(trigger_ids=["fnd_cmp_event_unmapped"])
        row = self.assessment_repo.get(result["assessment_id"])
        compare_irec_id = next(
            irec_id
            for irec_id in row["applied_inference_record_ids_json"]
            if self.ir_repo.get(irec_id)["rule_id"] == "comparability_gate.v1.baseline"
        )
        compare_irec = self.ir_repo.get(compare_irec_id)
        self.assertEqual(compare_irec["result"], "miss")
        self.assertIn(
            "comparability_requirement:event_cluster_alignment_complete:failed",
            compare_irec["justification_json"]["unmatched_conditions"],
        )

    def test_weekday_pairing_tie_fails_weekday_and_tie_breaker_requirements(self) -> None:
        self._insert_compare_delta_finding(
            "fnd_cmp_weekday_tie",
            comparability_status="needs_attention",
            issues=[
                {
                    "code": "weekday_pairing_tie",
                    "severity": "warning",
                    "message": "weekday tie remained",
                }
            ],
            include_calendar_alignment=True,
        )
        result = self._recompute(trigger_ids=["fnd_cmp_weekday_tie"])
        row = self.assessment_repo.get(result["assessment_id"])
        compare_irec_id = next(
            irec_id
            for irec_id in row["applied_inference_record_ids_json"]
            if self.ir_repo.get(irec_id)["rule_id"] == "comparability_gate.v1.baseline"
        )
        compare_irec = self.ir_repo.get(compare_irec_id)
        self.assertEqual(compare_irec["result"], "partial")
        unmatched = compare_irec["justification_json"]["unmatched_conditions"]
        self.assertIn(
            "comparability_requirement:weekday_pairing_compatible:failed",
            unmatched,
        )
        self.assertIn(
            "comparability_requirement:alignment_tie_breaker_resolved:failed",
            unmatched,
        )

    def test_test_result_finding_contributes_to_comparability_gate(self) -> None:
        proposition_id = "prop_test_hypothesis_001"
        _insert_proposition(
            self.store,
            proposition_id=proposition_id,
            assessment_type="test_hypothesis_assessment",
            proposition_type="test_hypothesis",
            metric="dau",
            identity_key="ik_test_hypothesis_001",
        )
        self._insert_test_result_finding(
            "fnd_test_cmp_partial",
            comparability_status="needs_attention",
            issues=[],
            include_calendar_alignment=True,
            aligned_ratio=0.8,
            unpaired_bucket_count=1,
        )
        result = self._recompute(
            proposition_id=proposition_id,
            trigger_ids=["fnd_test_cmp_partial"],
        )
        row = self.assessment_repo.get(result["assessment_id"])
        compare_irec_id = next(
            irec_id
            for irec_id in row["applied_inference_record_ids_json"]
            if self.ir_repo.get(irec_id)["rule_id"] == "comparability_gate.v1.baseline"
        )
        compare_irec = self.ir_repo.get(compare_irec_id)
        self.assertEqual(compare_irec["result"], "partial")
        self.assertEqual(compare_irec["input_finding_ids_json"], ["fnd_test_cmp_partial"])
        self.assertIn(
            "comparability_requirement:calendar_coverage_sufficient:failed",
            compare_irec["justification_json"]["unmatched_conditions"],
        )

    def test_non_numeric_coverage_fails_requirement(self) -> None:
        self._insert_compare_delta_finding(
            "fnd_cmp_bad_coverage",
            comparability_status="needs_attention",
            issues=[],
            include_calendar_alignment=True,
            calendar_alignment_overrides={
                "effective_coverage_summary": {
                    "aligned_bucket_count": 7,
                    "unpaired_bucket_count": 0,
                    "aligned_ratio": "1.0",
                }
            },
        )
        result = self._recompute(trigger_ids=["fnd_cmp_bad_coverage"])
        row = self.assessment_repo.get(result["assessment_id"])
        compare_irec_id = next(
            irec_id
            for irec_id in row["applied_inference_record_ids_json"]
            if self.ir_repo.get(irec_id)["rule_id"] == "comparability_gate.v1.baseline"
        )
        compare_irec = self.ir_repo.get(compare_irec_id)
        self.assertIn(
            "comparability_requirement:calendar_coverage_sufficient:failed",
            compare_irec["justification_json"]["unmatched_conditions"],
        )

    def test_needs_attention_with_empty_issues_partial_if_alignment_bad(self) -> None:
        self._insert_compare_delta_finding(
            "fnd_cmp_empty_issues",
            comparability_status="needs_attention",
            issues=[],
            include_calendar_alignment=True,
            aligned_ratio=0.999,
            unpaired_bucket_count=0,
        )
        result = self._recompute(trigger_ids=["fnd_cmp_empty_issues"])
        row = self.assessment_repo.get(result["assessment_id"])
        compare_irec_id = next(
            irec_id
            for irec_id in row["applied_inference_record_ids_json"]
            if self.ir_repo.get(irec_id)["rule_id"] == "comparability_gate.v1.baseline"
        )
        compare_irec = self.ir_repo.get(compare_irec_id)
        self.assertEqual(compare_irec["result"], "partial")
        self.assertIn(
            "comparability_signal:window_alignment:needs_attention",
            compare_irec["justification_json"]["matched_conditions"],
        )

    def test_complete_calendar_alignment_records_hit_and_comparable_signal(self) -> None:
        self._insert_compare_delta_finding(
            "fnd_cmp_full_success",
            comparability_status="comparable",
            issues=[],
            include_calendar_alignment=True,
            aligned_ratio=1.0,
            unpaired_bucket_count=0,
        )
        result = self._recompute(trigger_ids=["fnd_cmp_full_success"])
        row = self.assessment_repo.get(result["assessment_id"])
        compare_irec_id = next(
            irec_id
            for irec_id in row["applied_inference_record_ids_json"]
            if self.ir_repo.get(irec_id)["rule_id"] == "comparability_gate.v1.baseline"
        )
        compare_irec = self.ir_repo.get(compare_irec_id)
        self.assertEqual(compare_irec["result"], "hit")
        matched = compare_irec["justification_json"]["matched_conditions"]
        for requirement_key in (
            "baseline_calendar_policy_resolved",
            "holiday_cluster_alignment_complete",
            "event_cluster_alignment_complete",
            "weekday_pairing_compatible",
            "calendar_coverage_sufficient",
            "alignment_tie_breaker_resolved",
        ):
            self.assertIn(f"comparability_requirement:{requirement_key}:met", matched)
        self.assertIn("comparability_signal:window_alignment:comparable", matched)
        self.assertEqual(row["gap_memberships_json"], [])

    def test_comparability_gap_resolves_when_requirement_recovers(self) -> None:
        self._insert_compare_delta_finding(
            "fnd_cmp_gap_open",
            comparability_status="needs_attention",
            issues=[],
            include_calendar_alignment=True,
            aligned_ratio=0.8,
            unpaired_bucket_count=1,
        )
        first = self._recompute_with_explicit_ctx(
            candidate_finding_ids=["fnd_cmp_gap_open"],
            open_gap_ids=[],
            current_latest_assessment_id=None,
            prior_assessment_ids=[],
        )
        first_row = self.assessment_repo.get(first["assessment_id"])
        self.assertEqual(len(first_row["gap_memberships_json"]), 1)
        gap_id = first_row["gap_memberships_json"][0]["gap_ref"]["gap_id"]
        gap = self.gap_repo.get(gap_id)
        self.assertEqual(gap["status"], "open")

        self._insert_compare_delta_finding(
            "fnd_cmp_gap_resolve",
            comparability_status="comparable",
            issues=[],
            include_calendar_alignment=True,
            aligned_ratio=1.0,
            unpaired_bucket_count=0,
        )
        second = self._recompute_with_explicit_ctx(
            candidate_finding_ids=["fnd_cmp_gap_resolve"],
            open_gap_ids=[gap_id],
            current_latest_assessment_id=first["assessment_id"],
            prior_assessment_ids=[first["assessment_id"]],
        )
        second_row = self.assessment_repo.get(second["assessment_id"])
        compare_irec_id = next(
            irec_id
            for irec_id in second_row["applied_inference_record_ids_json"]
            if self.ir_repo.get(irec_id)["rule_id"] == "comparability_gate.v1.baseline"
        )
        compare_irec = self.ir_repo.get(compare_irec_id)
        self.assertIn(gap_id, compare_irec["resolved_gap_ids_json"])
        self.assertEqual(self.gap_repo.get(gap_id)["status"], "resolved")
        self.assertEqual(second_row["gap_memberships_json"], [])

    def test_missing_payload_json_is_skipped(self) -> None:
        self._insert_test_result_finding(
            "fnd_test_no_payload",
            comparability_status="needs_attention",
            issues=[],
            payload_is_none=True,
        )
        result = self._recompute(trigger_ids=["fnd_test_no_payload"])
        row = self.assessment_repo.get(result["assessment_id"])
        compare_irec_id = next(
            irec_id
            for irec_id in row["applied_inference_record_ids_json"]
            if self.ir_repo.get(irec_id)["rule_id"] == "comparability_gate.v1.baseline"
        )
        compare_irec = self.ir_repo.get(compare_irec_id)
        self.assertEqual(compare_irec["result"], "hit")
        self.assertEqual(compare_irec["input_finding_ids_json"], [])
        self.assertEqual(
            compare_irec["justification_json"]["notes"],
            ["no comparability-bearing finding inputs"],
        )


# ---------------------------------------------------------------------------
# 20. TestMultiplePropositionsIsolation
# ---------------------------------------------------------------------------


class TestMultiplePropositionsIsolation(_RecomputeBase):
    """Recomputing prop_A does not affect prop_B snapshots or gaps."""

    PROP_B = "prop_002"

    def setUp(self) -> None:
        super().setUp()
        _insert_proposition(
            self.store,
            proposition_id=self.PROP_B,
            assessment_type="anomaly_assessment",
            proposition_type="anomaly",
            metric="error_rate",
            identity_key="ik_002",
        )

    def test_prop_b_has_no_snapshots_after_prop_a_recompute(self) -> None:
        self._recompute(proposition_id=self.PROP_ID)
        snapshots_b = self.assessment_repo.list_by_proposition(self.PROP_B)
        self.assertEqual(len(snapshots_b), 0)

    def test_prop_a_has_no_snapshots_after_prop_b_recompute(self) -> None:
        self._recompute(proposition_id=self.PROP_B)
        snapshots_a = self.assessment_repo.list_by_proposition(self.PROP_ID)
        self.assertEqual(len(snapshots_a), 0)

    def test_independent_snapshot_seqs(self) -> None:
        ra = self._recompute(proposition_id=self.PROP_ID)
        rb = self._recompute(proposition_id=self.PROP_B)
        self.assertEqual(ra["snapshot_seq"], 1)
        self.assertEqual(rb["snapshot_seq"], 1)

    def test_gap_isolation(self) -> None:
        # Prop A gets a gap; prop B should have no gaps
        ra = self._recompute(proposition_id=self.PROP_ID)
        row_a = self.assessment_repo.get(ra["assessment_id"])
        # prop A has a gap (no findings → precondition miss)
        self.assertEqual(len(row_a["gap_memberships_json"]), 1)
        # Prop B has its own recompute
        rb = self._recompute(proposition_id=self.PROP_B)
        row_b = self.assessment_repo.get(rb["assessment_id"])
        # Prop B also has a gap (no findings), but it's a different gap object
        gap_ids_a = {m["gap_ref"]["gap_id"] for m in row_a["gap_memberships_json"]}
        gap_ids_b = {m["gap_ref"]["gap_id"] for m in row_b["gap_memberships_json"]}
        self.assertEqual(len(gap_ids_a & gap_ids_b), 0)  # no shared gap_ids


if __name__ == "__main__":
    unittest.main()
