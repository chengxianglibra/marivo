"""Pure reduction from canonical Event journey rows to funnel rows."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

import pandas as pd

from marivo.analysis.errors import GroupedReconciliationFailedError
from marivo.analysis.event import EventPattern, _event_repair

JOURNEY_COLUMNS = (
    "journey_id",
    "completion_status",
    "subject_identity",
    "step_key",
    "event_identity",
    "occurred_at",
    "elapsed_from_start",
    "elapsed_from_previous",
)

FUNNEL_ADDITIVE_COLUMNS = (
    "cohort_count",
    "resolved_cohort_count",
    "entry_count",
    "resolved_entry_count",
    "reached_count",
    "lost_count",
    "coverage_censored_count",
)

FUNNEL_RATE_COLUMNS = (
    "conversion_from_first",
    "conversion_from_previous",
    "loss_rate_from_previous",
)

FUNNEL_COLUMNS = (
    "step_key",
    "cohort_count",
    "resolved_cohort_count",
    "entry_count",
    "resolved_entry_count",
    "reached_count",
    "lost_count",
    *FUNNEL_RATE_COLUMNS,
    "coverage_censored_count",
)


@dataclass(frozen=True)
class FunnelReduction:
    """One deterministic funnel reduction and its reconciliation evidence."""

    rows: pd.DataFrame
    ungrouped_rows: pd.DataFrame
    additive_columns: tuple[str, ...]
    ungrouped_hash: str
    grouped_hash: str


def _identity_tuple(value: object) -> tuple[object, ...]:
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return tuple(value)
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        converted = tolist()
        if isinstance(converted, list):
            return tuple(converted)
    raise ValueError("subject_identity must be a tuple-valued identity")


def _is_missing(value: object) -> bool:
    try:
        missing = pd.isna(cast("Any", value))
    except (TypeError, ValueError):
        return False
    return bool(missing) if isinstance(missing, bool) else False


def _validate_journey_rows(
    rows: pd.DataFrame,
    *,
    pattern: EventPattern,
    require_unique_subject: bool,
    event_coverage_complete: Mapping[str, bool] | None = None,
) -> pd.DataFrame:
    """Validate and return one normalized subject-step state table."""

    if type(pattern) is not EventPattern:
        raise TypeError("funnel reduction requires an exact EventPattern")
    missing_columns = tuple(column for column in JOURNEY_COLUMNS if column not in rows.columns)
    if missing_columns:
        raise ValueError(f"journey rows are missing required columns: {missing_columns!r}")

    step_keys = tuple(step.key for step in pattern.steps)
    step_order = {key: index for index, key in enumerate(step_keys)}
    if not step_keys:
        raise ValueError("funnel reduction requires at least one PatternStep")
    if event_coverage_complete is not None:
        expected_events = {step.event.path for step in pattern.steps}
        if set(event_coverage_complete) != expected_events:
            raise ValueError(
                "event_coverage_complete must reference exactly the Event inputs in pattern"
            )
        if any(type(value) is not bool for value in event_coverage_complete.values()):
            raise TypeError("event_coverage_complete values must be exact booleans")
    observed_keys = set(rows["step_key"].dropna().astype(str))
    unknown_keys = observed_keys - set(step_keys)
    if unknown_keys:
        raise ValueError(f"journey rows contain unknown step keys: {sorted(unknown_keys)!r}")
    if rows.empty:
        return pd.DataFrame(
            columns=(
                "journey_id",
                "subject_identity",
                "completion_status",
                "step_key",
                "step_order",
                "reached",
                "unknown",
            )
        )

    states: list[dict[str, object]] = []
    seen_subjects: set[tuple[object, ...]] = set()
    for journey_id, journey in rows.groupby("journey_id", dropna=False, sort=False):
        if _is_missing(journey_id):
            raise ValueError("journey_id cannot be null")
        if len(journey) != len(step_keys):
            raise ValueError("every journey must contain exactly one row per PatternStep")
        if journey["step_key"].duplicated(keep=False).any():
            raise ValueError("every journey must contain exactly one row per PatternStep")
        if set(journey["step_key"].astype(str)) != set(step_keys):
            raise ValueError("every journey must contain the complete persisted PatternStep set")
        if journey["subject_identity"].map(_identity_tuple).nunique(dropna=False) != 1:
            raise ValueError("one journey cannot contain multiple subject identities")
        subject_identity = _identity_tuple(journey["subject_identity"].iloc[0])
        if not subject_identity or any(_is_missing(value) for value in subject_identity):
            raise ValueError("subject_identity components must be non-null")
        if require_unique_subject and subject_identity in seen_subjects:
            raise ValueError("first-per-subject funnel requires at most one journey per subject")
        seen_subjects.add(subject_identity)

        statuses = tuple(journey["completion_status"].drop_duplicates())
        if len(statuses) != 1 or statuses[0] not in {
            "complete",
            "incomplete",
            "coverage_censored",
        }:
            raise ValueError("one journey must retain one valid completion_status")
        status = str(statuses[0])
        ordered = journey.assign(
            __step_order=journey["step_key"].astype(str).map(step_order)
        ).sort_values("__step_order", kind="stable")
        reached = ordered["occurred_at"].notna().tolist()
        identities_present = ordered["event_identity"].map(lambda value: not _is_missing(value))
        if identities_present.tolist() != reached:
            raise ValueError("event_identity and occurred_at nullability must agree")
        missing_seen = False
        for present in reached:
            if not present:
                missing_seen = True
            elif missing_seen:
                raise ValueError("journey occurrence assignment must be a reached prefix")
        if not reached[0]:
            raise ValueError("every canonical journey must reach its first PatternStep")
        if status == "complete" and not all(reached):
            raise ValueError("complete journey must reach every PatternStep")
        if status != "complete" and all(reached):
            raise ValueError("non-complete journey must retain a missing PatternStep")

        for step, present in zip(pattern.steps, reached, strict=True):
            unknown = (
                status == "coverage_censored"
                if event_coverage_complete is None
                else not event_coverage_complete[step.event.path]
            )
            states.append(
                {
                    "journey_id": str(journey_id),
                    "subject_identity": subject_identity,
                    "completion_status": status,
                    "step_key": step.key,
                    "step_order": step_order[step.key],
                    "reached": present,
                    "unknown": unknown and not present,
                }
            )
    return pd.DataFrame.from_records(states)


def _safe_ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def _reduce_group(
    states: pd.DataFrame,
    *,
    step_keys: tuple[str, ...],
) -> list[dict[str, object]]:
    cohort_count = int(states["subject_identity"].nunique(dropna=False))
    rows: list[dict[str, object]] = []
    for index, step_key in enumerate(step_keys):
        current = states.loc[states["step_key"] == step_key]
        reached_count = int(current["reached"].sum())
        if index == 0:
            resolved_cohort_count = cohort_count
            entry_count = cohort_count
            resolved_entry_count = cohort_count
            lost_count = 0
            censored_count = 0
            conversion_from_previous = None
            loss_rate_from_previous = None
        else:
            previous = states.loc[states["step_key"] == step_keys[index - 1]]
            entered_subjects = set(
                previous.loc[previous["reached"], "subject_identity"].map(_identity_tuple)
            )
            entry_count = len(entered_subjects)
            censored_count = int(
                (
                    current["subject_identity"].map(_identity_tuple).isin(entered_subjects)
                    & current["unknown"]
                ).sum()
            )
            resolved_entry_count = entry_count - censored_count
            lost_count = resolved_entry_count - reached_count
            resolved_cohort_count = cohort_count - int(current["unknown"].sum())
            conversion_from_previous = _safe_ratio(reached_count, resolved_entry_count)
            loss_rate_from_previous = _safe_ratio(lost_count, resolved_entry_count)
        rows.append(
            {
                "step_key": step_key,
                "cohort_count": cohort_count,
                "resolved_cohort_count": resolved_cohort_count,
                "entry_count": entry_count,
                "resolved_entry_count": resolved_entry_count,
                "reached_count": reached_count,
                "lost_count": lost_count,
                "conversion_from_first": _safe_ratio(
                    reached_count,
                    resolved_cohort_count,
                ),
                "conversion_from_previous": conversion_from_previous,
                "loss_rate_from_previous": loss_rate_from_previous,
                "coverage_censored_count": censored_count,
            }
        )
    return rows


def _attach_axes(
    states: pd.DataFrame,
    *,
    axis_values: pd.DataFrame,
    axis_columns: tuple[str, ...],
) -> pd.DataFrame:
    required = ("subject_identity", *axis_columns)
    missing = tuple(column for column in required if column not in axis_values.columns)
    if missing:
        raise ValueError(f"subject axis values are missing required columns: {missing!r}")
    normalized = axis_values.loc[:, list(required)].copy()
    normalized["subject_identity"] = normalized["subject_identity"].map(_identity_tuple)
    if normalized["subject_identity"].duplicated(keep=False).any():
        raise ValueError("subject axis enrichment must produce exactly one row per subject")
    expected = set(states["subject_identity"].map(_identity_tuple))
    received = set(normalized["subject_identity"])
    if received != expected:
        raise ValueError("subject axis enrichment must cover exactly the journey subjects")
    return states.merge(
        normalized,
        on="subject_identity",
        how="left",
        validate="many_to_one",
        sort=False,
    )


def funnel_reconciliation_hash(
    rows: pd.DataFrame,
    *,
    step_keys: tuple[str, ...],
) -> str:
    """Hash exact additive funnel totals in retained PatternStep order."""

    by_step = rows.set_index("step_key")
    payload = [
        {
            "step_key": step_key,
            **{
                column: int(cast("Any", by_step.loc[step_key, column]))
                for column in FUNNEL_ADDITIVE_COLUMNS
            },
        }
        for step_key in step_keys
    ]
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _reconcile_grouped(
    grouped_rows: pd.DataFrame,
    ungrouped_rows: pd.DataFrame,
    *,
    step_keys: tuple[str, ...],
) -> tuple[str, str]:
    grouped_totals = (
        grouped_rows.groupby("step_key", dropna=False, sort=False)[list(FUNNEL_ADDITIVE_COLUMNS)]
        .sum()
        .reindex(step_keys, fill_value=0)
        .reset_index()
    )
    ungrouped_hash = funnel_reconciliation_hash(
        ungrouped_rows,
        step_keys=step_keys,
    )
    grouped_hash = funnel_reconciliation_hash(
        grouped_totals,
        step_keys=step_keys,
    )
    if grouped_hash != ungrouped_hash:
        raise GroupedReconciliationFailedError(
            message="grouped funnel additive counts do not reconcile to ungrouped totals",
            expected=ungrouped_hash,
            received=grouped_hash,
            location="session.events.funnel.grouped_reconciliation",
            repair=_event_repair(
                kind="inspect",
                action="Inspect subject-axis cardinality and grouped additive counts.",
                help_target="events.funnel",
            ),
        )
    return ungrouped_hash, grouped_hash


def reduce_event_funnel(
    journey_rows: pd.DataFrame,
    *,
    pattern: EventPattern,
    event_coverage_complete: Mapping[str, bool],
    axis_values: pd.DataFrame | None = None,
    axis_columns: tuple[str, ...] = (),
) -> FunnelReduction:
    """Reduce dense canonical journey rows without querying or rematching Events."""

    if len(set(axis_columns)) != len(axis_columns):
        raise ValueError("funnel axis output columns must be unique")
    if axis_values is None and axis_columns:
        raise ValueError("axis_values are required when axis_columns are declared")
    if axis_values is not None and not axis_columns:
        raise ValueError("axis_columns are required when axis_values are provided")

    states = _validate_journey_rows(
        journey_rows,
        pattern=pattern,
        require_unique_subject=True,
        event_coverage_complete=event_coverage_complete,
    )
    step_keys = tuple(step.key for step in pattern.steps)
    ungrouped_rows = pd.DataFrame.from_records(
        _reduce_group(states, step_keys=step_keys),
        columns=FUNNEL_COLUMNS,
    )
    if not axis_columns:
        digest = funnel_reconciliation_hash(
            ungrouped_rows,
            step_keys=step_keys,
        )
        return FunnelReduction(
            rows=ungrouped_rows.copy(deep=True),
            ungrouped_rows=ungrouped_rows,
            additive_columns=FUNNEL_ADDITIVE_COLUMNS,
            ungrouped_hash=digest,
            grouped_hash=digest,
        )

    assert axis_values is not None
    grouped_states = _attach_axes(
        states,
        axis_values=axis_values,
        axis_columns=axis_columns,
    )
    records: list[dict[str, object]] = []
    group_key: str | list[str] = axis_columns[0] if len(axis_columns) == 1 else list(axis_columns)
    for raw_axis_values, group in grouped_states.groupby(
        group_key,
        dropna=False,
        sort=False,
    ):
        values = raw_axis_values if isinstance(raw_axis_values, tuple) else (raw_axis_values,)
        axis_payload = dict(zip(axis_columns, values, strict=True))
        records.extend({**axis_payload, **row} for row in _reduce_group(group, step_keys=step_keys))
    grouped_rows = pd.DataFrame.from_records(
        records,
        columns=(
            *axis_columns,
            *FUNNEL_COLUMNS,
        ),
    )
    ungrouped_hash, grouped_hash = _reconcile_grouped(
        grouped_rows,
        ungrouped_rows,
        step_keys=step_keys,
    )
    return FunnelReduction(
        rows=grouped_rows,
        ungrouped_rows=ungrouped_rows,
        additive_columns=FUNNEL_ADDITIVE_COLUMNS,
        ungrouped_hash=ungrouped_hash,
        grouped_hash=grouped_hash,
    )


__all__ = [
    "FUNNEL_ADDITIVE_COLUMNS",
    "FUNNEL_COLUMNS",
    "FUNNEL_RATE_COLUMNS",
    "FunnelReduction",
    "funnel_reconciliation_hash",
    "reduce_event_funnel",
]
