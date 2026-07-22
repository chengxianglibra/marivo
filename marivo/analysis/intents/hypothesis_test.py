"""Hypothesis tests over MetricFrames."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
from datetime import UTC, datetime
from math import sqrt
from time import monotonic
from typing import Literal, cast

import pandas as pd
from scipy import stats

from marivo.analysis._semantic_persistence import job_semantics_from_frames
from marivo.analysis.errors import (
    AlignmentFailedError,
    SemanticKindMismatchError,
    TestAlignmentError,
    TestPolicyError,
    TestShapeNotTestableError,
)
from marivo.analysis.evidence.pipeline import (
    CommitInputs,
    CommitParams,
    CommitSemanticAnchors,
    commit_result,
)
from marivo.analysis.evidence.types import Subject
from marivo.analysis.frames.hypothesis import HypothesisTestResult, HypothesisTestResultMeta
from marivo.analysis.frames.metric import MetricFrame
from marivo.analysis.intents._derived import (
    compose_lineage,
    ensure_frame_in_session,
    gen_ref,
    params_digest,
    resolve_metric_value_column,
    resolve_session,
)
from marivo.analysis.intents._validate import require_single_metric
from marivo.analysis.intents._window_pairs import (
    _not_nan,
    _panel_grain,
    _prepared_value_map,
    _walk_ordinal_pairs,
)
from marivo.analysis.lineage import LineageStep
from marivo.analysis.policies import AlignmentPolicy, SamplingPolicy
from marivo.analysis.session._runtime import persist_job_record, register_frame_artifact
from marivo.analysis.session.core import Session, ensure_session_writable


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
    analysis_purpose: str | None = None,
    session: Session | None = None,
) -> HypothesisTestResult:
    session = resolve_session(session)
    ensure_session_writable(session)
    if not isinstance(a, MetricFrame) or not isinstance(b, MetricFrame):
        raise SemanticKindMismatchError(message="hypothesis_test requires MetricFrame inputs")
    require_single_metric(a, intent="hypothesis_test")
    require_single_metric(b, intent="hypothesis_test")
    # hypothesis_test operates on arity-1 metric frames; multi-metric frames are
    # gated out upstream. Narrow metric_id for downstream HypothesisTestResultMeta.
    assert a.meta.metric_id is not None
    assert b.meta.metric_id is not None
    ensure_frame_in_session(a, session=session, label="hypothesis_test a")
    ensure_frame_in_session(b, session=session, label="hypothesis_test b")
    if hypothesis != "mean_changed":
        raise TestPolicyError(message=f"unsupported hypothesis {hypothesis!r}")
    if not 0 < alpha <= 0.5:
        raise TestPolicyError(message="alpha must be in (0, 0.5]", context={"alpha": alpha})
    alignment = alignment or AlignmentPolicy(kind="window_bucket")
    sampling = sampling or SamplingPolicy()
    if alignment.kind != "window_bucket":
        raise TestPolicyError(
            message="hypothesis_test v1 only supports window_bucket alignment",
            context={"alignment": alignment.model_dump(mode="json")},
        )
    if alignment.mode != "ordinal_bucket" or alignment.strict_lengths:
        raise TestPolicyError(
            message="hypothesis_test v1 only supports default window_bucket alignment",
            context={"alignment": alignment.model_dump(mode="json")},
        )
    if a.meta.semantic_kind != b.meta.semantic_kind:
        raise SemanticKindMismatchError(
            message="hypothesis_test requires matching semantic_kind",
            context={"a": a.meta.semantic_kind, "b": b.meta.semantic_kind},
        )
    if a.meta.semantic_model != b.meta.semantic_model:
        raise SemanticKindMismatchError(
            message="hypothesis_test requires matching semantic_model",
            context={"a": a.meta.semantic_model, "b": b.meta.semantic_model},
        )
    if a.meta.semantic_kind == "scalar":
        raise TestShapeNotTestableError(
            message="scalar MetricFrame is not testable for mean_changed"
        )

    expected_pairing = "segment_key" if a.meta.semantic_kind == "segmented" else "window_bucket"
    if sampling.pairing != expected_pairing:
        raise TestPolicyError(
            message="SamplingPolicy.pairing does not match input shape",
            context={
                "semantic_kind": a.meta.semantic_kind,
                "pairing": sampling.pairing,
                "expected": expected_pairing,
            },
        )

    started_at = datetime.now(UTC)
    started = monotonic()
    a_df = a._dataframe_copy()
    b_df = b._dataframe_copy()
    a_value_column = resolve_metric_value_column(
        a,
        a_df,
        value_a,
        parameter="value_a",
        purpose="hypothesis_test a",
    )
    b_value_column = resolve_metric_value_column(
        b,
        b_df,
        value_b,
        parameter="value_b",
        purpose="hypothesis_test b",
    )
    a_value = a_value_column.internal_name
    b_value = b_value_column.internal_name

    if a.meta.semantic_kind == "panel":
        segment_dims = _segment_dimensions(a)
        rows = _panel_tests(
            a_df,
            b_df,
            a_value=a_value,
            b_value=b_value,
            segment_dims=segment_dims,
            frame_a=a,
            frame_b=b,
            min_n=sampling.min_n,
            alpha=alpha,
        )
        result_shape: Literal["single", "per_segment"] = "per_segment"
    else:
        if a.meta.semantic_kind == "segmented":
            paired = _paired_values(
                a_df, b_df, a_value=a_value, b_value=b_value, keys=_segment_dimensions(a)
            )
        else:
            paired = _ordinal_paired_values(
                a_df, b_df, a_value=a_value, b_value=b_value, frame_a=a, frame_b=b
            )
        if paired.empty:
            raise TestAlignmentError(message="hypothesis_test alignment produced no paired rows")
        row = _paired_t_row(paired, min_n=sampling.min_n, alpha=alpha)
        if row["reason_code"] == "insufficient_pairs":
            raise TestShapeNotTestableError(
                message="paired sample size is below SamplingPolicy.min_n",
                context={"sample_size": row["sample_size"], "min_n": sampling.min_n},
            )
        rows = [row]
        result_shape = "single"

    output = pd.DataFrame(rows)
    alignment_dump = alignment.model_dump(mode="json")
    sampling_dump = sampling.model_dump(mode="json")
    params = {
        "source_a_ref": a.ref,
        "source_b_ref": b.ref,
        "value_a": a_value_column.public_name,
        "value_b": b_value_column.public_name,
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
        analysis_purpose=analysis_purpose,
        created_at=finished_at,
        row_count=len(output),
        byte_size=0,
        lineage=compose_lineage(
            [a, b],
            step=LineageStep(
                intent="hypothesis_test",
                job_ref=job_ref,
                inputs=source_refs,
                params_digest=params_digest(params),
                analysis_purpose=analysis_purpose,
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
    left_subject = {"metric": a.meta.metric_id, "window": a.meta.window}
    right_subject = {"metric": b.meta.metric_id, "window": b.meta.window}
    frame = cast(
        "HypothesisTestResult",
        commit_result(
            store=session._evidence_store(),
            frames_dir=session._layout.frames_dir,
            frame=frame,
            step_type="hypothesis_test",
            inputs=CommitInputs(
                input_refs=[a.meta.artifact_id or a.ref, b.meta.artifact_id or b.ref]
            ),
            params=CommitParams(values=params),
            semantic_anchors=CommitSemanticAnchors.from_frames(a, b),
            subject=Subject(analysis_axis="scalar"),
            extractor_family="hypothesis_test_result",
            seeding_context={
                "left_subject": left_subject,
                "right_subject": right_subject,
                "alternative": "two_sided",
            },
        ),
    )
    register_frame_artifact(session, frame)
    persist_job_record(
        session,
        {
            "id": job_ref,
            "session_id": session.id,
            "intent": "hypothesis_test",
            **job_semantics_from_frames(a, b),
            "analysis_purpose": analysis_purpose,
            "params": params,
            "input_frame_refs": source_refs,
            "output_frame_ref": frame.meta.artifact_id or frame_ref,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_ms": int((monotonic() - started) * 1000),
            "status": "succeeded",
            "error": None,
            "semantic_project_root": str(session.catalog._project.semantic_root),
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


def _ordinal_paired_values(
    a_df: pd.DataFrame,
    b_df: pd.DataFrame,
    *,
    a_value: str,
    b_value: str,
    frame_a: MetricFrame,
    frame_b: MetricFrame,
) -> pd.DataFrame:
    """Pair two time-indexed frames by ordinal bucket position.

    Uses :func:`_walk_ordinal_pairs` to enumerate the expected bucket
    timestamps for each frame and walk them in lockstep. Emits one row
    per ordinal where both frames have an observed (non-NaN) value.
    """
    grain = _panel_grain(frame_a)
    if not isinstance(grain, str) or not grain:
        raise AlignmentFailedError(
            message="hypothesis_test alignment requires a time axis grain",
            context={"kind": "WindowBucketGrainMissing"},
        )
    time_column = _time_column(frame_a)
    a_map = _prepared_value_map(a_df, time_column=time_column, value_column=a_value, grain=grain)
    b_map = _prepared_value_map(b_df, time_column=time_column, value_column=b_value, grain=grain)
    rows: list[dict[str, object]] = []
    for pair in _walk_ordinal_pairs(a_map, b_map, grain=grain, frame_a=frame_a, frame_b=frame_b):
        if pair.a_present and pair.b_present and _not_nan(pair.a_value) and _not_nan(pair.b_value):
            rows.append({"value_a": pair.a_value, "value_b": pair.b_value})
    return pd.DataFrame(rows, columns=["value_a", "value_b"])


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
    frame_a: MetricFrame,
    frame_b: MetricFrame,
    min_n: int,
    alpha: float,
) -> list[dict[str, object]]:
    """Run a paired t-test per segment, pairing the time axis ordinally.

    Each segment slice is paired by ordinal bucket position via
    ``_ordinal_paired_values`` so that two panel windows over disjoint date
    ranges still produce paired rows per segment.
    """
    rows: list[dict[str, object]] = []
    group_key: str | list[str] = segment_dims[0] if len(segment_dims) == 1 else segment_dims
    a_groups = dict(iter(a_df.groupby(group_key, dropna=False)))
    b_groups = dict(iter(b_df.groupby(group_key, dropna=False)))
    any_paired = False
    for segment_key in list(a_groups.keys()) + [key for key in b_groups if key not in a_groups]:
        a_segment = a_groups.get(segment_key)
        b_segment = b_groups.get(segment_key)
        if a_segment is None or b_segment is None:
            continue
        paired = _ordinal_paired_values(
            a_segment,
            b_segment,
            a_value=a_value,
            b_value=b_value,
            frame_a=frame_a,
            frame_b=frame_b,
        )
        if paired.empty:
            continue
        any_paired = True
        values = segment_key if isinstance(segment_key, tuple) else (segment_key,)
        prefix = dict(zip(segment_dims, values, strict=True))
        rows.append(_paired_t_row(paired, min_n=min_n, alpha=alpha, prefix=prefix))
    if not any_paired:
        raise TestAlignmentError(message="hypothesis_test alignment produced no paired rows")
    return rows
