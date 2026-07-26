"""Pure contracts for deterministic replay-based Lifecycle history."""

from __future__ import annotations

import pandas as pd
import pytest

from marivo.analysis.errors import (
    AmbiguousEventOrderError,
    InsufficientStateHistoryError,
)
from marivo.analysis.intents._event_occurrences import EventOccurrence
from marivo.analysis.intents._lifecycle_replay import replay_state_model
from marivo.datasource.ir import AiContextIR
from marivo.refs import ref
from marivo.semantic.ir import (
    LifecycleStateIR,
    SourceLocation,
    StateInceptionIR,
    StateModelIR,
    StateTransitionIR,
    StateTriggerIR,
)


def _trigger(event: str, role: str = "order") -> StateTriggerIR:
    return StateTriggerIR(event_ref=f"commerce.{event}", participant_role=role)


def _model(
    *,
    transitions: tuple[StateTransitionIR, ...] | None = None,
) -> StateModelIR:
    created = LifecycleStateIR(name="created", initial=True, terminal=False)
    paid = LifecycleStateIR(name="paid", initial=False, terminal=False)
    fulfilled = LifecycleStateIR(name="fulfilled", initial=False, terminal=True)
    cancelled = LifecycleStateIR(name="cancelled", initial=False, terminal=True)
    return StateModelIR(
        semantic_id="commerce.order_lifecycle",
        domain="commerce",
        name="order_lifecycle",
        subject="commerce.orders",
        states=(created, paid, fulfilled, cancelled),
        inceptions=(StateInceptionIR(trigger=_trigger("order_created")),),
        transitions=(
            transitions
            if transitions is not None
            else (
                StateTransitionIR(
                    from_state="created",
                    trigger=_trigger("payment_captured"),
                    to_state="paid",
                ),
                StateTransitionIR(
                    from_state="paid",
                    trigger=_trigger("order_fulfilled"),
                    to_state="fulfilled",
                ),
                StateTransitionIR(
                    from_state="created",
                    trigger=_trigger("order_cancelled"),
                    to_state="cancelled",
                ),
                StateTransitionIR(
                    from_state="paid",
                    trigger=_trigger("order_cancelled"),
                    to_state="cancelled",
                ),
            )
        ),
        ai_context=AiContextIR(business_definition="Order lifecycle."),
        python_symbol="order_lifecycle",
        location=SourceLocation(file="/project/models/orders.py", line=1),
    )


def _occurrence(
    event: str,
    event_id: str,
    subject_id: str,
    occurred_at: str,
    *,
    role: str = "order",
) -> EventOccurrence:
    event_ref = ref.event(f"commerce.{event}")
    return EventOccurrence(
        event_ref=event_ref,
        participant_name=role,
        event_identity=(event_id,),
        subject_identity=(subject_id,),
        occurred_at=pd.Timestamp(occurred_at),
    )


def _streams(
    *items: EventOccurrence,
) -> dict[tuple[object, str], tuple[EventOccurrence, ...]]:
    grouped: dict[tuple[object, str], list[EventOccurrence]] = {}
    for item in items:
        grouped.setdefault((item.event_ref, item.participant_name), []).append(item)
    return {
        key: tuple(
            sorted(
                values,
                key=lambda item: (item.occurred_at, item.event_identity),
            )
        )
        for key, values in grouped.items()
    }


