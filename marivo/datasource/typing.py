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
    synonyms: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()
    instructions: str | None = None
    owner_notes: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("guardrails", "synonyms", "examples"):
            value = getattr(self, field_name)
            if not isinstance(value, tuple):
                raise TypeError(
                    f"AiContextValue.{field_name} must be tuple[str, ...], "
                    f"got {type(value).__name__}. "
                    f"Use ms.ai_context() to construct AiContextValue."
                )
            if value and not all(isinstance(item, str) for item in value):
                raise TypeError(
                    f"AiContextValue.{field_name} must be tuple[str, ...], "
                    f"got non-string items. "
                    f"Use ms.ai_context() to construct AiContextValue."
                )
        for field_name in ("business_definition", "instructions", "owner_notes"):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, str):
                raise TypeError(
                    f"AiContextValue.{field_name} must be str | None, "
                    f"got {type(value).__name__}. "
                    f"Use ms.ai_context() to construct AiContextValue."
                )
