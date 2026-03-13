"""Analysis-core primitives for the incremental refactor."""

from app.analysis_core.ir import (
    AnalysisRequest,
    AnalysisStepIR,
    ArtifactExpectation,
    ExecutionPlanIR,
    SemanticIntent,
    from_legacy_step,
)
from app.analysis_core.composites import CompositeStepTemplate, CompositeWorkflowSpec
from app.analysis_core.primitives import (
    COMPOSITE_STEP_TYPES,
    OPTIONAL_STEP_TYPES,
    PRIMITIVE_STEP_TYPES,
    STEP_TAXONOMY,
    SUPPORTED_STEP_TYPES,
    is_optional_step,
    step_category_for,
)
from app.analysis_core.step_registry import StepRunnerRegistry
from app.analysis_core.step_runners import (
    build_composite_step_registry,
    build_primitive_step_registry,
    build_service_step_registry,
)
from app.analysis_core.workflows import (
    CompositeWorkflowRuntime,
    WATCH_TIME_DROP_WORKFLOW,
    WORKFLOW_SPECS,
)

__all__ = [
    "AnalysisRequest",
    "AnalysisStepIR",
    "ArtifactExpectation",
    "COMPOSITE_STEP_TYPES",
    "CompositeStepTemplate",
    "CompositeWorkflowRuntime",
    "CompositeWorkflowSpec",
    "ExecutionPlanIR",
    "OPTIONAL_STEP_TYPES",
    "PRIMITIVE_STEP_TYPES",
    "SemanticIntent",
    "STEP_TAXONOMY",
    "StepRunnerRegistry",
    "SUPPORTED_STEP_TYPES",
    "WATCH_TIME_DROP_WORKFLOW",
    "WORKFLOW_SPECS",
    "build_composite_step_registry",
    "build_primitive_step_registry",
    "build_service_step_registry",
    "from_legacy_step",
    "is_optional_step",
    "step_category_for",
]
