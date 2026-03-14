from __future__ import annotations

from app.analysis_core.primitives import SUPPORTED_STEP_TYPES
from app.analysis_core.step_registry import StepRunnerRegistry
from app.analysis_core.step_runners import generic, synthesis

def build_primitive_step_registry(service: object, registry: StepRunnerRegistry | None = None) -> StepRunnerRegistry:
    registry = registry or StepRunnerRegistry()
    generic.register(registry, service)
    return registry


def build_service_step_registry(service: object) -> StepRunnerRegistry:
    registry = StepRunnerRegistry()
    build_primitive_step_registry(service, registry)
    synthesis.register(registry, service)
    return registry
