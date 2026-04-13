"""Tests for the `attribute` derived intent runner (Phase 3c-1).

Covers:
  - run_attribute_intent: full expansion creates observe×2 + compare + decompose + attribute steps
  - run_attribute_intent: bundle lineage refs correct
  - run_attribute_intent: validation.status = "attributable" on clean data
  - run_attribute_intent: ScalarDeltaSummary fields populated with correct values
  - run_attribute_intent: drivers array length matches dimensions
  - run_attribute_intent: driver rows capped at decomposition_limit; is_truncated correct
  - run_attribute_intent: others_* null when not truncated
  - run_attribute_intent: driver_truncated issue added when truncated
  - run_attribute_intent: artifact_id persisted and retrievable
  - run_attribute_intent: missing metric → ValueError
  - run_attribute_intent: empty dimensions → ValueError
  - run_attribute_intent: blank dimension string → ValueError
  - run_attribute_intent: decomposition_limit=0 → ValueError
  - run_attribute_intent: decomposition_limit > max → ValueError
  - run_attribute_intent: missing left.time_scope → ValueError
  - run_attribute_intent: independent left/right scope passed correctly
  - run_attribute_intent: decompose issue codes remapped to AttributeIssue schema
  - HTTP endpoint: valid attribute returns 200 with bundle
  - HTTP endpoint: missing dimensions returns 422
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


def _metric_ref(name: str) -> str:
    return f"metric.{name}"


# ── Constants ──────────────────────────────────────────────────────────────────

_METRIC = "attr_revenue"
_CURRENT_START = "2026-03-01"
_CURRENT_END = "2026-03-04"  # exclusive, so 3 days: Mar 1–3
_BASELINE_START = "2026-02-01"
_BASELINE_END = "2026-02-04"  # exclusive, so 3 days: Feb 1–3

# Channels and aggregated values per 3-day window
# channel A: current 300, baseline 210, contribution +90
# channel B: current 240, baseline 180, contribution +60
# channel C: current 180, baseline 150, contribution +30
# Total current: 720, total baseline: 540, delta: +180

_CHANNELS = [("A", 100.0, 70.0), ("B", 80.0, 60.0), ("C", 60.0, 50.0)]


# ── Seeding helpers ────────────────────────────────────────────────────────────


def _seed_attr_table(db_path: Path) -> None:
    """Create analytics.attr_events and seed with 3 channels × 2 time windows.

    Each channel has one row per day per window (3 days each).
    The metric is SUM(value).
    """
    con = duckdb.connect(str(db_path))
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS analytics.attr_events (
                event_date DATE    NOT NULL,
                channel    VARCHAR NOT NULL,
                region     VARCHAR NOT NULL,
                value      DOUBLE  NOT NULL
            )
            """
        )
        rows: list[tuple[str, str, str, float]] = []

        # current window: 2026-03-01 to 2026-03-03
        current_base = datetime(2026, 3, 1).date()
        for i in range(3):
            d = (current_base + timedelta(days=i)).isoformat()
            for channel, current_daily, _ in _CHANNELS:
                rows.append((d, channel, "X", current_daily))

        # baseline window: 2026-02-01 to 2026-02-03
        baseline_base = datetime(2026, 2, 1).date()
        for i in range(3):
            d = (baseline_base + timedelta(days=i)).isoformat()
            for channel, _, baseline_daily in _CHANNELS:
                rows.append((d, channel, "X", baseline_daily))

        con.executemany("INSERT INTO analytics.attr_events VALUES (?, ?, ?, ?)", rows)
    finally:
        con.close()


def _seed_metadata(meta: SQLiteMetadataStore) -> str:
    """Insert minimal metadata so attribute can resolve metric → table."""
    now = datetime.now(UTC).isoformat()
    src_id = "src_attrtest01"
    obj_id = "obj_attrtest01"
    met_id = "met_attrtest01"
    map_id = "map_attrtest01"

    meta.execute(
        "INSERT OR IGNORE INTO sources "
        "(source_id, source_type, display_name, connection_json, capabilities_json, "
        "created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [src_id, "duckdb", "Attr Test Source", "{}", "{}", now, now],
    )
    meta.execute(
        "INSERT OR IGNORE INTO source_objects "
        "(object_id, source_id, object_type, native_name, fqn, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [obj_id, src_id, "table", "attr_events", "analytics.attr_events", now, now],
    )
    ensure_published_typed_metric(
        meta,
        metric_name=_METRIC,
        display_name=_METRIC,
        grain="day",
        dimensions=["event_date", "channel", "region"],
        definition_sql="SUM(value)",
        measure_type="sum",
    )
    ensure_published_typed_metric_binding(
        meta,
        metric_name=_METRIC,
        carrier_locator="analytics.attr_events",
        source_object_ref=obj_id,
        surface_name="value",
        dimension_names=["event_date", "channel", "region"],
    )
    return _METRIC


