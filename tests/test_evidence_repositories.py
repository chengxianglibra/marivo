"""Repository contract tests for canonical evidence objects (Phase 4b-1).

Covers acceptance criteria:
- Each repository: create + get round-trip
- list queries with filtering and ordering
- FindingRepository: UNIQUE idempotency (same finding_id re-insert is ignored)
- PropositionRepository: get_by_identity_key hit/miss; add_seed_finding_refs + junction query
- AssessmentRepository: get_latest returns highest snapshot_seq; empty returns None;
  list_by_proposition ordering; next_snapshot_seq
- ActionProposalRepository: priority_rank ordering; action_kind filter
- EvidenceGapRepository: create + get; status filter
- InferenceRecordRepository: create + get; list_by_assessment
"""

from __future__ import annotations

import json
import unittest

from app.storage.evidence_repositories import (
    ActionProposalRepository,
    AssessmentRepository,
    EvidenceGapRepository,
    FindingRepository,
    InferenceRecordRepository,
    PropositionRepository,
)
from app.storage.sqlite_metadata import SQLiteMetadataStore
from tests.shared_fixtures import make_temp_metadata_store

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_store() -> SQLiteMetadataStore:
    return make_temp_metadata_store()


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


def _make_finding(
    finding_id: str = "fnd_aaa001",
    session_id: str = "sess_001",
    artifact_id: str = "art_001",
    finding_type: str = "observation",
    canonical_item_key: str = "value",
) -> dict:
    return {
        "finding_id": finding_id,
        "session_id": session_id,
        "artifact_id": artifact_id,
        "step_ref_json": json.dumps(
            {"session_id": session_id, "step_id": "step_001", "step_type": "observe"}
        ),
        "finding_type": finding_type,
        "canonical_item_key": canonical_item_key,
        "subject_json": json.dumps(
            {"metric": "dau", "entity": None, "slice": {}, "grain": None, "analysis_axis": "scalar"}
        ),
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
                "source_step_type": "observe",
                "extractor_name": "obs_extractor",
                "extractor_version": "v1",
                "artifact_schema_version": "v1",
                "canonical_item_key": canonical_item_key,
                "artifact_item_ref": {"collection": "value", "index": None, "key": None},
                "projection_ref": None,
            }
        ),
        "payload_json": json.dumps({"observation_kind": "scalar", "value": 1234.0, "unit": None}),
        "schema_version": "v1",
    }


def _insert_proposition(
    store: SQLiteMetadataStore,
    proposition_id: str = "prop_001",
    session_id: str = "sess_001",
    proposition_type: str = "delta_proposition",
    identity_key: str = "delta_proposition:dau:overall",
) -> None:
    store.execute(
        "INSERT INTO propositions "
        "(proposition_id, session_id, proposition_type, subject_json, origin_json, assessment_anchor_json, lineage_json, identity_key) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            proposition_id,
            session_id,
            proposition_type,
            '{"metric":"dau"}',
            '{"source":"system_seeded"}',
            "{}",
            "{}",
            identity_key,
        ],
    )


def _insert_assessment(
    store: SQLiteMetadataStore,
    assessment_id: str = "asmnt_001",
    session_id: str = "sess_001",
    proposition_id: str = "prop_001",
    snapshot_seq: int = 1,
) -> None:
    store.execute(
        "INSERT INTO assessments "
        "(assessment_id, session_id, proposition_id, assessment_type, snapshot_seq, "
        "status, confidence_grade, confidence_rationale_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            assessment_id,
            session_id,
            proposition_id,
            "directional",
            snapshot_seq,
            "insufficient_evidence",
            "low",
            "{}",
        ],
    )


def _insert_inference_record(
    store: SQLiteMetadataStore,
    inference_record_id: str = "ir_001",
    session_id: str = "sess_001",
    proposition_id: str = "prop_001",
    assessment_id: str = "asmnt_001",
) -> None:
    store.execute(
        "INSERT INTO inference_records "
        "(inference_record_id, session_id, proposition_id, assessment_id, rule_id, result) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            inference_record_id,
            session_id,
            proposition_id,
            assessment_id,
            "rule_no_evidence",
            "gap_opened",
        ],
    )


