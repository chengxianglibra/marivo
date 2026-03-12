from __future__ import annotations

from app.analysis_core.step_registry import StepRunnerRegistry
from app.analysis_core.step_runners import ads, generic, qoe, recommendation, synthesis, watch_time

SUPPORTED_STEP_TYPES = (
    "compare_watch_time",
    "analyze_qoe",
    "analyze_ads",
    "analyze_recommendation",
    "synthesize_findings",
    "compare_metric",
    "profile_table",
    "sample_rows",
)


def build_service_step_registry(service: object) -> StepRunnerRegistry:
    registry = StepRunnerRegistry()
    watch_time.register(registry, service)
    qoe.register(registry, service)
    ads.register(registry, service)
    recommendation.register(registry, service)
    synthesis.register(registry, service)
    generic.register(registry, service)
    return registry
