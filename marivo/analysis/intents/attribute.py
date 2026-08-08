"""Public deterministic attribution composite operator."""

from __future__ import annotations

from datetime import UTC, datetime
from time import monotonic
from typing import Any, Literal, cast

import pandas as pd

from marivo._temporal import _new_time_scope
from marivo.analysis._cumulative import (
    GrainToDateAnchorSemanticsV1,
    TrailingAnchorSemanticsV1,
)
from marivo.analysis.attribution_contract import (
    DistinctAttributionBasisV1,
    QuantileAttributionBasisV1,
)
from marivo.analysis.cumulative_attribution import (
    AvailableCumulativeBridgeV1,
    DirectCumulativeAttributionV1,
    select_cumulative_attribution_route,
)
from marivo.analysis.errors import (
    AttributeAdmissionBlockedError,
    AttributionMaterializationError,
    SemanticKindMismatchError,
)
from marivo.analysis.evidence.identity import make_issue_id
from marivo.analysis.evidence.types import AnalysisScope, ComparabilityIssue
from marivo.analysis.frames._attribution_columns import ATTRIBUTION_PATH_COLUMN
from marivo.analysis.frames.attribution import (
    AllHistoryAnchorSemanticsV1,
    AttributionFrame,
    AttributionReconciliation,
    CumulativeAllHistoryFlowEvidenceV1,
    CumulativeAllHistoryPartitionV1,
    CumulativeAttributionAnchorV1,
    CumulativeAttributionPartitionV1,
    CumulativeBusinessAxisEvidenceV1,
    CumulativeComparablePeriodPartitionV1,
    CumulativeGrainToDateFlowEvidenceV1,
    CumulativeTrailingFlowEvidenceV1,
    QuantileReplacementEvidenceV1,
)
from marivo.analysis.frames.base import BaseFrame
from marivo.analysis.frames.delta import (
    CumulativeDeltaFrameMetaV1,
    DeltaFrame,
    DeltaFrameMeta,
    _attribute_admission,
)
from marivo.analysis.frames.metric import MetricFrame
from marivo.analysis.intents._attribution_mode import AttributionMode, validate_attribution_mode
from marivo.analysis.intents._derived import (
    ensure_frame_in_session,
    persist_attribution_frame,
    resolve_session,
)
from marivo.analysis.intents._nonadditive_attribution import (
    attribute_distinct,
    attribute_exact_quantile,
    attribute_qdigest_quantile,
)
from marivo.analysis.intents._replay import (
    _dimension_ref,
    recover_alignment_policy,
    recover_observe_replay,
)
from marivo.analysis.intents.compare import compare
from marivo.analysis.intents.decompose import (
    _decompose,
    _effective_component_axis_column,
    _finalize_attribution_output,
    _multi_axis_hierarchy_output,
    _multi_axis_joint_sum_output,
    _normalize_axis_boundary,
    _single_axis_sum_output,
    _validate_attribution_semantics,
    decompose,
)
from marivo.analysis.session.core import Session, ensure_session_can_execute
from marivo.analysis.windows.grain import Grain, to_temporal_grain
from marivo.refs import DimensionKind, RefPayloadV1, TimeDimensionKind
from marivo.refs import ref as ref_factory
from marivo.semantic.catalog import _SemanticInput


def _normalize_attribute_axes(
    session: Session,
    axes: list[_SemanticInput[DimensionKind | TimeDimensionKind]],
) -> list[str]:
    if not axes:
        raise SemanticKindMismatchError(
            message="attribute requires at least one axis",
            context={"argument": "axes"},
        )
    axis_ids = [_normalize_axis_boundary(session, axis) for axis in axes]
    if len(set(axis_ids)) != len(axis_ids):
        raise SemanticKindMismatchError(
            message="attribute axes must be distinct",
            context={"argument": "axes", "reason": "duplicate_axes", "axes": axis_ids},
        )
    return axis_ids


def _missing_axis_ids(frame: DeltaFrame, axis_ids: list[str]) -> list[str]:
    columns = [str(column) for column in frame._dataframe_copy().columns]
    return [
        axis_id
        for axis_id in axis_ids
        if _effective_component_axis_column(frame, axis_id, columns) is None
    ]


def _load_metric_source(
    session: Session,
    ref: str,
    *,
    label: str,
    delta: DeltaFrame,
    missing_axes: list[str],
) -> MetricFrame:
    try:
        frame = session.get_frame(ref)
    except Exception as exc:
        raise AttributionMaterializationError(
            message=f"attribute could not load {label} source frame",
            context={
                "recoverability_status": "source_frame_missing",
                "delta_ref": delta.ref,
                "missing_axes": missing_axes,
                "source_refs": {
                    "current": delta.meta.source_current_ref,
                    "baseline": delta.meta.source_baseline_ref,
                },
            },
        ) from exc
    if not isinstance(frame, MetricFrame):
        raise AttributionMaterializationError(
            message=f"attribute {label} source is not a MetricFrame",
            context={
                "recoverability_status": "source_frame_not_metric",
                "delta_ref": delta.ref,
                "missing_axes": missing_axes,
                "source_ref": ref,
                "source_kind": getattr(getattr(frame, "meta", None), "kind", type(frame).__name__),
            },
        )
    return frame


def _cumulative_anchor_evidence(frame: DeltaFrame) -> CumulativeAttributionAnchorV1:
    assert isinstance(frame.meta, DeltaFrameMeta)
    if frame.meta.cumulative_change is not None:
        return AllHistoryAnchorSemanticsV1()
    alignment = frame.meta.comparable_period_alignment()
    if alignment is None:
        raise AttributionMaterializationError(
            message="cumulative delta is missing canonical anchor evidence",
            context={"recoverability_status": "anchor_evidence_missing", "delta_ref": frame.ref},
        )
    anchor = alignment.canonical_anchor
    if isinstance(anchor, GrainToDateAnchorSemanticsV1 | TrailingAnchorSemanticsV1):
        return anchor
    raise AssertionError("unknown cumulative anchor evidence")