# ---------------------------------------------------------------------------
# FindingRepository tests
# ---------------------------------------------------------------------------


class TestFindingRepository(unittest.TestCase):
    def setUp(self) -> None:
        self.store = _make_store()
        _insert_session(self.store)
        _insert_artifact(self.store)
        self.repo = FindingRepository(self.store)

    def test_create_and_get_round_trip(self) -> None:
        finding = _make_finding()
        self.repo.create(finding)
        result = self.repo.get("fnd_aaa001")
        self.assertIsNotNone(result)
        self.assertEqual(result["finding_id"], "fnd_aaa001")
        self.assertEqual(result["finding_type"], "observation")

    def test_get_deserializes_json_fields(self) -> None:
        self.repo.create(_make_finding())
        result = self.repo.get("fnd_aaa001")
        self.assertIsInstance(result["step_ref_json"], dict)
        self.assertIsInstance(result["subject_json"], dict)
        self.assertIsInstance(result["quality_json"], dict)
        self.assertIsInstance(result["provenance_json"], dict)
        self.assertIsInstance(result["payload_json"], dict)

    def test_get_unknown_returns_none(self) -> None:
        result = self.repo.get("fnd_does_not_exist")
        self.assertIsNone(result)

    def test_create_idempotent_same_finding_id(self) -> None:
        """Re-inserting the same finding_id must be a silent no-op."""
        f = _make_finding()
        self.repo.create(f)
        # Change payload — second insert should be ignored
        f2 = dict(f)
        f2["payload_json"] = json.dumps(
            {"observation_kind": "scalar", "value": 9999.0, "unit": None}
        )
        self.repo.create(f2)
        result = self.repo.get("fnd_aaa001")
        payload = result["payload_json"]
        self.assertEqual(payload["value"], 1234.0)  # original value preserved

    def test_create_idempotent_same_unique_key(self) -> None:
        """Same (artifact_id, finding_type, canonical_item_key) is also idempotent."""
        self.repo.create(_make_finding(finding_id="fnd_aaa001", canonical_item_key="value"))
        # different finding_id but same unique key triple — should be ignored
        self.repo.create(_make_finding(finding_id="fnd_bbb002", canonical_item_key="value"))
        by_artifact = self.repo.list_by_artifact("art_001")
        self.assertEqual(len(by_artifact), 1)

    def test_list_by_artifact(self) -> None:
        self.repo.create(_make_finding(finding_id="fnd_aaa001", canonical_item_key="value"))
        _insert_artifact(self.store, artifact_id="art_002")
        self.repo.create(
            _make_finding(
                finding_id="fnd_bbb002", artifact_id="art_002", canonical_item_key="value"
            )
        )
        results = self.repo.list_by_artifact("art_001")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["finding_id"], "fnd_aaa001")

    def test_list_by_session_no_filter(self) -> None:
        self.repo.create(
            _make_finding(
                finding_id="fnd_aaa001", finding_type="observation", canonical_item_key="value"
            )
        )
        _insert_artifact(self.store, artifact_id="art_002")
        self.repo.create(
            _make_finding(
                finding_id="fnd_bbb002",
                artifact_id="art_002",
                finding_type="delta",
                canonical_item_key="result",
            )
        )
        results = self.repo.list_by_session("sess_001")
        self.assertEqual(len(results), 2)

    def test_list_by_session_with_finding_type_filter(self) -> None:
        self.repo.create(
            _make_finding(
                finding_id="fnd_aaa001", finding_type="observation", canonical_item_key="value"
            )
        )
        _insert_artifact(self.store, artifact_id="art_002")
        self.repo.create(
            _make_finding(
                finding_id="fnd_bbb002",
                artifact_id="art_002",
                finding_type="delta",
                canonical_item_key="result",
            )
        )
        observations = self.repo.list_by_session("sess_001", finding_type="observation")
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]["finding_id"], "fnd_aaa001")

    def test_list_by_session_empty(self) -> None:
        results = self.repo.list_by_session("sess_001")
        self.assertEqual(results, [])

    def test_observed_window_none_is_preserved(self) -> None:
        f = _make_finding()
        f["observed_window_json"] = None
        self.repo.create(f)
        result = self.repo.get("fnd_aaa001")
        self.assertIsNone(result["observed_window_json"])


