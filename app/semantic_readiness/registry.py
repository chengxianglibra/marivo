"""Registry for semantic readiness evaluators.

Provides a mapping from object_kind (entity, metric, process, etc) to
the corresponding evaluator implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .evaluators import PlaceholderSemanticReadinessEvaluator
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
    """Build registry with placeholder evaluators for all object kinds.

    Phase A uses placeholder evaluators that preserve simple status mapping.
    Phase B will replace with object-specific evaluators.
    """
    registry = SemanticReadinessRegistry()
    object_kinds: tuple[ObjectKind, ...] = (
        "entity",
        "metric",
        "process",
        "dimension",
        "time",
        "enum",
        "binding",
        "compiler_profile",
    )
    for object_kind in object_kinds:
        registry.register(object_kind, PlaceholderSemanticReadinessEvaluator(object_kind))
    return registry
