"""Pure time-to-event projection over canonical Event journey rows."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
import pandas as pd

from marivo.analysis.event import EventPattern, PatternStep
from marivo.analysis.intents._event_funnel import _identity_tuple, _validate_journey_rows

TIME_TO_EVENT_COLUMNS = (
    "journey_id",
    "subject_identity",
    "start_event_identity",
    "start_time",
    "end_event_identity",
    "end_time",
    "duration",
    "completion_status",
)


def _exact_step_index(pattern: EventPattern, step: PatternStep, *, argument: str) -> int:
    if type(step) is not PatternStep:
        raise TypeError(f"{argument} must be an exact PatternStep")
    matches = [
        index
        for index, candidate in enumerate(pattern.steps)
        if candidate.fingerprint == step.fingerprint
    ]
    if len(matches) != 1:
        raise ValueError(f"{argument} must occur exactly once in the persisted EventPattern")
    return matches[0]


def reduce_event_time_to_event(
    journey_rows: pd.DataFrame,
    *,
    pattern: EventPattern,
    start_step: PatternStep,
    end_step: PatternStep,
) -> pd.DataFrame:
    """Project persisted start/end assignments without querying or rematching Events."""

    start_index = _exact_step_index(pattern, start_step, argument="start_step")
    end_index = _exact_step_index(pattern, end_step, argument="end_step")
    if start_index >= end_index:
        raise ValueError("start_step must precede end_step in the persisted EventPattern")
    _validate_journey_rows(
        journey_rows,
        pattern=pattern,
        require_unique_subject=False,
    )
    if journey_rows.empty:
        return pd.DataFrame(columns=TIME_TO_EVENT_COLUMNS)

    records: list[dict[str, object]] = []
    for journey_id, journey in journey_rows.groupby("journey_id", dropna=False, sort=False):
        by_step = journey.set_index(journey["step_key"].astype(str), drop=False)
        start = by_step.loc[start_step.key]
        if pd.isna(start["occurred_at"]):
            continue
        end = by_step.loc[end_step.key]
        start_time = pd.Timestamp(start["occurred_at"])
        end_reached = not pd.isna(end["occurred_at"])
        end_time = pd.Timestamp(end["occurred_at"]) if end_reached else None
        duration = end_time - start_time if end_time is not None else None
        if duration is not None and duration < pd.Timedelta(0):
            raise ValueError("time-to-event duration cannot be negative")
        source_status = str(start["completion_status"])
        status = "complete" if end_reached else source_status
        if status not in {"complete", "incomplete", "coverage_censored"}:
            raise ValueError("time-to-event completion_status is invalid")
        records.append(
            {
                "journey_id": str(journey_id),
                "subject_identity": _identity_tuple(start["subject_identity"]),
                "start_event_identity": _identity_tuple(start["event_identity"]),
                "start_time": start_time,
                "end_event_identity": (
                    _identity_tuple(end["event_identity"]) if end_reached else None
                ),
                "end_time": end_time,
                "duration": duration,
                "completion_status": status,
            }
        )
    result = pd.DataFrame.from_records(records, columns=TIME_TO_EVENT_COLUMNS)
    if result["journey_id"].duplicated(keep=False).any():
        raise ValueError("time-to-event must emit at most one row per journey")
    return result.sort_values("journey_id", kind="stable", ignore_index=True)


__all__ = ["TIME_TO_EVENT_COLUMNS", "reduce_event_time_to_event"]
