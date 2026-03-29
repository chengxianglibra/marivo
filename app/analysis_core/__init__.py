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
    COMPOSITE_STEP_TYPES,
    PRIMITIVE_STEP_TYPES,
    STEP_TAXONOMY,
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
    "COMPOSITE_STEP_TYPES",
    "PRIMITIVE_STEP_TYPES",
    "STEP_TAXONOMY",
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
