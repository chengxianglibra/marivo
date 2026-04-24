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

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.intents.attribute import run_attribute_intent
from app.main import create_app
from app.service import SemanticLayerService
from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
from app.storage.sqlite_metadata import SQLiteMetadataStore
from tests.semantic_test_helpers import (
    ensure_active_duckdb_mapping,
    ensure_published_typed_metric,
    ensure_published_typed_metric_binding,
)
from tests.shared_fixtures import get_named_seeded_duckdb_path


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
        "current_window": {"start": _CURRENT_START, "end": _CURRENT_END},
        "baseline_window": {"start": "2025-03-01", "end": "2025-03-04"},
        "bucket_pairing": [
            {
                "current_bucket_start": _CURRENT_START,
                "baseline_bucket_start": "2025-03-01",
                "pairing_reason": "same_weekday_nearest",
                "shift_days": -364,
                "issues": [],
                "strictness_level": "strict",
                "is_reused_baseline_bucket": False,
            }
        ],
        "rollup_safe": True,
        "coverage_summary": {
            "aligned_bucket_count": 3,
            "unpaired_bucket_count": 0,
            "aligned_ratio": 1.0,
        },
        "comparability_warnings": [],
    }


# ── Seeding helpers ────────────────────────────────────────────────────────────


def _seed_attr_table(db_path: Path) -> None:
    """Copy the shared seeded analytics.attr_events fixture into place."""
    get_named_seeded_duckdb_path(db_path, "attribute_intent")


def _seed_metadata(meta: SQLiteMetadataStore) -> str:
    """Insert minimal metadata so attribute can resolve metric → table."""
    now = datetime.now(UTC).isoformat()
    src_id = "src_attrtest01"
    obj_id = "obj_attrtest01"
    met_id = "met_attrtest01"
    map_id = "map_attrtest01"

    meta.execute(
        "INSERT OR IGNORE INTO sources "
        "(source_id, source_type, display_name, authority_json, sync_mode, "
        "intrinsic_capabilities_json, policy_json, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            src_id,
            "duckdb",
            "Attr Test Source",
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
            "attr_events",
            "analytics.attr_events",
            json.dumps({"catalog": "main", "schema": "analytics", "table": "attr_events"}),
            now,
            now,
        ],
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
    ensure_active_duckdb_mapping(meta, source_id=src_id, now=now)
    return _METRIC


# ── Direct service tests ───────────────────────────────────────────────────────


class _LazyBundle:
    """Descriptor for lazy bundle computation on first access.

    Avoids running 6 full attribute intents in setUpClass when only 1-2 bundles
    are needed by the tests actually executed in a given worker.
    """

    def __init__(
        self, name: str, dimensions: list[str] | None = None, decomposition_limit: int = 5
    ):
        self.name = name
        self.dimensions = dimensions
        self.decomposition_limit = decomposition_limit
        self._cache_key = f"_lazy_{name}"
        self._session_key = f"_lazy_sid_{name}"

    def __get__(self, obj: AttributeRunnerServiceTests, objtype: type) -> dict:
        if obj is None:
            raise AttributeError(f"{self.name} bundle accessed on class, not instance")
        cache = getattr(objtype, "_bundle_cache", None)
        if cache is None:
            cache = {}
            objtype._bundle_cache = cache
        if self._cache_key not in cache:
            session_id = obj.service.create_session(f"attr {self.name} cache", {}, {}, {})[
                "session_id"
            ]
            cache[self._session_key] = session_id
            cache[self._cache_key] = obj._run_attribute(
                session_id,
                dimensions=self.dimensions,
                decomposition_limit=self.decomposition_limit,
            )
        return cache[self._cache_key]


class _LazySessionId:
    """Descriptor to get session_id for a lazy bundle."""

    def __init__(self, bundle_name: str):
        self.bundle_name = bundle_name
        self._session_key = f"_lazy_sid_{bundle_name}"
        self._cache_key = f"_lazy_{bundle_name}"

    def __get__(self, obj: AttributeRunnerServiceTests, objtype: type) -> str:
        if obj is None:
            raise AttributeError(f"{self.bundle_name} session_id accessed on class, not instance")
        cache = getattr(objtype, "_bundle_cache", {})
        if self._session_key not in cache:
            # Trigger bundle computation first
            getattr(obj, self.bundle_name + "_bundle")
            cache = getattr(objtype, "_bundle_cache", {})
        return cache[self._session_key]