def _json_scalar(value: object) -> str | int | float | bool | None:
    if pd.isna(cast("Any", value)):
        return None
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return str(isoformat())
    item = getattr(value, "item", None)
    if callable(item):
        value = item()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _business_axis_partitions(
    original: DeltaFrame, expanded_df: pd.DataFrame
) -> tuple[CumulativeAttributionPartitionV1, ...]:
    original_df = original._dataframe_copy()
    bucket_column: str | None = None
    axes = original.meta.alignment.get("axes")
    if isinstance(axes, dict):
        for axis in axes.values():
            if isinstance(axis, dict) and axis.get("role") == "time":
                candidate = axis.get("column")
                if isinstance(candidate, str) and candidate in original_df.columns:
                    bucket_column = candidate
                    break
    partitions: list[CumulativeAttributionPartitionV1] = []
    if bucket_column is None:
        target = float(pd.to_numeric(original_df["delta"], errors="raise").sum())
        tolerance = max(1e-12, 1e-9 * max(abs(target), 1.0))
        partitions.append(
            CumulativeAttributionPartitionV1(
                comparison_key=(),
                target_delta=target,
                contribution_sum=target,
                row_count=len(expanded_df),
                residual=0.0,
                tolerance=tolerance,
            )
        )
        return tuple(partitions)
    for bucket, rows in expanded_df.groupby(bucket_column, dropna=False, sort=True):
        target_rows = original_df[
            original_df[bucket_column].map(_json_scalar) == _json_scalar(bucket)
        ]
        target = float(pd.to_numeric(target_rows["delta"], errors="raise").sum())
        tolerance = max(1e-12, 1e-9 * max(abs(target), 1.0))
        partitions.append(
            CumulativeAttributionPartitionV1(
                comparison_key=((bucket_column, _json_scalar(bucket)),),
                target_delta=target,
                contribution_sum=target,
                row_count=len(rows),
                residual=0.0,
                tolerance=tolerance,
            )
        )
    return tuple(partitions)


def _attribute_direct_cumulative_business_axes(
    frame: DeltaFrame,
    *,
    current: MetricFrame,
    baseline: MetricFrame,
    axis_ids: list[str],
    mode: AttributionMode | None,
    analysis_purpose: str | None,
    session: Session,
) -> AttributionFrame:
    """Attribute direct cumulative levels with zero-valued absent partitions."""

    assert isinstance(frame.meta, CumulativeDeltaFrameMetaV1)
    started_at = datetime.now(UTC)
    started = monotonic()
    current_df = current._dataframe_copy()
    baseline_df = baseline._dataframe_copy()
    current_dimensions = tuple(
        (binding.ref.path, binding.column)
        for binding in current.meta.axis_bindings
        if binding.role == "dimension"
    )
    baseline_dimensions = tuple(
        (binding.ref.path, binding.column)
        for binding in baseline.meta.axis_bindings
        if binding.role == "dimension"
    )
    if current_dimensions != baseline_dimensions:
        raise AttributionMaterializationError(
            message="replayed cumulative business axes differ across sources",
            context={"recoverability_status": "source_axis_mismatch", "delta_ref": frame.ref},
        )
    dimension_columns = [column for _, column in current_dimensions]
    axis_column_by_ref = dict(current_dimensions)
    try:
        axis_columns = [axis_column_by_ref[axis_id] for axis_id in axis_ids]
    except KeyError as exc:
        raise AttributionMaterializationError(
            message="replayed cumulative source omitted a requested business axis",
            context={
                "recoverability_status": "expanded_axis_missing",
                "delta_ref": frame.ref,
                "axis": str(exc.args[0]),
            },
        ) from exc
    current_time = next(
        (
            binding.column
            for binding in current.meta.axis_bindings
            if binding.role == "time_dimension"
        ),
        None,
    )
    baseline_time = next(
        (
            binding.column
            for binding in baseline.meta.axis_bindings
            if binding.role == "time_dimension"
        ),
        None,
    )
    if current_time != baseline_time:
        raise AttributionMaterializationError(
            message="replayed cumulative time coordinates differ across sources",
            context={"recoverability_status": "source_axis_mismatch", "delta_ref": frame.ref},
        )
    original_df = frame._dataframe_copy()
    parent_current_time, parent_baseline_time = _comparison_time_columns(frame, original_df)
    aligned_parts: list[pd.DataFrame] = []
    if current_time is None:
        current_values = current_df[[*dimension_columns, "value"]].rename(
            columns={"value": "current"}
        )
        baseline_values = baseline_df[[*dimension_columns, "value"]].rename(
            columns={"value": "baseline"}
        )
        aligned_parts.append(
            pd.merge(
                current_values,
                baseline_values,
                on=dimension_columns,
                how="outer",
                validate="one_to_one",
            )
        )
    else:
        if parent_current_time is None:
            raise AttributionMaterializationError(
                message="source delta lost its cumulative comparison bucket coordinate",
                context={
                    "recoverability_status": "comparison_coordinate_missing",
                    "delta_ref": frame.ref,
                },
            )
        for _, parent in original_df.iterrows():
            current_key = _json_scalar(parent[parent_current_time])
            baseline_key = _json_scalar(
                parent[parent_baseline_time]
                if parent_baseline_time is not None
                else parent[parent_current_time]
            )
            current_rows = current_df[current_df[current_time].map(_json_scalar) == current_key][
                [*dimension_columns, "value"]
            ].rename(columns={"value": "current"})
            baseline_rows = baseline_df[
                baseline_df[current_time].map(_json_scalar) == baseline_key
            ][[*dimension_columns, "value"]].rename(columns={"value": "baseline"})
            aligned_part = pd.merge(
                current_rows,
                baseline_rows,
                on=dimension_columns,
                how="outer",
                validate="one_to_one",
            )
            aligned_part[parent_current_time] = parent[parent_current_time]
            aligned_parts.append(aligned_part)
    aligned = pd.concat(aligned_parts, ignore_index=True)
    aligned["current"] = pd.to_numeric(aligned["current"], errors="raise").fillna(0.0)
    aligned["baseline"] = pd.to_numeric(aligned["baseline"], errors="raise").fillna(0.0)
    aligned["delta"] = aligned["current"] - aligned["baseline"]
    bucket_column = parent_current_time
    if mode is None:
        output = _single_axis_sum_output(
            aligned,
            axis_column=axis_columns[0],
            value_column="delta",
            bucket_column=bucket_column,
        )
        driver_field = axis_columns[0]
    elif mode == "joint":
        output = _multi_axis_joint_sum_output(
            aligned,
            axis_columns=axis_columns,
            value_column="delta",
            bucket_column=bucket_column,
        )
        driver_field = None
    else:
        output = _multi_axis_hierarchy_output(
            aligned,
            axis_columns=axis_columns,
            value_column="delta",
            bucket_column=bucket_column,
        )
        driver_field = ATTRIBUTION_PATH_COLUMN
    output, reconciliation = _finalize_attribution_output(
        output,
        bucket_column=bucket_column,
        deepest_only=mode == "hierarchy",
    )
    evidence = CumulativeBusinessAxisEvidenceV1(
        anchor=_cumulative_anchor_evidence(frame),
        over_ref=frame.meta.cumulative_attribution.over_ref,
        partitions=_business_axis_partitions(frame, aligned),
    )
    for partition in evidence.partitions:
        rows = output
        for name, value in partition.comparison_key:
            rows = rows[rows[name].map(_json_scalar) == value]
        if mode == "hierarchy" and not rows.empty:
            rows = rows[rows["level"] == rows["level"].max()]
        contribution_sum = float(pd.to_numeric(rows["contribution"], errors="raise").sum())
        if abs(contribution_sum - partition.target_delta) > partition.tolerance:
            raise AttributionMaterializationError(
                message="replayed cumulative business levels do not reconcile to the source delta",
                context={
                    "recoverability_status": "source_revision_changed",
                    "delta_ref": frame.ref,
                    "comparison_key": partition.comparison_key,
                    "target_delta": partition.target_delta,
                    "contribution_sum": contribution_sum,
                },
            )
    semantic_kind: Literal["scalar", "time_series", "segmented", "panel"] = (
        "panel" if bucket_column is not None else "segmented"
    )
    return persist_attribution_frame(
        session=session,
        df=output,
        intent="attribute",
        params={
            "source_ref": frame.ref,
            "axes": axis_ids,
            "mode": mode,
        },
        sources=[frame, current, baseline],
        metric_ids=[frame.meta.metric_id],
        attribution_kind="decomposition",
        driver_field=driver_field,
        value_column="delta",
        contribution_column="contribution",
        method="sum",
        semantic_kind=semantic_kind,
        semantic_model=frame.meta.semantic_model,
        started_at=started_at,
        started_monotonic=started,
        analysis_purpose=analysis_purpose,
        reconciliation=reconciliation,
        axis_ids=axis_ids,
        axis_columns=axis_columns,
        mode=mode,
        bucket_column=bucket_column,
        method_evidence=evidence,
    )


