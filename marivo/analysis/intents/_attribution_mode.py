"""Shared validation for explicit multi-axis attribution output modes."""

from __future__ import annotations

from marivo.analysis.attribution_contract import AttributionMode
from marivo.analysis.errors import AnalysisRepair, SemanticKindMismatchError
from marivo.introspection.live.model import LiveHelpTarget

__all__ = ["AttributionMode", "validate_attribution_mode"]


def validate_attribution_mode(
    axis_ids: list[str],
    mode: AttributionMode | None,
    *,
    intent: str,
    legal_modes: tuple[AttributionMode, ...] = ("joint", "hierarchy"),
) -> AttributionMode | None:
    """Validate the explicit output shape required for multi-axis attribution."""
    legacy_multiresolution = mode == "multiresolution"  # type: ignore[comparison-overlap]
    if legacy_multiresolution:
        raise SemanticKindMismatchError(
            message="multiresolution attribution mode has been removed",
            repair=AnalysisRepair(
                kind="retry",
                action=(
                    "Replace mode='multiresolution' with mode='hierarchy'. Then inspect "
                    "AttributionFrame.meta.resolution_evidence before rolling up parent and "
                    "child rows."
                ),
                help_target=LiveHelpTarget(surface="analysis", canonical_id="attribute"),
                snippet="result = session.attribute(delta, axes=axes, mode='hierarchy')",
            ),
            context={
                "argument": "mode",
                "reason": "removed_multi_axis_mode",
                "axis_count": len(axis_ids),
                "mode": mode,
                "supported_modes": list(legal_modes),
                "replacement_mode": "hierarchy",
            },
        )
    if len(axis_ids) <= 1:
        # mode only distinguishes joint vs hierarchy output for *multiple* axes;
        # with a single axis there is nothing to combine, so it is meaningless
        # and ignored rather than forcing callers to branch on axis count.
        return None
    if mode in legal_modes:
        return mode
    if mode is None:
        reason = "multi_axis_mode_required"
        message = f"{intent} requires one of {legal_modes!r} for multiple axes"
    else:
        reason = "invalid_multi_axis_mode"
        message = f"{intent} mode must be one of {legal_modes!r} for multiple axes"
    raise SemanticKindMismatchError(
        message=message,
        context={
            "argument": "mode",
            "reason": reason,
            "axis_count": len(axis_ids),
            "mode": mode,
            "supported_modes": list(legal_modes),
        },
    )