class AttributeRunnerServiceTests(unittest.TestCase):
    # Lazy bundle descriptors - computed only when accessed by a test
    default_bundle = _LazyBundle("default", dimensions=["channel"])
    default_session_id = _LazySessionId("default")
    two_dim_bundle = _LazyBundle("two_dim", dimensions=["channel", "region"])
    two_dim_session_id = _LazySessionId("two_dim")
    truncated_bundle = _LazyBundle("truncated", dimensions=["channel"], decomposition_limit=2)
    not_truncated_bundle = _LazyBundle(
        "not_truncated", dimensions=["channel"], decomposition_limit=10
    )
    deduplicated_bundle = _LazyBundle("dedup", dimensions=["channel", "channel", "region"])
    limit_three_bundle = _LazyBundle("limit_three", dimensions=["channel"], decomposition_limit=3)

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "attr_svc.duckdb"
        meta_path = Path(cls.temp_dir.name) / "attr_svc.meta.sqlite"

        _seed_attr_table(db_path)
        cls.analytics = DuckDBAnalyticsEngine(str(db_path))
        cls.metadata = SQLiteMetadataStore(str(meta_path))
        cls.metadata.initialize()
        cls.analytics.initialize()
        _seed_metadata(cls.metadata)

        cls.service = SemanticLayerService(cls.metadata, cls.analytics)
        cls._bundle_cache: dict[str, dict] = {}

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def _make_session(self) -> str:
        return self.service.create_session("attr test", {}, {}, {})["session_id"]

    @classmethod
    def _run_attribute(
        cls,
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
        return cls.service.run_intent(
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

    def _attribute(
        self,
        session_id: str,
        dimensions: list[str] | None = None,
        decomposition_limit: int = 5,
        left_scope: dict | None = None,
        right_scope: dict | None = None,
    ) -> dict:
        return self._run_attribute(
            session_id,
            dimensions=dimensions,
            decomposition_limit=decomposition_limit,
            left_scope=left_scope,
            right_scope=right_scope,
        )

    def test_expansion_creates_all_steps(self) -> None:
        """attribute with 1 dimension creates 5 steps: obs×2 + compare + decompose + attribute."""
        rows = self.metadata.query_rows(
            "SELECT step_type FROM steps WHERE session_id = ?", [self.default_session_id]
        )
        step_types = [r["step_type"] for r in rows]
        self.assertEqual(step_types.count("observe"), 2)
        self.assertEqual(step_types.count("compare"), 1)
        self.assertEqual(step_types.count("decompose"), 1)
        self.assertEqual(step_types.count("attribute"), 1)
        self.assertEqual(len(step_types), 5)

    def test_expansion_two_dimensions_creates_six_steps(self) -> None:
        """attribute with 2 dimensions creates 6 steps."""
        rows = self.metadata.query_rows(
            "SELECT step_type FROM steps WHERE session_id = ?", [self.two_dim_session_id]
        )
        step_types = [r["step_type"] for r in rows]
        self.assertEqual(len(step_types), 6)
        self.assertEqual(step_types.count("decompose"), 2)

    def test_bundle_lineage_refs_correct(self) -> None:
        """Lineage refs in bundle match the intermediate step ids."""
        sid = self.default_session_id
        bundle = self.default_bundle

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
        self.assertEqual(self.default_bundle["validation"]["status"], "attributable")

    def test_driver_projection_quantitative_by_default(self) -> None:
        """Clean driver sets should keep contribution_share as quantitative attribution."""
        driver = self.default_bundle["drivers"][0]

        self.assertEqual(driver["interpretation"], "quantitative")
        self.assertFalse(driver["share_suppressed"])
        self.assertTrue(any(row["contribution_share"] is not None for row in driver["rows"]))

    def test_scalar_delta_summary_correct_values(self) -> None:
        """ScalarDeltaSummary should reflect known current=720, baseline=540, delta=180."""
        comparison = self.default_bundle["comparison"]

        self.assertEqual(comparison["comparison_type"], "scalar_delta")
        # current total = sum(100+80+60) * 3 days = 720
        self.assertAlmostEqual(comparison["left_value"], 720.0, places=1)
        # baseline total = sum(70+60+50) * 3 days = 540
        self.assertAlmostEqual(comparison["right_value"], 540.0, places=1)
        self.assertAlmostEqual(comparison["absolute_delta"], 180.0, places=1)
        self.assertAlmostEqual(comparison["relative_delta"], 180.0 / 540.0, places=4)
        self.assertEqual(comparison["direction"], "increase")
        self.assertEqual(comparison["comparability_status"], "comparable")

    def test_reuses_calendar_alignment_through_internal_compare(self) -> None:
        """attribute should inherit frozen alignment metadata through its internal compare step."""
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
            bundle = self._attribute(sid)

        compare_step_id = bundle["compare_ref"]["step_id"]
        compare_artifact = self.service._resolve_artifact_for_ref(sid, compare_step_id)
        self.assertIsNotNone(compare_artifact)
        self.assertEqual(bundle["validation"]["status"], "attributable")
        self.assertEqual(
            compare_artifact["resolved_input_summary"]["calendar_alignment"]["reuse_source"],
            "observation_resolved_policy_summary",
        )
        self.assertEqual(
            compare_artifact["resolved_input_summary"]["calendar_alignment"]["policy_ref"],
            "calendar_policy.weekday_yoy",
        )

    def test_calendar_alignment_mismatch_fails_through_internal_compare(self) -> None:
        """attribute should fail when its internal compare sees mismatched frozen metadata."""
        sid = self._make_session()
        summaries = [
            _resolved_policy_summary(),
            _resolved_policy_summary(resolved_calendar_source="calendar.business_events"),
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
                "attribute: COMPARE_FAILED - comparison failed: compare: NOT_COMPARABLE - "
                "left and right observations freeze different calendar sources, so the "
                "alignment metadata is not comparable. Re-run both observations against "
                "the same resolved calendar source.",
            ),
        ):
            self._attribute(sid)

    def test_drivers_length_matches_dimensions(self) -> None:
        """drivers array length equals len(dimensions)."""
        self.assertEqual(len(self.two_dim_bundle["drivers"]), 2)
        self.assertEqual(self.two_dim_bundle["drivers"][0]["dimension"], "channel")
        self.assertEqual(self.two_dim_bundle["drivers"][1]["dimension"], "region")

    def test_driver_rows_capped_at_decomposition_limit(self) -> None:
        """With decomposition_limit=2 and 3 channels, is_truncated should be True."""
        driver = self.truncated_bundle["drivers"][0]

        self.assertLessEqual(len(driver["rows"]), 2)
        self.assertEqual(driver["returned_row_count"], 2)
        self.assertEqual(driver["total_row_count"], 3)
        self.assertTrue(driver["is_truncated"])
        # others aggregation should be present
        self.assertIsNotNone(driver["others_absolute_contribution"])
        self.assertIsNotNone(driver["others_contribution_share"])

    def test_driver_truncated_issue_added_when_truncated(self) -> None:
        """When is_truncated, a driver_truncated issue must appear in driver.issues."""
        driver = self.truncated_bundle["drivers"][0]
        issue_codes = [i["code"] for i in driver["issues"]]
        self.assertIn("driver_truncated", issue_codes)

    def test_others_null_when_not_truncated(self) -> None:
        """others_* fields should be None when decomposition_limit >= total rows."""
        driver = self.not_truncated_bundle["drivers"][0]

        self.assertFalse(driver["is_truncated"])
        self.assertIsNone(driver["others_absolute_contribution"])
        self.assertIsNone(driver["others_contribution_share"])

    def test_artifact_id_persisted(self) -> None:
        """Bundle artifact_id should be queryable via _resolve_artifact_for_ref."""
        sid = self.default_session_id
        bundle = self.default_bundle

        step_id = bundle["step_ref"]["step_id"]
        artifact = self.service._resolve_artifact_for_ref(sid, step_id)
        self.assertIsNotNone(artifact)
        self.assertEqual(artifact.get("result_type"), "attribute_bundle")

    def test_bundle_required_fields_present(self) -> None:
        """All required top-level bundle fields must be present."""
        bundle = self.default_bundle

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
        self.assertEqual(self.limit_three_bundle["projection_metadata"]["decomposition_limit"], 3)

    def test_projection_metadata_no_min_contribution_pct(self) -> None:
        """projection_metadata must not contain min_contribution_pct (not in schema)."""
        self.assertNotIn("min_contribution_pct", self.default_bundle["projection_metadata"])

    def test_version_fields_correct(self) -> None:
        """version must carry all three required version fields."""
        version = self.default_bundle["version"]
        self.assertEqual(version["intent_contract_version"], "attribute.v1")
        self.assertEqual(version["projection_version"], "attribute_bundle.v1")
        self.assertIn("derived_logic_version", version)

    def test_provenance_contains_projection_version(self) -> None:
        """Persisted step provenance must include projection_version."""
        sid = self.default_session_id
        bundle = self.default_bundle
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
        self.assertEqual(self.default_bundle["result_type"], "attribute_bundle")
        self.assertEqual(self.default_bundle["intent_type"], "attribute")

    def test_deduplicated_dimensions(self) -> None:
        """Duplicate dimensions should be deduplicated; bundle.dimensions is deduped list."""
        self.assertEqual(self.deduplicated_bundle["dimensions"], ["channel", "region"])
        self.assertEqual(len(self.deduplicated_bundle["drivers"]), 2)

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

    def test_independent_left_right_calendar_policy_ref(self) -> None:
        """Side-level calendar_policy_ref values are forwarded to the corresponding observe calls."""
        sid = self._make_session()
        captured_params: list[dict[str, Any]] = []

        def _capture_observe(
            svc: SemanticLayerService,
            session_id: str,
            params: dict[str, Any] | None,
        ) -> dict[str, Any]:
            captured_params.append(dict(params or {}))
            return {
                "step_ref": {
                    "step_id": f"step_{len(captured_params)}",
                    "step_type": "observe",
                },
                "artifact_id": f"artifact_{len(captured_params)}",
                "observation_type": "scalar",
                "time_scope": dict(params["time_scope"]),
            }

        with (
            patch("app.intents.attribute.run_observe_intent", side_effect=_capture_observe),
            patch(
                "app.intents.attribute.run_compare_intent",
                return_value={
                    "step_ref": {"step_id": "step_compare", "step_type": "compare"},
                    "artifact_id": "artifact_compare",
                    "comparability": {"status": "comparable", "issues": []},
                    "left_value": 10.0,
                    "right_value": 8.0,
                    "absolute_delta": 2.0,
                    "relative_delta": 0.25,
                    "direction": "increase",
                },
            ),
            patch(
                "app.intents.attribute.run_decompose_intent",
                return_value={
                    "step_ref": {"step_id": "step_decompose", "step_type": "decompose"},
                    "artifact_id": "artifact_decompose",
                    "attribution": {"status": "attributable", "issues": []},
                    "rows": [
                        {
                            "key": "A",
                            "left_value": 10.0,
                            "right_value": 8.0,
                            "absolute_contribution": 2.0,
                            "contribution_share": 1.0,
                            "direction": "increase",
                            "presence": "both",
                        }
                    ],
                    "scope_absolute_delta": 2.0,
                    "unexplained_absolute_delta": 0.0,
                    "unexplained_share": 0.0,
                    "unexplained_reason": None,
                },
            ),
        ):
            bundle = self.service.run_intent(
                sid,
                "attribute",
                {
                    "metric": _METRIC,
                    "left": {
                        "time_scope": {
                            "kind": "range",
                            "start": _CURRENT_START,
                            "end": _CURRENT_END,
                        },
                        "calendar_policy_ref": "calendar_policy.weekday_yoy",
                    },
                    "right": {
                        "time_scope": {
                            "kind": "range",
                            "start": "2025-03-01",
                            "end": "2025-03-04",
                        },
                        "calendar_policy_ref": "calendar_policy.weekday_yoy",
                    },
                    "dimensions": ["channel"],
                },
            )

        self.assertEqual(len(captured_params), 2)
        self.assertEqual(captured_params[0]["calendar_policy_ref"], "calendar_policy.weekday_yoy")
        self.assertEqual(captured_params[1]["calendar_policy_ref"], "calendar_policy.weekday_yoy")
        self.assertEqual(bundle["validation"]["status"], "attributable")

    def test_reconciliation_needs_attention_suppresses_share_projection(self) -> None:
        """High-divergence decompose output remains directional but hides share ratios."""
        sid = self._make_session()

        with (
            patch(
                "app.intents.attribute.run_observe_intent",
                side_effect=[
                    {
                        "step_ref": {"step_id": "step_left_obs", "step_type": "observe"},
                        "artifact_id": "artifact_left_obs",
                        "observation_type": "scalar",
                        "time_scope": {
                            "kind": "range",
                            "start": _CURRENT_START,
                            "end": _CURRENT_END,
                        },
                    },
                    {
                        "step_ref": {"step_id": "step_right_obs", "step_type": "observe"},
                        "artifact_id": "artifact_right_obs",
                        "observation_type": "scalar",
                        "time_scope": {
                            "kind": "range",
                            "start": _BASELINE_START,
                            "end": _BASELINE_END,
                        },
                    },
                ],
            ),
            patch(
                "app.intents.attribute.run_compare_intent",
                return_value={
                    "step_ref": {"step_id": "step_compare", "step_type": "compare"},
                    "artifact_id": "artifact_compare",
                    "comparability": {"status": "comparable", "issues": []},
                    "left_value": 200.0,
                    "right_value": 100.0,
                    "absolute_delta": 100.0,
                    "relative_delta": 1.0,
                    "direction": "increase",
                },
            ),
            patch(
                "app.intents.attribute.run_decompose_intent",
                return_value={
                    "step_ref": {"step_id": "step_decompose", "step_type": "decompose"},
                    "artifact_id": "artifact_decompose",
                    "attribution": {
                        "status": "needs_attention",
                        "issues": [
                            {
                                "code": "attribution_not_reconcilable",
                                "severity": "error",
                                "message": (
                                    "Explained sum diverges from scope_absolute_delta by 57.4%."
                                ),
                            }
                        ],
                    },
                    "rows": [
                        {
                            "key": "entry_source",
                            "left_value": 157.4,
                            "right_value": 0.0,
                            "absolute_contribution": 157.4,
                            "contribution_share": 1.574,
                            "direction": "increase",
                            "presence": "both",
                        },
                        {
                            "key": "initiative_type",
                            "left_value": 43.4,
                            "right_value": 0.0,
                            "absolute_contribution": 43.4,
                            "contribution_share": 0.434,
                            "direction": "increase",
                            "presence": "both",
                        },
                        {
                            "key": "entry_resource",
                            "left_value": 21.6,
                            "right_value": 0.0,
                            "absolute_contribution": 21.6,
                            "contribution_share": 0.216,
                            "direction": "increase",
                            "presence": "both",
                        },
                    ],
                    "scope_absolute_delta": 100.0,
                    "unexplained_absolute_delta": -122.4,
                    "unexplained_share": -1.224,
                    "unexplained_reason": "scope_recomputation_failed",
                },
            ),
        ):
            bundle = self.service.run_intent(
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
                    "dimensions": ["channel"],
                    "decomposition_limit": 2,
                },
            )

        driver = bundle["drivers"][0]
        self.assertEqual(bundle["validation"]["status"], "needs_attention")
        self.assertEqual(driver["attribution_status"], "needs_attention")
        self.assertEqual(driver["interpretation"], "directional_only")
        self.assertTrue(driver["share_suppressed"])
        self.assertEqual(driver["returned_row_count"], 2)
        self.assertEqual(driver["total_row_count"], 3)
        self.assertEqual(driver["rows"][0]["absolute_contribution"], 157.4)
        self.assertIsNone(driver["rows"][0]["contribution_share"])
        self.assertIsNone(driver["rows"][1]["contribution_share"])
        self.assertEqual(driver["others_absolute_contribution"], 21.6)
        self.assertIsNone(driver["others_contribution_share"])
        self.assertEqual(driver["unexplained_reason"], "scope_recomputation_failed")
        self.assertEqual(
            bundle["projection_metadata"]["share_suppression_policy"],
            "suppress_on_reconciliation_needs_attention",
        )

        driver_messages = [issue["message"] for issue in driver["issues"]]
        validation_messages = [issue["message"] for issue in bundle["validation"]["issues"]]
        self.assertTrue(
            any("contribution_share values were suppressed" in m for m in driver_messages)
        )
        self.assertTrue(
            any("contribution_share values were suppressed" in m for m in validation_messages)
        )

    def test_issue_codes_remapped_to_attribute_schema(self) -> None:
        """Any issues in drivers must use AttributeIssue schema codes, not raw decompose codes."""
        bundle = self.truncated_bundle
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


