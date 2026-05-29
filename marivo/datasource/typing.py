"""Shared AiContext types for marivo.datasource and marivo.semantic."""

from __future__ import annotations

from typing import Any, TypedDict

from marivo.datasource.ir import AiContextIR

__all__ = ["AiContext"]

_VALID_AI_CONTEXT_KEYS = frozenset(
    {
        "business_definition",
        "guardrails",
        "synonyms",
        "examples",
        "instructions",
        "owner_notes",
    }
)


class AiContext(TypedDict, total=False):
    """Structured AI-facing context for semantic objects.

    All fields are optional. When provided, they give AI agents
    and tooling richer semantic understanding of each object.
    """

    business_definition: str | None
    guardrails: list[str]
    synonyms: list[str]
    examples: list[str]
    instructions: str | None
    owner_notes: str | None


def _build_ai_context(
    ai_context: AiContext | dict[str, Any] | None,
    *,
    on_error: Any,
) -> AiContextIR:
    """Validate and convert ai_context into an AiContextIR.

    Args:
        on_error: Called as ``on_error(message, details)`` on validation
                  failure.  Each caller raises its own error type.
    """
    if ai_context is None:
        return AiContextIR()

    invalid_keys = set(ai_context) - _VALID_AI_CONTEXT_KEYS
    if invalid_keys:
        on_error(
            f"ai_context contains invalid keys: {sorted(invalid_keys)}. "
            f"Allowed keys: {sorted(_VALID_AI_CONTEXT_KEYS)}.",
            {"field": "ai_context", "reason": f"unknown keys: {sorted(invalid_keys)}"},
        )

    data = dict(ai_context)

    for list_key in ("guardrails", "synonyms", "examples"):
        value = data.get(list_key, ())
        if value is None:
            value = ()
        if not isinstance(value, list | tuple) or not all(isinstance(item, str) for item in value):
            on_error(
                f"ai_context['{list_key}'] must be list[str], got {type(value).__name__}.",
                {"field": f"ai_context.{list_key}", "reason": "must be a list of strings"},
            )
        data[list_key] = tuple(value)

    for str_key in ("business_definition", "instructions", "owner_notes"):
        value = data.get(str_key)
        if value is not None and not isinstance(value, str):
            on_error(
                f"ai_context['{str_key}'] must be str | None, got {type(value).__name__}.",
                {"field": f"ai_context.{str_key}", "reason": "must be a string"},
            )

    return AiContextIR(
        business_definition=data.get("business_definition"),
        guardrails=data.get("guardrails", ()),
        synonyms=data.get("synonyms", ()),
        examples=data.get("examples", ()),
        instructions=data.get("instructions"),
        owner_notes=data.get("owner_notes"),
    )
