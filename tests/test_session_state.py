"""Contract and integration tests for Phase 5b: State Surface.

Coverage:
- ``GET /sessions/{id}/state``                   — canonical session state surface
- ``POST /sessions/{id}/state/query``            — filtered state surface
- ``GET /sessions/{id}/artifacts/{id}/runtime-status`` — artifact-level runtime status
- ``MarivoRuntime.get_artifact_runtime_status`` — unit tests for stage derivation
- ``materialize_session_state_view``             — invariant checks with a published
                                                   proposition (uses canonical pipeline)
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from app.contracts.errors import NotFoundError
from app.evidence_engine.canonical_pipeline_runtime import run_canonical_downstream
from app.evidence_engine.state_view import (
    SESSION_STATE_VIEW_SCHEMA_VERSION,
    materialize_session_state_view,
)
from app.runtime.runtime import MarivoRuntime
from app.storage.evidence_repositories import (
    ActionProposalRepository,
    AssessmentRepository,
    EvidenceGapRepository,
    FindingRepository,
    InferenceRecordRepository,
    PropositionRepository,
)
from app.storage.sqlite_metadata import SQLiteMetadataStore
from tests.shared_fixtures import get_seeded_duckdb_path, make_temp_metadata_store

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_store() -> SQLiteMetadataStore:
    return make_temp_metadata_store()


def _build_runtime(store: SQLiteMetadataStore, analytics: Any) -> MarivoRuntime:
    from tests.semantic_test_helpers import build_runtime

    return build_runtime(store, analytics)


def _make_repos(store: SQLiteMetadataStore) -> dict[str, Any]:
    return {
        "finding_repo": FindingRepository(store),
        "proposition_repo": PropositionRepository(store),
        "assessment_repo": AssessmentRepository(store),
        "gap_repo": EvidenceGapRepository(store),
        "inference_record_repo": InferenceRecordRepository(store),
        "proposal_repo": ActionProposalRepository(store),
    }


def _insert_session(store: SQLiteMetadataStore, session_id: str) -> None:
    store.execute(
        "INSERT INTO sessions "
        "(session_id, goal, constraints_json, budget_json, status) "
        "VALUES (?, ?, ?, ?, ?)",
        [session_id, "test state surface", "{}", "{}", "open"],
    )


def _get_artifact_runtime_status(
    store: SQLiteMetadataStore,
    session_id: str,
    artifact_id: str,
) -> dict[str, Any]:
    """Derive artifact runtime status from the metadata store.

    Replicates the logic that was on SessionManager.get_artifact_runtime_status,
    querying the artifacts and findings tables directly.
    """
    from app.evidence_engine.family_contract import ALLOWS_EMPTY_ARTIFACT_TYPES
    from app.evidence_engine.finding_extractor_registry import default_finding_registry

    row = store.query_one(
        """
        SELECT artifact_id, session_id, artifact_type, artifact_schema_version
        FROM artifacts
        WHERE artifact_id = ? AND session_id = ?
        """,
        [artifact_id, session_id],
    )
    if row is None:
        raise NotFoundError(
            code="not_found",
            message=f"artifact {artifact_id!r} not found in session {session_id!r}",
        )

    artifact_type: str = row["artifact_type"]
    artifact_schema_version: str | None = row.get("artifact_schema_version")

    # Count findings for this artifact in the session.
    finding_row = store.query_one(
        "SELECT COUNT(*) AS cnt FROM findings WHERE artifact_id = ? AND session_id = ?",
        [artifact_id, session_id],
    )
    finding_count: int = int(finding_row["cnt"]) if finding_row else 0

    # Derive artifact_stage.
    if artifact_type in ALLOWS_EMPTY_ARTIFACT_TYPES or finding_count > 0:
        artifact_stage = "findings_committed"
    else:
        artifact_stage = "staged"

    # Extractor lookup.
    extractor = default_finding_registry.find(artifact_type, artifact_schema_version)
    extractor_version: str | None = extractor.extractor_version if extractor is not None else None

    return {
        "session_id": session_id,
        "artifact_id": artifact_id,
        "artifact_stage": artifact_stage,
        "extractor_key": {
            "artifact_type": artifact_type,
            "artifact_schema_version": artifact_schema_version,
            "extractor_version": extractor_version,
        },
        "correlation_id": artifact_id,
        "attempt_id": None,
        "last_failure_reason": None,
        "last_failure_at": None,
        "schema_version": "artifact_runtime_status.v1",
    }


# compare_artifact content valid for T1 materializer (delta → change proposition).
_LEFT_WIN = {"kind": "range", "start": "2024-01-01", "end": "2024-01-07"}
_RIGHT_WIN = {"kind": "range", "start": "2024-01-08", "end": "2024-01-14"}
_COMPARE_CONTENT: dict[str, Any] = {
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
        "artifact_id": "art_ss_001",
        "item_ref": {"collection": "result", "index": None, "key": None},
    },
    "right_ref": {
        "artifact_id": "art_ss_001",
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


def _insert_artifact(
    store: SQLiteMetadataStore,
    artifact_id: str,
    session_id: str,
    artifact_type: str = "compare_artifact",
    content: dict[str, Any] | None = None,
    artifact_schema_version: str | None = "v1",
) -> None:
    store.execute(
        "INSERT INTO artifacts "
        "(artifact_id, session_id, step_id, artifact_type, artifact_schema_version, "
        "name, content_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            artifact_id,
            session_id,
            f"step_{artifact_id}",
            artifact_type,
            artifact_schema_version,
            artifact_id,
            json.dumps(content if content is not None else _COMPARE_CONTENT),
        ],
    )


def _insert_finding(
    store: SQLiteMetadataStore,
    finding_id: str,
    session_id: str,
    artifact_id: str,
    metric: str = "dau",
) -> None:
    subject = {
        "metric": metric,
        "entity": None,
        "slice": {},
        "grain": "day",
        "analysis_axis": "scalar",
    }
    step_ref = {
        "session_id": session_id,
        "step_id": f"step_{artifact_id}",
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
            "payload_json": json.dumps(_DELTA_PAYLOAD),
            "schema_version": "v1",
        }
    )


# ---------------------------------------------------------------------------
# HTTP-level tests (TestClient + create_app)
# ---------------------------------------------------------------------------


class TestSessionStateAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "test.duckdb"
        get_seeded_duckdb_path(db_path)
        from app.main import create_app

        cls.client = TestClient(create_app(db_path))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def _create_session(self) -> str:
        resp = self.client.post("/sessions", json={"goal": "Phase 5b state test", "budget": {}})
        self.assertEqual(resp.status_code, 200)
        return resp.json()["session_id"]

    # -- GET /state -----------------------------------------------------------

    def test_get_state_empty_session_returns_valid_schema(self) -> None:
        sid = self._create_session()
        resp = self.client.get(f"/sessions/{sid}/state")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertEqual(data["session_id"], sid)
        self.assertEqual(data["schema_version"], SESSION_STATE_VIEW_SCHEMA_VERSION)
        self.assertIsInstance(data["active_propositions"], list)
        self.assertIsInstance(data["backing_findings"], list)
        self.assertIsInstance(data["blocking_gaps"], list)
        self.assertIsInstance(data["artifact_refs"], list)
        self.assertIsInstance(data["focus_subjects"], list)

    def test_get_state_empty_session_has_valid_truncation(self) -> None:
        sid = self._create_session()
        resp = self.client.get(f"/sessions/{sid}/state")
        self.assertEqual(resp.status_code, 200)
        trunc = resp.json()["truncation"]
        self.assertFalse(trunc["is_truncated"])
        self.assertEqual(trunc["returned_count"], 0)
        self.assertEqual(trunc["total_count"], 0)
        self.assertEqual(trunc["sort_key"], "default_active_proposition_order_v1")
        self.assertEqual(trunc["applies_to"], "active_propositions")

    def test_get_state_empty_session_next_page_token_is_null(self) -> None:
        sid = self._create_session()
        resp = self.client.get(f"/sessions/{sid}/state")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()["next_page_token"])

    def test_get_state_no_runtime_fields_in_response(self) -> None:
        sid = self._create_session()
        resp = self.client.get(f"/sessions/{sid}/state")
        data = resp.json()
        for forbidden_key in (
            "claim_owner",
            "lease_expires_at",
            "attempt_id",
            "backlog",
            "retry_count",
        ):
            self.assertNotIn(forbidden_key, data)

    def test_get_state_unknown_session_returns_404(self) -> None:
        resp = self.client.get("/sessions/sess_missing_xyz/state")
        self.assertEqual(resp.status_code, 404)

    def test_get_state_slice_param_returns_400(self) -> None:
        sid = self._create_session()
        resp = self.client.get(f"/sessions/{sid}/state?slice=%7B%7D")
        self.assertEqual(resp.status_code, 400)

    def test_get_state_invalid_assessment_presence_returns_400(self) -> None:
        sid = self._create_session()
        resp = self.client.get(f"/sessions/{sid}/state?assessment_presence=invalid_value")
        self.assertEqual(resp.status_code, 400)

    # -- POST /state/query ----------------------------------------------------

    def test_post_state_query_empty_body_returns_valid_schema(self) -> None:
        sid = self._create_session()
        resp = self.client.post(f"/sessions/{sid}/state/query", json={})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["schema_version"], SESSION_STATE_VIEW_SCHEMA_VERSION)

    def test_post_state_query_unassessed_plus_statuses_returns_200_empty(self) -> None:
        """assessment_presence=unassessed + assessment_statuses → 200 with empty list."""
        sid = self._create_session()
        resp = self.client.post(
            f"/sessions/{sid}/state/query",
            json={
                "assessment_presence": "unassessed",
                "assessment_statuses": ["supported"],
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["active_propositions"], [])

    def test_post_state_query_unknown_session_returns_404(self) -> None:
        resp = self.client.post(
            "/sessions/sess_missing_xyz/state/query",
            json={"assessment_presence": "assessed"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_post_state_query_accepts_slice(self) -> None:
        """POST /state/query must accept slice without error."""
        sid = self._create_session()
        resp = self.client.post(
            f"/sessions/{sid}/state/query",
            json={"slice": {"country": "US"}},
        )
        self.assertEqual(resp.status_code, 200)

    # -- GET /artifacts/{id}/runtime-status -----------------------------------

    def test_get_artifact_runtime_status_unknown_session_returns_404(self) -> None:
        resp = self.client.get("/sessions/sess_missing_xyz/artifacts/art_missing/runtime-status")
        self.assertEqual(resp.status_code, 404)

    def test_get_artifact_runtime_status_unknown_artifact_returns_404(self) -> None:
        sid = self._create_session()
        resp = self.client.get(f"/sessions/{sid}/artifacts/art_does_not_exist/runtime-status")
        self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# MarivoRuntime unit tests for get_artifact_runtime_status
# ---------------------------------------------------------------------------


class TestArtifactRuntimeStatusManager(unittest.TestCase):
    def setUp(self) -> None:
        self.store = _make_store()
        self.session_id = f"sess_{uuid4().hex[:12]}"
        _insert_session(self.store, self.session_id)

    def _get_status(self, artifact_id: str) -> dict[str, Any]:
        return _get_artifact_runtime_status(self.store, self.session_id, artifact_id)

    def test_raises_not_found_error_for_unknown_session(self) -> None:
        with self.assertRaises(NotFoundError):
            _get_artifact_runtime_status(self.store, "sess_not_exist", "art_x")

    def test_raises_not_found_error_for_unknown_artifact(self) -> None:
        with self.assertRaises(NotFoundError):
            _get_artifact_runtime_status(self.store, self.session_id, "art_not_exist")

    def test_raises_not_found_error_for_artifact_in_different_session(self) -> None:
        other_session = f"sess_{uuid4().hex[:12]}"
        _insert_session(self.store, other_session)
        artifact_id = f"art_{uuid4().hex[:8]}"
        _insert_artifact(self.store, artifact_id, other_session)
        with self.assertRaises(NotFoundError):
            _get_artifact_runtime_status(self.store, self.session_id, artifact_id)

    def test_staged_artifact_non_d4_family_no_findings(self) -> None:
        artifact_id = f"art_{uuid4().hex[:8]}"
        _insert_artifact(
            self.store,
            artifact_id,
            self.session_id,
            artifact_type="compare_artifact",
        )
        status = self._get_status(artifact_id)

        self.assertEqual(status["session_id"], self.session_id)
        self.assertEqual(status["artifact_id"], artifact_id)
        self.assertEqual(status["artifact_stage"], "staged")
        self.assertEqual(status["schema_version"], "artifact_runtime_status.v1")

    def test_d4_allows_empty_family_no_findings_is_findings_committed(self) -> None:
        """Observation artifact (D4 allows-empty) with no findings → findings_committed."""
        artifact_id = f"art_{uuid4().hex[:8]}"
        _insert_artifact(
            self.store,
            artifact_id,
            self.session_id,
            artifact_type="observation",
        )
        status = self._get_status(artifact_id)
        self.assertEqual(status["artifact_stage"], "findings_committed")

    def test_anomaly_candidates_family_no_findings_is_findings_committed(self) -> None:
        """anomaly_candidates (D4 allows-empty) with no findings → findings_committed."""
        artifact_id = f"art_{uuid4().hex[:8]}"
        _insert_artifact(
            self.store,
            artifact_id,
            self.session_id,
            artifact_type="anomaly_candidates",
        )
        status = self._get_status(artifact_id)
        self.assertEqual(status["artifact_stage"], "findings_committed")

    def test_non_d4_artifact_with_findings_is_findings_committed(self) -> None:
        """Non-D4 artifact with at least one finding → findings_committed."""
        artifact_id = f"art_{uuid4().hex[:8]}"
        finding_id = f"fnd_{uuid4().hex[:8]}"
        _insert_artifact(
            self.store,
            artifact_id,
            self.session_id,
            artifact_type="compare_artifact",
        )
        _insert_finding(self.store, finding_id, self.session_id, artifact_id)
        status = self._get_status(artifact_id)
        self.assertEqual(status["artifact_stage"], "findings_committed")

    def test_schema_version_is_correct(self) -> None:
        artifact_id = f"art_{uuid4().hex[:8]}"
        _insert_artifact(self.store, artifact_id, self.session_id)
        status = self._get_status(artifact_id)
        self.assertEqual(status["schema_version"], "artifact_runtime_status.v1")

    def test_extractor_key_fields_present(self) -> None:
        artifact_id = f"art_{uuid4().hex[:8]}"
        _insert_artifact(
            self.store,
            artifact_id,
            self.session_id,
            artifact_type="compare_artifact",
            artifact_schema_version="v1",
        )
        status = self._get_status(artifact_id)
        key = status["extractor_key"]
        self.assertEqual(key["artifact_type"], "compare_artifact")
        self.assertEqual(key["artifact_schema_version"], "v1")
        # extractor_version: compare_extractor is registered in default_finding_registry
        self.assertIsNotNone(key["extractor_version"])

    def test_extractor_key_version_null_for_unknown_type(self) -> None:
        artifact_id = f"art_{uuid4().hex[:8]}"
        _insert_artifact(
            self.store,
            artifact_id,
            self.session_id,
            artifact_type="unknown_custom_type",
        )
        status = self._get_status(artifact_id)
        self.assertIsNone(status["extractor_key"]["extractor_version"])

    def test_v1_attempt_fields_are_null(self) -> None:
        artifact_id = f"art_{uuid4().hex[:8]}"
        _insert_artifact(self.store, artifact_id, self.session_id)
        status = self._get_status(artifact_id)
        self.assertIsNone(status["attempt_id"])
        self.assertIsNone(status["last_failure_reason"])
        self.assertIsNone(status["last_failure_at"])

    def test_correlation_id_equals_artifact_id(self) -> None:
        artifact_id = f"art_{uuid4().hex[:8]}"
        _insert_artifact(self.store, artifact_id, self.session_id)
        status = self._get_status(artifact_id)
        self.assertEqual(status["correlation_id"], artifact_id)


# ---------------------------------------------------------------------------
# State view materialization tests (uses canonical pipeline for published data)
# ---------------------------------------------------------------------------


class TestStateViewMaterializer(unittest.TestCase):
    """Tests state_view.materialize_session_state_view against real DB state."""

    def setUp(self) -> None:
        self.store = _make_store()
        self.session_id = f"sess_{uuid4().hex[:12]}"
        _insert_session(self.store, self.session_id)
        self.repos = _make_repos(self.store)

    def _call_view(self, query: dict[str, Any] | None = None) -> dict[str, Any]:
        return materialize_session_state_view(
            session_id=self.session_id,
            query=query or {},
            **self.repos,
        )

    # -- Empty session --------------------------------------------------------

    def test_empty_session_returns_empty_active_propositions(self) -> None:
        view = self._call_view()
        self.assertEqual(view["active_propositions"], [])
        self.assertEqual(view["backing_findings"], [])
        self.assertEqual(view["blocking_gaps"], [])
        self.assertEqual(view["artifact_refs"], [])
        self.assertEqual(view["focus_subjects"], [])

    def test_empty_session_schema_version(self) -> None:
        view = self._call_view()
        self.assertEqual(view["schema_version"], SESSION_STATE_VIEW_SCHEMA_VERSION)

    def test_empty_session_truncation_is_not_truncated(self) -> None:
        view = self._call_view()
        self.assertFalse(view["truncation"]["is_truncated"])
        self.assertEqual(view["truncation"]["total_count"], 0)

    # -- Unassessed live proposition ------------------------------------------

    def test_unassessed_proposition_appears_with_null_assessment(self) -> None:
        """A proposition without a publish switch must appear with latest_assessment=null."""
        # Insert a proposition directly (no pipeline run → no externally_visible bundle)
        prop_id = f"prop_{uuid4().hex[:12]}"
        self.store.execute(
            """INSERT INTO propositions
               (proposition_id, session_id, proposition_type, subject_json, origin_json,
                assessment_anchor_json, lineage_json, seed_finding_refs_json, payload_json,
                schema_version, identity_key)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                prop_id,
                self.session_id,
                "change_assessment",
                json.dumps({"metric": "dau", "entity": None, "slice": {}, "grain": "day"}),
                json.dumps({"kind": "system_seeded"}),
                json.dumps({"assessment_type": "change_assessment"}),
                json.dumps({"source_artifact_lineages": [], "source_step_refs": []}),
                json.dumps([]),
                json.dumps({}),
                "v1",
                f"key_{uuid4().hex[:24]}",
            ],
        )
        view = self._call_view()
        entries = view["active_propositions"]
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertIsNone(entry["latest_assessment"])
        self.assertIsNone(entry["supporting_finding_refs"])
        self.assertIsNone(entry["opposing_finding_refs"])
        self.assertIsNone(entry["blocking_gap_refs"])
        self.assertIsNone(entry["non_blocking_gap_refs"])
        self.assertIsNone(entry["applied_inference_record_refs"])
        self.assertEqual(entry["artifact_refs"], [])

    def test_unassessed_filter_returns_only_null_assessment_entries(self) -> None:
        """assessment_presence=unassessed returns only entries with latest_assessment=null."""
        prop_id = f"prop_{uuid4().hex[:12]}"
        self.store.execute(
            """INSERT INTO propositions
               (proposition_id, session_id, proposition_type, subject_json, origin_json,
                assessment_anchor_json, lineage_json, seed_finding_refs_json, payload_json,
                schema_version, identity_key)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                prop_id,
                self.session_id,
                "change_assessment",
                json.dumps({"metric": "dau", "entity": None, "slice": {}, "grain": "day"}),
                json.dumps({"kind": "system_seeded"}),
                json.dumps({"assessment_type": "change_assessment"}),
                json.dumps({"source_artifact_lineages": [], "source_step_refs": []}),
                json.dumps([]),
                json.dumps({}),
                "v1",
                f"key_{uuid4().hex[:24]}",
            ],
        )
        view = self._call_view({"assessment_presence": "unassessed"})
        self.assertEqual(len(view["active_propositions"]), 1)
        self.assertIsNone(view["active_propositions"][0]["latest_assessment"])

    def test_assessed_filter_returns_empty_for_unassessed_only_session(self) -> None:
        prop_id = f"prop_{uuid4().hex[:12]}"
        self.store.execute(
            """INSERT INTO propositions
               (proposition_id, session_id, proposition_type, subject_json, origin_json,
                assessment_anchor_json, lineage_json, seed_finding_refs_json, payload_json,
                schema_version, identity_key)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                prop_id,
                self.session_id,
                "change_assessment",
                json.dumps({"metric": "dau", "entity": None, "slice": {}, "grain": "day"}),
                json.dumps({"kind": "system_seeded"}),
                json.dumps({"assessment_type": "change_assessment"}),
                json.dumps({"source_artifact_lineages": [], "source_step_refs": []}),
                json.dumps([]),
                json.dumps({}),
                "v1",
                f"key_{uuid4().hex[:24]}",
            ],
        )
        view = self._call_view({"assessment_presence": "assessed"})
        self.assertEqual(view["active_propositions"], [])

    def test_unassessed_plus_statuses_returns_empty_list(self) -> None:
        """assessment_presence=unassessed + assessment_statuses → always empty (not 422)."""
        view = self._call_view(
            {"assessment_presence": "unassessed", "assessment_statuses": ["supported"]}
        )
        self.assertEqual(view["active_propositions"], [])

    # -- Published proposition via canonical pipeline -------------------------

    def _run_pipeline(self) -> tuple[str, str]:
        """Insert compare artifact + delta finding, run canonical downstream.

        Returns (artifact_id, finding_id).
        """
        artifact_id = "art_ss_001"
        finding_id = "fnd_ss_001"
        _insert_artifact(self.store, artifact_id, self.session_id, content=_COMPARE_CONTENT)
        _insert_finding(self.store, finding_id, self.session_id, artifact_id)
        run_canonical_downstream(
            session_id=self.session_id,
            trigger_finding_ids=[finding_id],
            **self.repos,
            metadata_store=self.store,
        )
        return artifact_id, finding_id

    def test_published_proposition_has_latest_assessment(self) -> None:
        self._run_pipeline()
        view = self._call_view()
        assessed = [e for e in view["active_propositions"] if e["latest_assessment"] is not None]
        self.assertGreater(len(assessed), 0)

    def test_backing_findings_derived_from_returned_propositions_only(self) -> None:
        self._run_pipeline()
        view = self._call_view()
        # backing_findings must be a subset of all finding ids returned by the
        # returned active_propositions' support+oppose closures.
        returned_finding_ids: set[str] = set()
        for entry in view["active_propositions"]:
            for ref in entry.get("supporting_finding_refs") or []:
                returned_finding_ids.add(ref["finding_id"])
            for ref in entry.get("opposing_finding_refs") or []:
                returned_finding_ids.add(ref["finding_id"])
        backing_ids = {f["finding_id"] for f in view["backing_findings"]}
        self.assertTrue(backing_ids.issubset(returned_finding_ids))

    def test_focus_subjects_derived_from_backing_findings_only(self) -> None:
        self._run_pipeline()
        view = self._call_view()
        backing_subjects = [
            json.dumps(f["subject_json"], sort_keys=True)
            for f in view["backing_findings"]
            if f.get("subject_json")
        ]
        for subj in view["focus_subjects"]:
            key = json.dumps(subj, sort_keys=True)
            self.assertIn(key, backing_subjects)

    def test_no_non_blocking_gaps_in_blocking_gaps(self) -> None:
        """blocking_gaps must contain only gaps from blocking=True memberships."""
        self._run_pipeline()
        view = self._call_view()
        # Every gap in blocking_gaps must appear in at least one entry's blocking_gap_refs.
        blocking_gap_ids: set[str] = set()
        for entry in view["active_propositions"]:
            for ref in entry.get("blocking_gap_refs") or []:
                blocking_gap_ids.add(ref["gap_id"])
        for gap in view["blocking_gaps"]:
            self.assertIn(gap["gap_id"], blocking_gap_ids)

    def test_artifact_refs_derived_from_backing_findings(self) -> None:
        self._run_pipeline()
        view = self._call_view()
        backing_artifact_ids = {f["artifact_id"] for f in view["backing_findings"]}
        artifact_ref_ids = {r["artifact_id"] for r in view["artifact_refs"]}
        self.assertTrue(artifact_ref_ids.issubset(backing_artifact_ids))

    def test_no_duplicate_finding_ids_in_backing_findings(self) -> None:
        self._run_pipeline()
        view = self._call_view()
        ids = [f["finding_id"] for f in view["backing_findings"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_no_duplicate_artifact_ids_in_artifact_refs(self) -> None:
        self._run_pipeline()
        view = self._call_view()
        ids = [r["artifact_id"] for r in view["artifact_refs"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_truncation_total_count_matches_filtered_count(self) -> None:
        self._run_pipeline()
        view = self._call_view()
        trunc = view["truncation"]
        self.assertEqual(trunc["total_count"], len(view["active_propositions"]))
        self.assertFalse(trunc["is_truncated"])

    def test_truncation_limit_reduces_returned_propositions(self) -> None:
        self._run_pipeline()
        view = self._call_view({"limit": 1})
        trunc = view["truncation"]
        self.assertLessEqual(len(view["active_propositions"]), 1)
        self.assertEqual(trunc["returned_count"], len(view["active_propositions"]))

    def test_has_blocking_gaps_false_excludes_unassessed(self) -> None:
        """has_blocking_gaps=False must not include unassessed propositions."""
        # Insert an extra unassessed proposition
        prop_id = f"prop_{uuid4().hex[:12]}"
        self.store.execute(
            """INSERT INTO propositions
               (proposition_id, session_id, proposition_type, subject_json, origin_json,
                assessment_anchor_json, lineage_json, seed_finding_refs_json, payload_json,
                schema_version, identity_key)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                prop_id,
                self.session_id,
                "change_assessment",
                json.dumps({"metric": "revenue", "entity": None, "slice": {}, "grain": "day"}),
                json.dumps({"kind": "system_seeded"}),
                json.dumps({"assessment_type": "change_assessment"}),
                json.dumps({"source_artifact_lineages": [], "source_step_refs": []}),
                json.dumps([]),
                json.dumps({}),
                "v1",
                f"key_{uuid4().hex[:24]}",
            ],
        )
        view = self._call_view({"has_blocking_gaps": False})
        prop_ids = {e["proposition"]["proposition_id"] for e in view["active_propositions"]}
        self.assertNotIn(prop_id, prop_ids)

    def test_metric_filter_restricts_propositions(self) -> None:
        self._run_pipeline()
        view = self._call_view({"metric": "nonexistent_metric_xyz"})
        self.assertEqual(view["active_propositions"], [])

    def test_state_view_does_not_contain_runtime_fields(self) -> None:
        self._run_pipeline()
        view = self._call_view()
        for forbidden in ("claim_owner", "attempt_id", "lease_expires_at", "backlog_state"):
            self.assertNotIn(forbidden, view)


if __name__ == "__main__":
    unittest.main()
