from __future__ import annotations

import unittest

from app.analysis_core.capability_profiles import derive_compiler_state
from app.analysis_core.compiler import (
    SemanticCompilerError,
    SemanticRequestCompatibilityError,
    compile_step,
)
from app.analysis_core.ir import AnalysisStepIR
from app.analysis_core.typed_resolution import (
    normalize_step_request,
    resolve_compiler_inputs,
)
from app.analysis_core.validator import validate_compiler_inputs
from app.evidence_engine.ref_boundary import assert_no_canonical_refs_in_semantic_payload
from app.semantic_runtime import SemanticRuntimeRepository
from app.semantic_runtime.errors import SemanticRuntimeNotFoundError, SemanticRuntimeNotReadyError
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


def _binding_for(bound_object_ref: str) -> ResolvedSemanticObject:
    suffix = bound_object_ref.split(".", 1)[-1].replace(".", "_")
    return _resolved_object(
        "binding",
        f"binding.{suffix}",
        semantic_object={
            "binding_id": f"binding_{suffix}",
            "header": {
                "binding_ref": f"binding.{suffix}",
                "binding_scope": "metric",
                "bound_object_ref": bound_object_ref,
            },
            "interface_contract": {
                "imports": [],
                "carrier_bindings": [
                    {
                        "binding_key": "primary",
                        "carrier_kind": "table",
                        "carrier_locator": "analytics.watch_events",
                    }
                ],
                "field_bindings": [
                    {
                        "carrier_binding_key": "primary",
                        "target": {"target_kind": "metric_input", "target_key": "value"},
                        "semantic_ref": "field.metric_value",
                        "surface_ref": "field.metric_value",
                    }
                ],
            },
            "status": "published",
            "revision": 1,
            "created_at": "2026-04-09T00:00:00Z",
            "updated_at": "2026-04-09T00:00:00Z",
        },
    )


def _binding_reader(object_ref: str) -> list[ResolvedSemanticObject]:
    return [_binding_for(object_ref)]


def _empty_binding_reader(object_ref: str) -> list[ResolvedSemanticObject]:
    _ = object_ref
    return []


def _binding_with_interface(
    bound_object_ref: str,
    *,
    interface_contract: dict[str, object],
) -> ResolvedSemanticObject:
    binding = _binding_for(bound_object_ref)
    binding.semantic_object["interface_contract"] = interface_contract
    return binding


def _metric_binding_with_imports(
    bound_object_ref: str,
    *,
    imports: list[dict[str, object]],
) -> ResolvedSemanticObject:
    binding = _binding_for(bound_object_ref)
    binding.semantic_object["interface_contract"] = {
        "imports": imports,
        "carrier_bindings": [
            {
                "binding_key": "primary",
                "carrier_kind": "table",
                "carrier_locator": "analytics.watch_events",
            }
        ],
        "field_bindings": [
            {
                "carrier_binding_key": "primary",
                "target": {"target_kind": "metric_input", "target_key": "value"},
                "semantic_ref": "field.metric_value",
                "surface_ref": "field.metric_value",
            }
        ],
    }
    return binding


def _entity_binding(
    binding_ref: str,
    *,
    bound_object_ref: str,
    field_bindings: list[dict[str, object]],
    carrier_bindings: list[dict[str, object]] | None = None,
) -> ResolvedSemanticObject:
    synthesized_surfaces = [
        {
            "surface_ref": str(field_binding.get("surface_ref") or "").strip(),
            "physical_name": str(field_binding.get("surface_ref") or "")
            .strip()
            .removeprefix("field."),
        }
        for field_binding in field_bindings
        if str(field_binding.get("surface_ref") or "").strip()
    ]
    return _resolved_object(
        "binding",
        binding_ref,
        semantic_object={
            "binding_id": binding_ref.replace(".", "_"),
            "header": {
                "binding_ref": binding_ref,
                "binding_scope": "entity",
                "bound_object_ref": bound_object_ref,
            },
            "interface_contract": {
                "imports": [],
                "carrier_bindings": carrier_bindings
                or [
                    {
                        "binding_key": "primary",
                        "carrier_kind": "table",
                        "carrier_locator": "analytics.entity_events",
                        "field_surfaces": synthesized_surfaces,
                    }
                ],
                "field_bindings": field_bindings,
            },
            "status": "published",
            "revision": 1,
            "created_at": "2026-04-09T00:00:00Z",
            "updated_at": "2026-04-09T00:00:00Z",
        },
    )


def _profile_reader(subject_ref: str) -> list[dict[str, object]]:
    if subject_ref == "metric.watch_time":
        return [
            {
                "profile_ref": "compiler_profile.watch_time_requirement",
                "profile_kind": "requirement",
                "subject_ref": subject_ref,
                "subject_revision": 1,
                "requirement": {"contract_modes": ["context_provider"]},
            }
        ]
    if subject_ref == "process.daily_check":
        return [
            {
                "profile_ref": "compiler_profile.daily_check_capability",
                "profile_kind": "capability",
                "subject_ref": subject_ref,
                "subject_revision": 1,
                "capability": {
                    "inferential_ready": True,
                    "supported_sample_summaries": ["rate_sample_summary"],
                },
            }
        ]
    return []


class _FakeSemanticRepository(SemanticRuntimeRepository):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.binding_map: dict[str, ResolvedSemanticObject] = {}

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
                    "sample_kind": "rate",
                    "additivity": "additive",
                    "population_subject_ref": "subject.user",
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
                "interface_contract": {"grouping": {"supports_grouping": True}},
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
                "interface_contract": {
                    "contract_mode": "context_provider",
                    "population_subject_ref": "subject.user",
                    "context_kind": "experiment_split",
                    "anchor_time_ref": "time.event_date",
                },
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

    def resolve_binding_ref(self, binding_ref: str) -> ResolvedSemanticObject:
        self.calls.append(("binding", binding_ref))
        try:
            return self.binding_map[binding_ref]
        except KeyError as error:
            raise SemanticRuntimeNotFoundError(
                f"Unknown binding ref: {binding_ref}",
                semantic_ref=binding_ref,
            ) from error


