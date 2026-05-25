"""Correlate same-model MetricFrames into AttributionFrames."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
from datetime import UTC, datetime
from time import monotonic
from typing import Literal

import pandas as pd
from pandas.api.types import is_numeric_dtype

from marivo.analysis_py.errors import AlignmentFailedError, SemanticKindMismatchError
from marivo.analysis_py.frames.attribution import AttributionFrame
from marivo.analysis_py.frames.metric import MetricFrame
from marivo.analysis_py.intents._derived import (
    ensure_frame_in_session,
    persist_attribution_frame,
    require_numeric_column,
    resolve_session,
)
from marivo.analysis_py.session.core import Session, ensure_session_writable


def correlate(
    a: MetricFrame,
    b: MetricFrame,
    *,
    value_a: str | None = None,
    value_b: str | None = None,
    align: Literal["sample", "bucket", "segment_key"] = "sample",
    method: Literal["pearson"] = "pearson",
    session: Session | None = None,
) -> AttributionFrame:
    session = resolve_session(session)
    ensure_session_writable(session)
    if not isinstance(a, MetricFrame) or not isinstance(b, MetricFrame):
        raise SemanticKindMismatchError(message="correlate requires MetricFrame inputs")
    ensure_frame_in_session(a, session=session, label="correlate a")
    ensure_frame_in_session(b, session=session, label="correlate b")
    if a.meta.semantic_model != b.meta.semantic_model:
        raise SemanticKindMismatchError(
            message="correlate requires frames from the same semantic_model",
            details={"a": a.meta.semantic_model, "b": b.meta.semantic_model},
        )
    if a.meta.semantic_kind != b.meta.semantic_kind:
        raise SemanticKindMismatchError(
            message="correlate requires matching semantic_kind",
            details={"a": a.meta.semantic_kind, "b": b.meta.semantic_kind},
        )
    if method != "pearson":
        raise SemanticKindMismatchError(message=f"unsupported correlation method {method!r}")

    started_at = datetime.now(UTC)
    started = monotonic()
    a_df = a.to_pandas()
    b_df = b.to_pandas()
    a_value = require_numeric_column(a_df, value_a, purpose="correlate a")
    b_value = require_numeric_column(b_df, value_b, purpose="correlate b")
    aligned, driver_field = _align(a_df, b_df, a_value=a_value, b_value=b_value, align=align)
    before_drop = len(aligned)
    aligned = aligned.dropna(subset=["value_a", "value_b"])
    if len(aligned) < 2:
        raise AlignmentFailedError(message=f"alignment '{align}' produced fewer than two rows")
    if aligned["value_a"].nunique(dropna=True) < 2 or aligned["value_b"].nunique(dropna=True) < 2:
        raise AlignmentFailedError(message="pearson correlation is undefined for constant input")

    correlation = float(aligned["value_a"].corr(aligned["value_b"], method=method))
    if pd.isna(correlation):
        raise AlignmentFailedError(message="pearson correlation produced NaN")

    output = pd.DataFrame(
        {
            "metric_id_a": [a.meta.metric_id],
            "metric_id_b": [b.meta.metric_id],
            "semantic_model": [a.meta.semantic_model],
            "semantic_kind": [a.meta.semantic_kind],
            "method": [method],
            "align": [align],
            "driver_field": [driver_field],
            "value_column_a": [a_value],
            "value_column_b": [b_value],
            "input_row_count_a": [len(a_df)],
            "input_row_count_b": [len(b_df)],
            "aligned_row_count": [len(aligned)],
            "dropped_row_count": [before_drop - len(aligned)],
            "correlation": [correlation],
        }
    )
    params = {
        "source_a_ref": a.ref,
        "source_b_ref": b.ref,
        "value_a": a_value,
        "value_b": b_value,
        "align": align,
        "method": method,
    }
    return persist_attribution_frame(
        session=session,
        df=output,
        intent="correlate",
        params=params,
        sources=[a, b],
        metric_ids=[a.meta.metric_id, b.meta.metric_id],
        attribution_kind="correlation",
        driver_field=driver_field,
        value_column=None,
        contribution_column=None,
        method=method,
        semantic_kind=a.meta.semantic_kind,
        semantic_model=a.meta.semantic_model,
        started_at=started_at,
        started_monotonic=started,
    )


def _align(
    a_df: pd.DataFrame,
    b_df: pd.DataFrame,
    *,
    a_value: str,
    b_value: str,
    align: str,
) -> tuple[pd.DataFrame, str | None]:
    if align == "sample":
        n = min(len(a_df), len(b_df))
        left = a_df.reset_index(drop=True).loc[: n - 1, [a_value]]
        right = b_df.reset_index(drop=True).loc[: n - 1, [b_value]]
        return (
            pd.DataFrame(
                {
                    "value_a": left[a_value],
                    "value_b": right[b_value],
                }
            ),
            None,
        )
    if align in {"bucket", "segment_key"}:
        keys = _common_non_numeric_columns(a_df, b_df)
        if not keys:
            raise AlignmentFailedError(message=f"align='{align}' could not find a common key")
        _ensure_unique_keys(a_df, keys=keys, label="a")
        _ensure_unique_keys(b_df, keys=keys, label="b")
        left = a_df[[*keys, a_value]].rename(columns={a_value: "value_a"})
        right = b_df[[*keys, b_value]].rename(columns={b_value: "value_b"})
        return pd.merge(left, right, on=keys, validate="one_to_one"), ",".join(keys)
    raise AlignmentFailedError(message=f"unknown align mode '{align}'")


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
        details={"side": label, "keys": keys, "duplicates": examples},
    )
