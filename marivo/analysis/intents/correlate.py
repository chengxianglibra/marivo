"""Correlate MetricFrames into AssociationResults."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
import hashlib
import json
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from math import isclose
from time import monotonic
from typing import Any, Literal, cast

import pandas as pd
from pandas.api.types import is_datetime64_any_dtype, is_numeric_dtype, is_object_dtype

from marivo.analysis._semantic_persistence import job_semantics_from_frames
from marivo.analysis.errors import AlignmentFailedError, SemanticKindMismatchError
from marivo.analysis.evidence.pipeline import (
    CommitInputs,
    CommitParams,
    CommitSemanticAnchors,
    commit_result,
)
from marivo.analysis.evidence.types import Subject
from marivo.analysis.frames.association import AssociationResult, AssociationResultMeta
from marivo.analysis.frames.metric import MetricFrame
from marivo.analysis.intents._derived import (
    compose_lineage,
    ensure_frame_in_session,
    require_numeric_column,
    resolve_session,
)
from marivo.analysis.intents._validate import require_single_metric
from marivo.analysis.lineage import LineageStep
from marivo.analysis.policies import AlignmentPolicy
from marivo.analysis.session._runtime import persist_job_record, register_frame_artifact
from marivo.analysis.session.core import Session, ensure_session_writable


def _gen_ref(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(4)}"


def _params_digest(params: dict[str, Any]) -> str:
    body = json.dumps(params, sort_keys=True, default=str).encode("utf-8")
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


@dataclass(frozen=True)
class _LagResult:
    correlation: float
    aligned_row_count: int
    dropped_row_count: int


def correlate(
    a: MetricFrame,
    b: MetricFrame,
    *,
    measure_a: str | None = None,
    measure_b: str | None = None,
    alignment: AlignmentPolicy | None = None,
    method: Literal["pearson", "spearman", "kendall"] = "pearson",
    lag_range: range | Sequence[int] | None = None,
    analysis_purpose: str | None = None,
    session: Session | None = None,
) -> AssociationResult:
    session = resolve_session(session)
    ensure_session_writable(session)
    if not isinstance(a, MetricFrame) or not isinstance(b, MetricFrame):
        raise SemanticKindMismatchError(message="correlate requires MetricFrame inputs")
    require_single_metric(a, intent="correlate")
    require_single_metric(b, intent="correlate")
    # correlate operates on arity-1 metric frames; multi-metric frames are gated
    # out upstream. Narrow metric_id for downstream AssociationResultMeta.
    assert a.meta.metric_id is not None
    assert b.meta.metric_id is not None
    ensure_frame_in_session(a, session=session, label="correlate a")
    ensure_frame_in_session(b, session=session, label="correlate b")
    if alignment is None:
        alignment = AlignmentPolicy(kind="window_bucket")
    if not isinstance(alignment, AlignmentPolicy):
        raise SemanticKindMismatchError(
            message="correlate requires alignment=AlignmentPolicy(...)",
            context={
                "expected_kind": "AlignmentPolicy",
                "got_kind": type(alignment).__name__,
            },
        )
    if alignment.kind != "window_bucket":
        raise SemanticKindMismatchError(
            message="correlate only supports AlignmentPolicy(kind='window_bucket')",
            context={"alignment": alignment.model_dump(mode="json")},
        )
    if alignment.mode != "ordinal_bucket" or alignment.strict_lengths:
        raise SemanticKindMismatchError(
            message="correlate only supports default window_bucket alignment",
            context={"alignment": alignment.model_dump(mode="json")},
        )
    if a.meta.semantic_kind != b.meta.semantic_kind:
        raise SemanticKindMismatchError(
            message="correlate requires matching semantic_kind",
            context={"a": a.meta.semantic_kind, "b": b.meta.semantic_kind},
        )
    if method not in {"pearson", "spearman", "kendall"}:
        raise SemanticKindMismatchError(message=f"unsupported correlation method {method!r}")
    lags = _resolve_lags(lag_range)
    if any(lag != 0 for lag in lags) and a.meta.semantic_kind not in {
        "time_series",
        "panel",
    }:
        raise SemanticKindMismatchError(
            message="non-zero lag_range requires time_series or panel MetricFrames",
            context={"semantic_kind": a.meta.semantic_kind, "lags": lags},
        )
    lag_mode = "single" if lag_range is None else "range"

    started_at = datetime.now(UTC)
    started = monotonic()
    a_df = a._dataframe_copy()
    b_df = b._dataframe_copy()
    a_value = require_numeric_column(a_df, measure_a, purpose="correlate a")
    b_value = require_numeric_column(b_df, measure_b, purpose="correlate b")
    alignment_keys = _alignment_keys(
        a,
        b,
        a_df=a_df,
        b_df=b_df,
        a_value=a_value,
        b_value=b_value,
    )
    aligned, alignment_keys = _align(
        a_df,
        b_df,
        a_value=a_value,
        b_value=b_value,
        keys=alignment_keys,
    )
    series_keys = _lag_series_keys(a, b, alignment_keys=alignment_keys, lags=lags)
    sort_keys = [*series_keys, *[key for key in alignment_keys if key not in series_keys]]
    if sort_keys:
        aligned = aligned.sort_values(sort_keys, kind="stable").reset_index(drop=True)
    driver_field = ",".join(alignment_keys) or None
    if len(aligned) < 2:
        raise AlignmentFailedError(
            message=f"alignment '{alignment.kind}' produced fewer than two rows"
        )
    if aligned["value_a"].nunique(dropna=True) < 2 or aligned["value_b"].nunique(dropna=True) < 2:
        raise AlignmentFailedError(message=f"{method} correlation is undefined for constant input")

    lag_results = _lag_results_by_lag(
        aligned,
        lags=lags,
        method=method,
        group_keys=series_keys,
    )
    finite = {
        lag: result.correlation
        for lag, result in lag_results.items()
        if pd.notna(result.correlation)
    }
    if not finite:
        raise AlignmentFailedError(message=f"{method} correlation produced NaN for every lag")
    best_lag = _select_best_lag(finite)
    best_correlation = finite[best_lag]

    alignment_dump = alignment.model_dump(mode="json")
    output = pd.DataFrame(
        {
            "metric_id_a": [a.meta.metric_id] * len(lags),
            "metric_id_b": [b.meta.metric_id] * len(lags),
            "semantic_model_a": [a.meta.semantic_model] * len(lags),
            "semantic_model_b": [b.meta.semantic_model] * len(lags),
            "semantic_kind": [a.meta.semantic_kind] * len(lags),
            "method": [method] * len(lags),
            "alignment_kind": [alignment.kind] * len(lags),
            "lag_mode": [lag_mode] * len(lags),
            "lag_offset": list(lags),
            "driver_field": [driver_field] * len(lags),
            "value_column_a": [a_value] * len(lags),
            "value_column_b": [b_value] * len(lags),
            "input_row_count_a": [len(a_df)] * len(lags),
            "input_row_count_b": [len(b_df)] * len(lags),
            "aligned_row_count": [lag_results[lag].aligned_row_count for lag in lags],
            "dropped_row_count": [lag_results[lag].dropped_row_count for lag in lags],
            "correlation": [lag_results[lag].correlation for lag in lags],
        }
    )
    params = {
        "source_a_ref": a.ref,
        "source_b_ref": b.ref,
        "measure_a": a_value,
        "measure_b": b_value,
        "alignment": alignment_dump,
        "method": method,
        "lags": list(lags),
    }
    frame_ref = _gen_ref("frame")
    job_ref = _gen_ref("job")
    finished_at = datetime.now(UTC)
    source_refs = [a.ref, b.ref]
    lag_policy = (
        {"mode": "single", "offset": 0}
        if lag_range is None
        else {"mode": "range", "lags": list(lags)}
    )
    meta = AssociationResultMeta(
        kind="association_result",
        ref=frame_ref,
        session_id=session.id,
        project_root=str(session.project_root),
        produced_by_job=job_ref,
        analysis_purpose=analysis_purpose,
        created_at=finished_at,
        row_count=len(output),
        byte_size=0,
        lineage=compose_lineage(
            [a, b],
            step=LineageStep(
                intent="correlate",
                job_ref=job_ref,
                inputs=source_refs,
                params_digest=_params_digest(params),
                analysis_purpose=analysis_purpose,
            ),
        ),
        source_refs=source_refs,
        metric_ids=[a.meta.metric_id, b.meta.metric_id],
        semantic_kinds=[a.meta.semantic_kind, b.meta.semantic_kind],
        semantic_models=[a.meta.semantic_model, b.meta.semantic_model],
        method=method,
        alignment=alignment_dump,
        lag_policy=lag_policy,
        aligned_row_count=lag_results[best_lag].aligned_row_count,
        dropped_row_count=lag_results[best_lag].dropped_row_count,
        correlation=best_correlation,
        best_lag=best_lag,
    )
    result = AssociationResult(_df=output, meta=meta)
    left_subject = {"metric": a.meta.metric_id}
    right_subject = {"metric": b.meta.metric_id}
    result = cast(
        "AssociationResult",
        commit_result(
            store=session._evidence_store(),
            frames_dir=session._layout.frames_dir,
            frame=result,
            step_type="correlate",
            inputs=CommitInputs(
                input_refs=[a.meta.artifact_id or a.ref, b.meta.artifact_id or b.ref]
            ),
            params=CommitParams(values=params),
            semantic_anchors=CommitSemanticAnchors.from_frames(a, b),
            subject=Subject(analysis_axis="correlation"),
            extractor_family="association_result",
            seeding_context={
                "left_subject": left_subject,
                "right_subject": right_subject,
                "aligned_window": a.meta.window or b.meta.window or {"basis": alignment.kind},
            },
        ),
    )
    register_frame_artifact(session, result)
    persist_job_record(
        session,
        {
            "id": job_ref,
            "session_id": session.id,
            "intent": "correlate",
            **job_semantics_from_frames(a, b),
            "analysis_purpose": analysis_purpose,
            "params": params,
            "input_frame_refs": source_refs,
            "output_frame_ref": result.meta.artifact_id or frame_ref,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_ms": int((monotonic() - started) * 1000),
            "status": "succeeded",
            "error": None,
            "semantic_project_root": str(session.catalog._project.semantic_root),
            "semantic_models": [a.meta.semantic_model, b.meta.semantic_model],
        },
    )
    return result


def _looks_like_datetime(series: pd.Series) -> bool:
    """Return True if an object-dtype Series contains date or datetime values."""
    non_null = series.dropna()
    if non_null.empty:
        return False
    first_valid = non_null.iloc[0]
    return isinstance(first_valid, (date, pd.Timestamp))


def _normalize_key_dtypes(
    left: pd.DataFrame, right: pd.DataFrame, keys: list[str]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Coerce merge-key columns to a common dtype when they differ."""
    for key in keys:
        left_dtype = left[key].dtype
        right_dtype = right[key].dtype
        if left_dtype == right_dtype:
            continue
        left_is_dt = is_datetime64_any_dtype(left_dtype)
        right_is_dt = is_datetime64_any_dtype(right_dtype)
        left_is_obj = is_object_dtype(left_dtype)
        right_is_obj = is_object_dtype(right_dtype)
        # If one side is datetime64 and the other is object that looks like dates,
        # normalize both to datetime64.
        if (left_is_dt and right_is_obj and _looks_like_datetime(right[key])) or (
            right_is_dt and left_is_obj and _looks_like_datetime(left[key])
        ):
            left[key] = pd.to_datetime(left[key])
            right[key] = pd.to_datetime(right[key])
    return left, right


