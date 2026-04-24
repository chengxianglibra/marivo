"""Tests for the `detect` atomic intent runner (Phase 3b-4).

Covers:
  - run_detect_intent: empty result (uniform data → no candidates)
  - run_detect_intent: spike data → candidate detected
  - run_detect_intent: sensitivity threshold differences
  - run_detect_intent: candidate ranking (score desc)
  - run_detect_intent: insufficient points → detectability needs_attention
  - run_detect_intent: artifact schema required fields present
  - run_detect_intent: limit truncation
  - run_detect_intent: split_by echoed in response
  - run_detect_intent: profile echoed in response
  - run_detect_intent: invalid sensitivity → ValueError
  - run_detect_intent: invalid mode → ValueError
  - run_detect_intent: invalid grain → ValueError
  - run_detect_intent: invalid profile → ValueError
  - HTTP endpoint: unknown metric → 422
  - HTTP endpoint: invalid time scope (start >= end) → 422
  - HTTP endpoint: missing required fields → 422
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
from app.storage.sqlite_metadata import SQLiteMetadataStore
from tests.semantic_test_helpers import (
    build_semantic_layer_service,
    ensure_active_duckdb_mapping,
    ensure_published_typed_metric,
    ensure_published_typed_metric_binding,
)
from tests.shared_fixtures import get_named_seeded_duckdb_path


def _metric_ref(name: str) -> str:
    return f"metric.{name}"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _seed_detect_tables(db_path: Path) -> None:
    """Copy the cached detect_intent template with spike/uniform data."""
    get_named_seeded_duckdb_path(db_path, "detect_intent")


def _seed_metadata(
    meta: SQLiteMetadataStore,
    *,
    src_suffix: str = "01",
    metric_name: str = "detect_event_count",
    table_fqn: str = "analytics.detect_events",
    native_name: str = "detect_events",
    binding_role: str = "primary",
    metric_input_target_keys: list[str] | None = None,
    measure_type: str | None = None,
) -> str:
    """Insert minimal metadata records so detect can resolve metric → table."""
    now = datetime.now(UTC).isoformat()
    existing_object = meta.query_one(
        """
        SELECT object_id, source_id
        FROM source_objects
        WHERE object_type = 'table' AND fqn = ?
        ORDER BY updated_at DESC, object_id
        LIMIT 1
        """,
        [table_fqn],
    )
    if existing_object is None:
        src_id = f"src_detecttest{src_suffix}"
        obj_id = f"obj_detecttest{src_suffix}"
        meta.execute(
            "INSERT OR IGNORE INTO sources "
            "(source_id, source_type, display_name, authority_json, sync_mode, "
            "intrinsic_capabilities_json, policy_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                src_id,
                "duckdb",
                "Detect Test Source",
                json.dumps(
                    {
                        "catalog_system": "duckdb",
                        "connection": {},
                        "synthetic_catalog": "main",
                    }
                ),
                "selected",
                json.dumps({"supports_partitions": False}),
                json.dumps({"allow_live_browse": True, "allow_sync": True}),
                now,
                now,
            ],
        )
        meta.execute(
            "INSERT OR IGNORE INTO source_objects "
            "(object_id, source_id, object_type, native_name, fqn, authority_locator_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                obj_id,
                src_id,
                "table",
                native_name,
                table_fqn,
                json.dumps({"catalog": "main", "schema": "analytics", "table": native_name}),
                now,
                now,
            ],
        )
    else:
        src_id = str(existing_object["source_id"])
        obj_id = str(existing_object["object_id"])
    ensure_published_typed_metric(
        meta,
        metric_name=metric_name,
        display_name=metric_name,
        grain="day",
        dimensions=["event_date"],
        definition_sql="COUNT(*)",
        measure_type=measure_type,
    )
    ensure_published_typed_metric_binding(
        meta,
        metric_name=metric_name,
        carrier_locator=table_fqn,
        source_object_ref=obj_id,
        binding_role=binding_role,
        metric_input_target_keys=metric_input_target_keys,
    )
    ensure_active_duckdb_mapping(meta, source_id=src_id, now=now)
    return metric_name


# ── Direct-service tests ──────────────────────────────────────────────────────


class DetectRunnerServiceTests(unittest.TestCase):
    """Tests that call run_detect_intent through SemanticLayerService directly."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "detect_svc.duckdb"
        meta_path = Path(cls.temp_dir.name) / "detect_svc.meta.sqlite"

        cls.analytics = DuckDBAnalyticsEngine(str(db_path))
        cls.metadata = SQLiteMetadataStore(str(meta_path))
        cls.metadata.initialize()
        cls.analytics.initialize()

        _seed_detect_tables(db_path)

        # Spike metric: detect_event_count → analytics.detect_events
        cls.spike_metric = _seed_metadata(
            cls.metadata,
            src_suffix="01",
            metric_name="detect_event_count",
            table_fqn="analytics.detect_events",
            native_name="detect_events",
        )
        # Uniform metric: uniform_event_count → analytics.uniform_events
        cls.uniform_metric = _seed_metadata(
            cls.metadata,
            src_suffix="02",
            metric_name="uniform_event_count",
            table_fqn="analytics.uniform_events",
            native_name="uniform_events",
        )

        cls.service = build_semantic_layer_service(cls.metadata, cls.analytics)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def _make_session(self) -> str:
        r = self.service.create_session("detect test session", {}, {}, {})
        return r["session_id"]

    def _detect(
        self,
        session_id: str,
        metric: str,
        start: str = "2026-01-01",
        end: str = "2026-01-15",
        sensitivity: str = "balanced",
        **extra: object,
    ) -> dict:
        params: dict = {
            "metric": _metric_ref(metric),
            "time_scope": {
                "mode": "single_window",
                "grain": "day",
                "current": {"start": start, "end": end},
            },
            "sensitivity": sensitivity,
        }
        params.update(extra)
        return self.service.run_intent(session_id, "detect", params)

    # ── Schema fields ─────────────────────────────────────────────────────────

    def test_detect_artifact_schema_fields(self) -> None:
        """Artifact must contain all mandatory schema fields."""
        session_id = self._make_session()
        result = self._detect(session_id, self.spike_metric)

        self.assertEqual(result["artifact_type"], "anomaly_candidates")
        self.assertEqual(result["artifact_schema_version"], "v1")
        self.assertIn("scan_summary", result)
        self.assertIn("total_candidate_count", result["scan_summary"])
        self.assertIn("detectability", result)
        self.assertIn("status", result["detectability"])
        self.assertIn("truncation", result)
        self.assertIn("returned_candidate_count", result["truncation"])
        self.assertIn("analytical_metadata", result)
        self.assertEqual(result["analytical_metadata"]["baseline_method"], "zscore")
        self.assertIn("provenance", result)
        self.assertIn("detector_version", result["provenance"])
        self.assertIn("artifact_id", result)
        self.assertIsNotNone(result["artifact_id"])
        # v1 required fields
        self.assertIn("split_by", result)
        self.assertIn("profile", result)

    # ── Empty semantics ────────────────────────────────────────────────────────

    def test_detect_empty_commits_success_artifact(self) -> None:
        """Uniform data: no anomaly candidates, but artifact is still committed successfully."""
        session_id = self._make_session()
        result = self._detect(session_id, self.uniform_metric)

        self.assertEqual(result["artifact_type"], "anomaly_candidates")
        # std = 0 → z = 0 for all → no candidates above threshold
        self.assertEqual(result["scan_summary"]["total_candidate_count"], 0)
        self.assertEqual(result["truncation"]["returned_candidate_count"], 0)
        self.assertFalse(result["truncation"]["truncated"])
        self.assertEqual(result["candidates"], [])

    # ── Spike detection ────────────────────────────────────────────────────────

    def test_detect_with_spike_returns_candidate(self) -> None:
        """Spike data: day 7 has 5× the normal count; should produce ≥ 1 candidate."""
        session_id = self._make_session()
        result = self._detect(session_id, self.spike_metric)

        self.assertGreaterEqual(result["scan_summary"]["total_candidate_count"], 1)
        candidates = result["candidates"]
        self.assertTrue(len(candidates) >= 1)

        top = candidates[0]
        self.assertIn("candidate_ref", top)
        self.assertIn("observed_value", top)
        self.assertIn("expected_value", top)
        self.assertIn("deviation_abs", top)
        self.assertIn("candidate_score", top)
        self.assertIn("flag_level", top)
        self.assertIn("direction", top)
        # Spike is upward
        self.assertEqual(top["direction"], "up")
        self.assertIn(top["flag_level"], ("low", "medium", "high"))

    # ── Sensitivity ────────────────────────────────────────────────────────────

    def test_detect_sensitivity_aggressive_detects_spike(self) -> None:
        """sensitivity='aggressive' (threshold 1.5) detects the spike."""
        session_id = self._make_session()
        result = self._detect(session_id, self.spike_metric, sensitivity="aggressive")
        self.assertGreater(result["scan_summary"]["total_candidate_count"], 0)

    def test_detect_sensitivity_conservative_detects_spike(self) -> None:
        """sensitivity='conservative' (threshold 2.5) still detects the 5× spike (z≈3.6)."""
        session_id = self._make_session()
        result = self._detect(session_id, self.spike_metric, sensitivity="conservative")
        self.assertGreater(result["scan_summary"]["total_candidate_count"], 0)

    def test_detect_sensitivity_balanced_detects_spike(self) -> None:
        """sensitivity='balanced' (threshold 2.0) detects the 5× spike."""
        session_id = self._make_session()
        result = self._detect(session_id, self.spike_metric, sensitivity="balanced")
        self.assertGreater(result["scan_summary"]["total_candidate_count"], 0)

    def test_detect_sensitivity_propagated_to_artifact(self) -> None:
        """Requested sensitivity value is stored in the artifact."""
        session_id = self._make_session()
        result = self._detect(session_id, self.spike_metric, sensitivity="aggressive")
        self.assertEqual(result["sensitivity"], "aggressive")

    def test_detect_invalid_sensitivity_raises(self) -> None:
        """Unknown sensitivity value → ValueError."""
        session_id = self._make_session()
        with self.assertRaises(ValueError):
            self._detect(session_id, self.spike_metric, sensitivity="extreme")

    # ── Profile ───────────────────────────────────────────────────────────────

    def test_detect_profile_echoed_in_response(self) -> None:
        """Explicitly requested profile is stored in the artifact."""
        session_id = self._make_session()
        result = self._detect(session_id, self.spike_metric, profile="spike_dip")
        self.assertEqual(result["profile"], "spike_dip")

    def test_detect_profile_defaults_to_auto(self) -> None:
        """Omitted profile normalises to 'auto'."""
        session_id = self._make_session()
        result = self._detect(session_id, self.spike_metric)
        self.assertEqual(result["profile"], "auto")

    def test_detect_invalid_profile_raises(self) -> None:
        """Unknown profile → ValueError."""
        session_id = self._make_session()
        with self.assertRaises(ValueError):
            self._detect(session_id, self.spike_metric, profile="zscore_raw")

    # ── split_by ──────────────────────────────────────────────────────────────

    def test_detect_split_by_echoed_in_response(self) -> None:
        """split_by is echoed in the artifact even though multi-series scan is not yet implemented."""
        session_id = self._make_session()
        result = self._detect(session_id, self.spike_metric, split_by="region")
        self.assertEqual(result["split_by"], "region")

    def test_detect_split_by_null_when_omitted(self) -> None:
        """Omitted split_by is null in the artifact."""
        session_id = self._make_session()
        result = self._detect(session_id, self.spike_metric)
        self.assertIsNone(result["split_by"])

    # ── limit truncation ──────────────────────────────────────────────────────

    def test_detect_limit_truncates_candidates(self) -> None:
        """limit=1 on spike data caps returned candidates and sets truncated=True."""
        session_id = self._make_session()
        result = self._detect(session_id, self.spike_metric, limit=1)
        # Spike data produces ≥1 candidate; with limit=1 we get exactly 1 back
        # if total > 1, truncated=True; if only 1, truncated=False — both valid
        self.assertLessEqual(result["truncation"]["returned_candidate_count"], 1)
        total = result["scan_summary"]["total_candidate_count"]
        if total > 1:
            self.assertTrue(result["truncation"]["truncated"])
            self.assertEqual(len(result["candidates"]), 1)
        else:
            self.assertFalse(result["truncation"]["truncated"])

    def test_detect_truncation_counts_consistent(self) -> None:
        """truncation.total_candidate_count matches scan_summary.total_candidate_count."""
        session_id = self._make_session()
        result = self._detect(session_id, self.spike_metric)
        self.assertEqual(
            result["truncation"]["total_candidate_count"],
            result["scan_summary"]["total_candidate_count"],
        )

    # ── time_scope validation ─────────────────────────────────────────────────

    def test_detect_invalid_mode_raises(self) -> None:
        """mode != 'single_window' → ValueError."""
        session_id = self._make_session()
        with self.assertRaises(ValueError):
            self.service.run_intent(
                session_id,
                "detect",
                {
                    "metric": self.spike_metric,
                    "time_scope": {
                        "mode": "compare",
                        "grain": "day",
                        "current": {"start": "2026-01-01", "end": "2026-01-15"},
                    },
                },
            )

    def test_detect_invalid_grain_raises(self) -> None:
        """Unsupported grain → ValueError."""
        session_id = self._make_session()
        with self.assertRaises(ValueError):
            self.service.run_intent(
                session_id,
                "detect",
                {
                    "metric": self.spike_metric,
                    "time_scope": {
                        "mode": "single_window",
                        "grain": "week",
                        "current": {"start": "2026-01-01", "end": "2026-01-15"},
                    },
                },
            )

    def test_detect_invalid_time_scope_start_gte_end_raises(self) -> None:
        """start >= end → ValueError."""
        session_id = self._make_session()
        with self.assertRaises(ValueError):
            self.service.run_intent(
                session_id,
                "detect",
                {
                    "metric": self.spike_metric,
                    "time_scope": {
                        "mode": "single_window",
                        "grain": "day",
                        "current": {"start": "2026-01-15", "end": "2026-01-01"},
                    },
                },
            )

    # ── Candidate ranking ──────────────────────────────────────────────────────

    def test_detect_candidate_ranking_score_desc(self) -> None:
        """Candidates are sorted by candidate_score descending."""
        session_id = self._make_session()
        result = self._detect(session_id, self.spike_metric)
        candidates = result["candidates"]
        if len(candidates) >= 2:
            scores = [c["candidate_score"] for c in candidates]
            self.assertEqual(scores, sorted(scores, reverse=True))

    # ── Insufficient points ────────────────────────────────────────────────────

    def test_detect_insufficient_points_needs_attention(self) -> None:
        """Only 2 data points → detectability.status = 'needs_attention', 0 candidates."""
        session_id = self._make_session()
        # Use 2-day range: only 2 buckets, below the min threshold of 3
        result = self._detect(
            session_id,
            self.spike_metric,
            start="2026-01-01",
            end="2026-01-03",  # 2 days
        )
        self.assertEqual(result["detectability"]["status"], "needs_attention")
        issues = result["detectability"]["issues"]
        self.assertTrue(any(i["code"] == "insufficient_points" for i in issues))
        guidance = result["detectability"]["guidance"]
        self.assertEqual(guidance["reason"], "insufficient_points")
        self.assertEqual(guidance["minimum_points_required"], 3)
        self.assertEqual(guidance["recommended_next_action"], "expand_scan_window")
        self.assertEqual(
            guidance["recommended_current_window"],
            {"start": "2025-12-31", "end": "2026-01-03"},
        )
        self.assertEqual(result["scan_summary"]["total_candidate_count"], 0)

    # ── Candidate ref ──────────────────────────────────────────────────────────

    def test_detect_candidate_ref_structure(self) -> None:
        """Each candidate has a valid candidate_ref with artifact_ref and item_ref."""
        session_id = self._make_session()
        result = self._detect(session_id, self.spike_metric)
        for i, c in enumerate(result["candidates"]):
            ref = c["candidate_ref"]
            self.assertIn("artifact_ref", ref)
            self.assertIn("item_ref", ref)
            self.assertEqual(ref["artifact_ref"]["session_id"], session_id)
            self.assertEqual(ref["artifact_ref"]["step_type"], "detect")
            self.assertEqual(ref["item_ref"]["collection"], "candidates")
            self.assertEqual(ref["item_ref"]["index"], i)

    # ── Metric not found ───────────────────────────────────────────────────────

    def test_detect_unknown_metric_raises_value_error(self) -> None:
        """Unresolved metric → ValueError."""
        session_id = self._make_session()
        with self.assertRaises(ValueError, msg="expect ValueError for unknown metric"):
            self.service.run_intent(
                session_id,
                "detect",
                {
                    "metric": _metric_ref("nonexistent_metric_xyz_abc"),
                    "time_scope": {
                        "mode": "single_window",
                        "grain": "day",
                        "current": {"start": "2026-01-01", "end": "2026-01-15"},
                    },
                },
            )

    # ── response time_scope shape ─────────────────────────────────────────────

    def test_detect_response_time_scope_shape(self) -> None:
        """Response time_scope must use mode/grain/current schema."""
        session_id = self._make_session()
        result = self._detect(session_id, self.spike_metric)
        ts = result["time_scope"]
        self.assertEqual(ts["mode"], "single_window")
        self.assertEqual(ts["grain"], "day")
        self.assertIn("current", ts)
        self.assertIn("start", ts["current"])
        self.assertIn("end", ts["current"])


