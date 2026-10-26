"""Immutable baseline declarations consumed by named Frame comparisons."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from marivo._definition.correspondence import CalendarCorrespondence
from marivo.analysis.core.model import reject
from marivo.analysis.dsl.frame_axis import FrameAxis

if TYPE_CHECKING:
    from marivo.analysis.dsl.frame import AnalysisFrame


@dataclass(frozen=True, slots=True, init=False, repr=False, eq=False)
class ComparisonBaseline:
    """One immutable Frame and its requested comparison correspondence.

    Args: No public constructor; use mv.baseline(frame, on=..., match=...).
    Returns: A declaration with read-only frame, on and match fields.
    Example: ``current.compare({'prior': mv.baseline(previous)})``.
    Constraints: Captures inputs without reading data or binding a current Frame.
    Direct construction and subclass extension are unsupported.
    """

    frame: AnalysisFrame
    on: tuple[tuple[FrameAxis, FrameAxis], ...] | None
    match: CalendarCorrespondence | None

    def __init__(self) -> None:
        raise TypeError("Use mv.baseline(frame, on=..., match=...).")

    def __repr__(self) -> str:
        axes = "inferred" if self.on is None else str(len(self.on))
        rule = "inferred" if self.match is None else self.match.kind
        return (
            f"<ComparisonBaseline input={self.frame._input_id[:16]} "
            f"axes={axes} match={rule}; use .show()>"
        )

    def show(self) -> None:
        """Print bounded captured baseline metadata without reading data.

        Args: None.
        Returns: None; prints the input identity, axis pairs and time rule.
        Example: ``mv.baseline(previous, match=mv.ordinal()).show()``.
        Constraints: Does not call Frame.show, evaluate inputs or bind correspondence.
        """
        print(repr(self))
        if self.on is not None:
            for left, right in self.on[:8]:
                print(f"on: {left.token[:40]!r} -> {right.token[:40]!r}")
            if len(self.on) > 8:
                print(f"on: {len(self.on) - 8} additional pairs omitted")
        print(f"match: {repr(self.match)[:160]}")


def baseline(
    frame: AnalysisFrame,
    *,
    on: tuple[tuple[FrameAxis, FrameAxis], ...] | None = None,
    match: CalendarCorrespondence | None = None,
) -> ComparisonBaseline:
    """Capture one named comparison's Frame and optional correspondence rules.

    Args:
        frame: Baseline Frame; the consuming compare requires the same Session.
        on: Complete (current axis, baseline axis) pairs, or None for inference.
        match: Closed calendar rule, or None for direct-relation/equality inference.
    Returns: An immutable ComparisonBaseline declaration.
    Example: ``current.compare({'prior': mv.baseline(previous, match=mv.ordinal())})``.
    Constraints: Checks input shapes only, with no reads. compare binds ownership,
    complete axes and temporal compatibility against its current Frame.
    """
    from marivo.analysis.dsl.frame import AnalysisFrame

    if not isinstance(frame, AnalysisFrame):
        reject(
            "an AnalysisFrame baseline",
            type(frame).__name__,
            "Pass mv.baseline(frame, on=..., match=...).",
            "baseline",
            canonical_id="baseline",
        )
    if on is not None and (
        type(on) is not tuple
        or any(
            type(pair) is not tuple
            or len(pair) != 2
            or any(not isinstance(axis, FrameAxis) for axis in pair)
            for pair in on
        )
    ):
        reject(
            "a tuple of complete FrameAxis pairs",
            repr(on),
            "Use on=((current.axes[0], frame.axes[0]),), or omit on for inference.",
            "baseline",
            canonical_id="baseline",
        )
    if match is not None and type(match) is not CalendarCorrespondence:
        reject(
            "a closed CalendarCorrespondence",
            repr(match),
            "Use match=mv.ordinal() or mv.offset(...), or omit match for inference.",
            "baseline",
            canonical_id="baseline",
        )
    result = object.__new__(ComparisonBaseline)
    object.__setattr__(result, "frame", frame)
    object.__setattr__(result, "on", on)
    object.__setattr__(result, "match", match)
    return result
