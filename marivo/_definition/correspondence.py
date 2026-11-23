"""Axis-free correspondence rules and immutable endpoint coordinate projections."""

from __future__ import annotations

from typing import TypeAlias

from marivo._definition.types import (
    AxisBinding,
    CalendarCoordinate,
    IntervalCoordinate,
    RowAttributeTime,
)
from marivo._kernel.temporal import CalendarCorrespondence as CalendarCorrespondence

ComparisonAxis: TypeAlias = AxisBinding | CalendarCoordinate | IntervalCoordinate


def coordinate_matches(left: ComparisonAxis, right: ComparisonAxis) -> bool:
    """Match business coordinates without treating definition versions as identity."""
    if isinstance(left, AxisBinding) and isinstance(right, AxisBinding):
        left_at = (
            (left.at.field.ref, left.at.root.ref)
            if isinstance(left.at, RowAttributeTime)
            else left.at
        )
        right_at = (
            (right.at.field.ref, right.at.root.ref)
            if isinstance(right.at, RowAttributeTime)
            else right.at
        )
        return (
            left.role == right.role
            and left.entity.ref == right.entity.ref
            and (left.field.ref if left.field is not None else None)
            == (right.field.ref if right.field is not None else None)
            and tuple(step.ref for step in left.path) == tuple(step.ref for step in right.path)
            and (left.path_root.ref if left.path_root is not None else None)
            == (right.path_root.ref if right.path_root is not None else None)
            and left.source_at == right.source_at
            and left.step_ats == right.step_ats
            and left_at == right_at
            and left.unit == right.unit
        )
    if isinstance(left, IntervalCoordinate) and isinstance(right, IntervalCoordinate):
        return left.timezone == right.timezone
    if isinstance(left, CalendarCoordinate) and isinstance(right, CalendarCoordinate):
        if (
            left.grain_spec != right.grain_spec
            or left.timezone != right.timezone
            or left.actual_only != right.actual_only
            or left.origin != right.origin
        ):
            return False
        known = {
            cell[0]: (cell, civil)
            for cell, civil in zip(left.cells, left.civil_bounds, strict=True)
        }
        return all(
            known.get(cell[0], (cell, civil)) == (cell, civil)
            for cell, civil in zip(right.cells, right.civil_bounds, strict=True)
        )
    return left == right


def coordinate_position(axes: tuple[ComparisonAxis, ...], axis: ComparisonAxis) -> int:
    """Locate one already validated business coordinate in its captured order."""
    return next(
        index for index, candidate in enumerate(axes) if coordinate_matches(candidate, axis)
    )


def interval_key_pairs(
    target: IntervalCoordinate, source: IntervalCoordinate
) -> tuple[tuple[int, int], ...] | None:
    """Return matching (target, source) ordinals, or None for a shared directory."""
    if target.bounds == source.bounds:
        return None
    targets = {bounds: index for index, bounds in enumerate(target.bounds)}
    return tuple(
        (targets[bounds], index) for index, bounds in enumerate(source.bounds) if bounds in targets
    )
