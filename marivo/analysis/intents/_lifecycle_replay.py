"""Pure deterministic replay semantics for one compiled StateModel."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING, Literal, TypeAlias, TypedDict

import pandas as pd

from marivo.analysis.errors import (
    AmbiguousEventOrderError,
    AnalysisRepair,
    InsufficientStateHistoryError,
)
from marivo.analysis.intents._event_occurrences import (
    EventOccurrence,
    OccurrenceSetKey,
    identity_sort_key,
)
from marivo.introspection.live.model import LiveHelpTarget
from marivo.refs import EventKind, Ref, ref

if TYPE_CHECKING:
    from marivo.semantic.ir import StateModelIR, StateTriggerIR

TriggerKey: TypeAlias = tuple[Ref[EventKind], str]
ViolationKind: TypeAlias = Literal["illegal_transition", "transition_from_terminal"]
EvaluationKind: TypeAlias = Literal[
    "ignored_pre_inception",
    "legal_inception",
    "legal_transition",
    "illegal_transition",
    "transition_from_terminal",
]

HISTORY_COLUMNS = (
    "subject_identity",
    "model_state",
    "valid_from",
    "valid_to",
    "entered_by_event_ref",
    "entered_by_event_identity",
    "exited_by_event_ref",
    "exited_by_event_identity",
    "interval_status",
)

TRACE_COLUMNS = (
    "subject_identity",
    "trigger_event_ref",
    "trigger_event_identity",
    "occurred_at",
    "model_state_at_event",
    "violation_kind",
)


@dataclass(frozen=True)
class ReplayResult:
    """Pure replay output consumed by Lifecycle artifact materialization."""

    history: pd.DataFrame
    violations: pd.DataFrame
    population_count: int
    seeded_subject_count: int
    coverage_censored_subject_count: int
    interval_count: int
    violation_count: int
    pre_inception_ignored_counts: dict[str, int]


@dataclass(frozen=True)
class _ReplayOccurrence:
    trigger: TriggerKey
    occurrence: EventOccurrence

    @property
    def event_ref(self) -> Ref[EventKind]:
        return self.occurrence.event_ref


@dataclass(frozen=True)
class _Evaluation:
    state_after: str | None
    kind: EvaluationKind


@dataclass(frozen=True)
class _ReplayMachine:
    state_order: tuple[str, ...]
    terminal_states: frozenset[str]
    initial_state: str
    inception_triggers: frozenset[TriggerKey]
    transitions: dict[tuple[str, TriggerKey], str]
    trigger_order: tuple[TriggerKey, ...]


@dataclass(frozen=True)
class _GroupOutcome:
    state_after: str | None
    classifications: tuple[tuple[int, EvaluationKind], ...]
    order: tuple[int, ...]


class _HistoryRow(TypedDict):
    subject_identity: tuple[object, ...]
    model_state: str
    valid_from: pd.Timestamp
    valid_to: pd.Timestamp
    entered_by_event_ref: str
    entered_by_event_identity: tuple[object, ...]
    exited_by_event_ref: str | None
    exited_by_event_identity: tuple[object, ...] | None
    interval_status: Literal["completed", "right_censored", "coverage_censored"]


class _TraceRow(TypedDict):
    subject_identity: tuple[object, ...]
    trigger_event_ref: str
    trigger_event_identity: tuple[object, ...]
    occurred_at: pd.Timestamp
    model_state_at_event: str
    violation_kind: ViolationKind


def _event_ref(value: str) -> Ref[EventKind]:
    path = value.removeprefix("event:")
    return ref.event(path)


def _trigger_key(trigger: StateTriggerIR) -> TriggerKey:
    return (
        _event_ref(trigger.event_ref),
        trigger.participant_role,
    )


def _compile_machine(model: StateModelIR) -> _ReplayMachine:
    states = tuple(model.states)
    initial = tuple(state.name for state in states if state.initial)
    if len(initial) != 1:
        raise ValueError("compiled StateModel replay requires exactly one initial state")
    inception_triggers = frozenset(
        _trigger_key(inception.trigger) for inception in model.inceptions
    )
    transitions = {
        (transition.from_state, _trigger_key(transition.trigger)): transition.to_state
        for transition in model.transitions
    }
    trigger_order = tuple(
        dict.fromkeys(
            (
                *(_trigger_key(inception.trigger) for inception in model.inceptions),
                *(_trigger_key(transition.trigger) for transition in model.transitions),
            )
        )
    )
    return _ReplayMachine(
        state_order=tuple(state.name for state in states),
        terminal_states=frozenset(state.name for state in states if state.terminal),
        initial_state=initial[0],
        inception_triggers=inception_triggers,
        transitions=transitions,
        trigger_order=trigger_order,
    )


def _evaluate(
    *,
    machine: _ReplayMachine,
    state: str | None,
    trigger: TriggerKey,
) -> _Evaluation:
    if state is None:
        if trigger in machine.inception_triggers:
            return _Evaluation(
                state_after=machine.initial_state,
                kind="legal_inception",
            )
        return _Evaluation(state_after=None, kind="ignored_pre_inception")
    if state in machine.terminal_states:
        return _Evaluation(
            state_after=state,
            kind="transition_from_terminal",
        )
    if trigger in machine.inception_triggers:
        return _Evaluation(state_after=state, kind="illegal_transition")
    target = machine.transitions.get((state, trigger))
    if target is None:
        return _Evaluation(state_after=state, kind="illegal_transition")
    return _Evaluation(state_after=target, kind="legal_transition")


def _same_time_outcomes(
    *,
    machine: _ReplayMachine,
    state: str | None,
    occurrences: tuple[_ReplayOccurrence, ...],
) -> tuple[_GroupOutcome, ...]:
    """Enumerate outcome-distinct cross-Event interleavings.

    The declared Event identity order is retained inside each EventRef.
    Distinct participant triggers for the same physical Event identity remain
    unordered. Dynamic programming collapses equivalent prefixes, so the
    search scales with admissible subsets rather than raw permutation count.
    """

    identity_keys = tuple(identity_sort_key(item.occurrence.event_identity) for item in occurrences)
    predecessors = tuple(
        frozenset(
            candidate_index
            for candidate_index, candidate in enumerate(occurrences)
            if candidate.event_ref == item.event_ref
            and identity_keys[candidate_index] < identity_keys[index]
        )
        for index, item in enumerate(occurrences)
    )

    @cache
    def visit(
        remaining: int,
        current_state: str | None,
    ) -> tuple[_GroupOutcome, ...]:
        if remaining == 0:
            return (
                _GroupOutcome(
                    state_after=current_state,
                    classifications=(),
                    order=(),
                ),
            )
        by_signature: dict[
            tuple[str | None, tuple[tuple[int, EvaluationKind], ...]],
            _GroupOutcome,
        ] = {}
        for occurrence_index, item in enumerate(occurrences):
            occurrence_bit = 1 << occurrence_index
            if remaining & occurrence_bit == 0:
                continue
            if any(
                remaining & (1 << predecessor) for predecessor in predecessors[occurrence_index]
            ):
                continue
            evaluation = _evaluate(
                machine=machine,
                state=current_state,
                trigger=item.trigger,
            )
            for suffix in visit(remaining ^ occurrence_bit, evaluation.state_after):
                classifications = tuple(
                    sorted(
                        (
                            (occurrence_index, evaluation.kind),
                            *suffix.classifications,
                        )
                    )
                )
                outcome = _GroupOutcome(
                    state_after=suffix.state_after,
                    classifications=classifications,
                    order=(occurrence_index, *suffix.order),
                )
                signature = (outcome.state_after, outcome.classifications)
                by_signature.setdefault(signature, outcome)
                if len(by_signature) > 1:
                    return tuple(by_signature.values())
        return tuple(by_signature.values())

    return visit((1 << len(occurrences)) - 1, state)


def _ordered_same_time_group(
    *,
    machine: _ReplayMachine,
    state: str | None,
    subject_occurrences: tuple[_ReplayOccurrence, ...],
) -> tuple[_ReplayOccurrence, ...]:
    outcomes = _same_time_outcomes(
        machine=machine,
        state=state,
        occurrences=subject_occurrences,
    )
    if len(outcomes) > 1:
        instant = subject_occurrences[0].occurrence.occurred_at.isoformat()
        event_refs = tuple(sorted({item.event_ref.key for item in subject_occurrences}))
        raise AmbiguousEventOrderError(
            message=("same-time modeled Events have order-dependent lifecycle outcomes"),
            expected=(
                "distinct occurrence times whenever cross-Event order changes "
                "state or violation classification"
            ),
            received=f"events={event_refs!r}, occurred_at={instant}",
            location="session.lifecycle.replay.occurrence_order",
            repair=AnalysisRepair(
                kind="inspect",
                action=(
                    "Inspect the source timestamps and model a more precise Event "
                    "occurred_at value before replaying."
                ),
                help_target=LiveHelpTarget(
                    surface="analysis",
                    canonical_id="lifecycle.replay",
                ),
            ),
        )
    return tuple(subject_occurrences[index] for index in outcomes[0].order)


def _occurrences_by_subject(
    *,
    machine: _ReplayMachine,
    occurrence_sets: dict[OccurrenceSetKey, tuple[EventOccurrence, ...]],
    end: pd.Timestamp,
) -> dict[tuple[object, ...], tuple[_ReplayOccurrence, ...]]:
    indexed: dict[tuple[object, ...], list[_ReplayOccurrence]] = {}
    for trigger in machine.trigger_order:
        for occurrence in occurrence_sets.get(trigger, ()):
            if occurrence.occurred_at >= end:
                continue
            indexed.setdefault(occurrence.subject_identity, []).append(
                _ReplayOccurrence(trigger=trigger, occurrence=occurrence)
            )
    return {
        subject_identity: tuple(
            sorted(
                items,
                key=lambda item: (
                    item.occurrence.occurred_at,
                    item.event_ref.key,
                    identity_sort_key(item.occurrence.event_identity),
                    item.occurrence.participant_name,
                ),
            )
        )
        for subject_identity, items in indexed.items()
    }


def _empty_history() -> pd.DataFrame:
    return pd.DataFrame(columns=HISTORY_COLUMNS)


def _empty_trace() -> pd.DataFrame:
    return pd.DataFrame(columns=TRACE_COLUMNS)


def _append_interval(
    rows: list[_HistoryRow],
    *,
    subject_identity: tuple[object, ...],
    state: str,
    entered_by: EventOccurrence,
    exited_by: EventOccurrence | None,
    window_start: pd.Timestamp,
    window_end: pd.Timestamp,
    final_status: Literal["right_censored", "coverage_censored"],
) -> None:
    actual_end = exited_by.occurred_at if exited_by is not None else window_end
    valid_from = max(entered_by.occurred_at, window_start)
    valid_to = min(actual_end, window_end)
    if valid_from >= valid_to:
        return
    if exited_by is not None and exited_by.occurred_at < window_end:
        exited_ref: str | None = exited_by.event_ref.key
        exited_identity: tuple[object, ...] | None = exited_by.event_identity
        interval_status: Literal[
            "completed",
            "right_censored",
            "coverage_censored",
        ] = "completed"
    else:
        exited_ref = None
        exited_identity = None
        interval_status = final_status
    rows.append(
        {
            "subject_identity": subject_identity,
            "model_state": state,
            "valid_from": valid_from,
            "valid_to": valid_to,
            "entered_by_event_ref": entered_by.event_ref.key,
            "entered_by_event_identity": entered_by.event_identity,
            "exited_by_event_ref": exited_ref,
            "exited_by_event_identity": exited_identity,
            "interval_status": interval_status,
        }
    )


def replay_state_model(
    *,
    model: StateModelIR,
    occurrence_sets: dict[OccurrenceSetKey, tuple[EventOccurrence, ...]],
    window_start: pd.Timestamp,
    window_end: pd.Timestamp,
    coverage_complete: bool,
    population: tuple[tuple[object, ...], ...] | None = None,
) -> ReplayResult:
    """Replay one compiled StateModel and clip state intervals to ``[start, end)``.

    Args:
        model: Catalog-compiled StateModel IR with canonical Event/role triggers.
        occurrence_sets: Normalized role-specific streams from the shared Event
            occurrence materializer.
        window_start: Inclusive timezone-aware UTC replay output bound.
        window_end: Exclusive timezone-aware UTC replay output bound.
        coverage_complete: Whether every modeled Event is authoritatively
            complete through ``window_end``.
        population: Exact cohort membership, or ``None`` to use the union of
            subjects observed through modeled trigger roles.

    Returns:
        Public history rows, private violation rows, and replay counts.

    Raises:
        AmbiguousEventOrderError: Same-time cross-Event ordering changes state
            or violation classification.
        InsufficientStateHistoryError: Complete input history proves that one
            or more population subjects have no qualifying inception.
    """

    if (
        not isinstance(window_start, pd.Timestamp)
        or not isinstance(window_end, pd.Timestamp)
        or window_start.tzinfo is None
        or window_end.tzinfo is None
        or window_start >= window_end
    ):
        raise ValueError("replay requires a non-empty timezone-aware half-open window")
    start = window_start.tz_convert("UTC")
    end = window_end.tz_convert("UTC")
    machine = _compile_machine(model)
    if not machine.inception_triggers:
        raise ValueError("from_inception replay requires at least one inception trigger")

    occurrences_by_subject = _occurrences_by_subject(
        machine=machine,
        occurrence_sets=occurrence_sets,
        end=end,
    )
    observed_population = set(occurrences_by_subject)
    if population is None:
        replay_population = tuple(sorted(observed_population, key=identity_sort_key))
    else:
        if len(set(population)) != len(population):
            raise ValueError("replay population identities must be unique")
        replay_population = tuple(sorted(population, key=identity_sort_key))

    history_rows: list[_HistoryRow] = []
    trace_rows: list[_TraceRow] = []
    pre_inception_counts = {
        f"{event_ref.path}#{participant_role}": 0
        for event_ref, participant_role in machine.trigger_order
    }
    seeded_subject_count = 0
    coverage_censored_subject_count = 0
    missing_inception_count = 0
    final_status: Literal["right_censored", "coverage_censored"] = (
        "right_censored" if coverage_complete else "coverage_censored"
    )

    for subject_identity in replay_population:
        occurrences = occurrences_by_subject.get(subject_identity, ())
        ordered: list[_ReplayOccurrence] = []
        state_for_ordering: str | None = None
        index = 0
        while index < len(occurrences):
            instant = occurrences[index].occurrence.occurred_at
            group_end = index + 1
            while (
                group_end < len(occurrences)
                and occurrences[group_end].occurrence.occurred_at == instant
            ):
                group_end += 1
            group = _ordered_same_time_group(
                machine=machine,
                state=state_for_ordering,
                subject_occurrences=occurrences[index:group_end],
            )
            ordered.extend(group)
            for item in group:
                state_for_ordering = _evaluate(
                    machine=machine,
                    state=state_for_ordering,
                    trigger=item.trigger,
                ).state_after
            index = group_end

        state: str | None = None
        entered_by: EventOccurrence | None = None
        for item in ordered:
            occurrence = item.occurrence
            evaluation = _evaluate(
                machine=machine,
                state=state,
                trigger=item.trigger,
            )
            if evaluation.kind == "ignored_pre_inception":
                pre_inception_counts[f"{item.event_ref.path}#{occurrence.participant_name}"] += 1
            elif evaluation.kind in {"legal_inception", "legal_transition"}:
                if state is not None and entered_by is not None:
                    _append_interval(
                        history_rows,
                        subject_identity=subject_identity,
                        state=state,
                        entered_by=entered_by,
                        exited_by=occurrence,
                        window_start=start,
                        window_end=end,
                        final_status=final_status,
                    )
                entered_by = occurrence
            else:
                if state is None:
                    raise AssertionError("a replay violation requires established state")
                violation_kind: ViolationKind = (
                    "transition_from_terminal"
                    if evaluation.kind == "transition_from_terminal"
                    else "illegal_transition"
                )
                trace_rows.append(
                    {
                        "subject_identity": subject_identity,
                        "trigger_event_ref": occurrence.event_ref.key,
                        "trigger_event_identity": occurrence.event_identity,
                        "occurred_at": occurrence.occurred_at,
                        "model_state_at_event": state,
                        "violation_kind": violation_kind,
                    }
                )
            state = evaluation.state_after

        if state is None or entered_by is None:
            if coverage_complete:
                missing_inception_count += 1
            else:
                coverage_censored_subject_count += 1
            continue
        seeded_subject_count += 1
        _append_interval(
            history_rows,
            subject_identity=subject_identity,
            state=state,
            entered_by=entered_by,
            exited_by=None,
            window_start=start,
            window_end=end,
            final_status=final_status,
        )

    if missing_inception_count:
        raise InsufficientStateHistoryError(
            message=(
                "complete modeled Event history cannot establish an inception "
                "for part of the replay population"
            ),
            expected="one qualifying inception occurrence per population subject",
            received=f"subjects_without_inception={missing_inception_count}",
            location="session.lifecycle.replay.seed",
            repair=AnalysisRepair(
                kind="inspect",
                action=(
                    "Inspect the modeled inception Event history and StateModel "
                    "trigger definition before replaying."
                ),
                help_target=LiveHelpTarget(
                    surface="analysis",
                    canonical_id="lifecycle.replay",
                ),
            ),
        )

    state_ordinal = {state: index for index, state in enumerate(machine.state_order)}
    history_rows.sort(
        key=lambda row: (
            identity_sort_key(row["subject_identity"]),
            row["valid_from"],
            state_ordinal[row["model_state"]],
        )
    )
    trace_rows.sort(
        key=lambda row: (
            identity_sort_key(row["subject_identity"]),
            row["occurred_at"],
            row["trigger_event_ref"],
            identity_sort_key(row["trigger_event_identity"]),
        )
    )
    history = pd.DataFrame(history_rows, columns=HISTORY_COLUMNS)
    violations = pd.DataFrame(trace_rows, columns=TRACE_COLUMNS)
    if history.empty:
        history = _empty_history()
    else:
        history["valid_from"] = pd.to_datetime(history["valid_from"], utc=True)
        history["valid_to"] = pd.to_datetime(history["valid_to"], utc=True)
    if violations.empty:
        violations = _empty_trace()
    else:
        violations["occurred_at"] = pd.to_datetime(
            violations["occurred_at"],
            utc=True,
        )
    return ReplayResult(
        history=history,
        violations=violations,
        population_count=len(replay_population),
        seeded_subject_count=seeded_subject_count,
        coverage_censored_subject_count=coverage_censored_subject_count,
        interval_count=len(history),
        violation_count=len(violations),
        pre_inception_ignored_counts=pre_inception_counts,
    )


__all__ = []