def _resolve_lags(lag_range: range | Sequence[int] | None) -> list[int]:
    """Return the sorted, de-duplicated signed lags to evaluate."""
    if lag_range is None:
        return [0]
    return sorted({int(lag) for lag in lag_range})


def _select_best_lag(correlations: dict[int, float]) -> int:
    """Select strongest absolute correlation, preferring the closest lag on ties."""
    strongest = max(abs(value) for value in correlations.values())
    tied = [
        lag
        for lag, value in correlations.items()
        if isclose(abs(value), strongest, rel_tol=1e-12, abs_tol=1e-12)
    ]
    return min(tied, key=lambda lag: (abs(lag), lag))


def _time_axis_column(frame: MetricFrame) -> str | None:
    axes = frame.meta.axes
    time_axis = axes.get("time")
    if isinstance(time_axis, dict):
        column = time_axis.get("column") or time_axis.get("field")
        if isinstance(column, str) and column:
            return column
    for axis in axes.values():
        if not isinstance(axis, dict) or axis.get("role") != "time":
            continue
        column = axis.get("column") or axis.get("field")
        if isinstance(column, str) and column:
            return column
    return None


def _axis_columns(frame: MetricFrame) -> list[str]:
    """Return declared time and dimension columns in stable metadata order."""
    columns: list[str] = []
    for axis in frame.meta.axes.values():
        if isinstance(axis, dict) and axis.get("role") in {"time", "dimension"}:
            column = axis.get("column") or axis.get("field")
            if isinstance(column, str) and column:
                columns.append(column)
        elif isinstance(axis, list):
            for entry in axis:
                if not isinstance(entry, dict):
                    continue
                column = entry.get("column") or entry.get("field")
                if isinstance(column, str) and column:
                    columns.append(column)
    return list(dict.fromkeys(columns))


