"""Pure alignment of two compatible funnel reductions into delta rows."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import pandas as pd

from marivo.analysis.frames.delta import FUNNEL_DELTA_COLUMNS
from marivo.analysis.intents._event_funnel import FUNNEL_ADDITIVE_COLUMNS

_RATE_COLUMN = "loss_rate_from_previous"


@dataclass(frozen=True)
class FunnelDelta:
    """One deterministic funnel comparison and its alignment receipt."""

    rows: pd.DataFrame
    axis_columns: tuple[str, ...]
    aligned_step_keys: tuple[str, ...]
    zero_filled_tuple_count: int
    alignment_kind: Literal["step_key_and_axis_tuple"] = "step_key_and_axis_tuple"


def build_funnel_delta(
    *,
    current: pd.DataFrame,
    baseline: pd.DataFrame,
    axis_columns: Sequence[str],
    step_order: Sequence[str],
) -> FunnelDelta:
    """Align two funnels on PatternStep identity plus the exact axis tuple."""
    axes = tuple(axis_columns)
    keys = [*axes, "step_key"]
    merged = current.merge(
        baseline,
        how="outer",
        on=keys,
        suffixes=("__current", "__baseline"),
        indicator=True,
    )
    one_sided = merged["_merge"] != "both"
    zero_filled = (
        int(merged.loc[one_sided, list(axes)].drop_duplicates().shape[0])
        if axes
        else int(one_sided.any())
    )

    output = pd.DataFrame(index=merged.index)
    for column in axes:
        output[column] = merged[column]
    output["step_key"] = merged["step_key"]

    for column in FUNNEL_ADDITIVE_COLUMNS:
        for side in ("current", "baseline"):
            output[f"{side}_{column}"] = merged[f"{column}__{side}"].fillna(0).astype("int64")

    for side in ("current", "baseline"):
        output[f"{side}_{_RATE_COLUMN}"] = merged[f"{_RATE_COLUMN}__{side}"].astype("float64")

    output[f"{_RATE_COLUMN}_delta"] = (
        output[f"current_{_RATE_COLUMN}"] - output[f"baseline_{_RATE_COLUMN}"]
    )

    step_rank = {key: index for index, key in enumerate(step_order)}
    output["__step_rank"] = output["step_key"].map(step_rank)
    output = (
        output.sort_values(by=[*axes, "__step_rank"], kind="mergesort")
        .drop(columns="__step_rank")
        .reset_index(drop=True)
    )
    output = output[[*axes, *FUNNEL_DELTA_COLUMNS]]
    return FunnelDelta(
        rows=output,
        axis_columns=axes,
        aligned_step_keys=tuple(step_order),
        zero_filled_tuple_count=zero_filled,
    )


__all__ = ["FunnelDelta", "build_funnel_delta"]
