"""Exact elapsed views over one retained canonical Journey assignment."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from marivo.analysis.core.domain_captures import fail
from marivo.analysis.core.model import Cell, Defined, Undefined, Unknown
from marivo.analysis.materialization.execute_deadline import check
from marivo.analysis.methods.journey_matching import CoverageWindow, JourneyAssignment

DurationStatus = Literal[
    "complete", "incomplete", "coverage_censored", "not_entered", "entry_unknown"
]


CELL_REASONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("undefined", ("not_entered", "not_completed")),
    ("unknown", ("entry_unknown", "coverage_censored")),
)


@dataclass(frozen=True, slots=True)
class DurationObservation:
    status: DurationStatus
    started_at: Cell
    completed_at: Cell
    duration: Cell
    observed_duration: Cell
    followup_until: Cell
    unit: Literal["us"] = "us"


def ticks(start: datetime, end: datetime) -> int:
    """Subtract captured microsecond instants without a floating intermediate."""
    if start.utcoffset() is None or end.utcoffset() is None:
        fail("duration_time", "elapsed duration requires captured aware instants", stage="consume")
    delta = end.astimezone(timezone.utc) - start.astimezone(timezone.utc)
    value = (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds
    if not 0 <= value < 2**63:
        fail("duration_overflow", "negative or overflowing elapsed ticks", stage="consume")
    return value


def duration(
    assignment: JourneyAssignment,
    *,
    from_step: int,
    to_step: int,
    events: tuple[str, ...],
    completion_through: datetime,
    coverage: tuple[CoverageWindow, ...],
) -> DurationObservation:
    """Project one exact ordered step pair, retaining unresolved and absent entry."""
    check()
    if (
        type(from_step) is not int
        or type(to_step) is not int
        or not 0 <= from_step < to_step < len(assignment.steps)
        or len(events) != len(assignment.steps)
        or len(assignment.reach) != len(assignment.steps)
    ):
        fail("duration_steps", "expected an exact increasing pair in the retained pattern")
    entered, completed = assignment.steps[from_step], assignment.steps[to_step]
    if entered is None:
        if assignment.reach[from_step] == "unreachable":
            absent = Undefined("not_entered")
            return DurationObservation(
                "not_entered",
                absent,
                Undefined("not_completed"),
                Undefined("not_completed"),
                absent,
                absent,
            )
        unknown = Unknown("entry_unknown")
        return DurationObservation(
            "entry_unknown",
            unknown,
            Undefined("not_completed"),
            Undefined("not_completed"),
            unknown,
            unknown,
        )
    if completed is not None:
        elapsed = Defined(ticks(entered.instant, completed.instant))
        return DurationObservation(
            "complete",
            Defined(entered.instant),
            Defined(completed.instant),
            elapsed,
            elapsed,
            Defined(completed.instant),
        )
    if assignment.reach[to_step] == "unreachable":
        elapsed = Defined(ticks(entered.instant, completion_through))
        return DurationObservation(
            "incomplete",
            Defined(entered.instant),
            Undefined("not_completed"),
            Undefined("not_completed"),
            elapsed,
            Defined(completion_through),
        )
    missing = next(
        index for index in range(from_step + 1, to_step + 1) if assignment.steps[index] is None
    )
    previous = assignment.steps[missing - 1]
    assert previous is not None
    window = next((item for item in coverage if item.event == events[missing]), None)
    followup: Cell = Unknown("coverage_censored")
    observed: Cell = Unknown("coverage_censored")
    if (
        window is not None
        and (window.start is None or window.start <= previous.instant)
        and window.end >= previous.instant
    ):
        until = min(window.end, completion_through)
        observed = Defined(ticks(entered.instant, until))
        followup = Defined(until)
    return DurationObservation(
        "coverage_censored",
        Defined(entered.instant),
        Undefined("not_completed"),
        Undefined("not_completed"),
        observed,
        followup,
    )


def dropped_before(assignment: JourneyAssignment, *, step: int, policy: str) -> Cell:
    """Read known dropout only for a first-per-subject noninitial exact step."""
    check()
    if (
        policy != "first_per_subject"
        or type(step) is not int
        or not 0 < step < len(assignment.reach)
    ):
        fail("dropout_policy", "dropout requires first_per_subject and a noninitial exact step")
    reach = assignment.reach[step]
    return Unknown("coverage_censored") if reach == "unknown" else Defined(reach == "unreachable")
