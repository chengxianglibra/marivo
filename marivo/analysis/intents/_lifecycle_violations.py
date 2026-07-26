"""Pure projection of the validated committed Lifecycle replay trace."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from marivo.analysis.frames.lifecycle import LIFECYCLE_VIOLATIONS_COLUMNS
from marivo.analysis.intents._event_occurrences import identity_sort_key


@dataclass(frozen=True)
class LifecycleViolationReduction:
    """Sorted isolated copy of the private replay violation trace."""

    rows: pd.DataFrame
    violation_count: int


def reduce_lifecycle_violations(
    trace: pd.DataFrame,
) -> LifecycleViolationReduction:
    """Return a deterministic copy without exposing the frame-owned table."""

    rows = trace.copy(deep=True)
    if rows.empty:
        rows = pd.DataFrame(columns=LIFECYCLE_VIOLATIONS_COLUMNS)
        rows["occurred_at"] = pd.to_datetime(rows["occurred_at"], utc=True)
    else:
        records = rows.to_dict("records")
        records.sort(
            key=lambda row: (
                identity_sort_key(row["subject_identity"]),
                pd.Timestamp(row["occurred_at"]),
                str(row["trigger_event_ref"]),
                identity_sort_key(row["trigger_event_identity"]),
            )
        )
        rows = pd.DataFrame(records, columns=LIFECYCLE_VIOLATIONS_COLUMNS)
        rows["occurred_at"] = pd.to_datetime(rows["occurred_at"], utc=True)
    return LifecycleViolationReduction(rows=rows, violation_count=len(rows))


__all__ = []