# ---------------------------------------------------------------------------
# PropositionRepository tests
# ---------------------------------------------------------------------------


class TestPropositionRepository(unittest.TestCase):
    def setUp(self) -> None:
        self.store = _make_store()
        _insert_session(self.store)
        self.repo = PropositionRepository(self.store)

    def _make_prop(
        self,
        proposition_id: str = "prop_001",
        proposition_type: str = "delta_proposition",
        identity_key: str = "delta_proposition:dau:overall",
    ) -> dict:
        return {
            "proposition_id": proposition_id,
            "session_id": "sess_001",
            "proposition_type": proposition_type,
            "subject_json": json.dumps({"metric": "dau"}),
            "origin_json": json.dumps({"source": "system_seeded"}),
            "assessment_anchor_json": json.dumps({}),
            "lineage_json": json.dumps({}),
            "seed_finding_refs_json": json.dumps([]),
            "payload_json": json.dumps({}),
            "schema_version": "v1",
            "identity_key": identity_key,
        }

    def test_create_and_get_round_trip(self) -> None:
        self.repo.create(self._make_prop())
        result = self.repo.get("prop_001")
        self.assertIsNotNone(result)
        self.assertEqual(result["proposition_id"], "prop_001")
        self.assertEqual(result["proposition_type"], "delta_proposition")

    def test_get_deserializes_json_fields(self) -> None:
        self.repo.create(self._make_prop())
        result = self.repo.get("prop_001")
        self.assertIsInstance(result["subject_json"], dict)
        self.assertIsInstance(result["seed_finding_refs_json"], list)
        self.assertIsInstance(result["payload_json"], dict)

    def test_get_unknown_returns_none(self) -> None:
        self.assertIsNone(self.repo.get("prop_unknown"))

    def test_list_by_session(self) -> None:
        self.repo.create(self._make_prop("prop_001", identity_key="k1"))
        self.repo.create(self._make_prop("prop_002", identity_key="k2"))
        results = self.repo.list_by_session("sess_001")
        self.assertEqual(len(results), 2)

    def test_list_by_session_empty(self) -> None:
        self.assertEqual(self.repo.list_by_session("sess_001"), [])

    def test_get_by_identity_key_hit(self) -> None:
        self.repo.create(self._make_prop(identity_key="delta_proposition:dau:overall"))
        result = self.repo.get_by_identity_key(
            "sess_001", "delta_proposition", "delta_proposition:dau:overall"
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["proposition_id"], "prop_001")

    def test_get_by_identity_key_miss(self) -> None:
        self.repo.create(self._make_prop(identity_key="delta_proposition:dau:overall"))
        result = self.repo.get_by_identity_key("sess_001", "delta_proposition", "different_key")
        self.assertIsNone(result)

    def test_get_by_identity_key_session_isolated(self) -> None:
        _insert_session(self.store, "sess_002")
        self.repo.create(self._make_prop(identity_key="k1"))
        result = self.repo.get_by_identity_key("sess_002", "delta_proposition", "k1")
        self.assertIsNone(result)

    def test_identity_key_unique_constraint_prevents_duplicate(self) -> None:
        """UNIQUE partial index must prevent two propositions with the same
        (session_id, proposition_type, identity_key) when identity_key != ''."""
        self.repo.create(self._make_prop("prop_001", identity_key="k_unique"))
        # Second create with different proposition_id but same identity_key is ignored.
        self.repo.create(self._make_prop("prop_002", identity_key="k_unique"))
        # Only the first should survive
        all_props = self.repo.list_by_session("sess_001")
        self.assertEqual(len(all_props), 1)
        self.assertEqual(all_props[0]["proposition_id"], "prop_001")

    def test_empty_identity_key_allows_multiple(self) -> None:
        """Propositions with identity_key='' are excluded from the UNIQUE partial
        index and must not conflict with each other (legacy/agent-authored rows)."""
        self.repo.create(self._make_prop("prop_001", identity_key=""))
        self.repo.create(self._make_prop("prop_002", identity_key=""))
        all_props = self.repo.list_by_session("sess_001")
        self.assertEqual(len(all_props), 2)

    def test_add_seed_finding_refs_and_query(self) -> None:
        self.repo.create(self._make_prop())
        # Seed finding must exist in findings table; insert directly
        _insert_artifact(self.store)
        fr = FindingRepository(self.store)
        fr.create(_make_finding())
        self.repo.add_seed_finding_refs(
            "prop_001", [{"finding_id": "fnd_aaa001", "role": "primary"}]
        )
        refs = self.repo.get_seed_finding_refs("prop_001")
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["finding_id"], "fnd_aaa001")
        self.assertEqual(refs[0]["role"], "primary")

    def test_add_seed_finding_refs_idempotent(self) -> None:
        self.repo.create(self._make_prop())
        _insert_artifact(self.store)
        fr = FindingRepository(self.store)
        fr.create(_make_finding())
        self.repo.add_seed_finding_refs(
            "prop_001", [{"finding_id": "fnd_aaa001", "role": "primary"}]
        )
        self.repo.add_seed_finding_refs(
            "prop_001", [{"finding_id": "fnd_aaa001", "role": "primary"}]
        )
        refs = self.repo.get_seed_finding_refs("prop_001")
        self.assertEqual(len(refs), 1)

    def test_list_seeded_proposition_ids(self) -> None:
        self.repo.create(self._make_prop("prop_001", identity_key="k1"))
        self.repo.create(self._make_prop("prop_002", identity_key="k2"))
        _insert_artifact(self.store)
        fr = FindingRepository(self.store)
        fr.create(_make_finding())
        self.repo.add_seed_finding_refs(
            "prop_001", [{"finding_id": "fnd_aaa001", "role": "primary"}]
        )
        self.repo.add_seed_finding_refs(
            "prop_002", [{"finding_id": "fnd_aaa001", "role": "secondary"}]
        )
        ids = self.repo.list_seeded_proposition_ids("fnd_aaa001")
        self.assertIn("prop_001", ids)
        self.assertIn("prop_002", ids)


