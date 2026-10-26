"""Shared AiContext types for marivo.datasource and marivo.semantic."""

from __future__ import annotations

from dataclasses import dataclass
from unicodedata import normalize

__all__ = ["AiContextValue"]


def _normalize_aliases(aliases: tuple[str, ...]) -> tuple[str, ...]:
    """Normalize author-provided search text, retaining first spelling and order."""
    if not isinstance(aliases, tuple):
        raise TypeError(
            "AiContext aliases must be tuple[str, ...], "
            f"got {type(aliases).__name__}. Use ms.ai_context() to construct aliases."
        )
    normalized: list[str] = []
    seen: set[str] = set()
    for alias in aliases:
        if not isinstance(alias, str):
            raise TypeError(
                "AiContext aliases must contain strings. Use ms.ai_context() to construct aliases."
            )
        text = " ".join(normalize("NFKC", alias).split())
        if not text:
            raise ValueError(
                "AiContext aliases must contain non-empty text. "
                "Use ms.ai_context() to construct aliases."
            )
        key = text.casefold()
        if key not in seen:
            seen.add(key)
            normalized.append(text)
    return tuple(normalized)


@dataclass(frozen=True)
class AiContextValue:
    """Validated AI-facing context for semantic and datasource objects.

    Construct via ``ms.ai_context(...)`` only — not from raw dicts.
    All list-type fields are stored as immutable tuples.
    """

    business_definition: str | None = None
    guardrails: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "aliases", _normalize_aliases(self.aliases))
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
