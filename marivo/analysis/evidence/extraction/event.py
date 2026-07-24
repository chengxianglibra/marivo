"""Extract one identity-safe observation from an Event Journey frame."""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from marivo.analysis.evidence.identity import make_finding_id
from marivo.analysis.evidence.types import (
    DerivationRule,
    EventJourneyObservationValue,
    EventSubject,
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


__all__ = ["extract_event_journey_finding"]
