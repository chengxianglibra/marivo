from __future__ import annotations

import unittest

from app.semantic_readiness import (
    ReadinessEvaluationContext,
    SemanticReadinessService,
    UnknownSemanticReadinessKindError,
    build_default_registry,
    build_snapshot,
)


class SemanticReadinessRegistryTests(unittest.TestCase):
    def test_default_registry_covers_all_object_kinds(self) -> None:
        registry = build_default_registry()

        for object_kind in (
            "entity",
            "metric",
            "process",
            "dimension",
            "time",
            "enum",
            "binding",
            "compiler_profile",
        ):
            self.assertIsNotNone(registry.evaluator_for(object_kind))

    def test_unknown_object_kind_raises(self) -> None:
        registry = build_default_registry()

        with self.assertRaises(UnknownSemanticReadinessKindError):
            registry.evaluator_for("asset")  # type: ignore[arg-type]

    def test_binding_stays_on_placeholder_evaluator(self) -> None:
        service = SemanticReadinessService()

        result = service.evaluate_snapshot(
            object_kind="binding",
            object_id="bind_123",
            ref="binding.watch_time",
            status="published",
            revision=4,
            semantic_object={"header": {"binding_ref": "binding.watch_time"}},
        )

        self.assertEqual(result.trace[0].source, "binding_placeholder_evaluator")


class ReadinessEvaluationContextTests(unittest.TestCase):
    def test_context_loaders_are_lazy(self) -> None:
        calls: list[str] = []
        snapshot = build_snapshot(
            object_kind="entity",
            object_id="entc_123",
            ref="entity.user",
            status="draft",
            revision=1,
            semantic_object={"header": {"entity_ref": "entity.user"}},
        )
        context = ReadinessEvaluationContext(
            snapshot=snapshot,
            dependency_snapshot_loader=lambda ref: calls.append(f"snapshot:{ref}") or None,
            subject_bindings_loader=lambda ref: calls.append(f"bindings:{ref}") or [],
        )

        self.assertEqual(calls, [])
        self.assertIsNone(context.load_dependency_snapshot("entity.account"))
        self.assertEqual(calls, ["snapshot:entity.account"])


class EntityReadinessEvaluatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = build_default_registry()

    def test_active_entity_without_grounding_requirement_is_ready(self) -> None:
        result = self._evaluate(
            semantic_object={
                "header": {"entity_ref": "entity.user"},
                "interface_contract": {
                    "identity": {
                        "key_refs": ["key.user_id"],
                        "uniqueness_scope": "global",
                        "id_stability": "stable",
                    }
                },
            }
        )

        self.assertEqual(result.lifecycle_status, "active")
        self.assertEqual(result.readiness_status, "ready")
        self.assertEqual(result.blocking_requirements, [])

    def test_active_entity_with_missing_identity_contract_is_not_ready(self) -> None:
        result = self._evaluate(
            semantic_object={
                "header": {"entity_ref": "entity.user"},
                "interface_contract": {"identity": {"key_refs": []}},
            }
        )

        self.assertEqual(result.readiness_status, "not_ready")
        self.assertEqual(result.blocking_requirements[0].code, "ENTITY_CONTRACT_INVALID")

    def test_active_entity_requires_binding_coverage_when_grounding_is_required(self) -> None:
        result = self._evaluate(
            semantic_object={
                "header": {"entity_ref": "entity.user"},
                "interface_contract": {
                    "identity": {
                        "key_refs": ["key.user_id"],
                        "uniqueness_scope": "global",
                        "id_stability": "stable",
                    },
                    "primary_time_ref": "time.signup_date",
                    "stable_descriptors": [{"dimension_ref": "dimension.country"}],
                },
            },
            require_physical_grounding=True,
            subject_bindings=[
                self._binding(
                    binding_ref="binding.user_identity",
                    binding_scope="entity",
                    field_bindings=[
                        self._field_binding("identity_key", "key.user_id", "key.user_id"),
                    ],
                )
            ],
        )

        self.assertEqual(result.readiness_status, "not_ready")
        self.assertEqual(
            {item.code for item in result.blocking_requirements},
            {"ENTITY_BINDING_COVERAGE_MISSING"},
        )

    def _evaluate(
        self,
        *,
        semantic_object: dict[str, object],
        require_physical_grounding: bool = False,
        subject_bindings: list[dict[str, object]] | None = None,
    ):
        snapshot = build_snapshot(
            object_kind="entity",
            object_id="entc_123",
            ref="entity.user",
            status="published",
            revision=3,
            semantic_object=semantic_object,
        )
        context = ReadinessEvaluationContext(
            snapshot=snapshot,
            require_physical_grounding=require_physical_grounding,
            subject_bindings_loader=lambda _ref: list(subject_bindings or []),
            carrier_source_object_loader=lambda _carrier: {"object_id": "src_123"},
        )
        return self.registry.evaluator_for("entity").evaluate(context)

    def _binding(
        self,
        *,
        binding_ref: str,
        binding_scope: str,
        field_bindings: list[dict[str, object]],
    ) -> dict[str, object]:
        return {
            "binding_ref": binding_ref,
            "binding_scope": binding_scope,
            "bound_object_ref": "entity.user",
            "status": "published",
            "interface_contract": {
                "imports": [],
                "carrier_bindings": [
                    {"binding_key": "primary", "carrier_locator": "warehouse.entity_table"}
                ],
                "field_bindings": field_bindings,
            },
        }

    def _field_binding(
        self, target_kind: str, target_key: str, semantic_ref: str
    ) -> dict[str, object]:
        return {
            "target": {"target_kind": target_kind, "target_key": target_key},
            "semantic_ref": semantic_ref,
        }


class MetricReadinessEvaluatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = build_default_registry()

    def test_published_metric_without_binding_is_active_but_not_ready(self) -> None:
        result = self._evaluate()

        self.assertEqual(result.lifecycle_status, "active")
        self.assertEqual(result.readiness_status, "not_ready")
        self.assertEqual(result.blocking_requirements[0].code, "METRIC_BINDING_MISSING")
        self.assertEqual(
            result.capabilities,
            {
                "supports_observe": True,
                "supports_attribute": True,
                "supports_diagnose": True,
                "supports_detect": True,
                "supports_validate": True,
                "supports_decompose": True,
            },
        )

    def test_metric_binding_requires_all_metric_inputs(self) -> None:
        result = self._evaluate(
            subject_bindings=[
                self._binding(
                    field_bindings=[
                        self._field_binding("metric_input", "numerator", "metric_input.converted"),
                    ]
                )
            ]
        )

        self.assertEqual(result.readiness_status, "not_ready")
        self.assertEqual(result.blocking_requirements[0].code, "METRIC_INPUT_COVERAGE_MISSING")

    def test_metric_dependency_must_be_active(self) -> None:
        result = self._evaluate(
            dependency_statuses={"entity.user": "draft"},
            subject_bindings=[
                self._binding(
                    field_bindings=[
                        self._field_binding("metric_input", "numerator", "metric_input.converted"),
                        self._field_binding("metric_input", "denominator", "metric_input.eligible"),
                        self._field_binding("primary_time", "time.event_date", "time.event_date"),
                    ]
                )
            ],
        )

        self.assertEqual(result.readiness_status, "not_ready")
        self.assertIn(
            "METRIC_DEPENDENCY_INACTIVE", {item.code for item in result.blocking_requirements}
        )

    def test_metric_with_active_dependencies_and_complete_binding_is_ready(self) -> None:
        result = self._evaluate(
            subject_bindings=[
                self._binding(
                    field_bindings=[
                        self._field_binding("metric_input", "numerator", "metric_input.converted"),
                        self._field_binding("metric_input", "denominator", "metric_input.eligible"),
                        self._field_binding("primary_time", "time.event_date", "time.event_date"),
                    ]
                )
            ],
        )

        self.assertEqual(result.readiness_status, "ready")
        self.assertEqual(result.blocking_requirements, [])

    def _evaluate(
        self,
        *,
        dependency_statuses: dict[str, str] | None = None,
        subject_bindings: list[dict[str, object]] | None = None,
        binding_import_statuses: dict[str, str] | None = None,
    ):
        semantic_object = {
            "header": {
                "metric_ref": "metric.conversion_rate",
                "metric_family": "rate_metric",
                "observed_entity_ref": "entity.user",
                "observation_grain_ref": "grain.user",
                "sample_kind": "rate",
                "value_semantics": "ratio",
                "additivity": "additive",
                "primary_time_ref": "time.event_date",
            },
            "payload": {
                "metric_family": "rate_metric",
                "numerator": {"name": "converted"},
                "denominator": {"name": "eligible"},
            },
        }
        dependency_statuses = dependency_statuses or {
            "entity.user": "published",
            "time.event_date": "published",
        }
        binding_import_statuses = binding_import_statuses or {}
        snapshot = build_snapshot(
            object_kind="metric",
            object_id="metc_123",
            ref="metric.conversion_rate",
            status="published",
            revision=2,
            semantic_object=semantic_object,
        )

        def dependency_loader(ref: str):
            # Handle binding import dependencies
            if ref.startswith("binding."):
                status = binding_import_statuses.get(ref, "published")
                if status == "missing":
                    return None
                return build_snapshot(
                    object_kind="binding",
                    object_id=f"{ref}_id",
                    ref=ref,
                    status=status,
                    revision=1,
                    semantic_object={"header": {"binding_ref": ref}},
                )
            # Handle entity/time dependencies
            if ref in dependency_statuses:
                return build_snapshot(
                    object_kind="entity" if ref.startswith("entity.") else "time",
                    object_id=f"{ref}_id",
                    ref=ref,
                    status=dependency_statuses[ref],
                    revision=1,
                    semantic_object={"header": {}},
                )
            return None

        context = ReadinessEvaluationContext(
            snapshot=snapshot,
            dependency_snapshot_loader=dependency_loader,
            subject_bindings_loader=lambda _ref: list(subject_bindings or []),
            carrier_source_object_loader=lambda _carrier: {"object_id": "src_123"},
        )
        return self.registry.evaluator_for("metric").evaluate(context)

    def _binding(self, *, field_bindings: list[dict[str, object]]) -> dict[str, object]:
        return {
            "binding_ref": "binding.metric_conversion_rate",
            "binding_scope": "metric",
            "bound_object_ref": "metric.conversion_rate",
            "status": "published",
            "interface_contract": {
                "imports": [],
                "carrier_bindings": [
                    {"binding_key": "metric_fact", "carrier_locator": "warehouse.metric_fact"}
                ],
                "field_bindings": field_bindings,
            },
        }

    def _field_binding(
        self, target_kind: str, target_key: str, semantic_ref: str
    ) -> dict[str, object]:
        return {
            "target": {"target_kind": target_kind, "target_key": target_key},
            "semantic_ref": semantic_ref,
        }

    def _binding_with_import(
        self, *, field_bindings: list[dict[str, object]], imported_binding_ref: str
    ) -> dict[str, object]:
        """Create a binding with an import for testing."""
        return {
            "binding_ref": "binding.metric_conversion_rate",
            "binding_scope": "metric",
            "bound_object_ref": "metric.conversion_rate",
            "status": "published",
            "interface_contract": {
                "imports": [{"imported_binding_ref": imported_binding_ref}],
                "carrier_bindings": [
                    {"binding_key": "metric_fact", "carrier_locator": "warehouse.metric_fact"}
                ],
                "field_bindings": field_bindings,
            },
        }

    def test_metric_binding_with_missing_import_is_not_ready(self) -> None:
        """Binding with missing import should be blocked."""
        result = self._evaluate(
            binding_import_statuses={"binding.missing_dependency": "missing"},
            subject_bindings=[
                self._binding_with_import(
                    field_bindings=[
                        self._field_binding("metric_input", "numerator", "metric_input.converted"),
                        self._field_binding("metric_input", "denominator", "metric_input.eligible"),
                        self._field_binding("primary_time", "time.event_date", "time.event_date"),
                    ],
                    imported_binding_ref="binding.missing_dependency",
                )
            ],
        )
        self.assertEqual(result.readiness_status, "not_ready")
        blocker_codes = {item.code for item in result.blocking_requirements}
        self.assertIn("METRIC_BINDING_IMPORT_MISSING", blocker_codes)

    def test_metric_binding_with_inactive_import_is_not_ready(self) -> None:
        """Binding with inactive (draft) import should be blocked."""
        result = self._evaluate(
            binding_import_statuses={"binding.draft_dependency": "draft"},
            subject_bindings=[
                self._binding_with_import(
                    field_bindings=[
                        self._field_binding("metric_input", "numerator", "metric_input.converted"),
                        self._field_binding("metric_input", "denominator", "metric_input.eligible"),
                        self._field_binding("primary_time", "time.event_date", "time.event_date"),
                    ],
                    imported_binding_ref="binding.draft_dependency",
                )
            ],
        )
        self.assertEqual(result.readiness_status, "not_ready")
        blocker_codes = {item.code for item in result.blocking_requirements}
        self.assertIn("METRIC_BINDING_IMPORT_MISSING", blocker_codes)


class ProcessReadinessEvaluatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = build_default_registry()

    def test_process_without_profile_stays_ready_but_marks_inferential_gap(self) -> None:
        result = self._evaluate()

        self.assertEqual(result.lifecycle_status, "active")
        self.assertEqual(result.readiness_status, "ready")
        self.assertEqual(result.capabilities["inferential_ready"], False)
        self.assertEqual(result.blocking_requirements[0].code, "PROCESS_PROFILE_MISSING")

    def test_process_profile_revision_mismatch_does_not_flip_basic_readiness(self) -> None:
        result = self._evaluate(
            profiles=[
                {
                    "profile_kind": "capability",
                    "profile_ref": "compiler_profile.exp_capability",
                    "subject_revision": 1,
                    "capability": {"inferential_ready": True},
                }
            ]
        )

        self.assertEqual(result.readiness_status, "ready")
        self.assertEqual(result.capabilities["inferential_ready"], False)
        self.assertEqual(result.blocking_requirements[0].code, "PROCESS_PROFILE_MISMATCH")

    def test_process_requires_binding_when_grounding_is_requested(self) -> None:
        result = self._evaluate(require_physical_grounding=True)

        self.assertEqual(result.readiness_status, "not_ready")
        self.assertIn(
            "PROCESS_BINDING_MISSING", {item.code for item in result.blocking_requirements}
        )

    def test_process_binding_and_matching_profile_make_it_ready(self) -> None:
        result = self._evaluate(
            require_physical_grounding=True,
            subject_bindings=[
                {
                    "binding_ref": "binding.exp_assignment",
                    "binding_scope": "process_object",
                    "bound_object_ref": "process.experiment_assignment",
                    "status": "published",
                    "interface_contract": {
                        "imports": [],
                        "carrier_bindings": [
                            {
                                "binding_key": "assignment",
                                "carrier_locator": "warehouse.exp_assignment",
                            }
                        ],
                        "field_bindings": [
                            {
                                "target": {
                                    "target_kind": "population_subject",
                                    "target_key": "subject.user",
                                },
                                "semantic_ref": "subject.user",
                            },
                            {
                                "target": {
                                    "target_kind": "analysis_window_anchor",
                                    "target_key": "time.assignment_date",
                                },
                                "semantic_ref": "time.assignment_date",
                            },
                        ],
                    },
                }
            ],
            profiles=[
                {
                    "profile_kind": "capability",
                    "profile_ref": "compiler_profile.exp_capability",
                    "subject_revision": 3,
                    "capability": {"inferential_ready": True},
                }
            ],
        )

        self.assertEqual(result.readiness_status, "ready")
        self.assertEqual(result.blocking_requirements, [])
        self.assertEqual(result.capabilities["inferential_ready"], True)

    def _evaluate(
        self,
        *,
        require_physical_grounding: bool = False,
        subject_bindings: list[dict[str, object]] | None = None,
        profiles: list[dict[str, object]] | None = None,
    ):
        snapshot = build_snapshot(
            object_kind="process",
            object_id="proc_123",
            ref="process.experiment_assignment",
            status="published",
            revision=3,
            semantic_object={
                "header": {
                    "process_ref": "process.experiment_assignment",
                    "process_type": "experiment_context",
                },
                "interface_contract": {
                    "contract_mode": "context_provider",
                    "context_kind": "experiment_split",
                    "population_subject_ref": "subject.user",
                    "anchor_time_ref": "time.assignment_date",
                },
                "payload": {"analysis_window": {"size": {"value": 7, "unit": "day"}}},
            },
        )
        context = ReadinessEvaluationContext(
            snapshot=snapshot,
            require_physical_grounding=require_physical_grounding,
            subject_bindings_loader=lambda _ref: list(subject_bindings or []),
            profiles_loader=lambda _kind, _ref: list(profiles or []),
            carrier_source_object_loader=lambda _carrier: {"object_id": "src_123"},
        )
        return self.registry.evaluator_for("process").evaluate(context)


if __name__ == "__main__":
    unittest.main()
