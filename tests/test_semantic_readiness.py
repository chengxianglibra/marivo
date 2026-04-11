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
    def test_default_registry_covers_all_phase_a_object_kinds(self) -> None:
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


class PlaceholderSemanticReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = SemanticReadinessService()

    def test_published_snapshot_preserves_phase_a_mapping(self) -> None:
        result = self.service.evaluate_snapshot(
            object_kind="metric",
            object_id="metc_123",
            ref="metric.watch_time",
            status="published",
            revision=3,
            semantic_object={"header": {"metric_ref": "metric.watch_time"}},
        )

        self.assertEqual(result.lifecycle_status, "active")
        self.assertEqual(result.readiness_status, "ready")
        self.assertEqual(result.blocking_requirements, [])
        self.assertEqual(result.capabilities, {})
        self.assertEqual(result.trace[0].stage, "compat_placeholder")
        self.assertEqual(result.trace[0].source, "metric_placeholder_evaluator")

    def test_deprecated_snapshot_is_not_ready(self) -> None:
        result = self.service.evaluate_snapshot(
            object_kind="binding",
            object_id="bind_123",
            ref="binding.watch_time",
            status="deprecated",
            revision=4,
            semantic_object={"header": {"binding_ref": "binding.watch_time"}},
        )

        self.assertEqual(result.lifecycle_status, "deprecated")
        self.assertEqual(result.readiness_status, "not_ready")

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
