"""Tests for the `detect` atomic intent runner (Phase 3b-4).

Covers:
  - run_detect_intent: empty result (uniform data → no candidates)
  - run_detect_intent: spike data → candidate detected
  - run_detect_intent: sensitivity threshold differences
  - run_detect_intent: candidate ranking (score desc)
  - run_detect_intent: insufficient points → detectability needs_attention
  - run_detect_intent: artifact schema required fields present
  - run_detect_intent: limit truncation
  - run_detect_intent: dimension scans independent series
  - run_detect_intent: strategy echoed in response
  - run_detect_intent: invalid sensitivity → ValueError
  - run_detect_intent: invalid granularity → ValueError
  - run_detect_intent: invalid strategy → ValueError
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from marivo.adapters.local.duckdb_analytics import DuckDBAnalyticsEngine
from marivo.adapters.local.sqlite_metadata import SQLiteMetadataStore
from marivo.runtime.intents.detect import run_detect_intent
from tests.runtime.intents._runner_fixtures import (
    _FAKE_ARTIFACT_ID,
    _SESSION,
    _make_compiled_mock,
)
from tests.semantic_test_helpers import (
    build_runtime,
    ensure_published_typed_metric,
    ensure_published_typed_metric_binding,
    seed_duckdb_source_object,
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
    db_path: str | Path | None = None,
    src_suffix: str = "01",
    metric_name: str = "detect_event_count",
    table_fqn: str = "analytics.detect_events",
    native_name: str = "detect_events",
    binding_role: str = "primary",
    measure_type: str | None = None,
    dimensions: list[str] | None = None,
) -> str:
    """Insert minimal metadata records so detect can resolve metric → table."""
    now = datetime.now(UTC).isoformat()
    src_id = f"ds_detecttest{src_suffix}"
    obj_id = f"obj_detecttest{src_suffix}"
    seed_duckdb_source_object(
        meta,
        source_id=src_id,
        object_id=obj_id,
        display_name="Detect Test Source",
        table_name=native_name,
        table_fqn=table_fqn,
        now=now,
        db_path=db_path,
    )
    ensure_published_typed_metric(
        meta,
        metric_name=metric_name,
        display_name=metric_name,
        grain="day",
        dimensions=dimensions or ["event_date"],
        definition_sql="COUNT(*)",
        measure_type=measure_type,
    )
    ensure_published_typed_metric_binding(
        meta,
        metric_name=metric_name,
        carrier_locator=table_fqn,
        source_object_ref=obj_id,
        binding_role=binding_role,
        dimension_names=dimensions or ["event_date"],
    )
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
            db_path=db_path,
            src_suffix="01",
            metric_name="detect_event_count",
            table_fqn="analytics.detect_events",
            native_name="detect_events",
            dimensions=["event_date", "dimension.cluster"],
        )
        # Uniform metric: uniform_event_count → analytics.uniform_events
        cls.uniform_metric = _seed_metadata(
            cls.metadata,
            db_path=db_path,
            src_suffix="02",
            metric_name="uniform_event_count",
            table_fqn="analytics.uniform_events",
            native_name="uniform_events",
            dimensions=["event_date", "dimension.cluster"],
        )

        cls.service = build_runtime(cls.metadata, cls.analytics)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def _make_session(self) -> str:
        r = self.service.create_session("detect test session")
        if isinstance(r, dict):
            return str(r["session_id"])
        return str(r.session_id)

    def _detect(
        self,
        session_id: str,
        metric: str,
        start: str = "2026-01-01",
        end: str = "2026-01-15",
        sensitivity: str | None = None,
        **extra: object,
    ) -> dict:
        params: dict = {
            "metric": _metric_ref(metric),
            "time_scope": {"field": "event_date", "start": start, "end": end},
            "granularity": "day",
            "strategy": "point_anomaly",
        }
        if sensitivity is not None:
            params["sensitivity"] = sensitivity
        params.update(extra)
        envelope = run_detect_intent(self.service, session_id, params)
        return envelope["result"]

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
        self.assertEqual(
            result["analytical_metadata"]["baseline_method"]["methods"]["point_anomaly"],
            "scan_window_zscore",
        )
        self.assertIn("provenance", result)
        self.assertIn("detector_version", result["provenance"])
        self.assertIn("artifact_id", result)
        self.assertIsNotNone(result["artifact_id"])
        # v1 required fields
        self.assertIn("dimension", result)
        self.assertIn("strategy", result)

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
        self.assertEqual(top["candidate_type"], "point_anomaly")
        self.assertIn("candidate_ref", top)
        self.assertIn("current_value", top)
        self.assertIn("baseline_value", top)
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

    # ── Strategy ──────────────────────────────────────────────────────────────

    def test_detect_strategy_echoed_in_response(self) -> None:
        """Explicitly requested strategy is stored in the artifact."""
        session_id = self._make_session()
        result = self._detect(session_id, self.spike_metric, strategy="period_shift")
        self.assertEqual(result["strategy"], "period_shift")

    def test_detect_sensitivity_defaults_to_aggressive(self) -> None:
        """Omitted sensitivity normalises to 'aggressive'."""
        session_id = self._make_session()
        result = self._detect(session_id, self.spike_metric)
        self.assertEqual(result["sensitivity"], "aggressive")

    def test_detect_period_shift_finds_structural_degradation(self) -> None:
        """period_shift compares the whole range to previous adjacent baseline."""
        session_id = self._make_session()
        result = self._detect(
            session_id,
            self.spike_metric,
            start="2026-01-09",
            end="2026-01-15",
            strategy="period_shift",
        )

        self.assertEqual(result["strategy"], "period_shift")
        self.assertEqual(result["scan_summary"]["total_candidate_count"], 1)
        candidate = result["candidates"][0]
        self.assertEqual(candidate["candidate_type"], "period_shift")
        self.assertEqual(candidate["direction"], "down")
        self.assertEqual(candidate["window"], {"start": "2026-01-09", "end": "2026-01-15"})
        self.assertEqual(
            candidate["baseline_window"],
            {"start": "2026-01-03", "end": "2026-01-09"},
        )
        self.assertLessEqual(candidate["deviation_pct"], -0.20)

    def test_detect_point_anomaly_ignores_uniform_structural_window(self) -> None:
        """point_anomaly alone returns 0 when all current-window buckets are similar."""
        session_id = self._make_session()
        result = self._detect(
            session_id,
            self.spike_metric,
            start="2026-01-09",
            end="2026-01-15",
            strategy="point_anomaly",
        )

        self.assertEqual(result["strategy"], "point_anomaly")
        self.assertEqual(result["scan_summary"]["total_candidate_count"], 0)

    def test_detect_invalid_strategy_raises(self) -> None:
        """Unknown strategy → ValueError."""
        session_id = self._make_session()
        with self.assertRaises(ValueError):
            self._detect(session_id, self.spike_metric, strategy="zscore_raw")

    # ── filter ───────────────────────────────────────────────────────────────

    def test_detect_filter_limits_scanned_population(self) -> None:
        """AOI filter expression narrows the scan through runtime predicate scope."""
        session_id = self._make_session()
        result = self._detect(
            session_id,
            self.spike_metric,
            filter={"dialects": [{"dialect": "ANSI_SQL", "expression": "cluster = 'beta'"}]},
        )

        self.assertEqual(result["scope"], {"predicate": "cluster = 'beta'"})
        self.assertEqual(result["scan_summary"]["total_candidate_count"], 0)
        self.assertEqual(result["candidates"], [])

    def test_detect_invalid_filter_raises(self) -> None:
        session_id = self._make_session()
        with self.assertRaises(ValueError):
            self._detect(session_id, self.spike_metric, filter={"dialects": []})

    # ── dimension ─────────────────────────────────────────────────────────────

    def test_detect_dimension_scans_independent_series(self) -> None:
        """dimension scans each dimension value as an independent series."""
        session_id = self._make_session()
        result = self._detect(session_id, self.spike_metric, dimension="dimension.cluster")

        self.assertEqual(result["dimension"], "dimension.cluster")
        self.assertEqual(result["scan_summary"]["eligible_series_count"], 2)
        self.assertEqual(result["scan_summary"]["scanned_series_count"], 2)
        self.assertEqual(result["scan_summary"]["excluded_series_count"], 0)
        self.assertGreaterEqual(result["scan_summary"]["total_candidate_count"], 1)
        self.assertTrue(
            any(c["slice"] == {"dimension.cluster": "alpha"} for c in result["candidates"])
        )

    def test_detect_dimension_unsupported_dimension_raises(self) -> None:
        """Unsupported split dimension is rejected instead of falling back to overall scan."""
        session_id = self._make_session()
        with self.assertRaises(ValueError):
            self._detect(session_id, self.spike_metric, dimension="dimension.missing_cluster")

    def test_detect_dimension_declared_dimension_without_physical_column_raises(self) -> None:
        """Declared split dimension must resolve to an executable column."""
        metric_name = _seed_metadata(
            self.metadata,
            db_path=Path(self.temp_dir.name) / "detect_svc.duckdb",
            src_suffix="03",
            metric_name="detect_missing_physical_dimension",
            table_fqn="analytics.detect_events",
            native_name="detect_events",
            dimensions=["event_date", "dimension.cluster_missing"],
        )
        session_id = self._make_session()
        with self.assertRaises(ValueError):
            self._detect(session_id, metric_name, dimension="dimension.cluster_missing")

    def test_detect_dimension_non_string_raises(self) -> None:
        """Only a single dimension string is supported."""
        session_id = self._make_session()
        with self.assertRaises(ValueError):
            self._detect(session_id, self.spike_metric, dimension=["dimension.cluster"])

    def test_detect_dimension_null_when_omitted(self) -> None:
        """Omitted dimension is null in the artifact."""
        session_id = self._make_session()
        result = self._detect(session_id, self.spike_metric)
        self.assertIsNone(result["dimension"])
        self.assertEqual(result["scan_summary"]["eligible_series_count"], 1)
        self.assertEqual(result["scan_summary"]["scanned_series_count"], 1)
        self.assertEqual(result["scan_summary"]["excluded_series_count"], 0)

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

    def test_detect_limit_must_be_positive(self) -> None:
        session_id = self._make_session()
        with self.assertRaises(ValueError):
            self._detect(session_id, self.spike_metric, limit=0)

    # ── time_scope validation ─────────────────────────────────────────────────

    def test_detect_hour_granularity_requires_datetime_boundaries(self) -> None:
        session_id = self._make_session()
        with self.assertRaises(ValueError):
            self._detect(session_id, self.spike_metric, granularity="hour")

    def test_detect_invalid_grain_raises(self) -> None:
        """Unsupported granularity → ValueError."""
        session_id = self._make_session()
        with self.assertRaises(ValueError):
            run_detect_intent(
                self.service,
                session_id,
                {
                    "metric": self.spike_metric,
                    "time_scope": {
                        "field": "event_date",
                        "start": "2026-01-01",
                        "end": "2026-01-15",
                    },
                    "granularity": "minute",
                    "strategy": "point_anomaly",
                },
            )

    def test_detect_invalid_time_scope_start_gte_end_raises(self) -> None:
        """start >= end → ValueError."""
        session_id = self._make_session()
        with self.assertRaises(ValueError):
            run_detect_intent(
                self.service,
                session_id,
                {
                    "metric": self.spike_metric,
                    "time_scope": {
                        "field": "event_date",
                        "start": "2026-01-15",
                        "end": "2026-01-01",
                    },
                    "granularity": "day",
                    "strategy": "point_anomaly",
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
            run_detect_intent(
                self.service,
                session_id,
                {
                    "metric": _metric_ref("nonexistent_metric_xyz_abc"),
                    "time_scope": {
                        "field": "event_date",
                        "start": "2026-01-01",
                        "end": "2026-01-15",
                    },
                    "granularity": "day",
                    "strategy": "point_anomaly",
                },
            )

    # ── response time_scope shape ─────────────────────────────────────────────

    def test_detect_response_time_scope_shape(self) -> None:
        """Response time_scope must use canonical field/start/end plus top-level granularity."""
        session_id = self._make_session()
        result = self._detect(session_id, self.spike_metric)
        ts = result["time_scope"]
        self.assertEqual(ts["field"], "event_date")
        self.assertEqual(ts["start"], "2026-01-01")
        self.assertEqual(ts["end"], "2026-01-15")
        self.assertEqual(result["granularity"], "day")


class TestDetectRunnerCommitPath(unittest.TestCase):
    """run_detect_intent must call _commit_artifact_with_extraction(step_type='detect')."""

    def _make_runtime(self) -> MagicMock:
        runtime = MagicMock()
        runtime.core = MagicMock()
        runtime.new_step_id.return_value = "step_4c2_001"
        runtime.commit_artifact_with_extraction.return_value = _FAKE_ARTIFACT_ID
        runtime.insert_step.return_value = None
        runtime.make_provenance.return_value = {"query_hash": "testhash"}
        runtime.build_step_semantic_metadata.return_value = {}
        return runtime

    def _run_detect(
        self,
        runtime: MagicMock,
        *,
        params_patch: dict[str, Any] | None = None,
        rows: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        from marivo.runtime.intents.detect import run_detect_intent

        runtime.core.normalize_intent_metric_ref.return_value = "m1"
        runtime.core.metric_name_from_ref.return_value = "m1"
        runtime.resolve_metric_execution_context.return_value = MagicMock(table_name="src.metrics")
        runtime.resolve_metric_table.return_value = "src.metrics"
        runtime.resolve_metric_dimensions.return_value = []
        runtime.resolve_engine_for_session.return_value = (
            MagicMock(),
            "duckdb",
            {"metrics": "src.metrics"},
        )
        runtime.resolve_metric_sql_for_execution.return_value = "SUM(val)"
        runtime.build_scoped_query.return_value = None
        runtime.compile_step.return_value = _make_compiled_mock()

        params = {
            "metric": "m1",
            "time_scope": {"field": "event_date", "start": "2024-01-01", "end": "2024-01-31"},
            "granularity": "day",
            "strategy": "point_anomaly",
        }
        if params_patch:
            params.update(params_patch)
        with patch("marivo.runtime.intents.detect.execute_compiled") as mock_exec:
            # 9 points with one spike (day 5 = 200) to produce ≥1 anomaly candidate.
            # mean≈111, std≈31, z(200)≈2.83 > balanced threshold 2.0.
            mock_exec.return_value.rows = rows or [
                {"bucket_start": f"2024-01-{d:02d}", "value": 200.0 if d == 5 else 100.0}
                for d in range(1, 10)
            ]
            return run_detect_intent(runtime, _SESSION, params)

    def test_detect_calls_commit_artifact_with_extraction(self) -> None:
        runtime = self._make_runtime()
        self._run_detect(runtime)
        runtime.commit_artifact_with_extraction.assert_called_once()

    def test_detect_passes_step_type_detect(self) -> None:
        runtime = self._make_runtime()
        self._run_detect(runtime)
        _, kwargs = runtime.commit_artifact_with_extraction.call_args
        self.assertEqual(kwargs.get("step_type"), "detect")

    def test_detect_artifact_type_is_anomaly_candidates(self) -> None:
        runtime = self._make_runtime()
        self._run_detect(runtime)
        args, _ = runtime.commit_artifact_with_extraction.call_args
        self.assertEqual(args[2], "anomaly_candidates")

    def test_detect_artifact_id_patched_in_result(self) -> None:
        # After _commit_artifact_with_extraction returns, detect.py patches artifact_id
        # into result["candidates"][*]["candidate_ref"]["artifact_ref"]["artifact_id"]
        # and result["artifact_id"].  Verify both are populated with the committed id.
        runtime = self._make_runtime()
        envelope = self._run_detect(runtime)
        result = envelope["result"]
        self.assertEqual(envelope["artifact_id"], _FAKE_ARTIFACT_ID)
        self.assertEqual(result["artifact_id"], _FAKE_ARTIFACT_ID)
        candidates = result.get("candidates", [])
        self.assertTrue(len(candidates) > 0, "expected at least one candidate in result")
        for c in candidates:
            self.assertEqual(
                c["candidate_ref"]["artifact_ref"]["artifact_id"],
                _FAKE_ARTIFACT_ID,
            )

    def test_detect_hour_granularity_accepts_datetime_boundaries(self) -> None:
        runtime = self._make_runtime()
        envelope = self._run_detect(
            runtime,
            params_patch={
                "time_scope": {
                    "field": "event_time",
                    "start": "2024-01-01T00:00:00",
                    "end": "2024-01-01T09:00:00",
                },
                "granularity": "hour",
            },
            rows=[
                {
                    "bucket_start": f"2024-01-01T{h:02d}:00:00",
                    "value": 200.0 if h == 4 else 100.0,
                }
                for h in range(9)
            ],
        )
        result = envelope["result"]

        self.assertEqual(result["granularity"], "hour")
        self.assertEqual(result["time_scope"]["start"], "2024-01-01T00:00:00")
        self.assertEqual(result["time_scope"]["end"], "2024-01-01T09:00:00")


# ── correlate ─────────────────────────────────────────────────────────────────
