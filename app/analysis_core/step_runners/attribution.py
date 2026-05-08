from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.analysis_core.step_registry import StepRunnerRegistry

if TYPE_CHECKING:
    from app.runtime.runtime import MarivoRuntime


def _normalize_params(params: dict[str, Any] | None) -> dict[str, Any]:
    return dict(params or {})


def register(registry: StepRunnerRegistry, runtime: MarivoRuntime) -> None:
    registry.register(
        "attribute_change",
        lambda session_id, params=None: _run_attribute_change(  # type: ignore[misc]
            runtime, session_id, _normalize_params(params)
        ),
    )


def _run_attribute_change(runtime: Any, session_id: str, params: dict[str, Any]) -> dict[str, Any]:
    from app.runtime.semantic_ops import run_attribute_change

    return run_attribute_change(runtime, session_id, params)
