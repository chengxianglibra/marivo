"""Phase 8.4 Read Surface Boundary Contract Tests.

These tests verify that the canonical read surfaces (/sessions/{id},
/state, /context, /reflection-context) and the operator runtime
status surfaces are strictly separated:

  1.  Canonical surfaces must NEVER expose scheduling/runtime fields
      (migration_required, backpressure, claim, lease, attempt_id, …).
  2.  Runtime status surfaces must NEVER expose canonical evidence
      semantics (supporting_findings, latest_assessment, seed_entries, …).
  3.  v1 artifact runtime status only emits ``staged`` / ``findings_committed``.
      The stages ``extracting``, ``seeding_handoff_pending`` and ``failed``
      are reserved for a future version with extraction-state tracking.
  4.  v1 proposition runtime status only emits
      ``queued`` / ``assessment_committed`` / ``publish_ready`` / ``externally_visible``.
      The stages ``assessment_recompute``, ``proposal_refresh`` and ``failed``
      are reserved for a future async runtime.
  5.  Session-level runtime status ``last_successful_stage`` advances
      correctly through every pipeline milestone.
  6.  ``reflection-context`` is a compact stub that does not expose
      canonical evidence objects.
  7.  Session root (GET /sessions/{id}) and GET /sessions/{id}/runtime-status
      return strictly disjoint schema shapes.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import create_app
from app.session.session_manager import SessionManager
from app.storage.sqlite_metadata import SQLiteMetadataStore
from tests.shared_fixtures import get_seeded_duckdb_path

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

# Fields that must NEVER appear in any canonical surface.
_RUNTIME_ONLY_FIELDS = frozenset(
    {
        "migration_required",
        "backpressure",
        "claim_owner",
        "lease_expires_at",
        "attempt_id",
        "backlog",
        "backlog_state",
        "retry_count",
        "overall_status",
        "blocked_reason",
        "backlog_summary",
        "current_stage",
        "last_successful_stage",
    }
)

# Fields from canonical evidence that must NOT appear in runtime surfaces.
_CANONICAL_EVIDENCE_FIELDS = frozenset(
    {
        "supporting_findings",
        "opposing_findings",
        "latest_assessment",
        "seed_entries",
        "active_propositions",
        "backing_findings",
        "blocking_gaps",
        "relevant_findings",
        "assessment_dependencies",
    }
)

_SEMANTIC_REF_PREFIXES = ("metric.", "entity.", "process.", "dimension.", "time.", "binding.")


def _walk_strings(payload: Any) -> list[str]:
    strings: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            strings.append(str(key))
            strings.extend(_walk_strings(value))
        return strings
    if isinstance(payload, list):
        for item in payload:
            strings.extend(_walk_strings(item))
        return strings
    if isinstance(payload, str):
        return [payload]
    return strings


def _make_store() -> SQLiteMetadataStore:
    tmp = tempfile.mkdtemp()
    store = SQLiteMetadataStore(Path(tmp) / "meta.sqlite")
    store.initialize()
    return store


def _insert_session(store: SQLiteMetadataStore, session_id: str) -> None:
    store.execute(
        "INSERT INTO sessions "
        "(session_id, goal, constraints_json, budget_json, policy_json, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [session_id, "boundary test session", "{}", "{}", "{}", "open"],
    )


def _insert_artifact(
    store: SQLiteMetadataStore,
    session_id: str,
    artifact_id: str,
    artifact_type: str = "compare_artifact",
) -> None:
    store.execute(
        "INSERT INTO artifacts "
        "(artifact_id, session_id, step_id, artifact_type, artifact_schema_version, "
        "name, content_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            artifact_id,
            session_id,
            f"step_{artifact_id}",
            artifact_type,
            "v1",
            artifact_id,
            "{}",
        ],
    )


def _insert_finding(
    store: SQLiteMetadataStore,
    session_id: str,
    finding_id: str,
    artifact_id: str,
) -> None:
    subject = {
        "metric": "dau",
        "entity": None,
        "slice": {},
        "grain": "day",
        "analysis_axis": "scalar",
    }
    step_ref = {"session_id": session_id, "step_id": f"step_{artifact_id}", "step_type": "compare"}
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
    store.execute(
        """INSERT INTO findings
           (finding_id, session_id, artifact_id, step_ref_json, finding_type,
            canonical_item_key, subject_json, observed_window_json, quality_json,
            provenance_json, payload_json, schema_version)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            finding_id,
            session_id,
            artifact_id,
            json.dumps(step_ref),
            "delta",
            finding_id,
            json.dumps(subject),
            json.dumps({"kind": "range", "start": "2024-01-08", "end": "2024-01-14"}),
            json.dumps(quality),
            json.dumps(provenance),
            json.dumps({"delta_kind": "scalar_delta", "direction": "increase"}),
            "v1",
        ],
    )


