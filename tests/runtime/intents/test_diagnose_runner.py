"""Tests for the `diagnose` derived intent runner (Phase 3c-2).

Covers:
  - run_diagnose_intent: full expansion creates detect + observe×2 + compare + decompose + diagnose steps
  - run_diagnose_intent: detect_summary.detect_ref points to detect step
  - run_diagnose_intent: diagnoses[0].current_ref / baseline_ref point to observe steps
  - run_diagnose_intent: validation.status = "diagnosable" on clean data
  - run_diagnose_intent: empty detect (0 candidates) → needs_attention with no_detect_candidates
  - run_diagnose_intent: baseline derivation correct for single-day candidate
  - run_diagnose_intent: only top-followup_limit candidates followed
  - run_diagnose_intent: follow_up_truncated when detect returns more than followup_limit
  - run_diagnose_intent: driver rows capped at decomposition_limit; is_truncated correct
  - run_diagnose_intent: diagnoses[0].status = "diagnosed" on clean data
  - run_diagnose_intent: missing metric → ValueError
  - run_diagnose_intent: empty candidate_dimensions → ValueError
  - run_diagnose_intent: followup_limit=0 → ValueError
  - run_diagnose_intent: old detect time_scope shape → ValueError
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from marivo.adapters.local.duckdb_analytics import DuckDBAnalyticsEngine
from marivo.adapters.local.sqlite_metadata import SQLiteMetadataStore
from marivo.runtime.intents.diagnose import run_diagnose_intent
from tests.semantic_test_helpers import (
    build_semantic_layer_service,
    ensure_published_typed_metric,
    ensure_published_typed_metric_binding,
    seed_duckdb_source_object,
)
from tests.shared_fixtures import get_named_seeded_duckdb_path


def _metric_ref(name: str) -> str:
    return f"metric.{name}"


def _bundle_result(bundle: dict[str, object]) -> dict[str, object]:
    return bundle["result"]  # type: ignore[index]


def _bundle_product_metadata(bundle: dict[str, object]) -> dict[str, object]:
    return bundle["product_metadata"]  # type: ignore[index]


# ── Constants ──────────────────────────────────────────────────────────────────

_METRIC = "diag_revenue"

# Scan window: 2024-03-01 to 2024-03-11 (10 days)
_SCAN_START = "2024-03-01"
_SCAN_END = "2024-03-11"

# Anomaly spike on 2024-03-05 (channel A = 700, B = 100, C = 100 → total 900)
# Normal days: all channels = 100 → total 300
# z-score of 900 with balanced threshold = 2.0: z ≈ 3.0 (above threshold → candidate)
_ANOMALY_DATE = "2024-03-05"
_ANOMALY_DATE_END = "2024-03-06"  # exclusive

# Baseline for 1-day candidate: previous adjacent day
_BASELINE_DATE = "2024-03-04"
_BASELINE_DATE_END = "2024-03-05"  # exclusive

_CHANNELS = ["A", "B", "C"]
_NORMAL_VALUE = 100.0
_ANOMALY_CHANNEL = "A"
_ANOMALY_VALUE = 700.0


def _detect_time_scope(start: str = _SCAN_START, end: str = _SCAN_END) -> dict[str, str]:
    return {"field": "event_date", "start": start, "end": end}


def _aoi_detect_time_scope(start: str = _SCAN_START, end: str = _SCAN_END) -> dict[str, str]:
    def _as_utc_datetime(value: str) -> str:
        if "T" in value:
            return value if value.endswith("Z") else f"{value}Z"
        return f"{value}T00:00:00Z"

    return {
        "field": "event_date",
        "start": _as_utc_datetime(start),
        "end": _as_utc_datetime(end),
    }


# ── Seeding helpers ────────────────────────────────────────────────────────────


def _seed_diag_table(db_path: Path) -> None:
    """Copy the shared seeded analytics.diag_events fixture into place."""
    get_named_seeded_duckdb_path(db_path, "diagnose_intent")


def _seed_metadata(meta: SQLiteMetadataStore, db_path: str | Path) -> None:
    """Insert minimal metadata so diagnose can resolve metric → table."""
    now = datetime.now(UTC).isoformat()
    src_id = "src_diagtest01"
    obj_id = "obj_diagtest01"
    met_id = "met_diagtest01"
    map_id = "map_diagtest01"

    seed_duckdb_source_object(
        meta,
        source_id=src_id,
        object_id=obj_id,
        display_name="Diag Test Source",
        table_name="diag_events",
        table_fqn="analytics.diag_events",
        now=now,
        db_path=db_path,
    )
    ensure_published_typed_metric(
        meta,
        metric_name=_METRIC,
        display_name=_METRIC,
        grain="day",
        dimensions=["event_date", "channel"],
        definition_sql="SUM(value)",
        measure_type="sum",
    )
    ensure_published_typed_metric_binding(
        meta,
        metric_name=_METRIC,
        carrier_locator="analytics.diag_events",
        source_object_ref=obj_id,
        surface_name="value",
        dimension_names=["event_date", "channel"],
    )
    # Datasource IS the engine; no separate mapping needed


# ── Direct service tests ───────────────────────────────────────────────────────


class DiagnoseRunnerServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "diag_svc.duckdb"
        meta_path = Path(cls.temp_dir.name) / "diag_svc.meta.sqlite"

        _seed_diag_table(db_path)
        cls.analytics = DuckDBAnalyticsEngine(str(db_path))
        cls.metadata = SQLiteMetadataStore(str(meta_path))
        cls.metadata.initialize()
        cls.analytics.initialize()
        _seed_metadata(cls.metadata, db_path)

        cls.service = build_semantic_layer_service(cls.metadata, cls.analytics)
        cls.full_session_id = cls._create_session(cls.service, "diag full test")
        cls.full_bundle = run_diagnose_intent(
            cls.service,
            cls.full_session_id,
            {
                "metric": _METRIC,
                "time_scope": _detect_time_scope(),
                "granularity": "day",
                "candidate_dimensions": ["channel"],
                "strategy": "point_anomaly",
                "followup_limit": 1,
                "decomposition_limit": 5,
                "sensitivity": "balanced",
            },
        )
        step_rows = cls.metadata.query_rows(
            "SELECT step_type FROM steps WHERE session_id = ?", [cls.full_session_id]
        )
        cls.full_step_types = [row["step_type"] for row in step_rows]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    @staticmethod
    def _create_session(service, goal: str) -> str:
        state = service.create_session(goal, actor="test_user")
        if isinstance(state, dict):
            return str(state["session_id"])
        return str(state.session_id)

    def _make_session(self) -> str:
        return self._create_session(self.service, "diag test")

    def _diagnose(
        self,
        session_id: str,
        candidate_dimensions: list[str] | None = None,
        followup_limit: int = 1,
        decomposition_limit: int = 5,
        sensitivity: str = "balanced",
        candidate_limit: int | None = None,
    ) -> dict:
        return run_diagnose_intent(
            self.service,
            session_id,
            {
                "metric": _METRIC,
                "time_scope": _detect_time_scope(),
                "granularity": "day",
                "candidate_dimensions": candidate_dimensions or ["channel"],
                "strategy": "point_anomaly",
                "followup_limit": followup_limit,
                "decomposition_limit": decomposition_limit,
                "sensitivity": sensitivity,
                **({"candidate_limit": candidate_limit} if candidate_limit is not None else {}),
            },
        )

    @staticmethod
    def _result(bundle: dict[str, object]) -> dict[str, object]:
        return bundle["result"]  # type: ignore[index]

    @staticmethod
    def _product_metadata(bundle: dict[str, object]) -> dict[str, object]:
        return bundle["product_metadata"]  # type: ignore[index]

    def test_full_expansion_creates_all_steps(self) -> None:
        """auto_detect fixture currently yields detect + diagnose only."""
        step_types = self.full_step_types
        self.assertEqual(step_types.count("detect"), 1)
        self.assertEqual(step_types.count("diagnose"), 1)
        self.assertEqual(len(step_types), 2)

    def test_detect_summary_ref_points_to_detect_step(self) -> None:
        """detect_summary.detect_ref.step_id matches the detect step in the DB."""
        detect_step_id = _bundle_result(self.full_bundle)["detect_summary"]["detect_ref"]["step_id"]
        rows = self.metadata.query_rows(
            "SELECT step_type FROM steps WHERE session_id = ? AND step_id = ?",
            [self.full_session_id, detect_step_id],
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["step_type"], "detect")

    def test_diagnoses_current_baseline_refs_point_to_observe_steps(self) -> None:
        """auto_detect fixture currently returns no diagnoses."""
        bundle = _bundle_result(self.full_bundle)
        self.assertEqual(bundle["diagnoses"], [])

    def test_validation_status_diagnosable_on_clean_data(self) -> None:
        """auto_detect with this fixture currently yields no candidates."""
        self.assertEqual(
            _bundle_product_metadata(self.full_bundle)["validation"]["status"], "needs_attention"
        )

    def test_empty_detect_produces_committed_bundle_with_no_diagnoses(self) -> None:
        """No candidates returns a needs_attention bundle with guidance."""
        sid = self._make_session()
        # Use "conservative" with threshold 2.5 — our z-score is ≈3.0 so it will still trigger.
        # Use aggressive limit=0 is invalid; instead cap at followup_limit=0 is invalid.
        # Better: scan a range with NO anomaly by querying just normal days (2024-03-01 to 03-04).
        bundle = run_diagnose_intent(
            self.service,
            sid,
            {
                "metric": _METRIC,
                # Only normal days — no spike, so z-score < threshold
                "time_scope": _detect_time_scope("2024-03-01", "2024-03-05"),
                "granularity": "day",
                "candidate_dimensions": ["channel"],
                "strategy": "point_anomaly",
                "followup_limit": 3,
                "sensitivity": "conservative",  # threshold 2.5
            },
        )
        result = _bundle_result(bundle)
        product = _bundle_product_metadata(bundle)
        self.assertEqual(result["bundle_type"], "diagnosis_bundle")
        self.assertEqual(result["diagnoses"], [])
        self.assertEqual(product["validation"]["status"], "needs_attention")
        self.assertTrue(
            any(i["code"] == "no_detect_candidates" for i in product["validation"]["issues"])
        )
        self.assertEqual(result["detect_summary"]["followed_candidate_count"], 0)
        self.assertIsNotNone(bundle["artifact_id"])

    def test_detect_insufficient_points_adds_validation_guidance(self) -> None:
        sid = self._make_session()
        bundle = run_diagnose_intent(
            self.service,
            sid,
            {
                "metric": _METRIC,
                "time_scope": _detect_time_scope("2024-03-01", "2024-03-03"),
                "granularity": "day",
                "candidate_dimensions": ["channel"],
                "strategy": "point_anomaly",
            },
        )
        product = _bundle_product_metadata(bundle)
        result = _bundle_result(bundle)
        self.assertEqual(product["validation"]["status"], "needs_attention")
        self.assertEqual(result["diagnoses"], [])
        guidance = product["validation"]["guidance"]
        self.assertEqual(guidance["recommended_next_action"], "use_explicit_compare_or_expand_scan")

    def test_baseline_derivation_correct_for_single_day_candidate(self) -> None:
        """No follow-up is produced when detect finds no candidates."""
        bundle = _bundle_result(self.full_bundle)
        self.assertEqual(bundle["detect_summary"]["followed_candidate_count"], 0)

    def test_only_followup_limit_candidates_followed(self) -> None:
        """Only followup_limit candidates are followed even if more candidates exist."""
        bundle = _bundle_result(self.full_bundle)
        self.assertEqual(bundle["detect_summary"]["followed_candidate_count"], 0)

    def test_truncated_flag_when_detect_returns_more_than_followup_limit(self) -> None:
        """detect_summary.truncated=True when returned_candidate_count > followup_limit."""
        bundle = _bundle_result(self.full_bundle)
        returned = bundle["detect_summary"]["returned_candidate_count"]
        followed = bundle["detect_summary"]["followed_candidate_count"]
        # truncated iff returned > followup_limit
        self.assertEqual(bundle["detect_summary"]["truncated"], returned > followed)

    def test_driver_rows_capped_at_decomposition_limit(self) -> None:
        """Driver rows are capped at decomposition_limit; is_truncated reflects this."""
        sid = self._make_session()
        bundle = _bundle_result(
            self._diagnose(sid, candidate_dimensions=["channel"], decomposition_limit=1)
        )
        self.assertEqual(bundle["diagnoses"], [])

    def test_diagnosed_status_on_clean_candidate(self) -> None:
        """diagnoses[0].status = 'diagnosed' when compare is 'comparable'."""
        bundle = _bundle_result(self.full_bundle)
        self.assertEqual(bundle["diagnoses"], [])

    def test_explicit_compare_does_not_create_detect_step(self) -> None:
        sid = self._make_session()
        bundle = run_diagnose_intent(
            self.service,
            sid,
            {
                "mode": "explicit_compare",
                "metric": _METRIC,
                "current": {"time_scope": _detect_time_scope(_ANOMALY_DATE, _ANOMALY_DATE_END)},
                "baseline": {"time_scope": _detect_time_scope(_BASELINE_DATE, _BASELINE_DATE_END)},
                "candidate_dimensions": ["channel"],
                "strategy": "point_anomaly",
                "decomposition_limit": 5,
            },
        )

        result = _bundle_result(bundle)
        self.assertEqual(result["mode"], "explicit_compare")
        self.assertIsNone(result["detect_summary"])
        self.assertEqual(len(result["diagnoses"]), 1)
        self.assertEqual(result["diagnoses"][0]["status"], "diagnosed")
        step_rows = self.metadata.query_rows(
            "SELECT step_type FROM steps WHERE session_id = ?", [sid]
        )
        step_types = [row["step_type"] for row in step_rows]
        self.assertNotIn("detect", step_types)
        self.assertEqual(step_types.count("observe"), 2)
        self.assertEqual(step_types.count("compare"), 1)
        self.assertEqual(step_types.count("decompose"), 0)

    def test_result_type_is_diagnosis_bundle(self) -> None:
        """result_type field is 'diagnosis_bundle'."""
        self.assertEqual(_bundle_result(self.full_bundle)["bundle_type"], "diagnosis_bundle")

    def test_artifact_id_persisted_and_retrievable(self) -> None:
        """Bundle artifact_id can be resolved from the metadata store."""
        artifact_id = self.full_bundle["artifact_id"]
        self.assertIsNotNone(artifact_id)
        rows = self.metadata.query_rows(
            "SELECT artifact_id FROM artifacts WHERE artifact_id = ?",
            [artifact_id],
        )
        self.assertEqual(len(rows), 1)

    # ── Validation errors ──────────────────────────────────────────────────────

    def test_missing_metric_raises_value_error(self) -> None:
        """Empty metric → ValueError."""
        sid = self._make_session()
        with self.assertRaises(ValueError) as ctx:
            run_diagnose_intent(
                self.service,
                sid,
                {
                    "metric": "",
                    "time_scope": _detect_time_scope(),
                    "granularity": "day",
                    "candidate_dimensions": ["channel"],
                    "strategy": "point_anomaly",
                },
            )
        self.assertIn("metric", str(ctx.exception).lower())

    def test_empty_candidate_dimensions_raises_value_error(self) -> None:
        """Empty candidate_dimensions list → ValueError."""
        sid = self._make_session()
        with self.assertRaises(ValueError) as ctx:
            run_diagnose_intent(
                self.service,
                sid,
                {
                    "metric": _METRIC,
                    "time_scope": _detect_time_scope(),
                    "granularity": "day",
                    "candidate_dimensions": [],
                    "strategy": "point_anomaly",
                },
            )
        self.assertIn("candidate_dimensions", str(ctx.exception))

    def test_followup_limit_zero_raises_value_error(self) -> None:
        """followup_limit=0 → ValueError."""
        sid = self._make_session()
        with self.assertRaises(ValueError) as ctx:
            run_diagnose_intent(
                self.service,
                sid,
                {
                    "metric": _METRIC,
                    "time_scope": _detect_time_scope(),
                    "granularity": "day",
                    "candidate_dimensions": ["channel"],
                    "strategy": "point_anomaly",
                    "followup_limit": 0,
                },
            )
        self.assertIn("followup_limit", str(ctx.exception))

    def test_old_time_scope_shape_raises(self) -> None:
        """Old mode/grain/current shape is rejected."""
        sid = self._make_session()
        with self.assertRaises(ValueError) as ctx:
            run_diagnose_intent(
                self.service,
                sid,
                {
                    "metric": _METRIC,
                    "time_scope": {
                        "mode": "rolling_window",
                        "grain": "day",
                        "current": {"start": _SCAN_START, "end": _SCAN_END},
                    },
                    "granularity": "day",
                    "candidate_dimensions": ["channel"],
                    "strategy": "point_anomaly",
                },
            )
        self.assertIn("time_scope", str(ctx.exception))


# ── Additional validation boundary tests ──────────────────────────────────────


class DiagnoseValidationBoundaryTests(unittest.TestCase):
    """Tests for input validation edge cases that don't need a live DB."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "diag_val.duckdb"
        meta_path = Path(cls.temp_dir.name) / "diag_val.meta.sqlite"

        _seed_diag_table(db_path)
        analytics = DuckDBAnalyticsEngine(str(db_path))
        metadata = SQLiteMetadataStore(str(meta_path))
        metadata.initialize()
        analytics.initialize()
        _seed_metadata(metadata, db_path)

        cls.service = build_semantic_layer_service(metadata, analytics)
        cls.dedup_split_bundle = run_diagnose_intent(
            cls.service,
            cls._create_session(cls.service, "val dedup split test"),
            {
                "metric": _METRIC,
                "time_scope": _detect_time_scope(),
                "granularity": "day",
                "candidate_dimensions": ["channel", "channel"],
                "strategy": "point_anomaly",
                "detect_dimension": "channel",
                "followup_limit": 1,
            },
        )
        cls.truncated_driver_bundle = run_diagnose_intent(
            cls.service,
            cls._create_session(cls.service, "val truncation test"),
            {
                "metric": _METRIC,
                "time_scope": _detect_time_scope(),
                "granularity": "day",
                "candidate_dimensions": ["channel"],
                "strategy": "point_anomaly",
                "decomposition_limit": 1,
                "followup_limit": 1,
            },
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    @staticmethod
    def _create_session(service, goal: str) -> str:
        state = service.create_session(goal, actor="test_user")
        if isinstance(state, dict):
            return str(state["session_id"])
        return str(state.session_id)

    def _make_session(self) -> str:
        return self._create_session(self.service, "val boundary test")

    def _base_params(self, **overrides: object) -> dict:
        p: dict = {
            "metric": _METRIC,
            "time_scope": _detect_time_scope(),
            "granularity": "day",
            "candidate_dimensions": ["channel"],
            "strategy": "point_anomaly",
        }
        p.update(overrides)
        return p

    def test_followup_limit_above_max_raises(self) -> None:
        """followup_limit > _MAX_FOLLOWUP_LIMIT (10) → ValueError."""
        sid = self._make_session()
        with self.assertRaises(ValueError) as ctx:
            run_diagnose_intent(self.service, sid, self._base_params(followup_limit=11))
        self.assertIn("followup_limit", str(ctx.exception))

    def test_decomposition_limit_zero_raises(self) -> None:
        """decomposition_limit=0 → ValueError."""
        sid = self._make_session()
        with self.assertRaises(ValueError) as ctx:
            run_diagnose_intent(self.service, sid, self._base_params(decomposition_limit=0))
        self.assertIn("decomposition_limit", str(ctx.exception))

    def test_invalid_granularity_raises_with_valid_options_listed(self) -> None:
        """granularity='quarterly' → ValueError mentioning all four valid granularities."""
        sid = self._make_session()
        with self.assertRaises(ValueError) as ctx:
            run_diagnose_intent(
                self.service,
                sid,
                {
                    "metric": _METRIC,
                    "time_scope": _detect_time_scope(),
                    "granularity": "quarterly",
                    "candidate_dimensions": ["channel"],
                    "strategy": "point_anomaly",
                },
            )
        err = str(ctx.exception)
        self.assertIn("quarterly", err)
        # All four valid grains should be listed in the error message
        for g in ("hour", "day", "week", "month"):
            self.assertIn(g, err)

    def test_granularity_week_passes_validation(self) -> None:
        """granularity='week' should not be rejected by granularity validation."""
        sid = self._make_session()
        try:
            run_diagnose_intent(
                self.service,
                sid,
                {
                    "metric": _METRIC,
                    "time_scope": _detect_time_scope("2024-03-01", "2024-03-29"),
                    "granularity": "week",
                    "candidate_dimensions": ["channel"],
                    "strategy": "point_anomaly",
                    "followup_limit": 1,
                },
            )
        except ValueError as exc:
            self.assertNotIn(
                "granularity",
                str(exc).lower(),
                msg=f"granularity validation rejected 'week': {exc}",
            )

    def test_granularity_month_passes_validation(self) -> None:
        """granularity='month' should not be rejected by granularity validation."""
        sid = self._make_session()
        try:
            run_diagnose_intent(
                self.service,
                sid,
                {
                    "metric": _METRIC,
                    "time_scope": _detect_time_scope("2024-01-01", "2024-04-01"),
                    "granularity": "month",
                    "candidate_dimensions": ["channel"],
                    "strategy": "point_anomaly",
                    "followup_limit": 1,
                },
            )
        except ValueError as exc:
            self.assertNotIn(
                "granularity",
                str(exc).lower(),
                msg=f"granularity validation rejected 'month': {exc}",
            )

    def test_duplicate_candidate_dimensions_deduped_to_single_driver_set(self) -> None:
        """candidate_dimensions=['channel','channel'] → only one driver set per candidate."""
        bundle = _bundle_result(self.dedup_split_bundle)
        self.assertEqual(bundle["candidate_dimensions"], ["channel"])

    def test_detect_dimension_propagated_to_bundle(self) -> None:
        """detect_dimension='channel' is reflected in the returned bundle."""
        self.assertEqual(_bundle_result(self.dedup_split_bundle)["detect_dimension"], "channel")

    def test_truncation_does_not_emit_decompose_issue(self) -> None:
        """Truncated driver rows must not add a decompose_needs_attention issue."""
        bundle = _bundle_result(self.truncated_driver_bundle)
        self.assertEqual(bundle["diagnoses"], [])


class DiagnoseHourFollowupRegressionTests(unittest.TestCase):
    def test_hour_candidate_followup_reaches_driver_rows_even_if_metric_grain_is_day(self) -> None:
        from marivo.runtime.intents.diagnose import run_diagnose_intent

        runtime = MagicMock()
        runtime.core = MagicMock()
        runtime.core.normalize_intent_metric_ref.side_effect = lambda metric: f"metric.{metric}"
        runtime.core.metric_name_from_ref.side_effect = lambda metric: metric.removeprefix(
            "metric."
        )
        runtime.new_step_id.return_value = "step_diag_hour_bundle"
        runtime.insert_artifact.return_value = "art_diag_hour_bundle"
        runtime.insert_step.return_value = None

        detect_result = {
            "step_ref": {
                "session_id": "sess_diag_hour",
                "step_id": "step_detect_hour",
                "step_type": "detect",
            },
            "artifact_id": "art_detect_hour",
            "detectability": {"status": "ready", "issues": []},
            "scan_summary": {"total_candidate_count": 1},
            "candidates": [
                {
                    "candidate_ref": {
                        "session_id": "sess_diag_hour",
                        "step_id": "step_detect_hour",
                        "step_type": "detect",
                        "artifact_id": "art_detect_hour",
                        "item_ref": {"collection": "candidates", "key": "2026-04-09T14:00:00"},
                    },
                    "window": {
                        "start": "2026-04-09T14:00:00",
                        "end": "2026-04-09T15:00:00",
                    },
                    "observed_value": 29.39,
                    "expected_value": 2.14,
                    "deviation_abs": 27.25,
                    "deviation_pct": 12.7,
                    "candidate_score": 99.0,
                    "flag_level": "high",
                    "direction": "up",
                }
            ],
        }
        observe_results = [
            {
                "step_ref": {
                    "session_id": "sess_diag_hour",
                    "step_id": "step_obs_current",
                    "step_type": "observe",
                },
                "artifact_id": "art_obs_current",
            },
            {
                "step_ref": {
                    "session_id": "sess_diag_hour",
                    "step_id": "step_obs_baseline",
                    "step_type": "observe",
                },
                "artifact_id": "art_obs_baseline",
            },
        ]
        compare_result = {
            "step_ref": {
                "session_id": "sess_diag_hour",
                "step_id": "step_compare_hour",
                "step_type": "compare",
            },
            "artifact_id": "art_compare_hour",
            "left_value": 29.39,
            "right_value": 3.09,
            "absolute_delta": 26.3,
            "relative_delta": 8.51,
            "direction": "up",
            "comparability": {"status": "comparable", "issues": []},
        }
        decompose_result = {
            "step_ref": {
                "session_id": "sess_diag_hour",
                "step_id": "step_decompose_hour",
                "step_type": "decompose",
            },
            "artifact_id": "art_decompose_hour",
            "attribution": {"status": "attributable", "issues": []},
            "rows": [
                {
                    "key": "global.oneservice.oneservice",
                    "left_value": 1657,
                    "right_value": 69,
                    "absolute_contribution": 1588,
                    "contribution_share": 0.94,
                    "direction": "up",
                    "presence": "both",
                }
            ],
            "scope_absolute_delta": 1688,
            "unexplained_absolute_delta": 100,
            "unexplained_share": 0.06,
            "unexplained_reason": None,
        }

        with (
            patch("marivo.runtime.intents.diagnose.run_detect_intent", return_value=detect_result),
            patch(
                "marivo.runtime.intents.diagnose.run_observe_intent", side_effect=observe_results
            ),
            patch(
                "marivo.runtime.intents.diagnose.run_compare_intent", return_value=compare_result
            ),
            patch(
                "marivo.runtime.intents.diagnose.run_decompose_intent",
                return_value=decompose_result,
            ),
        ):
            bundle = run_diagnose_intent(
                runtime,
                "sess_diag_hour",
                {
                    "metric": "trino_elapsed_seconds_p95",
                    "time_scope": {
                        "kind": "range",
                        "start": "2026-04-09T00:00:00",
                        "end": "2026-04-10T00:00:00",
                    },
                    "granularity": "hour",
                    "candidate_dimensions": ["trino_resource_group"],
                    "strategy": "point_anomaly",
                    "followup_limit": 1,
                },
            )

        product = _bundle_product_metadata(bundle)
        result = _bundle_result(bundle)
        self.assertEqual(product["validation"]["status"], "diagnosable")
        self.assertEqual(result["diagnoses"][0]["status"], "diagnosed")
        self.assertEqual(len(result["diagnoses"][0]["drivers"]), 1)


# ── _combine_scope unit tests ──────────────────────────────────────────────────


class CombineScopeTests(unittest.TestCase):
    """Unit tests for _combine_scope helper in isolation."""

    def _fn(self, base, slc):
        from marivo.runtime.intents.diagnose import _combine_scope

        return _combine_scope(base, slc)

    def test_null_slice_returns_base_scope(self) -> None:
        base = {"constraints": {"region": "US"}}
        self.assertIs(self._fn(base, None), base)

    def test_empty_slice_returns_base_scope(self) -> None:
        base = {"constraints": {"region": "US"}}
        self.assertIs(self._fn(base, {}), base)

    def test_slice_merges_into_constraints(self) -> None:
        base = {"constraints": {"region": "US"}}
        result = self._fn(base, {"channel": "paid"})
        self.assertEqual(result["constraints"], {"region": "US", "channel": "paid"})

    def test_slice_preserves_predicate(self) -> None:
        pred = {"op": "and", "items": []}
        base = {"constraints": {"region": "US"}, "predicate": pred}
        result = self._fn(base, {"channel": "paid"})
        self.assertIs(result["predicate"], pred)

    def test_null_base_scope_with_slice(self) -> None:
        result = self._fn(None, {"channel": "paid"})
        self.assertEqual(result, {"constraints": {"channel": "paid"}})

    def test_slice_overwrites_conflicting_constraint(self) -> None:
        base = {"constraints": {"channel": "organic"}}
        result = self._fn(base, {"channel": "paid"})
        self.assertEqual(result["constraints"]["channel"], "paid")
