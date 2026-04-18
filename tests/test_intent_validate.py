"""Tests for the `validate` derived intent runner (Phase 3c-3).

Covers:
  - run_validate_intent: full expansion creates observe×2 + test + validate steps (4 total)
  - run_validate_intent: refs.left_observation_ref.step_id points to an observe step
  - run_validate_intent: refs.right_observation_ref.step_id points to an observe step
  - run_validate_intent: refs.test_ref.step_id points to a test step
  - run_validate_intent: validation.status = "validated" on clean numeric data
  - run_validate_intent: result.decision = "reject_null" for clearly different groups
  - run_validate_intent: result.decision = "fail_to_reject" when both sides have same data
  - run_validate_intent: artifact_id persisted and retrievable
  - run_validate_intent: result_type = "validation_bundle"
  - run_validate_intent: sample_kind = "rate" triggers rate_sample_summary observations
  - run_validate_intent: hypothesis.alternative = "greater" propagated to bundle
  - run_validate_intent: method = "auto" resolved to concrete method in bundle
  - run_validate_intent: result.estimate has estimand and value
  - run_validate_intent: missing metric → ValueError
  - run_validate_intent: missing left.time_scope → ValueError
  - run_validate_intent: missing right.time_scope → ValueError
  - run_validate_intent: sample_kind = "auto" → ValueError (SAMPLE_KIND_AMBIGUOUS)
  - run_validate_intent: hypothesis.family = "ratio" → ValueError
  - run_validate_intent: hypothesis.alpha = 0 → ValueError
  - run_validate_intent: method = "badmethod" → ValueError
  - HTTP endpoint: valid validate returns 200 with result_type = "validation_bundle"
  - HTTP endpoint: missing left returns 422
  - HTTP endpoint: unknown session returns 404
  - HTTP endpoint: sample_kind omitted returns 422 with SAMPLE_KIND_AMBIGUOUS
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import duckdb
from fastapi.testclient import TestClient

from app.main import create_app
from app.service import SemanticLayerService
from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
from app.storage.sqlite_metadata import SQLiteMetadataStore
from tests.semantic_test_helpers import (
    ensure_published_typed_metric,
    ensure_published_typed_metric_binding,
)


def _metric_ref(name: str) -> str:
    return f"metric.{name}"


# ── Constants ──────────────────────────────────────────────────────────────────

_METRIC_NUMERIC = "val_numeric"
_METRIC_RATE = "val_rate"

# Window A: 5 rows, values [95, 98, 100, 102, 105], mean=100, std≈3.81
_WINDOW_A_START = "2024-01-01"
_WINDOW_A_END = "2024-01-06"  # exclusive

# Window B: 5 rows, values [5, 8, 10, 12, 15], mean=10, std≈3.81
# welch_t of A vs B: t≈37, p≈0 → reject_null ✓
_WINDOW_B_START = "2024-02-01"
_WINDOW_B_END = "2024-02-06"  # exclusive

_WINDOW_A_VALUES = [95.0, 98.0, 100.0, 102.0, 105.0]
_WINDOW_B_VALUES = [5.0, 8.0, 10.0, 12.0, 15.0]

# binary_values: window A → k=4 successes out of 5; window B → k=1 out of 5
_WINDOW_A_BINARY = [1.0, 1.0, 1.0, 1.0, 0.0]
_WINDOW_B_BINARY = [0.0, 0.0, 0.0, 0.0, 1.0]

_DATES_A = ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
_DATES_B = ["2024-02-01", "2024-02-02", "2024-02-03", "2024-02-04", "2024-02-05"]


def _resolved_policy_summary(
    *,
    policy_ref: str = "calendar_policy.weekday_yoy",
    comparison_basis: str = "yoy",
    resolved_calendar_source: str = "calendar.cn_public_holidays",
    resolved_calendar_version: str = "2026.01",
) -> dict[str, object]:
    return {
        "policy_ref": policy_ref,
        "comparison_basis": comparison_basis,
        "resolved_calendar_source": resolved_calendar_source,
        "resolved_calendar_version": resolved_calendar_version,
        "resolved_baseline_generation_rule": {
            "strategy": "offset",
            "offset_value": -1,
            "offset_unit": "year",
            "fixed_start": None,
            "fixed_end": None,
            "named_window_ref": None,
        },
        "current_window": {"start": _WINDOW_A_START, "end": _WINDOW_A_END},
        "baseline_window": {"start": "2023-01-01", "end": "2023-01-06"},
        "bucket_pairing": [
            {
                "current_bucket_start": _WINDOW_A_START,
                "baseline_bucket_start": "2023-01-01",
                "pairing_reason": "same_weekday_nearest",
                "shift_days": -364,
                "issues": [],
            }
        ],
        "coverage_summary": {
            "aligned_bucket_count": 5,
            "unpaired_bucket_count": 0,
            "aligned_ratio": 1.0,
        },
        "comparability_warnings": [],
    }


# ── Seeding helpers ────────────────────────────────────────────────────────────


def _seed_val_table(db_path: Path) -> None:
    """Create analytics.val_events with two time windows having distinct value profiles."""
    con = duckdb.connect(str(db_path))
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS analytics.val_events (
                event_date   DATE   NOT NULL,
                value        DOUBLE NOT NULL,
                binary_value DOUBLE NOT NULL
            )
            """
        )
        rows: list[tuple[str, float, float]] = []
        for d, v, b in zip(_DATES_A, _WINDOW_A_VALUES, _WINDOW_A_BINARY, strict=True):
            rows.append((d, v, b))
        for d, v, b in zip(_DATES_B, _WINDOW_B_VALUES, _WINDOW_B_BINARY, strict=True):
            rows.append((d, v, b))
        con.executemany("INSERT INTO analytics.val_events VALUES (?, ?, ?)", rows)
    finally:
        con.close()


