"""Validate baseline options and named comparison declarations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypeVar

from marivo._definition.correspondence import CalendarCorrespondence
from marivo.analysis.dsl.frame_axis import FrameAxis
from marivo.analysis.model.errors import reject
from marivo.analysis.model.value_names import validate_paths

BaselineT = TypeVar("BaselineT")


def validate_baseline_options(
    on: tuple[tuple[FrameAxis, FrameAxis], ...] | None,
    match: CalendarCorrespondence | None,
) -> None:
    if on is not None and (
        type(on) is not tuple
        or any(
            type(pair) is not tuple
            or len(pair) != 2  # type: ignore[redundant-expr]  # Preserve validation for untyped inputs.
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


def named_baselines(
    baselines: object, declaration: type[BaselineT], *, on: object, match: object
) -> tuple[tuple[str, BaselineT], ...]:
    """Normalize named inputs before binding the current Frame."""
    if isinstance(baselines, Mapping) and baselines:
        if on is not None or match is not None:
            reject(
                "correspondence rules inside each named baseline declaration",
                repr((on, match)),
                'Use compare({"alias": mv.baseline(frame, on=..., match=...)}).',
                "frame.compare",
                canonical_id="analysis_frame.compare",
            )
        if any(type(value) is not declaration for value in baselines.values()):
            reject(
                "a ComparisonBaseline declaration for every named baseline",
                repr(tuple(type(value).__name__ for value in baselines.values())),
                'Use compare({"alias": mv.baseline(frame, on=..., match=...)}).',
                "frame.compare",
                canonical_id="analysis_frame.compare",
            )
        return tuple(baselines.items())
    else:
        reject(
            "one baseline Frame or a nonempty named ComparisonBaseline mapping",
            type(baselines).__name__,
            'Use compare(frame), or compare({"alias": mv.baseline(frame)}).',
            "frame.compare",
            canonical_id="analysis_frame.compare",
        )


def validate_baseline_aliases(names: tuple[str, ...]) -> None:
    validate_paths(
        tuple((name,) for name in names),
        location="frame.compare.aliases",
        canonical_id="analysis_frame.compare",
    )
    if "current" in names:
        reject(
            "baseline aliases other than current",
            repr(names),
            "Rename the baseline alias.",
            "frame.compare",
            canonical_id="analysis_frame.compare",
        )


def show_baseline(
    preview: str,
    on: tuple[tuple[FrameAxis, FrameAxis], ...] | None,
    match: CalendarCorrespondence | None,
) -> None:
    print(preview)
    if on is not None:
        for left, right in on[:8]:
            print(f"on: {left.token[:40]!r} -> {right.token[:40]!r}")
        if len(on) > 8:
            print(f"on: {len(on) - 8} additional pairs omitted")
    print(f"match: {repr(match)[:160]}")
