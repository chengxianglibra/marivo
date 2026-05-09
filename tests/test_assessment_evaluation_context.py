"""Tests for the assessment evaluation context builder (Phase 4f-1).

Covers all 9 acceptance domains from the plan:

1.  TestContextBuilderBasic         — happy-path minimal construction
2.  TestPriorAssessmentLoad         — Phase 2: prior assessment ordering / latest
3.  TestOpenGapLoad                 — Phase 2: open vs resolved gap filtering
4.  TestTriggerNormalization        — Phase 4: dedup + sort
5.  TestCarryForward                — Phase 5: closure replay from latest assessment
6.  TestCompatibilityFilter         — Phase 6: family + subject filtering
7.  TestSubjectCompatibility        — Phase 6: subject non-conflict in isolation
8.  TestAuthoredFallback            — Phase 7: discovery fallback conditions
9.  TestCandidateSetStability       — Phase 8: dedup + sort + replay stability
10. TestSchemaVersionAndFields      — TypedDict shape / schema_version
11. TestValidation                  — ValueError on contract violation
12. TestHelperFunctions             — unit tests of _compatible_finding_types,
                                      _subject_compatible, _stable_dedup
"""

from __future__ import annotations

import json
import unittest
from typing import Any

from marivo.evidence_engine.assessment_evaluation_context import (
    EVALUATION_CONTEXT_SCHEMA_VERSION,
    AssessmentEvaluationContext,
    _compatible_finding_types,
    _stable_dedup,
    _subject_compatible,
    build_assessment_evaluation_context,
)
from marivo.storage.evidence_repositories import (
    AssessmentRepository,
    EvidenceGapRepository,
    FindingRepository,
    InferenceRecordRepository,
    PropositionRepository,
)
from marivo.storage.sqlite_metadata import SQLiteMetadataStore
from tests.shared_fixtures import make_temp_metadata_store

# ---------------------------------------------------------------------------
# Shared test-store factory
# ---------------------------------------------------------------------------


def _make_store() -> SQLiteMetadataStore:
    return make_temp_metadata_store()


# ---------------------------------------------------------------------------
# Low-level insert helpers (bypass repository layer for FK-chain setup)
# ---------------------------------------------------------------------------


def _insert_session(
    store: SQLiteMetadataStore,
    session_id: str = "sess_001",
) -> None:
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


def _insert_raw_proposition(
    store: SQLiteMetadataStore,
    proposition_id: str = "prop_001",
    session_id: str = "sess_001",
    proposition_type: str = "change",
    assessment_type: str = "change_assessment",
    origin_kind: str = "system_seeded",
    subject_metric: str | None = "dau",
    seed_finding_refs: list[dict] | None = None,
    identity_key: str = "identity_key_001",
) -> None:
    origin: dict[str, Any]
    if origin_kind == "system_seeded":
        origin = {"kind": "system_seeded", "template_id": "t1", "template_version": "v1"}
    else:
        origin = {
            "kind": "agent_authored",
            "author_type": "agent",
            "authored_label": None,
            "authored_input_ref": None,
        }
    subject = {
        "metric": subject_metric,
        "entity": None,
        "slice": {},
        "grain": None,
        "analysis_axis": "change",
    }
    assessment_anchor = {"assessment_type": assessment_type}
    lineage = {
        "creation_mode": "seeded" if origin_kind == "system_seeded" else "authored",
        "source_artifact_lineages": [],
        "source_step_refs": [],
        "derived_from_proposition_ref": None,
        "derivation_version": "v1",
    }
    refs = seed_finding_refs or []
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
            json.dumps(refs),
            "{}",
            identity_key,
        ],
    )


def _make_finding_row(
    finding_id: str = "fnd_001",
    session_id: str = "sess_001",
    artifact_id: str = "art_001",
    finding_type: str = "delta",
    metric: str | None = "dau",
    grain: str | None = None,
    entity: str | None = None,
) -> dict[str, Any]:
    subject = {
        "metric": metric,
        "entity": entity,
        "slice": {},
        "grain": grain,
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


def _insert_assessment_row(
    store: SQLiteMetadataStore,
    assessment_id: str = "asmnt_001",
    session_id: str = "sess_001",
    proposition_id: str = "prop_001",
    snapshot_seq: int = 1,
    assessment_type: str = "change_assessment",
    status: str = "insufficient",
    supporting_finding_ids: list[str] | None = None,
    opposing_finding_ids: list[str] | None = None,
    applied_inference_record_ids: list[str] | None = None,
    supersedes_assessment_id: str | None = None,
) -> None:
    store.execute(
        "INSERT INTO assessments "
        "(assessment_id, session_id, proposition_id, assessment_type, snapshot_seq, "
        "status, confidence_grade, confidence_rationale_json, "
        "supporting_finding_ids_json, opposing_finding_ids_json, "
        "applied_inference_record_ids_json, supersedes_assessment_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            assessment_id,
            session_id,
            proposition_id,
            assessment_type,
            snapshot_seq,
            status,
            "low",
            json.dumps(
                {
                    "evidence_sufficiency": "weak",
                    "evidence_consistency": "mixed",
                    "rule_coverage": "minimal",
                    "data_quality_impact": "none",
                    "rationale_notes": [],
                }
            ),
            json.dumps(supporting_finding_ids or []),
            json.dumps(opposing_finding_ids or []),
            json.dumps(applied_inference_record_ids or []),
            supersedes_assessment_id,
        ],
    )