def test_replay_clips_pre_window_seed_and_records_fixed_violations() -> None:
    streams = _streams(
        _occurrence("payment_captured", "pre", "o1", "2026-06-29T00:00:00Z"),
        _occurrence("order_created", "created", "o1", "2026-06-30T00:00:00Z"),
        _occurrence("order_created", "pre-window-again", "o1", "2026-06-30T12:00:00Z"),
        _occurrence("order_fulfilled", "early", "o1", "2026-07-01T12:00:00Z"),
        _occurrence("payment_captured", "paid", "o1", "2026-07-02T00:00:00Z"),
        _occurrence("order_created", "again", "o1", "2026-07-02T12:00:00Z"),
        _occurrence("order_fulfilled", "done", "o1", "2026-07-03T00:00:00Z"),
        _occurrence("order_created", "terminal", "o1", "2026-07-04T00:00:00Z"),
    )

    result = replay_state_model(
        model=_model(),
        occurrence_sets=streams,
        window_start=pd.Timestamp("2026-07-01T00:00:00Z"),
        window_end=pd.Timestamp("2026-07-05T00:00:00Z"),
        coverage_complete=True,
    )

    history = result.history
    assert history["model_state"].tolist() == ["created", "paid", "fulfilled"]
    assert history["valid_from"].tolist() == [
        pd.Timestamp("2026-07-01T00:00:00Z"),
        pd.Timestamp("2026-07-02T00:00:00Z"),
        pd.Timestamp("2026-07-03T00:00:00Z"),
    ]
    assert history["valid_to"].tolist() == [
        pd.Timestamp("2026-07-02T00:00:00Z"),
        pd.Timestamp("2026-07-03T00:00:00Z"),
        pd.Timestamp("2026-07-05T00:00:00Z"),
    ]
    assert history["interval_status"].tolist() == [
        "completed",
        "completed",
        "right_censored",
    ]
    assert history.iloc[0]["entered_by_event_identity"] == ("created",)
    assert result.violations["violation_kind"].tolist() == [
        "illegal_transition",
        "illegal_transition",
        "illegal_transition",
        "transition_from_terminal",
    ]
    assert result.pre_inception_ignored_counts["commerce.payment_captured#order"] == 1
    assert set(result.pre_inception_ignored_counts) == {
        "commerce.order_created#order",
        "commerce.payment_captured#order",
        "commerce.order_fulfilled#order",
        "commerce.order_cancelled#order",
    }
    assert result.population_count == 1
    assert result.seeded_subject_count == 1
    assert result.coverage_censored_subject_count == 0
    assert result.interval_count == 3
    assert result.violation_count == 4


def test_same_event_occurrences_use_declared_identity_order() -> None:
    trigger = _trigger("advanced")
    model = _model(
        transitions=(
            StateTransitionIR(
                from_state="created",
                trigger=trigger,
                to_state="paid",
            ),
            StateTransitionIR(
                from_state="paid",
                trigger=trigger,
                to_state="fulfilled",
            ),
        )
    )
    streams = _streams(
        _occurrence("order_created", "seed", "o1", "2026-06-30T00:00:00Z"),
        _occurrence("advanced", "b", "o1", "2026-07-02T00:00:00Z"),
        _occurrence("advanced", "a", "o1", "2026-07-02T00:00:00Z"),
    )

    result = replay_state_model(
        model=model,
        occurrence_sets=streams,
        window_start=pd.Timestamp("2026-07-01T00:00:00Z"),
        window_end=pd.Timestamp("2026-07-03T00:00:00Z"),
        coverage_complete=True,
    )

    assert result.history["model_state"].tolist() == ["created", "fulfilled"]
    assert result.history.iloc[0]["exited_by_event_identity"] == ("a",)
    assert result.history.iloc[1]["entered_by_event_identity"] == ("b",)
    assert result.violations.empty


def test_cross_event_same_time_fails_only_when_outcome_depends_on_order() -> None:
    streams = _streams(
        _occurrence("order_created", "seed", "o1", "2026-06-30T00:00:00Z"),
        _occurrence("payment_captured", "paid", "o1", "2026-07-02T00:00:00Z"),
        _occurrence("order_cancelled", "cancelled", "o1", "2026-07-02T00:00:00Z"),
    )

    with pytest.raises(AmbiguousEventOrderError) as exc_info:
        replay_state_model(
            model=_model(),
            occurrence_sets=streams,
            window_start=pd.Timestamp("2026-07-01T00:00:00Z"),
            window_end=pd.Timestamp("2026-07-03T00:00:00Z"),
            coverage_complete=True,
        )

    error = exc_info.value
    assert error.kind == "ambiguous_event_order"
    assert error.expected
    assert error.received
    assert error.location == "session.lifecycle.replay.occurrence_order"
    assert error.repair is not None
    assert error.repair.help_target.canonical_id == "lifecycle.replay"


def test_cross_event_same_time_with_equivalent_illegal_outcomes_is_stable() -> None:
    streams = _streams(
        _occurrence("order_created", "seed", "o1", "2026-06-30T00:00:00Z"),
        _occurrence("order_fulfilled", "early", "o1", "2026-07-02T00:00:00Z"),
        _occurrence("unknown_modeled", "other", "o1", "2026-07-02T00:00:00Z"),
    )
    model = _model(
        transitions=(
            StateTransitionIR(
                from_state="paid",
                trigger=_trigger("order_fulfilled"),
                to_state="fulfilled",
            ),
            StateTransitionIR(
                from_state="paid",
                trigger=_trigger("unknown_modeled"),
                to_state="fulfilled",
            ),
        )
    )

    result = replay_state_model(
        model=model,
        occurrence_sets=streams,
        window_start=pd.Timestamp("2026-07-01T00:00:00Z"),
        window_end=pd.Timestamp("2026-07-03T00:00:00Z"),
        coverage_complete=True,
    )

    assert result.history["model_state"].tolist() == ["created"]
    assert result.violations["violation_kind"].tolist() == [
        "illegal_transition",
        "illegal_transition",
    ]


