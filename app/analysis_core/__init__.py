"""Analysis-core primitives for the incremental refactor."""

from app.analysis_core.ir import (
    AnalysisRequest,
    AnalysisStepIR,
    ArtifactExpectation,
    ExecutionPlanIR,
    SemanticIntent,
    from_legacy_step,
)
from app.analysis_core.step_registry import StepRunnerRegistry
from app.analysis_core.step_runners import SUPPORTED_STEP_TYPES, build_service_step_registry

__all__ = [
    "AnalysisRequest",
    "AnalysisStepIR",
    "ArtifactExpectation",
    "ExecutionPlanIR",
    "SemanticIntent",
    "StepRunnerRegistry",
    "SUPPORTED_STEP_TYPES",
    "build_service_step_registry",
    "from_legacy_step",
]