def _insert_inference_record_row(
    store: SQLiteMetadataStore,
    inference_record_id: str = "ir_001",
    session_id: str = "sess_001",
    proposition_id: str = "prop_001",
    assessment_id: str = "asmnt_001",
    input_finding_ids: list[str] | None = None,
) -> None:
    store.execute(
        "INSERT INTO inference_records "
        "(inference_record_id, session_id, proposition_id, assessment_id, "
        "rule_id, rule_version, result, input_finding_ids_json, input_assessment_ids_json, "
        "opened_gap_ids_json, resolved_gap_ids_json, confidence_contribution_json, justification_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            inference_record_id,
            session_id,
            proposition_id,
            assessment_id,
            "rule_precondition_check",
            "v1",
            "miss",
            json.dumps(input_finding_ids or []),
            json.dumps([]),
            json.dumps([]),
            json.dumps([]),
            json.dumps({"direction": "neutral", "magnitude": "small"}),
            json.dumps({"matched_conditions": [], "unmatched_conditions": [], "notes": []}),
        ],
    )


def _insert_gap_row(
    store: SQLiteMetadataStore,
    gap_id: str = "gap_001",
    session_id: str = "sess_001",
    proposition_id: str = "prop_001",
    status: str = "open",
    opened_by_inference_record_id: str = "ir_001",
    related_finding_ids: list[str] | None = None,
) -> None:
    store.execute(
        "INSERT INTO evidence_gaps "
        "(gap_id, session_id, proposition_id, gap_kind, title, description, status, "
        "missing_requirement_json, satisfiable_by_json, related_finding_ids_json, "
        "opened_by_inference_record_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            gap_id,
            session_id,
            proposition_id,
            "missing_finding",
            "missing baseline",
            "no baseline observation",
            status,
            json.dumps(
                {
                    "requirement_type": "finding_family",
                    "requirement_key": "baseline",
                    "requirement_params": {"finding_type": "delta", "minimum_count": 1},
                }
            ),
            json.dumps([]),
            json.dumps(related_finding_ids or []),
            opened_by_inference_record_id,
        ],
    )


# ---------------------------------------------------------------------------
# Base class: sets up repos + standard store
# ---------------------------------------------------------------------------


class _RepoBase(unittest.TestCase):
    """Sets up a fresh SQLite store and all six repositories."""

    def setUp(self) -> None:
        self.store = _make_store()
        _insert_session(self.store)
        _insert_artifact(self.store)
        self.finding_repo = FindingRepository(self.store)
        self.proposition_repo = PropositionRepository(self.store)
        self.assessment_repo = AssessmentRepository(self.store)
        self.gap_repo = EvidenceGapRepository(self.store)
        self.ir_repo = InferenceRecordRepository(self.store)

    # ------------------------------------------------------------------
    # Convenience wrappers
    # ------------------------------------------------------------------

    def _build(
        self,
        *,
        session_id: str = "sess_001",
        proposition_id: str = "prop_001",
        candidate_assessment_id: str = "cand_asmnt_001",
        trigger_finding_ids: list[str] | None = None,
    ) -> AssessmentEvaluationContext:
        prop = self.proposition_repo.get(proposition_id)
        assert prop is not None, f"Proposition {proposition_id!r} not in DB"
        return build_assessment_evaluation_context(
            session_id=session_id,
            proposition_id=proposition_id,
            proposition=prop,
            candidate_assessment_id=candidate_assessment_id,
            trigger_finding_ids=trigger_finding_ids or [],
            assessment_repo=self.assessment_repo,
            gap_repo=self.gap_repo,
            finding_repo=self.finding_repo,
            inference_record_repo=self.ir_repo,
        )

    def _insert_proposition(
        self,
        proposition_id: str = "prop_001",
        session_id: str = "sess_001",
        assessment_type: str = "change_assessment",
        origin_kind: str = "system_seeded",
        subject_metric: str | None = "dau",
        seed_finding_refs: list[dict] | None = None,
    ) -> None:
        _insert_raw_proposition(
            self.store,
            proposition_id=proposition_id,
            session_id=session_id,
            assessment_type=assessment_type,
            origin_kind=origin_kind,
            subject_metric=subject_metric,
            seed_finding_refs=seed_finding_refs,
            identity_key=f"ik_{proposition_id}",
        )

    def _insert_finding(
        self,
        finding_id: str,
        finding_type: str = "delta",
        metric: str | None = "dau",
        session_id: str = "sess_001",
        artifact_id: str = "art_001",
    ) -> None:
        self.finding_repo.create(
            _make_finding_row(
                finding_id=finding_id,
                session_id=session_id,
                artifact_id=artifact_id,
                finding_type=finding_type,
                metric=metric,
            )
        )


