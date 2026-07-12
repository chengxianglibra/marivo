"""Shared validation for explicit multi-axis attribution output modes."""

from __future__ import annotations

from typing import Literal

from marivo.analysis.errors import SemanticKindMismatchError

type AttributionMode = Literal["joint", "hierarchy"]


def validate_attribution_mode(
    axis_ids: list[str],
    mode: AttributionMode | None,
    *,
    intent: str,
) -> AttributionMode | None:
    """Validate the explicit output shape required for multi-axis attribution."""
    if len(axis_ids) == 1:
        if mode is not None:
            raise SemanticKindMismatchError(
                message=f"{intent} mode is only valid when multiple axes are requested",
                details={
                    "argument": "mode",
                    "reason": "single_axis_mode_not_applicable",
                    "axis_count": 1,
                    "mode": mode,
                },
            )
        return None
    if mode in {"joint", "hierarchy"}:
        return mode
    if mode is None:
        reason = "multi_axis_mode_required"
        message = f"{intent} requires mode='joint' or mode='hierarchy' for multiple axes"
    else:
        reason = "invalid_multi_axis_mode"
        message = f"{intent} mode must be 'joint' or 'hierarchy' for multiple axes"
    raise SemanticKindMismatchError(
        message=message,
        details={
            "argument": "mode",
            "reason": reason,
            "axis_count": len(axis_ids),
            "mode": mode,
            "supported_modes": ["joint", "hierarchy"],
        },
    )
