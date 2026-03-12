from __future__ import annotations

from typing import TYPE_CHECKING

from app.analysis_core.step_registry import StepRunnerRegistry

if TYPE_CHECKING:
    from app.service import SemanticLayerService


def register(registry: StepRunnerRegistry, service: "SemanticLayerService") -> None:
    registry.register("synthesize_findings", lambda session_id, _params=None: service._run_synthesis(session_id))