# ---------------------------------------------------------------------------
# 1. TestContextBuilderBasic
# ---------------------------------------------------------------------------


class TestContextBuilderBasic(_RepoBase):
    """Happy-path construction with minimal inputs."""

    def test_empty_proposition_no_assessments_no_triggers(self) -> None:
        self._insert_proposition()
        ctx = self._build()
        self.assertEqual(ctx["session_id"], "sess_001")
        self.assertEqual(ctx["proposition"]["proposition_id"], "prop_001")
        self.assertIsNone(ctx["current_latest_assessment_id"])
        self.assertEqual(ctx["prior_assessment_ids"], [])
        self.assertEqual(ctx["open_gap_ids"], [])
        self.assertEqual(ctx["resolved_seed_finding_ids"], [])
        self.assertEqual(ctx["trigger_finding_ids"], [])
        self.assertEqual(ctx["candidate_finding_ids"], [])

    def test_seed_hydration_resolved(self) -> None:
        self._insert_finding("fnd_seed")
        refs = [
            {"finding_ref": {"session_id": "sess_001", "finding_id": "fnd_seed"}, "role": "primary"}
        ]
        self._insert_proposition(seed_finding_refs=refs)
        ctx = self._build()
        self.assertIn("fnd_seed", ctx["resolved_seed_finding_ids"])
        self.assertIn("fnd_seed", ctx["candidate_finding_ids"])

    def test_unresolvable_seed_ref_excluded_silently(self) -> None:
        # Seed references a non-existent finding
        refs = [
            {
                "finding_ref": {"session_id": "sess_001", "finding_id": "fnd_nonexistent"},
                "role": "primary",
            }
        ]
        self._insert_proposition(seed_finding_refs=refs)
        ctx = self._build()
        self.assertEqual(ctx["resolved_seed_finding_ids"], [])
        self.assertEqual(ctx["candidate_finding_ids"], [])

    def test_candidate_assessment_id_passed_through(self) -> None:
        self._insert_proposition()
        ctx = self._build(candidate_assessment_id="my_candidate_id")
        self.assertEqual(ctx["candidate_assessment_id"], "my_candidate_id")


# ---------------------------------------------------------------------------
# 2. TestPriorAssessmentLoad
# ---------------------------------------------------------------------------


class TestPriorAssessmentLoad(_RepoBase):
    """Phase 2: prior assessment snapshot ordering."""

    def test_no_assessments_returns_none_and_empty(self) -> None:
        self._insert_proposition()
        ctx = self._build()
        self.assertIsNone(ctx["current_latest_assessment_id"])
        self.assertEqual(ctx["prior_assessment_ids"], [])

    def test_single_assessment_becomes_latest(self) -> None:
        self._insert_proposition()
        _insert_assessment_row(self.store, assessment_id="asmnt_001", snapshot_seq=1)
        ctx = self._build()
        self.assertEqual(ctx["current_latest_assessment_id"], "asmnt_001")
        self.assertEqual(ctx["prior_assessment_ids"], ["asmnt_001"])

    def test_multiple_assessments_ordered_asc_latest_is_highest_seq(self) -> None:
        self._insert_proposition()
        _insert_assessment_row(self.store, assessment_id="asmnt_001", snapshot_seq=1)
        _insert_assessment_row(
            self.store,
            assessment_id="asmnt_002",
            snapshot_seq=2,
            supersedes_assessment_id="asmnt_001",
        )
        _insert_assessment_row(
            self.store,
            assessment_id="asmnt_003",
            snapshot_seq=3,
            supersedes_assessment_id="asmnt_002",
        )
        ctx = self._build()
        self.assertEqual(ctx["prior_assessment_ids"], ["asmnt_001", "asmnt_002", "asmnt_003"])
        self.assertEqual(ctx["current_latest_assessment_id"], "asmnt_003")

    def test_different_proposition_assessments_not_included(self) -> None:
        self._insert_proposition()
        _insert_raw_proposition(self.store, proposition_id="prop_002", identity_key="ik2")
        _insert_assessment_row(
            self.store, assessment_id="asmnt_p2", proposition_id="prop_002", snapshot_seq=1
        )
        ctx = self._build()
        self.assertIsNone(ctx["current_latest_assessment_id"])
        self.assertNotIn("asmnt_p2", ctx["prior_assessment_ids"])


