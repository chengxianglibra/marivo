"""Shared normalization and validation for intent parameters.

This module consolidates the duplicated guard/normalization logic that was
previously scattered across observe.py, detect.py, diagnose.py, attribute.py,
and validate.py. It runs after AOI structural validation passes but before
intent handler business logic.

Each function is a pure validator/normalizer with no runtime dependencies.
"""

from __future__ import annotations

from marivo.time_contracts import normalize_hour_boundary

_VALID_GRANULARITIES = frozenset({"hour", "day", "week", "month"})


def normalize_metric_ref(metric_ref: str | None) -> str:
    """Strip whitespace and reject empty/None metric refs.

    Runtime-level normalization (e.g. prefix resolution) is intentionally
    NOT done here — that requires the runtime.core instance and happens
    in the intent handler.
    """
    if not metric_ref or not metric_ref.strip():
        raise ValueError("intent requires 'metric'")
    return metric_ref.strip()


def normalize_dimensions(dimensions: list[str] | None) -> list[str] | None:
    """Normalize a dimensions list: strip, dedup, remove blanks.

    Returns None if the result is empty (matches AOI semantics where
    absent dimensions means scalar/time-series mode).
    """
    if dimensions is None:
        return None
    seen: set[str] = set()
    result: list[str] = []
    for d in dimensions:
        stripped = d.strip()
        if stripped and stripped not in seen:
            seen.add(stripped)
            result.append(stripped)
    return result if result else None


def validate_granularity(granularity: str | None) -> str | None:
    """Validate granularity value against allowed set."""
    if granularity is None:
        return None
    if granularity not in _VALID_GRANULARITIES:
        raise ValueError(
            f"granularity='{granularity}' is not valid. "
            f"Must be one of: {sorted(_VALID_GRANULARITIES)}"
        )
    return granularity


def validate_hour_boundaries(granularity: str | None, start: str | None, end: str | None) -> None:
    """When granularity is 'hour', enforce datetime (not date-only) boundaries."""
    if granularity != "hour":
        return
    if start:
        normalize_hour_boundary(str(start), label="time_scope.start")
    if end:
        normalize_hour_boundary(str(end), label="time_scope.end")
