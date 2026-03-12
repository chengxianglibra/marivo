from __future__ import annotations

from typing import TYPE_CHECKING

from app.analysis_core.step_registry import StepRunnerRegistry

if TYPE_CHECKING:
    from app.service import SemanticLayerService


def register(registry: StepRunnerRegistry, service: "SemanticLayerService") -> None:
    registry.register("analyze_ads", lambda session_id, _params=None: service._run_ad_analysis(session_id))
