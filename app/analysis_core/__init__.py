"""Analysis-core primitives for the incremental refactor."""

from app.analysis_core.ir import (
    AnalysisRequest,
    AnalysisStepIR,
    ArtifactExpectation,
    ExecutionPlanIR,
    SemanticIntent,
    from_legacy_step,
)
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

__all__ = [
    "AnalysisRequest",
    "AnalysisStepIR",
    "ArtifactExpectation",
    "COMPOSITE_STEP_TYPES",
    "ExecutionPlanIR",
    "OPTIONAL_STEP_TYPES",
    "PRIMITIVE_STEP_TYPES",
    "SemanticIntent",
    "STEP_TAXONOMY",
    "StepRunnerRegistry",
    "SUPPORTED_STEP_TYPES",
    "build_composite_step_registry",
    "build_primitive_step_registry",
    "build_service_step_registry",
    "from_legacy_step",
    "is_optional_step",
    "step_category_for",
]
