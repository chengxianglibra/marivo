# DEPRECATED: use app.core.intent.intent_registry
from __future__ import annotations

from collections.abc import Callable
from typing import Any

IntentRunner = Callable[[str, dict[str, Any] | None], dict[str, Any]]


class IntentRunnerRegistry:
    """Registry mapping intent types to runner callables.

    Parallel to StepRunnerRegistry but for the typed intent surface.
    Each runner signature: (session_id, params) -> result_dict.
    """

    def __init__(self) -> None:
        self._runners: dict[str, IntentRunner] = {}

    def register(self, intent_type: str, runner: IntentRunner) -> None:
        self._runners[intent_type.strip().lower()] = runner

    def get(self, intent_type: str) -> IntentRunner:
        normalized = intent_type.strip().lower()
        if normalized not in self._runners:
            raise KeyError(f"Unknown intent runner: {intent_type}")
        return self._runners[normalized]

    def run(
        self, session_id: str, intent_type: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return self.get(intent_type)(session_id, params)

    def keys(self) -> list[str]:
        return list(self._runners)

    def supported_intent_types(self) -> tuple[str, ...]:
        return tuple(sorted(self._runners))