def _seed_metadata(meta: SQLiteMetadataStore) -> None:
    """Insert minimal metadata for both val_numeric and val_rate metrics."""
    now = "2024-06-01T00:00:00"
    src_id = "src_valtest01"
    obj_id = "obj_valtest01"
    meta.execute(
        "INSERT OR IGNORE INTO sources "
        "(source_id, source_type, display_name, connection_json, capabilities_json, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [src_id, "duckdb", "Val Test Source", "{}", "{}", now, now],
    )
    meta.execute(
        "INSERT OR IGNORE INTO source_objects "
        "(object_id, source_id, object_type, native_name, fqn, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [obj_id, src_id, "table", "val_events", "analytics.val_events", now, now],
    )

    ensure_published_typed_metric(
        meta,
        metric_name=_METRIC_NUMERIC,
        display_name=_METRIC_NUMERIC,
        grain="day",
        dimensions=["event_date"],
        definition_sql="value",
    )
    ensure_published_typed_metric(
        meta,
        metric_name=_METRIC_RATE,
        display_name=_METRIC_RATE,
        grain="day",
        dimensions=["event_date"],
        definition_sql="binary_value",
        measure_type="rate",
    )
    ensure_published_typed_metric_binding(
        meta,
        metric_name=_METRIC_NUMERIC,
        carrier_locator="analytics.val_events",
        source_object_ref=obj_id,
        surface_name="value",
    )
    ensure_published_typed_metric_binding(
        meta,
        metric_name=_METRIC_RATE,
        carrier_locator="analytics.val_events",
        source_object_ref=obj_id,
        surface_name="binary_value",
    )


# ── Direct service tests ───────────────────────────────────────────────────────


class ValidateRunnerServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "val_svc.duckdb"
        meta_path = Path(cls.temp_dir.name) / "val_svc.meta.sqlite"

        cls.analytics = DuckDBAnalyticsEngine(str(db_path))
        cls.metadata = SQLiteMetadataStore(str(meta_path))
        cls.metadata.initialize()
        cls.analytics.initialize()

        _seed_val_table(db_path)
        _seed_metadata(cls.metadata)

        cls.service = SemanticLayerService(cls.metadata, cls.analytics)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def _make_session(self) -> str:
        return self.service.create_session("validate test", {}, {}, {})["session_id"]

    def _validate_numeric(
        self,
        session_id: str,
        left_start: str = _WINDOW_A_START,
        left_end: str = _WINDOW_A_END,
        right_start: str = _WINDOW_B_START,
        right_end: str = _WINDOW_B_END,
        alternative: str = "two_sided",
        alpha: float = 0.05,
    ) -> dict:
        return self.service.run_intent(
            session_id,
            "validate",
            {
                "metric": _METRIC_NUMERIC,
                "left": {"time_scope": {"kind": "range", "start": left_start, "end": left_end}},
                "right": {"time_scope": {"kind": "range", "start": right_start, "end": right_end}},
                "sample_kind": "numeric",
                "hypothesis": {"family": "difference", "alternative": alternative, "alpha": alpha},
                "method": "auto",
            },
        )

    def test_full_expansion_creates_four_steps(self) -> None:
        """validate expands to observe×2 + test + validate = 4 steps in DB."""
        sid = self._make_session()
        self._validate_numeric(sid)
        rows = self.metadata.query_rows("SELECT step_type FROM steps WHERE session_id = ?", [sid])
        step_types = [r["step_type"] for r in rows]
        self.assertEqual(step_types.count("observe"), 2)
        self.assertEqual(step_types.count("test"), 1)
        self.assertEqual(step_types.count("validate"), 1)
        self.assertEqual(len(step_types), 4)

    def test_left_observation_ref_points_to_observe_step(self) -> None:
        """refs.left_observation_ref.step_id resolves to an observe step in DB."""
        sid = self._make_session()
        bundle = self._validate_numeric(sid)
        left_ref = bundle["refs"]["left_observation_ref"]
        self.assertIsNotNone(left_ref)
        rows = self.metadata.query_rows(
            "SELECT step_type FROM steps WHERE session_id = ? AND step_id = ?",
            [sid, left_ref["step_id"]],
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["step_type"], "observe")

    def test_right_observation_ref_points_to_observe_step(self) -> None:
        """refs.right_observation_ref.step_id resolves to an observe step in DB."""
        sid = self._make_session()
        bundle = self._validate_numeric(sid)
        right_ref = bundle["refs"]["right_observation_ref"]
        self.assertIsNotNone(right_ref)
        rows = self.metadata.query_rows(
            "SELECT step_type FROM steps WHERE session_id = ? AND step_id = ?",
            [sid, right_ref["step_id"]],
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["step_type"], "observe")

    def test_test_ref_points_to_test_step(self) -> None:
        """refs.test_ref.step_id resolves to a test step in DB."""
        sid = self._make_session()
        bundle = self._validate_numeric(sid)
        test_ref = bundle["refs"]["test_ref"]
        self.assertIsNotNone(test_ref)
        rows = self.metadata.query_rows(
            "SELECT step_type FROM steps WHERE session_id = ? AND step_id = ?",
            [sid, test_ref["step_id"]],
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["step_type"], "test")

    def test_validation_status_validated_on_clean_data(self) -> None:
        """validation.status = 'validated' when both sides produce clean inferential artifacts."""
        sid = self._make_session()
        bundle = self._validate_numeric(sid)
        self.assertEqual(bundle["validation"]["status"], "validated")

    def test_result_type_is_validation_bundle(self) -> None:
        """result_type = 'validation_bundle'."""
        sid = self._make_session()
        bundle = self._validate_numeric(sid)
        self.assertEqual(bundle["result_type"], "validation_bundle")

    def test_reuses_calendar_alignment_through_internal_test(self) -> None:
        """validate should inherit frozen alignment metadata through its internal test step."""
        sid = self._make_session()
        summary = _resolved_policy_summary()
        original_commit_artifact = self.service._commit_artifact_with_extraction

        def _commit_artifact_with_alignment(
            session_id: str,
            step_id: str,
            artifact_type: str,
            name: str,
            content: Any,
            **kwargs: Any,
        ) -> str:
            if artifact_type == "observation":
                content_payload = dict(content)
                content_payload["resolved_policy_summary"] = summary
                content = content_payload
            return original_commit_artifact(
                session_id, step_id, artifact_type, name, content, **kwargs
            )

        with patch.object(
            self.service,
            "_commit_artifact_with_extraction",
            side_effect=_commit_artifact_with_alignment,
        ):
            bundle = self._validate_numeric(sid)

        test_step_id = bundle["refs"]["test_ref"]["step_id"]
        test_artifact = self.service._resolve_artifact_for_ref(sid, test_step_id)
        self.assertIsNotNone(test_artifact)
        self.assertEqual(bundle["validation"]["status"], "validated")
        self.assertEqual(
            test_artifact["source_lineage"]["calendar_alignment"]["reuse_source"],
            "observation_resolved_policy_summary",
        )
        self.assertEqual(
            test_artifact["source_lineage"]["calendar_alignment"]["policy_ref"],
            "calendar_policy.weekday_yoy",
        )

    def test_calendar_alignment_mismatch_fails_through_internal_test(self) -> None:
        """validate should fail when its internal test sees mismatched frozen metadata."""
        sid = self._make_session()
        summaries = [
            _resolved_policy_summary(),
            _resolved_policy_summary(policy_ref="calendar_policy.holiday_yoy"),
        ]
        original_commit_artifact = self.service._commit_artifact_with_extraction

        def _commit_artifact_with_alignment(
            session_id: str,
            step_id: str,
            artifact_type: str,
            name: str,
            content: Any,
            **kwargs: Any,
        ) -> str:
            if artifact_type == "observation":
                content_payload = dict(content)
                content_payload["resolved_policy_summary"] = summaries.pop(0)
                content = content_payload
            return original_commit_artifact(
                session_id, step_id, artifact_type, name, content, **kwargs
            )

        with (
            patch.object(
                self.service,
                "_commit_artifact_with_extraction",
                side_effect=_commit_artifact_with_alignment,
            ),
            self.assertRaisesRegex(
                ValueError,
                "validate: TEST_FAILED - hypothesis test failed: test: NOT_COMPARABLE - "
                "left and right observations freeze different calendar policies, so the "
                "comparison basis is not directly comparable. Re-run both observations "
                "with the same calendar_policy_ref before comparing them.",
            ),
        ):
            self._validate_numeric(sid)

    def test_reject_null_for_clearly_different_groups(self) -> None:
        """result.decision = 'reject_null' when window A (mean=100) vs window B (mean=10)."""
        sid = self._make_session()
        bundle = self._validate_numeric(sid)
        self.assertEqual(bundle["result"]["decision"], "reject_null")

    def test_fail_to_reject_when_both_sides_same_window(self) -> None:
        """result.decision = 'fail_to_reject' when both sides observe the same data (t_stat=0)."""
        sid = self._make_session()
        bundle = self._validate_numeric(
            sid,
            left_start=_WINDOW_A_START,
            left_end=_WINDOW_A_END,
            right_start=_WINDOW_A_START,  # same window as left
            right_end=_WINDOW_A_END,
        )
        self.assertEqual(bundle["result"]["decision"], "fail_to_reject")

    def test_artifact_id_persisted_and_retrievable(self) -> None:
        """artifact_id is persisted to the metadata store and can be queried."""
        sid = self._make_session()
        bundle = self._validate_numeric(sid)
        artifact_id = bundle["artifact_id"]
        self.assertIsNotNone(artifact_id)
        rows = self.metadata.query_rows(
            "SELECT artifact_id FROM artifacts WHERE artifact_id = ?",
            [artifact_id],
        )
        self.assertEqual(len(rows), 1)

    def test_sample_kind_rate_triggers_rate_summary_observations(self) -> None:
        """sample_kind='rate' causes observe steps to produce rate_sample_summary artifacts."""
        sid = self._make_session()
        bundle = self.service.run_intent(
            sid,
            "validate",
            {
                "metric": _METRIC_RATE,
                "left": {
                    "time_scope": {
                        "kind": "range",
                        "start": _WINDOW_A_START,
                        "end": _WINDOW_A_END,
                    }
                },
                "right": {
                    "time_scope": {
                        "kind": "range",
                        "start": _WINDOW_B_START,
                        "end": _WINDOW_B_END,
                    }
                },
                "sample_kind": "rate",
            },
        )
        left_ref = bundle["refs"]["left_observation_ref"]
        right_ref = bundle["refs"]["right_observation_ref"]
        self.assertEqual(left_ref["observation_type"], "rate_sample_summary")
        self.assertEqual(right_ref["observation_type"], "rate_sample_summary")
        # decision must be deterministic (not undetermined)
        self.assertIn(bundle["result"]["decision"], {"reject_null", "fail_to_reject"})

    def test_hypothesis_alternative_propagated_to_bundle(self) -> None:
        """hypothesis.alternative = 'greater' is echoed back in the bundle hypothesis field."""
        sid = self._make_session()
        bundle = self._validate_numeric(sid, alternative="greater")
        self.assertEqual(bundle["hypothesis"]["alternative"], "greater")

    def test_method_is_resolved_in_bundle(self) -> None:
        """method='auto' is resolved to a concrete method in the bundle (not 'auto')."""
        sid = self._make_session()
        bundle = self._validate_numeric(sid)  # method="auto" default
        self.assertIn(bundle["method"], {"welch_t", "two_proportion_z"})
        self.assertNotEqual(bundle["method"], "auto")

    def test_result_estimate_has_estimand_and_value(self) -> None:
        """result.estimate has required estimand and non-null value for clearly different groups."""
        sid = self._make_session()
        bundle = self._validate_numeric(sid)
        estimate = bundle["result"]["estimate"]
        self.assertIsNotNone(estimate)
        self.assertIn(estimate["estimand"], {"mean_diff", "rate_diff"})
        self.assertIsNotNone(estimate["value"])


