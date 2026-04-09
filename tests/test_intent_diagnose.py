"""Tests for the `diagnose` derived intent runner (Phase 3c-2).

Covers:
  - run_diagnose_intent: full expansion creates detect + observe×2 + compare + decompose + diagnose steps
  - run_diagnose_intent: detect_summary.detect_ref points to detect step
  - run_diagnose_intent: diagnoses[0].current_ref / baseline_ref point to observe steps
  - run_diagnose_intent: validation.status = "diagnosable" on clean data
  - run_diagnose_intent: empty detect (0 candidates) → diagnoses=[], bundle still committed
  - run_diagnose_intent: baseline derivation correct for single-day candidate
  - run_diagnose_intent: only top-followup_limit candidates followed
  - run_diagnose_intent: follow_up_truncated when detect returns more than followup_limit
  - run_diagnose_intent: driver rows capped at decomposition_limit; is_truncated correct
  - run_diagnose_intent: diagnoses[0].status = "diagnosed" on clean data
  - run_diagnose_intent: missing metric → ValueError
  - run_diagnose_intent: empty candidate_dimensions → ValueError
  - run_diagnose_intent: followup_limit=0 → ValueError
  - run_diagnose_intent: time_scope.mode != "single_window" → ValueError
  - HTTP endpoint: valid diagnose returns 200 with result_type="diagnosis_bundle"
  - HTTP endpoint: missing candidate_dimensions returns 422
  - HTTP endpoint: unknown session returns 404
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

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


# ── Seeding helpers ────────────────────────────────────────────────────────────


def _seed_diag_table(db_path: Path) -> None:
    """Create analytics.diag_events with a clear anomaly spike on 2024-03-05."""
    con = duckdb.connect(str(db_path))
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS analytics.diag_events (
                event_date DATE    NOT NULL,
                channel    VARCHAR NOT NULL,
                value      DOUBLE  NOT NULL
            )
            """
        )
        rows: list[tuple[str, str, float]] = []
        base = datetime(2024, 3, 1).date()
        for i in range(10):  # 2024-03-01 to 2024-03-10
            d = (base + timedelta(days=i)).isoformat()
            for ch in _CHANNELS:
                # Spike: day 5 (2024-03-05), channel A = 700; everything else = 100
                if d == _ANOMALY_DATE and ch == _ANOMALY_CHANNEL:
                    rows.append((d, ch, _ANOMALY_VALUE))
                else:
                    rows.append((d, ch, _NORMAL_VALUE))
        con.executemany("INSERT INTO analytics.diag_events VALUES (?, ?, ?)", rows)
    finally:
        con.close()