def _alignment_keys(
    a: MetricFrame,
    b: MetricFrame,
    *,
    a_df: pd.DataFrame,
    b_df: pd.DataFrame,
    a_value: str,
    b_value: str,
) -> list[str]:
    """Resolve shared declared axes, retaining legacy non-numeric key fallback."""
    b_axes = set(_axis_columns(b))
    declared = [
        column
        for column in _axis_columns(a)
        if column in b_axes
        and column in a_df.columns
        and column in b_df.columns
        and column not in {a_value, b_value}
    ]
    fallback = _common_non_numeric_columns(a_df, b_df)
    return list(dict.fromkeys([*declared, *fallback]))


def _lag_series_keys(
    a: MetricFrame,
    b: MetricFrame,
    *,
    alignment_keys: list[str],
    lags: list[int],
) -> list[str]:
    """Return keys that identify independent panel series for lag shifting."""
    if not any(lag != 0 for lag in lags) or a.meta.semantic_kind != "panel":
        return []
    time_a = _time_axis_column(a)
    time_b = _time_axis_column(b)
    if time_a is None or time_a != time_b or time_a not in alignment_keys:
        raise AlignmentFailedError(
            message="signed lag for panel frames requires one shared time key",
            context={
                "time_column_a": time_a,
                "time_column_b": time_b,
                "alignment_keys": alignment_keys,
            },
        )
    series_keys = [key for key in alignment_keys if key != time_a]
    if not series_keys:
        raise AlignmentFailedError(
            message="signed lag for panel frames requires at least one shared series key",
            context={"time_column": time_a, "alignment_keys": alignment_keys},
        )
    return series_keys