class _ImportedBindingRepository(_FakeSemanticRepository):
    def __init__(self) -> None:
        super().__init__()
        self.binding_map = {
            "binding.entity_user": _entity_binding(
                "binding.entity_user",
                bound_object_ref="entity.user",
                field_bindings=[
                    {
                        "carrier_binding_key": "primary",
                        "target": {
                            "target_kind": "stable_descriptor",
                            "target_key": "dimension.cluster",
                        },
                        "semantic_ref": "dimension.cluster",
                        "surface_ref": "field.cluster",
                    },
                    {
                        "carrier_binding_key": "primary",
                        "target": {
                            "target_kind": "stable_descriptor",
                            "target_key": "dimension.cluster",
                        },
                        "semantic_ref": "dimension.cluster",
                        "surface_ref": "field.cluster_shadow",
                    },
                    {
                        "carrier_binding_key": "primary",
                        "target": {
                            "target_kind": "stable_descriptor",
                            "target_key": "dimension.country",
                        },
                        "semantic_ref": "dimension.country",
                        "surface_ref": "field.country",
                    },
                    {
                        "carrier_binding_key": "primary",
                        "target": {"target_kind": "identity_key", "target_key": "key.user_id"},
                        "semantic_ref": "key.user_id",
                        "surface_ref": "field.user_id",
                    },
                ],
            ),
            "binding.entity_account": _entity_binding(
                "binding.entity_account",
                bound_object_ref="entity.account",
                field_bindings=[
                    {
                        "carrier_binding_key": "primary",
                        "target": {
                            "target_kind": "stable_descriptor",
                            "target_key": "dimension.cluster",
                        },
                        "semantic_ref": "dimension.cluster",
                        "surface_ref": "field.account_cluster",
                    }
                ],
            ),
            "binding.metric_other": _resolved_object(
                "binding",
                "binding.metric_other",
                semantic_object={
                    "binding_id": "binding_metric_other",
                    "header": {
                        "binding_ref": "binding.metric_other",
                        "binding_scope": "metric",
                        "bound_object_ref": "metric.other",
                    },
                    "interface_contract": {
                        "imports": [],
                        "carrier_bindings": [
                            {
                                "binding_key": "primary",
                                "carrier_kind": "table",
                                "carrier_locator": "analytics.other_metric",
                            }
                        ],
                        "field_bindings": [
                            {
                                "carrier_binding_key": "primary",
                                "target": {"target_kind": "metric_input", "target_key": "value"},
                                "semantic_ref": "field.metric_value",
                                "surface_ref": "field.metric_value",
                            }
                        ],
                    },
                    "status": "published",
                    "revision": 1,
                    "created_at": "2026-04-09T00:00:00Z",
                    "updated_at": "2026-04-09T00:00:00Z",
                },
            ),
        }

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
                    "sample_kind": "rate",
                    "additivity": "additive",
                    "population_subject_ref": "subject.user",
                    "observed_entity_ref": "entity.user",
                },
                "payload": {"definition_sql": "avg(play_duration_seconds)"},
                "status": "published",
                "revision": 1,
                "created_at": "2026-04-09T00:00:00Z",
                "updated_at": "2026-04-09T00:00:00Z",
            },
        )


class _ImportedBindingFallbackRepository(_ImportedBindingRepository):
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
                    "sample_kind": "rate",
                    "additivity": "additive",
                    "population_subject_ref": "entity.user",
                    "observed_entity_ref": "",
                },
                "payload": {"definition_sql": "avg(play_duration_seconds)"},
                "status": "published",
                "revision": 1,
                "created_at": "2026-04-09T00:00:00Z",
                "updated_at": "2026-04-09T00:00:00Z",
            },
        )


class _NoAnchorMetricRepository(_ImportedBindingRepository):
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
                    "sample_kind": "rate",
                    "additivity": "additive",
                    "population_subject_ref": "",
                    "observed_entity_ref": "",
                },
                "payload": {"definition_sql": "avg(play_duration_seconds)"},
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


class _NotReadyMetricRepository(_FakeSemanticRepository):
    def resolve_metric_ref(self, metric_ref: str) -> ResolvedSemanticObject:
        raise SemanticRuntimeNotReadyError(
            f"Semantic ref is not ready: {metric_ref}",
            semantic_ref=metric_ref,
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
            dependency_refs=["entity.user", "time.event_date"],
        )


class _NonGroupingDimensionRepository(_FakeSemanticRepository):
    def resolve_dimension_ref(self, dimension_ref: str) -> ResolvedSemanticObject:
        resolved = super().resolve_dimension_ref(dimension_ref)
        resolved.semantic_object["interface_contract"] = {"grouping": {"supports_grouping": False}}
        return resolved


class _MetricDimensionRepository(_FakeSemanticRepository):
    def resolve_metric_ref(self, metric_ref: str) -> ResolvedSemanticObject:
        resolved = super().resolve_metric_ref(metric_ref)
        resolved.semantic_object["payload"] = {
            "definition_sql": "avg(play_duration_seconds)",
            "allowed_dimensions": ["country", "dimension.platform"],
        }
        return resolved


class _NonGroupingMetricDimensionRepository(_MetricDimensionRepository):
    def resolve_dimension_ref(self, dimension_ref: str) -> ResolvedSemanticObject:
        resolved = super().resolve_dimension_ref(dimension_ref)
        resolved.semantic_object["interface_contract"] = {"grouping": {"supports_grouping": False}}
        return resolved


class _TimeAnchoredDimensionRepository(_MetricDimensionRepository):
    def resolve_dimension_ref(self, dimension_ref: str) -> ResolvedSemanticObject:
        resolved = super().resolve_dimension_ref(dimension_ref)
        resolved.semantic_object["interface_contract"] = {
            "grouping": {"supports_grouping": True},
            "time_derived_requirement": {"required_time_anchor_ref": "time.other_anchor"},
        }
        return resolved


class _TimeAnchoredImportedBindingRepository(_ImportedBindingRepository):
    def resolve_dimension_ref(self, dimension_ref: str) -> ResolvedSemanticObject:
        resolved = super().resolve_dimension_ref(dimension_ref)
        resolved.semantic_object["interface_contract"] = {
            "grouping": {"supports_grouping": True},
            "time_derived_requirement": {"required_time_anchor_ref": "time.other_anchor"},
        }
        return resolved


class _UniqueImportedBindingRepository(_ImportedBindingRepository):
    def __init__(self) -> None:
        super().__init__()
        self.binding_map["binding.entity_user"] = _entity_binding(
            "binding.entity_user",
            bound_object_ref="entity.user",
            field_bindings=[
                {
                    "carrier_binding_key": "primary",
                    "target": {
                        "target_kind": "stable_descriptor",
                        "target_key": "dimension.cluster",
                    },
                    "semantic_ref": "dimension.cluster",
                    "surface_ref": "field.cluster",
                },
                {
                    "carrier_binding_key": "primary",
                    "target": {
                        "target_kind": "stable_descriptor",
                        "target_key": "dimension.country",
                    },
                    "semantic_ref": "dimension.country",
                    "surface_ref": "field.country",
                },
            ],
        )


class _MissingTimeAnchoredDimensionRepository(_TimeAnchoredDimensionRepository):
    def resolve_time_ref(self, time_ref: str) -> ResolvedSemanticObject:
        raise SemanticRuntimeNotFoundError(
            f"Unknown time ref: {time_ref}",
            semantic_ref=time_ref,
        )