# ---------------------------------------------------------------------------
# AssessmentRepository tests
# ---------------------------------------------------------------------------


class TestAssessmentRepository(unittest.TestCase):
    def setUp(self) -> None:
        self.store = _make_store()
        _insert_session(self.store)
        _insert_proposition(self.store)
        self.repo = AssessmentRepository(self.store)

    def _make_assessment(
        self,
        assessment_id: str = "asmnt_001",
        snapshot_seq: int = 1,
        status: str = "insufficient_evidence",
        supersedes: str | None = None,
    ) -> dict:
        return {
            "assessment_id": assessment_id,
            "session_id": "sess_001",
            "proposition_id": "prop_001",
            "assessment_type": "directional",
            "snapshot_seq": snapshot_seq,
            "status": status,
            "confidence_grade": "low",
            "confidence_rationale_json": json.dumps({}),
            "supporting_finding_ids_json": json.dumps([]),
            "opposing_finding_ids_json": json.dumps([]),
            "gap_memberships_json": json.dumps([]),
            "applied_inference_record_ids_json": json.dumps([]),
            "supersedes_assessment_id": supersedes,
            "payload_json": json.dumps({}),
            "schema_version": "v1",
        }

    def test_create_and_get_round_trip(self) -> None:
        self.repo.create(self._make_assessment())
        result = self.repo.get("asmnt_001")
        self.assertIsNotNone(result)
        self.assertEqual(result["assessment_id"], "asmnt_001")
        self.assertEqual(result["snapshot_seq"], 1)

    def test_get_deserializes_json_fields(self) -> None:
        self.repo.create(self._make_assessment())
        result = self.repo.get("asmnt_001")
        self.assertIsInstance(result["confidence_rationale_json"], dict)
        self.assertIsInstance(result["supporting_finding_ids_json"], list)
        self.assertIsInstance(result["gap_memberships_json"], list)

    def test_get_unknown_returns_none(self) -> None:
        self.assertIsNone(self.repo.get("asmnt_unknown"))

    def test_get_latest_returns_none_when_empty(self) -> None:
        result = self.repo.get_latest("prop_001")
        self.assertIsNone(result)

    def test_get_latest_single_snapshot(self) -> None:
        self.repo.create(self._make_assessment("asmnt_001", snapshot_seq=1))
        result = self.repo.get_latest("prop_001")
        self.assertIsNotNone(result)
        self.assertEqual(result["snapshot_seq"], 1)

    def test_get_latest_returns_highest_snapshot_seq(self) -> None:
        self.repo.create(self._make_assessment("asmnt_001", snapshot_seq=1))
        self.repo.create(self._make_assessment("asmnt_002", snapshot_seq=2, supersedes="asmnt_001"))
        self.repo.create(self._make_assessment("asmnt_003", snapshot_seq=3, supersedes="asmnt_002"))
        result = self.repo.get_latest("prop_001")
        self.assertEqual(result["snapshot_seq"], 3)
        self.assertEqual(result["assessment_id"], "asmnt_003")

    def test_list_by_proposition_ordered_by_seq(self) -> None:
        # Insert prerequisites first (FK constraint), then higher seqs
        # Insertion order: seq 1, 3, 2 — verify sorted output is [1, 2, 3]
        self.repo.create(self._make_assessment("asmnt_001", snapshot_seq=1))
        self.repo.create(self._make_assessment("asmnt_003", snapshot_seq=3, supersedes="asmnt_001"))
        self.repo.create(self._make_assessment("asmnt_002", snapshot_seq=2, supersedes="asmnt_001"))
        results = self.repo.list_by_proposition("prop_001")
        seqs = [r["snapshot_seq"] for r in results]
        self.assertEqual(seqs, [1, 2, 3])

    def test_list_by_session(self) -> None:
        self.repo.create(self._make_assessment("asmnt_001", snapshot_seq=1))
        results = self.repo.list_by_session("sess_001")
        self.assertEqual(len(results), 1)

    def test_next_snapshot_seq_empty(self) -> None:
        self.assertEqual(self.repo.next_snapshot_seq("prop_001"), 1)

    def test_next_snapshot_seq_after_inserts(self) -> None:
        self.repo.create(self._make_assessment("asmnt_001", snapshot_seq=1))
        self.repo.create(self._make_assessment("asmnt_002", snapshot_seq=2, supersedes="asmnt_001"))
        self.assertEqual(self.repo.next_snapshot_seq("prop_001"), 3)


