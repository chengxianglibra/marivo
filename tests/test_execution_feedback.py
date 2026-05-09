from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any, ClassVar, cast

from fastapi.testclient import TestClient

from marivo.adapters.server.routing_runtime import RoutingRuntime
from marivo.api.app_factory import create_app
from marivo.contracts.errors import ExecutionError
from marivo.core.semantic.compiler import (
    CompiledQuery,
    SemanticCompilerError,
    SemanticRequestCompatibilityError,
)
from marivo.core.semantic.ir import AnalysisStepIR
from marivo.ports.analytics import AnalyticsEngine
from marivo.routing import RoutingFailure, RoutingResolutionError
from marivo.runtime.errors import SemanticRuntimeNotReadyError
from marivo.runtime.execution.federation import FederationPlanner
from marivo.runtime.semantic.executor import execute_compiled
from marivo.runtime.semantic.feedback import (
    compile_failure_from_error,
    federation_failure_from_plan,
)
from tests.shared_fixtures import get_seeded_duckdb_path


class FailingEngine(AnalyticsEngine):
    def initialize(self) -> None:
        return None

    def query_rows(self, sql: str, params: list[object] | None = None) -> list[dict[str, object]]:
        _ = sql, params
        raise RuntimeError("engine offline")

    def table_exists(self, table_name: str) -> bool:
        return False

    def table_row_count(self, table_name: str) -> int:
        return 0


class FakeRouter:
    def resolve_tables(self, table_names: list[str]) -> Any:
        raise RoutingResolutionError(
            RoutingFailure(
                code="routing_no_common_engine",
                message=f"No common engine for tables {table_names}",
                routing_detail={
                    "resolution_status": "no_common_engine",
                    "unresolved_tables": list(table_names),
                },
            )
        )


