from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.analysis_core.step_registry import StepRunnerRegistry

if TYPE_CHECKING:
    from app.service import SemanticLayerService


def register(registry: StepRunnerRegistry, service: "SemanticLayerService") -> None:
    registry.register("compare_watch_time", lambda session_id, _params=None: service._run_compare_watch_time(session_id))