class AttributeHourWindowTests(unittest.TestCase):
    def test_attribute_forwards_hour_boundaries_to_observe_and_preserves_them_in_bundle(
        self,
    ) -> None:
        class _FakeService:
            def __init__(self) -> None:
                self._step_counter = 0
                # Mock semantic_repository for metric resolution
                self.semantic_repository = MagicMock()
                mock_metric = MagicMock()
                mock_metric.additivity_constraints = {
                    "dimension_policy": "all",
                    "time_axis_policy": "additive",
                }
                mock_metric.primary_time_ref = "time.default"
                mock_metric.sample_kind = "rate"
                self.semantic_repository.resolve_metric.return_value = mock_metric

            @staticmethod
            def normalize_intent_metric_ref(metric_ref: str) -> str:
                return metric_ref

            @staticmethod
            def metric_name_from_ref(metric_ref: str) -> str:
                return metric_ref.removeprefix("metric.")

            def _new_step_id(self) -> str:
                self._step_counter += 1
                return f"step_{self._step_counter}"

            @staticmethod
            def _insert_artifact(
                session_id: str,
                step_id: str,
                artifact_type: str,
                artifact_name: str,
                payload: dict,
            ) -> str:
                _ = (session_id, step_id, artifact_type, artifact_name, payload)
                return "artifact_attribute_hour"

            @staticmethod
            def _insert_step(
                step_id: str,
                session_id: str,
                step_type: str,
                summary: str,
                result: dict,
                provenance: dict | None = None,
            ) -> None:
                _ = (step_id, session_id, step_type, summary, result, provenance)

        left_time_scope = {
            "kind": "range",
            "start": "2024-01-01 01:00:00",
            "end": "2024-01-01 03:00:00",
        }
        right_time_scope = {
            "kind": "range",
            "start": "2024-01-01T03:00:00",
            "end": "2024-01-01T05:00:00",
        }
        observe_results = [
            {
                "step_ref": {"step_id": "step_left_obs", "step_type": "observe"},
                "artifact_id": "artifact_left_obs",
                "observation_type": "scalar",
                "time_scope": {
                    "kind": "range",
                    "start": "2024-01-01T01:00:00",
                    "end": "2024-01-01T03:00:00",
                },
            },
            {
                "step_ref": {"step_id": "step_right_obs", "step_type": "observe"},
                "artifact_id": "artifact_right_obs",
                "observation_type": "scalar",
                "time_scope": {
                    "kind": "range",
                    "start": "2024-01-01T03:00:00",
                    "end": "2024-01-01T05:00:00",
                },
            },
        ]
        compare_result = {
            "step_ref": {"step_id": "step_compare", "step_type": "compare"},
            "artifact_id": "artifact_compare",
            "comparability": {"status": "comparable", "issues": []},
            "left_value": 10.0,
            "right_value": 8.0,
            "absolute_delta": 2.0,
            "relative_delta": 0.25,
            "direction": "increase",
        }
        decompose_result = {
            "step_ref": {"step_id": "step_decompose", "step_type": "decompose"},
            "artifact_id": "artifact_decompose",
            "attribution": {"status": "attributable", "issues": []},
            "rows": [
                {
                    "key": "A",
                    "left_value": 10.0,
                    "right_value": 8.0,
                    "absolute_contribution": 2.0,
                    "contribution_share": 1.0,
                    "direction": "increase",
                    "presence": "both",
                }
            ],
            "scope_absolute_delta": 2.0,
            "unexplained_absolute_delta": 0.0,
            "unexplained_share": 0.0,
            "unexplained_reason": None,
        }

        with (
            patch(
                "app.intents.attribute.run_observe_intent",
                side_effect=observe_results,
            ) as observe_mock,
            patch(
                "app.intents.attribute.run_compare_intent",
                return_value=compare_result,
            ),
            patch(
                "app.intents.attribute.run_decompose_intent",
                return_value=decompose_result,
            ),
        ):
            bundle = run_attribute_intent(
                _FakeService(),
                "sess_hour_attr",
                {
                    "metric": "metric.attr_hourly",
                    "left": {"time_scope": left_time_scope},
                    "right": {"time_scope": right_time_scope},
                    "dimensions": ["channel"],
                },
            )

        self.assertEqual(observe_mock.call_count, 2)
        self.assertEqual(observe_mock.call_args_list[0].args[2]["time_scope"], left_time_scope)
        self.assertEqual(observe_mock.call_args_list[1].args[2]["time_scope"], right_time_scope)
        self.assertEqual(
            bundle["left"]["time_scope"],
            {"kind": "range", "start": "2024-01-01T01:00:00", "end": "2024-01-01T03:00:00"},
        )
        self.assertEqual(
            bundle["right"]["time_scope"],
            {"kind": "range", "start": "2024-01-01T03:00:00", "end": "2024-01-01T05:00:00"},
        )
        self.assertEqual(bundle["validation"]["status"], "attributable")