# ---------------------------------------------------------------------------
# 3. TestOpenGapLoad
# ---------------------------------------------------------------------------


class TestOpenGapLoad(_RepoBase):
    """Phase 2: open vs resolved gap filtering."""

    def setUp(self) -> None:
        super().setUp()
        self._insert_proposition()
        _insert_assessment_row(self.store)
        _insert_inference_record_row(self.store)

    def test_open_gap_in_open_gap_ids(self) -> None:
        _insert_gap_row(self.store, gap_id="gap_001", status="open")
        ctx = self._build()
        self.assertIn("gap_001", ctx["open_gap_ids"])

    def test_resolved_gap_not_in_open_gap_ids(self) -> None:
        _insert_gap_row(self.store, gap_id="gap_001", status="resolved")
        ctx = self._build()
        self.assertNotIn("gap_001", ctx["open_gap_ids"])

    def test_mixed_status_only_open_returned(self) -> None:
        _insert_gap_row(self.store, gap_id="gap_001", status="open")
        _insert_gap_row(self.store, gap_id="gap_002", status="resolved")
        ctx = self._build()
        self.assertEqual(ctx["open_gap_ids"], ["gap_001"])

    def test_no_gaps_returns_empty_list(self) -> None:
        ctx = self._build()
        self.assertEqual(ctx["open_gap_ids"], [])


# ---------------------------------------------------------------------------
# 4. TestTriggerNormalization
# ---------------------------------------------------------------------------


class TestTriggerNormalization(_RepoBase):
    """Phase 4: trigger_finding_ids dedup + sort."""

    def setUp(self) -> None:
        super().setUp()
        self._insert_proposition()

    def test_duplicates_deduped(self) -> None:
        self._insert_finding("fnd_aaa")
        ctx = self._build(trigger_finding_ids=["fnd_aaa", "fnd_aaa", "fnd_aaa"])
        self.assertEqual(ctx["trigger_finding_ids"].count("fnd_aaa"), 1)

    def test_sorted_ascending(self) -> None:
        self._insert_finding("fnd_zzz")
        self._insert_finding("fnd_aaa")
        self._insert_finding("fnd_mmm")
        ctx = self._build(trigger_finding_ids=["fnd_zzz", "fnd_aaa", "fnd_mmm"])
        self.assertEqual(ctx["trigger_finding_ids"], ["fnd_aaa", "fnd_mmm", "fnd_zzz"])

    def test_empty_trigger_list(self) -> None:
        ctx = self._build(trigger_finding_ids=[])
        self.assertEqual(ctx["trigger_finding_ids"], [])


# ---------------------------------------------------------------------------
# 5. TestCarryForward
# ---------------------------------------------------------------------------


class TestCarryForward(_RepoBase):
    """Phase 5: carry-forward from latest assessment closure."""

    def setUp(self) -> None:
        super().setUp()
        self._insert_proposition()

    def test_support_oppose_findings_carried_forward(self) -> None:
        self._insert_finding("fnd_support")
        self._insert_finding("fnd_oppose")
        _insert_assessment_row(
            self.store,
            supporting_finding_ids=["fnd_support"],
            opposing_finding_ids=["fnd_oppose"],
        )
        ctx = self._build()
        self.assertIn("fnd_support", ctx["candidate_finding_ids"])
        self.assertIn("fnd_oppose", ctx["candidate_finding_ids"])

    def test_inference_record_input_findings_carried_forward(self) -> None:
        self._insert_finding("fnd_ir_input")
        _insert_assessment_row(self.store, applied_inference_record_ids=["ir_001"])
        _insert_inference_record_row(
            self.store, inference_record_id="ir_001", input_finding_ids=["fnd_ir_input"]
        )
        ctx = self._build()
        self.assertIn("fnd_ir_input", ctx["candidate_finding_ids"])

    def test_open_gap_related_findings_included(self) -> None:
        self._insert_finding("fnd_gap_related")
        _insert_assessment_row(self.store)
        _insert_inference_record_row(self.store)
        _insert_gap_row(self.store, status="open", related_finding_ids=["fnd_gap_related"])
        ctx = self._build()
        self.assertIn("fnd_gap_related", ctx["candidate_finding_ids"])

    def test_no_prior_assessment_no_carry_forward(self) -> None:
        # No assessments → nothing to carry forward
        self._insert_finding("fnd_001")
        ctx = self._build()
        # fnd_001 is not triggered, not a seed → should NOT appear
        self.assertNotIn("fnd_001", ctx["candidate_finding_ids"])

    def test_cross_session_carry_forward_excluded(self) -> None:
        # A finding in session_002 should be excluded even if referenced
        _insert_session(self.store, "sess_002")
        _insert_artifact(self.store, "art_002", "sess_002")
        self.finding_repo.create(
            _make_finding_row("fnd_other_sess", session_id="sess_002", artifact_id="art_002")
        )
        _insert_assessment_row(
            self.store,
            supporting_finding_ids=["fnd_other_sess"],
        )
        ctx = self._build()
        self.assertNotIn("fnd_other_sess", ctx["candidate_finding_ids"])


