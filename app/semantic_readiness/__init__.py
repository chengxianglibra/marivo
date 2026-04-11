"""Semantic readiness evaluator framework.

This module provides a registry-based framework for computing readiness status
of semantic objects (entities, metrics, processes, dimensions, bindings, etc).

Phase A (current) uses placeholder evaluators that preserve the simple mapping:
  - published → active lifecycle + ready readiness
  - draft → draft lifecycle + not_ready readiness
  - deprecated → deprecated lifecycle + not_ready readiness

Phase B will replace placeholders with object-specific evaluators that compute
blocking_requirements and capabilities based on dependencies, bindings, and
physical grounding requirements.

Key components:
  - SemanticReadinessService: Top-level entry point for evaluating readiness
  - SemanticReadinessRegistry: Registry mapping object_kind → evaluator
  - ReadinessEvaluationContext: Context with lazy loaders for dependencies
  - ReadinessResult: Output with lifecycle, readiness, blockers, capabilities
"""

from .binding_utils import binding_contract_target_exists
from .context import ReadinessEvaluationContext, ReadinessObjectSnapshot, build_snapshot
from .registry import (
    SemanticReadinessRegistry,
    UnknownSemanticReadinessKindError,
    build_default_registry,
)
from .service import SemanticReadinessService
from .types import (
    BlockingRequirementPayload,
    ObjectKind,
    ReadinessResult,
    ReadinessTraceItem,
    SemanticReadinessEvaluator,
    derive_lifecycle_status,
    derive_readiness_status,
)

__all__ = [
    "BlockingRequirementPayload",
    "ObjectKind",
    "ReadinessEvaluationContext",
    "ReadinessObjectSnapshot",
    "ReadinessResult",
    "ReadinessTraceItem",
    "SemanticReadinessEvaluator",
    "SemanticReadinessRegistry",
    "SemanticReadinessService",
    "UnknownSemanticReadinessKindError",
    "binding_contract_target_exists",
    "build_default_registry",
    "build_snapshot",
    "derive_lifecycle_status",
    "derive_readiness_status",
]