class ExecutionFeedbackTests(unittest.TestCase):
    def test_execute_compiled_wraps_translation_error(self) -> None:
        engine = FailingEngine()
        query = CompiledQuery("SELECT 1", metadata={"engine_type": "unknown"})

        with self.assertRaises(ExecutionError) as error:
            execute_compiled(engine, query)

        self.assertEqual(error.exception.code, "translation_error")
        self.assertIn("prefer_default_engine", error.exception.fallback_candidates)

    def test_execute_compiled_wraps_engine_error(self) -> None:
        engine = FailingEngine()
        query = CompiledQuery(
            "SELECT 1", metadata={"engine_type": "duckdb", "step_type": "sample_rows"}
        )

        with self.assertRaises(ExecutionError) as error:
            execute_compiled(engine, query)

        self.assertEqual(error.exception.code, "engine_query_failed")
        self.assertTrue(error.exception.replan_candidate)

    def test_routing_runtime_returns_structured_fallback_feedback(self) -> None:
        runtime = RoutingRuntime(cast("Any", FakeRouter()), FailingEngine())

        resolution = runtime.resolve_tables(["watch_events"])

        self.assertTrue(resolution.fallback_used)
        self.assertIsNotNone(resolution.feedback)
        assert resolution.feedback is not None
        self.assertEqual(resolution.feedback.code, "routing_no_common_engine")

    def test_routing_feedback_preserves_new_structured_routing_codes(self) -> None:
        runtime = RoutingRuntime(cast("Any", FakeRouter()), FailingEngine())
        runtime.query_router = cast(
            "Any",
            type(
                "SourceUnavailableRouter",
                (),
                {
                    "resolve_tables": lambda self, table_names: (_ for _ in ()).throw(
                        RoutingResolutionError(
                            RoutingFailure(
                                code="routing_source_unavailable",
                                message=f"Source unavailable for {table_names}",
                                routing_detail={
                                    "resolution_status": "no_ready_mappings",
                                    "readiness_blockers": [
                                        {"failure_code": "engine_invalid_connection"}
                                    ],
                                },
                            )
                        )
                    )
                },
            )(),
        )

        resolution = runtime.resolve_tables(["watch_events"])

        self.assertTrue(resolution.fallback_used)
        assert resolution.feedback is not None
        self.assertEqual(resolution.feedback.code, "routing_source_unavailable")
        self.assertEqual(
            resolution.feedback.detail["routing_detail"]["readiness_blockers"][0]["failure_code"],
            "engine_invalid_connection",
        )

    def test_federation_failure_includes_plan_detail(self) -> None:
        plan = FederationPlanner().build_plan(
            translated_sql="SELECT 1",
            target_engine_type="trino",
            metadata={
                "step_type": "metric_query",
                "federation": {
                    "required": True,
                    "sources": [
                        {"engine_type": "duckdb", "table_names": ["watch_events"]},
                        {"engine_type": "trino", "table_names": ["ad_events"]},
                    ],
                },
            },
        )

        failure = federation_failure_from_plan(plan)

        self.assertEqual(failure.code, "federation_not_implemented")
        self.assertEqual(failure.detail["plan"]["mode"], "staged_handoff")
        self.assertIn("prefer_single_engine_route", failure.fallback_candidates)

    def test_compile_failure_preserves_structured_compile_error(self) -> None:
        step = AnalysisStepIR(index=2, step_type="metric_query")
        error = SemanticCompilerError(
            {
                "error_code": "COMPILER_BINDING_MISSING",
                "failed_gate": "binding_grounding",
                "message": "Resolved metric is not grounded by any published binding",
                "subject_ref": "metric.watch_time",
            }
        )

        failure = compile_failure_from_error(step, error, semantic_context={"metric_sql": "avg(x)"})

        self.assertEqual(failure.code, "compiler_binding_missing")
        self.assertEqual(failure.detail["compile_error"]["failed_gate"], "binding_grounding")
        self.assertEqual(failure.detail["compile_error"]["subject_ref"], "metric.watch_time")

    def test_compile_failure_maps_not_ready_error_to_readiness_feedback(self) -> None:
        step = AnalysisStepIR(index=1, step_type="metric_query")
        error = SemanticRuntimeNotReadyError(
            "Semantic ref is not ready: metric.watch_time",
            semantic_ref="metric.watch_time",
            object_kind="metric",
            lifecycle_status="active",
            readiness_status="not_ready",
            blocking_requirements=[
                {
                    "code": "METRIC_INPUT_COVERAGE_MISSING",
                    "message": "Missing required metric input coverage",
                }
            ],
            capabilities={},
            dependency_refs=["entity.user"],
        )

        failure = compile_failure_from_error(step, error, semantic_context={"metric_sql": "avg(x)"})

        self.assertEqual(failure.code, "semantic_not_ready")
        self.assertEqual(failure.category, "readiness")
        self.assertEqual(
            failure.detail["readiness_error"]["blocking_requirements"][0]["code"],
            "METRIC_INPUT_COVERAGE_MISSING",
        )
        self.assertEqual(failure.detail["readiness_error"]["subject_ref"], "metric.watch_time")

    def test_compile_failure_maps_compatibility_error_to_compatibility_feedback(self) -> None:
        step = AnalysisStepIR(index=1, step_type="metric_query")
        error = SemanticRequestCompatibilityError(
            {
                "message": "Request is incompatible with resolved semantic objects",
                "code": "semantic_request_incompatible",
                "category": "compatibility",
                "subject_ref": "dimension.country",
                "issues": [
                    {
                        "code": "COMPILER_DIMENSION_TIME_ANCHOR_MISMATCH",
                        "gate": "dimension_compatibility",
                        "category": "compatibility",
                        "severity": "error",
                        "message": "Time-derived dimension anchor is incompatible",
                        "subject_ref": "dimension.country",
                        "details": {},
                    }
                ],
                "request_context": {
                    "step_type": "metric_query",
                    "intent_kind": "metric_query",
                    "metric_ref": "metric.watch_time",
                    "dimension_refs": ["dimension.country"],
                },
            }
        )

        failure = compile_failure_from_error(step, error, semantic_context={"metric_sql": "avg(x)"})

        self.assertEqual(failure.code, "semantic_request_incompatible")
        self.assertEqual(failure.category, "compatibility")
        self.assertEqual(
            failure.detail["compatibility_error"]["issues"][0]["code"],
            "COMPILER_DIMENSION_TIME_ANCHOR_MISMATCH",
        )

    def test_compile_failure_keeps_import_missing_as_compatibility_feedback(self) -> None:
        step = AnalysisStepIR(index=1, step_type="metric_query")
        error = SemanticRequestCompatibilityError(
            {
                "message": "Request is incompatible with resolved semantic objects",
                "code": "semantic_request_incompatible",
                "category": "compatibility",
                "subject_ref": "dimension.cluster",
                "issues": [
                    {
                        "code": "COMPILER_DIMENSION_IMPORT_MISSING",
                        "gate": "dimension_compatibility",
                        "category": "compatibility",
                        "severity": "error",
                        "message": "Requested dimension requires an imported entity dimension bridge",
                        "subject_ref": "dimension.cluster",
                        "details": {
                            "metric_ref": "metric.watch_time",
                            "metric_entity_anchor_ref": "entity.user",
                            "available_imported_dimension_refs": [],
                        },
                    }
                ],
                "request_context": {
                    "step_type": "metric_query",
                    "intent_kind": "metric_query",
                    "metric_ref": "metric.watch_time",
                    "dimension_refs": ["dimension.cluster"],
                },
            }
        )

        failure = compile_failure_from_error(step, error, semantic_context={"metric_sql": "avg(x)"})

        self.assertEqual(failure.code, "semantic_request_incompatible")
        self.assertEqual(failure.category, "compatibility")
        self.assertEqual(
            failure.detail["compatibility_error"]["issues"][0]["code"],
            "COMPILER_DIMENSION_IMPORT_MISSING",
        )


class ExecutionFeedbackIntegrationTests(unittest.TestCase):
    client: ClassVar[TestClient]
    temp_dir: ClassVar[tempfile.TemporaryDirectory[str]]

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "execution_feedback.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_make_provenance_includes_routing_feedback_context(self) -> None:
        from marivo.runtime.semantic_ops import _make_provenance

        routing_feedback = {
            "code": "routing_no_common_engine",
            "category": "routing",
        }
        provenance = _make_provenance("SELECT 1", routing=routing_feedback)
        self.assertIn("routing", provenance)
        self.assertEqual(provenance["routing"]["code"], "routing_no_common_engine")


if __name__ == "__main__":
    unittest.main()