# ---------------------------------------------------------------------------
# 6. TestCompatibilityFilter
# ---------------------------------------------------------------------------


class TestCompatibilityFilter(_RepoBase):
    """Phase 6: finding_type and subject compatibility filtering."""

    def setUp(self) -> None:
        super().setUp()
        # change_assessment proposition with metric=dau
        self._insert_proposition(assessment_type="change_assessment", subject_metric="dau")

    def test_compatible_finding_type_included(self) -> None:
        # delta is compatible with change_assessment
        self._insert_finding("fnd_delta", finding_type="delta", metric="dau")
        ctx = self._build(trigger_finding_ids=["fnd_delta"])
        self.assertIn("fnd_delta", ctx["candidate_finding_ids"])

    def test_incompatible_finding_type_excluded(self) -> None:
        # decomposition_item is NOT compatible with change_assessment
        self._insert_finding("fnd_decomp", finding_type="decomposition_item", metric="dau")
        ctx = self._build(trigger_finding_ids=["fnd_decomp"])
        self.assertNotIn("fnd_decomp", ctx["candidate_finding_ids"])

    def test_observation_finding_always_compatible(self) -> None:
        self._insert_finding("fnd_obs", finding_type="observation", metric="dau")
        ctx = self._build(trigger_finding_ids=["fnd_obs"])
        self.assertIn("fnd_obs", ctx["candidate_finding_ids"])

    def test_cross_session_finding_excluded_even_if_compatible_type(self) -> None:
        _insert_session(self.store, "sess_002")
        _insert_artifact(self.store, "art_002", "sess_002")
        self.finding_repo.create(
            _make_finding_row(
                "fnd_x",
                session_id="sess_002",
                artifact_id="art_002",
                finding_type="delta",
                metric="dau",
            )
        )
        ctx = self._build(trigger_finding_ids=["fnd_x"])
        self.assertNotIn("fnd_x", ctx["candidate_finding_ids"])

    def test_subject_metric_conflict_excluded(self) -> None:
        # Proposition metric=dau; finding metric=mau → excluded
        self._insert_finding("fnd_mau", finding_type="delta", metric="mau")
        ctx = self._build(trigger_finding_ids=["fnd_mau"])
        self.assertNotIn("fnd_mau", ctx["candidate_finding_ids"])

    def test_finding_with_null_metric_compatible_with_any_metric(self) -> None:
        # Finding metric=None → always compatible when proposition constrains metric
        self._insert_finding("fnd_null_metric", finding_type="delta", metric=None)
        ctx = self._build(trigger_finding_ids=["fnd_null_metric"])
        self.assertIn("fnd_null_metric", ctx["candidate_finding_ids"])


# ---------------------------------------------------------------------------
# 7. TestSubjectCompatibility
# ---------------------------------------------------------------------------


class TestSubjectCompatibility(unittest.TestCase):
    """Unit tests for _subject_compatible (isolated)."""

    def test_both_null_compatible(self) -> None:
        self.assertTrue(
            _subject_compatible(
                {"metric": None, "entity": None, "grain": None},
                {"metric": None, "entity": None, "grain": None},
            )
        )

    def test_matching_metric_compatible(self) -> None:
        self.assertTrue(
            _subject_compatible(
                {"metric": "dau"},
                {"metric": "dau"},
            )
        )

    def test_conflicting_metric_incompatible(self) -> None:
        self.assertFalse(
            _subject_compatible(
                {"metric": "dau"},
                {"metric": "mau"},
            )
        )

    def test_proposition_null_metric_finding_any_metric_compatible(self) -> None:
        self.assertTrue(
            _subject_compatible(
                {"metric": None},
                {"metric": "mau"},
            )
        )

    def test_finding_null_metric_with_proposition_metric_compatible(self) -> None:
        self.assertTrue(
            _subject_compatible(
                {"metric": "dau"},
                {"metric": None},
            )
        )

    def test_grain_conflict_incompatible(self) -> None:
        self.assertFalse(
            _subject_compatible(
                {"metric": None, "grain": "day"},
                {"metric": None, "grain": "week"},
            )
        )

    def test_entity_conflict_incompatible(self) -> None:
        self.assertFalse(
            _subject_compatible(
                {"entity": "account"},
                {"entity": "user"},
            )
        )

    def test_matching_entity_compatible(self) -> None:
        self.assertTrue(
            _subject_compatible(
                {"entity": "account"},
                {"entity": "account"},
            )
        )


