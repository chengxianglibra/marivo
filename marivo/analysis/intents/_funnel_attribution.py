"""Pure additive ratio-mix decomposition of one funnel loss-rate delta."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from marivo.analysis.frames.attribution import FUNNEL_ATTRIBUTION_TOLERANCE


@dataclass(frozen=True)
class LossRateDecomposition:
    """One reconciled decomposition and its explicit pool denominators."""

    rows: pd.DataFrame
    total_delta: float
    contribution_sum: float
    positive_pool: float
    negative_pool: float
    residual: float


def decompose_loss_rate(
    *,
    components: pd.DataFrame,
    axis_columns: Sequence[str],
) -> LossRateDecomposition:
    """Decompose a loss-rate delta into additive loss and denominator-mix parts."""
    axes = tuple(axis_columns)
    current_lost = float(components["current_lost_count"].sum())
    current_entry = float(components["current_resolved_entry_count"].sum())
    baseline_lost = float(components["baseline_lost_count"].sum())
    baseline_entry = float(components["baseline_resolved_entry_count"].sum())
    if current_entry <= 0 or baseline_entry <= 0:
        raise ValueError(
            "funnel attribution requires positive resolved entry denominators on both sides"
        )

    rate_current = current_lost / current_entry
    rate_baseline = baseline_lost / baseline_entry
    total_delta = rate_current - rate_baseline

    loss = (
        components["current_lost_count"].astype("float64")
        - components["baseline_lost_count"].astype("float64")
    ) / current_entry
    denominator_mix = components["baseline_lost_count"].astype("float64") * (
        1.0 / current_entry - 1.0 / baseline_entry
    )

    frames: list[pd.DataFrame] = []
    for kind, series in (("loss", loss), ("denominator_mix", denominator_mix)):
        part = components[list(axes)].copy()
        part["contribution_kind"] = kind
        part["contribution"] = series.to_numpy()
        frames.append(part)
    rows = pd.concat(frames, ignore_index=True)

    contributions = rows["contribution"].to_numpy(dtype="float64")
    positive_pool = float(contributions[contributions > 0].sum())
    negative_pool = float(contributions[contributions < 0].sum())
    contribution_sum = float(contributions.sum())
    residual = total_delta - contribution_sum

    total_shares = np.full(contributions.shape, np.nan, dtype="float64")
    if total_delta != 0:
        total_shares = contributions / total_delta
    positive_shares = np.full(contributions.shape, np.nan, dtype="float64")
    if positive_pool != 0:
        positive_mask = contributions > 0
        positive_shares[positive_mask] = contributions[positive_mask] / positive_pool
    negative_shares = np.full(contributions.shape, np.nan, dtype="float64")
    if negative_pool != 0:
        negative_mask = contributions < 0
        negative_shares[negative_mask] = contributions[negative_mask] / negative_pool
    rows["share_of_total_delta"] = total_shares
    rows["share_of_positive_pool"] = positive_shares
    rows["share_of_negative_pool"] = negative_shares
    rows = rows.sort_values(
        by=[*axes, "contribution_kind"],
        kind="mergesort",
    ).reset_index(drop=True)

    if abs(residual) > FUNNEL_ATTRIBUTION_TOLERANCE:
        raise ValueError(
            "funnel attribution failed exact reconciliation; "
            f"residual={residual!r} total_delta={total_delta!r}"
        )
    return LossRateDecomposition(
        rows=rows,
        total_delta=total_delta,
        contribution_sum=contribution_sum,
        positive_pool=positive_pool,
        negative_pool=negative_pool,
        residual=residual,
    )


__all__ = ["LossRateDecomposition", "decompose_loss_rate"]