def test_same_event_identity_with_distinct_roles_has_no_invented_role_order() -> None:
    event_ref = ref.event("commerce.assignment_changed")
    model = _model(
        transitions=(
            StateTransitionIR(
                from_state="created",
                trigger=_trigger("assignment_changed", role="buyer"),
                to_state="paid",
            ),
            StateTransitionIR(
                from_state="created",
                trigger=_trigger("assignment_changed", role="recipient"),
                to_state="cancelled",
            ),
        )
    )
    shared = {
        (ref.event("commerce.order_created"), "order"): (
            _occurrence("order_created", "seed", "o1", "2026-06-30T00:00:00Z"),
        ),
        (event_ref, "buyer"): (
            _occurrence(
                "assignment_changed",
                "changed",
                "o1",
                "2026-07-02T00:00:00Z",
                role="buyer",
            ),
        ),
        (event_ref, "recipient"): (
            _occurrence(
                "assignment_changed",
                "changed",
                "o1",
                "2026-07-02T00:00:00Z",
                role="recipient",
            ),
        ),
    }

    with pytest.raises(AmbiguousEventOrderError):
        replay_state_model(
            model=model,
            occurrence_sets=shared,
            window_start=pd.Timestamp("2026-07-01T00:00:00Z"),
            window_end=pd.Timestamp("2026-07-03T00:00:00Z"),
            coverage_complete=True,
        )


def test_missing_inception_is_error_only_when_history_is_complete() -> None:
    streams = _streams(
        _occurrence("payment_captured", "paid", "o1", "2026-07-02T00:00:00Z"),
    )

    with pytest.raises(InsufficientStateHistoryError) as exc_info:
        replay_state_model(
            model=_model(),
            occurrence_sets=streams,
            window_start=pd.Timestamp("2026-07-01T00:00:00Z"),
            window_end=pd.Timestamp("2026-07-03T00:00:00Z"),
            coverage_complete=True,
        )

    error = exc_info.value
    assert error.kind == "insufficient_state_history"
    assert error.expected
    assert error.received == "subjects_without_inception=1"
    assert error.location == "session.lifecycle.replay.seed"
    assert error.repair is not None
    assert error.repair.help_target.canonical_id == "lifecycle.replay"

    censored = replay_state_model(
        model=_model(),
        occurrence_sets=streams,
        window_start=pd.Timestamp("2026-07-01T00:00:00Z"),
        window_end=pd.Timestamp("2026-07-03T00:00:00Z"),
        coverage_complete=False,
    )
    assert censored.history.empty
    assert censored.violations.empty
    assert censored.population_count == 1
    assert censored.seeded_subject_count == 0
    assert censored.coverage_censored_subject_count == 1


def test_explicit_population_includes_subjects_without_modeled_occurrences() -> None:
    result = replay_state_model(
        model=_model(),
        occurrence_sets={},
        window_start=pd.Timestamp("2026-07-01T00:00:00Z"),
        window_end=pd.Timestamp("2026-07-03T00:00:00Z"),
        coverage_complete=False,
        population=(("o2",), ("o1",)),
    )

    assert result.population_count == 2
    assert result.seeded_subject_count == 0
    assert result.coverage_censored_subject_count == 2
    assert tuple(result.history.columns) == (
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
    assert tuple(result.violations.columns) == (
        "subject_identity",
        "trigger_event_ref",
        "trigger_event_identity",
        "occurred_at",
        "model_state_at_event",
        "violation_kind",
    )


def test_unknown_coverage_marks_final_seeded_interval_coverage_censored() -> None:
    result = replay_state_model(
        model=_model(),
        occurrence_sets=_streams(
            _occurrence("order_created", "seed", "o1", "2026-06-30T00:00:00Z"),
        ),
        window_start=pd.Timestamp("2026-07-01T00:00:00Z"),
        window_end=pd.Timestamp("2026-07-03T00:00:00Z"),
        coverage_complete=False,
    )

    assert result.history["interval_status"].tolist() == ["coverage_censored"]
    assert result.seeded_subject_count == 1
    assert result.coverage_censored_subject_count == 0
