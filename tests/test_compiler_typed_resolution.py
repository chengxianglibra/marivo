from __future__ import annotations

import unittest

from app.analysis_core.compiler import compile_step
from app.analysis_core.ir import AnalysisStepIR
from app.analysis_core.typed_resolution import (
    normalize_step_request,
    resolve_compiler_inputs,
)
from app.semantic_runtime import SemanticRuntimeRepository
from app.semantic_runtime.errors import SemanticRuntimeNotFoundError
from app.semantic_runtime.resolution import ResolvedSemanticObject


def _resolved_object(
    object_kind: str,
    semantic_ref: str,
    *,
    semantic_object: dict[str, object],
) -> ResolvedSemanticObject:
    return ResolvedSemanticObject(
        object_kind=object_kind,
        object_id=f"{object_kind}_1",
        ref=semantic_ref,
        semantic_object=semantic_object,
        status="published",
        revision=1,
        created_at="2026-04-09T00:00:00Z",
        updated_at="2026-04-09T00:00:00Z",
    )


class _FakeSemanticRepository(SemanticRuntimeRepository):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def resolve_metric_ref(self, metric_ref: str) -> ResolvedSemanticObject:
        self.calls.append(("metric", metric_ref))
        return _resolved_object(
            "metric",
            metric_ref,
            semantic_object={
                "metric_contract_id": "metric_contract_1",
                "header": {
                    "metric_ref": metric_ref,
                    "primary_time_ref": "time.event_date",
                },
                "payload": {"definition_sql": "avg(play_duration_seconds)"},
                "status": "published",
                "revision": 1,
                "created_at": "2026-04-09T00:00:00Z",
                "updated_at": "2026-04-09T00:00:00Z",
            },
        )

    def resolve_dimension_ref(self, dimension_ref: str) -> ResolvedSemanticObject:
        self.calls.append(("dimension", dimension_ref))
        return _resolved_object(
            "dimension",
            dimension_ref,
            semantic_object={
                "dimension_contract_id": "dimension_contract_1",
                "header": {"dimension_ref": dimension_ref},
                "status": "published",
                "revision": 1,
                "created_at": "2026-04-09T00:00:00Z",
                "updated_at": "2026-04-09T00:00:00Z",
            },
        )

    def resolve_process_ref(self, process_ref: str) -> ResolvedSemanticObject:
        self.calls.append(("process", process_ref))
        return _resolved_object(
            "process",
            process_ref,
            semantic_object={
                "process_contract_id": "process_contract_1",
                "header": {"process_ref": process_ref},
                "status": "published",
                "revision": 1,
                "created_at": "2026-04-09T00:00:00Z",
                "updated_at": "2026-04-09T00:00:00Z",
            },
        )

    def resolve_time_ref(self, time_ref: str) -> ResolvedSemanticObject:
        self.calls.append(("time", time_ref))
        return _resolved_object(
            "time",
            time_ref,
            semantic_object={
                "time_contract_id": "time_contract_1",
                "header": {"time_ref": time_ref},
                "status": "published",
                "revision": 1,
                "created_at": "2026-04-09T00:00:00Z",
                "updated_at": "2026-04-09T00:00:00Z",
            },
        )


class _MissingDimensionRepository(_FakeSemanticRepository):
    def resolve_dimension_ref(self, dimension_ref: str) -> ResolvedSemanticObject:
        raise SemanticRuntimeNotFoundError(
            f"Unknown semantic ref: {dimension_ref}",
            semantic_ref=dimension_ref,
        )


class _MissingTimeRepository(_FakeSemanticRepository):
    def resolve_time_ref(self, time_ref: str) -> ResolvedSemanticObject:
        raise SemanticRuntimeNotFoundError(
            f"Unknown time ref: {time_ref}",
            semantic_ref=time_ref,
        )


