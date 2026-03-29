from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.analysis_core.step_registry import StepRunnerRegistry

if TYPE_CHECKING:
    from app.service import SemanticLayerService


def register(registry: StepRunnerRegistry, service: SemanticLayerService) -> None:
    registry.register(
        "metric_query",
        lambda session_id, params=None: service._run_metric_query(
            session_id, _normalize_params(params)
        ),
    )
    registry.register(
        "profile_table",
        lambda session_id, params=None: service._run_profile_table(
            session_id, _normalize_params(params)
        ),
    )
    registry.register(
        "sample_rows",
        lambda session_id, params=None: service._run_sample_rows(
            session_id, _normalize_params(params)
        ),
    )
    registry.register(
        "aggregate_query",
        lambda session_id, params=None: service._run_aggregate_query(
            session_id, _normalize_params(params)
        ),
    )


def _normalize_params(params: dict[str, Any] | None) -> dict[str, Any]:
    return dict(params or {})
