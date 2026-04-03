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
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb

from app.analysis_core import (
    COMPOSITE_STEP_TYPES,
    STEP_TAXONOMY,
    SUPPORTED_STEP_TYPES,
)
from app.analysis_core.compiler import compile_step
from app.analysis_core.executor import execute_compiled
from app.analysis_core.ir import STEP_ARTIFACT_KINDS, STEP_OBSERVATION_TYPES, AnalysisStepIR
from app.service import SemanticLayerService
from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
from app.storage.sqlite_metadata import SQLiteMetadataStore

# ---------------------------------------------------------------------------
# Shared helper: minimal metadata + analytics engine seeder
# ---------------------------------------------------------------------------

_METRIC = "reg_revenue"
_TABLE = "reg_events"


def _seed_duckdb(db_path: Path) -> None:
    """Seed a minimal DuckDB table used by the observe path tests."""
    con = duckdb.connect(str(db_path))
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS analytics.reg_events (
                event_date DATE    NOT NULL,
                region     VARCHAR NOT NULL,
                value      DOUBLE  NOT NULL
            )
            """
        )
        rows = []
        base = datetime(2026, 3, 1).date()
        for i in range(7):
            d = (base + timedelta(days=i)).isoformat()
            rows.append((d, "us", float(100 + i * 10)))
            rows.append((d, "eu", float(80 + i * 5)))
        con.executemany("INSERT INTO analytics.reg_events VALUES (?, ?, ?)", rows)
    finally:
        con.close()


def _seed_metadata(meta: SQLiteMetadataStore, db_path: Path | None = None) -> None:
    """Insert the minimal metadata rows for observe path resolution.

    Pass ``db_path`` to populate the engine's connection JSON so that
    QueryRouter.resolve_engine_for_tables can build a real analytics engine.
    """
    now = datetime.now(UTC).isoformat()
    src_id = "src_reg8501"
    obj_id = "obj_reg8501"
    met_id = "met_reg8501"
    eng_id = "eng_reg8501"
    bind_id = "bind_reg8501"
    engine_conn = json.dumps({"path": str(db_path)}) if db_path is not None else "{}"

    meta.execute(
        "INSERT OR IGNORE INTO sources "
        "(source_id, source_type, display_name, connection_json, capabilities_json, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [src_id, "duckdb", "Reg8.5 Source", "{}", "{}", now, now],
    )
    meta.execute(
        "INSERT OR IGNORE INTO source_objects "
        "(object_id, source_id, object_type, native_name, fqn, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [obj_id, src_id, "table", _TABLE, f"analytics.{_TABLE}", now, now],
    )
    meta.execute(
        "INSERT OR IGNORE INTO semantic_metrics "
        "(metric_id, name, display_name, description, definition_sql, dimensions_json, "
        " status, grain, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            met_id,
            _METRIC,
            _METRIC,
            "",
            "SUM(value)",
            json.dumps(["region"]),
            "published",
            "day",
            now,
            now,
        ],
    )
    meta.execute(
        "INSERT OR IGNORE INTO semantic_mappings "
        "(mapping_id, semantic_type, semantic_id, object_id, mapping_type, mapping_json, "
        " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ["map_reg8501", "metric", met_id, obj_id, "primary", "{}", now, now],
    )
    meta.execute(
        "INSERT OR IGNORE INTO engines "
        "(engine_id, engine_type, display_name, connection_json, capabilities_json, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [eng_id, "duckdb", "Reg8.5 Engine", engine_conn, "{}", now, now],
    )
    meta.execute(
        "INSERT OR IGNORE INTO source_engine_bindings "
        "(binding_id, source_id, engine_id, priority, status, namespace_json, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [bind_id, src_id, eng_id, 5, "active", "{}", now, now],
    )


# ---------------------------------------------------------------------------
# 1. Taxonomy Integrity
# ---------------------------------------------------------------------------


class TaxonomyIntegrityTests(unittest.TestCase):
    """Verify that removed step types are cleanly absent and existing ones intact."""

    def test_correlate_metrics_not_in_supported_step_types(self) -> None:
        self.assertNotIn("correlate_metrics", SUPPORTED_STEP_TYPES)

    def test_synthesize_findings_not_in_supported_step_types(self) -> None:
        self.assertNotIn("synthesize_findings", SUPPORTED_STEP_TYPES)

    def test_correlate_metrics_not_in_taxonomy(self) -> None:
        self.assertNotIn("correlate_metrics", STEP_TAXONOMY)

    def test_synthesize_findings_not_in_taxonomy(self) -> None:
        self.assertNotIn("synthesize_findings", STEP_TAXONOMY)

    def test_synthesize_findings_not_in_step_observation_types(self) -> None:
        self.assertNotIn("synthesize_findings", STEP_OBSERVATION_TYPES)

    def test_correlate_metrics_not_in_step_artifact_kinds(self) -> None:
        self.assertNotIn("correlate_metrics", STEP_ARTIFACT_KINDS)

    def test_synthesize_findings_not_in_step_artifact_kinds(self) -> None:
        self.assertNotIn("synthesize_findings", STEP_ARTIFACT_KINDS)

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


class ObservePathRegressionTests(unittest.TestCase):
    """Verify the observe intent produces a well-formed artifact against a real
    DuckDB engine. This guards against regressions in the observe execution path
    after removal of the legacy EvidencePipeline / DefaultClaimSynthesizer wiring.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "reg85.duckdb"
        meta_path = Path(cls.temp_dir.name) / "reg85.meta.sqlite"

        cls.analytics = DuckDBAnalyticsEngine(str(db_path))
        cls.metadata = SQLiteMetadataStore(str(meta_path))
        cls.metadata.initialize()
        cls.analytics.initialize()

        _seed_duckdb(db_path)
        _seed_metadata(cls.metadata)

        cls.service = SemanticLayerService(cls.metadata, cls.analytics)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def _session(self) -> str:
        return self.service.create_session("reg8.5 observe", {}, {}, {})["session_id"]

    def test_observe_scalar_produces_artifact(self) -> None:
        sid = self._session()
        result = self.service.run_intent(
            sid,
            "observe",
            {
                "metric": _METRIC,
                "time_scope": {"kind": "range", "start": "2026-03-01", "end": "2026-03-08"},
            },
        )
        self.assertIn("artifact_id", result)
        self.assertIsNotNone(result["artifact_id"])

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
# 5. Step Runner Registry Wiring
# ---------------------------------------------------------------------------