# ---------------------------------------------------------------------------
# ActionProposalRepository tests
# ---------------------------------------------------------------------------


class TestActionProposalRepository(unittest.TestCase):
    def setUp(self) -> None:
        self.store = _make_store()
        _insert_session(self.store)
        self.repo = ActionProposalRepository(self.store)

    def _make_proposal(
        self,
        action_proposal_id: str = "ap_001",
        action_kind: str = "investigate_further",
        priority_rank: float = 1.0,
    ) -> dict:
        return {
            "action_proposal_id": action_proposal_id,
            "session_id": "sess_001",
            "action_kind": action_kind,
            "primary_assessment_ref_json": json.dumps(
                {"assessment_id": "asmnt_001", "proposition_id": "prop_001", "snapshot_seq": 1}
            ),
            "related_assessment_refs_json": json.dumps([]),
            "target_proposition_ref_json": json.dumps(
                {"session_id": "sess_001", "proposition_id": "prop_001"}
            ),
            "proposal_context_json": json.dumps(
                {"session_goal": None, "risk_budget": None, "policy_profile": "default"}
            ),
            "priority_axes_json": json.dumps([]),
            "priority_rank": priority_rank,
            "rationale_json": json.dumps({"summary": "needs investigation"}),
            "payload_json": json.dumps({}),
            "policy_version": "v1",
            "schema_version": "v1",
        }

    def test_create_and_get_round_trip(self) -> None:
        self.repo.create(self._make_proposal())
        result = self.repo.get("ap_001")
        self.assertIsNotNone(result)
        self.assertEqual(result["action_proposal_id"], "ap_001")
        self.assertEqual(result["action_kind"], "investigate_further")

    def test_get_deserializes_json_fields(self) -> None:
        self.repo.create(self._make_proposal())
        result = self.repo.get("ap_001")
        self.assertIsInstance(result["primary_assessment_ref_json"], dict)
        self.assertIsInstance(result["target_proposition_ref_json"], dict)
        self.assertIsInstance(result["proposal_context_json"], dict)
        self.assertIsInstance(result["rationale_json"], dict)

    def test_get_unknown_returns_none(self) -> None:
        self.assertIsNone(self.repo.get("ap_unknown"))

    def test_list_by_session_ordered_by_priority_rank(self) -> None:
        self.repo.create(self._make_proposal("ap_003", priority_rank=3.0))
        self.repo.create(self._make_proposal("ap_001", priority_rank=1.0))
        self.repo.create(self._make_proposal("ap_002", priority_rank=2.0))
        results = self.repo.list_by_session("sess_001")
        ranks = [r["priority_rank"] for r in results]
        self.assertEqual(ranks, [1.0, 2.0, 3.0])

    def test_list_by_session_empty(self) -> None:
        self.assertEqual(self.repo.list_by_session("sess_001"), [])

    def test_list_by_session_action_kind_filter(self) -> None:
        self.repo.create(
            self._make_proposal("ap_001", action_kind="investigate_further", priority_rank=1.0)
        )
        self.repo.create(self._make_proposal("ap_002", action_kind="escalate", priority_rank=2.0))
        results = self.repo.list_by_session("sess_001", action_kind="escalate")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["action_proposal_id"], "ap_002")


