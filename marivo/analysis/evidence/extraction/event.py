"""Extract one identity-safe observation from an Event Journey frame."""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from marivo.analysis.evidence.identity import make_finding_id
from marivo.analysis.evidence.types import (
    DerivationRule,
    EventFunnelObservationValue,
    EventFunnelStepObservation,
    EventJourneyObservationValue,
    EventSubject,
    EventTimeToEventObservationValue,
    Finding,
    ObservationFindingValue,
)


def extract_event_journey_finding(
    *,
    df: pd.DataFrame,
    artifact_id: str,
    session_id: str,
    subject: EventSubject,
    committed_at: datetime,
    unused_event_count: int,
    source_refs: tuple[str, ...],
) -> Finding:
    """Summarize journey outcomes without retaining row-level identities."""
    required = {"journey_id", "completion_status"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Event Journey evidence requires columns {sorted(missing)}")

    attempts = df.loc[:, ["journey_id", "completion_status"]].drop_duplicates()
    status_counts = attempts.groupby("journey_id", dropna=False)["completion_status"].nunique()
    if not status_counts.empty and int(status_counts.max()) != 1:
        raise ValueError("each journey_id must have exactly one completion_status")
    outcomes = attempts.drop_duplicates(subset=["journey_id"])
    counts = outcomes["completion_status"].value_counts()
    attempt_count = len(outcomes)
    value = EventJourneyObservationValue(
        attempt_count=attempt_count,
        complete_count=int(counts.get("complete", 0)),
        incomplete_count=int(counts.get("incomplete", 0)),
        coverage_censored_count=int(counts.get("coverage_censored", 0)),
        unused_event_count=unused_event_count,
    )
    canonical_item_key = "journey_outcomes"
    return Finding(
        finding_id=make_finding_id(artifact_id, "observation", canonical_item_key),
        finding_type="observation",
        epistemic_kind="observed",
        artifact_id=artifact_id,
        session_id=session_id,
        subject=subject,
        canonical_item_key=canonical_item_key,
        value=ObservationFindingValue(row_count=attempt_count, value=value),
        derivation=DerivationRule(
            rule_id="extract.event_journey",
            rule_version="v1",
            operator="events.match",
            source_fields=("journey_id", "completion_status"),
            source_finding_refs=(),
        ),
        source_refs=source_refs,
        committed_at=committed_at,
    )


def extract_event_funnel_finding(
    *,
    df: pd.DataFrame,
    artifact_id: str,
    session_id: str,
    subject: EventSubject,
    committed_at: datetime,
    step_order: tuple[str, ...],
    axis_columns: tuple[str, ...],
    reconciliation_passed: bool,
    source_unused_event_count: int,
    source_refs: tuple[str, ...],
) -> Finding:
    """Summarize funnel populations without retaining axis or subject values."""
    required = {
        "step_key",
        "cohort_count",
        "resolved_cohort_count",
        "resolved_entry_count",
        "reached_count",
        "lost_count",
        "coverage_censored_count",
        "conversion_from_first",
        "conversion_from_previous",
    }
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Event funnel evidence requires columns {sorted(missing)}")
    unknown_steps = set(df["step_key"].astype(str)) - set(step_order)
    if unknown_steps:
        raise ValueError(f"Event funnel contains unknown steps {sorted(unknown_steps)}")

    summaries: list[EventFunnelStepObservation] = []
    for step_key in step_order[:5]:
        step_rows = df.loc[df["step_key"].astype(str) == step_key]
        if step_rows.empty:
            raise ValueError(f"Event funnel is missing step {step_key!r}")
        reached_count = int(step_rows["reached_count"].sum())
        resolved_cohort = int(step_rows["resolved_cohort_count"].sum())
        previous_denominator = int(step_rows["resolved_entry_count"].sum())
        summaries.append(
            EventFunnelStepObservation(
                step_key=step_key,
                reached_count=reached_count,
                lost_count=int(step_rows["lost_count"].sum()),
                coverage_censored_count=int(step_rows["coverage_censored_count"].sum()),
                conversion_from_first=(
                    reached_count / resolved_cohort if resolved_cohort else None
                ),
                conversion_from_previous=(
                    reached_count / previous_denominator
                    if step_key != step_order[0] and previous_denominator
                    else None
                ),
            )
        )
    first_rows = df.loc[df["step_key"].astype(str) == step_order[0]]
    axis_tuple_count = (
        int(first_rows.loc[:, list(axis_columns)].drop_duplicates().shape[0]) if axis_columns else 1
    )
    value = EventFunnelObservationValue(
        cohort_count=int(first_rows["cohort_count"].sum()),
        step_count=len(step_order),
        axis_tuple_count=axis_tuple_count,
        source_unused_event_count=source_unused_event_count,
        grouped=bool(axis_columns),
        reconciliation_passed=reconciliation_passed,
        steps=tuple(summaries),
    )
    canonical_item_key = "funnel_outcomes"
    return Finding(
        finding_id=make_finding_id(artifact_id, "observation", canonical_item_key),
        finding_type="observation",
        epistemic_kind="observed",
        artifact_id=artifact_id,
        session_id=session_id,
        subject=subject,
        canonical_item_key=canonical_item_key,
        value=ObservationFindingValue(row_count=len(df), value=value),
        derivation=DerivationRule(
            rule_id="extract.event_funnel",
            rule_version="v1",
            operator="events.funnel",
            source_fields=tuple(sorted(required)),
            source_finding_refs=(),
        ),
        source_refs=source_refs,
        committed_at=committed_at,
    )


def extract_event_time_to_event_finding(
    *,
    df: pd.DataFrame,
    artifact_id: str,
    session_id: str,
    subject: EventSubject,
    committed_at: datetime,
    source_unused_end_count: int,
    source_refs: tuple[str, ...],
) -> Finding:
    """Summarize time-to-event outcomes without retaining journey identities."""
    required = {"completion_status", "duration"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"time-to-event evidence requires columns {sorted(missing)}")
    counts = df["completion_status"].astype(str).value_counts()
    unknown = set(counts.index) - {"complete", "incomplete", "coverage_censored"}
    if unknown:
        raise ValueError(f"time-to-event contains unknown statuses {sorted(unknown)}")
    durations = pd.to_timedelta(df["duration"], errors="coerce").dropna()
    duration_seconds = durations.dt.total_seconds()
    value = EventTimeToEventObservationValue(
        qualifying_count=len(df),
        complete_count=int(counts.get("complete", 0)),
        incomplete_count=int(counts.get("incomplete", 0)),
        coverage_censored_count=int(counts.get("coverage_censored", 0)),
        source_unused_end_count=source_unused_end_count,
        duration_count=len(duration_seconds),
        min_duration_seconds=(float(duration_seconds.min()) if len(duration_seconds) else None),
        median_duration_seconds=(
            float(duration_seconds.median()) if len(duration_seconds) else None
        ),
        max_duration_seconds=(float(duration_seconds.max()) if len(duration_seconds) else None),
    )
    canonical_item_key = "time_to_event_outcomes"
    return Finding(
        finding_id=make_finding_id(artifact_id, "observation", canonical_item_key),
        finding_type="observation",
        epistemic_kind="observed",
        artifact_id=artifact_id,
        session_id=session_id,
        subject=subject,
        canonical_item_key=canonical_item_key,
        value=ObservationFindingValue(row_count=len(df), value=value),
        derivation=DerivationRule(
            rule_id="extract.event_time_to_event",
            rule_version="v1",
            operator="events.time_to_event",
            source_fields=("completion_status", "duration"),
            source_finding_refs=(),
        ),
        source_refs=source_refs,
        committed_at=committed_at,
    )


__all__ = [
    "extract_event_funnel_finding",
    "extract_event_journey_finding",
    "extract_event_time_to_event_finding",
]