class StepRunnerRegistryRegressionTests(unittest.TestCase):
    """Verify that the step runner registry no longer contains removed runners
    and that existing runners are still wired correctly.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "reg85_reg.duckdb"
        meta_path = Path(cls.temp_dir.name) / "reg85_reg.meta.sqlite"
        cls.analytics = DuckDBAnalyticsEngine(str(db_path))
        cls.metadata = SQLiteMetadataStore(str(meta_path))
        cls.metadata.initialize()
        cls.analytics.initialize()
        cls.service = SemanticLayerService(cls.metadata, cls.analytics)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_correlate_metrics_not_in_registry(self) -> None:
        supported = self.service.step_registry.supported_step_types()
        self.assertNotIn("correlate_metrics", supported)

    def test_synthesize_findings_not_in_registry(self) -> None:
        supported = self.service.step_registry.supported_step_types()
        self.assertNotIn("synthesize_findings", supported)

    def test_metric_query_is_in_registry(self) -> None:
        supported = self.service.step_registry.supported_step_types()
        self.assertIn("metric_query", supported)

    def test_sample_rows_is_in_registry(self) -> None:
        supported = self.service.step_registry.supported_step_types()
        self.assertIn("sample_rows", supported)

    def test_attribute_change_is_in_registry(self) -> None:
        supported = self.service.step_registry.supported_step_types()
        self.assertIn("attribute_change", supported)


# ---------------------------------------------------------------------------
# 6. Semantic Layer Non-Regression
# ---------------------------------------------------------------------------


class SemanticLayerRegressionTests(unittest.TestCase):
    """Basic semantic resolution capabilities must not regress."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "reg85_sem.duckdb"
        meta_path = Path(cls.temp_dir.name) / "reg85_sem.meta.sqlite"
        cls.analytics = DuckDBAnalyticsEngine(str(db_path))
        cls.metadata = SQLiteMetadataStore(str(meta_path))
        cls.metadata.initialize()
        cls.analytics.initialize()
        _seed_duckdb(db_path)
        _seed_metadata(cls.metadata)
        cls.service = SemanticLayerService(cls.metadata, cls.analytics)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_resolve_metric_sql_returns_expected_expression(self) -> None:
        sql = self.service.resolve_metric_sql(_METRIC)
        self.assertEqual(sql, "SUM(value)")

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