# ---------------------------------------------------------------------------
# EvidenceGapRepository tests
# ---------------------------------------------------------------------------


class TestEvidenceGapRepository(unittest.TestCase):
    def setUp(self) -> None:
        self.store = _make_store()
        _insert_session(self.store)
        _insert_proposition(self.store)
        _insert_assessment(self.store)
        _insert_inference_record(self.store)
        self.repo = EvidenceGapRepository(self.store)

    def _make_gap(
        self,
        gap_id: str = "gap_001",
        status: str = "open",
        resolved_by: str | None = None,
    ) -> dict:
        return {
            "gap_id": gap_id,
            "session_id": "sess_001",
            "proposition_id": "prop_001",
            "gap_kind": "missing_counterpart",
            "title": "missing baseline",
            "description": "no baseline observation",
            "status": status,
            "missing_requirement_json": json.dumps({"kind": "observation", "for": "baseline"}),
            "satisfiable_by_json": json.dumps([]),
            "related_finding_ids_json": json.dumps([]),
            "opened_by_inference_record_id": "ir_001",
            "resolved_by_inference_record_id": resolved_by,
            "schema_version": "v1",
            "resolved_at": None,
        }

    def test_create_and_get_round_trip(self) -> None:
        self.repo.create(self._make_gap())
        result = self.repo.get("gap_001")
        self.assertIsNotNone(result)
        self.assertEqual(result["gap_id"], "gap_001")
        self.assertEqual(result["gap_kind"], "missing_counterpart")

    def test_get_deserializes_json_fields(self) -> None:
        self.repo.create(self._make_gap())
        result = self.repo.get("gap_001")
        self.assertIsInstance(result["missing_requirement_json"], dict)
        self.assertIsInstance(result["satisfiable_by_json"], list)
        self.assertIsInstance(result["related_finding_ids_json"], list)

    def test_get_unknown_returns_none(self) -> None:
        self.assertIsNone(self.repo.get("gap_unknown"))

    def test_list_by_proposition_all(self) -> None:
        self.repo.create(self._make_gap("gap_001", status="open"))
        self.repo.create(self._make_gap("gap_002", status="resolved"))
        results = self.repo.list_by_proposition("prop_001")
        self.assertEqual(len(results), 2)

    def test_list_by_proposition_status_filter(self) -> None:
        self.repo.create(self._make_gap("gap_001", status="open"))
        self.repo.create(self._make_gap("gap_002", status="resolved"))
        open_gaps = self.repo.list_by_proposition("prop_001", status="open")
        self.assertEqual(len(open_gaps), 1)
        self.assertEqual(open_gaps[0]["gap_id"], "gap_001")

    def test_list_by_session(self) -> None:
        self.repo.create(self._make_gap("gap_001", status="open"))
        self.repo.create(self._make_gap("gap_002", status="resolved"))
        results = self.repo.list_by_session("sess_001")
        self.assertEqual(len(results), 2)

    def test_list_by_session_status_filter(self) -> None:
        self.repo.create(self._make_gap("gap_001", status="open"))
        self.repo.create(self._make_gap("gap_002", status="resolved"))
        open_gaps = self.repo.list_by_session("sess_001", status="open")
        self.assertEqual(len(open_gaps), 1)
        self.assertEqual(open_gaps[0]["gap_id"], "gap_001")

    def test_list_by_session_empty(self) -> None:
        self.assertEqual(self.repo.list_by_session("sess_001"), [])


