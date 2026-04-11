"""Top-level service for semantic readiness evaluation.

SemanticReadinessService is the main entry point for computing readiness
status of semantic objects. It builds an evaluation context, dispatches
to the appropriate evaluator via the registry, and returns the result.

In Phase A, the service is lightweight because placeholder evaluators
use simple status mapping. In Phase B, evaluators will query metadata
via the context's lazy loaders to compute blockers and capabilities.
"""

from __future__ import annotations

from typing import Any

from app.storage.metadata import MetadataStore

from .context import ReadinessEvaluationContext, build_snapshot
from .registry import SemanticReadinessRegistry, build_default_registry
from .types import ObjectKind, ReadinessResult


class SemanticReadinessService:
    """Service for evaluating semantic object readiness.

    Usage:
        service = SemanticReadinessService(metadata)
        result = service.evaluate_snapshot(
            object_kind="metric",
            object_id="metc_123",
            ref="metric.watch_time",
            status="published",
            revision=3,
            semantic_object={"header": {"metric_ref": "metric.watch_time"}},
        )
        payload = result.contract_payload()

    The service creates its own registry instance by default. For testing
    or custom evaluator registration, pass a custom registry.
    """

    def __init__(
        self,
        metadata: MetadataStore | None = None,
        registry: SemanticReadinessRegistry | None = None,
    ) -> None:
        """Initialize the readiness service.

        Args:
            metadata: MetadataStore for dependency/binding/profile queries.
                Can be None for Phase A placeholder evaluators.
            registry: Optional custom registry. Defaults to build_default_registry().
        """
        self.metadata = metadata
        self.registry = registry or build_default_registry()

    def evaluate_snapshot(
        self,
        *,
        object_kind: ObjectKind,
        object_id: str,
        ref: str,
        status: str,
        revision: int,
        semantic_object: dict[str, Any],
        require_physical_grounding: bool = False,
        required_capabilities: list[str] | None = None,
        intent_kind: str | None = None,
    ) -> ReadinessResult:
        """Evaluate readiness for a semantic object snapshot.

        Args:
            object_kind: The semantic object type (entity, metric, process, etc).
            object_id: Unique identifier for the object.
            ref: Semantic reference string (e.g., "metric.watch_time").
            status: Storage status from database (draft, published, deprecated).
            revision: Object revision number.
            semantic_object: Full object dict for evaluator inspection.
            require_physical_grounding: Whether physical binding is required.
            required_capabilities: List of required capability keys.
            intent_kind: Analysis intent kind (observe, compare, etc).

        Returns:
            ReadinessResult with lifecycle_status, readiness_status,
            blocking_requirements, capabilities, and trace.
        """
        context = ReadinessEvaluationContext(
            snapshot=build_snapshot(
                object_kind=object_kind,
                object_id=object_id,
                ref=ref,
                status=status,
                revision=revision,
                semantic_object=semantic_object,
            ),
            metadata=self.metadata,
            require_physical_grounding=require_physical_grounding,
            required_capabilities=list(required_capabilities or []),
            intent_kind=intent_kind,
        )
        evaluator = self.registry.evaluator_for(object_kind)
        return evaluator.evaluate(context)
