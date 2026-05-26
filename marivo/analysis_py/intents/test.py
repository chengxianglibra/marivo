"""Hypothesis tests over MetricFrames."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
from datetime import UTC, datetime
from math import sqrt
from time import monotonic
from typing import Literal, cast

import pandas as pd
from scipy import stats

from marivo.analysis_py.errors import (
    SemanticKindMismatchError,
    TestAlignmentError,
    TestPolicyError,
    TestShapeNotTestableError,
)
from marivo.analysis_py.frames.hypothesis import HypothesisTestResult, HypothesisTestResultMeta
from marivo.analysis_py.frames.metric import MetricFrame
from marivo.analysis_py.intents._derived import (
    compose_lineage,
    ensure_frame_in_session,
    gen_ref,
    params_digest,
    require_numeric_column,
    resolve_session,
)
from marivo.analysis_py.lineage import LineageStep
from marivo.analysis_py.policies import AlignmentPolicy, SamplingPolicy
from marivo.analysis_py.session.core import Session, ensure_session_writable
from marivo.analysis_py.session.persistence import write_frame_to_disk, write_job_record


def hypothesis_test(
    a: MetricFrame,
    b: MetricFrame,
    *,
    hypothesis: Literal["mean_changed"] = "mean_changed",
    value_a: str | None = None,
    value_b: str | None = None,
    alignment: AlignmentPolicy | None = None,
    sampling: SamplingPolicy | None = None,
    alpha: float = 0.05,
    session: Session | None = None,
) -> HypothesisTestResult:
    session = resolve_session(session)
    ensure_session_writable(session)
    if not isinstance(a, MetricFrame) or not isinstance(b, MetricFrame):
        raise SemanticKindMismatchError(message="test requires MetricFrame inputs")
    ensure_frame_in_session(a, session=session, label="test a")
    ensure_frame_in_session(b, session=session, label="test b")
    if hypothesis != "mean_changed":
        raise TestPolicyError(message=f"unsupported hypothesis {hypothesis!r}")
    if not 0 < alpha <= 0.5:
        raise TestPolicyError(message="alpha must be in (0, 0.5]", details={"alpha": alpha})
    alignment = alignment or AlignmentPolicy(kind="calendar_bucket")
    sampling = sampling or SamplingPolicy()
    if alignment.kind != "calendar_bucket":
        raise TestPolicyError(
            message="test v1 only supports calendar_bucket alignment",
            details={"alignment": alignment.model_dump(mode="json")},
        )
    if a.meta.semantic_kind != b.meta.semantic_kind:
        raise SemanticKindMismatchError(
            message="test requires matching semantic_kind",
            details={"a": a.meta.semantic_kind, "b": b.meta.semantic_kind},
        )
    if a.meta.semantic_model != b.meta.semantic_model:
        raise SemanticKindMismatchError(
            message="test requires matching semantic_model",
            details={"a": a.meta.semantic_model, "b": b.meta.semantic_model},
        )
    if a.meta.semantic_kind == "scalar":
        raise TestShapeNotTestableError(message="scalar MetricFrame is not testable for mean_changed")

    expected_pairing = "segment_key" if a.meta.semantic_kind == "segmented" else "calendar_bucket"
    if sampling.pairing != expected_pairing:
        raise TestPolicyError(
            message="SamplingPolicy.pairing does not match input shape",
            details={
                "semantic_kind": a.meta.semantic_kind,
                "pairing": sampling.pairing,
                "expected": expected_pairing,
            },
        )

    started_at = datetime.now(UTC)
    started = monotonic()
    a_df = a.to_pandas()
    b_df = b.to_pandas()
    a_value = require_numeric_column(a_df, value_a, purpose="test a")
    b_value = require_numeric_column(b_df, value_b, purpose="test b")

    if a.meta.semantic_kind == "panel":
        segment_dims = _segment_dimensions(a)
        rows = _panel_tests(
            a_df,
            b_df,
            a_value=a_value,
            b_value=b_value,
            segment_dims=segment_dims,
            min_n=sampling.min_n,
            alpha=alpha,
        )
        result_shape: Literal["single", "per_segment"] = "per_segment"
    else:
        paired = _paired_values(a_df, b_df, a_value=a_value, b_value=b_value, keys=_pairing_keys(a))
        if paired.empty:
            raise TestAlignmentError(message="test alignment produced no paired rows")
        row = _paired_t_row(paired, min_n=sampling.min_n, alpha=alpha)
        if row["reason_code"] == "insufficient_pairs":
            raise TestShapeNotTestableError(
                message="paired sample size is below SamplingPolicy.min_n",
                details={"sample_size": row["sample_size"], "min_n": sampling.min_n},
            )
        rows = [row]
        result_shape = "single"

    output = pd.DataFrame(rows)
    alignment_dump = alignment.model_dump(mode="json")
    sampling_dump = sampling.model_dump(mode="json")
    params = {
        "source_a_ref": a.ref,
        "source_b_ref": b.ref,
        "value_a": a_value,
        "value_b": b_value,
        "hypothesis": hypothesis,
        "method": "paired_t",
        "alignment": alignment_dump,
        "sampling": sampling_dump,
        "alpha": alpha,
        "result_shape": result_shape,
    }
    frame_ref = gen_ref("frame")
    job_ref = gen_ref("job")
    source_refs = [a.ref, b.ref]
    finished_at = datetime.now(UTC)
    meta = HypothesisTestResultMeta(
        kind="hypothesis_test_result",
        ref=frame_ref,
        session_id=session.id,
        project_root=str(session.project_root),
        produced_by_job=job_ref,
        created_at=finished_at,
        row_count=len(output),
        byte_size=0,
        lineage=compose_lineage(
            [a, b],
            step=LineageStep(
                intent="test",
                job_ref=job_ref,
                inputs=source_refs,
                params_digest=params_digest(params),
            ),
        ),
        source_refs=source_refs,
        metric_ids=[a.meta.metric_id, b.meta.metric_id],
        semantic_kinds=[a.meta.semantic_kind, b.meta.semantic_kind],
        semantic_models=[a.meta.semantic_model, b.meta.semantic_model],
        hypothesis="mean_changed",
        method="paired_t",
        alignment=alignment_dump,
        sampling=sampling_dump,
        alpha=alpha,
        result_shape=result_shape,
        segment_dimensions=_segment_dimensions(a) if result_shape == "per_segment" else [],
        rejected_count=int(output["rejected"].sum()) if "rejected" in output else 0,
        not_enough_data_count=int((output["reason_code"] == "insufficient_pairs").sum())
        if "reason_code" in output
        else 0,
    )
    frame = HypothesisTestResult(_df=output, meta=meta)
    frame.meta = cast("HypothesisTestResultMeta", write_frame_to_disk(session.layout, frame))
    write_job_record(
        session.layout,
        {
            "id": job_ref,
            "session_id": session.id,
            "intent": "test",
            "params": params,
            "input_frame_refs": source_refs,
            "output_frame_ref": frame_ref,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_ms": int((monotonic() - started) * 1000),
            "status": "succeeded",
            "error": None,
            "semantic_project_root": session.semantic_project.root,
            "semantic_model": a.meta.semantic_model,
            "semantic_models": [a.meta.semantic_model, b.meta.semantic_model],
        },
    )
    return frame


def _segment_dimensions(frame: MetricFrame) -> list[str]:
    dims = frame.meta.axes.get("dimensions", [])
    return [str(dim["field"]) for dim in dims if isinstance(dim, dict) and "field" in dim]


def _time_column(frame: MetricFrame) -> str:
    time_axis = frame.meta.axes.get("time", {})
    if isinstance(time_axis, dict) and isinstance(time_axis.get("field"), str):
        return str(time_axis["field"])
    if isinstance(time_axis, dict) and isinstance(time_axis.get("column"), str):
        return str(time_axis["column"])
    return "time"


def _pairing_keys(frame: MetricFrame) -> list[str]:
    if frame.meta.semantic_kind == "segmented":
        return _segment_dimensions(frame)
    return [_time_column(frame)]


def _paired_values(
    a_df: pd.DataFrame,
    b_df: pd.DataFrame,
    *,
    a_value: str,
    b_value: str,
    keys: list[str],
) -> pd.DataFrame:
    left = a_df[[*keys, a_value]].rename(columns={a_value: "value_a"})
    right = b_df[[*keys, b_value]].rename(columns={b_value: "value_b"})
    paired = pd.merge(left, right, on=keys, validate="one_to_one")
    return paired.dropna(subset=["value_a", "value_b"])


def _paired_t_row(
    paired: pd.DataFrame,
    *,
    min_n: int,
    alpha: float,
    prefix: dict[str, object] | None = None,
) -> dict[str, object]:
    prefix = prefix or {}
    diff = paired["value_a"] - paired["value_b"]
    n = int(diff.count())
    mean_a = float(paired["value_a"].mean()) if n else float("nan")
    mean_b = float(paired["value_b"].mean()) if n else float("nan")
    if n < min_n:
        return {
            **prefix,
            "test_statistic": float("nan"),
            "p_value": float("nan"),
            "df": n - 1,
            "sample_size": n,
            "mean_a": mean_a,
            "mean_b": mean_b,
            "mean_diff": float("nan"),
            "ci_lower": float("nan"),
            "ci_upper": float("nan"),
            "rejected": False,
            "reason_code": "insufficient_pairs",
        }
    mean_diff = float(diff.mean())
    sd = float(diff.std(ddof=1))
    if sd == 0:
        return {
            **prefix,
            "test_statistic": float("nan"),
            "p_value": float("nan"),
            "df": n - 1,
            "sample_size": n,
            "mean_a": mean_a,
            "mean_b": mean_b,
            "mean_diff": mean_diff,
            "ci_lower": float("nan"),
            "ci_upper": float("nan"),
            "rejected": False,
            "reason_code": "constant_diff",
        }
    test_stat = mean_diff / (sd / sqrt(n))
    p_value = 2 * float(stats.t.sf(abs(test_stat), n - 1))
    crit = float(stats.t.ppf(1 - alpha / 2, n - 1))
    half_width = crit * sd / sqrt(n)
    return {
        **prefix,
        "test_statistic": float(test_stat),
        "p_value": p_value,
        "df": n - 1,
        "sample_size": n,
        "mean_a": mean_a,
        "mean_b": mean_b,
        "mean_diff": mean_diff,
        "ci_lower": mean_diff - half_width,
        "ci_upper": mean_diff + half_width,
        "rejected": p_value < alpha,
        "reason_code": "ok",
    }


def _panel_tests(
    a_df: pd.DataFrame,
    b_df: pd.DataFrame,
    *,
    a_value: str,
    b_value: str,
    segment_dims: list[str],
    min_n: int,
    alpha: float,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    keys = [*segment_dims, "time"]
    paired = _paired_values(a_df, b_df, a_value=a_value, b_value=b_value, keys=keys)
    if paired.empty:
        raise TestAlignmentError(message="test alignment produced no paired rows")
    group_key: str | list[str] = segment_dims[0] if len(segment_dims) == 1 else segment_dims
    for segment_key, group in paired.groupby(group_key, dropna=False):
        values = segment_key if isinstance(segment_key, tuple) else (segment_key,)
        prefix = dict(zip(segment_dims, values, strict=True))
        rows.append(_paired_t_row(group, min_n=min_n, alpha=alpha, prefix=prefix))
    return rows