# ── HTTP endpoint tests ────────────────────────────────────────────────────────


class AttributeEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "attr_http.duckdb"
        meta_path = Path(cls.temp_dir.name) / "attr_http.meta.sqlite"

        _seed_attr_table(db_path)
        analytics = DuckDBAnalyticsEngine(str(db_path))
        metadata = SQLiteMetadataStore(str(meta_path))
        metadata.initialize()
        analytics.initialize()
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

    def test_http_attribute_accepts_side_level_calendar_policy_ref(self) -> None:
        """POST /sessions/{id}/intents/attribute accepts side-level calendar_policy_ref."""
        captured: dict[str, Any] = {}

        def _capture_run_intent(
            session_id: str, intent: str, payload: dict[str, Any]
        ) -> dict[str, Any]:
            captured["session_id"] = session_id
            captured["intent"] = intent
            captured["payload"] = payload
            return {"result_type": "attribute_bundle"}

        with patch.object(
            self.client.app.state.service, "run_intent", side_effect=_capture_run_intent
        ):
            resp = self.client.post(
                f"/sessions/{self.session_id}/intents/attribute",
                json={
                    "metric": _metric_ref(_METRIC),
                    "left": {
                        "time_scope": {
                            "kind": "range",
                            "start": _CURRENT_START,
                            "end": _CURRENT_END,
                        },
                        "calendar_policy_ref": "calendar_policy.weekday_yoy",
                    },
                    "right": {
                        "time_scope": {
                            "kind": "range",
                            "start": "2025-03-01",
                            "end": "2025-03-04",
                        },
                        "calendar_policy_ref": "calendar_policy.weekday_yoy",
                    },
                    "dimensions": ["channel"],
                },
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("result_type"), "attribute_bundle")
        self.assertEqual(captured["session_id"], self.session_id)
        self.assertEqual(captured["intent"], "attribute")
        self.assertEqual(
            captured["payload"]["left"]["calendar_policy_ref"],
            "calendar_policy.weekday_yoy",
        )
        self.assertEqual(
            captured["payload"]["right"]["calendar_policy_ref"],
            "calendar_policy.weekday_yoy",
        )

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

    def test_http_additivity_violation_returns_409(self) -> None:
        """POST with disallowed dimensions on subset policy returns 409 with structured payload."""
        from app.execution.errors import ExecutionError

        def _raise_additivity_violation(
            session_id: str, intent: str, payload: dict[str, Any]
        ) -> dict[str, Any]:
            raise ExecutionError(
                code="ADDITIVITY_CONSTRAINT_DIMENSION_NOT_ALLOWED",
                category="compatibility",
                message="attribute: ADDITIVITY_CONSTRAINT_DIMENSION_NOT_ALLOWED - test",
                detail={
                    "compatibility_error": {
                        "code": "ADDITIVITY_CONSTRAINT_DIMENSION_NOT_ALLOWED",
                        "metric": "metric.test",
                        "dimension_policy": "subset",
                        "time_axis_policy": "non_additive",
                        "allowed_dimensions": ["dimension.country"],
                        "disallowed_dimensions": ["dimension.product"],
                        "time_rollup_allowed": False,
                        "remediation_hint": "Retry with only allowed dimensions: ['dimension.country']",
                    },
                },
            )

        with patch.object(
            self.client.app.state.service, "run_intent", side_effect=_raise_additivity_violation
        ):
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
                    "dimensions": ["dimension.product"],
                },
            )
            self.assertEqual(resp.status_code, 409)
            body = resp.json()
            detail = body["detail"]
            self.assertEqual(detail["code"], "ADDITIVITY_CONSTRAINT_DIMENSION_NOT_ALLOWED")
            self.assertIn("allowed_dimensions", detail)
            self.assertIn("disallowed_dimensions", detail)
            self.assertEqual(detail["allowed_dimensions"], ["dimension.country"])
            self.assertEqual(detail["disallowed_dimensions"], ["dimension.product"])
