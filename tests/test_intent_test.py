"""Tests for the `test` atomic intent runner (Phase 3b-5).

Covers:
  - run_test_intent: Welch's t-test rejects null for large mean difference
  - run_test_intent: Welch's t-test fails to reject for same distribution
  - run_test_intent: two-proportion z-test rejects null for large rate difference
  - run_test_intent: artifact schema required fields present
  - run_test_intent: step is committed and retrievable
  - run_test_intent: cross-session ref raises ValueError
  - run_test_intent: non-observe upstream type raises ValueError
  - run_test_intent: observation_type mismatch raises ValueError
  - run_test_intent: invalid observation_type raises ValueError
  - run_test_intent: alpha out of range raises ValueError
  - run_test_intent: method-type mismatch raises ValueError
  - run_test_intent: alternative='greater' and 'less' produce correct directional p-value
  - HTTP endpoint: valid test returns 200
  - HTTP endpoint: missing left_ref returns 422
  - HTTP endpoint: nonexistent session returns 404
  - HTTP endpoint: nonexistent step ref returns 422
"""

from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.service import SemanticLayerService
from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
from app.storage.sqlite_metadata import SQLiteMetadataStore
from tests.semantic_test_helpers import (
    ensure_published_typed_metric,
    ensure_published_typed_metric_binding,
)
from tests.shared_fixtures import get_named_seeded_duckdb_path


def _metric_ref(name: str) -> str:
    return f"metric.{name}"


# ── Constants ──────────────────────────────────────────────────────────────────

_TIME_START = "2026-01-01"
_TIME_END = "2026-02-01"
_DATE = "2026-01-15"  # single date row for per-row data


def _resolved_policy_summary(
    *,
    policy_ref: str = "calendar_policy.weekday_yoy",
    comparison_basis: str = "yoy",
    resolved_calendar_source: str = "calendar.cn_public_holidays",
    resolved_calendar_version: str = "2026.01",
    comparability_warnings: list[str] | None = None,
    aligned_bucket_count: int = 31,
    unpaired_bucket_count: int = 0,
    expected_bucket_count: int = 31,
    present_bucket_count: int = 31,
    missing_bucket_count: int = 0,
) -> dict[str, object]:
    total_bucket_count = aligned_bucket_count + unpaired_bucket_count
    aligned_ratio = (
        float(aligned_bucket_count) / float(total_bucket_count) if total_bucket_count else 0.0
    )
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
        "current_window": {"start": _TIME_START, "end": _TIME_END},
        "baseline_window": {"start": "2025-01-01", "end": "2025-02-01"},
        "bucket_pairing": [
            {
                "current_bucket_start": _TIME_START,
                "baseline_bucket_start": "2025-01-01",
                "pairing_reason": "same_weekday_nearest",
                "shift_days": -364,
                "issues": [],
                "strictness_level": "strict",
                "is_reused_baseline_bucket": False,
            }
        ],
        "rollup_safe": True,
        "coverage_summary": {
            "aligned_bucket_count": aligned_bucket_count,
            "unpaired_bucket_count": unpaired_bucket_count,
            "aligned_ratio": aligned_ratio,
        },
        "data_coverage_summary": {
            "expected_bucket_count": expected_bucket_count,
            "present_bucket_count": present_bucket_count,
            "missing_bucket_count": missing_bucket_count,
            "coverage_ratio": (
                float(present_bucket_count) / float(expected_bucket_count)
                if expected_bucket_count
                else 0.0
            ),
            "aligned_expected_bucket_count": expected_bucket_count,
            "aligned_present_current_bucket_count": present_bucket_count,
            "aligned_present_baseline_bucket_count": present_bucket_count,
            "aligned_present_both_bucket_count": present_bucket_count,
        },
        "comparability_warnings": list(comparability_warnings or []),
    }


def _seed_metric(
    meta: SQLiteMetadataStore,
    *,
    suffix: str,
    metric_name: str,
    table_fqn: str,
    native_name: str,
    definition_sql: str,
) -> str:
    """Insert source → object → metric → mapping for one test metric."""
    now = datetime.now(UTC).isoformat()
    src_id = f"src_test{suffix}"
    obj_id = f"obj_test{suffix}"
    met_id = f"met_test{suffix}"
    map_id = f"map_test{suffix}"

    meta.execute(
        "INSERT OR IGNORE INTO sources "
        "(source_id, source_type, display_name, authority_json, sync_mode, "
        "intrinsic_capabilities_json, policy_json, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            src_id,
            "duckdb",
            f"Test Source {suffix}",
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
        "(object_id, source_id, object_type, native_name, fqn, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [obj_id, src_id, "table", native_name, table_fqn, now, now],
    )
    ensure_published_typed_metric(
        meta,
        metric_name=metric_name,
        display_name=metric_name,
        grain="day",
        dimensions=["event_date"],
        definition_sql=definition_sql,
    )
    ensure_published_typed_metric_binding(
        meta,
        metric_name=metric_name,
        carrier_locator=table_fqn,
        source_object_ref=obj_id,
    )
    return metric_name


def _duckdb_table_visible(engine: DuckDBAnalyticsEngine, table_fqn: str) -> bool:
    """Check table visibility via DuckDB catalog queries.

    The HTTP setup in this file is validating seeded test tables, so use the
    catalog directly instead of the generic helper to keep the assertion
    deterministic in this test harness.
    """
    schema, table_name = table_fqn.rsplit(".", 1)
    with engine._connect() as con:
        row = con.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = ? AND table_name = ?",
            [schema, table_name],
        ).fetchone()
    return row is not None and row[0] == 1


# ── Direct-service tests ──────────────────────────────────────────────────────


