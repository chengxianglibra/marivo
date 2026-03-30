from __future__ import annotations

from typing import TYPE_CHECKING

from app.analysis_core.step_registry import StepRunnerRegistry

if TYPE_CHECKING:
    from app.service import SemanticLayerService


def _normalize_params(params: dict[str, object] | None) -> dict[str, object]:
    return dict(params or {})


def register(registry: StepRunnerRegistry, service: SemanticLayerService) -> None:
    registry.register(
        "attribute_change",
        lambda session_id, params=None: service._run_attribute_change(  # type: ignore[misc]
            session_id, _normalize_params(params)
        ),
    )
