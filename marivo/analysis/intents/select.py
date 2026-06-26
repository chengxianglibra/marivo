"""CandidateSet.select - typed read of one CandidateSet row's field."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
import json
from typing import Any, Literal

import pandas as pd

from marivo.analysis.errors import SemanticKindMismatchError
from marivo.analysis.frames.candidate import CandidateSet, CandidateShape
from marivo.analysis.windows import AbsoluteWindow
from marivo.refs import SemanticRef
from marivo.semantic.catalog import SemanticKind
from marivo.semantic.refs import DimensionRef, make_ref

SelectField = Literal[
    "axis",
    "selector",
    "window",
    "baseline_window",
    "direction",
    "score",
    "observed_value",
    "baseline_value",
    "delta",
    "item_id",
    "affordances",
]

_ALWAYS_AVAILABLE: set[str] = {"item_id", "score", "affordances"}

_FIELD_BY_SHAPE: dict[CandidateShape, set[str]] = {
    "point_anomaly": _ALWAYS_AVAILABLE
    | {"window", "direction", "observed_value", "baseline_value", "delta"},
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
    attribute: SelectField | str,
) -> Any:
    """Read one typed attribute from a single rank of a CandidateSet.

    When to use: extract a typed value from a CandidateSet row returned by discover.

    Each candidate ``shape`` exposes a different attribute set (e.g. ``axis`` is
    only available on ``driver_axis``). Dot-paths into ``selector`` / ``keys``
    (``"selector.country"``) are supported.

    Args:
        candidate_set: A CandidateSet returned by ``session.discover``.
        rank: 1-indexed rank of the row to read. Must be in
            ``[1, candidate_set.meta.row_count]``.
        attribute: One of the canonical attributes (``axis``, ``selector``, ``window``,
            ``baseline_window``, ``direction``, ``score``, ``item_id``,
            ``affordances``) — or a dot-path under ``selector`` /
            ``keys``. Only attributes valid for the candidate's shape are accepted.

    Returns:
        The typed value: ``SemanticRef`` for ``axis``, ``AbsoluteWindow`` for
        ``window`` / ``baseline_window``, ``dict[SemanticRef, Any]`` for
        ``selector``, otherwise a primitive (``float`` / ``str`` / ``list``).

    Raises:
        SemanticKindMismatchError: ``candidate_set`` is not a CandidateSet, ``rank``
            is out of range, or ``attribute`` is not available for the candidate shape.

    Example:
        >>> candidates = session.discover.point_anomalies(series, threshold=1.0)
        >>> candidates.select(rank=1, attribute="window")
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

    base_attr, _, sub_attr = attribute.partition(".")
    if sub_attr:
        if base_attr not in {"keys", "selector"}:
            raise SemanticKindMismatchError(
                message=f"select dot-path attribute {attribute!r} is not supported",
                details={"shape": shape, "attribute": attribute},
            )
        return _select_dot_path(row, shape, base_attr, sub_attr)

    if base_attr not in _FIELD_BY_SHAPE.get(shape, set()):
        raise SemanticKindMismatchError(
            message=f"select attribute {attribute!r} is not available for shape {shape!r}",
            details={
                "shape": shape,
                "attribute": attribute,
                "valid_fields": sorted(_FIELD_BY_SHAPE.get(shape, set())),
            },
        )

    if base_attr == "axis":
        semantic_id = row.get("axis_semantic_id")
        if isinstance(semantic_id, str) and semantic_id:
            return make_ref(semantic_id, SemanticKind.DIMENSION)
        return str(row["axis"])
    if base_attr == "selector":
        raw = row["selector_json"]
        if not raw:
            raise SemanticKindMismatchError(
                message="select(attribute='selector') row has empty selector_json",
                details={"shape": shape, "attribute": attribute},
            )
        decoded = json.loads(raw)
        return {_selector_key(name): value for name, value in decoded.items()}
    if base_attr == "window":
        return _absolute_window(row["window_start"], row["window_end"])
    if base_attr == "baseline_window":
        return _absolute_window(row["baseline_window_start"], row["baseline_window_end"])
    if base_attr == "direction":
        return None if pd.isna(row["direction"]) else str(row["direction"])
    if base_attr == "score":
        return float(row["score"])
    if base_attr == "observed_value":
        return None if pd.isna(row["observed_value"]) else float(row["observed_value"])
    if base_attr == "baseline_value":
        return None if pd.isna(row["baseline_value"]) else float(row["baseline_value"])
    if base_attr == "delta":
        return None if pd.isna(row["delta"]) else float(row["delta"])
    if base_attr == "item_id":
        return str(row["item_id"])
    if base_attr == "affordances":
        from marivo.analysis.frames.base import ArtifactAffordance

        raw = row["affordances_json"] if isinstance(row["affordances_json"], str) else "[]"
        return [ArtifactAffordance.model_validate(entry) for entry in json.loads(raw)]
    raise SemanticKindMismatchError(
        message=f"select attribute {attribute!r} is not recognized",
        details={"shape": shape, "attribute": attribute},
    )


def _select_dot_path(row: pd.Series, shape: CandidateShape, base_attr: str, key: str) -> Any:
    column = "selector_json" if base_attr == "selector" else "keys_json"
    raw = row[column]
    if not raw:
        raise SemanticKindMismatchError(
            message=f"select(attribute='{base_attr}.{key}') row has empty {column}",
            details={"shape": shape, "attribute": f"{base_attr}.{key}"},
        )
    decoded = json.loads(raw)
    if key not in decoded:
        matched_key = next(
            (
                candidate
                for candidate in decoded
                if isinstance(candidate, str) and candidate.rsplit(".", 1)[-1] == key
            ),
            None,
        )
        if matched_key is not None:
            return decoded[matched_key]
        raise SemanticKindMismatchError(
            message=f"select(attribute='{base_attr}.{key}') key not present in row",
            details={
                "shape": shape,
                "attribute": f"{base_attr}.{key}",
                "available_keys": sorted(decoded.keys()),
            },
        )
    return decoded[key]


def _selector_key(name: str) -> SemanticRef | str:
    if name.count(".") >= 2:
        return DimensionRef(name)
    return name


def _absolute_window(start_value: Any, end_value: Any) -> AbsoluteWindow:
    return AbsoluteWindow(
        start=pd.Timestamp(start_value).isoformat(),
        end=pd.Timestamp(end_value).isoformat(),
    )