# ── HTTP endpoint tests ───────────────────────────────────────────────────────


class DetectIntentEndpointTests(unittest.TestCase):
    """HTTP-level tests for /sessions/{id}/intents/detect.

    Uses the standard seeded DuckDB (analytics.watch_events) so the query
    can execute via the default fallback analytics engine.  watch_events has
    uniform per-day row counts, so detect returns 0 candidates — testing the
    success-empty artifact path.  Spike detection is covered by the direct
    service tests (DetectRunnerServiceTests).
    """

    @classmethod
    def setUpClass(cls) -> None:
        from tests.shared_fixtures import get_seeded_duckdb_path

        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "detect_http.duckdb"
        get_seeded_duckdb_path(db_path)

        # Create the metadata store separately so we can seed it before the app starts.
        meta_path = db_path.with_suffix(".meta.sqlite")
        metadata = SQLiteMetadataStore(str(meta_path))
        metadata.initialize()

        # Register metric pointing to analytics.watch_events (exists in seeded DuckDB).
        # No engine binding → QueryRouter falls back to the default analytics engine.
        _seed_metadata(
            metadata,
            src_suffix="http01",
            metric_name="http_detect_metric",
            table_fqn="analytics.watch_events",
            native_name="watch_events",
        )

        cls.client = TestClient(create_app(db_path=db_path, metadata_store=metadata))
        r = cls.client.post("/sessions", json={"goal": "detect HTTP test"})
        cls.session_id = r.json()["session_id"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def _time_scope(
        self, start: str = "2026-02-07", end: str = "2026-03-08", grain: str = "day"
    ) -> dict:
        return {"mode": "single_window", "grain": grain, "current": {"start": start, "end": end}}

    def test_detect_missing_metric_returns_422(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/detect",
            json={"time_scope": self._time_scope()},
        )
        self.assertEqual(r.status_code, 422)

    def test_detect_missing_time_scope_returns_422(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/detect",
            json={"metric": _metric_ref("http_detect_metric")},
        )
        self.assertEqual(r.status_code, 422)

    def test_detect_unknown_metric_returns_422(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/detect",
            json={
                "metric": _metric_ref("metric_that_does_not_exist_xyz"),
                "time_scope": self._time_scope(),
            },
        )
        self.assertEqual(r.status_code, 422)

    def test_detect_not_ready_metric_returns_409_with_structured_readiness_error(self) -> None:
        metadata = self.client.app.state.service.metadata
        metric_name = _seed_metadata(
            metadata,
            src_suffix="http_not_ready",
            metric_name="http_detect_not_ready_metric",
            table_fqn="analytics.watch_events",
            native_name="watch_events",
            metric_input_target_keys=["numerator"],
            measure_type="average",
        )

        response = self.client.post(
            f"/sessions/{self.session_id}/intents/detect",
            json={
                "metric": _metric_ref(metric_name),
                "time_scope": self._time_scope(),
            },
        )

        self.assertEqual(response.status_code, 409, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "semantic_not_ready")
        self.assertEqual(detail["category"], "readiness")
        self.assertEqual(detail["subject_ref"], "metric.http_detect_not_ready_metric")
        self.assertEqual(detail["readiness_status"], "not_ready")

    def test_detect_ready_metric_with_auxiliary_binding_returns_200(self) -> None:
        metadata = self.client.app.state.service.metadata
        metric_name = _seed_metadata(
            metadata,
            src_suffix="http_aux",
            metric_name="http_detect_aux_metric",
            table_fqn="analytics.watch_events",
            native_name="watch_events",
            binding_role="auxiliary",
        )

        response = self.client.post(
            f"/sessions/{self.session_id}/intents/detect",
            json={
                "metric": _metric_ref(metric_name),
                "time_scope": self._time_scope(),
                "sensitivity": "balanced",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["artifact_type"], "anomaly_candidates")

    def test_detect_invalid_time_scope_returns_422(self) -> None:
        """start >= end is rejected with 422."""
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/detect",
            json={
                "metric": _metric_ref("http_detect_metric"),
                "time_scope": self._time_scope(start="2026-02-21", end="2026-02-07"),
            },
        )
        self.assertEqual(r.status_code, 422)

    def test_detect_invalid_mode_returns_422(self) -> None:
        """mode != 'single_window' → 422."""
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/detect",
            json={
                "metric": _metric_ref("http_detect_metric"),
                "time_scope": {
                    "mode": "compare",
                    "grain": "day",
                    "current": {"start": "2026-02-07", "end": "2026-03-08"},
                },
            },
        )
        self.assertEqual(r.status_code, 422)

    def test_detect_invalid_grain_returns_422(self) -> None:
        """Unsupported grain → 422."""
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/detect",
            json={
                "metric": _metric_ref("http_detect_metric"),
                "time_scope": {
                    "mode": "single_window",
                    "grain": "week",
                    "current": {"start": "2026-02-07", "end": "2026-03-08"},
                },
            },
        )
        self.assertEqual(r.status_code, 422)

    def test_detect_returns_200_with_valid_metric(self) -> None:
        """Full detect execution returns 200 with anomaly_candidates artifact."""
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/detect",
            json={
                "metric": _metric_ref("http_detect_metric"),
                # Seeded data covers 2026-02-07 to 2026-03-07
                "time_scope": self._time_scope(),
                "sensitivity": "balanced",
            },
        )
        self.assertEqual(r.status_code, 200, msg=r.text)
        body = r.json()
        self.assertEqual(body["artifact_type"], "anomaly_candidates")
        self.assertIn("scan_summary", body)
        self.assertIn("total_candidate_count", body["scan_summary"])

    def test_detect_success_empty_on_uniform_data(self) -> None:
        """Uniform watch_events data: detect returns 200 with total_candidate_count = 0."""
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/detect",
            json={
                "metric": _metric_ref("http_detect_metric"),
                "time_scope": self._time_scope(),
                "sensitivity": "balanced",
            },
        )
        self.assertEqual(r.status_code, 200, msg=r.text)
        body = r.json()
        # watch_events has the same number of rows per day (256) → std=0 → no candidates
        self.assertEqual(body["scan_summary"]["total_candidate_count"], 0)

    def test_detect_nonexistent_session_returns_404(self) -> None:
        r = self.client.post(
            "/sessions/sess_does_not_exist/intents/detect",
            json={
                "metric": _metric_ref("http_detect_metric"),
                "time_scope": self._time_scope(),
            },
        )
        self.assertEqual(r.status_code, 404)
