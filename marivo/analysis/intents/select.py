"""CandidateSet.select - one closed typed value for a ranked candidate row."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
import json
from typing import Any, cast

import pandas as pd

from marivo.analysis.errors import SemanticKindMismatchError
from marivo.analysis.frames.candidate import (
    CandidateSelection,
    CandidateSet,
    CrossSectionalOutlierSelection,
    DriverAxisSelection,
    PeriodShiftSelection,
    PointAnomalySelection,
    SliceSelection,
    WindowSelection,
)
from marivo.analysis.windows import AbsoluteWindow
from marivo.refs import DimensionKind, Ref
from marivo.refs import ref as ref_factory


def select(candidate_set: CandidateSet, *, rank: int = 1) -> CandidateSelection:
    """Return the shape-specific immutable value at a 1-indexed candidate rank."""
    if not isinstance(candidate_set, CandidateSet):
        raise SemanticKindMismatchError(
            message="select requires a CandidateSet input",
            context={
                "expected_kind": "candidate_set",
                "got_kind": type(candidate_set).__name__,
            },
        )
    row_count = len(candidate_set._df)
    if rank < 1 or rank > row_count:
        raise SemanticKindMismatchError(
            message=f"select rank {rank} is out of range",
            context={"row_count": row_count, "requested_rank": rank},
        )

    row = candidate_set._df.iloc[rank - 1]
    common: dict[str, Any] = {
        "candidate_ref": str(row["item_id"]),
        "source_artifact_ref": candidate_set.meta.source_ref,
        "rank": rank,
        "score": float(row["score"]),
        "reason_codes": tuple(_json_list(row["reason_codes_json"])),
    }
    shape = candidate_set.meta.shape
    if shape == "point_anomaly":
        return PointAnomalySelection(
            **common,
            window=_optional_window(row, "window_start", "window_end"),
            keys=_selector(row, "keys_json"),
            direction=str(row["direction"]),
            observed_value=float(row["observed_value"]),
            baseline_value=float(row["baseline_value"]),
            delta=float(row["delta"]),
        )
    if shape == "period_shift":
        return PeriodShiftSelection(
            **common,
            window=_required_window(row, "window_start", "window_end", shape),
            baseline_window=_required_window(
                row, "baseline_window_start", "baseline_window_end", shape
            ),
            keys=_selector(row, "keys_json"),
            direction=str(row["direction"]),
        )
    if shape == "driver_axis":
        semantic_id = row.get("axis_semantic_id")
        axis: Ref[DimensionKind] | str
        if isinstance(semantic_id, str) and semantic_id:
            axis = ref_factory.dimension(semantic_id)
        else:
            axis = str(row["axis"])
        return DriverAxisSelection(**common, axis=axis)
    if shape == "slice":
        return SliceSelection(
            **common,
            selector=_selector(row, "selector_json", required=True),
            window=_optional_window(row, "window_start", "window_end"),
        )
    if shape == "window":
        return WindowSelection(
            **common,
            window=_required_window(row, "window_start", "window_end", shape),
            keys=_selector(row, "keys_json"),
        )
    if shape == "cross_sectional_outlier":
        return CrossSectionalOutlierSelection(
            **common,
            keys=_selector(row, "keys_json", required=True),
            direction=str(row["direction"]),
            peer_scope=tuple(_json_list(row["peer_scope_json"])),
        )
    raise AssertionError(f"unhandled CandidateSet shape {shape!r}")


def _json_list(raw: object) -> list[str]:
    if not isinstance(raw, str) or not raw:
        return []
    decoded = json.loads(raw)
    return [str(value) for value in decoded] if isinstance(decoded, list) else []


def _selector(
    row: pd.Series, column: str, *, required: bool = False
) -> dict[Ref[DimensionKind] | str, str | int | float | bool | None]:
    raw = row[column]
    if not isinstance(raw, str) or not raw:
        if required:
            raise SemanticKindMismatchError(
                message=f"candidate row has no {column}",
                context={"shape": column, "selector_column": column},
            )
        return {}
    decoded = cast("dict[str, str | int | float | bool | None]", json.loads(raw))
    return {_selector_key(name): value for name, value in decoded.items()}


def _selector_key(name: str) -> Ref[DimensionKind] | str:
    if name.count(".") >= 2:
        return ref_factory.dimension(name)
    return name


def _optional_window(row: pd.Series, start: str, end: str) -> AbsoluteWindow | None:
    if pd.isna(row[start]) or pd.isna(row[end]):
        return None
    return _absolute_window(row[start], row[end])


def _required_window(row: pd.Series, start: str, end: str, shape: str) -> AbsoluteWindow:
    window = _optional_window(row, start, end)
    if window is None:
        raise SemanticKindMismatchError(
            message=f"CandidateSet[{shape}] row has no required window",
            context={"shape": shape, "window_columns": [start, end]},
        )
    return window


def _absolute_window(start_value: Any, end_value: Any) -> AbsoluteWindow:
    return AbsoluteWindow(
        start=pd.Timestamp(start_value).isoformat(),
        end=pd.Timestamp(end_value).isoformat(),
    )


__all__ = ["select"]