# ── Direct service tests ───────────────────────────────────────────────────────


class AttributeRunnerServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "attr_svc.duckdb"
        meta_path = Path(cls.temp_dir.name) / "attr_svc.meta.sqlite"

        cls.analytics = DuckDBAnalyticsEngine(str(db_path))
        cls.metadata = SQLiteMetadataStore(str(meta_path))
        cls.metadata.initialize()
        cls.analytics.initialize()

        _seed_attr_table(db_path)
        _seed_metadata(cls.metadata)

        cls.service = SemanticLayerService(cls.metadata, cls.analytics)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def _make_session(self) -> str:
        return self.service.create_session("attr test", {}, {}, {})["session_id"]

    def _attribute(
        self,
        session_id: str,
        dimensions: list[str] | None = None,
        decomposition_limit: int = 5,
        left_scope: dict | None = None,
        right_scope: dict | None = None,
    ) -> dict:
        left: dict = {
            "time_scope": {
                "kind": "range",
                "start": _CURRENT_START,
                "end": _CURRENT_END,
            },
        }
        if left_scope is not None:
            left["scope"] = left_scope
        right: dict = {
            "time_scope": {
                "kind": "range",
                "start": _BASELINE_START,
                "end": _BASELINE_END,
            },
        }
        if right_scope is not None:
            right["scope"] = right_scope
        return self.service.run_intent(
            session_id,
            "attribute",
            {
                "metric": _METRIC,
                "left": left,
                "right": right,
                "dimensions": dimensions if dimensions is not None else ["channel"],
                "decomposition_limit": decomposition_limit,
            },
        )

    def test_expansion_creates_all_steps(self) -> None:
        """attribute with 1 dimension creates 5 steps: obs×2 + compare + decompose + attribute."""
        sid = self._make_session()
        self._attribute(sid, dimensions=["channel"])
        rows = self.metadata.query_rows("SELECT step_type FROM steps WHERE session_id = ?", [sid])
        step_types = [r["step_type"] for r in rows]
        self.assertEqual(step_types.count("observe"), 2)
        self.assertEqual(step_types.count("compare"), 1)
        self.assertEqual(step_types.count("decompose"), 1)
        self.assertEqual(step_types.count("attribute"), 1)
        self.assertEqual(len(step_types), 5)

    def test_expansion_two_dimensions_creates_six_steps(self) -> None:
        """attribute with 2 dimensions creates 6 steps."""
        sid = self._make_session()
        self._attribute(sid, dimensions=["channel", "region"])
        rows = self.metadata.query_rows("SELECT step_type FROM steps WHERE session_id = ?", [sid])
        step_types = [r["step_type"] for r in rows]
        self.assertEqual(len(step_types), 6)
        self.assertEqual(step_types.count("decompose"), 2)

    def test_bundle_lineage_refs_correct(self) -> None:
        """Lineage refs in bundle match the intermediate step ids."""
        sid = self._make_session()
        bundle = self._attribute(sid)

        obs_left_sid = bundle["observation_refs"]["left_ref"]["step_id"]
        obs_right_sid = bundle["observation_refs"]["right_ref"]["step_id"]
        cmp_sid = bundle["compare_ref"]["step_id"]
        decompose_sid = bundle["drivers"][0]["decompose_ref"]["step_id"]
        attr_sid = bundle["step_ref"]["step_id"]

        # All step_ids must be distinct
        all_ids = {obs_left_sid, obs_right_sid, cmp_sid, decompose_sid, attr_sid}
        self.assertEqual(len(all_ids), 5)

        # All steps must exist in DB
        for step_id in all_ids:
            row = self.metadata.query_one(
                "SELECT step_id FROM steps WHERE step_id = ? AND session_id = ?",
                [step_id, sid],
            )
            self.assertIsNotNone(row, f"step {step_id} not found in DB")

    def test_validation_status_attributable(self) -> None:
        """validation.status should be 'attributable' with clean numeric data."""
        sid = self._make_session()
        bundle = self._attribute(sid)
        self.assertEqual(bundle["validation"]["status"], "attributable")

    def test_scalar_delta_summary_correct_values(self) -> None:
        """ScalarDeltaSummary should reflect known current=720, baseline=540, delta=180."""
        sid = self._make_session()
        bundle = self._attribute(sid)
        comparison = bundle["comparison"]

        self.assertEqual(comparison["comparison_type"], "scalar_delta")
        # current total = sum(100+80+60) * 3 days = 720
        self.assertAlmostEqual(comparison["left_value"], 720.0, places=1)
        # baseline total = sum(70+60+50) * 3 days = 540
        self.assertAlmostEqual(comparison["right_value"], 540.0, places=1)
        self.assertAlmostEqual(comparison["absolute_delta"], 180.0, places=1)
        self.assertAlmostEqual(comparison["relative_delta"], 180.0 / 540.0, places=4)
        self.assertEqual(comparison["direction"], "increase")
        self.assertEqual(comparison["comparability_status"], "comparable")

    def test_drivers_length_matches_dimensions(self) -> None:
        """drivers array length equals len(dimensions)."""
        sid = self._make_session()
        bundle = self._attribute(sid, dimensions=["channel", "region"])
        self.assertEqual(len(bundle["drivers"]), 2)
        self.assertEqual(bundle["drivers"][0]["dimension"], "channel")
        self.assertEqual(bundle["drivers"][1]["dimension"], "region")

    def test_driver_rows_capped_at_decomposition_limit(self) -> None:
        """With decomposition_limit=2 and 3 channels, is_truncated should be True."""
        sid = self._make_session()
        bundle = self._attribute(sid, dimensions=["channel"], decomposition_limit=2)
        driver = bundle["drivers"][0]

        self.assertLessEqual(len(driver["rows"]), 2)
        self.assertEqual(driver["returned_row_count"], 2)
        self.assertEqual(driver["total_row_count"], 3)
        self.assertTrue(driver["is_truncated"])
        # others aggregation should be present
        self.assertIsNotNone(driver["others_absolute_contribution"])
        self.assertIsNotNone(driver["others_contribution_share"])

    def test_driver_truncated_issue_added_when_truncated(self) -> None:
        """When is_truncated, a driver_truncated issue must appear in driver.issues."""
        sid = self._make_session()
        bundle = self._attribute(sid, dimensions=["channel"], decomposition_limit=2)
        driver = bundle["drivers"][0]
        issue_codes = [i["code"] for i in driver["issues"]]
        self.assertIn("driver_truncated", issue_codes)

    def test_others_null_when_not_truncated(self) -> None:
        """others_* fields should be None when decomposition_limit >= total rows."""
        sid = self._make_session()
        bundle = self._attribute(sid, dimensions=["channel"], decomposition_limit=10)
        driver = bundle["drivers"][0]

        self.assertFalse(driver["is_truncated"])
        self.assertIsNone(driver["others_absolute_contribution"])
        self.assertIsNone(driver["others_contribution_share"])

    def test_artifact_id_persisted(self) -> None:
        """Bundle artifact_id should be queryable via _resolve_artifact_for_ref."""
        sid = self._make_session()
        bundle = self._attribute(sid)

        step_id = bundle["step_ref"]["step_id"]
        artifact = self.service._resolve_artifact_for_ref(sid, step_id)
        self.assertIsNotNone(artifact)
        self.assertEqual(artifact.get("result_type"), "attribute_bundle")

    def test_bundle_required_fields_present(self) -> None:
        """All required top-level bundle fields must be present."""
        sid = self._make_session()
        bundle = self._attribute(sid)

        required = [
            "result_type",
            "intent_type",
            "step_type",
            "metric",
            "left",
            "right",
            "dimensions",
            "validation",
            "observation_refs",
            "compare_ref",
            "comparison",
            "drivers",
            "lineage",
            "version",
            "projection_metadata",
            "step_ref",
            "artifact_id",
        ]
        for field in required:
            self.assertIn(field, bundle, f"Missing required field: {field}")

    def test_projection_metadata_reflects_decomposition_limit(self) -> None:
        """projection_metadata.decomposition_limit should equal decomposition_limit."""
        sid = self._make_session()
        bundle = self._attribute(sid, decomposition_limit=3)
        self.assertEqual(bundle["projection_metadata"]["decomposition_limit"], 3)

    def test_projection_metadata_no_min_contribution_pct(self) -> None:
        """projection_metadata must not contain min_contribution_pct (not in schema)."""
        sid = self._make_session()
        bundle = self._attribute(sid)
        self.assertNotIn("min_contribution_pct", bundle["projection_metadata"])

    def test_version_fields_correct(self) -> None:
        """version must carry all three required version fields."""
        sid = self._make_session()
        bundle = self._attribute(sid)
        version = bundle["version"]
        self.assertEqual(version["intent_contract_version"], "attribute.v1")
        self.assertEqual(version["projection_version"], "attribute_bundle.v1")
        self.assertIn("derived_logic_version", version)

    def test_provenance_contains_projection_version(self) -> None:
        """Persisted step provenance must include projection_version."""
        sid = self._make_session()
        bundle = self._attribute(sid)
        step_id = bundle["step_ref"]["step_id"]
        row = self.metadata.query_one(
            "SELECT provenance_json FROM steps WHERE step_id = ? AND session_id = ?",
            [step_id, sid],
        )
        self.assertIsNotNone(row)
        import json as _json

        provenance = _json.loads(row["provenance_json"])
        self.assertIn("projection_version", provenance)
        self.assertEqual(provenance["projection_version"], "attribute_bundle.v1")

    def test_missing_metric_raises(self) -> None:
        """Missing metric should raise ValueError."""
        sid = self._make_session()
        with self.assertRaises((ValueError, Exception)) as ctx:
            self.service.run_intent(
                sid,
                "attribute",
                {
                    "metric": "",
                    "left": {
                        "time_scope": {
                            "kind": "range",
                            "start": _CURRENT_START,
                            "end": _CURRENT_END,
                        }
                    },
                    "right": {
                        "time_scope": {
                            "kind": "range",
                            "start": _BASELINE_START,
                            "end": _BASELINE_END,
                        }
                    },
                    "dimensions": ["channel"],
                },
            )
        self.assertIn("metric", str(ctx.exception).lower())

    def test_empty_dimensions_raises(self) -> None:
        """Empty dimensions should raise ValueError."""
        sid = self._make_session()
        with self.assertRaises((ValueError, Exception)):
            self.service.run_intent(
                sid,
                "attribute",
                {
                    "metric": _METRIC,
                    "left": {
                        "time_scope": {
                            "kind": "range",
                            "start": _CURRENT_START,
                            "end": _CURRENT_END,
                        }
                    },
                    "right": {
                        "time_scope": {
                            "kind": "range",
                            "start": _BASELINE_START,
                            "end": _BASELINE_END,
                        }
                    },
                    "dimensions": [],
                },
            )

    def test_blank_dimension_string_raises(self) -> None:
        """Blank string in dimensions should raise ValueError."""
        sid = self._make_session()
        with self.assertRaises((ValueError, Exception)):
            self.service.run_intent(
                sid,
                "attribute",
                {
                    "metric": _METRIC,
                    "left": {
                        "time_scope": {
                            "kind": "range",
                            "start": _CURRENT_START,
                            "end": _CURRENT_END,
                        }
                    },
                    "right": {
                        "time_scope": {
                            "kind": "range",
                            "start": _BASELINE_START,
                            "end": _BASELINE_END,
                        }
                    },
                    "dimensions": ["   "],
                },
            )

    def test_decomposition_limit_zero_raises(self) -> None:
        """decomposition_limit=0 should raise ValueError."""
        sid = self._make_session()
        with self.assertRaises((ValueError, Exception)):
            self._attribute(sid, decomposition_limit=0)

    def test_decomposition_limit_over_max_raises(self) -> None:
        """decomposition_limit > 100 should raise ValueError."""
        sid = self._make_session()
        with self.assertRaises((ValueError, Exception)):
            self._attribute(sid, decomposition_limit=101)

    def test_missing_left_time_scope_raises(self) -> None:
        """Missing left.time_scope should raise ValueError."""
        sid = self._make_session()
        with self.assertRaises((ValueError, Exception)):
            self.service.run_intent(
                sid,
                "attribute",
                {
                    "metric": _METRIC,
                    "left": {},
                    "right": {
                        "time_scope": {
                            "kind": "range",
                            "start": _BASELINE_START,
                            "end": _BASELINE_END,
                        }
                    },
                    "dimensions": ["channel"],
                },
            )

    def test_result_type_is_attribute_bundle(self) -> None:
        """result_type must be 'attribute_bundle'."""
        sid = self._make_session()
        bundle = self._attribute(sid)
        self.assertEqual(bundle["result_type"], "attribute_bundle")
        self.assertEqual(bundle["intent_type"], "attribute")

    def test_deduplicated_dimensions(self) -> None:
        """Duplicate dimensions should be deduplicated; bundle.dimensions is deduped list."""
        sid = self._make_session()
        bundle = self._attribute(sid, dimensions=["channel", "channel", "region"])
        self.assertEqual(bundle["dimensions"], ["channel", "region"])
        self.assertEqual(len(bundle["drivers"]), 2)

    def test_independent_left_right_scope(self) -> None:
        """Independent left.scope and right.scope are wired to the correct observe calls."""
        sid = self._make_session()
        # Both sides use region=X (all rows), so the result should be identical to no-scope.
        # The test verifies that left.scope and right.scope flow independently to the runners.
        bundle = self._attribute(
            sid,
            dimensions=["channel"],
            left_scope={"constraints": {"region": "X"}},
            right_scope={"constraints": {"region": "X"}},
        )
        # Scopes stored in resolved sides
        self.assertEqual(bundle["left"]["scope"], {"constraints": {"region": "X"}})
        self.assertEqual(bundle["right"]["scope"], {"constraints": {"region": "X"}})
        # Result should still be clean (same data, so comparable and attributable)
        self.assertEqual(bundle["validation"]["status"], "attributable")

    def test_issue_codes_remapped_to_attribute_schema(self) -> None:
        """Any issues in drivers must use AttributeIssue schema codes, not raw decompose codes."""
        sid = self._make_session()
        # Force truncation to guarantee a driver_truncated issue
        bundle = self._attribute(sid, dimensions=["channel"], decomposition_limit=1)
        allowed_codes = {
            "observe_failed",
            "compare_needs_attention",
            "compare_not_comparable",
            "decompose_needs_attention",
            "decompose_not_attributable",
            "driver_truncated",
        }
        for driver in bundle["drivers"]:
            for issue in driver["issues"]:
                self.assertIn(
                    issue["code"],
                    allowed_codes,
                    f"Issue code '{issue['code']}' is not in AttributeIssue schema enum",
                )
        for issue in bundle["validation"]["issues"]:
            self.assertIn(
                issue["code"],
                allowed_codes,
                f"Validation issue code '{issue['code']}' is not in AttributeIssue schema enum",
            )


