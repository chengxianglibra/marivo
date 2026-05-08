from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.analysis_core.step_registry import StepRunnerRegistry

if TYPE_CHECKING:
    from app.runtime.runtime import MarivoRuntime


def register(registry: StepRunnerRegistry, runtime: MarivoRuntime) -> None:
    registry.register(
        "metric_query",
        lambda session_id, params=None: _run_metric_query(  # type: ignore[misc]
            runtime, session_id, _normalize_params(params)
        ),
    )
    registry.register(
        "profile_table",
        lambda session_id, params=None: _run_profile_table(  # type: ignore[misc]
            runtime, session_id, _normalize_params(params)
        ),
    )
    registry.register(
        "sample_rows",
        lambda session_id, params=None: _run_sample_rows(  # type: ignore[misc]
            runtime, session_id, _normalize_params(params)
        ),
    )
    registry.register(
        "aggregate_query",
        lambda session_id, params=None: _run_aggregate_query(  # type: ignore[misc]
            runtime, session_id, _normalize_params(params)
        ),
    )


def _run_metric_query(runtime: Any, session_id: str, params: dict[str, Any]) -> dict[str, Any]:
    from app.runtime.semantic_ops import run_metric_query

    return run_metric_query(runtime, session_id, params)


def _run_profile_table(runtime: Any, session_id: str, params: dict[str, Any]) -> dict[str, Any]:
    from app.runtime.semantic_ops import run_profile_table

    return run_profile_table(runtime, session_id, params)


def _run_sample_rows(runtime: Any, session_id: str, params: dict[str, Any]) -> dict[str, Any]:
    from app.runtime.semantic_ops import run_sample_rows

    return run_sample_rows(runtime, session_id, params)


def _run_aggregate_query(runtime: Any, session_id: str, params: dict[str, Any]) -> dict[str, Any]:
    from app.runtime.semantic_ops import run_aggregate_query

    return run_aggregate_query(runtime, session_id, params)


def _normalize_params(params: dict[str, Any] | None) -> dict[str, Any]:
    return dict(params or {})
