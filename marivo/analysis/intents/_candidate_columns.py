"""Union-of-columns layout for CandidateSet rows.

Single source of truth for column order, dtypes, per-shape required /
optional columns, row construction, and shape-level validation.
"""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
import json
from collections.abc import Iterable
from typing import Any, cast

import pandas as pd

from marivo.analysis.errors import FrameMetaInvalidError
from marivo.analysis.followups import _parse_item_followups
from marivo.analysis.frames.candidate import CandidateShape

CANDIDATE_COLUMNS: list[str] = [
    "item_id",
    "score",
    "direction",
    "reason_codes_json",
    "source_refs_json",
    "selector_json",
    "keys_json",
    "window_start",
    "window_end",
    "baseline_window_start",
    "baseline_window_end",
    "axis",
    "peer_scope_json",
    "recommended_followups_json",
]
CANDIDATE_DTYPES: dict[str, str] = {
    "item_id": "string",
    "score": "float64",
    "direction": "string",
    "reason_codes_json": "string",
    "source_refs_json": "string",
    "selector_json": "string",
    "keys_json": "string",
    "window_start": "datetime64[ns, UTC]",
    "window_end": "datetime64[ns, UTC]",
    "baseline_window_start": "datetime64[ns, UTC]",
    "baseline_window_end": "datetime64[ns, UTC]",
    "axis": "string",
    "peer_scope_json": "string",
    "recommended_followups_json": "string",
}

_COMMON_REQUIRED: set[str] = {
    "item_id",
    "score",
    "reason_codes_json",
    "source_refs_json",
    "recommended_followups_json",
}

REQUIRED_COLUMNS_BY_SHAPE: dict[CandidateShape, set[str]] = {
    "point_anomaly": _COMMON_REQUIRED | {"window_start", "window_end", "direction"},
    "period_shift": _COMMON_REQUIRED
    | {
        "window_start",
        "window_end",
        "baseline_window_start",
        "baseline_window_end",
        "direction",
    },
    "driver_axis": _COMMON_REQUIRED | {"axis"},
    "slice": _COMMON_REQUIRED | {"selector_json", "keys_json"},
    "window": _COMMON_REQUIRED | {"window_start", "window_end"},
    "cross_sectional_outlier": _COMMON_REQUIRED | {"keys_json", "direction"},
}

ALLOWED_OPTIONAL_COLUMNS_BY_SHAPE: dict[CandidateShape, set[str]] = {
    "point_anomaly": {"keys_json", "baseline_window_start", "baseline_window_end"},
    "period_shift": {"keys_json"},
    "driver_axis": set(),
    "slice": {"window_start", "window_end"},
    "window": {"keys_json"},
    "cross_sectional_outlier": {"peer_scope_json"},
}

_JSON_DEFAULTS: dict[str, str] = {
    "reason_codes_json": "[]",
    "source_refs_json": "[]",
    "selector_json": "",
    "keys_json": "",
    "peer_scope_json": "",
    "recommended_followups_json": "[]",
}


def empty_candidate_frame() -> pd.DataFrame:
    return pd.DataFrame({col: pd.Series(dtype=CANDIDATE_DTYPES[col]) for col in CANDIDATE_COLUMNS})


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _to_utc_datetime(raw: Any) -> pd.Timestamp:
    ts = pd.Timestamp(raw)
    if ts.tz is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _row_to_record(row: dict[str, Any]) -> dict[str, Any]:
    record: dict[str, Any] = dict.fromkeys(CANDIDATE_COLUMNS)
    for col, default in _JSON_DEFAULTS.items():
        record[col] = default

    if "item_id" in row:
        record["item_id"] = str(row["item_id"])
    if "score" in row:
        record["score"] = float(row["score"])
    if "direction" in row and row["direction"] is not None:
        record["direction"] = str(row["direction"])
    if "axis" in row and row["axis"] is not None:
        record["axis"] = str(row["axis"])

    if "reason_codes" in row:
        record["reason_codes_json"] = _json_dumps(list(row["reason_codes"]))
    if "source_refs" in row:
        record["source_refs_json"] = _json_dumps(list(row["source_refs"]))
    if "selector" in row:
        record["selector_json"] = _json_dumps(dict(row["selector"]))
    if "keys" in row:
        record["keys_json"] = _json_dumps(dict(row["keys"]))
    if "peer_scope" in row:
        record["peer_scope_json"] = _json_dumps(list(row["peer_scope"]))
    if "recommended_followups" in row:
        record["recommended_followups_json"] = _json_dumps(list(row["recommended_followups"]))

    if "window" in row and row["window"] is not None:
        window = row["window"]
        record["window_start"] = _to_utc_datetime(window["start"])
        record["window_end"] = _to_utc_datetime(window["end"])
    if "baseline_window" in row and row["baseline_window"] is not None:
        baseline = row["baseline_window"]
        record["baseline_window_start"] = _to_utc_datetime(baseline["start"])
        record["baseline_window_end"] = _to_utc_datetime(baseline["end"])

    return record