def _shifted_pairs(group: pd.DataFrame, *, lag: int) -> pd.DataFrame:
    """Pair one ordered series by position without pandas label alignment."""
    n = len(group)
    offset = abs(lag)
    if offset >= n:
        return pd.DataFrame(columns=["value_a", "value_b"])
    if lag > 0:
        a_slice = group["value_a"].iloc[: n - lag]
        b_slice = group["value_b"].iloc[lag:]
    elif lag < 0:
        a_slice = group["value_a"].iloc[offset:]
        b_slice = group["value_b"].iloc[: n - offset]
    else:
        a_slice = group["value_a"]
        b_slice = group["value_b"]
    return pd.DataFrame(
        {
            "value_a": a_slice.to_numpy(copy=False),
            "value_b": b_slice.to_numpy(copy=False),
        }
    )


def _lag_results_by_lag(
    aligned: pd.DataFrame,
    *,
    lags: list[int],
    method: Literal["pearson", "spearman", "kendall"],
    group_keys: Sequence[str] = (),
) -> dict[int, _LagResult]:
    """Compute each lag within independent series, then filter null pairs."""
    if group_keys:
        grouper: str | list[str] = str(group_keys[0]) if len(group_keys) == 1 else list(group_keys)
        groups = [group for _, group in aligned.groupby(grouper, sort=False, dropna=False)]
    else:
        groups = [aligned]

    results: dict[int, _LagResult] = {}
    for lag in lags:
        pair_parts = [_shifted_pairs(group, lag=lag) for group in groups]
        candidates = pd.concat(pair_parts, ignore_index=True)
        valid = candidates.dropna(subset=["value_a", "value_b"]).reset_index(drop=True)
        dropped_row_count = len(candidates) - len(valid)
        if (
            len(valid) < 2
            or valid["value_a"].nunique(dropna=True) < 2
            or valid["value_b"].nunique(dropna=True) < 2
        ):
            correlation = float("nan")
        else:
            correlation = float(valid["value_a"].corr(valid["value_b"], method=method))
        results[lag] = _LagResult(
            correlation=correlation,
            aligned_row_count=len(valid),
            dropped_row_count=dropped_row_count,
        )
    return results


