"""Closed canonical replay records and a deadline-aware scalar state machine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from itertools import groupby
from typing import ClassVar, Literal

from pydantic import ConfigDict

from marivo.analysis.core.domain_captures import StateModelCapture, fail
from marivo.analysis.materialization.execute_deadline import check

Key = tuple[str | int, ...]
Disposition = Literal[
    "inception",
    "pre_inception",
    "legal_transition",
    "illegal_transition",
    "transition_from_terminal",
    "unknown_origin",
    "unknown_followup",
]


@dataclass(frozen=True, slots=True)
class Occurrence:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    event: str
    key: Key
    occurred_at: datetime
    sequence: int | str | None = None


@dataclass(frozen=True, slots=True)
class Evaluation:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    occurrence: Occurrence
    before: str | None
    after: str | None
    disposition: Disposition
    terminal_set: bool = False


@dataclass(frozen=True, slots=True)
class Transition:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    ordinal: int
    occurrence: Occurrence
    from_state: str
    to_state: str


@dataclass(frozen=True, slots=True)
class Interval:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    ordinal: int
    state: str
    entered: Occurrence
    exited: Occurrence | None
    start: datetime
    end: datetime
    status: Literal["completed", "right_censored", "coverage_censored"]
    left_clipped: bool
    observed_ticks: int | None


@dataclass(frozen=True, slots=True)
class History:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    subject: Key
    classification: Literal["seeded", "not_started", "coverage_censored"]
    inception: Occurrence | None
    known_through: datetime | None
    evaluations: tuple[Evaluation, ...]
    transitions: tuple[Transition, ...]
    violations: tuple[Evaluation, ...]
    pre_inception: tuple[Occurrence, ...]
    intervals: tuple[Interval, ...]


def ordered(
    group: tuple[Occurrence, ...], model: StateModelCapture, *, terminal: bool
) -> tuple[tuple[Occurrence, ...], bool]:
    """Topologically order facts; only a proved terminal group is set-valued."""
    order = model.order
    declarations = (
        {} if order is None else {item.event_ref: item for item in order.definition.sequences}
    )
    ranks: dict[int, int] = {}
    for index, occurrence in enumerate(group):
        check()
        declaration = declarations.get(occurrence.event)
        if declaration is not None:
            if declaration.order == "integer":
                if (
                    type(occurrence.sequence) is not int
                    or not -(2**63) <= occurrence.sequence < 2**63
                ):
                    fail("business_order", "missing integer sequence", stage="consume")
                ranks[index] = occurrence.sequence
            else:
                if occurrence.sequence not in declaration.order:
                    fail("business_order", "invalid sequence enum", stage="consume")
                ranks[index] = declaration.order.index(occurrence.sequence)
    edges = set()
    for i, first in enumerate(group):
        for j, second in enumerate(group):
            check()
            if i != j and i in ranks and j in ranks and ranks[i] < ranks[j]:
                edges.add((i, j))
            if order is not None and any(
                first.event == edge.before_event and second.event == edge.after_event
                for edge in order.definition.conflicts
            ):
                edges.add((i, j))
    remaining = set(range(len(group)))
    result: list[Occurrence] = []
    ambiguous = False
    while remaining:
        check()
        ready = {i for i in remaining if not any(b == i and a in remaining for a, b in edges)}
        if not ready:
            fail("business_order", "contradictory sequence/precedence cycle", stage="consume")
        if len(ready) > 1:
            ambiguous = True
            if not terminal:
                fail(
                    "business_order",
                    "simultaneous replay triggers require a unique business_order; terminal state must already be proved before the group",
                    stage="consume",
                )
        # Identity orders a set for storage only. It cannot choose a replay outcome.
        result.extend(group[i] for i in sorted(ready, key=lambda i: (group[i].event, group[i].key)))
        remaining -= ready
    if ambiguous:
        result.sort(key=lambda item: (item.event, item.key))
    return tuple(result), ambiguous


def ticks(start: datetime, end: datetime) -> int:
    delta = end - start
    value = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
    if not 0 <= value < 2**63:
        fail("history_duration", "negative or overflowing interval ticks", stage="consume")
    return value


def replay(
    subject: Key,
    occurrences: tuple[Occurrence, ...],
    model: StateModelCapture,
    *,
    start: datetime,
    end: datetime,
    known_through: datetime | None,
) -> History:
    definition = model.definition
    initial = next(state.name for state in definition.states if state.initial)
    terminals = {state.name for state in definition.states if state.terminal}
    inceptions = {item.trigger.event_ref for item in definition.inceptions}
    transitions = {
        (item.from_state, item.trigger.event_ref): item.to_state for item in definition.transitions
    }
    state: str | None = None
    inception: Occurrence | None = None
    evaluations: list[Evaluation] = []
    legal: list[Transition] = []
    entries: list[tuple[str, Occurrence]] = []
    for point, grouped in groupby(
        sorted(occurrences, key=lambda item: item.occurred_at), lambda item: item.occurred_at
    ):
        check()
        if point.utcoffset() is None or point >= end:
            fail("history_input", "trigger outside captured aware exclusive end", stage="consume")
        group, terminal_set = ordered(
            tuple(grouped),
            model,
            terminal=state in terminals,
        )
        for occurrence in group:
            check()
            if known_through is None or (point >= known_through and state not in terminals):
                state = None
                evaluations.append(
                    Evaluation(
                        occurrence,
                        None,
                        None,
                        "unknown_origin" if known_through is None else "unknown_followup",
                    )
                )
                continue
            before = state
            if state is None:
                if occurrence.event in inceptions:
                    state = initial
                    inception = occurrence
                    disposition: Disposition = "inception"
                    entries.append((state, occurrence))
                else:
                    disposition = "pre_inception"
            elif state in terminals:
                disposition = "transition_from_terminal"
            elif occurrence.event in inceptions:
                disposition = "illegal_transition"
            elif (state, occurrence.event) in transitions:
                state = transitions[state, occurrence.event]
                disposition = "legal_transition"
                assert before is not None
                legal.append(Transition(len(legal) + 1, occurrence, before, state))
                entries.append((state, occurrence))
            else:
                disposition = "illegal_transition"
            evaluations.append(Evaluation(occurrence, before, state, disposition, terminal_set))
    complete = known_through == end
    if complete and occurrences and inception is None:
        fail(
            "history_inception",
            "complete source-origin history has modeled triggers but no required inception",
            stage="consume",
        )
    trusted = (
        inception
        if inception is not None
        and known_through is not None
        and inception.occurred_at < known_through
        else None
    )
    classification: Literal["seeded", "not_started", "coverage_censored"] = (
        "seeded"
        if complete and trusted is not None
        else "not_started"
        if complete
        else "coverage_censored"
    )
    intervals = fragments(tuple(entries), start=start, end=end, known_through=known_through)
    return History(
        subject,
        classification,
        trusted,
        known_through,
        tuple(evaluations),
        tuple(legal),
        tuple(
            e
            for e in evaluations
            if e.disposition in ("illegal_transition", "transition_from_terminal")
        ),
        tuple(e.occurrence for e in evaluations if e.disposition == "pre_inception"),
        tuple(intervals),
    )


def fragments(
    entries: tuple[tuple[str, Occurrence], ...],
    *,
    start: datetime,
    end: datetime,
    known_through: datetime | None,
) -> tuple[Interval, ...]:
    intervals: list[Interval] = []
    for index, (entered_state, entered) in enumerate(entries):
        check()
        exited = entries[index + 1][1] if index + 1 < len(entries) else None
        left = max(start, entered.occurred_at)
        right = min(end, end if exited is None else exited.occurred_at)
        if left >= right:
            continue
        proved = (
            known_through is not None
            and entered.occurred_at < known_through
            and right <= known_through
            and (exited is None or exited.occurred_at < known_through)
        )
        status: Literal["completed", "right_censored", "coverage_censored"] = (
            ("completed" if exited is not None else "right_censored")
            if proved
            else "coverage_censored"
        )
        observed = (
            None
            if known_through is None or left >= known_through
            else ticks(left, min(right, known_through))
        )
        intervals.append(
            Interval(
                index + 1,
                entered_state,
                entered,
                exited,
                left,
                right,
                status,
                entered.occurred_at < start,
                observed,
            )
        )
    return tuple(intervals)