def build_union_columns(shape: CandidateShape, rows: Iterable[dict[str, Any]]) -> pd.DataFrame:
    """Materialize candidate rows into the canonical union-of-columns DataFrame."""

    records = [_row_to_record(row) for row in rows]
    if not records:
        return empty_candidate_frame()
    df = pd.DataFrame(records, columns=CANDIDATE_COLUMNS)
    return df.astype(CANDIDATE_DTYPES)


def _is_neutral(column: str, value: Any) -> bool:
    """True when *value* in *column* indicates the row did not populate the field.

    - datetime / float columns: pd.NA / NaT / NaN is neutral.
    - nullable string columns (direction, axis): pd.NA / None is neutral.
    - JSON columns whose default is "" (selector_json, keys_json,
      peer_scope_json): empty string is neutral.
    - JSON columns whose default is "[]" (reason_codes_json,
      source_refs_json, recommended_followups_json): considered populated,
      since an empty array is a valid value distinct from "missing".
    """

    dtype = CANDIDATE_DTYPES[column]
    if dtype.startswith("datetime") or dtype == "float64":
        return bool(pd.isna(value))
    # nullable "string" dtype — guard against pd.NA before any comparison.
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    if column in _JSON_DEFAULTS:
        return bool(value == "" and _JSON_DEFAULTS[column] == "")
    return bool(value == "")


def validate_shape_columns(shape: CandidateShape, df: pd.DataFrame) -> None:
    """Enforce per-shape required / optional column matrix on a candidate frame."""

    required = REQUIRED_COLUMNS_BY_SHAPE[shape]
    allowed_optional = ALLOWED_OPTIONAL_COLUMNS_BY_SHAPE[shape]
    permitted = required | allowed_optional

    for column in CANDIDATE_COLUMNS:
        if column in required:
            for index, value in df[column].items():
                if _is_neutral(column, value):
                    raise FrameMetaInvalidError(
                        message=(
                            f"candidate row {index} missing required {shape!r} column {column!r}"
                        ),
                        details={
                            "kind": "CandidateRowSchemaInvalid",
                            "shape": shape,
                            "column": column,
                            "row_index": int(cast("Any", index)),
                            "reason": "required",
                        },
                    )
            continue

        if column in permitted:
            continue

        for index, value in df[column].items():
            if not _is_neutral(column, value):
                raise FrameMetaInvalidError(
                    message=(
                        f"candidate row {index} has unexpected value in column {column!r} "
                        f"for shape {shape!r}"
                    ),
                    details={
                        "kind": "CandidateRowSchemaInvalid",
                        "shape": shape,
                        "column": column,
                        "row_index": int(cast("Any", index)),
                        "reason": "unexpected",
                    },
                )

    for index, raw in df["recommended_followups_json"].items():
        try:
            _parse_item_followups(raw if isinstance(raw, str) else None)
        except FrameMetaInvalidError:
            raise
        except Exception as exc:
            raise FrameMetaInvalidError(
                message=(f"candidate row {index} has invalid recommended_followups_json"),
                details={
                    "kind": "ItemFollowupShapeInvalid",
                    "row_index": int(cast("Any", index)),
                    "shape": shape,
                    "raw": raw,
                },
            ) from exc
