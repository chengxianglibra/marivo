"""Shared AiContext types for marivo.datasource and marivo.semantic."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["AiContextValue"]


@dataclass(frozen=True)
class AiContextValue:
    """Validated AI-facing context for semantic and datasource objects.

    Construct via ``ms.ai_context(...)`` only — not from raw dicts.
    All list-type fields are stored as immutable tuples.
    """

    business_definition: str | None = None
    guardrails: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.guardrails, tuple):
            raise TypeError(
                "AiContextValue.guardrails must be tuple[str, ...], "
                f"got {type(self.guardrails).__name__}. "
                "Use ms.ai_context() to construct AiContextValue."
            )
        if self.guardrails and not all(isinstance(item, str) for item in self.guardrails):
            raise TypeError(
                "AiContextValue.guardrails must be tuple[str, ...], "
                "got non-string items. Use ms.ai_context() to construct AiContextValue."
            )
        if self.business_definition is not None and not isinstance(self.business_definition, str):
            raise TypeError(
                "AiContextValue.business_definition must be str | None, "
                f"got {type(self.business_definition).__name__}. "
                "Use ms.ai_context() to construct AiContextValue."
            )