# ---------------------------------------------------------------------------
# 8. TestAuthoredFallback
# ---------------------------------------------------------------------------


class TestAuthoredFallback(_RepoBase):
    """Phase 7: discovery fallback for agent_authored propositions."""

    def test_authored_no_seeds_no_prior_no_triggers_fallback_runs(self) -> None:
        # agent_authored + compatible delta finding → included via fallback
        self._insert_proposition(
            origin_kind="agent_authored", assessment_type="change_assessment", subject_metric="dau"
        )
        self._insert_finding("fnd_delta", finding_type="delta", metric="dau")
        ctx = self._build()
        self.assertIn("fnd_delta", ctx["candidate_finding_ids"])

    def test_system_seeded_no_fallback(self) -> None:
        # system_seeded proposition → fallback never runs even if no seeds/prior/triggers
        self._insert_proposition(origin_kind="system_seeded")
        self._insert_finding("fnd_delta", finding_type="delta", metric="dau")
        ctx = self._build()
        # Not triggered, not a seed → should NOT appear via fallback
        self.assertNotIn("fnd_delta", ctx["candidate_finding_ids"])

    def test_authored_with_resolved_seed_no_fallback(self) -> None:
        self._insert_finding("fnd_seed", finding_type="delta", metric="dau")
        refs = [
            {"finding_ref": {"session_id": "sess_001", "finding_id": "fnd_seed"}, "role": "primary"}
        ]
        self._insert_proposition(origin_kind="agent_authored", seed_finding_refs=refs)
        self._insert_finding("fnd_other", finding_type="delta", metric="dau")
        ctx = self._build()
        # Seed resolved → fallback not triggered; fnd_other not reachable
        self.assertIn("fnd_seed", ctx["candidate_finding_ids"])
        self.assertNotIn("fnd_other", ctx["candidate_finding_ids"])

    def test_authored_with_prior_assessment_with_evidence_no_fallback(self) -> None:
        self._insert_proposition(origin_kind="agent_authored")
        self._insert_finding("fnd_support", finding_type="delta", metric="dau")
        _insert_assessment_row(
            self.store,
            supporting_finding_ids=["fnd_support"],
            opposing_finding_ids=[],
        )
        self._insert_finding("fnd_other", finding_type="delta", metric="dau")
        ctx = self._build()
        # Latest assessment has support evidence → fallback suppressed
        # fnd_other not reachable via fallback
        self.assertNotIn("fnd_other", ctx["candidate_finding_ids"])

    def test_authored_with_prior_assessment_empty_closure_fallback_runs(self) -> None:
        """Prior assessment exists but support+oppose both empty → fallback still runs."""
        self._insert_proposition(
            origin_kind="agent_authored", assessment_type="change_assessment", subject_metric="dau"
        )
        self._insert_finding("fnd_delta", finding_type="delta", metric="dau")
        _insert_assessment_row(
            self.store,
            supporting_finding_ids=[],
            opposing_finding_ids=[],
        )
        ctx = self._build()
        # Empty closure → condition 3 of _should_run_discovery_fallback satisfied
        self.assertIn("fnd_delta", ctx["candidate_finding_ids"])

    def test_authored_fallback_incompatible_findings_excluded(self) -> None:
        # Fallback scan should still apply compatibility filter
        self._insert_proposition(
            origin_kind="agent_authored", assessment_type="change_assessment", subject_metric="dau"
        )
        self._insert_finding("fnd_mau", finding_type="delta", metric="mau")
        ctx = self._build()
        self.assertNotIn("fnd_mau", ctx["candidate_finding_ids"])


# ---------------------------------------------------------------------------
# 9. TestCandidateSetStability
# ---------------------------------------------------------------------------