def _attribute_cumulative_business_axes(
    frame: DeltaFrame,
    *,
    axes: list[_SemanticInput[DimensionKind | TimeDimensionKind]],
    axis_ids: list[str],
    mode: AttributionMode | None,
    analysis_purpose: str | None,
    session: Session,
) -> AttributionFrame:
    """Replay the current cumulative sources and reuse ordinary decomposition."""

    assert isinstance(frame.meta, CumulativeDeltaFrameMetaV1)
    _validate_attribution_semantics(frame, axes=axis_ids, session=session)
    current = _load_metric_source(
        session,
        frame.meta.source_current_ref,
        label="current",
        delta=frame,
        missing_axes=axis_ids,
    )
    baseline = _load_metric_source(
        session,
        frame.meta.source_baseline_ref,
        label="baseline",
        delta=frame,
        missing_axes=axis_ids,
    )
    requested_refs = [_dimension_ref(session, axis_id) for axis_id in axis_ids]
    current_replay = recover_observe_replay(current, session=session).with_dimensions(
        requested_refs
    )
    baseline_replay = recover_observe_replay(baseline, session=session).with_dimensions(
        requested_refs
    )
    alignment = recover_alignment_policy(frame)
    expanded_current = current_replay.call_observe(session)
    expanded_baseline = baseline_replay.call_observe(session)
    if isinstance(
        frame.meta.cumulative_attribution.structure,
        DirectCumulativeAttributionV1,
    ):
        return _attribute_direct_cumulative_business_axes(
            frame,
            current=expanded_current,
            baseline=expanded_baseline,
            axis_ids=axis_ids,
            mode=mode,
            analysis_purpose=analysis_purpose,
            session=session,
        )
    expanded_delta = compare(
        expanded_current,
        expanded_baseline,
        alignment=alignment,
        session=session,
    )
    if not isinstance(expanded_delta.meta, CumulativeDeltaFrameMetaV1) or (
        expanded_delta.meta.cumulative_attribution != frame.meta.cumulative_attribution
    ):
        raise AttributionMaterializationError(
            message="replayed cumulative sources changed attribution structure",
            context={
                "recoverability_status": "structure_projection_changed",
                "delta_ref": frame.ref,
                "expanded_delta_ref": expanded_delta.ref,
                "source_refs": (current.ref, baseline.ref),
            },
        )
    method_evidence = CumulativeBusinessAxisEvidenceV1(
        anchor=_cumulative_anchor_evidence(frame),
        over_ref=frame.meta.cumulative_attribution.over_ref,
        partitions=_business_axis_partitions(frame, expanded_delta._dataframe_copy()),
    )
    return _decompose(
        expanded_delta,
        axes=axes,
        mode=mode,
        session=session,
        _intent="attribute",
        _analysis_purpose=analysis_purpose,
        _params_extra={
            "source_ref": frame.ref,
            "alignment_policy": alignment.model_dump(mode="json"),
        },
        _method_evidence=method_evidence,
        _scope_frame=frame,
        _materialization_sources=(expanded_current, expanded_baseline),
    )


def _as_utc(value: object, *, report_timezone: str) -> pd.Timestamp:
    timestamp = pd.Timestamp(cast("Any", value))
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize(report_timezone)
    return timestamp.tz_convert("UTC")


def _grain_end(start: pd.Timestamp, *, grain: Grain, report_timezone: str) -> pd.Timestamp:
    unit = grain.unit
    count = grain.count
    local = _as_utc(start, report_timezone=report_timezone).tz_convert(report_timezone)
    offsets = {
        "second": pd.DateOffset(seconds=count),
        "minute": pd.DateOffset(minutes=count),
        "hour": pd.DateOffset(hours=count),
        "day": pd.DateOffset(days=count),
        "week": pd.DateOffset(weeks=count),
        "month": pd.DateOffset(months=count),
        "quarter": pd.DateOffset(months=3 * count),
        "year": pd.DateOffset(years=count),
    }
    return (local + offsets[str(unit)]).tz_convert("UTC")


