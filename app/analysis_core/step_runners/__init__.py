from __future__ import annotations

from typing import TYPE_CHECKING

from app.analysis_core.step_registry import StepRunnerRegistry
from app.analysis_core.step_runners import attribution, generic

if TYPE_CHECKING:
    from app.service import SemanticLayerService


def build_primitive_step_registry(
    service: SemanticLayerService, registry: StepRunnerRegistry | None = None
) -> StepRunnerRegistry:
    registry = registry or StepRunnerRegistry()
    generic.register(registry, service)
    attribution.register(registry, service)
    return registry


def build_service_step_registry(service: SemanticLayerService) -> StepRunnerRegistry:
    registry = StepRunnerRegistry()
    build_primitive_step_registry(service, registry)
    return registry