@pytest.mark.slow
class TestRunnerServiceTests(unittest.TestCase):
    """Tests that call run_test_intent through SemanticLayerService directly."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "test_svc.duckdb"
        meta_path = Path(cls.temp_dir.name) / "test_svc.meta.sqlite"

        cls.analytics = DuckDBAnalyticsEngine(str(db_path))
        cls.metadata = SQLiteMetadataStore(str(meta_path))
        cls.metadata.initialize()
        get_named_seeded_duckdb_path(db_path, "test_intent")
        cls.analytics.initialize()

        cls.metric_num_a = _seed_metric(
            cls.metadata,
            suffix="numa",
            metric_name="test_response_time_a",
            table_fqn="analytics.test_numeric_a",
            native_name="test_numeric_a",
            definition_sql="response_time",
        )
        cls.metric_num_b = _seed_metric(
            cls.metadata,
            suffix="numb",
            metric_name="test_response_time_b",
            table_fqn="analytics.test_numeric_b",
            native_name="test_numeric_b",
            definition_sql="response_time",
        )
        cls.metric_rate_a = _seed_metric(
            cls.metadata,
            suffix="ratea",
            metric_name="test_conversion_a",
            table_fqn="analytics.test_rate_a",
            native_name="test_rate_a",
            definition_sql="CAST(converted AS DOUBLE)",
        )
        cls.metric_rate_b = _seed_metric(
            cls.metadata,
            suffix="rateb",
            metric_name="test_conversion_b",
            table_fqn="analytics.test_rate_b",
            native_name="test_rate_b",
            definition_sql="CAST(converted AS DOUBLE)",
        )

        cls.service = SemanticLayerService(cls.metadata, cls.analytics)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def _make_session(self) -> str:
        r = self.service.create_session("test intent session", {}, {}, {})
        return r["session_id"]

    def _observe_numeric(self, session_id: str, metric: str) -> dict:
        return self.service.run_intent(
            session_id,
            "observe",
            {
                "metric": metric,
                "result_mode": "numeric_sample_summary",
                "time_scope": {"kind": "range", "start": _TIME_START, "end": _TIME_END},
            },
        )

    def _observe_rate(self, session_id: str, metric: str) -> dict:
        return self.service.run_intent(
            session_id,
            "observe",
            {
                "metric": metric,
                "result_mode": "rate_sample_summary",
                "time_scope": {"kind": "range", "start": _TIME_START, "end": _TIME_END},
            },
        )

    def _run_test(
        self,
        session_id: str,
        left_artifact_id: str,
        left_step_id: str,
        right_artifact_id: str,
        right_step_id: str,
        observation_type: str = "numeric_sample_summary",
        **hypothesis_kwargs: object,
    ) -> dict:
        hyp = {"family": "difference"}
        hyp.update(hypothesis_kwargs)
        return self.service.run_intent(
            session_id,
            "test",
            {
                "left_ref": {
                    "step_id": left_step_id,
                    "step_type": "observe",
                    "artifact_id": left_artifact_id,
                    "observation_type": observation_type,
                },
                "right_ref": {
                    "step_id": right_step_id,
                    "step_type": "observe",
                    "artifact_id": right_artifact_id,
                    "observation_type": observation_type,
                },
                "hypothesis": hyp,
                "method": "auto",
            },
        )

    def _patch_observe_artifact(
        self,
        session_id: str,
        step_id: str,
        *,
        resolved_policy_summary: dict[str, object] | None,
    ) -> None:
        artifact_result = self.service._resolve_artifact_with_id(session_id, step_id)
        self.assertIsNotNone(artifact_result)
        artifact_id, artifact = artifact_result
        artifact_payload = deepcopy(artifact)
        artifact_payload["resolved_policy_summary"] = resolved_policy_summary
        self.metadata.execute(
            "UPDATE artifacts SET content_json = ? WHERE artifact_id = ?",
            [json.dumps(artifact_payload), artifact_id],
        )

    def _assert_calendar_alignment_mismatch_raises(
        self,
        *,
        field_name: str,
        right_value: str,
        expected_message: str,
    ) -> None:
        session_id = self._make_session()
        obs_a = self._observe_numeric(session_id, self.metric_num_a)
        obs_b = self._observe_numeric(session_id, self.metric_num_b)
        self._patch_observe_artifact(
            session_id,
            obs_a["step_ref"]["step_id"],
            resolved_policy_summary=_resolved_policy_summary(),
        )
        right_summary = _resolved_policy_summary()
        right_summary[field_name] = right_value
        self._patch_observe_artifact(
            session_id,
            obs_b["step_ref"]["step_id"],
            resolved_policy_summary=right_summary,
        )

        with self.assertRaisesRegex(ValueError, expected_message):
            self._run_test(
                session_id,
                obs_a["artifact_id"],
                obs_a["step_ref"]["step_id"],
                obs_b["artifact_id"],
                obs_b["step_ref"]["step_id"],
            )

    # ── Welch's t-test ─────────────────────────────────────────────────────────

    def test_numeric_welch_t_rejects_null_for_large_diff(self) -> None:
        """N(100,10) vs N(130,10), n=200: t-test must reject null (large mean diff)."""
        session_id = self._make_session()
        obs_a = self._observe_numeric(session_id, self.metric_num_a)
        obs_b = self._observe_numeric(session_id, self.metric_num_b)

        result = self._run_test(
            session_id,
            obs_a["artifact_id"],
            obs_a["step_ref"]["step_id"],
            obs_b["artifact_id"],
            obs_b["step_ref"]["step_id"],
        )

        self.assertEqual(result["method"], "welch_t")
        self.assertTrue(result["decision"]["reject_null"])
        self.assertLess(result["p_value"], 0.05)
        # estimate ≈ -30 (mean_a - mean_b)
        self.assertLess(result["estimate"]["value"], -20.0)

    def test_numeric_welch_t_fails_to_reject_for_same_metric(self) -> None:
        """Same metric observed twice: mean diff ≈ 0, should fail to reject null."""
        session_id = self._make_session()
        obs_a1 = self._observe_numeric(session_id, self.metric_num_a)
        obs_a2 = self._observe_numeric(session_id, self.metric_num_a)

        result = self._run_test(
            session_id,
            obs_a1["artifact_id"],
            obs_a1["step_ref"]["step_id"],
            obs_a2["artifact_id"],
            obs_a2["step_ref"]["step_id"],
        )

        self.assertEqual(result["method"], "welch_t")
        # Same data → same artifact values → estimate = 0.0, t = 0, p = 1.0
        self.assertAlmostEqual(result["estimate"]["value"], 0.0, places=6)
        self.assertFalse(result["decision"]["reject_null"])

    # ── Two-proportion z-test ──────────────────────────────────────────────────

    def test_rate_proportion_z_rejects_null(self) -> None:
        """30% vs 50% conversion, n=1000 each: z-test must reject null."""
        session_id = self._make_session()
        obs_ra = self._observe_rate(session_id, self.metric_rate_a)
        obs_rb = self._observe_rate(session_id, self.metric_rate_b)

        result = self._run_test(
            session_id,
            obs_ra["artifact_id"],
            obs_ra["step_ref"]["step_id"],
            obs_rb["artifact_id"],
            obs_rb["step_ref"]["step_id"],
            observation_type="rate_sample_summary",
        )

        self.assertEqual(result["method"], "two_proportion_z")
        self.assertTrue(result["decision"]["reject_null"])
        self.assertLess(result["p_value"], 0.05)
        # rate_a ≈ 0.30, rate_b ≈ 0.50 → estimate ≈ -0.20
        self.assertLess(result["estimate"]["value"], -0.10)

    def test_rate_alternative_greater(self) -> None:
        """alternative='greater' with rate_b > rate_a: p-value should be near 1 (H0: p_left > p_right)."""
        session_id = self._make_session()
        obs_ra = self._observe_rate(session_id, self.metric_rate_a)
        obs_rb = self._observe_rate(session_id, self.metric_rate_b)

        result = self._run_test(
            session_id,
            obs_ra["artifact_id"],
            obs_ra["step_ref"]["step_id"],
            obs_rb["artifact_id"],
            obs_rb["step_ref"]["step_id"],
            observation_type="rate_sample_summary",
            alternative="greater",
        )

        # left(~30%) is NOT greater than right(~50%), so we fail to reject
        self.assertFalse(result["decision"]["reject_null"])
        self.assertGreater(result["p_value"], 0.10)

    def test_rate_alternative_less(self) -> None:
        """alternative='less' with rate_a < rate_b: should reject null."""
        session_id = self._make_session()
        obs_ra = self._observe_rate(session_id, self.metric_rate_a)
        obs_rb = self._observe_rate(session_id, self.metric_rate_b)

        result = self._run_test(
            session_id,
            obs_ra["artifact_id"],
            obs_ra["step_ref"]["step_id"],
            obs_rb["artifact_id"],
            obs_rb["step_ref"]["step_id"],
            observation_type="rate_sample_summary",
            alternative="less",
        )

        # left(~30%) IS less than right(~50%), so we reject H0
        self.assertTrue(result["decision"]["reject_null"])
        self.assertLess(result["p_value"], 0.05)

    # ── Artifact schema ────────────────────────────────────────────────────────

    def test_artifact_schema_fields(self) -> None:
        """All mandatory artifact schema fields must be present."""
        session_id = self._make_session()
        obs_a = self._observe_numeric(session_id, self.metric_num_a)
        obs_b = self._observe_numeric(session_id, self.metric_num_b)

        result = self._run_test(
            session_id,
            obs_a["artifact_id"],
            obs_a["step_ref"]["step_id"],
            obs_b["artifact_id"],
            obs_b["step_ref"]["step_id"],
        )

        self.assertEqual(result["artifact_schema_version"], "v1")
        self.assertEqual(result["result_type"], "hypothesis_test")
        self.assertNotIn("artifact_type", result)
        self.assertIn("hypothesis", result)
        self.assertEqual(result["hypothesis"]["family"], "difference")
        self.assertIn("alternative", result["hypothesis"])
        self.assertIn("alpha", result["hypothesis"])
        self.assertIn("method", result)
        self.assertIn("estimate", result)
        self.assertIn("estimand", result["estimate"])
        self.assertEqual(result["estimate"]["estimand"], "mean_diff")
        self.assertIn("statistic", result)
        self.assertEqual(result["statistic"]["name"], "t")
        self.assertIn("p_value", result)
        self.assertIsNotNone(result["p_value"])
        self.assertIn("decision", result)
        self.assertIn("reject_null", result["decision"])
        self.assertIn("assumptions", result)
        self.assertEqual(result["assumptions"]["variance_model"], "welch")
        self.assertEqual(result["assumptions"]["distribution_check"], "unchecked")
        self.assertIn("validation", result)
        self.assertIn("status", result["validation"])
        self.assertIn("execution_metadata", result)
        self.assertEqual(result["execution_metadata"]["engine"], "none")
        self.assertIn("source_lineage", result)
        self.assertIn("step_ref", result)
        self.assertIn("artifact_id", result)
        self.assertIsNotNone(result["artifact_id"])

    def test_rate_artifact_estimand_is_rate_diff(self) -> None:
        """Rate test artifact must have estimand='rate_diff' and statistic.name='z'."""
        session_id = self._make_session()
        obs_ra = self._observe_rate(session_id, self.metric_rate_a)
        obs_rb = self._observe_rate(session_id, self.metric_rate_b)

        result = self._run_test(
            session_id,
            obs_ra["artifact_id"],
            obs_ra["step_ref"]["step_id"],
            obs_rb["artifact_id"],
            obs_rb["step_ref"]["step_id"],
            observation_type="rate_sample_summary",
        )

        self.assertEqual(result["estimate"]["estimand"], "rate_diff")
        self.assertEqual(result["statistic"]["name"], "z")
        self.assertIsNone(result["assumptions"]["variance_model"])
        self.assertIsNone(result["assumptions"]["distribution_check"])

    def test_metric_mismatch_produces_warning(self) -> None:
        """Different metric names on same obs type → needs_attention with metric_mismatch warning."""
        session_id = self._make_session()
        obs_a = self._observe_numeric(session_id, self.metric_num_a)
        obs_b = self._observe_numeric(session_id, self.metric_num_b)

        result = self._run_test(
            session_id,
            obs_a["artifact_id"],
            obs_a["step_ref"]["step_id"],
            obs_b["artifact_id"],
            obs_b["step_ref"]["step_id"],
        )

        self.assertEqual(result["validation"]["status"], "needs_attention")
        codes = [i["code"] for i in result["validation"]["issues"]]
        self.assertIn("metric_mismatch", codes)
        # Statistical result is still computed despite the warning
        self.assertIn("reject_null", result["decision"])
        self.assertIsNotNone(result["p_value"])

    def test_calendar_alignment_metadata_mismatch_raises(self) -> None:
        """Single-sided frozen alignment metadata must fail test comparability reuse."""
        session_id = self._make_session()
        obs_a = self._observe_numeric(session_id, self.metric_num_a)
        obs_b = self._observe_numeric(session_id, self.metric_num_b)
        self._patch_observe_artifact(
            session_id,
            obs_a["step_ref"]["step_id"],
            resolved_policy_summary=_resolved_policy_summary(),
        )

        with self.assertRaisesRegex(
            ValueError,
            "test: NOT_COMPARABLE - calendar alignment metadata is missing on one observation",
        ):
            self._run_test(
                session_id,
                obs_a["artifact_id"],
                obs_a["step_ref"]["step_id"],
                obs_b["artifact_id"],
                obs_b["step_ref"]["step_id"],
            )

    def test_calendar_alignment_version_mismatch_raises(self) -> None:
        """Mismatched frozen calendar version must fail test comparability reuse."""
        self._assert_calendar_alignment_mismatch_raises(
            field_name="resolved_calendar_version",
            right_value="2026.02",
            expected_message=(
                "test: NOT_COMPARABLE - left and right observations freeze different calendar "
                "versions"
            ),
        )

    def test_calendar_alignment_policy_mismatch_raises(self) -> None:
        """Mismatched frozen calendar policy ref must fail test comparability reuse."""
        self._assert_calendar_alignment_mismatch_raises(
            field_name="policy_ref",
            right_value="calendar_policy.holiday_yoy",
            expected_message=(
                "test: NOT_COMPARABLE - left and right observations freeze different calendar "
                "policies"
            ),
        )

    def test_calendar_alignment_comparison_basis_mismatch_raises(self) -> None:
        """Mismatched frozen comparison basis must fail test comparability reuse."""
        self._assert_calendar_alignment_mismatch_raises(
            field_name="comparison_basis",
            right_value="mom",
            expected_message=(
                "test: NOT_COMPARABLE - left and right observations freeze different calendar "
                "comparison bases"
            ),
        )

    def test_calendar_alignment_source_mismatch_raises(self) -> None:
        """Mismatched frozen calendar source must fail test comparability reuse."""
        self._assert_calendar_alignment_mismatch_raises(
            field_name="resolved_calendar_source",
            right_value="calendar.business_events",
            expected_message=(
                "test: NOT_COMPARABLE - left and right observations freeze different calendar "
                "sources"
            ),
        )

    def test_calendar_alignment_warnings_flow_into_validation_issues(self) -> None:
        """Frozen alignment warnings should be surfaced through the test validation issues."""
        session_id = self._make_session()
        obs_a = self._observe_numeric(session_id, self.metric_num_a)
        obs_b = self._observe_numeric(session_id, self.metric_num_b)
        warning_summary = _resolved_policy_summary(
            comparability_warnings=["fallback_applied"],
            aligned_bucket_count=30,
            unpaired_bucket_count=1,
        )
        self._patch_observe_artifact(
            session_id,
            obs_a["step_ref"]["step_id"],
            resolved_policy_summary=warning_summary,
        )
        self._patch_observe_artifact(
            session_id,
            obs_b["step_ref"]["step_id"],
            resolved_policy_summary=warning_summary,
        )

        result = self._run_test(
            session_id,
            obs_a["artifact_id"],
            obs_a["step_ref"]["step_id"],
            obs_b["artifact_id"],
            obs_b["step_ref"]["step_id"],
        )

        self.assertEqual(result["validation"]["status"], "needs_attention")
        issues_by_code = {issue["code"]: issue for issue in result["validation"]["issues"]}
        codes = list(issues_by_code)
        self.assertIn("fallback_applied", codes)
        self.assertIn("alignment_coverage_insufficient", codes)
        self.assertEqual(issues_by_code["fallback_applied"]["gate_family"], "comparability_gate")
        self.assertFalse(issues_by_code["fallback_applied"]["blocking"])
        self.assertEqual(
            issues_by_code["fallback_applied"]["message"],
            (
                "calendar alignment required a fallback matcher, so the comparison is usable "
                "but less strictly aligned than the primary policy path. Review whether the "
                "fallback alignment is acceptable; otherwise fill in the missing annotations "
                "or choose a policy that better matches this window."
            ),
        )
        self.assertEqual(
            issues_by_code["alignment_coverage_insufficient"]["gate_family"],
            "comparability_gate",
        )
        self.assertFalse(issues_by_code["alignment_coverage_insufficient"]["blocking"])
        self.assertEqual(
            issues_by_code["alignment_coverage_insufficient"]["details"],
            {
                "left_coverage_summary": {
                    "aligned_bucket_count": 30,
                    "unpaired_bucket_count": 1,
                    "aligned_ratio": 30 / 31,
                },
                "right_coverage_summary": {
                    "aligned_bucket_count": 30,
                    "unpaired_bucket_count": 1,
                    "aligned_ratio": 30 / 31,
                },
                "effective_coverage_summary": {
                    "aligned_bucket_count": 30,
                    "unpaired_bucket_count": 1,
                    "aligned_ratio": 30 / 31,
                },
                "next_action_hint": "shrink_window_or_complete_mapping",
            },
        )
        self.assertEqual(
            result["source_lineage"]["calendar_alignment"]["reuse_source"],
            "observation_resolved_policy_summary",
        )
        self.assertEqual(
            result["source_lineage"]["calendar_alignment"]["policy_ref"],
            "calendar_policy.weekday_yoy",
        )
        self.assertTrue(result["source_lineage"]["calendar_alignment"]["rollup_safe"])
        self.assertEqual(
            result["source_lineage"]["calendar_alignment"]["effective_coverage_summary"],
            {
                "aligned_bucket_count": 30,
                "unpaired_bucket_count": 1,
                "aligned_ratio": 30 / 31,
            },
        )

    def test_calendar_alignment_weekday_pairing_tie_blocks_validation(self) -> None:
        """Unresolved weekday tie must stay in comparability_gate and block test execution."""
        session_id = self._make_session()
        obs_a = self._observe_numeric(session_id, self.metric_num_a)
        obs_b = self._observe_numeric(session_id, self.metric_num_b)
        warning_summary = _resolved_policy_summary(
            comparability_warnings=["weekday_pairing_tie"],
        )
        self._patch_observe_artifact(
            session_id,
            obs_a["step_ref"]["step_id"],
            resolved_policy_summary=warning_summary,
        )
        self._patch_observe_artifact(
            session_id,
            obs_b["step_ref"]["step_id"],
            resolved_policy_summary=warning_summary,
        )

        with self.assertRaisesRegex(
            ValueError,
            "test: NOT_COMPARABLE - weekday alignment produced an unresolved tie",
        ):
            self._run_test(
                session_id,
                obs_a["artifact_id"],
                obs_a["step_ref"]["step_id"],
                obs_b["artifact_id"],
                obs_b["step_ref"]["step_id"],
            )

    # ── Validation error cases ─────────────────────────────────────────────────

    def test_cross_session_ref_raises(self) -> None:
        """left_ref.session_id != current session → ValueError."""
        session_id = self._make_session()
        other_session = self._make_session()

        obs_a = self._observe_numeric(session_id, self.metric_num_a)
        obs_b = self._observe_numeric(session_id, self.metric_num_b)

        with self.assertRaises(ValueError, msg="cross-session ref should raise ValueError"):
            self.service.run_intent(
                session_id,
                "test",
                {
                    "left_ref": {
                        "artifact_id": obs_a["artifact_id"],
                        "observation_type": "numeric_sample_summary",
                        "step_id": obs_a["step_ref"]["step_id"],
                        "session_id": other_session,  # wrong session
                        "step_type": "observe",
                    },
                    "right_ref": {
                        "artifact_id": obs_b["artifact_id"],
                        "observation_type": "numeric_sample_summary",
                        "step_id": obs_b["step_ref"]["step_id"],
                        "step_type": "observe",
                    },
                    "hypothesis": {"family": "difference"},
                    "method": "auto",
                },
            )

    def test_invalid_step_type_raises(self) -> None:
        """step_type != 'observe' → ValueError."""
        session_id = self._make_session()
        obs_a = self._observe_numeric(session_id, self.metric_num_a)
        obs_b = self._observe_numeric(session_id, self.metric_num_b)

        with self.assertRaises(ValueError):
            self.service.run_intent(
                session_id,
                "test",
                {
                    "left_ref": {
                        "artifact_id": obs_a["artifact_id"],
                        "observation_type": "numeric_sample_summary",
                        "step_id": obs_a["step_ref"]["step_id"],
                        "step_type": "compare",  # invalid
                    },
                    "right_ref": {
                        "artifact_id": obs_b["artifact_id"],
                        "observation_type": "numeric_sample_summary",
                        "step_id": obs_b["step_ref"]["step_id"],
                        "step_type": "observe",
                    },
                    "hypothesis": {"family": "difference"},
                    "method": "auto",
                },
            )

    def test_observation_type_mismatch_raises(self) -> None:
        """numeric vs rate observation_type → ValueError (NOT_COMPARABLE)."""
        session_id = self._make_session()
        obs_num = self._observe_numeric(session_id, self.metric_num_a)
        obs_rate = self._observe_rate(session_id, self.metric_rate_a)

        with self.assertRaises(ValueError, msg="type mismatch should raise ValueError"):
            self.service.run_intent(
                session_id,
                "test",
                {
                    "left_ref": {
                        "artifact_id": obs_num["artifact_id"],
                        "observation_type": "numeric_sample_summary",
                        "step_id": obs_num["step_ref"]["step_id"],
                        "step_type": "observe",
                    },
                    "right_ref": {
                        "artifact_id": obs_rate["artifact_id"],
                        "observation_type": "rate_sample_summary",
                        "step_id": obs_rate["step_ref"]["step_id"],
                        "step_type": "observe",
                    },
                    "hypothesis": {"family": "difference"},
                    "method": "auto",
                },
            )

    def test_nonexistent_step_ref_raises(self) -> None:
        """Left ref pointing to unknown step_id → ValueError (STEP_NOT_FOUND)."""
        session_id = self._make_session()
        obs_b = self._observe_numeric(session_id, self.metric_num_b)

        with self.assertRaises(ValueError):
            self.service.run_intent(
                session_id,
                "test",
                {
                    "left_ref": {
                        "artifact_id": "art_nonexistent_step",
                        "observation_type": "numeric_sample_summary",
                        "step_id": "obs_nonexistent_step",
                        "step_type": "observe",
                    },
                    "right_ref": {
                        "artifact_id": obs_b["artifact_id"],
                        "observation_type": "numeric_sample_summary",
                        "step_id": obs_b["step_ref"]["step_id"],
                        "step_type": "observe",
                    },
                    "hypothesis": {"family": "difference"},
                    "method": "auto",
                },
            )

    def test_alpha_out_of_range_raises(self) -> None:
        """alpha <= 0 or alpha >= 1 → ValueError."""
        session_id = self._make_session()
        obs_a = self._observe_numeric(session_id, self.metric_num_a)
        obs_b = self._observe_numeric(session_id, self.metric_num_b)

        for bad_alpha in (0.0, 1.0, -0.05, 1.5):
            with self.subTest(alpha=bad_alpha), self.assertRaises(ValueError):
                self.service.run_intent(
                    session_id,
                    "test",
                    {
                        "left_ref": {
                            "artifact_id": obs_a["artifact_id"],
                            "observation_type": "numeric_sample_summary",
                            "step_id": obs_a["step_ref"]["step_id"],
                            "step_type": "observe",
                        },
                        "right_ref": {
                            "artifact_id": obs_b["artifact_id"],
                            "observation_type": "numeric_sample_summary",
                            "step_id": obs_b["step_ref"]["step_id"],
                            "step_type": "observe",
                        },
                        "hypothesis": {
                            "family": "difference",
                            "alpha": bad_alpha,
                        },
                        "method": "auto",
                    },
                )

    def test_method_type_mismatch_welch_with_rate_raises(self) -> None:
        """method='welch_t' with rate_sample_summary artifacts → ValueError (NOT_COMPARABLE)."""
        session_id = self._make_session()
        obs_ra = self._observe_rate(session_id, self.metric_rate_a)
        obs_rb = self._observe_rate(session_id, self.metric_rate_b)

        with self.assertRaises(ValueError):
            self.service.run_intent(
                session_id,
                "test",
                {
                    "left_ref": {
                        "artifact_id": obs_ra["artifact_id"],
                        "observation_type": "rate_sample_summary",
                        "step_id": obs_ra["step_ref"]["step_id"],
                        "step_type": "observe",
                    },
                    "right_ref": {
                        "artifact_id": obs_rb["artifact_id"],
                        "observation_type": "rate_sample_summary",
                        "step_id": obs_rb["step_ref"]["step_id"],
                        "step_type": "observe",
                    },
                    "hypothesis": {"family": "difference"},
                    "method": "welch_t",  # explicitly wrong for rate data
                },
            )

    def test_method_type_mismatch_proportion_z_with_numeric_raises(self) -> None:
        """method='two_proportion_z' with numeric_sample_summary artifacts → ValueError."""
        session_id = self._make_session()
        obs_a = self._observe_numeric(session_id, self.metric_num_a)
        obs_b = self._observe_numeric(session_id, self.metric_num_b)

        with self.assertRaises(ValueError):
            self.service.run_intent(
                session_id,
                "test",
                {
                    "left_ref": {
                        "artifact_id": obs_a["artifact_id"],
                        "observation_type": "numeric_sample_summary",
                        "step_id": obs_a["step_ref"]["step_id"],
                        "step_type": "observe",
                    },
                    "right_ref": {
                        "artifact_id": obs_b["artifact_id"],
                        "observation_type": "numeric_sample_summary",
                        "step_id": obs_b["step_ref"]["step_id"],
                        "step_type": "observe",
                    },
                    "hypothesis": {"family": "difference"},
                    "method": "two_proportion_z",  # explicitly wrong for numeric data
                },
            )

    def test_invalid_hypothesis_family_raises(self) -> None:
        """hypothesis.family != 'difference' → ValueError."""
        session_id = self._make_session()
        obs_a = self._observe_numeric(session_id, self.metric_num_a)
        obs_b = self._observe_numeric(session_id, self.metric_num_b)

        with self.assertRaises(ValueError):
            self.service.run_intent(
                session_id,
                "test",
                {
                    "left_ref": {
                        "artifact_id": obs_a["artifact_id"],
                        "observation_type": "numeric_sample_summary",
                        "step_id": obs_a["step_ref"]["step_id"],
                        "step_type": "observe",
                    },
                    "right_ref": {
                        "artifact_id": obs_b["artifact_id"],
                        "observation_type": "numeric_sample_summary",
                        "step_id": obs_b["step_ref"]["step_id"],
                        "step_type": "observe",
                    },
                    "hypothesis": {"family": "equivalence"},  # not supported in v1
                    "method": "auto",
                },
            )

    def test_invalid_alternative_raises(self) -> None:
        """hypothesis.alternative not in {'two_sided', 'greater', 'less'} → ValueError."""
        session_id = self._make_session()
        obs_a = self._observe_numeric(session_id, self.metric_num_a)
        obs_b = self._observe_numeric(session_id, self.metric_num_b)

        with self.assertRaises(ValueError):
            self.service.run_intent(
                session_id,
                "test",
                {
                    "left_ref": {
                        "artifact_id": obs_a["artifact_id"],
                        "observation_type": "numeric_sample_summary",
                        "step_id": obs_a["step_ref"]["step_id"],
                        "step_type": "observe",
                    },
                    "right_ref": {
                        "artifact_id": obs_b["artifact_id"],
                        "observation_type": "numeric_sample_summary",
                        "step_id": obs_b["step_ref"]["step_id"],
                        "step_type": "observe",
                    },
                    "hypothesis": {"family": "difference", "alternative": "not_valid"},
                    "method": "auto",
                },
            )

    def test_missing_step_ids_raises(self) -> None:
        """Both left_ref and right_ref step_id required → ValueError when missing."""
        session_id = self._make_session()
        with self.assertRaises(ValueError):
            self.service.run_intent(
                session_id,
                "test",
                {
                    "left_ref": {
                        "artifact_id": "art_missing_left",
                        "observation_type": "numeric_sample_summary",
                        "step_type": "observe",
                    },  # missing step_id
                    "right_ref": {
                        "artifact_id": "art_missing_right",
                        "observation_type": "numeric_sample_summary",
                        "step_type": "observe",
                    },
                    "hypothesis": {"family": "difference"},
                    "method": "auto",
                },
            )

    # ── Custom alpha ────────────────────────────────────────────────────────────

    def test_custom_alpha_reflected_in_decision(self) -> None:
        """Custom alpha=0.001 may fail to reject when default alpha=0.05 would reject."""
        session_id = self._make_session()
        obs_a = self._observe_numeric(session_id, self.metric_num_a)
        obs_b = self._observe_numeric(session_id, self.metric_num_b)

        result_strict = self.service.run_intent(
            session_id,
            "test",
            {
                "left_ref": {
                    "artifact_id": obs_a["artifact_id"],
                    "observation_type": "numeric_sample_summary",
                    "step_id": obs_a["step_ref"]["step_id"],
                    "step_type": "observe",
                },
                "right_ref": {
                    "artifact_id": obs_b["artifact_id"],
                    "observation_type": "numeric_sample_summary",
                    "step_id": obs_b["step_ref"]["step_id"],
                    "step_type": "observe",
                },
                "hypothesis": {"family": "difference", "alpha": 0.001},
                "method": "auto",
            },
        )

        # p_value is deterministic; result says what it says
        self.assertEqual(result_strict["hypothesis"]["alpha"], 0.001)
        self.assertIn("reject_null", result_strict["decision"])

    # ── Step committed ──────────────────────────────────────────────────────────

    def test_step_committed_after_test(self) -> None:
        """After running test, the step must be retrievable from the session."""
        session_id = self._make_session()
        obs_a = self._observe_numeric(session_id, self.metric_num_a)
        obs_b = self._observe_numeric(session_id, self.metric_num_b)

        result = self._run_test(
            session_id,
            obs_a["artifact_id"],
            obs_a["step_ref"]["step_id"],
            obs_b["artifact_id"],
            obs_b["step_ref"]["step_id"],
        )

        step_id = result["step_ref"]["step_id"]
        rows = self.service.metadata.query_rows(
            "SELECT step_type, summary FROM steps WHERE step_id = ? AND session_id = ?",
            [step_id, session_id],
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["step_type"], "test")
        # Summary must include p-value notation
        self.assertIn("p=", rows[0]["summary"])

    def test_mismatched_artifact_id_raises(self) -> None:
        """Request artifact_id must match the committed upstream artifact lineage."""
        session_id = self._make_session()
        obs_a = self._observe_numeric(session_id, self.metric_num_a)
        obs_b = self._observe_numeric(session_id, self.metric_num_b)

        with self.assertRaises(ValueError):
            self.service.run_intent(
                session_id,
                "test",
                {
                    "left_ref": {
                        "artifact_id": "art_wrong_left",
                        "observation_type": "numeric_sample_summary",
                        "step_id": obs_a["step_ref"]["step_id"],
                        "step_type": "observe",
                    },
                    "right_ref": {
                        "artifact_id": obs_b["artifact_id"],
                        "observation_type": "numeric_sample_summary",
                        "step_id": obs_b["step_ref"]["step_id"],
                        "step_type": "observe",
                    },
                    "hypothesis": {"family": "difference"},
                    "method": "auto",
                },
            )

    def test_lineage_query_hash_uses_resolved_artifact_ids(self) -> None:
        """Result lineage should retain the resolved upstream artifact IDs."""
        session_id = self._make_session()
        obs_a = self._observe_numeric(session_id, self.metric_num_a)
        obs_b = self._observe_numeric(session_id, self.metric_num_b)

        result = self._run_test(
            session_id,
            obs_a["artifact_id"],
            obs_a["step_ref"]["step_id"],
            obs_b["artifact_id"],
            obs_b["step_ref"]["step_id"],
        )

        self.assertEqual(result["left_ref"]["artifact_id"], obs_a["artifact_id"])
        self.assertEqual(result["right_ref"]["artifact_id"], obs_b["artifact_id"])
        self.assertEqual(
            result["source_lineage"]["left_source"]["step_ref"]["artifact_id"],
            obs_a["artifact_id"],
        )
        self.assertEqual(
            result["source_lineage"]["right_source"]["step_ref"]["artifact_id"],
            obs_b["artifact_id"],
        )


# ── HTTP endpoint tests ───────────────────────────────────────────────────────


class TestIntentEndpointTests(unittest.TestCase):
    """HTTP-level tests for /sessions/{id}/intents/test."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "test_http.duckdb"

        analytics = DuckDBAnalyticsEngine(str(db_path))
        get_named_seeded_duckdb_path(db_path, "test_intent")
        analytics.initialize()

        meta_path = db_path.with_suffix(".meta.sqlite")
        metadata = SQLiteMetadataStore(str(meta_path))
        metadata.initialize()

        with analytics._connect() as con:
            # Verify within same connection
            r1 = con.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = 'analytics' AND table_name = 'test_numeric_a'"
            ).fetchone()
            assert r1 and r1[0] == 1, f"Table not found within seeding connection: {r1}"

        # Verify in a new connection (before create_app)
        with analytics._connect() as con:
            r2 = con.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = 'analytics' AND table_name = 'test_numeric_a'"
            ).fetchone()
            assert r2 and r2[0] == 1, f"Table not found in new connection before create_app: {r2}"

        _seed_metric(
            metadata,
            suffix="httpnuma",
            metric_name="http_test_num_a",
            table_fqn="analytics.test_numeric_a",
            native_name="test_numeric_a",
            definition_sql="response_time",
        )
        _seed_metric(
            metadata,
            suffix="httpnumb",
            metric_name="http_test_num_b",
            table_fqn="analytics.test_numeric_b",
            native_name="test_numeric_b",
            definition_sql="response_time",
        )

        cls.client = TestClient(
            create_app(db_path=db_path, metadata_store=metadata, analytics_engine=analytics)
        )

        # Create session + run two observe intents to get valid step refs
        r = cls.client.post("/sessions", json={"goal": "test HTTP endpoint test"})
        assert r.status_code == 200, r.text
        cls.session_id = r.json()["session_id"]

        time_scope = {"kind": "range", "start": _TIME_START, "end": _TIME_END}

        r_a = cls.client.post(
            f"/sessions/{cls.session_id}/intents/observe",
            json={
                "metric": _metric_ref("http_test_num_a"),
                "result_mode": "numeric_sample_summary",
                "time_scope": time_scope,
            },
        )
        assert r_a.status_code == 200, r_a.text
        cls.left_artifact_id = r_a.json()["artifact_id"]
        cls.left_step_id = r_a.json()["step_ref"]["step_id"]

        r_b = cls.client.post(
            f"/sessions/{cls.session_id}/intents/observe",
            json={
                "metric": _metric_ref("http_test_num_b"),
                "result_mode": "numeric_sample_summary",
                "time_scope": time_scope,
            },
        )
        assert r_b.status_code == 200, r_b.text
        cls.right_artifact_id = r_b.json()["artifact_id"]
        cls.right_step_id = r_b.json()["step_ref"]["step_id"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def _valid_body(self, **overrides: object) -> dict:
        body = {
            "left_ref": {
                "artifact_id": self.left_artifact_id,
                "observation_type": "numeric_sample_summary",
                "step_id": self.left_step_id,
                "step_type": "observe",
            },
            "right_ref": {
                "artifact_id": self.right_artifact_id,
                "observation_type": "numeric_sample_summary",
                "step_id": self.right_step_id,
                "step_type": "observe",
            },
            "hypothesis": {"family": "difference"},
            "method": "auto",
        }
        body.update(overrides)
        return body

    def test_valid_test_returns_200(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/test",
            json=self._valid_body(),
        )
        self.assertEqual(r.status_code, 200, msg=r.text)
        body = r.json()
        self.assertEqual(body["result_type"], "hypothesis_test")
        self.assertNotIn("artifact_type", body)
        self.assertIn("p_value", body)
        self.assertIn("decision", body)

    def test_missing_left_ref_returns_422(self) -> None:
        body = self._valid_body()
        del body["left_ref"]
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/test",
            json=body,
        )
        self.assertEqual(r.status_code, 422)

    def test_missing_right_ref_returns_422(self) -> None:
        body = self._valid_body()
        del body["right_ref"]
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/test",
            json=body,
        )
        self.assertEqual(r.status_code, 422)

    def test_nonexistent_session_returns_404(self) -> None:
        r = self.client.post(
            "/sessions/sess_does_not_exist/intents/test",
            json=self._valid_body(),
        )
        self.assertEqual(r.status_code, 404)

    def test_nonexistent_step_ref_returns_422(self) -> None:
        body = self._valid_body()
        body["left_ref"] = {
            "artifact_id": "art_nonexistent_xyz",
            "observation_type": "numeric_sample_summary",
            "step_id": "obs_nonexistent_xyz",
            "step_type": "observe",
        }
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/test",
            json=body,
        )
        self.assertEqual(r.status_code, 422)

    def test_missing_artifact_id_returns_422(self) -> None:
        body = self._valid_body()
        del body["left_ref"]["artifact_id"]
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/test",
            json=body,
        )
        self.assertEqual(r.status_code, 422)

    def test_missing_observation_type_returns_422(self) -> None:
        body = self._valid_body()
        del body["right_ref"]["observation_type"]
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/test",
            json=body,
        )
        self.assertEqual(r.status_code, 422)

    def test_invalid_alpha_returns_422(self) -> None:
        body = self._valid_body()
        body["hypothesis"] = {"family": "difference", "alpha": 1.5}
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/test",
            json=body,
        )
        self.assertEqual(r.status_code, 422)

    def test_invalid_method_returns_422(self) -> None:
        body = self._valid_body()
        body["method"] = "chi_square"  # not in enum
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/test",
            json=body,
        )
        self.assertEqual(r.status_code, 422)