class _IncompatibleProcessRepository(_FakeSemanticRepository):
    def resolve_process_ref(self, process_ref: str) -> ResolvedSemanticObject:
        resolved = super().resolve_process_ref(process_ref)
        resolved.semantic_object["interface_contract"] = {
            "contract_mode": "entity_stream",
            "population_subject_ref": "subject.account",
            "context_kind": "cohort_membership",
            "entity_ref": "entity.account",
            "anchor_time_ref": "time.event_date",
        }
        return resolved


class CompilerTypedResolutionTests(unittest.TestCase):
    def test_normalize_metric_query_request_preserves_plain_dimensions(self) -> None:
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
            ["platform", "dimension.country"],
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

        assert resolved.resolved_metric is not None
        self.assertEqual(resolved.resolved_metric.ref, "metric.watch_time")
        self.assertEqual(resolved.resolved_dimension_refs, ["platform"])
        assert resolved.resolved_filter_time is not None
        self.assertEqual(resolved.resolved_filter_time.ref, "time.event_date")
        self.assertEqual(
            repository.calls,
            [
                ("metric", "metric.watch_time"),
                ("dimension", "platform"),
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
        self.assertIn("missing_dimension", resolved.warnings[0]["message"])

    def test_resolve_compiler_inputs_collects_imported_dimensions_from_matching_entity_binding(
        self,
    ) -> None:
        repository = _ImportedBindingRepository()
        normalized = normalize_step_request(
            AnalysisStepIR(
                index=0,
                step_type="metric_query",
                params={"table": "analytics.watch_events", "metric": "watch_time"},
            )
        )

        resolved = resolve_compiler_inputs(
            normalized,
            semantic_repository=repository,
            binding_reader=lambda object_ref: [
                _metric_binding_with_imports(
                    object_ref,
                    imports=[
                        {
                            "import_key": "entity_bridge",
                            "binding_ref": "binding.entity_user",
                            "required_ref_prefixes": ["dimension."],
                        }
                    ],
                )
            ],
        )

        self.assertEqual(resolved.metric_entity_anchor_ref, "entity.user")
        self.assertEqual(
            resolved.resolved_imported_dimension_refs, ["dimension.cluster", "dimension.country"]
        )
        self.assertEqual(resolved.imported_dimension_conflicts, {})
        cluster = next(
            bridge
            for bridge in resolved.resolved_imported_dimensions
            if bridge.dimension_ref == "dimension.cluster"
        )
        self.assertEqual(cluster.source_binding_ref, "binding.entity_user")
        self.assertEqual(cluster.source_entity_ref, "entity.user")
        self.assertEqual(cluster.import_key, "entity_bridge")

    def test_resolve_compiler_inputs_falls_back_to_population_subject_for_entity_anchor(
        self,
    ) -> None:
        repository = _ImportedBindingFallbackRepository()
        normalized = normalize_step_request(
            AnalysisStepIR(
                index=0,
                step_type="metric_query",
                params={"table": "analytics.watch_events", "metric": "watch_time"},
            )
        )

        resolved = resolve_compiler_inputs(
            normalized,
            semantic_repository=repository,
            binding_reader=lambda object_ref: [
                _metric_binding_with_imports(
                    object_ref,
                    imports=[
                        {
                            "import_key": "entity_bridge",
                            "binding_ref": "binding.entity_user",
                            "required_ref_prefixes": ["dimension."],
                        }
                    ],
                )
            ],
        )

        self.assertEqual(resolved.metric_entity_anchor_ref, "entity.user")
        self.assertEqual(
            resolved.resolved_imported_dimension_refs, ["dimension.cluster", "dimension.country"]
        )

    def test_resolve_compiler_inputs_skips_imported_dimensions_without_entity_anchor(self) -> None:
        repository = _NoAnchorMetricRepository()
        normalized = normalize_step_request(
            AnalysisStepIR(
                index=0,
                step_type="metric_query",
                params={"table": "analytics.watch_events", "metric": "watch_time"},
            )
        )

        resolved = resolve_compiler_inputs(
            normalized,
            semantic_repository=repository,
            binding_reader=lambda object_ref: [
                _metric_binding_with_imports(
                    object_ref,
                    imports=[
                        {
                            "import_key": "entity_bridge",
                            "binding_ref": "binding.entity_user",
                            "required_ref_prefixes": ["dimension."],
                        }
                    ],
                )
            ],
        )

        self.assertIsNone(resolved.metric_entity_anchor_ref)
        self.assertEqual(resolved.resolved_imported_dimensions, [])
        self.assertEqual(resolved.imported_dimension_conflicts, {})
        self.assertEqual(resolved.warnings, [])

    def test_resolve_compiler_inputs_ignores_non_entity_and_anchor_mismatched_imports(self) -> None:
        repository = _ImportedBindingRepository()
        normalized = normalize_step_request(
            AnalysisStepIR(
                index=0,
                step_type="metric_query",
                params={"table": "analytics.watch_events", "metric": "watch_time"},
            )
        )

        resolved = resolve_compiler_inputs(
            normalized,
            semantic_repository=repository,
            binding_reader=lambda object_ref: [
                _metric_binding_with_imports(
                    object_ref,
                    imports=[
                        {
                            "import_key": "wrong_scope",
                            "binding_ref": "binding.metric_other",
                            "required_ref_prefixes": ["dimension."],
                        },
                        {
                            "import_key": "wrong_anchor",
                            "binding_ref": "binding.entity_account",
                            "required_ref_prefixes": ["dimension."],
                        },
                    ],
                )
            ],
        )

        self.assertEqual(resolved.metric_entity_anchor_ref, "entity.user")
        self.assertEqual(resolved.resolved_imported_dimensions, [])
        self.assertEqual(resolved.imported_dimension_conflicts, {})

    def test_resolve_compiler_inputs_records_conflicts_for_multi_source_imported_dimensions(
        self,
    ) -> None:
        repository = _ImportedBindingRepository()
        repository.binding_map["binding.entity_user_alt"] = _entity_binding(
            "binding.entity_user_alt",
            bound_object_ref="entity.user",
            field_bindings=[
                {
                    "carrier_binding_key": "primary",
                    "target": {
                        "target_kind": "stable_descriptor",
                        "target_key": "dimension.cluster",
                    },
                    "semantic_ref": "dimension.cluster",
                    "surface_ref": "field.cluster_alt",
                }
            ],
        )
        normalized = normalize_step_request(
            AnalysisStepIR(
                index=0,
                step_type="metric_query",
                params={"table": "analytics.watch_events", "metric": "watch_time"},
            )
        )

        resolved = resolve_compiler_inputs(
            normalized,
            semantic_repository=repository,
            binding_reader=lambda object_ref: [
                _metric_binding_with_imports(
                    object_ref,
                    imports=[
                        {
                            "import_key": "entity_bridge",
                            "binding_ref": "binding.entity_user",
                            "required_ref_prefixes": ["dimension."],
                        },
                        {
                            "import_key": "entity_bridge_alt",
                            "binding_ref": "binding.entity_user_alt",
                            "required_ref_prefixes": ["dimension."],
                        },
                    ],
                )
            ],
        )

        self.assertEqual(resolved.resolved_imported_dimension_refs, ["dimension.country"])
        self.assertIn("dimension.cluster", resolved.imported_dimension_conflicts)
        self.assertEqual(
            [
                bridge.source_binding_ref
                for bridge in resolved.imported_dimension_conflicts["dimension.cluster"]
            ],
            ["binding.entity_user", "binding.entity_user_alt"],
        )

    def test_resolve_compiler_inputs_warns_when_imported_binding_cannot_be_resolved(self) -> None:
        repository = _ImportedBindingRepository()
        normalized = normalize_step_request(
            AnalysisStepIR(
                index=0,
                step_type="metric_query",
                params={"table": "analytics.watch_events", "metric": "watch_time"},
            )
        )

        resolved = resolve_compiler_inputs(
            normalized,
            semantic_repository=repository,
            binding_reader=lambda object_ref: [
                _metric_binding_with_imports(
                    object_ref,
                    imports=[
                        {
                            "import_key": "missing_import",
                            "binding_ref": "binding.missing_entity",
                            "required_ref_prefixes": ["dimension."],
                        }
                    ],
                )
            ],
        )

        self.assertEqual(resolved.resolved_imported_dimensions, [])
        self.assertEqual(len(resolved.warnings), 1)
        self.assertEqual(resolved.warnings[0]["code"], "binding_import_unresolved")
        self.assertEqual(resolved.warnings[0]["binding_ref"], "binding.missing_entity")
        self.assertEqual(resolved.warnings[0]["import_key"], "missing_import")

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
                "binding_reader": _binding_reader,
                "compatibility_profile_reader": _profile_reader,
            },
        )

        self.assertIn("ROUND(avg(play_duration_seconds), 2) AS current_value", compiled.sql)
        self.assertIsNotNone(compiled.ir_bundle)
        assert compiled.ir_bundle is not None
        resolved_bindings = compiled.ir_bundle["plan"]["inputs"].get("resolved_bindings")
        assert resolved_bindings is not None
        self.assertEqual(compiled.ir_bundle["plan"]["header"]["root_intent_kind"], "metric_query")
        self.assertEqual(
            compiled.ir_bundle["plan"]["inputs"]["metric_ref"],
            "metric.watch_time",
        )
        self.assertEqual(
            resolved_bindings[0]["binding_ref"],
            "binding.watch_time",
        )
        self.assertEqual(
            compiled.ir_bundle["compile_report"]["validation_summary"]["resolved_filter_time_ref"],
            "time.event_date",
        )
        self.assertTrue(
            any(
                record["validation_kind"] == "metric_process_compatibility"
                for record in compiled.ir_bundle["compile_report"]["validation_trace"]
            )
        )
        self.assertEqual(compiled.metadata["normalized_request_class"], "root_metric_process")
        self.assertEqual(compiled.metadata["resolved_metric_ref"], "metric.watch_time")
        self.assertEqual(compiled.metadata["resolved_dimension_refs"], ["platform"])
        self.assertEqual(compiled.metadata["resolved_filter_time_ref"], "time.event_date")
        self.assertEqual(compiled.metadata["resolved_binding_refs"], ["binding.watch_time"])
        self.assertEqual(compiled.metadata["metric_entity_anchor_ref"], "subject.user")
        self.assertEqual(compiled.metadata["resolved_imported_dimensions"], [])
        self.assertEqual(compiled.metadata["imported_dimension_conflicts"], {})
        self.assertEqual(compiled.metadata["resolved_imported_dimension_sources"], [])
        self.assertNotIn("compiler_validation", compiled.metadata)
        self.assertNotIn("compiler_profile_trace", compiled.metadata)
        profile_trace = compiled.ir_bundle["compile_report"]["profile_usage_trace"]
        assert profile_trace is not None
        self.assertEqual(profile_trace[0]["subject_ref"], "metric.watch_time")
        self.assertEqual(profile_trace[0]["subject_revision"], 1)
        self.assertEqual(profile_trace[0]["resolved_subject_revision"], 1)
        assert_no_canonical_refs_in_semantic_payload(
            compiled.ir_bundle,
            surface="compiler_ir_bundle",
        )
        assert_no_canonical_refs_in_semantic_payload(
            compiled.metadata,
            surface="compiler_metadata",
        )
        self.assertNotIn("artifact_refs", compiled.ir_bundle["compile_report"])
        self.assertNotIn("finding_ref", str(compiled.ir_bundle))

    def test_compile_step_propagates_not_ready_metric_error(self) -> None:
        with self.assertRaises(SemanticRuntimeNotReadyError) as ctx:
            compile_step(
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
                    },
                ),
                engine_type="duckdb",
                semantic_context={
                    "semantic_repository": _NotReadyMetricRepository(),
                },
            )

        error = ctx.exception
        self.assertEqual(error.semantic_ref, "metric.watch_time")
        self.assertEqual(error.readiness_status, "not_ready")
        self.assertEqual(
            error.blocking_requirements[0]["code"],
            "METRIC_INPUT_COVERAGE_MISSING",
        )

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

    def test_compile_step_fails_for_unresolved_typed_dimension_ref(self) -> None:
        with self.assertRaises(SemanticRequestCompatibilityError) as ctx:
            compile_step(
                AnalysisStepIR(
                    index=0,
                    step_type="metric_query",
                    params={
                        "metric": "watch_time",
                        "table": "analytics.watch_events",
                        "dimensions": ["dimension.missing"],
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
                    "semantic_repository": _MissingDimensionRepository(),
                    "binding_reader": _binding_reader,
                },
            )

        self.assertEqual(
            ctx.exception.detail["issues"][0]["code"],
            "COMPILER_DIMENSION_UNRESOLVED",
        )

    def test_validate_compiler_inputs_requires_process_capability_profile_for_validate(
        self,
    ) -> None:
        normalized = normalize_step_request(
            AnalysisStepIR(index=0, step_type="validate", params={"metric": "watch_time"})
        )
        normalized.process_ref = "process.daily_check"
        resolved = resolve_compiler_inputs(
            normalized,
            semantic_repository=_FakeSemanticRepository(),
            binding_reader=_binding_reader,
        )
        derived = derive_compiler_state(
            intent_kind="validate",
            resolved_metric=resolved.resolved_metric,
            resolved_process=resolved.resolved_process,
            resolved_bindings=resolved.resolved_bindings,
            profile_reader=None,
        )

        result = validate_compiler_inputs(
            step_type="validate",
            resolved_inputs=resolved,
            derived_state=derived,
        )

        self.assertFalse(result.ok)
        self.assertIn("COMPILER_PROFILE_MISSING", [issue.code for issue in result.issues])

    def test_validate_compiler_inputs_rejects_profile_revision_mismatch(self) -> None:
        normalized = normalize_step_request(
            AnalysisStepIR(index=0, step_type="validate", params={"metric": "watch_time"})
        )
        normalized.process_ref = "process.daily_check"
        resolved = resolve_compiler_inputs(
            normalized,
            semantic_repository=_FakeSemanticRepository(),
            binding_reader=_binding_reader,
        )

        def _stale_profile_reader(subject_ref: str) -> list[dict[str, object]]:
            profiles = _profile_reader(subject_ref)
            if subject_ref != "process.daily_check":
                return profiles
            return [dict(profiles[0], subject_revision=99)]

        derived = derive_compiler_state(
            intent_kind="validate",
            resolved_metric=resolved.resolved_metric,
            resolved_process=resolved.resolved_process,
            resolved_bindings=resolved.resolved_bindings,
            profile_reader=_stale_profile_reader,
        )

        result = validate_compiler_inputs(
            step_type="validate",
            resolved_inputs=resolved,
            derived_state=derived,
        )

        self.assertFalse(result.ok)
        self.assertIn("COMPILER_PROFILE_REVISION_MISMATCH", [issue.code for issue in result.issues])
        self.assertTrue(
            any(
                trace.subject_ref == "process.daily_check" and trace.reason == "revision_mismatch"
                for trace in derived.profile_traces
            )
        )

    def test_validate_compiler_inputs_requires_process_when_metric_profile_demands_it(self) -> None:
        normalized = normalize_step_request(
            AnalysisStepIR(index=0, step_type="validate", params={"metric": "watch_time"})
        )
        resolved = resolve_compiler_inputs(
            normalized,
            semantic_repository=_FakeSemanticRepository(),
            binding_reader=_binding_reader,
        )
        derived = derive_compiler_state(
            intent_kind="validate",
            resolved_metric=resolved.resolved_metric,
            resolved_process=resolved.resolved_process,
            resolved_bindings=resolved.resolved_bindings,
            profile_reader=_profile_reader,
        )

        result = validate_compiler_inputs(
            step_type="validate",
            resolved_inputs=resolved,
            derived_state=derived,
        )

        self.assertFalse(result.ok)
        self.assertIn("COMPILER_PROCESS_REQUIRED", [issue.code for issue in result.issues])

    def test_validate_compiler_inputs_rejects_incompatible_metric_and_process_subjects(
        self,
    ) -> None:
        normalized = normalize_step_request(
            AnalysisStepIR(index=0, step_type="validate", params={"metric": "watch_time"})
        )
        normalized.process_ref = "process.daily_check"
        resolved = resolve_compiler_inputs(
            normalized,
            semantic_repository=_IncompatibleProcessRepository(),
            binding_reader=_binding_reader,
        )
        derived = derive_compiler_state(
            intent_kind="validate",
            resolved_metric=resolved.resolved_metric,
            resolved_process=resolved.resolved_process,
            resolved_bindings=resolved.resolved_bindings,
            profile_reader=None,
        )

        result = validate_compiler_inputs(
            step_type="validate",
            resolved_inputs=resolved,
            derived_state=derived,
        )

        self.assertFalse(result.ok)
        self.assertIn(
            "COMPILER_METRIC_PROCESS_INCOMPATIBLE", [issue.code for issue in result.issues]
        )

    def test_validate_compiler_inputs_rejects_process_profile_that_does_not_satisfy_metric(
        self,
    ) -> None:
        normalized = normalize_step_request(
            AnalysisStepIR(index=0, step_type="validate", params={"metric": "watch_time"})
        )
        normalized.process_ref = "process.daily_check"
        resolved = resolve_compiler_inputs(
            normalized,
            semantic_repository=_IncompatibleProcessRepository(),
            binding_reader=_binding_reader,
        )
        derived = derive_compiler_state(
            intent_kind="validate",
            resolved_metric=resolved.resolved_metric,
            resolved_process=resolved.resolved_process,
            resolved_bindings=resolved.resolved_bindings,
            profile_reader=_profile_reader,
        )

        result = validate_compiler_inputs(
            step_type="validate",
            resolved_inputs=resolved,
            derived_state=derived,
        )

        self.assertFalse(result.ok)
        self.assertIn("COMPILER_PROFILE_NOT_SATISFIED", [issue.code for issue in result.issues])

    def test_validate_compiler_inputs_requires_metric_binding(self) -> None:
        normalized = normalize_step_request(
            AnalysisStepIR(
                index=0,
                step_type="metric_query",
                params={"metric": "watch_time", "table": "analytics.watch_events"},
            )
        )
        resolved = resolve_compiler_inputs(
            normalized,
            semantic_repository=_FakeSemanticRepository(),
            binding_reader=_empty_binding_reader,
        )
        derived = derive_compiler_state(
            intent_kind="metric_query",
            resolved_metric=resolved.resolved_metric,
            resolved_process=resolved.resolved_process,
            resolved_bindings=resolved.resolved_bindings,
            profile_reader=None,
        )

        result = validate_compiler_inputs(
            step_type="metric_query",
            resolved_inputs=resolved,
            derived_state=derived,
        )

        self.assertFalse(result.ok)
        self.assertIn("COMPILER_BINDING_MISSING", [issue.code for issue in result.issues])

    def test_validate_compiler_inputs_rejects_binding_without_carrier_bindings(self) -> None:
        normalized = normalize_step_request(
            AnalysisStepIR(
                index=0,
                step_type="metric_query",
                params={"metric": "watch_time", "table": "analytics.watch_events"},
            )
        )
        resolved = resolve_compiler_inputs(
            normalized,
            semantic_repository=_FakeSemanticRepository(),
            binding_reader=lambda object_ref: [
                _binding_with_interface(
                    object_ref,
                    interface_contract={"carrier_bindings": [], "field_bindings": []},
                )
            ],
        )
        derived = derive_compiler_state(
            intent_kind="metric_query",
            resolved_metric=resolved.resolved_metric,
            resolved_process=resolved.resolved_process,
            resolved_bindings=resolved.resolved_bindings,
            profile_reader=None,
        )

        result = validate_compiler_inputs(
            step_type="metric_query",
            resolved_inputs=resolved,
            derived_state=derived,
        )

        self.assertFalse(result.ok)
        self.assertIn("COMPILER_BINDING_INVALID", [issue.code for issue in result.issues])

    def test_validate_compiler_inputs_rejects_binding_without_field_bindings(self) -> None:
        normalized = normalize_step_request(
            AnalysisStepIR(
                index=0,
                step_type="metric_query",
                params={"metric": "watch_time", "table": "analytics.watch_events"},
            )
        )
        resolved = resolve_compiler_inputs(
            normalized,
            semantic_repository=_FakeSemanticRepository(),
            binding_reader=lambda object_ref: [
                _binding_with_interface(
                    object_ref,
                    interface_contract={
                        "carrier_bindings": [
                            {
                                "binding_key": "primary",
                                "carrier_kind": "table",
                                "carrier_locator": "analytics.watch_events",
                            }
                        ],
                        "field_bindings": [],
                    },
                )
            ],
        )
        derived = derive_compiler_state(
            intent_kind="metric_query",
            resolved_metric=resolved.resolved_metric,
            resolved_process=resolved.resolved_process,
            resolved_bindings=resolved.resolved_bindings,
            profile_reader=None,
        )

        result = validate_compiler_inputs(
            step_type="metric_query",
            resolved_inputs=resolved,
            derived_state=derived,
        )

        self.assertFalse(result.ok)
        self.assertIn("COMPILER_BINDING_INVALID", [issue.code for issue in result.issues])

    def test_validate_compiler_inputs_rejects_binding_with_unknown_carrier_binding_key(
        self,
    ) -> None:
        normalized = normalize_step_request(
            AnalysisStepIR(
                index=0,
                step_type="metric_query",
                params={"metric": "watch_time", "table": "analytics.watch_events"},
            )
        )
        resolved = resolve_compiler_inputs(
            normalized,
            semantic_repository=_FakeSemanticRepository(),
            binding_reader=lambda object_ref: [
                _binding_with_interface(
                    object_ref,
                    interface_contract={
                        "carrier_bindings": [
                            {
                                "binding_key": "primary",
                                "carrier_kind": "table",
                                "carrier_locator": "analytics.watch_events",
                            }
                        ],
                        "field_bindings": [
                            {
                                "carrier_binding_key": "missing",
                                "target": {
                                    "target_kind": "metric_input",
                                    "target_key": "value",
                                },
                                "semantic_ref": "field.metric_value",
                                "surface_ref": "field.metric_value",
                            }
                        ],
                    },
                )
            ],
        )
        derived = derive_compiler_state(
            intent_kind="metric_query",
            resolved_metric=resolved.resolved_metric,
            resolved_process=resolved.resolved_process,
            resolved_bindings=resolved.resolved_bindings,
            profile_reader=None,
        )

        result = validate_compiler_inputs(
            step_type="metric_query",
            resolved_inputs=resolved,
            derived_state=derived,
        )

        self.assertFalse(result.ok)
        self.assertIn("COMPILER_BINDING_INVALID", [issue.code for issue in result.issues])

    def test_validate_compiler_inputs_does_not_treat_grouping_support_as_request_compatibility(
        self,
    ) -> None:
        normalized = normalize_step_request(
            AnalysisStepIR(
                index=0,
                step_type="metric_query",
                params={
                    "metric": "watch_time",
                    "table": "analytics.watch_events",
                    "dimensions": ["dimension.country"],
                },
            )
        )
        resolved = resolve_compiler_inputs(
            normalized,
            semantic_repository=_NonGroupingMetricDimensionRepository(),
            binding_reader=_binding_reader,
        )
        derived = derive_compiler_state(
            intent_kind="metric_query",
            resolved_metric=resolved.resolved_metric,
            resolved_process=resolved.resolved_process,
            resolved_bindings=resolved.resolved_bindings,
            profile_reader=None,
        )

        result = validate_compiler_inputs(
            step_type="metric_query",
            resolved_inputs=resolved,
            derived_state=derived,
        )

        self.assertTrue(result.ok)
        self.assertNotIn("COMPILER_DIMENSION_UNSUPPORTED", [issue.code for issue in result.issues])

    def test_validate_compiler_inputs_allows_imported_dimension_bridge(self) -> None:
        normalized = normalize_step_request(
            AnalysisStepIR(
                index=0,
                step_type="metric_query",
                params={
                    "metric": "watch_time",
                    "table": "analytics.watch_events",
                    "dimensions": ["dimension.cluster"],
                },
            )
        )
        resolved = resolve_compiler_inputs(
            normalized,
            semantic_repository=_ImportedBindingRepository(),
            binding_reader=lambda object_ref: [
                _metric_binding_with_imports(
                    object_ref,
                    imports=[
                        {
                            "import_key": "entity_bridge",
                            "binding_ref": "binding.entity_user",
                            "required_ref_prefixes": ["dimension."],
                        }
                    ],
                )
            ],
        )
        derived = derive_compiler_state(
            intent_kind="metric_query",
            resolved_metric=resolved.resolved_metric,
            resolved_process=resolved.resolved_process,
            resolved_bindings=resolved.resolved_bindings,
            profile_reader=None,
        )

        result = validate_compiler_inputs(
            step_type="metric_query",
            resolved_inputs=resolved,
            derived_state=derived,
        )

        self.assertTrue(result.ok)

    def test_validate_compiler_inputs_rejects_missing_imported_dimension_bridge(self) -> None:
        normalized = normalize_step_request(
            AnalysisStepIR(
                index=0,
                step_type="metric_query",
                params={
                    "metric": "watch_time",
                    "table": "analytics.watch_events",
                    "dimensions": ["dimension.cluster"],
                },
            )
        )
        resolved = resolve_compiler_inputs(
            normalized,
            semantic_repository=_ImportedBindingRepository(),
            binding_reader=_binding_reader,
        )
        derived = derive_compiler_state(
            intent_kind="metric_query",
            resolved_metric=resolved.resolved_metric,
            resolved_process=resolved.resolved_process,
            resolved_bindings=resolved.resolved_bindings,
            profile_reader=None,
        )

        result = validate_compiler_inputs(
            step_type="metric_query",
            resolved_inputs=resolved,
            derived_state=derived,
        )

        self.assertFalse(result.ok)
        issue = next(
            issue for issue in result.issues if issue.code == "COMPILER_DIMENSION_IMPORT_MISSING"
        )
        self.assertEqual(issue.subject_ref, "dimension.cluster")
        self.assertEqual(issue.details["metric_entity_anchor_ref"], "entity.user")
        self.assertEqual(issue.details["available_imported_dimension_refs"], [])

    def test_validate_compiler_inputs_rejects_ambiguous_imported_dimension_bridge(self) -> None:
        repository = _ImportedBindingRepository()
        repository.binding_map["binding.entity_user_alt"] = _entity_binding(
            "binding.entity_user_alt",
            bound_object_ref="entity.user",
            field_bindings=[
                {
                    "carrier_binding_key": "primary",
                    "target": {
                        "target_kind": "stable_descriptor",
                        "target_key": "dimension.cluster",
                    },
                    "semantic_ref": "dimension.cluster",
                    "surface_ref": "field.cluster_alt",
                }
            ],
        )
        normalized = normalize_step_request(
            AnalysisStepIR(
                index=0,
                step_type="metric_query",
                params={
                    "metric": "watch_time",
                    "table": "analytics.watch_events",
                    "dimensions": ["dimension.cluster"],
                },
            )
        )
        resolved = resolve_compiler_inputs(
            normalized,
            semantic_repository=repository,
            binding_reader=lambda object_ref: [
                _metric_binding_with_imports(
                    object_ref,
                    imports=[
                        {
                            "import_key": "entity_bridge",
                            "binding_ref": "binding.entity_user",
                            "required_ref_prefixes": ["dimension."],
                        },
                        {
                            "import_key": "entity_bridge_alt",
                            "binding_ref": "binding.entity_user_alt",
                            "required_ref_prefixes": ["dimension."],
                        },
                    ],
                )
            ],
        )
        derived = derive_compiler_state(
            intent_kind="metric_query",
            resolved_metric=resolved.resolved_metric,
            resolved_process=resolved.resolved_process,
            resolved_bindings=resolved.resolved_bindings,
            profile_reader=None,
        )

        result = validate_compiler_inputs(
            step_type="metric_query",
            resolved_inputs=resolved,
            derived_state=derived,
        )

        self.assertFalse(result.ok)
        issue = next(
            issue for issue in result.issues if issue.code == "COMPILER_DIMENSION_IMPORT_AMBIGUOUS"
        )
        self.assertEqual(issue.subject_ref, "dimension.cluster")
        self.assertEqual(
            [candidate["source_binding_ref"] for candidate in issue.details["candidates"]],
            ["binding.entity_user", "binding.entity_user_alt"],
        )

    def test_validate_compiler_inputs_rejects_non_exported_dimension_without_entity_anchor(
        self,
    ) -> None:
        normalized = normalize_step_request(
            AnalysisStepIR(
                index=0,
                step_type="metric_query",
                params={
                    "metric": "watch_time",
                    "table": "analytics.watch_events",
                    "dimensions": ["dimension.cluster"],
                },
            )
        )
        resolved = resolve_compiler_inputs(
            normalized,
            semantic_repository=_NoAnchorMetricRepository(),
            binding_reader=_binding_reader,
        )
        derived = derive_compiler_state(
            intent_kind="metric_query",
            resolved_metric=resolved.resolved_metric,
            resolved_process=resolved.resolved_process,
            resolved_bindings=resolved.resolved_bindings,
            profile_reader=None,
        )

        result = validate_compiler_inputs(
            step_type="metric_query",
            resolved_inputs=resolved,
            derived_state=derived,
        )

        self.assertFalse(result.ok)
        issue = next(
            issue for issue in result.issues if issue.code == "COMPILER_DIMENSION_NOT_EXPORTED"
        )
        self.assertEqual(issue.subject_ref, "dimension.cluster")
        self.assertEqual(issue.details["available_metric_dimension_refs"], [])

    def test_validate_compiler_inputs_rejects_dimension_time_anchor_mismatch(self) -> None:
        normalized = normalize_step_request(
            AnalysisStepIR(
                index=0,
                step_type="metric_query",
                params={
                    "metric": "watch_time",
                    "table": "analytics.watch_events",
                    "dimensions": ["dimension.country"],
                },
            )
        )
        resolved = resolve_compiler_inputs(
            normalized,
            semantic_repository=_TimeAnchoredDimensionRepository(),
            binding_reader=_binding_reader,
        )
        derived = derive_compiler_state(
            intent_kind="metric_query",
            resolved_metric=resolved.resolved_metric,
            resolved_process=resolved.resolved_process,
            resolved_bindings=resolved.resolved_bindings,
            profile_reader=None,
        )

        result = validate_compiler_inputs(
            step_type="metric_query",
            resolved_inputs=resolved,
            derived_state=derived,
        )

        self.assertFalse(result.ok)
        self.assertIn(
            "COMPILER_DIMENSION_TIME_ANCHOR_MISMATCH",
            [issue.code for issue in result.issues],
        )
        issue = next(
            issue
            for issue in result.issues
            if issue.code == "COMPILER_DIMENSION_TIME_ANCHOR_MISMATCH"
        )
        self.assertEqual(issue.category, "compatibility")

    def test_validate_compiler_inputs_checks_time_anchor_after_import_bridge_resolution(
        self,
    ) -> None:
        normalized = normalize_step_request(
            AnalysisStepIR(
                index=0,
                step_type="metric_query",
                params={
                    "metric": "watch_time",
                    "table": "analytics.watch_events",
                    "dimensions": ["dimension.cluster"],
                },
            )
        )
        resolved = resolve_compiler_inputs(
            normalized,
            semantic_repository=_TimeAnchoredImportedBindingRepository(),
            binding_reader=lambda object_ref: [
                _metric_binding_with_imports(
                    object_ref,
                    imports=[
                        {
                            "import_key": "entity_bridge",
                            "binding_ref": "binding.entity_user",
                            "required_ref_prefixes": ["dimension."],
                        }
                    ],
                )
            ],
        )
        derived = derive_compiler_state(
            intent_kind="metric_query",
            resolved_metric=resolved.resolved_metric,
            resolved_process=resolved.resolved_process,
            resolved_bindings=resolved.resolved_bindings,
            profile_reader=None,
        )

        result = validate_compiler_inputs(
            step_type="metric_query",
            resolved_inputs=resolved,
            derived_state=derived,
        )

        self.assertFalse(result.ok)
        self.assertIn(
            "COMPILER_DIMENSION_TIME_ANCHOR_MISMATCH",
            [issue.code for issue in result.issues],
        )
        self.assertNotIn(
            "COMPILER_DIMENSION_IMPORT_MISSING",
            [issue.code for issue in result.issues],
        )

    def test_compile_step_raises_structured_request_compatibility_error(self) -> None:
        with self.assertRaises(SemanticRequestCompatibilityError) as ctx:
            compile_step(
                AnalysisStepIR(
                    index=0,
                    step_type="metric_query",
                    params={
                        "metric": "watch_time",
                        "table": "analytics.watch_events",
                        "dimensions": ["dimension.country"],
                    },
                ),
                engine_type="duckdb",
                semantic_context={
                    "semantic_repository": _TimeAnchoredDimensionRepository(),
                    "binding_reader": _binding_reader,
                },
            )

        detail = ctx.exception.detail
        self.assertEqual(detail["code"], "semantic_request_incompatible")
        self.assertEqual(detail["category"], "compatibility")
        self.assertEqual(detail["subject_ref"], "dimension.country")
        self.assertEqual(
            detail["issues"][0]["code"],
            "COMPILER_DIMENSION_TIME_ANCHOR_MISMATCH",
        )

    def test_compile_step_raises_import_missing_request_compatibility_error(self) -> None:
        with self.assertRaises(SemanticRequestCompatibilityError) as ctx:
            compile_step(
                AnalysisStepIR(
                    index=0,
                    step_type="metric_query",
                    params={
                        "metric": "watch_time",
                        "table": "analytics.watch_events",
                        "dimensions": ["dimension.cluster"],
                    },
                ),
                engine_type="duckdb",
                semantic_context={
                    "semantic_repository": _ImportedBindingRepository(),
                    "binding_reader": _binding_reader,
                },
            )

        detail = ctx.exception.detail
        self.assertEqual(detail["code"], "semantic_request_incompatible")
        self.assertEqual(detail["subject_ref"], "dimension.cluster")
        self.assertEqual(
            detail["issues"][0]["code"],
            "COMPILER_DIMENSION_IMPORT_MISSING",
        )
        self.assertEqual(detail["request_context"]["dimension_refs"], ["dimension.cluster"])

    def test_compile_step_records_imported_dimension_lineage_in_metadata(self) -> None:
        compiled = compile_step(
            AnalysisStepIR(
                index=0,
                step_type="metric_query",
                params={
                    "metric": "watch_time",
                    "table": "analytics.watch_events",
                    "dimensions": ["dimension.cluster"],
                },
            ),
            engine_type="duckdb",
            semantic_context={
                "metric_sql": "avg(play_duration_seconds)",
                "dimensions": ["dimension.cluster"],
                "semantic_repository": _UniqueImportedBindingRepository(),
                "binding_reader": lambda object_ref: [
                    _metric_binding_with_imports(
                        object_ref,
                        imports=[
                            {
                                "import_key": "entity_bridge",
                                "binding_ref": "binding.entity_user",
                                "required_ref_prefixes": ["dimension."],
                            }
                        ],
                    )
                ],
            },
        )

        self.assertEqual(compiled.metadata["metric_entity_anchor_ref"], "entity.user")
        self.assertEqual(
            compiled.metadata["resolved_imported_dimensions"],
            [
                {
                    "dimension_ref": "dimension.cluster",
                    "source_binding_ref": "binding.entity_user",
                    "source_entity_ref": "entity.user",
                    "import_key": "entity_bridge",
                },
                {
                    "dimension_ref": "dimension.country",
                    "source_binding_ref": "binding.entity_user",
                    "source_entity_ref": "entity.user",
                    "import_key": "entity_bridge",
                },
            ],
        )
        self.assertEqual(
            compiled.metadata["resolved_imported_dimension_sources"],
            [
                {
                    "dimension_ref": "dimension.cluster",
                    "source_binding_ref": "binding.entity_user",
                    "source_entity_ref": "entity.user",
                    "import_key": "entity_bridge",
                    "carrier_binding_key": "primary",
                    "carrier_locator": "analytics.entity_events",
                    "surface_ref": "field.cluster",
                    "physical_name": "cluster",
                }
            ],
        )

    def test_compile_step_rejects_imported_dimension_without_unique_field_lineage(self) -> None:
        with self.assertRaises(SemanticRequestCompatibilityError) as ctx:
            compile_step(
                AnalysisStepIR(
                    index=0,
                    step_type="metric_query",
                    params={
                        "metric": "watch_time",
                        "table": "analytics.watch_events",
                        "dimensions": ["dimension.cluster"],
                    },
                ),
                engine_type="duckdb",
                semantic_context={
                    "semantic_repository": _ImportedBindingRepository(),
                    "binding_reader": lambda object_ref: [
                        _metric_binding_with_imports(
                            object_ref,
                            imports=[
                                {
                                    "import_key": "entity_bridge",
                                    "binding_ref": "binding.entity_user",
                                    "required_ref_prefixes": ["dimension."],
                                }
                            ],
                        )
                    ],
                },
            )

        self.assertEqual(
            ctx.exception.detail["issues"][0]["code"],
            "COMPILER_DIMENSION_IMPORT_LINEAGE_MISSING",
        )

    def test_compile_step_rejects_imported_dimension_without_physical_carrier_source(self) -> None:
        repository = _ImportedBindingRepository()
        repository.binding_map["binding.entity_user"] = _entity_binding(
            "binding.entity_user",
            bound_object_ref="entity.user",
            carrier_bindings=[
                {
                    "binding_key": "primary",
                    "carrier_kind": "table",
                }
            ],
            field_bindings=[
                {
                    "carrier_binding_key": "primary",
                    "target": {
                        "target_kind": "stable_descriptor",
                        "target_key": "dimension.cluster",
                    },
                    "semantic_ref": "dimension.cluster",
                    "surface_ref": "field.cluster",
                }
            ],
        )
        with self.assertRaises(SemanticRequestCompatibilityError) as ctx:
            compile_step(
                AnalysisStepIR(
                    index=0,
                    step_type="metric_query",
                    params={
                        "metric": "watch_time",
                        "table": "analytics.watch_events",
                        "dimensions": ["dimension.cluster"],
                    },
                ),
                engine_type="duckdb",
                semantic_context={
                    "semantic_repository": repository,
                    "binding_reader": lambda object_ref: [
                        _metric_binding_with_imports(
                            object_ref,
                            imports=[
                                {
                                    "import_key": "entity_bridge",
                                    "binding_ref": "binding.entity_user",
                                    "required_ref_prefixes": ["dimension."],
                                }
                            ],
                        )
                    ],
                },
            )

        self.assertEqual(
            ctx.exception.detail["issues"][0]["code"],
            "COMPILER_DIMENSION_IMPORT_PHYSICAL_UNRESOLVED",
        )

    def test_compile_step_prefers_non_compatibility_error_when_mixed_with_compatibility(
        self,
    ) -> None:
        with self.assertRaises(SemanticCompilerError) as ctx:
            compile_step(
                AnalysisStepIR(
                    index=0,
                    step_type="metric_query",
                    params={
                        "metric": "watch_time",
                        "table": "analytics.watch_events",
                        "dimensions": ["dimension.country"],
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
                    "semantic_repository": _MissingTimeAnchoredDimensionRepository(),
                    "binding_reader": _binding_reader,
                },
            )

        self.assertEqual(
            ctx.exception.compile_error["error_code"],
            "COMPILER_TIME_REF_UNRESOLVED",
        )


if __name__ == "__main__":
    unittest.main()
