"""Correlate MetricFrames into AssociationResults."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
import hashlib
import json
import secrets
from datetime import UTC, date, datetime
from time import monotonic
from typing import Any, Literal, cast

import pandas as pd
from pandas.api.types import is_datetime64_any_dtype, is_numeric_dtype, is_object_dtype

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


def correlate(
    a: MetricFrame,
    b: MetricFrame,
    *,
    measure_a: str | None = None,
    measure_b: str | None = None,
    alignment: AlignmentPolicy | None = None,
    method: Literal["pearson"] = "pearson",
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
    if method != "pearson":
        raise SemanticKindMismatchError(message=f"unsupported correlation method {method!r}")

    started_at = datetime.now(UTC)
    started = monotonic()
    a_df = a.to_pandas()
    b_df = b.to_pandas()
    a_value = require_numeric_column(a_df, measure_a, purpose="correlate a")
    b_value = require_numeric_column(b_df, measure_b, purpose="correlate b")
    aligned, driver_field = _align(a_df, b_df, a_value=a_value, b_value=b_value)
    before_drop = len(aligned)
    aligned = aligned.dropna(subset=["value_a", "value_b"])
    if len(aligned) < 2:
        raise AlignmentFailedError(
            message=f"alignment '{alignment.kind}' produced fewer than two rows"
        )
    if aligned["value_a"].nunique(dropna=True) < 2 or aligned["value_b"].nunique(dropna=True) < 2:
        raise AlignmentFailedError(message="pearson correlation is undefined for constant input")

    correlation = float(aligned["value_a"].corr(aligned["value_b"], method=method))
    if pd.isna(correlation):
        raise AlignmentFailedError(message="pearson correlation produced NaN")

    alignment_dump = alignment.model_dump(mode="json")
    output = pd.DataFrame(
        {
            "metric_id_a": [a.meta.metric_id],
            "metric_id_b": [b.meta.metric_id],
            "semantic_model_a": [a.meta.semantic_model],
            "semantic_model_b": [b.meta.semantic_model],
            "semantic_kind": [a.meta.semantic_kind],
            "method": [method],
            "alignment_kind": [alignment.kind],
            "lag_mode": ["single"],
            "lag_offset": [0],
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
        "measure_a": a_value,
        "measure_b": b_value,
        "alignment": alignment_dump,
        "method": method,
    }
    frame_ref = _gen_ref("frame")
    job_ref = _gen_ref("job")
    finished_at = datetime.now(UTC)
    source_refs = [a.ref, b.ref]
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
        lag_policy={"mode": "single", "offset": 0},
        aligned_row_count=len(aligned),
        dropped_row_count=before_drop - len(aligned),
        correlation=correlation,
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
            semantic_anchors=CommitSemanticAnchors(
                values={"left_metric_id": a.meta.metric_id, "right_metric_id": b.meta.metric_id}
            ),
            subject=Subject(metric=None, analysis_axis="correlation"),
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
            "semantic_model": a.meta.semantic_model,
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


def _align(
    a_df: pd.DataFrame,
    b_df: pd.DataFrame,
    *,
    a_value: str,
    b_value: str,
) -> tuple[pd.DataFrame, str | None]:
    keys = _common_non_numeric_columns(a_df, b_df)
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
            None,
        )
    _ensure_unique_keys(a_df, keys=keys, label="a")
    _ensure_unique_keys(b_df, keys=keys, label="b")
    left = a_df[[*keys, a_value]].rename(columns={a_value: "value_a"})
    right = b_df[[*keys, b_value]].rename(columns={b_value: "value_b"})
    left, right = _normalize_key_dtypes(left, right, keys)
    return pd.merge(left, right, on=keys, validate="one_to_one"), ",".join(keys)


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
