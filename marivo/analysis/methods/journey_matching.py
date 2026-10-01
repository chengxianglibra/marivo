"""Canonical assignment over governed, business-ordered occurrence inputs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise
from typing import Literal

from marivo.analysis.core.domain_captures import fail
from marivo.analysis.event import EveryStart, FirstPerSubject
from marivo.analysis.materialization.execute_deadline import check

Key = tuple[str | int, ...]
Reach = Literal["reached", "unreachable", "unknown"]


@dataclass(frozen=True, slots=True)
class OrderedOccurrence:
    """One captured occurrence with an ordinal proved by the order consumer."""

    event: str
    key: Key
    subject: Key
    instant: datetime
    ordinal: int


@dataclass(frozen=True, slots=True)
class CoverageWindow:
    """A validated Event coverage interval; None start denotes source origin."""

    event: str
    start: datetime | None
    end: datetime

    def covers(self, start: datetime, end: datetime) -> bool:
        return (self.start is None or self.start <= start) and self.end >= end


@dataclass(frozen=True, slots=True)
class JourneyAssignment:
    """Retained assignment; callers bind its identity to pattern and realization."""

    subject: Key
    start: OrderedOccurrence
    steps: tuple[OrderedOccurrence | None, ...]
    reach: tuple[Reach, ...]


def match(
    occurrences: tuple[OrderedOccurrence, ...],
    *,
    events: tuple[str, ...],
    policy: FirstPerSubject | EveryStart,
    cohort_start: datetime,
    cohort_end: datetime,
    completion_through: datetime,
    coverage: tuple[CoverageWindow, ...],
) -> tuple[JourneyAssignment, ...]:
    """Assign exact steps using captured order and attempt-specific coverage."""
    check()
    if (
        not events
        or type(policy) not in (FirstPerSubject, EveryStart)
        or any(not event for event in events)
        or any(
            value.utcoffset() is None for value in (cohort_start, cohort_end, completion_through)
        )
        or not cohort_start < cohort_end <= completion_through
        or len({window.event for window in coverage}) != len(coverage)
        or any(
            window.event not in events
            or window.end.utcoffset() is None
            or (
                window.start is not None
                and (window.start.utcoffset() is None or window.start >= window.end)
            )
            for window in coverage
        )
    ):
        fail("journey_binding", "invalid pattern, coverage or matching bounds")
    windows = {window.event: window for window in coverage}
    groups: dict[Key, list[OrderedOccurrence]] = {}
    identities: set[tuple[str, Key]] = set()
    ordinals: dict[Key, set[int]] = {}
    for occurrence in occurrences:
        check()
        identity = (occurrence.event, occurrence.key)
        if (
            occurrence.event not in events
            or not occurrence.key
            or not occurrence.subject
            or any(
                type(value) not in (str, int) or value == ""
                for value in (*occurrence.key, *occurrence.subject)
            )
            or occurrence.instant.utcoffset() is None
            or type(occurrence.ordinal) is not int
            or not cohort_start <= occurrence.instant < completion_through
            or identity in identities
            or occurrence.ordinal in ordinals.setdefault(occurrence.subject, set())
        ):
            fail("journey_input", "invalid or duplicate captured occurrence", stage="consume")
        identities.add(identity)
        ordinals[occurrence.subject].add(occurrence.ordinal)
        groups.setdefault(occurrence.subject, []).append(occurrence)
    result: list[JourneyAssignment] = []
    for subject, captured in groups.items():
        check()
        ordered = sorted(captured, key=lambda item: item.ordinal)
        if any(left.instant > right.instant for left, right in pairwise(ordered)):
            fail("business_order", "captured ordinal reverses instant order", stage="consume")
        starts = [item for item in ordered if item.event == events[0] and item.instant < cohort_end]
        if isinstance(policy, FirstPerSubject):
            starts = starts[:1]
        reserved: set[tuple[str, Key]] = set()
        for start in starts:
            check()
            assigned: list[OrderedOccurrence | None] = [start]
            reach: list[Reach] = ["reached"]
            previous = start
            missing: Reach | None = None
            for index, event in enumerate(events[1:], 1):
                check()
                if missing is not None:
                    assigned.append(None)
                    reach.append(missing)
                    continue
                final_exclusive = (
                    index == len(events) - 1
                    and isinstance(policy, EveryStart)
                    and policy.completion_assignment == "exclusive"
                )
                found = None
                for candidate in ordered:
                    check()
                    if (
                        candidate.ordinal > previous.ordinal
                        and candidate.event == event
                        and (
                            not final_exclusive or (candidate.event, candidate.key) not in reserved
                        )
                    ):
                        found = candidate
                        break
                assigned.append(found)
                if found is None:
                    window = windows.get(event)
                    missing = (
                        "unreachable"
                        if window is not None
                        and window.covers(previous.instant, completion_through)
                        else "unknown"
                    )
                    reach.append(missing)
                else:
                    reach.append("reached")
                    previous = found
                    if final_exclusive:
                        reserved.add((found.event, found.key))
            result.append(JourneyAssignment(subject, start, tuple(assigned), tuple(reach)))
    return tuple(result)
