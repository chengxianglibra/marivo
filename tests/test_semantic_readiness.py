from __future__ import annotations

import unittest

from app.semantic_readiness import (
    ReadinessEvaluationContext,
    ReadinessResult,
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

    def test_binding_uses_concrete_evaluator(self) -> None:
        service = SemanticReadinessService()

        result = service.evaluate_snapshot(
            object_kind="binding",
            object_id="bind_123",
            ref="binding.watch_time",
            status="published",
            revision=4,
            semantic_object={"header": {"binding_ref": "binding.watch_time"}},
        )

        self.assertEqual(result.trace[0].source, "binding_readiness_evaluator")


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

    def test_active_entity_with_drifted_binding_is_stale(self) -> None:
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
                        self._field_binding(
                            "primary_time",
                            "time.signup_date",
                            "time.signup_date",
                        ),
                        self._field_binding(
                            "stable_descriptor",
                            "dimension.country",
                            "dimension.country",
                        ),
                    ],
                )
            ],
            carrier_source_object_loader=lambda _carrier: None,
        )

        self.assertEqual(result.readiness_status, "stale")
        self.assertEqual(
            {item.code for item in result.blocking_requirements},
            {"ENTITY_CARRIER_SOURCE_MISSING"},
        )

    def test_draft_entity_is_not_ready(self) -> None:
        result = self._evaluate(
            status="draft",
            semantic_object={
                "header": {"entity_ref": "entity.user"},
                "interface_contract": {
                    "identity": {
                        "key_refs": ["key.user_id"],
                        "uniqueness_scope": "global",
                        "id_stability": "stable",
                    }
                },
            },
        )

        self.assertEqual(result.lifecycle_status, "draft")
        self.assertEqual(result.readiness_status, "not_ready")

    def test_deprecated_entity_is_not_ready(self) -> None:
        result = self._evaluate(
            status="deprecated",
            semantic_object={
                "header": {"entity_ref": "entity.user"},
                "interface_contract": {
                    "identity": {
                        "key_refs": ["key.user_id"],
                        "uniqueness_scope": "global",
                        "id_stability": "stable",
                    }
                },
            },
        )

        self.assertEqual(result.lifecycle_status, "deprecated")
        self.assertEqual(result.readiness_status, "not_ready")

    def _evaluate(
        self,
        *,
        semantic_object: dict[str, object],
        status: str = "published",
        require_physical_grounding: bool = False,
        subject_bindings: list[dict[str, object]] | None = None,
        carrier_source_object_loader=None,
    ):
        snapshot = build_snapshot(
            object_kind="entity",
            object_id="entc_123",
            ref="entity.user",
            status=status,
            revision=3,
            semantic_object=semantic_object,
        )
        context = ReadinessEvaluationContext(
            snapshot=snapshot,
            require_physical_grounding=require_physical_grounding,
            subject_bindings_loader=lambda _ref: list(subject_bindings or []),
            carrier_source_object_loader=carrier_source_object_loader
            or (lambda _carrier: {"object_id": "src_123"}),
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

    def test_metric_readiness_does_not_require_imported_dimension_bridge(self) -> None:
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
        self.assertNotIn(
            "METRIC_IMPORTED_DIMENSION_BRIDGE_MISSING",
            {item.code for item in result.blocking_requirements},
        )

    def test_metric_with_drifted_binding_is_stale(self) -> None:
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
            carrier_source_object_loader=lambda _carrier: None,
        )

        self.assertEqual(result.readiness_status, "stale")
        self.assertEqual(
            {item.code for item in result.blocking_requirements},
            {"METRIC_CARRIER_SOURCE_MISSING"},
        )

    def test_draft_metric_is_not_ready(self) -> None:
        result = self._evaluate(status="draft")

        self.assertEqual(result.lifecycle_status, "draft")
        self.assertEqual(result.readiness_status, "not_ready")

    def test_deprecated_metric_is_not_ready(self) -> None:
        result = self._evaluate(status="deprecated")

        self.assertEqual(result.lifecycle_status, "deprecated")
        self.assertEqual(result.readiness_status, "not_ready")

    def _evaluate(
        self,
        *,
        status: str = "published",
        dependency_statuses: dict[str, str] | None = None,
        subject_bindings: list[dict[str, object]] | None = None,
        binding_import_statuses: dict[str, str] | None = None,
        carrier_source_object_loader=None,
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
            status=status,
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
            carrier_source_object_loader=carrier_source_object_loader
            or (lambda _carrier: {"object_id": "src_123"}),
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
        self.assertEqual(result.readiness_status, "stale")
        blocker_codes = {item.code for item in result.blocking_requirements}
        self.assertIn("METRIC_BINDING_IMPORT_MISSING", blocker_codes)

    def test_metric_binding_with_inactive_import_is_stale(self) -> None:
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
        self.assertEqual(result.readiness_status, "stale")
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

    def test_process_profile_revision_mismatch_is_stale(self) -> None:
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

        self.assertEqual(result.readiness_status, "stale")
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

    def test_process_with_drifted_grounding_is_stale(self) -> None:
        result = self._evaluate(
            require_physical_grounding=True,
            carrier_source_object_loader=lambda _carrier: None,
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

        self.assertEqual(result.readiness_status, "stale")
        self.assertEqual(
            {item.code for item in result.blocking_requirements},
            {"PROCESS_CARRIER_SOURCE_MISSING"},
        )

    def test_draft_process_is_not_ready(self) -> None:
        result = self._evaluate(status="draft")

        self.assertEqual(result.lifecycle_status, "draft")
        self.assertEqual(result.readiness_status, "not_ready")

    def test_deprecated_process_is_not_ready(self) -> None:
        result = self._evaluate(status="deprecated")

        self.assertEqual(result.lifecycle_status, "deprecated")
        self.assertEqual(result.readiness_status, "not_ready")

    def _evaluate(
        self,
        *,
        status: str = "published",
        require_physical_grounding: bool = False,
        subject_bindings: list[dict[str, object]] | None = None,
        profiles: list[dict[str, object]] | None = None,
        carrier_source_object_loader=None,
    ):
        snapshot = build_snapshot(
            object_kind="process",
            object_id="proc_123",
            ref="process.experiment_assignment",
            status=status,
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
            carrier_source_object_loader=carrier_source_object_loader
            or (lambda _carrier: {"object_id": "src_123"}),
        )
        return self.registry.evaluator_for("process").evaluate(context)


class DimensionReadinessEvaluatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = build_default_registry()

    def test_active_dimension_with_grouping_is_ready(self) -> None:
        result = self._evaluate(
            semantic_object={
                "header": {"dimension_ref": "dimension.country"},
                "interface_contract": {
                    "value_domain": {
                        "structure_kind": "flat",
                        "semantic_role": "category",
                        "value_type": "string",
                        "domain_kind": "enumerated",
                    },
                    "grouping": {"supports_grouping": True},
                },
            }
        )

        self.assertEqual(result.readiness_status, "ready")
        self.assertEqual(result.capabilities["supports_grouping"], True)

    def test_time_derived_dimension_requires_time_anchor(self) -> None:
        result = self._evaluate(
            semantic_object={
                "header": {"dimension_ref": "dimension.signup_week"},
                "interface_contract": {
                    "value_domain": {
                        "structure_kind": "time_derived",
                        "semantic_role": "temporal_bucket",
                        "value_type": "string",
                        "domain_kind": "open",
                    }
                },
            }
        )

        self.assertEqual(result.readiness_status, "not_ready")
        self.assertEqual(
            result.blocking_requirements[0].code,
            "DIMENSION_TIME_DERIVED_REQUIREMENT_MISSING",
        )

    def test_dimension_without_grouping_support_is_not_ready(self) -> None:
        result = self._evaluate(
            semantic_object={
                "header": {"dimension_ref": "dimension.country"},
                "interface_contract": {
                    "value_domain": {
                        "structure_kind": "flat",
                        "semantic_role": "category",
                        "value_type": "string",
                        "domain_kind": "enumerated",
                    },
                    "grouping": {"supports_grouping": False},
                },
            }
        )

        self.assertEqual(result.readiness_status, "not_ready")
        self.assertEqual(result.blocking_requirements[0].code, "DIMENSION_GROUPING_UNSUPPORTED")

    def _evaluate(self, *, semantic_object: dict[str, object], status: str = "published"):
        snapshot = build_snapshot(
            object_kind="dimension",
            object_id="dimc_123",
            ref="dimension.country",
            status=status,
            revision=2,
            semantic_object=semantic_object,
        )
        return self.registry.evaluator_for("dimension").evaluate(
            ReadinessEvaluationContext(snapshot=snapshot)
        )

    def test_dimension_with_invalid_contract_is_not_ready(self) -> None:
        result = self._evaluate(
            semantic_object={
                "header": {},
                "interface_contract": {
                    "value_domain": {},
                },
            }
        )

        self.assertEqual(result.readiness_status, "not_ready")
        self.assertEqual(result.blocking_requirements[0].code, "DIMENSION_CONTRACT_INVALID")

    def test_draft_dimension_is_not_ready(self) -> None:
        result = self._evaluate(
            status="draft",
            semantic_object={
                "header": {"dimension_ref": "dimension.country"},
                "interface_contract": {
                    "value_domain": {
                        "structure_kind": "flat",
                        "semantic_role": "category",
                        "value_type": "string",
                        "domain_kind": "enumerated",
                    },
                    "grouping": {"supports_grouping": True},
                },
            },
        )

        self.assertEqual(result.lifecycle_status, "draft")
        self.assertEqual(result.readiness_status, "not_ready")

    def test_deprecated_dimension_is_not_ready(self) -> None:
        result = self._evaluate(
            status="deprecated",
            semantic_object={
                "header": {"dimension_ref": "dimension.country"},
                "interface_contract": {
                    "value_domain": {
                        "structure_kind": "flat",
                        "semantic_role": "category",
                        "value_type": "string",
                        "domain_kind": "enumerated",
                    },
                    "grouping": {"supports_grouping": True},
                },
            },
        )

        self.assertEqual(result.lifecycle_status, "deprecated")
        self.assertEqual(result.readiness_status, "not_ready")


class TimeReadinessEvaluatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = build_default_registry()

    def test_active_time_semantic_is_ready(self) -> None:
        result = self._evaluate(semantic_roles=["business_anchor", "measurement"])

        self.assertEqual(result.readiness_status, "ready")
        self.assertEqual(result.capabilities["supports_business_anchor"], True)
        self.assertEqual(result.capabilities["supports_measurement"], True)

    def test_time_semantic_requires_roles(self) -> None:
        result = self._evaluate(semantic_roles=[])

        self.assertEqual(result.readiness_status, "not_ready")
        self.assertEqual(result.blocking_requirements[0].code, "TIME_CONTRACT_INVALID")

    def _evaluate(self, *, semantic_roles: list[str], status: str = "published"):
        snapshot = build_snapshot(
            object_kind="time",
            object_id="time_123",
            ref="time.event_date",
            status=status,
            revision=1,
            semantic_object={
                "header": {
                    "time_ref": "time.event_date",
                    "semantic_roles": semantic_roles,
                }
            },
        )
        return self.registry.evaluator_for("time").evaluate(
            ReadinessEvaluationContext(snapshot=snapshot)
        )

    def test_draft_time_semantic_is_not_ready(self) -> None:
        result = self._evaluate(status="draft", semantic_roles=["measurement"])

        self.assertEqual(result.lifecycle_status, "draft")
        self.assertEqual(result.readiness_status, "not_ready")

    def test_deprecated_time_semantic_is_not_ready(self) -> None:
        result = self._evaluate(status="deprecated", semantic_roles=["measurement"])

        self.assertEqual(result.lifecycle_status, "deprecated")
        self.assertEqual(result.readiness_status, "not_ready")


class EnumReadinessEvaluatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = build_default_registry()

    def test_active_enum_set_is_ready(self) -> None:
        result = self._evaluate(
            versions=[{"enum_version": "v1", "values": [{"value_key": "CN", "label": "China"}]}]
        )

        self.assertEqual(result.readiness_status, "ready")
        self.assertEqual(result.capabilities["version_count"], 1)

    def test_enum_set_requires_populated_versions(self) -> None:
        result = self._evaluate(versions=[])

        self.assertEqual(result.readiness_status, "not_ready")
        self.assertEqual(result.blocking_requirements[0].code, "ENUM_CONTRACT_INVALID")

    def _evaluate(self, *, versions: list[dict[str, object]], status: str = "published"):
        snapshot = build_snapshot(
            object_kind="enum",
            object_id="enum_123",
            ref="enum.country_code",
            status=status,
            revision=1,
            semantic_object={
                "header": {
                    "enum_set_ref": "enum.country_code",
                    "value_type": "string",
                },
                "versions": versions,
            },
        )
        return self.registry.evaluator_for("enum").evaluate(
            ReadinessEvaluationContext(snapshot=snapshot)
        )

    def test_draft_enum_set_is_not_ready(self) -> None:
        result = self._evaluate(status="draft", versions=[])

        self.assertEqual(result.lifecycle_status, "draft")
        self.assertEqual(result.readiness_status, "not_ready")

    def test_deprecated_enum_set_is_not_ready(self) -> None:
        result = self._evaluate(
            status="deprecated",
            versions=[{"enum_version": "v1", "values": [{"value_key": "CN", "label": "China"}]}],
        )

        self.assertEqual(result.lifecycle_status, "deprecated")
        self.assertEqual(result.readiness_status, "not_ready")


class BindingReadinessEvaluatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = build_default_registry()

    def test_binding_missing_synced_carrier_is_stale(self) -> None:
        result = self._evaluate(
            field_bindings=[
                {
                    "carrier_binding_key": "primary",
                    "target": {"target_kind": "metric_input", "target_key": "numerator"},
                    "semantic_ref": "metric_input.converted",
                },
                {
                    "carrier_binding_key": "primary",
                    "target": {"target_kind": "metric_input", "target_key": "denominator"},
                    "semantic_ref": "metric_input.eligible",
                },
                {
                    "carrier_binding_key": "primary",
                    "target": {"target_kind": "primary_time", "target_key": "time.event_date"},
                    "semantic_ref": "time.event_date",
                },
            ],
            carrier_source_object_loader=lambda _carrier: None,
        )

        self.assertEqual(result.readiness_status, "stale")
        self.assertIn(
            "BINDING_CARRIER_SOURCE_MISSING",
            {item.code for item in result.blocking_requirements},
        )

    def test_binding_missing_target_coverage_is_not_ready(self) -> None:
        result = self._evaluate(
            field_bindings=[
                {
                    "carrier_binding_key": "primary",
                    "target": {"target_kind": "metric_input", "target_key": "numerator"},
                    "semantic_ref": "metric_input.converted",
                }
            ]
        )

        self.assertEqual(result.readiness_status, "not_ready")
        self.assertIn(
            "BINDING_TARGET_COVERAGE_MISSING",
            {item.code for item in result.blocking_requirements},
        )

    def test_binding_with_complete_metric_coverage_is_ready(self) -> None:
        result = self._evaluate(
            field_bindings=[
                {
                    "carrier_binding_key": "primary",
                    "target": {"target_kind": "metric_input", "target_key": "numerator"},
                    "semantic_ref": "metric_input.converted",
                },
                {
                    "carrier_binding_key": "primary",
                    "target": {"target_kind": "metric_input", "target_key": "denominator"},
                    "semantic_ref": "metric_input.eligible",
                },
                {
                    "carrier_binding_key": "primary",
                    "target": {"target_kind": "primary_time", "target_key": "time.event_date"},
                    "semantic_ref": "time.event_date",
                },
            ]
        )

        self.assertEqual(result.readiness_status, "ready")
        self.assertEqual(result.capabilities["covers_required_targets"], True)

    def test_binding_native_timestamp_column_requires_timestamp_like_type(self) -> None:
        result = self._evaluate(
            field_bindings=[
                {
                    "carrier_binding_key": "primary",
                    "target": {"target_kind": "metric_input", "target_key": "numerator"},
                    "semantic_ref": "metric_input.converted",
                },
                {
                    "carrier_binding_key": "primary",
                    "target": {"target_kind": "metric_input", "target_key": "denominator"},
                    "semantic_ref": "metric_input.eligible",
                },
            ],
            time_bindings=[
                {
                    "carrier_binding_key": "primary",
                    "target": {"target_kind": "primary_time", "target_key": "time.event_date"},
                    "semantic_ref": "time.event_date",
                    "resolution_kind": "timestamp_column",
                    "timestamp_surface_ref": "field.create_time",
                }
            ],
            carrier_bindings=[
                {
                    "binding_key": "primary",
                    "carrier_locator": "warehouse.metric_fact",
                    "field_surfaces": [
                        {"surface_ref": "field.create_time", "physical_name": "create_time"}
                    ],
                }
            ],
            carrier_source_object_loader=lambda _carrier: {
                "object_id": "src_123",
                "properties_json": '{"columns":[{"name":"create_time","type":"varchar"}]}',
            },
        )

        self.assertEqual(result.readiness_status, "not_ready")
        self.assertIn(
            "TIME_BINDING_TIMESTAMP_NATIVE_TYPE_MISMATCH",
            {item.code for item in result.blocking_requirements},
        )

    def test_binding_string_timestamp_column_requires_explicit_format(self) -> None:
        result = self._evaluate(
            field_bindings=[
                {
                    "carrier_binding_key": "primary",
                    "target": {"target_kind": "metric_input", "target_key": "numerator"},
                    "semantic_ref": "metric_input.converted",
                },
                {
                    "carrier_binding_key": "primary",
                    "target": {"target_kind": "metric_input", "target_key": "denominator"},
                    "semantic_ref": "metric_input.eligible",
                },
            ],
            time_bindings=[
                {
                    "carrier_binding_key": "primary",
                    "target": {"target_kind": "primary_time", "target_key": "time.event_date"},
                    "semantic_ref": "time.event_date",
                    "resolution_kind": "timestamp_column",
                    "timestamp_surface_ref": "field.create_time",
                }
            ],
            carrier_bindings=[
                {
                    "binding_key": "primary",
                    "carrier_locator": "warehouse.metric_fact",
                    "field_surfaces": [
                        {"surface_ref": "field.create_time", "physical_name": "create_time"}
                    ],
                }
            ],
            carrier_source_object_loader=lambda _carrier: {"object_id": "src_123"},
        )

        self.assertEqual(result.readiness_status, "not_ready")
        self.assertIn(
            "TIME_BINDING_TIMESTAMP_FORMAT_MISSING",
            {item.code for item in result.blocking_requirements},
        )

    def test_binding_iso8601_naive_timestamp_column_is_ready(self) -> None:
        result = self._evaluate(
            field_bindings=[
                {
                    "carrier_binding_key": "primary",
                    "target": {"target_kind": "metric_input", "target_key": "numerator"},
                    "semantic_ref": "metric_input.converted",
                },
                {
                    "carrier_binding_key": "primary",
                    "target": {"target_kind": "metric_input", "target_key": "denominator"},
                    "semantic_ref": "metric_input.eligible",
                },
            ],
            time_bindings=[
                {
                    "carrier_binding_key": "primary",
                    "target": {"target_kind": "primary_time", "target_key": "time.event_date"},
                    "semantic_ref": "time.event_date",
                    "resolution_kind": "timestamp_column",
                    "timestamp_surface_ref": "field.create_time",
                    "timestamp_format": "iso8601_t_naive",
                }
            ],
            carrier_bindings=[
                {
                    "binding_key": "primary",
                    "carrier_locator": "warehouse.metric_fact",
                    "field_surfaces": [
                        {"surface_ref": "field.create_time", "physical_name": "create_time"}
                    ],
                }
            ],
            carrier_source_object_loader=lambda _carrier: {
                "object_id": "src_123",
                "properties_json": '{"columns":[{"name":"create_time","type":"varchar"}]}',
            },
        )

        self.assertEqual(result.readiness_status, "ready")

    def _evaluate(
        self,
        *,
        status: str = "published",
        field_bindings: list[dict[str, object]] | None = None,
        time_bindings: list[dict[str, object]] | None = None,
        carrier_bindings: list[dict[str, object]] | None = None,
        carrier_source_object_loader=None,
    ):
        snapshot = build_snapshot(
            object_kind="binding",
            object_id="bind_123",
            ref="binding.metric_conversion_rate",
            status=status,
            revision=1,
            semantic_object={
                "header": {
                    "binding_ref": "binding.metric_conversion_rate",
                    "binding_scope": "metric",
                    "bound_object_ref": "metric.conversion_rate",
                },
                "interface_contract": {
                    "imports": [],
                    "carrier_bindings": carrier_bindings
                    if carrier_bindings is not None
                    else [{"binding_key": "primary", "carrier_locator": "warehouse.metric_fact"}],
                    "field_bindings": field_bindings if field_bindings is not None else [],
                    "time_bindings": time_bindings if time_bindings is not None else [],
                },
            },
        )
        context = ReadinessEvaluationContext(
            snapshot=snapshot,
            dependency_snapshot_loader=lambda ref: build_snapshot(
                object_kind="metric",
                object_id="metc_123",
                ref=ref,
                status="published",
                revision=2,
                semantic_object={
                    "header": {
                        "metric_ref": "metric.conversion_rate",
                        "metric_family": "rate_metric",
                        "observed_entity_ref": "entity.user",
                        "primary_time_ref": "time.event_date",
                    },
                    "payload": {
                        "metric_family": "rate_metric",
                        "numerator": {"name": "converted"},
                        "denominator": {"name": "eligible"},
                    },
                },
            ),
            carrier_source_object_loader=carrier_source_object_loader
            or (lambda _carrier: {"object_id": "src_123"}),
        )
        return self.registry.evaluator_for("binding").evaluate(context)

    def test_binding_without_carrier_bindings_is_not_ready(self) -> None:
        result = self._evaluate(carrier_bindings=[])

        self.assertEqual(result.readiness_status, "not_ready")
        self.assertIn(
            "BINDING_CARRIER_MISSING",
            {item.code for item in result.blocking_requirements},
        )

    def test_binding_without_field_bindings_is_not_ready(self) -> None:
        result = self._evaluate(field_bindings=[])

        self.assertEqual(result.readiness_status, "not_ready")
        self.assertIn(
            "BINDING_FIELD_MAPPING_MISSING",
            {item.code for item in result.blocking_requirements},
        )

    def test_binding_with_invalid_carrier_binding_key_is_not_ready(self) -> None:
        result = self._evaluate(
            field_bindings=[
                {
                    "carrier_binding_key": "invalid_key",
                    "target": {"target_kind": "metric_input", "target_key": "numerator"},
                    "semantic_ref": "metric_input.converted",
                }
            ]
        )

        self.assertEqual(result.readiness_status, "not_ready")
        self.assertIn(
            "BINDING_FIELD_MAPPING_MISSING",
            {item.code for item in result.blocking_requirements},
        )

    def test_binding_with_inactive_import_is_stale(self) -> None:
        result = self._evaluate_with_import(import_status="draft")

        self.assertEqual(result.readiness_status, "stale")
        self.assertIn(
            "BINDING_IMPORT_INACTIVE",
            {item.code for item in result.blocking_requirements},
        )

    def test_binding_with_missing_import_is_stale(self) -> None:
        result = self._evaluate_with_import(import_status="missing")

        self.assertEqual(result.readiness_status, "stale")
        self.assertIn(
            "BINDING_IMPORT_INACTIVE",
            {item.code for item in result.blocking_requirements},
        )

    def test_binding_with_inactive_subject_is_stale(self) -> None:
        result = self._evaluate_with_subject_status(subject_status="draft")

        self.assertEqual(result.readiness_status, "stale")
        self.assertIn(
            "BINDING_SUBJECT_INACTIVE",
            {item.code for item in result.blocking_requirements},
        )

    def test_binding_blockers_are_deduplicated(self) -> None:
        result = self._evaluate(
            carrier_bindings=[
                {"binding_key": "primary", "carrier_locator": "warehouse.metric_fact"},
                {"binding_key": "secondary", "carrier_locator": "warehouse.metric_fact_secondary"},
            ],
            carrier_source_object_loader=lambda _carrier: None,
        )

        carrier_blockers = [
            b for b in result.blocking_requirements if b.code == "BINDING_CARRIER_SOURCE_MISSING"
        ]
        self.assertEqual(len(carrier_blockers), 2)
        blocker_keys = [
            (b.code, b.subject_ref, b.dependency_ref) for b in result.blocking_requirements
        ]
        self.assertEqual(len(blocker_keys), len(set(blocker_keys)))

    def test_draft_binding_is_not_ready(self) -> None:
        result = self._evaluate(status="draft")

        self.assertEqual(result.lifecycle_status, "draft")
        self.assertEqual(result.readiness_status, "not_ready")

    def test_deprecated_binding_is_not_ready(self) -> None:
        result = self._evaluate(status="deprecated")

        self.assertEqual(result.lifecycle_status, "deprecated")
        self.assertEqual(result.readiness_status, "not_ready")

    def _evaluate_with_import(self, *, import_status: str) -> ReadinessResult:
        def dependency_loader(ref: str):
            if ref == "binding.imported_binding":
                if import_status == "missing":
                    return None
                return build_snapshot(
                    object_kind="binding",
                    object_id="bind_import",
                    ref=ref,
                    status=import_status,
                    revision=1,
                    semantic_object={"header": {"binding_ref": ref}},
                )
            if ref == "metric.conversion_rate":
                return build_snapshot(
                    object_kind="metric",
                    object_id="metc_123",
                    ref=ref,
                    status="published",
                    revision=2,
                    semantic_object={
                        "header": {
                            "metric_ref": ref,
                            "metric_family": "rate_metric",
                            "observed_entity_ref": "entity.user",
                            "primary_time_ref": "time.event_date",
                        },
                        "payload": {
                            "metric_family": "rate_metric",
                            "numerator": {"name": "converted"},
                            "denominator": {"name": "eligible"},
                        },
                    },
                )
            return None

        snapshot = build_snapshot(
            object_kind="binding",
            object_id="bind_123",
            ref="binding.metric_conversion_rate",
            status="published",
            revision=1,
            semantic_object={
                "header": {
                    "binding_ref": "binding.metric_conversion_rate",
                    "binding_scope": "metric",
                    "bound_object_ref": "metric.conversion_rate",
                },
                "interface_contract": {
                    "imports": [{"imported_binding_ref": "binding.imported_binding"}],
                    "carrier_bindings": [
                        {"binding_key": "primary", "carrier_locator": "warehouse.metric_fact"}
                    ],
                    "field_bindings": [
                        {
                            "carrier_binding_key": "primary",
                            "target": {"target_kind": "metric_input", "target_key": "numerator"},
                            "semantic_ref": "metric_input.converted",
                        },
                        {
                            "carrier_binding_key": "primary",
                            "target": {"target_kind": "metric_input", "target_key": "denominator"},
                            "semantic_ref": "metric_input.eligible",
                        },
                        {
                            "carrier_binding_key": "primary",
                            "target": {
                                "target_kind": "primary_time",
                                "target_key": "time.event_date",
                            },
                            "semantic_ref": "time.event_date",
                        },
                    ],
                },
            },
        )
        context = ReadinessEvaluationContext(
            snapshot=snapshot,
            dependency_snapshot_loader=dependency_loader,
            carrier_source_object_loader=lambda _carrier: {"object_id": "src_123"},
        )
        return self.registry.evaluator_for("binding").evaluate(context)

    def _evaluate_with_subject_status(self, *, subject_status: str) -> ReadinessResult:
        snapshot = build_snapshot(
            object_kind="binding",
            object_id="bind_123",
            ref="binding.metric_conversion_rate",
            status="published",
            revision=1,
            semantic_object={
                "header": {
                    "binding_ref": "binding.metric_conversion_rate",
                    "binding_scope": "metric",
                    "bound_object_ref": "metric.conversion_rate",
                },
                "interface_contract": {
                    "imports": [],
                    "carrier_bindings": [
                        {"binding_key": "primary", "carrier_locator": "warehouse.metric_fact"}
                    ],
                    "field_bindings": [
                        {
                            "carrier_binding_key": "primary",
                            "target": {"target_kind": "metric_input", "target_key": "numerator"},
                            "semantic_ref": "metric_input.converted",
                        },
                        {
                            "carrier_binding_key": "primary",
                            "target": {"target_kind": "metric_input", "target_key": "denominator"},
                            "semantic_ref": "metric_input.eligible",
                        },
                        {
                            "carrier_binding_key": "primary",
                            "target": {
                                "target_kind": "primary_time",
                                "target_key": "time.event_date",
                            },
                            "semantic_ref": "time.event_date",
                        },
                    ],
                },
            },
        )
        context = ReadinessEvaluationContext(
            snapshot=snapshot,
            dependency_snapshot_loader=lambda ref: build_snapshot(
                object_kind="metric",
                object_id="metc_123",
                ref=ref,
                status=subject_status,
                revision=2,
                semantic_object={
                    "header": {
                        "metric_ref": ref,
                        "metric_family": "rate_metric",
                        "observed_entity_ref": "entity.user",
                        "primary_time_ref": "time.event_date",
                    },
                    "payload": {
                        "metric_family": "rate_metric",
                        "numerator": {"name": "converted"},
                        "denominator": {"name": "eligible"},
                    },
                },
            ),
            carrier_source_object_loader=lambda _carrier: {"object_id": "src_123"},
        )
        return self.registry.evaluator_for("binding").evaluate(context)


class CompatibilityProfileReadinessEvaluatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = build_default_registry()

    def test_profile_with_matching_subject_revision_is_ready(self) -> None:
        result = self._evaluate(subject_revision=3)

        self.assertEqual(result.readiness_status, "ready")
        self.assertEqual(result.capabilities["matches_subject_revision"], True)

    def test_profile_revision_mismatch_is_stale(self) -> None:
        result = self._evaluate(subject_revision=2)

        self.assertEqual(result.readiness_status, "stale")
        self.assertEqual(
            result.blocking_requirements[0].code,
            "PROFILE_SUBJECT_REVISION_MISMATCH",
        )

    def _evaluate(self, *, subject_revision: int | None):
        snapshot = build_snapshot(
            object_kind="compiler_profile",
            object_id="cprof_123",
            ref="compiler_profile.metric_requirement",
            status="published",
            revision=4,
            semantic_object={
                "profile_ref": "compiler_profile.metric_requirement",
                "profile_kind": "requirement",
                "subject_kind": "metric",
                "subject_ref": "metric.conversion_rate",
                "subject_revision": subject_revision,
                "requirement": {"entity_refs": ["entity.user"]},
            },
        )
        context = ReadinessEvaluationContext(
            snapshot=snapshot,
            dependency_snapshot_loader=lambda ref: build_snapshot(
                object_kind="metric",
                object_id="metc_123",
                ref=ref,
                status="published",
                revision=3,
                semantic_object={"header": {"metric_ref": ref}},
            ),
        )
        return self.registry.evaluator_for("compiler_profile").evaluate(context)

    def test_profile_with_inactive_subject_is_not_ready(self) -> None:
        result = self._evaluate_with_subject_status(subject_status="draft")

        self.assertEqual(result.readiness_status, "not_ready")
        self.assertIn(
            "PROFILE_SUBJECT_INACTIVE",
            {item.code for item in result.blocking_requirements},
        )

    def test_profile_with_missing_subject_is_not_ready(self) -> None:
        result = self._evaluate_with_missing_subject()

        self.assertEqual(result.readiness_status, "not_ready")
        self.assertIn(
            "PROFILE_SUBJECT_INACTIVE",
            {item.code for item in result.blocking_requirements},
        )

    def test_profile_with_invalid_contract_is_not_ready(self) -> None:
        result = self._evaluate_invalid_contract()

        self.assertEqual(result.readiness_status, "not_ready")
        self.assertIn(
            "PROFILE_CONTRACT_INVALID",
            {item.code for item in result.blocking_requirements},
        )

    def test_draft_profile_is_not_ready(self) -> None:
        result = self._evaluate_with_profile_status(profile_status="draft")

        self.assertEqual(result.lifecycle_status, "draft")
        self.assertEqual(result.readiness_status, "not_ready")

    def test_stale_profile_has_explanatory_blocker(self) -> None:
        result = self._evaluate(subject_revision=2)

        self.assertEqual(result.readiness_status, "stale")
        self.assertEqual(len(result.blocking_requirements), 1)
        self.assertEqual(result.blocking_requirements[0].code, "PROFILE_SUBJECT_REVISION_MISMATCH")

    def test_deprecated_profile_is_not_ready(self) -> None:
        result = self._evaluate_with_profile_status(profile_status="deprecated")

        self.assertEqual(result.lifecycle_status, "deprecated")
        self.assertEqual(result.readiness_status, "not_ready")

    def _evaluate_with_subject_status(self, *, subject_status: str) -> ReadinessResult:
        snapshot = build_snapshot(
            object_kind="compiler_profile",
            object_id="cprof_123",
            ref="compiler_profile.metric_requirement",
            status="published",
            revision=4,
            semantic_object={
                "profile_ref": "compiler_profile.metric_requirement",
                "profile_kind": "requirement",
                "subject_kind": "metric",
                "subject_ref": "metric.conversion_rate",
                "subject_revision": 3,
                "requirement": {"entity_refs": ["entity.user"]},
            },
        )
        context = ReadinessEvaluationContext(
            snapshot=snapshot,
            dependency_snapshot_loader=lambda ref: build_snapshot(
                object_kind="metric",
                object_id="metc_123",
                ref=ref,
                status=subject_status,
                revision=3,
                semantic_object={"header": {"metric_ref": ref}},
            ),
        )
        return self.registry.evaluator_for("compiler_profile").evaluate(context)

    def _evaluate_with_missing_subject(self) -> ReadinessResult:
        snapshot = build_snapshot(
            object_kind="compiler_profile",
            object_id="cprof_123",
            ref="compiler_profile.metric_requirement",
            status="published",
            revision=4,
            semantic_object={
                "profile_ref": "compiler_profile.metric_requirement",
                "profile_kind": "requirement",
                "subject_kind": "metric",
                "subject_ref": "metric.conversion_rate",
                "subject_revision": 3,
                "requirement": {"entity_refs": ["entity.user"]},
            },
        )
        context = ReadinessEvaluationContext(
            snapshot=snapshot,
            dependency_snapshot_loader=lambda _ref: None,
        )
        return self.registry.evaluator_for("compiler_profile").evaluate(context)

    def _evaluate_invalid_contract(self) -> ReadinessResult:
        snapshot = build_snapshot(
            object_kind="compiler_profile",
            object_id="cprof_123",
            ref="compiler_profile.metric_requirement",
            status="published",
            revision=4,
            semantic_object={
                "subject_kind": "metric",
                "subject_ref": "metric.conversion_rate",
                "subject_revision": 3,
            },
        )
        context = ReadinessEvaluationContext(snapshot=snapshot)
        return self.registry.evaluator_for("compiler_profile").evaluate(context)

    def _evaluate_with_profile_status(self, *, profile_status: str) -> ReadinessResult:
        snapshot = build_snapshot(
            object_kind="compiler_profile",
            object_id="cprof_123",
            ref="compiler_profile.metric_requirement",
            status=profile_status,
            revision=4,
            semantic_object={
                "profile_ref": "compiler_profile.metric_requirement",
                "profile_kind": "requirement",
                "subject_kind": "metric",
                "subject_ref": "metric.conversion_rate",
                "subject_revision": 3,
                "requirement": {"entity_refs": ["entity.user"]},
            },
        )
        context = ReadinessEvaluationContext(
            snapshot=snapshot,
            dependency_snapshot_loader=lambda ref: build_snapshot(
                object_kind="metric",
                object_id="metc_123",
                ref=ref,
                status="published",
                revision=3,
                semantic_object={"header": {"metric_ref": ref}},
            ),
        )
        return self.registry.evaluator_for("compiler_profile").evaluate(context)


if __name__ == "__main__":
    unittest.main()