def _source_window_end(source: MetricFrame, *, report_timezone: str) -> pd.Timestamp:
    window = source.meta.window
    if not isinstance(window, dict) or "end" not in window:
        raise AttributionMaterializationError(
            message="cumulative source is missing its exact observation window",
            context={"recoverability_status": "source_window_missing", "source_ref": source.ref},
        )
    return _as_utc(window["end"], report_timezone=report_timezone)


def _source_dimension_columns(source: MetricFrame) -> tuple[str, ...]:
    return tuple(
        binding.column for binding in source.meta.axis_bindings if binding.role == "dimension"
    )


def _comparison_time_columns(
    frame: DeltaFrame, dataframe: pd.DataFrame
) -> tuple[str | None, str | None]:
    current: str | None = None
    axes = frame.meta.alignment.get("axes")
    if isinstance(axes, dict):
        for axis in axes.values():
            if isinstance(axis, dict) and axis.get("role") == "time":
                candidate = axis.get("column")
                if isinstance(candidate, str) and candidate in dataframe.columns:
                    current = candidate
                    break
    if current is None and "bucket_start_a" in dataframe.columns:
        current = "bucket_start_a"
    configured = frame.meta.alignment.get("baseline_bucket_column")
    baseline = (
        configured if isinstance(configured, str) and configured in dataframe.columns else None
    )
    if baseline is None and current is not None and f"{current}_b" in dataframe.columns:
        baseline = f"{current}_b"
    if baseline is None and "bucket_start_b" in dataframe.columns:
        baseline = "bucket_start_b"
    return current, baseline


def _comparison_endpoint(
    row: pd.Series,
    source: MetricFrame,
    column: str | None,
    *,
    bridge_grain: Grain,
    report_timezone: str,
) -> pd.Timestamp:
    if column is None:
        return _source_window_end(source, report_timezone=report_timezone)
    return min(
        _grain_end(
            _as_utc(row[column], report_timezone=report_timezone),
            grain=bridge_grain,
            report_timezone=report_timezone,
        ),
        _source_window_end(source, report_timezone=report_timezone),
    )