class TestCandidateSetStability(_RepoBase):
    """Phase 8: dedup, sort, and replay stability of candidate_finding_ids."""

    def setUp(self) -> None:
        super().setUp()
        self._insert_proposition()

    def test_duplicate_ids_from_multiple_sources_deduped(self) -> None:
        # fnd_x appears as seed AND trigger
        self._insert_finding("fnd_x", finding_type="delta", metric="dau")
        refs = [
            {"finding_ref": {"session_id": "sess_001", "finding_id": "fnd_x"}, "role": "primary"}
        ]
        _insert_raw_proposition(
            self.store,
            proposition_id="prop_dedup",
            seed_finding_refs=refs,
            identity_key="ik_dedup",
        )
        prop = self.proposition_repo.get("prop_dedup")
        assert prop is not None
        ctx = build_assessment_evaluation_context(
            session_id="sess_001",
            proposition_id="prop_dedup",
            proposition=prop,
            candidate_assessment_id="cand",
            trigger_finding_ids=["fnd_x"],  # also a seed
            assessment_repo=self.assessment_repo,
            gap_repo=self.gap_repo,
            finding_repo=self.finding_repo,
            inference_record_repo=self.ir_repo,
        )
        self.assertEqual(ctx["candidate_finding_ids"].count("fnd_x"), 1)

    def test_stable_sort_ascending(self) -> None:
        self._insert_finding("fnd_z")
        self._insert_finding("fnd_a")
        self._insert_finding("fnd_m")
        ctx = self._build(trigger_finding_ids=["fnd_z", "fnd_a", "fnd_m"])
        self.assertEqual(ctx["candidate_finding_ids"], ["fnd_a", "fnd_m", "fnd_z"])

    def test_replay_identical_inputs_same_result(self) -> None:
        self._insert_finding("fnd_001")
        self._insert_finding("fnd_002")
        ctx1 = self._build(trigger_finding_ids=["fnd_002", "fnd_001"])
        ctx2 = self._build(trigger_finding_ids=["fnd_002", "fnd_001"])
        self.assertEqual(ctx1["candidate_finding_ids"], ctx2["candidate_finding_ids"])

    def test_empty_inputs_empty_candidate_set(self) -> None:
        ctx = self._build(trigger_finding_ids=[])
        self.assertEqual(ctx["candidate_finding_ids"], [])

    def test_many_sources_deduped_and_sorted(self) -> None:
        # 3 findings across seed, trigger, carry-forward — expect sorted union
        self._insert_finding("fnd_ccc", finding_type="delta", metric="dau")
        self._insert_finding("fnd_aaa", finding_type="delta", metric="dau")
        self._insert_finding("fnd_bbb", finding_type="delta", metric="dau")
        refs = [
            {"finding_ref": {"session_id": "sess_001", "finding_id": "fnd_ccc"}, "role": "primary"}
        ]
        _insert_raw_proposition(
            self.store,
            proposition_id="prop_multi",
            seed_finding_refs=refs,
            identity_key="ik_multi",
        )
        _insert_assessment_row(
            self.store,
            assessment_id="asmnt_multi",
            proposition_id="prop_multi",
            supporting_finding_ids=["fnd_bbb"],
        )
        prop = self.proposition_repo.get("prop_multi")
        assert prop is not None
        ctx = build_assessment_evaluation_context(
            session_id="sess_001",
            proposition_id="prop_multi",
            proposition=prop,
            candidate_assessment_id="cand",
            trigger_finding_ids=["fnd_aaa"],
            assessment_repo=self.assessment_repo,
            gap_repo=self.gap_repo,
            finding_repo=self.finding_repo,
            inference_record_repo=self.ir_repo,
        )
        self.assertEqual(ctx["candidate_finding_ids"], ["fnd_aaa", "fnd_bbb", "fnd_ccc"])


# ---------------------------------------------------------------------------
# 10. TestSchemaVersionAndFields
# ---------------------------------------------------------------------------


class TestSchemaVersionAndFields(_RepoBase):
    """TypedDict shape, schema_version, and field derivation."""

    def setUp(self) -> None:
        super().setUp()
        self._insert_proposition(assessment_type="change_assessment")

    def test_schema_version_constant(self) -> None:
        ctx = self._build()
        self.assertEqual(ctx["schema_version"], EVALUATION_CONTEXT_SCHEMA_VERSION)
        self.assertEqual(ctx["schema_version"], "assessment_evaluation_context.v1")

    def test_assessment_type_derived_from_anchor(self) -> None:
        ctx = self._build()
        self.assertEqual(ctx["assessment_type"], "change_assessment")

    def test_candidate_assessment_id_unchanged(self) -> None:
        ctx = self._build(candidate_assessment_id="test_cand_id_xyz")
        self.assertEqual(ctx["candidate_assessment_id"], "test_cand_id_xyz")

    def test_all_typed_dict_keys_present(self) -> None:
        ctx = self._build()
        expected_keys = {
            "session_id",
            "proposition",
            "assessment_type",
            "candidate_assessment_id",
            "current_latest_assessment_id",
            "prior_assessment_ids",
            "open_gap_ids",
            "resolved_seed_finding_ids",
            "trigger_finding_ids",
            "candidate_finding_ids",
            "schema_version",
        }
        self.assertEqual(set(ctx.keys()), expected_keys)