# ---------------------------------------------------------------------------
# 7. Query Router Non-Regression
# ---------------------------------------------------------------------------


class QueryRouterRegressionTests(unittest.TestCase):
    """QueryRouter must still resolve table → engine after routing module cleanup."""

    @classmethod
    def setUpClass(cls) -> None:
        from app.engines import EngineService
        from app.routing import QueryRouter

        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "reg85_router.duckdb"
        meta_path = Path(cls.temp_dir.name) / "reg85_router.meta.sqlite"
        analytics = DuckDBAnalyticsEngine(str(db_path))
        cls.metadata = SQLiteMetadataStore(str(meta_path))
        cls.metadata.initialize()
        analytics.initialize()
        _seed_duckdb(db_path)
        _seed_metadata(cls.metadata, db_path=db_path)
        cls.engine_service = EngineService(cls.metadata)
        cls.router = QueryRouter(cls.metadata, cls.engine_service)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_resolve_known_table_returns_engine(self) -> None:
        engine = self.router.resolve_engine_for_tables([_TABLE])
        self.assertIsNotNone(engine)

    def test_resolve_unknown_table_raises(self) -> None:
        with self.assertRaises((KeyError, ValueError)):
            self.router.resolve_engine_for_tables(["totally_nonexistent_table_xyz"])


# ---------------------------------------------------------------------------
# 8. Governance Non-Regression
# ---------------------------------------------------------------------------


class GovernanceRegressionTests(unittest.TestCase):
    """Core governance policy lifecycle must be unaffected by the cleanup."""

    @classmethod
    def setUpClass(cls) -> None:
        from app.governance import GovernanceService

        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "reg85_gov.duckdb"
        meta_path = Path(cls.temp_dir.name) / "reg85_gov.meta.sqlite"
        analytics = DuckDBAnalyticsEngine(str(db_path))
        cls.metadata = SQLiteMetadataStore(str(meta_path))
        cls.metadata.initialize()
        analytics.initialize()
        cls.gov = GovernanceService(cls.metadata, analytics)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_create_aggregate_only_policy(self) -> None:
        pol = self.gov.create_policy("reg85_agg_only", "aggregate_only")
        self.assertTrue(pol["policy_id"].startswith("pol_"))
        self.assertEqual(pol["policy_type"], "aggregate_only")
        self.assertTrue(pol["enabled"])

    def test_create_field_mask_policy(self) -> None:
        pol = self.gov.create_policy(
            "reg85_field_mask", "field_mask", definition={"fields": ["email"]}
        )
        self.assertEqual(pol["definition"]["fields"], ["email"])

    def test_invalid_policy_type_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.gov.create_policy("bad_pol", "nonexistent_type")

    def test_list_and_get_policy_round_trip(self) -> None:
        pol = self.gov.create_policy(
            "reg85_max_rows", "max_rows", definition={"max_rows_scanned": 500}
        )
        fetched = self.gov.get_policy(pol["policy_id"])
        self.assertEqual(fetched["name"], "reg85_max_rows")
        self.assertEqual(fetched["definition"]["max_rows_scanned"], 500)

    def test_disable_and_reenable_policy(self) -> None:
        pol = self.gov.create_policy("reg85_toggle", "row_filter")
        updated = self.gov.update_policy(pol["policy_id"], enabled=False)
        self.assertFalse(updated["enabled"])
        reenabled = self.gov.update_policy(pol["policy_id"], enabled=True)
        self.assertTrue(reenabled["enabled"])


if __name__ == "__main__":
    unittest.main()
