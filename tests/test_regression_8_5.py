"""Phase 8.5 Regression Tests.

Guards against regressions in:
  - semantic layer basic capabilities
  - query router
  - compiler/executor
  - governance checks
  - critical `observe` path execution consistency

These tests are specifically designed to catch regressions introduced by the
bulk deletion of legacy evidence engine components and step runner consolidation:
  - correlate_metrics step type removed from taxonomy
  - synthesize_findings step type removed from taxonomy / step runner registry
  - correlation math moved from deleted causal_checkers.py → intents/correlate.py
  - evidence_edges catalog graph traversal removed
  - EvidencePipeline / DefaultClaimSynthesizer removed from service wiring
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.analysis_core import (
    COMPOSITE_STEP_TYPES,
    STEP_TAXONOMY,
    SUPPORTED_STEP_TYPES,
)
from app.analysis_core.compiler import compile_step
from app.analysis_core.executor import execute_compiled
from app.analysis_core.ir import STEP_ARTIFACT_KINDS, STEP_OBSERVATION_TYPES, AnalysisStepIR
from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
from app.storage.sqlite_metadata import SQLiteMetadataStore
from tests.semantic_test_helpers import (
    build_semantic_layer_service,
    ensure_published_typed_metric,
    ensure_published_typed_metric_binding,
    seed_duckdb_source_object,
)
from tests.shared_fixtures import get_named_seeded_duckdb_path

# ---------------------------------------------------------------------------
# Shared helper: minimal metadata + analytics engine seeder
# ---------------------------------------------------------------------------

_METRIC = "reg_revenue"
_TABLE = "reg_events"


def _seed_metadata(meta: SQLiteMetadataStore, db_path: Path | None = None) -> None:
    """Insert the minimal metadata rows for observe path resolution.

    Pass ``db_path`` to populate the engine's connection JSON so that
    QueryRouter.resolve_engine_for_tables can build a real analytics engine.
    """
    now = datetime.now(UTC).isoformat()
    src_id = "src_reg8501"
    obj_id = "obj_reg8501"

    seed_duckdb_source_object(
        meta,
        source_id=src_id,
        object_id=obj_id,
        display_name="Reg8.5 Source",
        table_name=_TABLE,
        table_fqn=f"analytics.{_TABLE}",
        now=now,
        db_path=db_path,
    )
    ensure_published_typed_metric(
        meta,
        metric_name=_METRIC,
        display_name=_METRIC,
        grain="day",
        dimensions=["event_date", "region"],
        definition_sql="SUM(value)",
        measure_type="sum",
    )
    binding_ref = ensure_published_typed_metric_binding(
        meta,
        metric_name=_METRIC,
        carrier_locator=f"analytics.{_TABLE}",
        source_object_ref=obj_id,
        dimension_names=["event_date", "region"],
    )
    binding_row = meta.query_one(
        "SELECT binding_id FROM typed_bindings WHERE binding_ref = ?",
        [binding_ref],
    )
    if binding_row is not None:
        meta.execute(
            """
            UPDATE field_bindings
            SET target_key = ?, semantic_ref = ?
            WHERE binding_id = ? AND target_kind = 'metric_input'
            """,
            ["measure", "metric_input.measure", binding_row["binding_id"]],
        )
    if db_path is not None:
        meta.execute(
            "UPDATE datasources SET connection_json = ? WHERE datasource_id = ?",
            [json.dumps({"path": str(db_path), "catalog": "main"}), src_id],
        )


class _RegressionServiceTestCase(unittest.TestCase):
    """Shared service setup for regression tests backed by a prepared DuckDB."""

    duckdb_filename = "reg85.duckdb"
    metadata_filename = "reg85.meta.sqlite"
    duckdb_template = "default"
    seed_metadata_db_path = False

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / cls.duckdb_filename
        meta_path = Path(cls.temp_dir.name) / cls.metadata_filename

        get_named_seeded_duckdb_path(db_path, cls.duckdb_template)
        cls.analytics = DuckDBAnalyticsEngine(str(db_path))
        cls.metadata = SQLiteMetadataStore(str(meta_path))
        cls.metadata.initialize()
        cls.analytics.initialize()
        _seed_metadata(cls.metadata, db_path=db_path if cls.seed_metadata_db_path else None)
        cls.service = build_semantic_layer_service(cls.metadata, cls.analytics)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()


# ---------------------------------------------------------------------------
# 1. Taxonomy Integrity
# ---------------------------------------------------------------------------


class TaxonomyIntegrityTests(unittest.TestCase):
    """Verify that removed step types are cleanly absent and existing ones intact."""

    def test_composite_step_types_is_empty(self) -> None:
        """synthesize_findings was the sole composite; its removal leaves COMPOSITE_STEP_TYPES empty."""
        self.assertEqual(len(COMPOSITE_STEP_TYPES), 0)

    def test_expected_primitive_step_types_present(self) -> None:
        required = {
            "metric_query",
            "sample_rows",
            "aggregate_query",
            "attribute_change",
            "profile_table",
        }
        self.assertTrue(required.issubset(set(SUPPORTED_STEP_TYPES)))

    def test_all_taxonomy_entries_have_description(self) -> None:
        for step_type, meta in STEP_TAXONOMY.items():
            self.assertTrue(meta.get("description"), f"{step_type} missing description")

    def test_compile_step_rejects_correlate_metrics(self) -> None:
        """compile_step must raise ValueError for removed step type."""
        with self.assertRaises(ValueError):
            compile_step(
                AnalysisStepIR(index=0, step_type="correlate_metrics", params={}),
                engine_type="duckdb",
            )

    def test_compile_step_rejects_synthesize_findings(self) -> None:
        """compile_step must raise ValueError for removed step type."""
        with self.assertRaises(ValueError):
            compile_step(
                AnalysisStepIR(index=0, step_type="synthesize_findings", params={}),
                engine_type="duckdb",
            )

    def test_removed_step_types_absent_from_registry_surfaces(self) -> None:
        self.assertNotIn("correlate_metrics", STEP_TAXONOMY)
        self.assertNotIn("synthesize_findings", STEP_TAXONOMY)
        self.assertNotIn("synthesize_findings", STEP_OBSERVATION_TYPES)
        self.assertNotIn("correlate_metrics", STEP_ARTIFACT_KINDS)
        self.assertNotIn("synthesize_findings", STEP_ARTIFACT_KINDS)
        self.assertNotIn("correlate_metrics", SUPPORTED_STEP_TYPES)
        self.assertNotIn("synthesize_findings", SUPPORTED_STEP_TYPES)


# ---------------------------------------------------------------------------
# 2. Compiler / Executor Consistency
# ---------------------------------------------------------------------------


class CompilerRegressionTests(unittest.TestCase):
    """Pin compiler output shape for the core metric_query path."""

    def test_metric_query_produces_delta_pct_and_sessions_columns(self) -> None:
        compiled = compile_step(
            AnalysisStepIR(
                index=0,
                step_type="metric_query",
                params={"metric": "revenue", "table": "analytics.reg_events"},
            ),
            engine_type="duckdb",
            semantic_context={
                "metric_sql": "SUM(value)",
                "dimensions": ["region"],
                "period_params": ["c1", "c2", "b1", "b2", "b1", "c2"],
            },
        )
        self.assertIn("delta_pct", compiled.sql)
        self.assertIn("current_value", compiled.sql)
        self.assertIn("baseline_value", compiled.sql)
        self.assertIn("analytics.reg_events", compiled.sql)
        self.assertEqual(compiled.metadata["engine_type"], "duckdb")

    def test_sample_rows_produces_limit_clause(self) -> None:
        compiled = compile_step(
            AnalysisStepIR(
                index=0,
                step_type="sample_rows",
                params={"table_name": "analytics.reg_events", "limit": 3},
            ),
            engine_type="duckdb",
        )
        self.assertIn("LIMIT 3", compiled.sql)
        self.assertIn("analytics.reg_events", compiled.sql)

    def test_executor_translates_duckdb_cast_syntax_for_trino(self) -> None:
        class _FakeEngine:
            def query_rows(self, sql: str, params=None) -> list:
                self.last_sql = sql
                return []

        engine = _FakeEngine()
        compiled = compile_step(
            AnalysisStepIR(
                index=0,
                step_type="sample_rows",
                params={"table_name": "analytics.reg_events", "limit": 1},
            ),
            engine_type="trino",
        )
        # Inject a DuckDB-style cast to trigger translation
        compiled.sql = "SELECT value::DOUBLE FROM analytics.reg_events LIMIT 1"
        execute_compiled(engine, compiled)
        self.assertIn("CAST(value AS DOUBLE)", engine.last_sql)


# ---------------------------------------------------------------------------
# 3. Correlation Math (moved from deleted causal_checkers.py)
# ---------------------------------------------------------------------------


class CorrelationMathRegressionTests(unittest.TestCase):
    """Verify that Pearson / Spearman helpers in intents.correlate still produce
    correct results after being moved from the deleted causal_checkers module.
    """

    def setUp(self) -> None:
        from app.intents.correlate import _pearson_correlation, _spearman_correlation

        self._pearson = _pearson_correlation
        self._spearman = _spearman_correlation

    def test_pearson_perfect_positive_correlation(self) -> None:
        x = [1.0, 2.0, 3.0, 4.0, 5.0]
        y = [2.0, 4.0, 6.0, 8.0, 10.0]
        self.assertAlmostEqual(self._pearson(x, y), 1.0, places=10)

    def test_pearson_perfect_negative_correlation(self) -> None:
        x = [1.0, 2.0, 3.0, 4.0, 5.0]
        y = [10.0, 8.0, 6.0, 4.0, 2.0]
        self.assertAlmostEqual(self._pearson(x, y), -1.0, places=10)

    def test_pearson_zero_variance_returns_zero(self) -> None:
        x = [5.0, 5.0, 5.0]
        y = [1.0, 2.0, 3.0]
        self.assertEqual(self._pearson(x, y), 0.0)

    def test_pearson_insufficient_pairs_returns_zero(self) -> None:
        self.assertEqual(self._pearson([1.0], [2.0]), 0.0)
        self.assertEqual(self._pearson([], []), 0.0)

    def test_spearman_monotone_positive_correlation(self) -> None:
        x = [1.0, 2.0, 3.0, 4.0, 5.0]
        y = [10.0, 30.0, 20.0, 40.0, 50.0]  # monotone overall
        result = self._spearman(x, y)
        # Not perfect, but should be positive
        self.assertGreater(result, 0.0)

    def test_spearman_perfect_rank_agreement(self) -> None:
        x = [1.0, 2.0, 3.0, 4.0, 5.0]
        y = [5.0, 10.0, 15.0, 20.0, 25.0]
        self.assertAlmostEqual(self._spearman(x, y), 1.0, places=10)

    def test_spearman_handles_ties_without_error(self) -> None:
        x = [1.0, 1.0, 2.0, 3.0]
        y = [2.0, 2.0, 4.0, 6.0]
        result = self._spearman(x, y)
        self.assertIsInstance(result, float)
        self.assertGreater(result, 0.0)


# ---------------------------------------------------------------------------
# 4. Observe Path End-to-End
# ---------------------------------------------------------------------------


class ObservePathRegressionTests(_RegressionServiceTestCase):
    """Verify the observe intent produces a well-formed artifact against a real
    DuckDB engine. This guards against regressions in the observe execution path
    after removal of the legacy EvidencePipeline / DefaultClaimSynthesizer wiring.
    """

    duckdb_template = "regression_8_5"
    seed_metadata_db_path = True

    def _session(self) -> str:
        return self.service.create_session("reg8.5 observe", {}, {}, {})["session_id"]

    def test_observe_scalar_artifact_has_correct_observation_type(self) -> None:
        sid = self._session()
        result = self.service.run_intent(
            sid,
            "observe",
            {
                "metric": _METRIC,
                "time_scope": {"kind": "range", "start": "2026-03-01", "end": "2026-03-08"},
            },
        )
        art_id = result["artifact_id"]
        rows = self.metadata.query_rows(
            "SELECT content_json FROM artifacts WHERE artifact_id = ?", [art_id]
        )
        self.assertEqual(len(rows), 1)
        content = json.loads(rows[0]["content_json"])
        self.assertEqual(content["observation_type"], "scalar")
        self.assertEqual(content["metric"], _METRIC)

    def test_observe_time_series_produces_artifact(self) -> None:
        sid = self._session()
        result = self.service.run_intent(
            sid,
            "observe",
            {
                "metric": _METRIC,
                "time_scope": {"kind": "range", "start": "2026-03-01", "end": "2026-03-08"},
                "granularity": "day",
            },
        )
        self.assertIn("artifact_id", result)
        art_id = result["artifact_id"]
        rows = self.metadata.query_rows(
            "SELECT content_json FROM artifacts WHERE artifact_id = ?", [art_id]
        )
        content = json.loads(rows[0]["content_json"])
        self.assertEqual(content["observation_type"], "time_series")

    def test_observe_segmented_produces_artifact(self) -> None:
        sid = self._session()
        result = self.service.run_intent(
            sid,
            "observe",
            {
                "metric": _METRIC,
                "time_scope": {"kind": "range", "start": "2026-03-01", "end": "2026-03-08"},
                "dimensions": ["region"],
            },
        )
        self.assertIn("artifact_id", result)
        art_id = result["artifact_id"]
        rows = self.metadata.query_rows(
            "SELECT content_json FROM artifacts WHERE artifact_id = ?", [art_id]
        )
        content = json.loads(rows[0]["content_json"])
        self.assertEqual(content["observation_type"], "segmented")

    def test_observe_step_is_committed_to_steps_table(self) -> None:
        sid = self._session()
        self.service.run_intent(
            sid,
            "observe",
            {
                "metric": _METRIC,
                "time_scope": {"kind": "range", "start": "2026-03-01", "end": "2026-03-08"},
            },
        )
        rows = self.metadata.query_rows("SELECT step_type FROM steps WHERE session_id = ?", [sid])
        step_types = [r["step_type"] for r in rows]
        self.assertIn("observe", step_types)

    def test_observe_unknown_metric_raises(self) -> None:
        sid = self._session()
        with self.assertRaises(Exception):
            self.service.run_intent(
                sid,
                "observe",
                {
                    "metric": "nonexistent_metric_xyz",
                    "time_scope": {"kind": "range", "start": "2026-03-01", "end": "2026-03-08"},
                },
            )

    def test_run_step_rejects_removed_step_types(self) -> None:
        sid = self._session()
        for removed in ("correlate_metrics", "synthesize_findings"):
            with self.assertRaises(Exception, msg=f"{removed} should be rejected"):
                self.service.run_step(sid, removed)


# ---------------------------------------------------------------------------
# 5. Semantic Layer Non-Regression
# ---------------------------------------------------------------------------


class SemanticLayerRegressionTests(_RegressionServiceTestCase):
    """Basic semantic resolution capabilities must not regress."""

    duckdb_filename = "reg85_sem.duckdb"
    metadata_filename = "reg85_sem.meta.sqlite"
    duckdb_template = "regression_8_5"

    def test_resolve_metric_dimensions_returns_list(self) -> None:
        dims = self.service.resolve_metric_dimensions(_METRIC)
        self.assertIsNotNone(dims)
        assert dims is not None
        self.assertIn("region", dims)

    def test_resolve_unknown_metric_returns_none(self) -> None:
        sql = self.service.resolve_metric_sql("totally_nonexistent_metric_xyz")
        self.assertIsNone(sql)

    def test_semantic_resolver_resolves_published_metric(self) -> None:
        resolved = self.service.semantic_resolver.resolve_metric(_METRIC)
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.definition_sql, "SUM(value)")
        self.assertIn("region", resolved.dimensions)


@pytest.mark.slow
class TypedMetricSqlCompilationTests(_RegressionServiceTestCase):
    duckdb_filename = "typed_metric_sql.duckdb"
    metadata_filename = "typed_metric_sql.meta.sqlite"
    duckdb_template = "regression_8_5"
    seed_metadata_db_path = True

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()

        source_object = cls.metadata.query_one(
            """
            SELECT object_id
            FROM source_objects
            WHERE object_type = 'table' AND fqn = ?
            ORDER BY updated_at DESC, object_id
            LIMIT 1
            """,
            [f"analytics.{_TABLE}"],
        )
        assert source_object is not None
        source_object_ref = str(source_object["object_id"])

        typed_metrics = [
            ("typed_count_distinct", "count", ["count_target"], {"count_target": "field.user_id"}),
            ("typed_sum_value", "sum", ["measure"], {"measure": "field.value"}),
            (
                "typed_average_value",
                "average",
                ["numerator", "denominator"],
                {"numerator": "field.value", "denominator": "field.user_id"},
            ),
            (
                "typed_rate_value",
                "rate",
                ["numerator", "denominator"],
                {"numerator": "field.numerator", "denominator": "field.denominator"},
            ),
            (
                "typed_p95_value",
                "percentile",
                ["value_component"],
                {"value_component": "field.value"},
            ),
        ]

        for metric_name, measure_type, input_keys, surface_map in typed_metrics:
            ensure_published_typed_metric(
                cls.metadata,
                metric_name=metric_name,
                display_name=metric_name,
                grain="day",
                dimensions=["region"],
                measure_type=measure_type,
            )
            binding_ref = ensure_published_typed_metric_binding(
                cls.metadata,
                metric_name=metric_name,
                carrier_locator=f"analytics.{_TABLE}",
                source_object_ref=source_object_ref,
                metric_input_target_keys=input_keys,
            )
            binding_row = cls.metadata.query_one(
                "SELECT binding_id FROM typed_bindings WHERE binding_ref = ?",
                [binding_ref],
            )
            assert binding_row is not None
            for target_key, surface_ref in surface_map.items():
                cls.metadata.execute(
                    """
                    UPDATE field_bindings
                    SET surface_ref = ?, semantic_ref = ?
                    WHERE binding_id = ? AND target_kind = 'metric_input' AND target_key = ?
                    """,
                    [
                        surface_ref,
                        f"metric_input.{target_key}",
                        binding_row["binding_id"],
                        target_key,
                    ],
                )
            if metric_name == "typed_count_distinct":
                metric_row = cls.metadata.query_one(
                    """
                    SELECT metric_contract_id, family_payload_json, additivity_constraints_json
                    FROM semantic_metric_contracts
                    WHERE metric_ref = ?
                    """,
                    [f"metric.{metric_name}"],
                )
                assert metric_row is not None
                family_payload = json.loads(metric_row["family_payload_json"] or "{}")
                family_payload["count_target"]["aggregation"] = "count_distinct"
                constraints = json.loads(metric_row["additivity_constraints_json"] or "{}")
                constraints["dimension_policy"] = "none"
                constraints["time_axis_policy"] = "non_additive"
                cls.metadata.execute(
                    """
                    UPDATE semantic_metric_contracts
                    SET family_payload_json = ?, additivity_constraints_json = ?
                    WHERE metric_contract_id = ?
                    """,
                    [
                        json.dumps(family_payload),
                        json.dumps(constraints),
                        metric_row["metric_contract_id"],
                    ],
                )

        cls.service = build_semantic_layer_service(cls.metadata, cls.analytics)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_count_metric_compiles_to_count_distinct(self) -> None:
        self.assertEqual(
            self.service.resolve_metric_sql("metric.typed_count_distinct"),
            "COUNT(DISTINCT user_id)",
        )

    def test_sum_metric_compiles_to_sum(self) -> None:
        self.assertEqual(self.service.resolve_metric_sql("metric.typed_sum_value"), "SUM(value)")
        self.assertEqual(self.service.resolve_metric_value_sql("metric.typed_sum_value"), "value")

    def test_average_metric_compiles_to_safe_ratio(self) -> None:
        self.assertEqual(
            self.service.resolve_metric_sql("metric.typed_average_value"),
            "SUM(value) / NULLIF(COUNT(user_id), 0)",
        )
        self.assertEqual(
            self.service.resolve_metric_value_sql("metric.typed_average_value"), "value"
        )

    def test_rate_metric_compiles_to_safe_ratio(self) -> None:
        self.assertEqual(
            self.service.resolve_metric_sql("metric.typed_rate_value"),
            "SUM(numerator) / NULLIF(SUM(denominator), 0)",
        )
        self.assertIsNone(self.service.resolve_metric_value_sql("metric.typed_rate_value"))

    def test_distribution_metric_compiles_to_duckdb_quantile_for_execution(self) -> None:
        execution_context = self.service._resolve_metric_execution_context("metric.typed_p95_value")
        self.assertEqual(
            self.service.resolve_metric_sql_for_execution(
                "metric.typed_p95_value",
                execution_context,
                engine_type="duckdb",
            ),
            "QUANTILE_CONT(value, 0.95)",
        )


if __name__ == "__main__":
    unittest.main()