def _correlations_by_lag(
    aligned: pd.DataFrame,
    *,
    lags: list[int],
    method: Literal["pearson", "spearman", "kendall"],
    group_keys: Sequence[str] = (),
) -> dict[int, float]:
    """Correlate value_a[t] with value_b[t+lag] for each lag.

    A positive lag means ``a`` leads ``b``; a negative lag means ``b`` leads
    ``a``. NaN is returned for lags whose overlap is too short or constant.
    """
    return {
        lag: result.correlation
        for lag, result in _lag_results_by_lag(
            aligned,
            lags=lags,
            method=method,
            group_keys=group_keys,
        ).items()
    }


def _align(
    a_df: pd.DataFrame,
    b_df: pd.DataFrame,
    *,
    a_value: str,
    b_value: str,
    keys: list[str],
) -> tuple[pd.DataFrame, list[str]]:
    if not keys:
        n = min(len(a_df), len(b_df))
        left = a_df.reset_index(drop=True).iloc[:n][[a_value]]
        right = b_df.reset_index(drop=True).iloc[:n][[b_value]]
        return (
            pd.DataFrame(
                {
                    "value_a": left[a_value],
                    "value_b": right[b_value],
                }
            ),
            [],
        )
    _ensure_unique_keys(a_df, keys=keys, label="a")
    _ensure_unique_keys(b_df, keys=keys, label="b")
    left = a_df[[*keys, a_value]].rename(columns={a_value: "value_a"})
    right = b_df[[*keys, b_value]].rename(columns={b_value: "value_b"})
    left, right = _normalize_key_dtypes(left, right, keys)
    return pd.merge(left, right, on=keys, validate="one_to_one"), keys


def _common_non_numeric_columns(a_df: pd.DataFrame, b_df: pd.DataFrame) -> list[str]:
    return [
        str(column)
        for column in a_df.columns
        if column in b_df.columns
        and not is_numeric_dtype(a_df[column])
        and not is_numeric_dtype(b_df[column])
    ]


def _ensure_unique_keys(df: pd.DataFrame, *, keys: list[str], label: str) -> None:
    duplicates = df.duplicated(subset=keys, keep=False)
    if not duplicates.any():
        return
    examples = df.loc[duplicates, keys].drop_duplicates().head(5).to_dict("records")
    raise AlignmentFailedError(
        message=f"correlate {label} has duplicate key tuples",
        context={"side": label, "keys": keys, "duplicates": examples},
    )