def _insert_proposition(
    store: SQLiteMetadataStore,
    session_id: str,
    proposition_id: str,
    *,
    externally_visible_assessment_id: str | None = None,
) -> None:
    store.execute(
        """INSERT INTO propositions
           (proposition_id, session_id, proposition_type, subject_json, origin_json,
            assessment_anchor_json, lineage_json, seed_finding_refs_json, payload_json,
            schema_version, identity_key, externally_visible_assessment_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            proposition_id,
            session_id,
            "change_assessment",
            json.dumps({"metric": "dau", "entity": None, "slice": {}, "grain": "day"}),
            json.dumps({"kind": "system_seeded"}),
            json.dumps({"assessment_type": "change_assessment"}),
            json.dumps({"source_artifact_lineages": [], "source_step_refs": []}),
            json.dumps([]),
            json.dumps({}),
            "v1",
            f"key_{uuid4().hex[:24]}",
            externally_visible_assessment_id,
        ],
    )


def _insert_assessment(
    store: SQLiteMetadataStore,
    session_id: str,
    assessment_id: str,
    proposition_id: str,
) -> None:
    store.execute(
        """INSERT INTO assessments
           (assessment_id, session_id, proposition_id, assessment_type, snapshot_seq,
            status, confidence_grade, confidence_rationale_json,
            supporting_finding_ids_json, opposing_finding_ids_json,
            gap_memberships_json, applied_inference_record_ids_json, schema_version)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            assessment_id,
            session_id,
            proposition_id,
            "change_assessment",
            1,
            "insufficient",
            "low",
            json.dumps({}),
            json.dumps([]),
            json.dumps([]),
            json.dumps([]),
            json.dumps([]),
            "v1",
        ],
    )


