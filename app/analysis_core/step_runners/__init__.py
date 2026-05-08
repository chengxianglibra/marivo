from __future__ import annotations

from typing import TYPE_CHECKING

from app.analysis_core.step_registry import StepRunnerRegistry
from app.analysis_core.step_runners import attribution, generic

if TYPE_CHECKING:
    from app.runtime.runtime import MarivoRuntime


def build_primitive_step_registry(
    runtime: MarivoRuntime, registry: StepRunnerRegistry | None = None
) -> StepRunnerRegistry:
    registry = registry or StepRunnerRegistry()
    generic.register(registry, runtime)
    attribution.register(registry, runtime)
    return registry


def build_service_step_registry(runtime: MarivoRuntime) -> StepRunnerRegistry:
    registry = StepRunnerRegistry()
    build_primitive_step_registry(runtime, registry)
    return registry
