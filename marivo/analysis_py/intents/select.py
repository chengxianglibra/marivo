"""mv.select - typed read of one CandidateSet row's field."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
import json
from typing import Any, Literal

import pandas as pd

from marivo.analysis_py.errors import SemanticKindMismatchError
from marivo.analysis_py.followups import _parse_item_followups
from marivo.analysis_py.frames.candidate import CandidateSet, CandidateShape
from marivo.analysis_py.refs import DimensionRef
from marivo.analysis_py.windows import AbsoluteWindow

SelectField = Literal[
    "axis",
    "selector",
    "window",
    "baseline_window",
    "direction",
    "score",
    "item_id",
    "recommended_followups",
]

_ALWAYS_AVAILABLE: set[str] = {"item_id", "score", "recommended_followups"}

_FIELD_BY_SHAPE: dict[CandidateShape, set[str]] = {
    "point_anomaly": _ALWAYS_AVAILABLE | {"window", "direction"},
    "period_shift": _ALWAYS_AVAILABLE | {"window", "baseline_window", "direction"},
    "driver_axis": _ALWAYS_AVAILABLE | {"axis"},
    "slice": _ALWAYS_AVAILABLE | {"selector", "window", "direction"},
    "window": _ALWAYS_AVAILABLE | {"window", "direction"},
    "cross_sectional_outlier": _ALWAYS_AVAILABLE | {"direction"},
}


def select(
    candidate_set: CandidateSet,
    *,
    rank: int = 1,
    field: SelectField | str,
) -> Any:
    """Read one typed field from a single rank of a CandidateSet.

    Each candidate ``shape`` exposes a different field set (e.g. ``axis`` is
    only available on ``driver_axis``). Dot-paths into ``selector`` / ``keys``
    (``"selector.country"``) are supported.

    Args:
        candidate_set: A CandidateSet returned by ``mv.discover``.
        rank: 1-indexed rank of the row to read. Must be in
            ``[1, candidate_set.meta.row_count]``.
        field: One of the canonical fields (``axis``, ``selector``, ``window``,
            ``baseline_window``, ``direction``, ``score``, ``item_id``,
            ``recommended_followups``) — or a dot-path under ``selector`` /
            ``keys``. Only fields valid for the candidate's shape are accepted.

    Returns:
        The typed value: ``DimensionRef`` for ``axis``, ``AbsoluteWindow`` for
        ``window`` / ``baseline_window``, ``dict[DimensionRef, Any]`` for
        ``selector``, otherwise a primitive (``float`` / ``str`` / ``list``).

    Raises:
        SemanticKindMismatchError: ``candidate_set`` is not a CandidateSet, ``rank``
            is out of range, or ``field`` is not available for the candidate shape.

    Example:
        >>> candidates = mv.discover(series, objective="point_anomalies", threshold=1.0)
        >>> mv.select(candidates, rank=1, field="window")
    """
    if not isinstance(candidate_set, CandidateSet):
        raise SemanticKindMismatchError(
            message="select requires a CandidateSet input",
            details={
                "expected_kind": "candidate_set",
                "got_kind": type(candidate_set).__name__,
            },
        )

    row_count = len(candidate_set._df)
    if rank < 1 or rank > row_count:
        raise SemanticKindMismatchError(
            message=f"select rank {rank} is out of range",
            details={
                "row_count": row_count,
                "requested_rank": rank,
            },
        )
    row = candidate_set._df.iloc[rank - 1]
    shape = candidate_set.meta.shape

    base_field, _, sub_field = field.partition(".")
    if sub_field:
        if base_field not in {"keys", "selector"}:
            raise SemanticKindMismatchError(
                message=f"select dot-path field {field!r} is not supported",
                details={"shape": shape, "field": field},
            )
        return _select_dot_path(row, shape, base_field, sub_field)

    if base_field not in _FIELD_BY_SHAPE.get(shape, set()):
        raise SemanticKindMismatchError(
            message=f"select field {field!r} is not available for shape {shape!r}",
            details={
                "shape": shape,
                "field": field,
                "valid_fields": sorted(_FIELD_BY_SHAPE.get(shape, set())),
            },
        )

    if base_field == "axis":
        return DimensionRef(id=str(row["axis"]))
    if base_field == "selector":
        raw = row["selector_json"]
        if not raw:
            raise SemanticKindMismatchError(
                message="select(field='selector') row has empty selector_json",
                details={"shape": shape, "field": field},
            )
        decoded = json.loads(raw)
        return {DimensionRef(id=name): value for name, value in decoded.items()}
    if base_field == "window":
        return _absolute_window(row["window_start"], row["window_end"])
    if base_field == "baseline_window":
        return _absolute_window(row["baseline_window_start"], row["baseline_window_end"])
    if base_field == "direction":
        return None if pd.isna(row["direction"]) else str(row["direction"])
    if base_field == "score":
        return float(row["score"])
    if base_field == "item_id":
        return str(row["item_id"])
    if base_field == "recommended_followups":
        return _parse_item_followups(
            row["recommended_followups_json"]
            if isinstance(row["recommended_followups_json"], str)
            else None
        )
    raise SemanticKindMismatchError(
        message=f"select field {field!r} is not recognized",
        details={"shape": shape, "field": field},
    )


def _select_dot_path(row: pd.Series, shape: CandidateShape, base_field: str, key: str) -> Any:
    column = "selector_json" if base_field == "selector" else "keys_json"
    raw = row[column]
    if not raw:
        raise SemanticKindMismatchError(
            message=f"select(field='{base_field}.{key}') row has empty {column}",
            details={"shape": shape, "field": f"{base_field}.{key}"},
        )
    decoded = json.loads(raw)
    if key not in decoded:
        raise SemanticKindMismatchError(
            message=f"select(field='{base_field}.{key}') key not present in row",
            details={
                "shape": shape,
                "field": f"{base_field}.{key}",
                "available_keys": sorted(decoded.keys()),
            },
        )
    return decoded[key]


def _absolute_window(start_value: Any, end_value: Any) -> AbsoluteWindow:
    return AbsoluteWindow(
        start=pd.Timestamp(start_value).isoformat(),
        end=pd.Timestamp(end_value).isoformat(),
    )
