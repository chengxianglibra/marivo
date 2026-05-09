"""Tests for canonical ref and membership TypedDicts (Phase 4a-3).

Acceptance criteria:
- Typed ref structures match design schemas in docs/analysis/evidence-engine/schemas/
- PropositionSeedRef isolation: creation-time only, no live membership semantics
- AssessmentRef includes snapshot_seq for immutable-snapshot anchoring
- GapMembershipEntry.blocking / severity are snapshot-owned (not EvidenceGapRef fields)
- InferenceRecordRef has three-way binding (inference_record_id, proposition_id, assessment_id)
- ProposalContext.policy_profile is required; session_goal and risk_budget may be None
- proposition_seed_finding_refs junction table exists in METADATA_DDL with UNIQUE constraint
- All three PropositionSeedRole values are valid
"""

from __future__ import annotations

import sqlite3
import unittest
from typing import get_type_hints

from marivo.adapters.schema import METADATA_DDL
from marivo.evidence_engine.canonical_finding import FindingRef
from marivo.evidence_engine.canonical_refs import (
    ArtifactLineageRef,
    AssessmentContextRef,
    AssessmentRef,
    EvidenceGapContextRef,
    EvidenceGapRef,
    FindingContextRef,
    GapMembershipEntry,
    InferenceRecordRef,
    ProposalContext,
    ProposalContextRef,
    PropositionContextRef,
    PropositionRef,
    PropositionSeedRef,
    PropositionSeedRole,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_finding_ref() -> FindingRef:
    return {"session_id": "sess_abc", "finding_id": "fnd_aabbccdd001122334455aabb"}


def _make_proposition_ref() -> PropositionRef:
    return {"session_id": "sess_abc", "proposition_id": "prop_aabbcc001122"}


def _make_assessment_ref() -> AssessmentRef:
    return {
        "assessment_id": "asmt_aabbcc001122",
        "proposition_id": "prop_aabbcc001122",
        "snapshot_seq": 1,
    }


def _make_gap_ref() -> EvidenceGapRef:
    return {"gap_id": "gap_aabbcc001122", "proposition_id": "prop_aabbcc001122"}


# ---------------------------------------------------------------------------
# PropositionRef shape
# ---------------------------------------------------------------------------


class TestPropositionRef(unittest.TestCase):
    def test_required_keys(self) -> None:
        ref = _make_proposition_ref()
        self.assertIn("session_id", ref)
        self.assertIn("proposition_id", ref)

    def test_no_extra_semantics(self) -> None:
        """PropositionRef must not carry assessment status or confidence."""
        ref = _make_proposition_ref()
        self.assertNotIn("status", ref)
        self.assertNotIn("confidence", ref)

    def test_session_id_is_required(self) -> None:
        hints = get_type_hints(PropositionRef)
        self.assertIn("session_id", hints)
        self.assertIn("proposition_id", hints)


# ---------------------------------------------------------------------------
# PropositionSeedRef isolation
# ---------------------------------------------------------------------------


class TestPropositionSeedRef(unittest.TestCase):
    def test_required_fields(self) -> None:
        seed_ref: PropositionSeedRef = {
            "finding_ref": _make_finding_ref(),
            "role": "primary",
        }
        self.assertIn("finding_ref", seed_ref)
        self.assertIn("role", seed_ref)

    def test_role_primary(self) -> None:
        seed_ref: PropositionSeedRef = {
            "finding_ref": _make_finding_ref(),
            "role": "primary",
        }
        self.assertEqual(seed_ref["role"], "primary")

    def test_role_secondary(self) -> None:
        seed_ref: PropositionSeedRef = {
            "finding_ref": _make_finding_ref(),
            "role": "secondary",
        }
        self.assertEqual(seed_ref["role"], "secondary")

    def test_role_context(self) -> None:
        seed_ref: PropositionSeedRef = {
            "finding_ref": _make_finding_ref(),
            "role": "context",
        }
        self.assertEqual(seed_ref["role"], "context")

    def test_no_supporting_or_opposing_fields(self) -> None:
        """PropositionSeedRef must not carry runtime membership semantics.

        The live support/opposition lives in assessment, not in seed refs.
        """
        hints = get_type_hints(PropositionSeedRef)
        self.assertNotIn("supporting", hints)
        self.assertNotIn("opposing", hints)
        self.assertNotIn("status", hints)
        self.assertNotIn("confidence", hints)

    def test_finding_ref_is_nested(self) -> None:
        """The inner ref must be a FindingRef (not a bare string)."""
        seed_ref: PropositionSeedRef = {
            "finding_ref": _make_finding_ref(),
            "role": "context",
        }
        self.assertIsInstance(seed_ref["finding_ref"], dict)
        self.assertIn("finding_id", seed_ref["finding_ref"])
        self.assertIn("session_id", seed_ref["finding_ref"])

    def test_all_roles_distinct(self) -> None:
        roles: list[PropositionSeedRole] = ["primary", "secondary", "context"]
        self.assertEqual(len(set(roles)), 3)


# ---------------------------------------------------------------------------
# ArtifactLineageRef
# ---------------------------------------------------------------------------


class TestArtifactLineageRef(unittest.TestCase):
    def test_required_fields(self) -> None:
        ref: ArtifactLineageRef = {
            "artifact_id": "art_abc",
            "artifact_schema_version": "v1",
            "extractor_version": "v1",
        }
        self.assertEqual(ref["artifact_id"], "art_abc")

    def test_nullable_fields(self) -> None:
        ref: ArtifactLineageRef = {
            "artifact_id": "art_abc",
            "artifact_schema_version": None,
            "extractor_version": None,
        }
        self.assertIsNone(ref["artifact_schema_version"])
        self.assertIsNone(ref["extractor_version"])


# ---------------------------------------------------------------------------
# AssessmentRef snapshot anchoring
# ---------------------------------------------------------------------------


class TestAssessmentRef(unittest.TestCase):
    def test_snapshot_seq_is_present(self) -> None:
        ref = _make_assessment_ref()
        self.assertIn("snapshot_seq", ref)

    def test_snapshot_seq_is_int(self) -> None:
        ref = _make_assessment_ref()
        self.assertIsInstance(ref["snapshot_seq"], int)

    def test_has_all_three_fields(self) -> None:
        ref = _make_assessment_ref()
        self.assertIn("assessment_id", ref)
        self.assertIn("proposition_id", ref)
        self.assertIn("snapshot_seq", ref)

    def test_snapshot_seq_in_typehints(self) -> None:
        hints = get_type_hints(AssessmentRef)
        self.assertIn("snapshot_seq", hints)
        self.assertEqual(hints["snapshot_seq"], int)

    def test_no_mutable_latest_flag(self) -> None:
        """AssessmentRef must not carry an is_latest flag (that is read-layer)."""
        hints = get_type_hints(AssessmentRef)
        self.assertNotIn("is_latest", hints)


# ---------------------------------------------------------------------------
# EvidenceGapRef
# ---------------------------------------------------------------------------


class TestEvidenceGapRef(unittest.TestCase):
    def test_required_fields(self) -> None:
        ref = _make_gap_ref()
        self.assertIn("gap_id", ref)
        self.assertIn("proposition_id", ref)

    def test_no_snapshot_owned_fields(self) -> None:
        """EvidenceGapRef must NOT carry blocking/severity (those belong to GapMembershipEntry)."""
        hints = get_type_hints(EvidenceGapRef)
        self.assertNotIn("blocking", hints)
        self.assertNotIn("severity", hints)


# ---------------------------------------------------------------------------
# GapMembershipEntry snapshot-owned classification
# ---------------------------------------------------------------------------


class TestGapMembershipEntry(unittest.TestCase):
    def test_required_fields(self) -> None:
        entry: GapMembershipEntry = {
            "gap_ref": _make_gap_ref(),
            "blocking": True,
            "severity": "high",
        }
        self.assertIn("gap_ref", entry)
        self.assertIn("blocking", entry)
        self.assertIn("severity", entry)

    def test_blocking_severity_are_in_entry_not_gap_ref(self) -> None:
        """Snapshot-owned fields belong to the entry, not to EvidenceGapRef."""
        entry_hints = get_type_hints(GapMembershipEntry)
        gap_ref_hints = get_type_hints(EvidenceGapRef)

        self.assertIn("blocking", entry_hints)
        self.assertIn("severity", entry_hints)
        self.assertNotIn("blocking", gap_ref_hints)
        self.assertNotIn("severity", gap_ref_hints)

    def test_gap_ref_is_typed(self) -> None:
        entry: GapMembershipEntry = {
            "gap_ref": _make_gap_ref(),
            "blocking": False,
            "severity": "low",
        }
        self.assertIsInstance(entry["gap_ref"], dict)
        self.assertIn("gap_id", entry["gap_ref"])

    def test_severity_values(self) -> None:
        for sev in ("low", "medium", "high", "critical"):
            entry: GapMembershipEntry = {
                "gap_ref": _make_gap_ref(),
                "blocking": False,
                "severity": sev,  # type: ignore[arg-type]
            }
            self.assertEqual(entry["severity"], sev)

    def test_non_blocking_and_blocking_are_independent(self) -> None:
        blocking_entry: GapMembershipEntry = {
            "gap_ref": _make_gap_ref(),
            "blocking": True,
            "severity": "critical",
        }
        non_blocking_entry: GapMembershipEntry = {
            "gap_ref": _make_gap_ref(),
            "blocking": False,
            "severity": "low",
        }
        self.assertTrue(blocking_entry["blocking"])
        self.assertFalse(non_blocking_entry["blocking"])
        # Same gap can appear in both entries (different snapshots have different
        # blocking/severity classifications)
        self.assertEqual(
            blocking_entry["gap_ref"]["gap_id"],
            non_blocking_entry["gap_ref"]["gap_id"],
        )


# ---------------------------------------------------------------------------
# InferenceRecordRef three-way binding
# ---------------------------------------------------------------------------


class TestInferenceRecordRef(unittest.TestCase):
    def test_three_way_binding(self) -> None:
        ref: InferenceRecordRef = {
            "inference_record_id": "infr_aabbcc001122",
            "proposition_id": "prop_aabbcc001122",
            "assessment_id": "asmt_aabbcc001122",
        }
        self.assertIn("inference_record_id", ref)
        self.assertIn("proposition_id", ref)
        self.assertIn("assessment_id", ref)

    def test_typehints_have_all_three(self) -> None:
        hints = get_type_hints(InferenceRecordRef)
        self.assertIn("inference_record_id", hints)
        self.assertIn("proposition_id", hints)
        self.assertIn("assessment_id", hints)


# ---------------------------------------------------------------------------
# ProposalContext
# ---------------------------------------------------------------------------


class TestProposalContext(unittest.TestCase):
    def test_policy_profile_required(self) -> None:
        """policy_profile must be present; session_goal and risk_budget may be None."""
        ctx: ProposalContext = {
            "session_goal": None,
            "risk_budget": None,
            "policy_profile": "default",
        }
        self.assertEqual(ctx["policy_profile"], "default")

    def test_session_goal_nullable(self) -> None:
        ctx: ProposalContext = {
            "session_goal": None,
            "risk_budget": "low",
            "policy_profile": "conservative",
        }
        self.assertIsNone(ctx["session_goal"])

    def test_risk_budget_nullable(self) -> None:
        ctx: ProposalContext = {
            "session_goal": "explain_change",
            "risk_budget": None,
            "policy_profile": "default",
        }
        self.assertIsNone(ctx["risk_budget"])

    def test_policy_profile_in_typehints(self) -> None:
        hints = get_type_hints(ProposalContext)
        self.assertIn("policy_profile", hints)
        self.assertIn("session_goal", hints)
        self.assertIn("risk_budget", hints)


# ---------------------------------------------------------------------------
# ProposalContextRef discriminated union variants
# ---------------------------------------------------------------------------


class TestProposalContextRef(unittest.TestCase):
    def test_proposition_variant(self) -> None:
        ref: PropositionContextRef = {
            "kind": "proposition",
            "proposition_ref": _make_proposition_ref(),
        }
        self.assertEqual(ref["kind"], "proposition")
        self.assertIn("proposition_ref", ref)

    def test_assessment_variant(self) -> None:
        ref: AssessmentContextRef = {
            "kind": "assessment",
            "assessment_ref": _make_assessment_ref(),
        }
        self.assertEqual(ref["kind"], "assessment")

    def test_finding_variant(self) -> None:
        ref: FindingContextRef = {
            "kind": "finding",
            "finding_ref": _make_finding_ref(),
        }
        self.assertEqual(ref["kind"], "finding")

    def test_evidence_gap_variant(self) -> None:
        ref: EvidenceGapContextRef = {
            "kind": "evidence_gap",
            "gap_ref": _make_gap_ref(),
        }
        self.assertEqual(ref["kind"], "evidence_gap")

    def test_union_type_alias_covers_all_variants(self) -> None:
        """ProposalContextRef union should include all four concrete types."""
        import typing

        args = typing.get_args(ProposalContextRef)
        kinds = set()
        for variant in args:
            hints = get_type_hints(variant)
            if "kind" in hints:
                # Literal type — extract the value
                k_args = typing.get_args(hints["kind"])
                if k_args:
                    kinds.add(k_args[0])
        self.assertEqual(kinds, {"proposition", "assessment", "finding", "evidence_gap"})


# ---------------------------------------------------------------------------
# proposition_seed_finding_refs junction table in METADATA_DDL
# ---------------------------------------------------------------------------


class TestPropositionSeedFindingRefsTable(unittest.TestCase):
    def _find_table_ddl(self) -> str | None:
        for stmt in METADATA_DDL:
            if (
                isinstance(stmt, str)
                and "proposition_seed_finding_refs" in stmt
                and "CREATE TABLE" in stmt
            ):
                return stmt
        return None

    def test_table_exists_in_ddl(self) -> None:
        self.assertIsNotNone(
            self._find_table_ddl(),
            "proposition_seed_finding_refs CREATE TABLE not found in METADATA_DDL",
        )

    def test_table_has_proposition_id_column(self) -> None:
        ddl = self._find_table_ddl()
        self.assertIsNotNone(ddl)
        self.assertIn("proposition_id", ddl)

    def test_table_has_finding_id_column(self) -> None:
        ddl = self._find_table_ddl()
        self.assertIsNotNone(ddl)
        self.assertIn("finding_id", ddl)

    def test_table_has_role_column(self) -> None:
        ddl = self._find_table_ddl()
        self.assertIsNotNone(ddl)
        self.assertIn("role", ddl)

    def test_table_has_unique_constraint(self) -> None:
        ddl = self._find_table_ddl()
        self.assertIsNotNone(ddl)
        self.assertIn("UNIQUE(proposition_id, finding_id)", ddl)
        self.assertNotIn("UNIQUE(proposition_id, finding_id, role)", ddl)

    def test_indexes_exist(self) -> None:
        idx_stmts = [
            s
            for s in METADATA_DDL
            if isinstance(s, str) and "proposition_seed_finding_refs" in s and "CREATE INDEX" in s
        ]
        idx_targets = set(idx_stmts)
        has_prop_idx = any("proposition_id" in s for s in idx_targets)
        has_finding_idx = any("finding_id" in s for s in idx_targets)
        self.assertTrue(has_prop_idx, "Missing index on proposition_id")
        self.assertTrue(has_finding_idx, "Missing index on finding_id")

    def test_junction_table_can_be_created_in_sqlite(self) -> None:
        """Integration: create all Phase 4 tables in an in-memory SQLite DB."""
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys = OFF")  # avoid ordering issues during setup
        for stmt in METADATA_DDL:
            if isinstance(stmt, str):
                try:
                    conn.execute(stmt)
                except sqlite3.OperationalError as exc:
                    self.fail(f"DDL execution failed: {exc}\n\nStatement:\n{stmt}")
        conn.commit()

        # Verify the table exists
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='proposition_seed_finding_refs'"
        )
        row = cur.fetchone()
        self.assertIsNotNone(row, "proposition_seed_finding_refs table not created")
        conn.close()

    def test_unique_constraint_enforced(self) -> None:
        """Same (proposition_id, finding_id) must be rejected on duplicate INSERT."""
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys = OFF")
        for stmt in METADATA_DDL:
            if isinstance(stmt, str):
                try:
                    conn.execute(stmt)
                except sqlite3.OperationalError as exc:
                    self.fail(f"DDL execution failed: {exc}\n\nStatement:\n{stmt}")
        conn.commit()

        insert = (
            "INSERT INTO proposition_seed_finding_refs"
            " (proposition_id, finding_id, role) VALUES (?, ?, ?)"
        )
        conn.execute(insert, ("prop_1", "fnd_1", "primary"))
        conn.commit()

        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(insert, ("prop_1", "fnd_1", "primary"))
            conn.commit()

        conn.close()

    def test_same_finding_different_role_is_rejected(self) -> None:
        """Same (proposition_id, finding_id) with a different role must also be rejected.

        A finding may seed a proposition in exactly one role; the UNIQUE constraint
        is on (proposition_id, finding_id), not (proposition_id, finding_id, role).
        """
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys = OFF")
        for stmt in METADATA_DDL:
            if isinstance(stmt, str):
                try:
                    conn.execute(stmt)
                except sqlite3.OperationalError as exc:
                    self.fail(f"DDL execution failed: {exc}\n\nStatement:\n{stmt}")
        conn.commit()

        insert = (
            "INSERT INTO proposition_seed_finding_refs"
            " (proposition_id, finding_id, role) VALUES (?, ?, ?)"
        )
        conn.execute(insert, ("prop_1", "fnd_1", "primary"))
        conn.commit()

        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(insert, ("prop_1", "fnd_1", "context"))  # same pair, different role
            conn.commit()

        conn.close()
