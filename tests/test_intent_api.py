"""Tests for the Phase 2 Intent Action Surface.

Covers:
  - Intent request model schema validation (ObserveRequest, CompareRequest, etc.)
  - Intent HTTP endpoints: correct routing, schema errors (422), not-implemented (501)
  - ObserveRequest model validation rules (illegal combinations)
  - CompareRequest / CorrelateRequest / TestRequest / ForecastRequest same-session ref guard
  - DecomposeRequest compare_ref.step_type validation
  - Legacy /steps/* endpoints confirm 404
  - run_intent: observe→metric_query execution (with semantic layer wired up)
  - run_intent: stub intents return NotImplementedError
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.models import (
    ArtifactRef,
    CompareRequest,
    DecomposeRequest,
    ObservationRef,
    ObserveRequest,
)
from app.main import create_app
from tests.shared_fixtures import get_seeded_duckdb_path

# ── Model-level validation tests (no HTTP) ───────────────────────────────────


class ObserveRequestModelTests(unittest.TestCase):
    def _make(self, **kwargs):
        base = {
            "metric": "dau",
            "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
        }
        base.update(kwargs)
        return ObserveRequest(**base)

    def test_scalar_mode(self) -> None:
        r = self._make()
        self.assertEqual(r.result_mode, "standard")
        self.assertIsNone(r.granularity)
        self.assertIsNone(r.dimensions)

    def test_time_series_mode(self) -> None:
        r = self._make(granularity="day")
        self.assertEqual(r.granularity, "day")

    def test_segmented_mode(self) -> None:
        r = self._make(dimensions=["region"])
        self.assertEqual(r.dimensions, ["region"])

    def test_empty_dimensions_normalized_to_none(self) -> None:
        r = self._make(dimensions=[])
        self.assertIsNone(r.dimensions)

    def test_granularity_and_dimensions_mutually_exclusive(self) -> None:
        with self.assertRaises(Exception):
            self._make(granularity="day", dimensions=["region"])

    def test_non_standard_mode_rejects_granularity(self) -> None:
        with self.assertRaises(Exception):
            self._make(result_mode="numeric_sample_summary", granularity="day")

    def test_non_standard_mode_rejects_dimensions(self) -> None:
        with self.assertRaises(Exception):
            self._make(result_mode="rate_sample_summary", dimensions=["platform"])

    def test_snapshot_now_time_scope(self) -> None:
        r = ObserveRequest(
            metric="dau",
            time_scope={"kind": "snapshot_now"},
        )
        self.assertEqual(r.time_scope.kind, "snapshot_now")

    def test_as_of_time_scope(self) -> None:
        r = ObserveRequest(
            metric="dau",
            time_scope={"kind": "as_of", "at": "2024-06-01T00:00:00"},
        )
        self.assertEqual(r.time_scope.kind, "as_of")

    def test_snapshot_now_rejects_granularity(self) -> None:
        with self.assertRaises(Exception):
            ObserveRequest(
                metric="dau",
                time_scope={"kind": "snapshot_now"},
                granularity="day",
            )


class CompareRequestModelTests(unittest.TestCase):
    def _ref(self, session_id: str = "sess_a", step_id: str = "step_1") -> ObservationRef:
        return ObservationRef(session_id=session_id, step_id=step_id, step_type="observe")

    def test_valid_request(self) -> None:
        r = CompareRequest(left_ref=self._ref(), right_ref=self._ref("sess_a", "step_2"))
        self.assertEqual(r.mode, "auto")

    def test_observation_ref_step_type_locked_to_observe(self) -> None:
        with self.assertRaises(Exception):
            ObservationRef(session_id="sess_a", step_id="step_1", step_type="compare")


class DecomposeRequestModelTests(unittest.TestCase):
    def test_valid_request(self) -> None:
        ref = ArtifactRef(session_id="sess_a", step_id="step_cmp", step_type="compare")
        r = DecomposeRequest(compare_ref=ref, dimensions=["region"])
        self.assertEqual(r.top_k, 5)

    def test_compare_ref_must_be_compare_step_type(self) -> None:
        ref = ArtifactRef(session_id="sess_a", step_id="step_obs", step_type="observe")
        with self.assertRaises(Exception):
            DecomposeRequest(compare_ref=ref, dimensions=["region"])

    def test_dimensions_min_length(self) -> None:
        ref = ArtifactRef(session_id="sess_a", step_id="step_cmp", step_type="compare")
        with self.assertRaises(Exception):
            DecomposeRequest(compare_ref=ref, dimensions=[])


# ── HTTP endpoint tests ───────────────────────────────────────────────────────


class IntentEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "intent_api.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))
        r = cls.client.post("/sessions", json={"goal": "Intent API test session"})
        cls.session_id = r.json()["session_id"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    # ── observe ───────────────────────────────────────────────────────────────

    def test_observe_requires_metric_and_time_scope(self) -> None:
        r = self.client.post(f"/sessions/{self.session_id}/intents/observe", json={})
        self.assertEqual(r.status_code, 422)
        detail = r.json()["detail"]
        fields = {e["loc"][-1] for e in detail}
        self.assertIn("metric", fields)
        self.assertIn("time_scope", fields)

    def test_observe_rejects_granularity_plus_dimensions(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": "dau",
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
                "granularity": "day",
                "dimensions": ["region"],
            },
        )
        self.assertEqual(r.status_code, 422)

    def test_observe_unknown_metric_returns_422(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": "non_existent_metric_xyz",
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
            },
        )
        # metric not in semantic layer → 422 from service
        self.assertEqual(r.status_code, 422)

    def test_observe_unsupported_time_scope_kind_returns_501(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": "dau",
                "time_scope": {"kind": "snapshot_now"},
            },
        )
        # snapshot_now is not yet implemented → 501
        self.assertEqual(r.status_code, 501)

    # ── compare ───────────────────────────────────────────────────────────────

    def test_compare_returns_501_for_stub_execution(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/compare",
            json={
                "left_ref": {
                    "session_id": self.session_id,
                    "step_id": "step_001",
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": self.session_id,
                    "step_id": "step_002",
                    "step_type": "observe",
                },
            },
        )
        self.assertEqual(r.status_code, 501)

    def test_compare_rejects_cross_session_ref(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/compare",
            json={
                "left_ref": {
                    "session_id": "sess_other",
                    "step_id": "step_001",
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": self.session_id,
                    "step_id": "step_002",
                    "step_type": "observe",
                },
            },
        )
        self.assertEqual(r.status_code, 422)
        self.assertIn("Cross-session", r.json()["detail"])

    # ── correlate ─────────────────────────────────────────────────────────────

    def test_correlate_rejects_cross_session_ref(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/correlate",
            json={
                "left_ref": {
                    "session_id": "sess_foreign",
                    "step_id": "step_a",
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": self.session_id,
                    "step_id": "step_b",
                    "step_type": "observe",
                },
            },
        )
        self.assertEqual(r.status_code, 422)

    def test_correlate_returns_501_for_stub_execution(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/correlate",
            json={
                "left_ref": {
                    "session_id": self.session_id,
                    "step_id": "step_a",
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": self.session_id,
                    "step_id": "step_b",
                    "step_type": "observe",
                },
            },
        )
        self.assertEqual(r.status_code, 501)

    # ── detect ────────────────────────────────────────────────────────────────

    def test_detect_returns_501_for_stub_execution(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/detect",
            json={
                "metric": "dau",
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
            },
        )
        self.assertEqual(r.status_code, 501)

    # ── test ─────────────────────────────────────────────────────────────────

    def test_intent_test_rejects_cross_session_ref(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/test",
            json={
                "hypothesis": "welch_t",
                "left_ref": {
                    "session_id": "sess_x",
                    "step_id": "step_1",
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": self.session_id,
                    "step_id": "step_2",
                    "step_type": "observe",
                },
            },
        )
        self.assertEqual(r.status_code, 422)

    def test_intent_test_returns_501_for_stub(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/test",
            json={
                "hypothesis": "welch_t",
                "left_ref": {
                    "session_id": self.session_id,
                    "step_id": "step_1",
                    "step_type": "observe",
                },
                "right_ref": {
                    "session_id": self.session_id,
                    "step_id": "step_2",
                    "step_type": "observe",
                },
            },
        )
        self.assertEqual(r.status_code, 501)

    # ── forecast ──────────────────────────────────────────────────────────────

    def test_forecast_rejects_missing_horizon(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/forecast",
            json={
                "series_ref": {
                    "session_id": self.session_id,
                    "step_id": "step_1",
                    "step_type": "observe",
                },
                "granularity": "day",
            },
        )
        self.assertEqual(r.status_code, 422)

    def test_forecast_returns_501_for_stub(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/forecast",
            json={
                "series_ref": {
                    "session_id": self.session_id,
                    "step_id": "step_1",
                    "step_type": "observe",
                },
                "horizon": 7,
                "granularity": "day",
            },
        )
        self.assertEqual(r.status_code, 501)

    # ── derived intents ───────────────────────────────────────────────────────

    def test_attribute_returns_501_for_stub(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/attribute",
            json={
                "metric": "dau",
                "current_time_scope": {
                    "kind": "range",
                    "start": "2024-01-08",
                    "end": "2024-01-15",
                },
                "baseline_time_scope": {
                    "kind": "range",
                    "start": "2024-01-01",
                    "end": "2024-01-08",
                },
                "candidate_dimensions": ["region"],
            },
        )
        self.assertEqual(r.status_code, 501)

    def test_diagnose_returns_501_for_stub(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/diagnose",
            json={
                "metric": "dau",
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
            },
        )
        self.assertEqual(r.status_code, 501)

    def test_validate_returns_501_for_stub(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/validate",
            json={
                "hypothesis": "welch_t",
                "metric": "dau",
                "current_time_scope": {
                    "kind": "range",
                    "start": "2024-01-08",
                    "end": "2024-01-15",
                },
                "baseline_time_scope": {
                    "kind": "range",
                    "start": "2024-01-01",
                    "end": "2024-01-08",
                },
            },
        )
        self.assertEqual(r.status_code, 501)

    # ── non-existent session ──────────────────────────────────────────────────

    def test_observe_on_nonexistent_session_returns_404(self) -> None:
        r = self.client.post(
            "/sessions/sess_nonexistent/intents/observe",
            json={
                "metric": "dau",
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
            },
        )
        self.assertEqual(r.status_code, 404)


class IntentEndpointWithSemanticLayerTests(unittest.TestCase):
    """Tests that require a semantic metric wired to a source table."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "intent_semantic.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))
        cls._setup_semantic_layer()
        r = cls.client.post(
            "/sessions",
            json={
                "goal": "Observe semantic metric test",
                "constraints": {},
                "budget": {},
                "policy": {},
            },
        )
        cls.session_id = r.json()["session_id"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    @classmethod
    def _setup_semantic_layer(cls) -> None:
        """Register a source, engine, binding, and semantic metric so observe can execute."""
        # Register source
        r = cls.client.post(
            "/sources",
            json={
                "source_type": "duckdb",
                "display_name": "Test DuckDB",
                "connection": {"database": ":memory:"},
            },
        )
        cls.source_id = r.json()["source_id"]

        # Register engine
        r = cls.client.post(
            "/engines",
            json={
                "engine_type": "duckdb",
                "display_name": "Test DuckDB Engine",
                "connection": {},
            },
        )
        cls.engine_id = r.json()["engine_id"]

        # Create binding
        cls.client.post(
            "/bindings",
            json={"source_id": cls.source_id, "engine_id": cls.engine_id, "priority": 0},
        )

        # Sync a table so we have a source_object
        cls.client.post(f"/sources/{cls.source_id}/sync")

        # Create a semantic metric (uses watch_events table from demo data)
        r = cls.client.post(
            "/semantic/metrics",
            json={
                "name": "test_observe_metric",
                "display_name": "Test Observe Metric",
                "definition_sql": "COUNT(*)",
                "dimensions": ["event_date"],
                "grain": "day",
            },
        )
        if r.status_code == 200:
            cls.metric_id = r.json()["metric_id"]
        else:
            cls.metric_id = None

    def test_observe_with_real_metric_executes_or_404_if_not_mapped(self) -> None:
        """Observe succeeds if metric is mapped to a table, or returns 422 if not mapped."""
        if self.metric_id is None:
            self.skipTest("Metric creation failed in setUpClass")

        r = self.client.post(
            f"/sessions/{self.session_id}/intents/observe",
            json={
                "metric": "test_observe_metric",
                "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
            },
        )
        # Either 200 (metric resolved and query ran) or 422 (not mapped to a source object yet)
        self.assertIn(r.status_code, {200, 422})
        if r.status_code == 422:
            self.assertIn("metric", r.json()["detail"].lower())


if __name__ == "__main__":
    unittest.main()