# ── Validation boundary tests ──────────────────────────────────────────────────


class ValidateValidationBoundaryTests(unittest.TestCase):
    """Input validation checks that do not require a meaningful query result."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "val_val.duckdb"
        meta_path = Path(cls.temp_dir.name) / "val_val.meta.sqlite"

        analytics = DuckDBAnalyticsEngine(str(db_path))
        metadata = SQLiteMetadataStore(str(meta_path))
        metadata.initialize()
        analytics.initialize()

        _seed_val_table(db_path)
        _seed_metadata(metadata)

        cls.service = SemanticLayerService(metadata, analytics)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def _make_session(self) -> str:
        return self.service.create_session("val boundary", {}, {}, {})["session_id"]

    def _base_params(self, **overrides: object) -> dict:
        p: dict = {
            "metric": _METRIC_NUMERIC,
            "left": {
                "time_scope": {"kind": "range", "start": _WINDOW_A_START, "end": _WINDOW_A_END}
            },
            "right": {
                "time_scope": {"kind": "range", "start": _WINDOW_B_START, "end": _WINDOW_B_END}
            },
            "sample_kind": "numeric",
        }
        p.update(overrides)
        return p

    def test_missing_metric_raises_value_error(self) -> None:
        """Empty metric → ValueError mentioning 'metric'."""
        sid = self._make_session()
        with self.assertRaises(ValueError) as ctx:
            self.service.run_intent(sid, "validate", self._base_params(metric=""))
        self.assertIn("metric", str(ctx.exception).lower())

    def test_missing_left_time_scope_raises_value_error(self) -> None:
        """left.time_scope missing kind → ValueError."""
        sid = self._make_session()
        with self.assertRaises(ValueError) as ctx:
            self.service.run_intent(
                sid,
                "validate",
                self._base_params(left={"time_scope": {}}),
            )
        self.assertIn("left", str(ctx.exception).lower())

    def test_missing_right_time_scope_raises_value_error(self) -> None:
        """right.time_scope missing kind → ValueError."""
        sid = self._make_session()
        with self.assertRaises(ValueError) as ctx:
            self.service.run_intent(
                sid,
                "validate",
                self._base_params(right={"time_scope": {}}),
            )
        self.assertIn("right", str(ctx.exception).lower())

    def test_sample_kind_auto_raises_sample_kind_ambiguous(self) -> None:
        """sample_kind='auto' → ValueError: SAMPLE_KIND_AMBIGUOUS."""
        sid = self._make_session()
        with self.assertRaises(ValueError) as ctx:
            self.service.run_intent(sid, "validate", self._base_params(sample_kind="auto"))
        self.assertIn("SAMPLE_KIND_AMBIGUOUS", str(ctx.exception))

    def test_hypothesis_family_ratio_raises_value_error(self) -> None:
        """hypothesis.family='ratio' → ValueError (only 'difference' in v1)."""
        sid = self._make_session()
        with self.assertRaises(ValueError) as ctx:
            self.service.run_intent(
                sid,
                "validate",
                self._base_params(hypothesis={"family": "ratio"}),
            )
        self.assertIn("difference", str(ctx.exception))

    def test_hypothesis_alpha_zero_raises_value_error(self) -> None:
        """hypothesis.alpha=0 → ValueError (must be in (0,1))."""
        sid = self._make_session()
        with self.assertRaises(ValueError) as ctx:
            self.service.run_intent(
                sid,
                "validate",
                self._base_params(hypothesis={"alpha": 0}),
            )
        self.assertIn("alpha", str(ctx.exception).lower())

    def test_invalid_method_raises_value_error(self) -> None:
        """method='badmethod' → ValueError."""
        sid = self._make_session()
        with self.assertRaises(ValueError) as ctx:
            self.service.run_intent(sid, "validate", self._base_params(method="badmethod"))
        self.assertIn("method", str(ctx.exception).lower())


# ── HTTP endpoint tests ────────────────────────────────────────────────────────


class ValidateHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "val_http.duckdb"
        meta_path = Path(cls.temp_dir.name) / "val_http.meta.sqlite"

        analytics = DuckDBAnalyticsEngine(str(db_path))
        metadata = SQLiteMetadataStore(str(meta_path))
        metadata.initialize()
        analytics.initialize()

        _seed_val_table(db_path)
        _seed_metadata(metadata)

        app = create_app(metadata_store=metadata, analytics_engine=analytics)
        cls.client = TestClient(app)

        resp = cls.client.post("/sessions", json={"goal": "validate http test"})
        cls.session_id = resp.json()["session_id"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_valid_validate_returns_200_with_bundle(self) -> None:
        """Valid validate request returns 200 with result_type='validation_bundle'."""
        resp = self.client.post(
            f"/sessions/{self.session_id}/intents/validate",
            json={
                "metric": _metric_ref(_METRIC_NUMERIC),
                "left": {
                    "time_scope": {"kind": "range", "start": _WINDOW_A_START, "end": _WINDOW_A_END}
                },
                "right": {
                    "time_scope": {"kind": "range", "start": _WINDOW_B_START, "end": _WINDOW_B_END}
                },
                "sample_kind": "numeric",
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["result_type"], "validation_bundle")

    def test_missing_left_returns_422(self) -> None:
        """Missing required 'left' field returns 422."""
        resp = self.client.post(
            f"/sessions/{self.session_id}/intents/validate",
            json={
                "metric": _metric_ref(_METRIC_NUMERIC),
                "right": {
                    "time_scope": {"kind": "range", "start": _WINDOW_B_START, "end": _WINDOW_B_END}
                },
                "sample_kind": "numeric",
            },
        )
        self.assertEqual(resp.status_code, 422)

    def test_unknown_session_returns_404(self) -> None:
        """Unknown session_id returns 404."""
        resp = self.client.post(
            "/sessions/sess_nonexistent/intents/validate",
            json={
                "metric": _metric_ref(_METRIC_NUMERIC),
                "left": {
                    "time_scope": {"kind": "range", "start": _WINDOW_A_START, "end": _WINDOW_A_END}
                },
                "right": {
                    "time_scope": {"kind": "range", "start": _WINDOW_B_START, "end": _WINDOW_B_END}
                },
                "sample_kind": "numeric",
            },
        )
        self.assertEqual(resp.status_code, 404)

    def test_sample_kind_omitted_returns_422_with_ambiguous_error(self) -> None:
        """Omitting sample_kind defaults to 'auto' → SAMPLE_KIND_AMBIGUOUS → 422."""
        resp = self.client.post(
            f"/sessions/{self.session_id}/intents/validate",
            json={
                "metric": _metric_ref(_METRIC_NUMERIC),
                "left": {
                    "time_scope": {"kind": "range", "start": _WINDOW_A_START, "end": _WINDOW_A_END}
                },
                "right": {
                    "time_scope": {"kind": "range", "start": _WINDOW_B_START, "end": _WINDOW_B_END}
                },
                # sample_kind omitted → defaults to "auto" → SAMPLE_KIND_AMBIGUOUS → 422
            },
        )
        self.assertEqual(resp.status_code, 422)
        self.assertIn("SAMPLE_KIND_AMBIGUOUS", resp.json()["detail"])
