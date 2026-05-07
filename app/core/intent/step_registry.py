from __future__ import annotations

from collections.abc import Callable
from typing import Any

StepRunner = Callable[[str, dict[str, Any] | None], dict[str, Any]]


class StepRunnerRegistry:
    """Compatibility-friendly registry for future runner extraction."""

    def __init__(self) -> None:
        self._runners: dict[str, StepRunner] = {}

    def register(self, step_type: str, runner: StepRunner) -> None:
        self._runners[step_type.strip().lower()] = runner

    def get(self, step_type: str) -> StepRunner:
        normalized = step_type.strip().lower()
        if normalized not in self._runners:
            raise KeyError(f"Unknown step runner: {step_type}")
        return self._runners[normalized]

    def run(
        self, session_id: str, step_type: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return self.get(step_type)(session_id, params)

    def keys(self) -> list[str]:
        return list(self._runners)

    def supported_step_types(self) -> tuple[str, ...]:
        return tuple(sorted(self._runners))