# ---------------------------------------------------------------------------
# InferenceRecordRepository tests
# ---------------------------------------------------------------------------


class TestInferenceRecordRepository(unittest.TestCase):
    def setUp(self) -> None:
        self.store = _make_store()
        _insert_session(self.store)
        _insert_proposition(self.store)
        _insert_assessment(self.store)
        self.repo = InferenceRecordRepository(self.store)

    def _make_record(
        self,
        inference_record_id: str = "ir_001",
        assessment_id: str = "asmnt_001",
        result: str = "gap_opened",
    ) -> dict:
        return {
            "inference_record_id": inference_record_id,
            "session_id": "sess_001",
            "proposition_id": "prop_001",
            "assessment_id": assessment_id,
            "rule_id": "rule_no_evidence",
            "rule_version": "v1",
            "result": result,
            "input_finding_ids_json": json.dumps([]),
            "input_assessment_ids_json": json.dumps([]),
            "opened_gap_ids_json": json.dumps([]),
            "resolved_gap_ids_json": json.dumps([]),
            "produced_status_transition_json": None,
            "confidence_contribution_json": json.dumps({}),
            "justification_json": json.dumps({}),
            "schema_version": "v1",
        }

    def test_create_and_get_round_trip(self) -> None:
        self.repo.create(self._make_record())
        result = self.repo.get("ir_001")
        self.assertIsNotNone(result)
        self.assertEqual(result["inference_record_id"], "ir_001")
        self.assertEqual(result["rule_id"], "rule_no_evidence")

    def test_get_deserializes_json_fields(self) -> None:
        self.repo.create(self._make_record())
        result = self.repo.get("ir_001")
        self.assertIsInstance(result["input_finding_ids_json"], list)
        self.assertIsInstance(result["confidence_contribution_json"], dict)

    def test_get_unknown_returns_none(self) -> None:
        self.assertIsNone(self.repo.get("ir_unknown"))

    def test_list_by_assessment(self) -> None:
        _insert_assessment(self.store, "asmnt_002", snapshot_seq=2)
        self.repo.create(self._make_record("ir_001", assessment_id="asmnt_001"))
        self.repo.create(self._make_record("ir_002", assessment_id="asmnt_002"))
        results = self.repo.list_by_assessment("asmnt_001")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["inference_record_id"], "ir_001")

    def test_list_by_proposition(self) -> None:
        self.repo.create(self._make_record("ir_001"))
        results = self.repo.list_by_proposition("prop_001")
        self.assertEqual(len(results), 1)

    def test_null_status_transition_preserved(self) -> None:
        self.repo.create(self._make_record())
        result = self.repo.get("ir_001")
        self.assertIsNone(result["produced_status_transition_json"])


if __name__ == "__main__":
    unittest.main()