# ---------------------------------------------------------------------------
# 11. TestValidation
# ---------------------------------------------------------------------------


class TestValidation(_RepoBase):
    """ValueError on canonical contract violations."""

    def setUp(self) -> None:
        super().setUp()

    def test_session_id_mismatch_raises_value_error(self) -> None:
        self._insert_proposition(session_id="sess_001")
        prop = self.proposition_repo.get("prop_001")
        assert prop is not None
        with self.assertRaises(ValueError, msg="should raise on session_id mismatch"):
            build_assessment_evaluation_context(
                session_id="sess_WRONG",
                proposition_id="prop_001",
                proposition=prop,
                candidate_assessment_id="cand",
                trigger_finding_ids=[],
                assessment_repo=self.assessment_repo,
                gap_repo=self.gap_repo,
                finding_repo=self.finding_repo,
                inference_record_repo=self.ir_repo,
            )

    def test_proposition_id_mismatch_raises_value_error(self) -> None:
        self._insert_proposition(session_id="sess_001")
        prop = self.proposition_repo.get("prop_001")
        assert prop is not None
        with self.assertRaises(ValueError):
            build_assessment_evaluation_context(
                session_id="sess_001",
                proposition_id="prop_WRONG",
                proposition=prop,
                candidate_assessment_id="cand",
                trigger_finding_ids=[],
                assessment_repo=self.assessment_repo,
                gap_repo=self.gap_repo,
                finding_repo=self.finding_repo,
                inference_record_repo=self.ir_repo,
            )

    def test_correct_inputs_no_error(self) -> None:
        self._insert_proposition()
        # Should not raise
        ctx = self._build()
        self.assertEqual(ctx["session_id"], "sess_001")


# ---------------------------------------------------------------------------
# 12. TestHelperFunctions
# ---------------------------------------------------------------------------


class TestCompatibleFindingTypesHelper(unittest.TestCase):
    """Unit tests for _compatible_finding_types."""

    def test_change_assessment_includes_delta(self) -> None:
        self.assertIn("delta", _compatible_finding_types("change_assessment"))

    def test_change_assessment_excludes_decomposition_item(self) -> None:
        self.assertNotIn("decomposition_item", _compatible_finding_types("change_assessment"))

    def test_observation_always_included(self) -> None:
        for at in [
            "change_assessment",
            "decomposition_assessment",
            "anomaly_assessment",
            "correlation_assessment",
            "test_hypothesis_assessment",
            "forecast_assessment",
        ]:
            self.assertIn("observation", _compatible_finding_types(at), msg=at)

    def test_all_six_assessment_types_covered(self) -> None:
        pairs = [
            ("change_assessment", "delta"),
            ("decomposition_assessment", "decomposition_item"),
            ("anomaly_assessment", "anomaly_candidate"),
            ("correlation_assessment", "correlation_result"),
            ("test_hypothesis_assessment", "test_result"),
            ("forecast_assessment", "forecast_point"),
        ]
        for assessment_type, finding_type in pairs:
            self.assertIn(
                finding_type,
                _compatible_finding_types(assessment_type),
                msg=f"{finding_type} should be in {assessment_type}",
            )

    def test_unknown_assessment_type_returns_only_observation(self) -> None:
        result = _compatible_finding_types("unknown_assessment_type")
        self.assertEqual(result, {"observation"})


class TestStableDedup(unittest.TestCase):
    """Unit tests for _stable_dedup."""

    def test_empty_list(self) -> None:
        self.assertEqual(_stable_dedup([]), [])

    def test_deduplicates_and_sorts(self) -> None:
        self.assertEqual(_stable_dedup(["c", "a", "b", "a", "c"]), ["a", "b", "c"])

    def test_already_sorted_unchanged(self) -> None:
        self.assertEqual(_stable_dedup(["a", "b", "c"]), ["a", "b", "c"])

    def test_single_element(self) -> None:
        self.assertEqual(_stable_dedup(["x"]), ["x"])

    def test_all_duplicates_collapse_to_one(self) -> None:
        self.assertEqual(_stable_dedup(["z", "z", "z"]), ["z"])


if __name__ == "__main__":
    unittest.main()
