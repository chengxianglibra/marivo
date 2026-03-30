"""Analysis-core primitives for the incremental refactor."""

from app.analysis_core.composites import CompositeStepTemplate, CompositeWorkflowSpec
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
    from_legacy_step,
    request_from_legacy_session,
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
    "PolicyTransformIR",
    "ResolvedEntityIR",
    "ResolvedMetricIR",
    "SemanticIntent",
    "SemanticResolutionIR",
    "StepRunnerRegistry",
    "build_primitive_step_registry",
    "build_service_step_registry",
    "from_legacy_step",
    "request_from_legacy_session",
    "step_category_for",
]
