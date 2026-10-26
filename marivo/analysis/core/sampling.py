"""Bounded, immutable witnesses for demanded fixed-clock sample failures."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from marivo._definition.observations import ObservationPeriod
from marivo.analysis.core.frozen_calendar import FrozenScalar


@dataclass(frozen=True, slots=True)
class SamplingGroup:
    coordinates: tuple[tuple[str, FrozenScalar | None], ...]
    expected_count: int
    observed_count: int
    missing_points: tuple[datetime, ...]
    missing_truncated: bool


@dataclass(frozen=True, slots=True)
class SamplingFailureContext:
    metric: str
    field: str
    reducer: str
    occurrence: tuple[int, ...]
    period: ObservationPeriod
    groups: tuple[SamplingGroup, ...]
    groups_truncated: bool


def render_sampling_failure(context: SamplingFailureContext) -> str:
    """Render the same bounded evidence for local and native failures."""
    lines = [
        "Sampling coverage:",
        f"  Metric: {context.metric}; reducer: {context.reducer}; layer: {context.occurrence}",
        f"  Time field: {context.field}",
        f"  Interval: [{context.period.start}, {context.period.end}); timezone: {context.period.timezone}",
    ]
    for group in context.groups:
        coordinates = (
            ", ".join(f"{name}={value!r}" for name, value in group.coordinates) or "overall"
        )
        points = ", ".join(point.isoformat() for point in group.missing_points)
        suffix = " (missing-point examples truncated)" if group.missing_truncated else ""
        lines.append(
            f"  {coordinates}: covered {group.observed_count}/{group.expected_count}; missing: {points}{suffix}"
        )
    if context.groups_truncated:
        lines.append("  Failure groups truncated to six examples.")
    return "\n".join(lines)
