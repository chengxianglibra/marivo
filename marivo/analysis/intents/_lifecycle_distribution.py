"""Pure point-in-time Lifecycle distribution and reconciliation."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from marivo.analysis.errors import (
    AnalysisRepair,
    GroupedReconciliationFailedError,
)
from marivo.analysis.intents._event_occurrences import stable_digest
from marivo.introspection.live.model import LiveHelpTarget


@dataclass(frozen=True)
class LifecycleDistributionReduction:
    """Dense distribution rows and persisted exact reconciliation facts."""

    rows: pd.DataFrame
    known_subject_counts: dict[str, int]
    coverage_censored_subject_counts: dict[str, int]
    grouped_reconciliation_hash: str


def lifecycle_state_membership(
    history: pd.DataFrame,
    *,
    instants: tuple[tuple[str, pd.Timestamp], ...],
) -> pd.DataFrame:
    """Resolve one established state per subject and exact instant."""

    records: list[dict[str, object]] = []
    for canonical, instant in instants:
        current = history.loc[
            (pd.to_datetime(history["valid_from"], utc=True) <= instant)
            & (pd.to_datetime(history["valid_to"], utc=True) > instant)
            & (history["interval_status"] != "coverage_censored")
        ]
        if current["subject_identity"].duplicated(keep=False).any():
            raise ValueError(
                "Lifecycle history resolved multiple states for one subject at an instant"
            )
        records.extend(
            {
                "subject_identity": row["subject_identity"],
                "as_of": canonical,
                "model_state": row["model_state"],
            }
            for row in current.to_dict("records")
        )
    return pd.DataFrame(
        records,
        columns=("subject_identity", "as_of", "model_state"),
    )


def _group_key(value: object) -> object:
    try:
        if bool(pd.Series([value]).isna().iloc[0]):
            return None
    except (TypeError, ValueError):
        pass
    return value


def reduce_lifecycle_distribution(
    history: pd.DataFrame,
    *,
    instants: tuple[tuple[str, pd.Timestamp], ...],
    state_order: tuple[str, ...],
    population_count: int,
    axis_values: pd.DataFrame | None = None,
    axis_columns: tuple[str, ...] = (),
) -> LifecycleDistributionReduction:
    """Produce dense state distributions with exact grouped reconciliation."""

    membership = lifecycle_state_membership(history, instants=instants)
    known_subject_counts = {
        canonical: int((membership["as_of"] == canonical).sum()) for canonical, _instant in instants
    }
    coverage_censored_subject_counts = {
        canonical: population_count - known_subject_counts[canonical]
        for canonical, _instant in instants
    }
    ungrouped = {
        (canonical, state): int(
            ((membership["as_of"] == canonical) & (membership["model_state"] == state)).sum()
        )
        for canonical, _instant in instants
        for state in state_order
    }

    if axis_columns:
        if axis_values is None:
            raise ValueError("axis_values are required when axis_columns are declared")
        required = {"subject_identity", "as_of", *axis_columns}
        if set(axis_values.columns) != required:
            raise ValueError(
                "Lifecycle axis values must contain subject_identity, as_of, and exact axes"
            )
        enriched = membership.merge(
            axis_values,
            on=["subject_identity", "as_of"],
            how="left",
            validate="one_to_one",
            sort=False,
        )
    else:
        enriched = membership

    rows: list[dict[str, object]] = []
    grouped_totals = {
        (canonical, state): 0 for canonical, _instant in instants for state in state_order
    }
    for canonical, _instant in instants:
        instant_rows = enriched.loc[enriched["as_of"] == canonical]
        if axis_columns:
            group_values = tuple(
                dict.fromkeys(
                    tuple(_group_key(row[column]) for column in axis_columns)
                    for row in instant_rows.to_dict("records")
                )
            )
            if not group_values:
                group_values = (tuple(None for _ in axis_columns),)
        else:
            group_values = ((),)
        for group in group_values:
            if axis_columns:
                mask = pd.Series(True, index=instant_rows.index)
                for column, value in zip(axis_columns, group, strict=True):
                    mask &= (
                        instant_rows[column].isna()
                        if value is None
                        else instant_rows[column] == value
                    )
                group_rows = instant_rows.loc[mask]
            else:
                group_rows = instant_rows
            denominator = len(group_rows)
            for state in state_order:
                count = int((group_rows["model_state"] == state).sum())
                grouped_totals[(canonical, state)] += count
                rows.append(
                    {
                        **dict(zip(axis_columns, group, strict=True)),
                        "as_of": canonical,
                        "model_state": state,
                        "subject_count": count,
                        "share": count / denominator if denominator else None,
                    }
                )

    reconciliation = [
        {
            "as_of": canonical,
            "model_state": state,
            "ungrouped_count": ungrouped[(canonical, state)],
            "grouped_count": grouped_totals[(canonical, state)],
        }
        for canonical, _instant in instants
        for state in state_order
    ]
    mismatches = [
        item for item in reconciliation if item["ungrouped_count"] != item["grouped_count"]
    ]
    if mismatches:
        raise GroupedReconciliationFailedError(
            message="Lifecycle distribution axis groups do not reconcile to source state counts",
            expected="grouped subject_count sums equal ungrouped counts for every instant/state",
            received=repr(mismatches[:5]),
            location="session.lifecycle.distribution.axes",
            repair=AnalysisRepair(
                kind="inspect",
                action=(
                    "Inspect subject-axis ownership and temporal joins before "
                    "retrying distribution."
                ),
                help_target=LiveHelpTarget(
                    surface="analysis",
                    canonical_id="lifecycle.distribution",
                ),
            ),
        )
    columns = (*axis_columns, "as_of", "model_state", "subject_count", "share")
    return LifecycleDistributionReduction(
        rows=pd.DataFrame(rows, columns=columns),
        known_subject_counts=known_subject_counts,
        coverage_censored_subject_counts=coverage_censored_subject_counts,
        grouped_reconciliation_hash=stable_digest(reconciliation),
    )


__all__ = []