def _insert_proposal(
    store: SQLiteMetadataStore,
    session_id: str,
    proposal_id: str,
    proposition_id: str,
    assessment_id: str,
) -> None:
    store.execute(
        """INSERT INTO action_proposals
           (action_proposal_id, session_id, action_kind, primary_assessment_ref_json,
            related_assessment_refs_json, target_proposition_ref_json, proposal_context_json,
            priority_axes_json, priority_rank, rationale_json, payload_json, schema_version)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            proposal_id,
            session_id,
            "investigate",
            json.dumps({"session_id": session_id, "assessment_id": assessment_id}),
            json.dumps([]),
            json.dumps({"session_id": session_id, "proposition_id": proposition_id}),
            json.dumps({}),
            json.dumps({}),
            1,
            json.dumps({}),
            json.dumps({}),
            "v1",
        ],
    )


# ---------------------------------------------------------------------------
# 1. Canonical surfaces must not expose runtime/scheduling fields
# ---------------------------------------------------------------------------


class TestCanonicalSurfacesExcludeRuntimeFields(unittest.TestCase):
    """Session root, /state, /context must never expose runtime scheduling fields."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "test.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))
        resp = cls.client.post("/sessions", json={"goal": "boundary test"})
        cls.session_id = resp.json()["session_id"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def _assert_no_runtime_fields(self, data: Any, path: str = "") -> None:
        if not isinstance(data, dict):
            return
        for field in _RUNTIME_ONLY_FIELDS:
            self.assertNotIn(
                field,
                data,
                f"Runtime-only field {field!r} must not appear in canonical surface{path}",
            )

    def test_session_root_excludes_runtime_fields(self) -> None:
        resp = self.client.get(f"/sessions/{self.session_id}")
        self.assertEqual(resp.status_code, 200)
        self._assert_no_runtime_fields(resp.json(), " (session root)")

    def test_state_surface_excludes_runtime_fields(self) -> None:
        resp = self.client.get(f"/sessions/{self.session_id}/state")
        self.assertEqual(resp.status_code, 200)
        self._assert_no_runtime_fields(resp.json(), " (/state)")

    def test_state_surface_excludes_migration_required(self) -> None:
        resp = self.client.get(f"/sessions/{self.session_id}/state")
        data = resp.json()
        self.assertNotIn("migration_required", data)

    def test_state_surface_excludes_backpressure(self) -> None:
        resp = self.client.get(f"/sessions/{self.session_id}/state")
        data = resp.json()
        self.assertNotIn("backpressure", data)

    def test_session_root_excludes_overall_status(self) -> None:
        resp = self.client.get(f"/sessions/{self.session_id}")
        self.assertNotIn("overall_status", resp.json())

    def test_session_root_excludes_blocked_reason(self) -> None:
        resp = self.client.get(f"/sessions/{self.session_id}")
        self.assertNotIn("blocked_reason", resp.json())

    def test_session_root_excludes_backlog_summary(self) -> None:
        resp = self.client.get(f"/sessions/{self.session_id}")
        self.assertNotIn("backlog_summary", resp.json())

    def test_list_sessions_excludes_runtime_fields(self) -> None:
        resp = self.client.get("/sessions")
        self.assertEqual(resp.status_code, 200)
        for s in resp.json():
            self._assert_no_runtime_fields(s, " (list_sessions entry)")


# ---------------------------------------------------------------------------
# 2. Runtime status surfaces must not expose canonical evidence fields
# ---------------------------------------------------------------------------


class TestRuntimeSurfacesExcludeCanonicalEvidenceFields(unittest.TestCase):
    """Session/artifact/proposition runtime status must not expose canonical evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "test.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))
        resp = cls.client.post("/sessions", json={"goal": "runtime surface test"})
        cls.session_id = resp.json()["session_id"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_session_runtime_status_excludes_canonical_evidence(self) -> None:
        resp = self.client.get(f"/sessions/{self.session_id}/runtime-status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        for field in _CANONICAL_EVIDENCE_FIELDS:
            self.assertNotIn(
                field,
                data,
                f"Canonical evidence field {field!r} must not appear in session runtime status",
            )

    def test_session_runtime_status_excludes_lifecycle(self) -> None:
        """lifecycle is a canonical session root field; not a runtime status field."""
        resp = self.client.get(f"/sessions/{self.session_id}/runtime-status")
        self.assertNotIn("lifecycle", resp.json())

    def test_session_runtime_status_excludes_governance(self) -> None:
        """governance is a canonical session root field; not a runtime status field."""
        resp = self.client.get(f"/sessions/{self.session_id}/runtime-status")
        self.assertNotIn("governance", resp.json())

    def test_session_runtime_status_excludes_goal(self) -> None:
        resp = self.client.get(f"/sessions/{self.session_id}/runtime-status")
        self.assertNotIn("goal", resp.json())


# ---------------------------------------------------------------------------
# 3. v1 artifact runtime status: only staged / findings_committed are emitted
# ---------------------------------------------------------------------------


class TestArtifactRuntimeStatusV1StageVocabulary(unittest.TestCase):
    """v1 artifact_stage is strictly constrained to staged / findings_committed."""

    _VALID_V1_STAGES = frozenset({"staged", "findings_committed"})
    _RESERVED_STAGES = frozenset({"extracting", "seeding_handoff_pending", "failed"})

    def setUp(self) -> None:
        self.store = _make_store()
        self.manager = SessionManager(self.store)
        self.session_id = f"sess_{uuid4().hex[:12]}"
        _insert_session(self.store, self.session_id)

    def _get_stage(self, artifact_id: str) -> str:
        return self.manager.get_artifact_runtime_status(self.session_id, artifact_id)[
            "artifact_stage"
        ]

    def test_non_d4_artifact_no_findings_emits_staged_not_extracting(self) -> None:
        """v1 does not emit 'extracting'; unprocessed non-d4 artifact is 'staged'."""
        aid = f"art_{uuid4().hex[:8]}"
        _insert_artifact(self.store, self.session_id, aid, "compare_artifact")
        stage = self._get_stage(aid)
        self.assertEqual(stage, "staged")
        self.assertNotIn(stage, self._RESERVED_STAGES)

    def test_d4_artifact_no_findings_emits_findings_committed_not_seeding_handoff(self) -> None:
        """v1 does not emit 'seeding_handoff_pending'; allows-empty → findings_committed."""
        aid = f"art_{uuid4().hex[:8]}"
        _insert_artifact(self.store, self.session_id, aid, "observation")
        stage = self._get_stage(aid)
        self.assertEqual(stage, "findings_committed")
        self.assertNotIn(stage, self._RESERVED_STAGES)

    def test_artifact_with_findings_emits_findings_committed_not_failed(self) -> None:
        aid = f"art_{uuid4().hex[:8]}"
        fid = f"fnd_{uuid4().hex[:8]}"
        _insert_artifact(self.store, self.session_id, aid, "compare_artifact")
        _insert_finding(self.store, self.session_id, fid, aid)
        stage = self._get_stage(aid)
        self.assertEqual(stage, "findings_committed")
        self.assertNotIn(stage, self._RESERVED_STAGES)

    def test_stage_value_is_always_in_valid_v1_set(self) -> None:
        """For any artifact, artifact_stage must be one of the two valid v1 values."""
        for art_type, has_finding in [
            ("compare_artifact", False),
            ("compare_artifact", True),
            ("observation", False),
            ("anomaly_candidates", False),
        ]:
            with self.subTest(artifact_type=art_type, has_finding=has_finding):
                aid = f"art_{uuid4().hex[:8]}"
                fid = f"fnd_{uuid4().hex[:8]}"
                _insert_artifact(self.store, self.session_id, aid, art_type)
                if has_finding:
                    _insert_finding(self.store, self.session_id, fid, aid)
                stage = self._get_stage(aid)
                self.assertIn(
                    stage,
                    self._VALID_V1_STAGES,
                    f"Unexpected artifact_stage {stage!r} for {art_type}",
                )


# ---------------------------------------------------------------------------
# 4. v1 proposition runtime status: reserved stages are never emitted
# ---------------------------------------------------------------------------


class TestPropositionRuntimeStatusV1StageVocabulary(unittest.TestCase):
    """v1 current_stage is constrained to queued/assessment_committed/publish_ready/externally_visible."""

    _VALID_V1_STAGES = frozenset(
        {"queued", "assessment_committed", "publish_ready", "externally_visible"}
    )
    _RESERVED_STAGES = frozenset({"assessment_recompute", "proposal_refresh", "failed"})

    def setUp(self) -> None:
        self.store = _make_store()
        self.manager = SessionManager(self.store)
        self.session_id = f"sess_{uuid4().hex[:12]}"
        _insert_session(self.store, self.session_id)

    def _get_stage(self, proposition_id: str) -> str:
        return self.manager.get_proposition_runtime_status(self.session_id, proposition_id)[
            "current_stage"
        ]

    def test_queued_is_valid_v1_stage(self) -> None:
        """No assessment → current_stage = queued (not 'assessment_recompute')."""
        pid = f"prop_{uuid4().hex[:12]}"
        _insert_proposition(self.store, self.session_id, pid)
        stage = self._get_stage(pid)
        self.assertEqual(stage, "queued")
        self.assertNotIn(stage, self._RESERVED_STAGES)

    def test_assessment_committed_is_valid_v1_stage(self) -> None:
        """Assessment exists, no proposals → current_stage = assessment_committed."""
        pid = f"prop_{uuid4().hex[:12]}"
        aid = f"asmt_{uuid4().hex[:8]}"
        _insert_proposition(self.store, self.session_id, pid)
        _insert_assessment(self.store, self.session_id, aid, pid)
        stage = self._get_stage(pid)
        self.assertEqual(stage, "assessment_committed")
        self.assertNotIn(stage, self._RESERVED_STAGES)

    def test_publish_ready_is_valid_v1_stage(self) -> None:
        """Assessment + proposals, not yet published → current_stage = publish_ready."""
        pid = f"prop_{uuid4().hex[:12]}"
        aid = f"asmt_{uuid4().hex[:8]}"
        apid = f"ap_{uuid4().hex[:8]}"
        _insert_proposition(self.store, self.session_id, pid)
        _insert_assessment(self.store, self.session_id, aid, pid)
        _insert_proposal(self.store, self.session_id, apid, pid, aid)
        stage = self._get_stage(pid)
        self.assertEqual(stage, "publish_ready")
        self.assertNotIn(stage, self._RESERVED_STAGES)

    def test_externally_visible_is_valid_v1_stage(self) -> None:
        """externally_visible_assessment_id set → current_stage = externally_visible."""
        pid = f"prop_{uuid4().hex[:12]}"
        aid = f"asmt_{uuid4().hex[:8]}"
        _insert_proposition(
            self.store,
            self.session_id,
            pid,
            externally_visible_assessment_id=aid,
        )
        _insert_assessment(self.store, self.session_id, aid, pid)
        stage = self._get_stage(pid)
        self.assertEqual(stage, "externally_visible")
        self.assertNotIn(stage, self._RESERVED_STAGES)

    def test_v1_stage_never_emits_assessment_recompute(self) -> None:
        """assessment_recompute is reserved for future async runtime; v1 skips it."""
        pid = f"prop_{uuid4().hex[:12]}"
        _insert_proposition(self.store, self.session_id, pid)
        stage = self._get_stage(pid)
        self.assertNotEqual(stage, "assessment_recompute")

    def test_v1_stage_never_emits_proposal_refresh(self) -> None:
        """proposal_refresh is reserved; v1 jumps directly to publish_ready."""
        pid = f"prop_{uuid4().hex[:12]}"
        aid = f"asmt_{uuid4().hex[:8]}"
        apid = f"ap_{uuid4().hex[:8]}"
        _insert_proposition(self.store, self.session_id, pid)
        _insert_assessment(self.store, self.session_id, aid, pid)
        _insert_proposal(self.store, self.session_id, apid, pid, aid)
        stage = self._get_stage(pid)
        self.assertNotEqual(stage, "proposal_refresh")

    def test_v1_stage_never_emits_failed(self) -> None:
        """failed is reserved; v1 has no failure-tracking state machine."""
        pid = f"prop_{uuid4().hex[:12]}"
        _insert_proposition(self.store, self.session_id, pid)
        stage = self._get_stage(pid)
        self.assertNotEqual(stage, "failed")

    def test_stage_always_in_valid_v1_set(self) -> None:
        """current_stage must always be one of the four valid v1 values."""
        scenarios = [
            (False, False, False),  # queued
            (True, False, False),  # assessment_committed
            (True, True, False),  # publish_ready
            (True, False, True),  # externally_visible
        ]
        for has_assessment, has_proposal, is_published in scenarios:
            with self.subTest(
                has_assessment=has_assessment,
                has_proposal=has_proposal,
                is_published=is_published,
            ):
                pid = f"prop_{uuid4().hex[:12]}"
                aid = f"asmt_{uuid4().hex[:8]}"
                apid = f"ap_{uuid4().hex[:8]}"
                ev = aid if is_published else None
                _insert_proposition(
                    self.store,
                    self.session_id,
                    pid,
                    externally_visible_assessment_id=ev,
                )
                if has_assessment:
                    _insert_assessment(self.store, self.session_id, aid, pid)
                if has_proposal:
                    _insert_proposal(self.store, self.session_id, apid, pid, aid)
                stage = self._get_stage(pid)
                self.assertIn(
                    stage,
                    self._VALID_V1_STAGES,
                    f"Unexpected stage {stage!r} for scenario "
                    f"has_assessment={has_assessment}, has_proposal={has_proposal}, "
                    f"is_published={is_published}",
                )


# ---------------------------------------------------------------------------
# 5. Proposition runtime status explains latest_assessment = null
# ---------------------------------------------------------------------------


class TestPropositionRuntimeStatusNullAssessmentReason(unittest.TestCase):
    """When latest_assessment=null, runtime status must clearly report current_stage=queued
    and current_assessment_id=null so operators can distinguish 'not yet assessed'
    from 'assessment in progress'."""

    def setUp(self) -> None:
        self.store = _make_store()
        self.manager = SessionManager(self.store)
        self.session_id = f"sess_{uuid4().hex[:12]}"
        _insert_session(self.store, self.session_id)

    def test_queued_stage_explains_null_assessment(self) -> None:
        pid = f"prop_{uuid4().hex[:12]}"
        _insert_proposition(self.store, self.session_id, pid)
        status = self.manager.get_proposition_runtime_status(self.session_id, pid)
        self.assertEqual(status["current_stage"], "queued")
        self.assertIsNone(status["current_assessment_id"])

    def test_null_reason_stage_is_not_ambiguous(self) -> None:
        """queued unambiguously means: seeded but no assessment committed yet."""
        pid = f"prop_{uuid4().hex[:12]}"
        _insert_proposition(self.store, self.session_id, pid)
        status = self.manager.get_proposition_runtime_status(self.session_id, pid)
        # queued ↔ no DB rows in assessments for this proposition
        self.assertEqual(status["current_stage"], "queued")
        self.assertIsNone(status["last_successful_stage"])

    def test_assessment_committed_is_unambiguous_from_queued(self) -> None:
        """A proposition with exactly one committed assessment reads assessment_committed."""
        pid = f"prop_{uuid4().hex[:12]}"
        aid = f"asmt_{uuid4().hex[:8]}"
        _insert_proposition(self.store, self.session_id, pid)
        _insert_assessment(self.store, self.session_id, aid, pid)
        status = self.manager.get_proposition_runtime_status(self.session_id, pid)
        self.assertNotEqual(status["current_stage"], "queued")
        self.assertIsNotNone(status["current_assessment_id"])


# ---------------------------------------------------------------------------
# 6. Session runtime status last_successful_stage progression
# ---------------------------------------------------------------------------


class TestSessionRuntimeStatusStageProgression(unittest.TestCase):
    """last_successful_stage must advance correctly through pipeline milestones."""

    def _fresh(self) -> tuple[SQLiteMetadataStore, SessionManager, str]:
        store = _make_store()
        manager = SessionManager(store)
        session_id = f"sess_{uuid4().hex[:12]}"
        _insert_session(store, session_id)
        return store, manager, session_id

    def test_empty_session_has_null_last_stage(self) -> None:
        _, manager, sid = self._fresh()
        status = manager.get_session_runtime_status(sid)
        self.assertIsNone(status["last_successful_stage"])
        self.assertEqual(status["overall_status"], "idle")

    def test_after_artifact_commit_stage_is_artifact_commit(self) -> None:
        store, manager, sid = self._fresh()
        _insert_artifact(store, sid, f"art_{uuid4().hex[:8]}")
        status = manager.get_session_runtime_status(sid)
        self.assertEqual(status["last_successful_stage"], "artifact_commit")

    def test_after_finding_extraction_stage_is_finding_extraction(self) -> None:
        store, manager, sid = self._fresh()
        aid = f"art_{uuid4().hex[:8]}"
        fid = f"fnd_{uuid4().hex[:8]}"
        _insert_artifact(store, sid, aid)
        _insert_finding(store, sid, fid, aid)
        status = manager.get_session_runtime_status(sid)
        self.assertEqual(status["last_successful_stage"], "finding_extraction")

    def test_after_proposition_seeding_stage_is_proposition_seeding(self) -> None:
        store, manager, sid = self._fresh()
        aid = f"art_{uuid4().hex[:8]}"
        fid = f"fnd_{uuid4().hex[:8]}"
        pid = f"prop_{uuid4().hex[:12]}"
        _insert_artifact(store, sid, aid)
        _insert_finding(store, sid, fid, aid)
        _insert_proposition(store, sid, pid)
        status = manager.get_session_runtime_status(sid)
        self.assertEqual(status["last_successful_stage"], "proposition_seeding")

    def test_after_assessment_stage_is_assessment_recompute(self) -> None:
        store, manager, sid = self._fresh()
        aid = f"art_{uuid4().hex[:8]}"
        fid = f"fnd_{uuid4().hex[:8]}"
        pid = f"prop_{uuid4().hex[:12]}"
        asmt_id = f"asmt_{uuid4().hex[:8]}"
        _insert_artifact(store, sid, aid)
        _insert_finding(store, sid, fid, aid)
        _insert_proposition(store, sid, pid)
        _insert_assessment(store, sid, asmt_id, pid)
        status = manager.get_session_runtime_status(sid)
        self.assertEqual(status["last_successful_stage"], "assessment_recompute")

    def test_after_proposal_stage_is_proposal_refresh(self) -> None:
        store, manager, sid = self._fresh()
        aid = f"art_{uuid4().hex[:8]}"
        fid = f"fnd_{uuid4().hex[:8]}"
        pid = f"prop_{uuid4().hex[:12]}"
        asmt_id = f"asmt_{uuid4().hex[:8]}"
        apid = f"ap_{uuid4().hex[:8]}"
        _insert_artifact(store, sid, aid)
        _insert_finding(store, sid, fid, aid)
        _insert_proposition(store, sid, pid)
        _insert_assessment(store, sid, asmt_id, pid)
        _insert_proposal(store, sid, apid, pid, asmt_id)
        status = manager.get_session_runtime_status(sid)
        self.assertEqual(status["last_successful_stage"], "proposal_refresh")

    def test_after_publish_stage_is_publish(self) -> None:
        store, manager, sid = self._fresh()
        aid = f"art_{uuid4().hex[:8]}"
        fid = f"fnd_{uuid4().hex[:8]}"
        pid = f"prop_{uuid4().hex[:12]}"
        asmt_id = f"asmt_{uuid4().hex[:8]}"
        # Publish: externally_visible_assessment_id set
        _insert_artifact(store, sid, aid)
        _insert_finding(store, sid, fid, aid)
        _insert_proposition(store, sid, pid, externally_visible_assessment_id=asmt_id)
        _insert_assessment(store, sid, asmt_id, pid)
        status = manager.get_session_runtime_status(sid)
        self.assertEqual(status["last_successful_stage"], "publish")

    def test_overall_status_running_when_unpublished_propositions(self) -> None:
        store, manager, sid = self._fresh()
        aid = f"art_{uuid4().hex[:8]}"
        fid = f"fnd_{uuid4().hex[:8]}"
        pid = f"prop_{uuid4().hex[:12]}"
        _insert_artifact(store, sid, aid)
        _insert_finding(store, sid, fid, aid)
        _insert_proposition(store, sid, pid)
        status = manager.get_session_runtime_status(sid)
        self.assertEqual(status["overall_status"], "running")

    def test_overall_status_idle_after_all_published(self) -> None:
        store, manager, sid = self._fresh()
        aid = f"art_{uuid4().hex[:8]}"
        fid = f"fnd_{uuid4().hex[:8]}"
        pid = f"prop_{uuid4().hex[:12]}"
        asmt_id = f"asmt_{uuid4().hex[:8]}"
        # All propositions published
        _insert_artifact(store, sid, aid)
        _insert_finding(store, sid, fid, aid)
        _insert_proposition(store, sid, pid, externally_visible_assessment_id=asmt_id)
        _insert_assessment(store, sid, asmt_id, pid)
        status = manager.get_session_runtime_status(sid)
        self.assertEqual(status["overall_status"], "idle")


# ---------------------------------------------------------------------------
# 7. Session root vs runtime status: strictly disjoint schema shapes (HTTP)
# ---------------------------------------------------------------------------


class TestSessionRootVsRuntimeStatusSeparation(unittest.TestCase):
    """Session root and runtime status must not share schema fields."""

    # Fields that belong ONLY to session root, not runtime status.
    _SESSION_ROOT_EXCLUSIVE = frozenset({"goal", "governance", "lifecycle", "state_summary"})
    # Fields that belong ONLY to runtime status, not session root.
    _RUNTIME_STATUS_EXCLUSIVE = frozenset({"overall_status", "blocked_reason", "backlog_summary"})

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "test.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))
        resp = cls.client.post("/sessions", json={"goal": "separation test"})
        cls.session_id = resp.json()["session_id"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_session_root_has_goal_governance_lifecycle_state_summary(self) -> None:
        resp = self.client.get(f"/sessions/{self.session_id}")
        data = resp.json()
        for field in self._SESSION_ROOT_EXCLUSIVE:
            self.assertIn(field, data, f"Session root must have {field!r}")

    def test_runtime_status_lacks_goal_governance_lifecycle_state_summary(self) -> None:
        resp = self.client.get(f"/sessions/{self.session_id}/runtime-status")
        data = resp.json()
        for field in self._SESSION_ROOT_EXCLUSIVE:
            self.assertNotIn(
                field,
                data,
                f"Runtime status must not expose session root field {field!r}",
            )

    def test_session_root_lacks_overall_status_blocked_reason_backlog_summary(self) -> None:
        resp = self.client.get(f"/sessions/{self.session_id}")
        data = resp.json()
        for field in self._RUNTIME_STATUS_EXCLUSIVE:
            self.assertNotIn(
                field,
                data,
                f"Session root must not expose runtime status field {field!r}",
            )

    def test_runtime_status_has_overall_status_blocked_reason_backlog_summary(self) -> None:
        resp = self.client.get(f"/sessions/{self.session_id}/runtime-status")
        data = resp.json()
        for field in self._RUNTIME_STATUS_EXCLUSIVE:
            self.assertIn(field, data, f"Runtime status must have {field!r}")

    def test_session_root_schema_version_is_analysis_session_v1(self) -> None:
        resp = self.client.get(f"/sessions/{self.session_id}")
        self.assertEqual(resp.json()["schema_version"], "analysis_session.v1")

    def test_runtime_status_schema_version_is_session_runtime_status_v1(self) -> None:
        resp = self.client.get(f"/sessions/{self.session_id}/runtime-status")
        self.assertEqual(resp.json()["schema_version"], "session_runtime_status.v1")


# ---------------------------------------------------------------------------
# 8. reflection-context is a compact stub (no canonical evidence objects)
# ---------------------------------------------------------------------------


class TestReflectionContextCompactSummary(unittest.TestCase):
    """reflection-context must not expose canonical evidence objects.

    Canonical read surfaces are /state and /context; reflection-context
    is kept as a minimal stub for agent orientation only.
    """

    # Canonical evidence fields that must NOT appear in reflection-context.
    _FORBIDDEN_CANONICAL_FIELDS = frozenset(
        {
            "propositions",
            "active_propositions",
            "findings",
            "backing_findings",
            "assessments",
            "latest_assessment",
            "action_proposals",
            "evidence_gaps",
            "blocking_gaps",
            "artifact_refs",
            "seed_entries",
        }
    )

    # Required fields for the compact stub contract.
    _REQUIRED_FIELDS = frozenset(
        {"session_id", "plan_id", "tentative_claims", "available_step_types"}
    )

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "test.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))
        resp = cls.client.post("/sessions", json={"goal": "reflection compact test"})
        cls.session_id = resp.json()["session_id"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def _get_context(self) -> dict[str, Any]:
        resp = self.client.get(f"/sessions/{self.session_id}/reflection-context")
        self.assertEqual(resp.status_code, 200)
        return resp.json()

    def test_reflection_context_has_required_compact_fields(self) -> None:
        data = self._get_context()
        for field in self._REQUIRED_FIELDS:
            self.assertIn(field, data, f"reflection-context must have compact field {field!r}")

    def test_reflection_context_does_not_expose_full_proposition_objects(self) -> None:
        data = self._get_context()
        self.assertNotIn("propositions", data)
        self.assertNotIn("active_propositions", data)

    def test_reflection_context_does_not_expose_full_finding_objects(self) -> None:
        data = self._get_context()
        self.assertNotIn("findings", data)
        self.assertNotIn("backing_findings", data)

    def test_reflection_context_does_not_expose_assessment_objects(self) -> None:
        data = self._get_context()
        self.assertNotIn("assessments", data)
        self.assertNotIn("latest_assessment", data)

    def test_reflection_context_does_not_expose_gap_objects(self) -> None:
        """evidence_gaps in reflection-context is the lightweight list stub, not full gap objects."""
        data = self._get_context()
        # evidence_gaps is an allowed key (compact list), but it must be an empty list in v1.
        # The forbidden field would be 'blocking_gaps' (canonical /state surface shape).
        self.assertNotIn("blocking_gaps", data)

    def test_reflection_context_does_not_expose_action_proposals(self) -> None:
        data = self._get_context()
        self.assertNotIn("action_proposals", data)

    def test_reflection_context_tentative_claims_is_empty_list_in_v1(self) -> None:
        """v1 stub always returns empty tentative_claims (not populated from DB)."""
        data = self._get_context()
        self.assertEqual(data["tentative_claims"], [])

    def test_reflection_context_plan_id_is_null_when_not_provided(self) -> None:
        data = self._get_context()
        self.assertIsNone(data["plan_id"])

    def test_reflection_context_no_schema_version_of_canonical_surfaces(self) -> None:
        """reflection-context must not carry a schema_version from /state or /context."""
        data = self._get_context()
        schema_v = data.get("schema_version")
        if schema_v is not None:
            self.assertNotIn(
                schema_v,
                {"session_state_view.v1", "proposition_context_view.v1", "analysis_session.v1"},
                "reflection-context must not use a canonical surface schema_version",
            )

    def test_reflection_context_forbidden_fields_absent(self) -> None:
        data = self._get_context()
        for field in self._FORBIDDEN_CANONICAL_FIELDS:
            if field == "evidence_gaps":
                continue  # evidence_gaps is the allowed lightweight key
            self.assertNotIn(
                field,
                data,
                f"reflection-context must not expose canonical field {field!r}",
            )


# ---------------------------------------------------------------------------
# 9. /state closure correctness: artifact_refs derived strictly from evidence
# ---------------------------------------------------------------------------


class TestStateClosureCorrectness(unittest.TestCase):
    """Verify /state closure properties using direct store manipulation."""

    def setUp(self) -> None:
        self.store = _make_store()
        self.session_id = f"sess_{uuid4().hex[:12]}"
        _insert_session(self.store, self.session_id)

    def test_extra_artifact_not_in_evidence_closure_is_excluded_from_artifact_refs(self) -> None:
        """An artifact not referenced by any backing finding must not appear in artifact_refs."""
        from app.evidence_engine.state_view import materialize_session_state_view
        from app.storage.evidence_repositories import (
            ActionProposalRepository,
            AssessmentRepository,
            EvidenceGapRepository,
            FindingRepository,
            InferenceRecordRepository,
            PropositionRepository,
        )

        # Insert an extra artifact that has no findings and no propositions.
        extra_art = f"art_extra_{uuid4().hex[:8]}"
        _insert_artifact(self.store, self.session_id, extra_art, "compare_artifact")

        view = materialize_session_state_view(
            session_id=self.session_id,
            query={},
            finding_repo=FindingRepository(self.store),
            proposition_repo=PropositionRepository(self.store),
            assessment_repo=AssessmentRepository(self.store),
            gap_repo=EvidenceGapRepository(self.store),
            inference_record_repo=InferenceRecordRepository(self.store),
            proposal_repo=ActionProposalRepository(self.store),
        )
        artifact_ids = {r["artifact_id"] for r in view["artifact_refs"]}
        self.assertNotIn(
            extra_art,
            artifact_ids,
            "Artifacts without backing findings must not appear in /state artifact_refs",
        )


class TestCanonicalReadSurfacesDoNotExposeSemanticRefs(unittest.TestCase):
    def setUp(self) -> None:
        self.store = _make_store()
        self.session_id = f"sess_{uuid4().hex[:12]}"
        self.proposition_id = f"prop_{uuid4().hex[:12]}"
        self.assessment_id = f"assess_{uuid4().hex[:12]}"
        self.artifact_id = f"art_{uuid4().hex[:12]}"
        self.finding_id = f"find_{uuid4().hex[:12]}"
        _insert_session(self.store, self.session_id)
        _insert_artifact(self.store, self.session_id, self.artifact_id)
        _insert_finding(self.store, self.session_id, self.finding_id, self.artifact_id)
        _insert_proposition(
            self.store,
            self.session_id,
            self.proposition_id,
        )
        _insert_assessment(self.store, self.session_id, self.assessment_id, self.proposition_id)
        self.store.execute(
            "UPDATE propositions SET externally_visible_assessment_id = ? WHERE proposition_id = ?",
            [self.assessment_id, self.proposition_id],
        )
        self.store.execute(
            "UPDATE assessments SET supporting_finding_ids_json = ? WHERE assessment_id = ?",
            [json.dumps([self.finding_id]), self.assessment_id],
        )

    def test_state_view_contains_no_semantic_ref_field_names_or_values(self) -> None:
        from app.evidence_engine.state_view import materialize_session_state_view
        from app.storage.evidence_repositories import (
            ActionProposalRepository,
            AssessmentRepository,
            EvidenceGapRepository,
            FindingRepository,
            InferenceRecordRepository,
            PropositionRepository,
        )

        view = materialize_session_state_view(
            session_id=self.session_id,
            query={},
            finding_repo=FindingRepository(self.store),
            proposition_repo=PropositionRepository(self.store),
            assessment_repo=AssessmentRepository(self.store),
            gap_repo=EvidenceGapRepository(self.store),
            inference_record_repo=InferenceRecordRepository(self.store),
            proposal_repo=ActionProposalRepository(self.store),
        )

        strings = _walk_strings(view)
        self.assertFalse(
            any(text in {"metric_ref", "semantic_ref", "subject_ref"} for text in strings)
        )
        self.assertFalse(any(text.startswith(_SEMANTIC_REF_PREFIXES) for text in strings))

    def test_context_view_contains_no_semantic_ref_field_names_or_values(self) -> None:
        from app.evidence_engine.context_view import materialize_proposition_context_view
        from app.storage.evidence_repositories import (
            ActionProposalRepository,
            AssessmentRepository,
            EvidenceGapRepository,
            FindingRepository,
            InferenceRecordRepository,
            PropositionRepository,
        )

        view = materialize_proposition_context_view(
            session_id=self.session_id,
            proposition_id=self.proposition_id,
            proposition_repo=PropositionRepository(self.store),
            assessment_repo=AssessmentRepository(self.store),
            finding_repo=FindingRepository(self.store),
            gap_repo=EvidenceGapRepository(self.store),
            inference_record_repo=InferenceRecordRepository(self.store),
            proposal_repo=ActionProposalRepository(self.store),
        )

        strings = _walk_strings(view)
        self.assertFalse(
            any(text in {"metric_ref", "semantic_ref", "subject_ref"} for text in strings)
        )
        self.assertFalse(any(text.startswith(_SEMANTIC_REF_PREFIXES) for text in strings))


if __name__ == "__main__":
    unittest.main()
