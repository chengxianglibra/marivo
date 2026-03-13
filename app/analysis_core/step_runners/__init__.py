from __future__ import annotations

from app.analysis_core.primitives import SUPPORTED_STEP_TYPES
from app.analysis_core.step_registry import StepRunnerRegistry
from app.analysis_core.step_runners import ads, generic, qoe, recommendation, synthesis, watch_time

def build_primitive_step_registry(service: object, registry: StepRunnerRegistry | None = None) -> StepRunnerRegistry:
    registry = registry or StepRunnerRegistry()
    generic.register(registry, service)
    return registry


def build_composite_step_registry(service: object, registry: StepRunnerRegistry | None = None) -> StepRunnerRegistry:
    registry = registry or StepRunnerRegistry()
    watch_time.register(registry, service)
    qoe.register(registry, service)
    ads.register(registry, service)
    recommendation.register(registry, service)
    synthesis.register(registry, service)
    return registry


def build_service_step_registry(service: object) -> StepRunnerRegistry:
    registry = StepRunnerRegistry()
    build_composite_step_registry(service, registry)
    build_primitive_step_registry(service, registry)
    return registry