def _reset_scope_start(
    endpoint: pd.Timestamp, *, reset_grain: str, report_timezone: str
) -> pd.Timestamp:
    # ``endpoint`` is the exclusive end of the cumulative bucket.  At an
    # exact reset boundary the bucket belongs to the preceding period.
    local = (endpoint - pd.Timedelta(nanoseconds=1)).tz_convert(report_timezone)
    if reset_grain == "week":
        start = local.normalize() - pd.DateOffset(days=local.weekday())
    elif reset_grain == "month":
        start = local.replace(day=1).normalize()
    elif reset_grain == "quarter":
        start = local.replace(month=((local.month - 1) // 3) * 3 + 1, day=1).normalize()
    elif reset_grain == "year":
        start = local.replace(month=1, day=1).normalize()
    else:
        raise AssertionError("unknown grain-to-date reset grain")
    return start.tz_convert("UTC")


def _observe_base_flow(
    source: MetricFrame,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    frame: DeltaFrame,
    session: Session,
) -> MetricFrame:
    from marivo.analysis.intents.observe import observe

    assert isinstance(frame.meta, CumulativeDeltaFrameMetaV1)
    contract = frame.meta.cumulative_attribution
    assert isinstance(contract.bridge, AvailableCumulativeBridgeV1)
    base_path = frame.meta.cumulative.get("base")
    if not isinstance(base_path, str):
        raise AttributionMaterializationError(
            message="direct cumulative source is missing its base metric ref",
            context={"recoverability_status": "base_metric_missing", "delta_ref": frame.ref},
        )
    replay = recover_observe_replay(source, session=session)
    over_ref = session.catalog.require(ref_factory.time_dimension(contract.over_ref.path)).ref
    return observe(
        session.catalog.require(ref_factory.metric(base_path)).ref,
        time_scope=_new_time_scope(start=start.isoformat(), end=end.isoformat()),
        grain=to_temporal_grain(contract.bridge.value.grain),
        dimensions=cast("Any", list(replay.dimensions) or None),
        slice_by=cast("Any", replay.slice_by or None),
        time_dimension=over_ref,
        cohort=replay.cohort,
        session=session,
    )


def _flow_rows(
    flow: MetricFrame,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    report_timezone: str,
    bridge_grain: Grain,
) -> pd.DataFrame:
    dataframe = flow._dataframe_copy()
    time_bindings = [
        binding for binding in flow.meta.axis_bindings if binding.role == "time_dimension"
    ]
    if len(time_bindings) != 1 or "value" not in dataframe.columns:
        raise AttributionMaterializationError(
            message="base-flow replay returned a non-canonical metric shape",
            context={"recoverability_status": "base_flow_shape_invalid", "flow_ref": flow.ref},
        )
    time_column = time_bindings[0].column
    output = dataframe.rename(columns={time_column: "__flow_bucket", "value": "__flow_value"})
    output["flow_interval_start"] = output["__flow_bucket"].map(
        lambda value: max(_as_utc(value, report_timezone=report_timezone), start)
    )
    output["flow_interval_end"] = output["__flow_bucket"].map(
        lambda value: min(
            _grain_end(
                _as_utc(value, report_timezone=report_timezone),
                grain=bridge_grain,
                report_timezone=report_timezone,
            ),
            end,
        )
    )
    return output[output["flow_interval_start"] < output["flow_interval_end"]].reset_index(
        drop=True
    )


def _filter_parent_dimensions(
    dataframe: pd.DataFrame, parent: pd.Series, source: MetricFrame
) -> pd.DataFrame:
    output = dataframe
    for column in _source_dimension_columns(source):
        if column in parent.index and column in output.columns:
            value = parent[column]
            output = (
                output[output[column].isna()] if pd.isna(value) else output[output[column] == value]
            )
    return output.copy()


def _parent_coordinates(
    row: pd.Series,
    *,
    current_time_column: str | None,
    baseline_time_column: str | None,
    source: MetricFrame,
    over_column: str,
) -> dict[str, object]:
    coordinates: dict[str, object] = {}
    for column in _source_dimension_columns(source):
        if column in row.index:
            coordinates[column] = row[column]
    if current_time_column is not None:
        output = (
            "comparison_bucket_start" if current_time_column == over_column else current_time_column
        )
        coordinates[output] = row[current_time_column]
    if baseline_time_column is not None:
        output = (
            "comparison_baseline_bucket_start"
            if baseline_time_column in (over_column, current_time_column)
            else baseline_time_column
        )
        coordinates[output] = row[baseline_time_column]
    return coordinates


def _merge_flow_sides(
    current: pd.DataFrame,
    baseline: pd.DataFrame,
    *,
    dimension_columns: tuple[str, ...],
) -> pd.DataFrame:
    keys = [*dimension_columns, "flow_interval_start", "flow_interval_end"]
    current_side = current[[*keys, "__flow_bucket", "__flow_value"]].rename(
        columns={"__flow_bucket": "__current_bucket", "__flow_value": "current_value"}
    )
    baseline_side = baseline[[*keys, "__flow_bucket", "__flow_value"]].rename(
        columns={"__flow_bucket": "__baseline_bucket", "__flow_value": "baseline_value"}
    )
    return pd.merge(current_side, baseline_side, on=keys, how="outer", validate="one_to_one")


def _finalize_flow_partition(
    rows: list[dict[str, object]],
    *,
    coordinates: dict[str, object],
    target_delta: float,
    over_column: str,
) -> tuple[list[dict[str, object]], CumulativeAttributionPartitionV1]:
    output = pd.DataFrame(rows)
    tolerance = max(1e-12, 1e-9 * max(abs(target_delta), 1.0))
    if output.empty:
        contribution_sum = 0.0
    else:
        contribution = pd.to_numeric(output["contribution"], errors="raise")
        contribution_sum = float(contribution.sum())
        positive_pool = float(contribution[contribution > 0].sum())
        negative_pool = float(-contribution[contribution < 0].sum())
        output["share_of_total_delta"] = (
            contribution / target_delta if target_delta != 0 else float("nan")
        )
        output["share_of_positive_pool"] = contribution.where(contribution > 0) / positive_pool
        output["share_of_negative_pool"] = -contribution.where(contribution < 0) / negative_pool
        side_order = {"current": 0, "baseline": 1, "both": 2}
        output["__abs"] = contribution.abs()
        output["__side_order"] = output["source_side"].map(side_order)
        output = output.sort_values(
            ["__abs", over_column, "flow_interval_start", "__side_order"],
            ascending=[False, True, True, True],
            kind="mergesort",
        ).drop(columns=["__abs", "__side_order"])
        output["rank"] = range(1, len(output) + 1)
    residual = target_delta - contribution_sum
    if abs(residual) > tolerance:
        raise AttributionMaterializationError(
            message="cumulative base-flow bridge does not reconcile to the observed delta",
            context={
                "recoverability_status": "source_revision_changed",
                "target_delta": target_delta,
                "contribution_sum": contribution_sum,
                "residual": residual,
                "tolerance": tolerance,
            },
        )
    comparison_key = tuple((name, _json_scalar(value)) for name, value in coordinates.items())
    partition = CumulativeAttributionPartitionV1(
        comparison_key=comparison_key,
        target_delta=target_delta,
        contribution_sum=contribution_sum,
        row_count=len(output),
        residual=residual,
        tolerance=tolerance,
    )
    return cast("list[dict[str, object]]", output.to_dict(orient="records")), partition


def _attribute_cumulative_accumulation_time(
    frame: DeltaFrame,
    *,
    analysis_purpose: str | None,
    session: Session,
) -> AttributionFrame:
    assert isinstance(frame.meta, CumulativeDeltaFrameMetaV1)
    contract = frame.meta.cumulative_attribution
    if not isinstance(contract.bridge, AvailableCumulativeBridgeV1) or not isinstance(
        contract.structure, DirectCumulativeAttributionV1
    ):
        raise AssertionError("blocked cumulative time route reached execution")
    started_at = datetime.now(UTC)
    started = monotonic()
    current = _load_metric_source(
        session,
        frame.meta.source_current_ref,
        label="current",
        delta=frame,
        missing_axes=[contract.over_ref.path],
    )
    baseline = _load_metric_source(
        session,
        frame.meta.source_baseline_ref,
        label="baseline",
        delta=frame,
        missing_axes=[contract.over_ref.path],
    )
    parent_df = frame._dataframe_copy()
    current_time_column, baseline_time_column = _comparison_time_columns(frame, parent_df)
    report_timezone = contract.bridge.value.report_timezone
    bridge_grain = contract.bridge.value.grain
    over_column = "bucket_start"
    dimension_columns = _source_dimension_columns(current)
    if dimension_columns != _source_dimension_columns(baseline):
        raise AttributionMaterializationError(
            message="cumulative source business coordinates changed across observations",
            context={
                "recoverability_status": "source_axis_mismatch",
                "current_ref": current.ref,
                "baseline_ref": baseline.ref,
            },
        )
    anchor = _cumulative_anchor_evidence(frame)
    all_rows: list[dict[str, object]] = []
    partitions: list[CumulativeAllHistoryPartitionV1 | CumulativeComparablePeriodPartitionV1] = []
    flow_sources: list[MetricFrame] = []

    def query(
        source: MetricFrame, start: pd.Timestamp, end: pd.Timestamp, parent: pd.Series
    ) -> pd.DataFrame:
        if start >= end:
            return pd.DataFrame(
                columns=[
                    *dimension_columns,
                    "__flow_bucket",
                    "__flow_value",
                    "flow_interval_start",
                    "flow_interval_end",
                ]
            )
        observed = _observe_base_flow(
            source,
            start=start,
            end=end,
            frame=frame,
            session=session,
        )
        flow_sources.append(observed)
        return _filter_parent_dimensions(
            _flow_rows(
                observed,
                start=start,
                end=end,
                report_timezone=report_timezone,
                bridge_grain=bridge_grain,
            ),
            parent,
            source,
        )

    for _, parent in parent_df.iterrows():
        coordinates = _parent_coordinates(
            parent,
            current_time_column=current_time_column,
            baseline_time_column=baseline_time_column,
            source=current,
            over_column=over_column,
        )
        target_delta = float(parent["delta"])
        partition_rows: list[dict[str, object]] = []
        current_end = (
            _as_utc(parent["current_evaluation_end"], report_timezone=report_timezone)
            if isinstance(anchor, AllHistoryAnchorSemanticsV1)
            else _comparison_endpoint(
                parent,
                current,
                current_time_column,
                bridge_grain=bridge_grain,
                report_timezone=report_timezone,
            )
        )
        baseline_end = (
            _as_utc(parent["baseline_evaluation_end"], report_timezone=report_timezone)
            if isinstance(anchor, AllHistoryAnchorSemanticsV1)
            else _comparison_endpoint(
                parent,
                baseline,
                baseline_time_column,
                bridge_grain=bridge_grain,
                report_timezone=report_timezone,
            )
        )
        if isinstance(anchor, AllHistoryAnchorSemanticsV1):
            source = current if current_end > baseline_end else baseline
            start = min(current_end, baseline_end)
            end = max(current_end, baseline_end)
            side = "current" if current_end > baseline_end else "baseline"
            for _, flow_row in query(source, start, end, parent).iterrows():
                value = float(flow_row["__flow_value"])
                partition_rows.append(
                    {
                        **coordinates,
                        over_column: flow_row["__flow_bucket"],
                        "flow_interval_start": flow_row["flow_interval_start"],
                        "flow_interval_end": flow_row["flow_interval_end"],
                        "source_side": side,
                        "effect_kind": "between_cutoffs",
                        "current_value": value if side == "current" else None,
                        "baseline_value": value if side == "baseline" else None,
                        "contribution": value if side == "current" else -value,
                    }
                )
        else:
            if isinstance(anchor, GrainToDateAnchorSemanticsV1):
                current_start = _reset_scope_start(
                    current_end,
                    reset_grain=anchor.reset_grain,
                    report_timezone=report_timezone,
                )
                baseline_start = _reset_scope_start(
                    baseline_end,
                    reset_grain=anchor.reset_grain,
                    report_timezone=report_timezone,
                )
            else:
                current_start = current_end - pd.Timedelta(seconds=anchor.span_seconds)
                baseline_start = baseline_end - pd.Timedelta(seconds=anchor.span_seconds)
            current_flow = query(current, current_start, current_end, parent)
            baseline_flow = query(baseline, baseline_start, baseline_end, parent)
            merged = _merge_flow_sides(
                current_flow,
                baseline_flow,
                dimension_columns=dimension_columns,
            )
            for _, flow_row in merged.iterrows():
                has_current = pd.notna(flow_row.get("current_value"))
                has_baseline = pd.notna(flow_row.get("baseline_value"))
                if has_current and has_baseline:
                    side = "both"
                    effect = (
                        "shared_scope_change"
                        if isinstance(anchor, GrainToDateAnchorSemanticsV1)
                        else "retained_change"
                    )
                elif has_current:
                    side = "current"
                    effect = (
                        "current_scope"
                        if isinstance(anchor, GrainToDateAnchorSemanticsV1)
                        else "entering"
                    )
                else:
                    side = "baseline"
                    effect = (
                        "baseline_scope"
                        if isinstance(anchor, GrainToDateAnchorSemanticsV1)
                        else "leaving"
                    )
                current_value = float(flow_row["current_value"]) if has_current else None
                baseline_value = float(flow_row["baseline_value"]) if has_baseline else None
                bucket = (
                    flow_row["__current_bucket"]
                    if pd.notna(flow_row.get("__current_bucket"))
                    else flow_row["__baseline_bucket"]
                )
                partition_rows.append(
                    {
                        **coordinates,
                        over_column: bucket,
                        "flow_interval_start": flow_row["flow_interval_start"],
                        "flow_interval_end": flow_row["flow_interval_end"],
                        "source_side": side,
                        "effect_kind": effect,
                        "current_value": current_value,
                        "baseline_value": baseline_value,
                        "contribution": (current_value or 0.0) - (baseline_value or 0.0),
                    }
                )
        finalized, partition = _finalize_flow_partition(
            partition_rows,
            coordinates=coordinates,
            target_delta=target_delta,
            over_column=over_column,
        )
        if isinstance(anchor, AllHistoryAnchorSemanticsV1):
            partition = CumulativeAllHistoryPartitionV1(
                **partition.model_dump(),
                current_evaluation_end=current_end.to_pydatetime(),
                baseline_evaluation_end=baseline_end.to_pydatetime(),
            )
        else:
            partition = CumulativeComparablePeriodPartitionV1(
                **partition.model_dump(),
                current_scope_start=current_start.to_pydatetime(),
                current_scope_end=current_end.to_pydatetime(),
                baseline_scope_start=baseline_start.to_pydatetime(),
                baseline_scope_end=baseline_end.to_pydatetime(),
            )
        all_rows.extend(finalized)
        partitions.append(partition)

    evidence: (
        CumulativeAllHistoryFlowEvidenceV1
        | CumulativeGrainToDateFlowEvidenceV1
        | CumulativeTrailingFlowEvidenceV1
    )
    if isinstance(anchor, AllHistoryAnchorSemanticsV1):
        evidence = CumulativeAllHistoryFlowEvidenceV1(
            anchor=anchor,
            over_ref=contract.over_ref,
            bridge_grain=contract.bridge.value,
            partitions=tuple(cast("CumulativeAllHistoryPartitionV1", item) for item in partitions),
        )
    elif isinstance(anchor, GrainToDateAnchorSemanticsV1):
        evidence = CumulativeGrainToDateFlowEvidenceV1(
            anchor=anchor,
            over_ref=contract.over_ref,
            bridge_grain=contract.bridge.value,
            partitions=tuple(
                cast("CumulativeComparablePeriodPartitionV1", item) for item in partitions
            ),
        )
    else:
        evidence = CumulativeTrailingFlowEvidenceV1(
            anchor=anchor,
            over_ref=contract.over_ref,
            bridge_grain=contract.bridge.value,
            partitions=tuple(
                cast("CumulativeComparablePeriodPartitionV1", item) for item in partitions
            ),
        )
    columns = [
        *((name for name, _ in partitions[0].comparison_key) if partitions else ()),
        over_column,
        "flow_interval_start",
        "flow_interval_end",
        "source_side",
        "effect_kind",
        "current_value",
        "baseline_value",
        "contribution",
        "rank",
        "share_of_total_delta",
        "share_of_positive_pool",
        "share_of_negative_pool",
    ]
    output = pd.DataFrame(all_rows, columns=columns)
    max_residual = max((abs(item.residual) for item in partitions), default=0.0)
    single = partitions[0] if len(partitions) == 1 else None
    reconciliation = AttributionReconciliation(
        partition_count=len(partitions),
        total_delta=single.target_delta if single is not None else None,
        contribution_sum=single.contribution_sum if single is not None else None,
        one_sided_contribution_sum=None,
        unattributed_contribution_sum=single.residual if single is not None else None,
        residual=single.residual if single is not None else None,
        max_abs_residual=max_residual,
    )
    unique_sources: list[BaseFrame] = []
    source_refs: set[str] = set()
    for lineage_source in [frame, *flow_sources]:
        if lineage_source.ref not in source_refs:
            unique_sources.append(lineage_source)
            source_refs.add(lineage_source.ref)
    bucket_column = next(iter(dict(partitions[0].comparison_key)), None) if partitions else None
    return persist_attribution_frame(
        session=session,
        df=output,
        intent="attribute",
        params={
            "source_ref": frame.ref,
            "axis": contract.over_ref.path,
        },
        sources=unique_sources,
        metric_ids=[frame.meta.metric_id],
        attribution_kind="decomposition",
        driver_field=over_column,
        value_column=None,
        contribution_column="contribution",
        method="sum",
        semantic_kind=frame.meta.semantic_kind,
        semantic_model=frame.meta.semantic_model,
        started_at=started_at,
        started_monotonic=started,
        analysis_purpose=analysis_purpose,
        reconciliation=reconciliation,
        axis_ids=[contract.over_ref.path],
        axis_columns=[over_column],
        mode=None,
        bucket_column=bucket_column,
        method_evidence=evidence,
        row_contract_version="cumulative-flow-attribution-rows/v1",
    )


def _attribute_nonadditive(
    frame: DeltaFrame,
    *,
    basis: DistinctAttributionBasisV1 | QuantileAttributionBasisV1,
    axis_ids: list[str],
    mode: AttributionMode | None,
    analysis_purpose: str | None,
    session: Session,
) -> AttributionFrame:
    """Execute graph-owned non-additive attribution with independent endpoints."""
    assert isinstance(frame.meta, DeltaFrameMeta)
    started_at = datetime.now(UTC)
    started = monotonic()
    current = _load_metric_source(
        session,
        frame.meta.source_current_ref,
        label="current",
        delta=frame,
        missing_axes=axis_ids,
    )
    baseline = _load_metric_source(
        session,
        frame.meta.source_baseline_ref,
        label="baseline",
        delta=frame,
        missing_axes=axis_ids,
    )
    for label, source in (("current", current), ("baseline", baseline)):
        graph = source.meta.expression_graph
        if graph is None:
            raise AttributionMaterializationError(
                message=f"attribute {label} source is missing its expression graph",
                context={"source_ref": source.ref, "label": label},
            )
        try:
            basis.authority.validate_graph(graph)
        except ValueError as exc:
            raise AttributionMaterializationError(
                message=f"attribute {label} source graph differs from the persisted basis",
                context={
                    "recoverability_status": "basis_source_graph_mismatch",
                    "source_ref": source.ref,
                    "label": label,
                },
            ) from exc
    current_endpoint = (
        recover_observe_replay(current, session=session).without_dimensions().call_observe(session)
    )
    baseline_endpoint = (
        recover_observe_replay(baseline, session=session).without_dimensions().call_observe(session)
    )
    endpoint_delta = compare(
        current_endpoint,
        baseline_endpoint,
        alignment=recover_alignment_policy(frame),
        session=session,
    )
    nonadditive_mode: Literal["joint", "multiresolution"] | None = (
        mode if mode == "joint" or mode == "multiresolution" else None
    )
    if isinstance(basis, DistinctAttributionBasisV1):
        result = attribute_distinct(
            current=current,
            baseline=baseline,
            endpoint_delta=endpoint_delta,
            basis=basis,
            axis_ids=axis_ids,
            mode=nonadditive_mode,
            source_delta_ref=frame.meta.artifact_id or frame.ref,
            session=session,
        )
        method = "distinct_membership"
    else:
        reproduction = basis.reproduction
        if reproduction.status != "reproducible":
            raise AssertionError("blocked quantile admission reached execution")
        quantile_executor = (
            attribute_exact_quantile
            if reproduction.distribution_representation == "exact_value_frequency"
            else attribute_qdigest_quantile
        )
        result = quantile_executor(
            current=current,
            baseline=baseline,
            endpoint_delta=endpoint_delta,
            basis=basis,
            axis_ids=axis_ids,
            mode=nonadditive_mode,
            source_delta_ref=frame.meta.artifact_id or frame.ref,
            session=session,
        )
        method = "quantile_replacement"
    params = {
        "source_ref": frame.ref,
        "independent_endpoint_delta_ref": endpoint_delta.ref,
        "axes": axis_ids,
        "mode": nonadditive_mode,
        "method": method,
    }
    extra_issues = []
    if (
        isinstance(result.method_evidence, QuantileReplacementEvidenceV1)
        and result.method_evidence.source_mode == "approximate"
        and result.method_evidence.source_error_bound is None
    ):
        extra_issues.append(
            ComparabilityIssue(
                issue_id=make_issue_id(
                    artifact_id=frame.ref,
                    kind="comparability_approximate",
                    source_refs=(current.ref, baseline.ref),
                ),
                kind="comparability_approximate",
                severity="warning",
                source_refs=(current.ref, baseline.ref),
                left_scope=current.meta.analysis_scope or AnalysisScope(),
                right_scope=baseline.meta.analysis_scope or AnalysisScope(),
                incompatible_fields=("source_error_bound",),
                approximation_details=(
                    "Trino qdigest source error bound is not declared by the persisted "
                    "datasource capability",
                ),
            )
        )
    return persist_attribution_frame(
        session=session,
        df=result.dataframe,
        intent="attribute",
        params=params,
        sources=[frame, endpoint_delta],
        metric_ids=[frame.meta.metric_id],
        attribution_kind="decomposition",
        driver_field=(
            result.axis_columns[0]
            if len(result.axis_columns) == 1
            else ATTRIBUTION_PATH_COLUMN
            if nonadditive_mode == "multiresolution"
            else None
        ),
        value_column=None,
        contribution_column="contribution",
        method=method,
        semantic_kind=frame.meta.semantic_kind,
        semantic_model=frame.meta.semantic_model,
        started_at=started_at,
        started_monotonic=started,
        analysis_purpose=analysis_purpose,
        extra_issues=extra_issues,
        reconciliation=result.reconciliation,
        axis_ids=axis_ids,
        axis_columns=result.axis_columns,
        mode=nonadditive_mode,
        bucket_column=result.bucket_column,
        method_evidence=result.method_evidence,
    )


def attribute(
    frame: DeltaFrame,
    *,
    axes: list[_SemanticInput[DimensionKind | TimeDimensionKind]],
    mode: AttributionMode | None = None,
    analysis_purpose: str | None = None,
    session: Session | None = None,
) -> AttributionFrame:
    """Attribute a DeltaFrame's movement over explicit deterministic axes."""
    resolved_session = resolve_session(session)
    ensure_session_can_execute(resolved_session)
    if not isinstance(frame, DeltaFrame):
        raise SemanticKindMismatchError(message="attribute requires a DeltaFrame input")
    # This funnel gate must stay BEFORE the cumulative read below: cumulative
    # no longer exists on FunnelDeltaFrameMeta, so narrowing the union first is
    # required (not a cosmetic reorder).
    if not isinstance(frame.meta, DeltaFrameMeta):
        raise SemanticKindMismatchError(
            message="generic attribute requires a metric DeltaFrame; DeltaFrame[funnel] "
            "attributes via session.attribute(<DeltaFrame[funnel]>, target=...)",
            context={"semantic_kind": frame.meta.semantic_kind},
        )
    ensure_frame_in_session(frame, session=resolved_session, label="attribute frame")
    axis_ids = _normalize_attribute_axes(resolved_session, axes)
    if isinstance(frame.meta, CumulativeDeltaFrameMetaV1):
        axis_refs = tuple(
            RefPayloadV1.from_ref(_dimension_ref(resolved_session, axis_id)) for axis_id in axis_ids
        )
        route, route_admission = select_cumulative_attribution_route(
            frame.meta.cumulative_attribution,
            axis_refs,
        )
        if route_admission.status == "blocked":
            raise AttributeAdmissionBlockedError(
                message=f"cumulative attribute route is blocked: {route_admission.blocker}",
                expected=f"DeltaFrame.contract().cumulative_attribution.{route}.status='supported'",
                received=(f"route={route!r} status='blocked' blocker={route_admission.blocker!r}"),
                location="session.attribute",
                repair=route_admission.repair,
                context={
                    "delta_ref": frame.ref,
                    "route": route,
                    "blocker": route_admission.blocker,
                    "axis_refs": tuple(item.path for item in axis_refs),
                },
            )
        admission = _attribute_admission(frame.meta)
        assert admission.status == "supported"
        validated_mode = validate_attribution_mode(
            axis_ids,
            mode,
            intent="attribute",
            legal_modes=admission.mode.multiple_axes,
        )
        if route == "business_axes":
            return _attribute_cumulative_business_axes(
                frame,
                axes=axes,
                axis_ids=axis_ids,
                mode=validated_mode,
                analysis_purpose=analysis_purpose,
                session=resolved_session,
            )
        return _attribute_cumulative_accumulation_time(
            frame,
            analysis_purpose=analysis_purpose,
            session=resolved_session,
        )
    if frame.meta.cumulative is not None:
        raise AttributionMaterializationError(
            message="cumulative delta uses an unsupported artifact schema",
            context={
                "recoverability_status": "unsupported_artifact_schema",
                "delta_ref": frame.ref,
                "repair": "Re-run observe and compare under the current environment.",
            },
        )
    admission = _attribute_admission(frame.meta)
    if admission.status == "blocked":
        blocked_context: dict[str, object] = {
            "delta_ref": frame.ref,
            "blocker": admission.blocker,
            "aggregation": frame.meta.aggregation,
            "composition_kind": (
                frame.meta.composition.get("kind")
                if isinstance(frame.meta.composition, dict)
                else None
            ),
        }
        basis = frame.meta.attribution_basis
        if basis is not None:
            reproduction = basis.reproduction
            blocked_context["source_method"] = getattr(reproduction, "source_method", None)
            blocked_context["source_mode"] = getattr(reproduction, "source_mode", None)
        raise AttributeAdmissionBlockedError(
            message=f"attribute is blocked: {admission.blocker}",
            expected="DeltaFrame.contract().attribute_admission.status='supported'",
            received=f"status='blocked' blocker={admission.blocker!r}",
            location="session.attribute",
            repair=admission.repair,
            context=blocked_context,
        )
    validated_mode = validate_attribution_mode(
        axis_ids,
        mode,
        intent="attribute",
        legal_modes=admission.mode.multiple_axes,
    )
    if frame.meta.attribution_basis is not None:
        return _attribute_nonadditive(
            frame,
            basis=frame.meta.attribution_basis,
            axis_ids=axis_ids,
            mode=validated_mode,
            analysis_purpose=analysis_purpose,
            session=resolved_session,
        )
    missing_axes = _missing_axis_ids(frame, axis_ids)
    if not missing_axes:
        return decompose(
            frame,
            axes=axes,
            mode=validated_mode,
            session=resolved_session,
            _intent="attribute",
            _analysis_purpose=analysis_purpose,
            _params_extra={
                "materialization_status": "not_required",
                "original_delta_ref": frame.ref,
            },
        )

    _validate_attribution_semantics(frame, axes=axis_ids, session=resolved_session)
    current = _load_metric_source(
        resolved_session,
        frame.meta.source_current_ref,
        label="current",
        delta=frame,
        missing_axes=missing_axes,
    )
    baseline = _load_metric_source(
        resolved_session,
        frame.meta.source_baseline_ref,
        label="baseline",
        delta=frame,
        missing_axes=missing_axes,
    )
    missing_axis_refs = [_dimension_ref(resolved_session, axis) for axis in missing_axes]
    current_replay = recover_observe_replay(current, session=resolved_session).with_dimensions(
        missing_axis_refs
    )
    baseline_replay = recover_observe_replay(baseline, session=resolved_session).with_dimensions(
        missing_axis_refs
    )
    alignment = recover_alignment_policy(frame)

    expanded_current = current_replay.call_observe(resolved_session)
    expanded_baseline = baseline_replay.call_observe(resolved_session)
    expanded_delta = compare(
        expanded_current,
        expanded_baseline,
        alignment=alignment,
        session=resolved_session,
    )
    return decompose(
        expanded_delta,
        axes=axes,
        mode=validated_mode,
        session=resolved_session,
        _intent="attribute",
        _analysis_purpose=analysis_purpose,
        _params_extra={
            "materialization_status": "expanded",
            "original_delta_ref": frame.ref,
            "missing_axes": missing_axes,
            "expanded_current_ref": expanded_current.ref,
            "expanded_baseline_ref": expanded_baseline.ref,
            "expanded_delta_ref": expanded_delta.ref,
            "alignment_policy": alignment.model_dump(mode="json"),
        },
    )
