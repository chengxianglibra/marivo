"""Registry for semantic readiness evaluators.

Provides a mapping from object_kind (entity, metric, process, etc) to
the corresponding evaluator implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast

from .evaluators import (
    EntityReadinessEvaluator,
    MetricReadinessEvaluator,
    PlaceholderSemanticReadinessEvaluator,
    ProcessReadinessEvaluator,
)
from .types import ObjectKind, SemanticReadinessEvaluator


class UnknownSemanticReadinessKindError(KeyError):
    """Raised when no evaluator is registered for an object_kind."""

    pass


@dataclass(slots=True)
class SemanticReadinessRegistry:
    """Registry mapping object_kind to readiness evaluator.

    Each semantic object type (entity, metric, process, dimension, time, enum,
    binding, compiler_profile) has a dedicated evaluator that computes
    lifecycle_status, readiness_status, blocking_requirements, and capabilities.

    Use register() to add a new evaluator, evaluator_for() to retrieve one.
    """

    _evaluators: dict[ObjectKind, SemanticReadinessEvaluator] = field(default_factory=dict)

    def register(self, object_kind: ObjectKind, evaluator: SemanticReadinessEvaluator) -> None:
        """Register an evaluator for a specific object_kind."""
        self._evaluators[object_kind] = evaluator

    def evaluator_for(self, object_kind: ObjectKind) -> SemanticReadinessEvaluator:
        """Get the evaluator for an object_kind.

        Raises:
            UnknownSemanticReadinessKindError: If no evaluator registered.
        """
        evaluator = self._evaluators.get(object_kind)
        if evaluator is None:
            raise UnknownSemanticReadinessKindError(
                f"No semantic readiness evaluator registered for {object_kind!r}"
            )
        return evaluator


def build_default_registry() -> SemanticReadinessRegistry:
    """Build registry with concrete evaluators for T3 object kinds.

    Entity, metric, and process use concrete evaluators. Remaining object
    families stay on placeholder evaluators until later tasks land.
    """
    registry = SemanticReadinessRegistry()
    registry.register("entity", EntityReadinessEvaluator())
    registry.register("metric", MetricReadinessEvaluator())
    registry.register("process", ProcessReadinessEvaluator())
    for object_kind in ("dimension", "time", "enum", "binding", "compiler_profile"):
        registry.register(
            cast("ObjectKind", object_kind),
            PlaceholderSemanticReadinessEvaluator(object_kind),
        )
    return registry