def _seed_metadata(meta: SQLiteMetadataStore) -> None:
    """Insert minimal metadata so diagnose can resolve metric → table."""
    now = datetime.now(UTC).isoformat()
    src_id = "src_diagtest01"
    obj_id = "obj_diagtest01"
    met_id = "met_diagtest01"
    map_id = "map_diagtest01"

    meta.execute(
        "INSERT OR IGNORE INTO sources "
        "(source_id, source_type, display_name, connection_json, capabilities_json, "
        "created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [src_id, "duckdb", "Diag Test Source", "{}", "{}", now, now],
    )
    meta.execute(
        "INSERT OR IGNORE INTO source_objects "
        "(object_id, source_id, object_type, native_name, fqn, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [obj_id, src_id, "table", "diag_events", "analytics.diag_events", now, now],
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


# ── Direct service tests ───────────────────────────────────────────────────────


class DiagnoseRunnerServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "diag_svc.duckdb"
        meta_path = Path(cls.temp_dir.name) / "diag_svc.meta.sqlite"

        cls.analytics = DuckDBAnalyticsEngine(str(db_path))
        cls.metadata = SQLiteMetadataStore(str(meta_path))
        cls.metadata.initialize()
        cls.analytics.initialize()

        _seed_diag_table(db_path)
        _seed_metadata(cls.metadata)

        cls.service = SemanticLayerService(cls.metadata, cls.analytics)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def _make_session(self) -> str:
        return self.service.create_session("diag test", {}, {}, {})["session_id"]

    def _diagnose(
        self,
        session_id: str,
        candidate_dimensions: list[str] | None = None,
        followup_limit: int = 1,
        decomposition_limit: int = 5,
        sensitivity: str = "balanced",
        candidate_limit: int | None = None,
    ) -> dict:
        return self.service.run_intent(
            session_id,
            "diagnose",
            {
                "metric": _METRIC,
                "time_scope": {
                    "mode": "single_window",
                    "grain": "day",
                    "current": {"start": _SCAN_START, "end": _SCAN_END},
                },
                "candidate_dimensions": candidate_dimensions or ["channel"],
                "followup_limit": followup_limit,
                "decomposition_limit": decomposition_limit,
                "sensitivity": sensitivity,
                **({"candidate_limit": candidate_limit} if candidate_limit is not None else {}),
            },
        )

    def test_full_expansion_creates_all_steps(self) -> None:
        """diagnose with 1 dimension + 1 candidate creates detect+obs×2+compare+decompose+diagnose."""
        sid = self._make_session()
        self._diagnose(sid, candidate_dimensions=["channel"], followup_limit=1)
        rows = self.metadata.query_rows("SELECT step_type FROM steps WHERE session_id = ?", [sid])
        step_types = [r["step_type"] for r in rows]
        self.assertEqual(step_types.count("detect"), 1)
        self.assertEqual(step_types.count("observe"), 2)
        self.assertEqual(step_types.count("compare"), 1)
        self.assertEqual(step_types.count("decompose"), 1)
        self.assertEqual(step_types.count("diagnose"), 1)
        self.assertEqual(len(step_types), 6)

    def test_detect_summary_ref_points_to_detect_step(self) -> None:
        """detect_summary.detect_ref.step_id matches the detect step in the DB."""
        sid = self._make_session()
        bundle = self._diagnose(sid)
        detect_step_id = bundle["detect_summary"]["detect_ref"]["step_id"]
        rows = self.metadata.query_rows(
            "SELECT step_type FROM steps WHERE session_id = ? AND step_id = ?",
            [sid, detect_step_id],
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["step_type"], "detect")

    def test_diagnoses_current_baseline_refs_point_to_observe_steps(self) -> None:
        """diagnoses[0].current_ref and baseline_ref each point to an observe step."""
        sid = self._make_session()
        bundle = self._diagnose(sid)
        self.assertGreater(len(bundle["diagnoses"]), 0, "Expected at least one followed candidate")
        cand_result = bundle["diagnoses"][0]
        current_ref = cand_result["current_ref"]
        baseline_ref = cand_result["baseline_ref"]
        self.assertIsNotNone(current_ref, "current_ref should not be None")
        self.assertIsNotNone(baseline_ref, "baseline_ref should not be None")

        for ref, label in ((current_ref, "current"), (baseline_ref, "baseline")):
            rows = self.metadata.query_rows(
                "SELECT step_type FROM steps WHERE session_id = ? AND step_id = ?",
                [sid, ref["step_id"]],
            )
            self.assertEqual(len(rows), 1, f"{label}_ref step not found in DB")
            self.assertEqual(rows[0]["step_type"], "observe", f"{label}_ref should be observe step")

    def test_validation_status_diagnosable_on_clean_data(self) -> None:
        """validation.status is 'diagnosable' when detect and follow-up succeed."""
        sid = self._make_session()
        bundle = self._diagnose(sid)
        self.assertEqual(bundle["validation"]["status"], "diagnosable")

    def test_empty_detect_produces_committed_bundle_with_no_diagnoses(self) -> None:
        """High sensitivity threshold → no candidates → diagnoses=[], still a valid bundle."""
        sid = self._make_session()
        # Use "conservative" with threshold 2.5 — our z-score is ≈3.0 so it will still trigger.
        # Use aggressive limit=0 is invalid; instead cap at followup_limit=0 is invalid.
        # Better: scan a range with NO anomaly by querying just normal days (2024-03-01 to 03-04).
        bundle = self.service.run_intent(
            sid,
            "diagnose",
            {
                "metric": _METRIC,
                "time_scope": {
                    "mode": "single_window",
                    "grain": "day",
                    # Only normal days — no spike, so z-score < threshold
                    "current": {"start": "2024-03-01", "end": "2024-03-05"},
                },
                "candidate_dimensions": ["channel"],
                "followup_limit": 3,
                "sensitivity": "conservative",  # threshold 2.5
            },
        )
        self.assertEqual(bundle["result_type"], "diagnosis_bundle")
        self.assertEqual(bundle["diagnoses"], [])
        self.assertEqual(bundle["detect_summary"]["followed_candidate_count"], 0)
        self.assertIsNotNone(bundle["artifact_id"])

    def test_baseline_derivation_correct_for_single_day_candidate(self) -> None:
        """baseline_window = previous adjacent equal-length day for a 1-day candidate."""
        sid = self._make_session()
        bundle = self._diagnose(sid)
        self.assertGreater(len(bundle["diagnoses"]), 0)
        cand = bundle["diagnoses"][0]
        derivation = cand["baseline_derivation"]
        self.assertEqual(derivation["policy"], "previous_adjacent_equal_length")
        self.assertIsNotNone(derivation["baseline_window"])
        # Anomaly day is 2024-03-05; baseline should be 2024-03-04
        bw = derivation["baseline_window"]
        self.assertEqual(bw["start"], _BASELINE_DATE)
        self.assertEqual(bw["end"], _BASELINE_DATE_END)

    def test_only_followup_limit_candidates_followed(self) -> None:
        """Only followup_limit candidates are followed even if more candidates exist."""
        sid = self._make_session()
        # Use aggressive sensitivity to potentially get more candidates, but cap followup at 1
        bundle = self.service.run_intent(
            sid,
            "diagnose",
            {
                "metric": _METRIC,
                "time_scope": {
                    "mode": "single_window",
                    "grain": "day",
                    "current": {"start": _SCAN_START, "end": _SCAN_END},
                },
                "candidate_dimensions": ["channel"],
                "followup_limit": 1,
                "sensitivity": "aggressive",  # threshold 1.5, may find more candidates
            },
        )
        self.assertLessEqual(
            len(bundle["diagnoses"]),
            1,
            "diagnoses should be capped at followup_limit=1",
        )

    def test_truncated_flag_when_detect_returns_more_than_followup_limit(self) -> None:
        """detect_summary.truncated=True when returned_candidate_count > followup_limit."""
        # Ensure detect returns candidates and followup_limit is smaller
        sid = self._make_session()
        # Aggressive sensitivity may find more candidates; followup_limit=0 is invalid but 1 ok
        bundle = self.service.run_intent(
            sid,
            "diagnose",
            {
                "metric": _METRIC,
                "time_scope": {
                    "mode": "single_window",
                    "grain": "day",
                    "current": {"start": _SCAN_START, "end": _SCAN_END},
                },
                "candidate_dimensions": ["channel"],
                "sensitivity": "aggressive",
                "followup_limit": 1,
            },
        )
        returned = bundle["detect_summary"]["returned_candidate_count"]
        followed = bundle["detect_summary"]["followed_candidate_count"]
        # truncated iff returned > followup_limit
        self.assertEqual(bundle["detect_summary"]["truncated"], returned > followed)

    def test_driver_rows_capped_at_decomposition_limit(self) -> None:
        """Driver rows are capped at decomposition_limit; is_truncated reflects this."""
        sid = self._make_session()
        # decomposition_limit=1, channel has 3 values → truncated
        bundle = self._diagnose(sid, candidate_dimensions=["channel"], decomposition_limit=1)
        self.assertGreater(len(bundle["diagnoses"]), 0)
        cand = bundle["diagnoses"][0]
        self.assertGreater(len(cand["drivers"]), 0)
        driver = cand["drivers"][0]
        self.assertLessEqual(driver["returned_row_count"], 1)
        self.assertTrue(driver["is_truncated"])

    def test_diagnosed_status_on_clean_candidate(self) -> None:
        """diagnoses[0].status = 'diagnosed' when compare is 'comparable'."""
        sid = self._make_session()
        bundle = self._diagnose(sid)
        self.assertGreater(len(bundle["diagnoses"]), 0)
        cand = bundle["diagnoses"][0]
        self.assertEqual(cand["status"], "diagnosed")

    def test_result_type_is_diagnosis_bundle(self) -> None:
        """result_type field is 'diagnosis_bundle'."""
        sid = self._make_session()
        bundle = self._diagnose(sid)
        self.assertEqual(bundle["result_type"], "diagnosis_bundle")

    def test_artifact_id_persisted_and_retrievable(self) -> None:
        """Bundle artifact_id can be resolved from the metadata store."""
        sid = self._make_session()
        bundle = self._diagnose(sid)
        artifact_id = bundle["artifact_id"]
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
            self.service.run_intent(
                sid,
                "diagnose",
                {
                    "metric": "",
                    "time_scope": {
                        "mode": "single_window",
                        "grain": "day",
                        "current": {"start": _SCAN_START, "end": _SCAN_END},
                    },
                    "candidate_dimensions": ["channel"],
                },
            )
        self.assertIn("metric", str(ctx.exception).lower())

    def test_empty_candidate_dimensions_raises_value_error(self) -> None:
        """Empty candidate_dimensions list → ValueError."""
        sid = self._make_session()
        with self.assertRaises(ValueError) as ctx:
            self.service.run_intent(
                sid,
                "diagnose",
                {
                    "metric": _METRIC,
                    "time_scope": {
                        "mode": "single_window",
                        "grain": "day",
                        "current": {"start": _SCAN_START, "end": _SCAN_END},
                    },
                    "candidate_dimensions": [],
                },
            )
        self.assertIn("candidate_dimensions", str(ctx.exception))

    def test_followup_limit_zero_raises_value_error(self) -> None:
        """followup_limit=0 → ValueError."""
        sid = self._make_session()
        with self.assertRaises(ValueError) as ctx:
            self.service.run_intent(
                sid,
                "diagnose",
                {
                    "metric": _METRIC,
                    "time_scope": {
                        "mode": "single_window",
                        "grain": "day",
                        "current": {"start": _SCAN_START, "end": _SCAN_END},
                    },
                    "candidate_dimensions": ["channel"],
                    "followup_limit": 0,
                },
            )
        self.assertIn("followup_limit", str(ctx.exception))

    def test_time_scope_mode_not_single_window_raises(self) -> None:
        """time_scope.mode != 'single_window' → ValueError."""
        sid = self._make_session()
        with self.assertRaises(ValueError) as ctx:
            self.service.run_intent(
                sid,
                "diagnose",
                {
                    "metric": _METRIC,
                    "time_scope": {
                        "mode": "rolling_window",
                        "grain": "day",
                        "current": {"start": _SCAN_START, "end": _SCAN_END},
                    },
                    "candidate_dimensions": ["channel"],
                },
            )
        self.assertIn("single_window", str(ctx.exception))


# ── HTTP endpoint tests ────────────────────────────────────────────────────────


class DiagnoseHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "diag_http.duckdb"
        meta_path = Path(cls.temp_dir.name) / "diag_http.meta.sqlite"

        analytics = DuckDBAnalyticsEngine(str(db_path))
        metadata = SQLiteMetadataStore(str(meta_path))
        metadata.initialize()
        analytics.initialize()

        _seed_diag_table(db_path)
        _seed_metadata(metadata)

        app = create_app(metadata_store=metadata, analytics_engine=analytics)
        cls.client = TestClient(app)

        # Create a session to reuse
        resp = cls.client.post("/sessions", json={"goal": "diag http test"})
        cls.session_id = resp.json()["session_id"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_valid_diagnose_returns_200_with_bundle(self) -> None:
        """Valid diagnose request returns 200 with result_type='diagnosis_bundle'."""
        resp = self.client.post(
            f"/sessions/{self.session_id}/intents/diagnose",
            json={
                "metric": _METRIC,
                "time_scope": {
                    "mode": "single_window",
                    "grain": "day",
                    "current": {"start": _SCAN_START, "end": _SCAN_END},
                },
                "candidate_dimensions": ["channel"],
                "followup_limit": 1,
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["result_type"], "diagnosis_bundle")

    def test_missing_candidate_dimensions_returns_422(self) -> None:
        """Missing required candidate_dimensions returns 422."""
        resp = self.client.post(
            f"/sessions/{self.session_id}/intents/diagnose",
            json={
                "metric": _METRIC,
                "time_scope": {
                    "mode": "single_window",
                    "grain": "day",
                    "current": {"start": _SCAN_START, "end": _SCAN_END},
                },
                # no candidate_dimensions
            },
        )
        self.assertEqual(resp.status_code, 422)

    def test_unknown_session_returns_404(self) -> None:
        """Unknown session returns 404."""
        resp = self.client.post(
            "/sessions/sess_nonexistent/intents/diagnose",
            json={
                "metric": _METRIC,
                "time_scope": {
                    "mode": "single_window",
                    "grain": "day",
                    "current": {"start": _SCAN_START, "end": _SCAN_END},
                },
                "candidate_dimensions": ["channel"],
            },
        )
        self.assertEqual(resp.status_code, 404)


# ── Additional validation boundary tests ──────────────────────────────────────


class DiagnoseValidationBoundaryTests(unittest.TestCase):
    """Tests for input validation edge cases that don't need a live DB."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "diag_val.duckdb"
        meta_path = Path(cls.temp_dir.name) / "diag_val.meta.sqlite"

        analytics = DuckDBAnalyticsEngine(str(db_path))
        metadata = SQLiteMetadataStore(str(meta_path))
        metadata.initialize()
        analytics.initialize()

        _seed_diag_table(db_path)
        _seed_metadata(metadata)

        cls.service = SemanticLayerService(metadata, analytics)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def _make_session(self) -> str:
        return self.service.create_session("val boundary test", {}, {}, {})["session_id"]

    def _base_params(self, **overrides: object) -> dict:
        p: dict = {
            "metric": _METRIC,
            "time_scope": {
                "mode": "single_window",
                "grain": "day",
                "current": {"start": _SCAN_START, "end": _SCAN_END},
            },
            "candidate_dimensions": ["channel"],
        }
        p.update(overrides)
        return p

    def test_followup_limit_above_max_raises(self) -> None:
        """followup_limit > _MAX_FOLLOWUP_LIMIT (10) → ValueError."""
        sid = self._make_session()
        with self.assertRaises(ValueError) as ctx:
            self.service.run_intent(sid, "diagnose", self._base_params(followup_limit=11))
        self.assertIn("followup_limit", str(ctx.exception))

    def test_decomposition_limit_zero_raises(self) -> None:
        """decomposition_limit=0 → ValueError."""
        sid = self._make_session()
        with self.assertRaises(ValueError) as ctx:
            self.service.run_intent(sid, "diagnose", self._base_params(decomposition_limit=0))
        self.assertIn("decomposition_limit", str(ctx.exception))

    def test_invalid_grain_raises_with_valid_options_listed(self) -> None:
        """grain='quarterly' → ValueError mentioning all four valid grains."""
        sid = self._make_session()
        with self.assertRaises(ValueError) as ctx:
            self.service.run_intent(
                sid,
                "diagnose",
                {
                    "metric": _METRIC,
                    "time_scope": {
                        "mode": "single_window",
                        "grain": "quarterly",
                        "current": {"start": _SCAN_START, "end": _SCAN_END},
                    },
                    "candidate_dimensions": ["channel"],
                },
            )
        err = str(ctx.exception)
        self.assertIn("quarterly", err)
        # All four valid grains should be listed in the error message
        for g in ("hour", "day", "week", "month"):
            self.assertIn(g, err)

    def test_grain_week_passes_grain_validation(self) -> None:
        """grain='week' should not be rejected by grain validation (may fail downstream)."""
        sid = self._make_session()
        try:
            self.service.run_intent(
                sid,
                "diagnose",
                {
                    "metric": _METRIC,
                    "time_scope": {
                        "mode": "single_window",
                        "grain": "week",
                        "current": {"start": "2024-03-01", "end": "2024-03-29"},
                    },
                    "candidate_dimensions": ["channel"],
                    "followup_limit": 1,
                },
            )
        except ValueError as exc:
            # Must NOT be the grain validation error
            self.assertNotIn(
                "grain", str(exc).lower(), msg=f"grain validation rejected 'week': {exc}"
            )

    def test_grain_month_passes_grain_validation(self) -> None:
        """grain='month' should not be rejected by grain validation."""
        sid = self._make_session()
        try:
            self.service.run_intent(
                sid,
                "diagnose",
                {
                    "metric": _METRIC,
                    "time_scope": {
                        "mode": "single_window",
                        "grain": "month",
                        "current": {"start": "2024-01-01", "end": "2024-04-01"},
                    },
                    "candidate_dimensions": ["channel"],
                    "followup_limit": 1,
                },
            )
        except ValueError as exc:
            self.assertNotIn(
                "grain", str(exc).lower(), msg=f"grain validation rejected 'month': {exc}"
            )

    def test_duplicate_candidate_dimensions_deduped_to_single_driver_set(self) -> None:
        """candidate_dimensions=['channel','channel'] → only one driver set per candidate."""
        sid = self._make_session()
        bundle = self.service.run_intent(
            sid,
            "diagnose",
            self._base_params(candidate_dimensions=["channel", "channel"], followup_limit=1),
        )
        self.assertEqual(bundle["candidate_dimensions"], ["channel"])
        if bundle["diagnoses"]:
            drivers = bundle["diagnoses"][0]["drivers"]
            dims = [d["dimension"] for d in drivers]
            self.assertEqual(
                dims, ["channel"], "Deduped list should produce exactly one driver set"
            )

    def test_detect_split_by_propagated_to_bundle(self) -> None:
        """detect_split_by='channel' is reflected in the returned bundle."""
        sid = self._make_session()
        bundle = self.service.run_intent(
            sid,
            "diagnose",
            self._base_params(detect_split_by="channel", followup_limit=1),
        )
        self.assertEqual(bundle["detect_split_by"], "channel")

    def test_truncation_does_not_emit_decompose_issue(self) -> None:
        """Truncated driver rows must not add a decompose_needs_attention issue."""
        sid = self._make_session()
        bundle = self.service.run_intent(
            sid,
            "diagnose",
            self._base_params(
                candidate_dimensions=["channel"],
                decomposition_limit=1,
                followup_limit=1,
            ),
        )
        self.assertGreater(len(bundle["diagnoses"]), 0)
        driver = bundle["diagnoses"][0]["drivers"][0]
        self.assertTrue(driver["is_truncated"])
        # No issue should blame truncation
        for iss in driver.get("issues") or []:
            self.assertNotEqual(
                iss.get("code"),
                "decompose_needs_attention",
                msg="Truncation must not emit decompose_needs_attention issue",
            )


# ── _combine_scope unit tests ──────────────────────────────────────────────────


class CombineScopeTests(unittest.TestCase):
    """Unit tests for _combine_scope helper in isolation."""

    def _fn(self, base, slc):
        from app.intents.diagnose import _combine_scope

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