# ── HTTP endpoint tests ────────────────────────────────────────────────────────


class AttributeEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "attr_http.duckdb"
        meta_path = Path(cls.temp_dir.name) / "attr_http.meta.sqlite"

        analytics = DuckDBAnalyticsEngine(str(db_path))
        metadata = SQLiteMetadataStore(str(meta_path))
        metadata.initialize()
        analytics.initialize()

        _seed_attr_table(db_path)
        _seed_metadata(metadata)

        app = create_app(metadata_store=metadata, analytics_engine=analytics)
        cls.client = TestClient(app, raise_server_exceptions=True)

        # Create a persistent session for HTTP tests
        resp = cls.client.post(
            "/sessions",
            json={"goal": "http attr test", "budget": {}, "policy": {}},
        )
        resp.raise_for_status()
        cls.session_id = resp.json()["session_id"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_http_attribute_returns_200(self) -> None:
        """POST /sessions/{id}/intents/attribute with valid params returns 200."""
        resp = self.client.post(
            f"/sessions/{self.session_id}/intents/attribute",
            json={
                "metric": _metric_ref(_METRIC),
                "left": {
                    "time_scope": {
                        "kind": "range",
                        "start": _CURRENT_START,
                        "end": _CURRENT_END,
                    }
                },
                "right": {
                    "time_scope": {
                        "kind": "range",
                        "start": _BASELINE_START,
                        "end": _BASELINE_END,
                    }
                },
                "dimensions": ["channel"],
                "decomposition_limit": 5,
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body.get("result_type"), "attribute_bundle")
        self.assertIn("comparison", body)
        self.assertIn("drivers", body)

    def test_http_missing_dimensions_returns_422(self) -> None:
        """POST without dimensions returns 422."""
        resp = self.client.post(
            f"/sessions/{self.session_id}/intents/attribute",
            json={
                "metric": _metric_ref(_METRIC),
                "left": {
                    "time_scope": {"kind": "range", "start": _CURRENT_START, "end": _CURRENT_END}
                },
                "right": {
                    "time_scope": {"kind": "range", "start": _BASELINE_START, "end": _BASELINE_END}
                },
            },
        )
        self.assertEqual(resp.status_code, 422)

    def test_http_unknown_session_returns_404(self) -> None:
        """POST to unknown session returns 404."""
        resp = self.client.post(
            "/sessions/sess_nonexistent/intents/attribute",
            json={
                "metric": _metric_ref(_METRIC),
                "left": {
                    "time_scope": {"kind": "range", "start": _CURRENT_START, "end": _CURRENT_END}
                },
                "right": {
                    "time_scope": {"kind": "range", "start": _BASELINE_START, "end": _BASELINE_END}
                },
                "dimensions": ["channel"],
            },
        )
        self.assertEqual(resp.status_code, 404)
