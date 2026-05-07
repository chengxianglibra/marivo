"""Analysis-core primitives for the incremental refactor.

.. deprecated:: Phase 3c
    This package is superseded by ``app.core.intent`` and ``app.core.semantic``.
    New code should import from the ``app.core.*`` packages directly.
    This module will be removed in Phase 3d.
"""

from app.analysis_core.composites import CompositeStepTemplate, CompositeWorkflowSpec
from app.analysis_core.intent_registry import IntentRunnerRegistry
from app.analysis_core.ir import (
    AnalysisRequest,
    AnalysisStepIR,
    ArtifactExpectation,
    ExecutionPlanIR,
    ExecutionTargetIR,
    PolicyTransformIR,
    ResolvedEntityIR,
    ResolvedMetricIR,
    SemanticIntent,
    SemanticResolutionIR,
    request_from_session_payload,
    step_ir_from_mapping,
)
from app.analysis_core.primitives import (
    ATOMIC_INTENT_TYPES,
    COMPOSITE_STEP_TYPES,
    DERIVED_INTENT_TYPES,
    INTENT_TAXONOMY,
    PRIMITIVE_STEP_TYPES,
    STEP_TAXONOMY,
    SUPPORTED_INTENT_TYPES,
    SUPPORTED_STEP_TYPES,
    step_category_for,
)
from app.analysis_core.step_registry import StepRunnerRegistry
from app.analysis_core.step_runners import (
    build_primitive_step_registry,
    build_service_step_registry,
)
from app.analysis_core.workflows import (
    WORKFLOW_SPECS,
    CompositeWorkflowRuntime,
)

__all__ = [
    "ATOMIC_INTENT_TYPES",
    "COMPOSITE_STEP_TYPES",
    "DERIVED_INTENT_TYPES",
    "INTENT_TAXONOMY",
    "PRIMITIVE_STEP_TYPES",
    "STEP_TAXONOMY",
    "SUPPORTED_INTENT_TYPES",
    "SUPPORTED_STEP_TYPES",
    "WORKFLOW_SPECS",
    "AnalysisRequest",
    "AnalysisStepIR",
    "ArtifactExpectation",
    "CompositeStepTemplate",
    "CompositeWorkflowRuntime",
    "CompositeWorkflowSpec",
    "ExecutionPlanIR",
    "ExecutionTargetIR",
    "IntentRunnerRegistry",
    "PolicyTransformIR",
    "ResolvedEntityIR",
    "ResolvedMetricIR",
    "SemanticIntent",
    "SemanticResolutionIR",
    "StepRunnerRegistry",
    "build_primitive_step_registry",
    "build_service_step_registry",
    "request_from_session_payload",
    "step_category_for",
    "step_ir_from_mapping",
]