class CompilerTypedResolutionTests(unittest.TestCase):
    def test_normalize_metric_query_request_promotes_typed_refs(self) -> None:
        normalized = normalize_step_request(
            AnalysisStepIR(
                index=0,
                step_type="metric_query",
                params={
                    "table": "analytics.watch_events",
                    "metric": "watch_time",
                    "dimensions": ["platform", "dimension.country", "platform"],
                    "time_scope": {
                        "mode": "compare",
                        "grain": "day",
                        "current": {"start": "2026-03-10", "end": "2026-03-17"},
                        "baseline": {"start": "2026-03-03", "end": "2026-03-10"},
                    },
                },
            )
        )

        self.assertEqual(normalized.request_class, "root_metric_process")
        self.assertEqual(normalized.metric_ref, "metric.watch_time")
        self.assertEqual(
            normalized.request_dimensions,
            ["dimension.platform", "dimension.country"],
        )
        assert normalized.request_time_scope is not None
        self.assertEqual(normalized.request_time_scope["mode"], "compare")
        self.assertEqual(normalized.request_time_scope["current"]["start"], "2026-03-10")

    def test_resolve_compiler_inputs_uses_runtime_repository(self) -> None:
        repository = _FakeSemanticRepository()
        normalized = normalize_step_request(
            AnalysisStepIR(
                index=0,
                step_type="metric_query",
                params={
                    "table": "analytics.watch_events",
                    "metric": "watch_time",
                    "dimensions": ["platform"],
                    "time_scope": {
                        "mode": "single_window",
                        "grain": "day",
                        "current": {"start": "2026-03-10", "end": "2026-03-17"},
                    },
                },
            )
        )

        resolved = resolve_compiler_inputs(
            normalized,
            semantic_repository=repository,
        )

        self.assertEqual(resolved.resolved_metric.ref, "metric.watch_time")
        self.assertEqual(resolved.resolved_dimension_refs, ["dimension.platform"])
        assert resolved.resolved_filter_time is not None
        self.assertEqual(resolved.resolved_filter_time.ref, "time.event_date")
        self.assertEqual(
            repository.calls,
            [
                ("metric", "metric.watch_time"),
                ("dimension", "dimension.platform"),
                ("time", "time.event_date"),
            ],
        )

    def test_resolve_compiler_inputs_records_unresolved_dimensions_as_warnings(self) -> None:
        normalized = normalize_step_request(
            AnalysisStepIR(
                index=0,
                step_type="metric_query",
                params={
                    "table": "analytics.watch_events",
                    "metric": "watch_time",
                    "dimensions": ["missing_dimension"],
                    "time_scope": {
                        "mode": "single_window",
                        "grain": "day",
                        "current": {"start": "2026-03-10", "end": "2026-03-17"},
                    },
                },
            )
        )

        resolved = resolve_compiler_inputs(
            normalized,
            semantic_repository=_MissingDimensionRepository(),
        )

        self.assertEqual(resolved.resolved_dimension_refs, [])
        self.assertEqual(len(resolved.warnings), 1)
        self.assertEqual(resolved.warnings[0]["code"], "dimension_ref_unresolved")
        self.assertIn("dimension.missing_dimension", resolved.warnings[0]["message"])

    def test_compile_step_keeps_sql_output_and_records_resolved_refs(self) -> None:
        compiled = compile_step(
            AnalysisStepIR(
                index=0,
                step_type="metric_query",
                params={
                    "metric": "watch_time",
                    "table": "analytics.watch_events",
                    "time_scope": {
                        "mode": "single_window",
                        "grain": "day",
                        "current": {"start": "2026-03-10", "end": "2026-03-17"},
                    },
                    "scoped_query": {
                        "mode": "single_window",
                        "analysis_time_expr": "event_date",
                        "analysis_time_kind": "date_field",
                        "current": {"start": "2026-03-10", "end": "2026-03-17"},
                    },
                },
            ),
            engine_type="duckdb",
            semantic_context={
                "metric_sql": "avg(play_duration_seconds)",
                "dimensions": ["platform"],
                "semantic_repository": _FakeSemanticRepository(),
            },
        )

        self.assertIn("ROUND(avg(play_duration_seconds), 2) AS current_value", compiled.sql)
        self.assertEqual(compiled.metadata["normalized_metric_ref"], "metric.watch_time")
        self.assertEqual(compiled.metadata["resolved_metric_ref"], "metric.watch_time")
        self.assertEqual(compiled.metadata["resolved_dimension_refs"], ["dimension.platform"])
        self.assertEqual(compiled.metadata["resolved_filter_time_ref"], "time.event_date")
        self.assertEqual(compiled.metadata["compiler_warnings"], [])

    def test_resolve_compiler_inputs_no_repository_warns_for_metric_and_dimensions(self) -> None:
        from app.analysis_core.typed_resolution import NormalizedCompilerRequest

        normalized = NormalizedCompilerRequest(
            intent_kind="metric_query",
            request_class="root_metric_process",
            table_name="analytics.watch_events",
            metric_ref="metric.watch_time",
            request_dimensions=["dimension.platform"],
        )

        resolved = resolve_compiler_inputs(normalized, semantic_repository=None)

        self.assertIsNone(resolved.resolved_metric)
        self.assertEqual(resolved.resolved_dimension_refs, [])
        codes = [w["code"] for w in resolved.warnings]
        self.assertIn("semantic_repository_missing", codes)
        metric_warns = [w for w in resolved.warnings if w.get("metric_ref") == "metric.watch_time"]
        self.assertEqual(len(metric_warns), 1)
        dim_warns = [w for w in resolved.warnings if w.get("dimension_ref") == "dimension.platform"]
        self.assertEqual(len(dim_warns), 1)

    def test_resolve_compiler_inputs_unresolvable_time_ref_records_warning(self) -> None:
        normalized = normalize_step_request(
            AnalysisStepIR(
                index=0,
                step_type="metric_query",
                params={
                    "table": "analytics.watch_events",
                    "metric": "watch_time",
                    "time_scope": {
                        "mode": "single_window",
                        "grain": "day",
                        "current": {"start": "2026-03-10", "end": "2026-03-17"},
                    },
                },
            )
        )

        resolved = resolve_compiler_inputs(
            normalized,
            semantic_repository=_MissingTimeRepository(),
        )

        self.assertIsNone(resolved.resolved_filter_time)
        self.assertEqual(len(resolved.warnings), 1)
        self.assertEqual(resolved.warnings[0]["code"], "time_ref_unresolved")
        self.assertEqual(resolved.warnings[0]["time_ref"], "time.event_date")

    def test_resolve_compiler_inputs_falls_back_to_process_anchor_time_ref(self) -> None:
        from app.analysis_core.typed_resolution import (
            NormalizedCompilerRequest,
            ResolvedCompilerInputs,
        )

        normalized = NormalizedCompilerRequest(
            intent_kind="detect",
            request_class="root_metric_process",
            table_name=None,
            process_ref="process.daily_check",
        )
        # Build a resolved inputs where metric has no primary_time_ref but process has anchor_time_ref
        resolved_inputs = ResolvedCompilerInputs(normalized_request=normalized)
        resolved_inputs.resolved_process = _resolved_object(
            "process",
            "process.daily_check",
            semantic_object={
                "anchor_time_ref": "time.check_date",
            },
        )

        from app.analysis_core.typed_resolution import _resolved_filter_time_ref

        time_ref = _resolved_filter_time_ref(normalized, resolved_inputs)
        self.assertEqual(time_ref, "time.check_date")

    def test_resolve_compiler_inputs_resolves_left_and_right_process_refs(self) -> None:
        from app.analysis_core.typed_resolution import NormalizedCompilerRequest

        repository = _FakeSemanticRepository()
        normalized = NormalizedCompilerRequest(
            intent_kind="compare",
            request_class="root_metric_process",
            table_name=None,
            left_process_ref="process.left",
            right_process_ref="process.right",
        )

        resolved = resolve_compiler_inputs(normalized, semantic_repository=repository)

        self.assertIsNotNone(resolved.resolved_left_process)
        self.assertIsNotNone(resolved.resolved_right_process)
        assert resolved.resolved_left_process is not None
        assert resolved.resolved_right_process is not None
        self.assertEqual(resolved.resolved_left_process.ref, "process.left")
        self.assertEqual(resolved.resolved_right_process.ref, "process.right")
        self.assertIn(("process", "process.left"), repository.calls)
        self.assertIn(("process", "process.right"), repository.calls)


if __name__ == "__main__":
    unittest.main()
