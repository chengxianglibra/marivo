"""Public deterministic attribution composite operator."""

from __future__ import annotations

from datetime import UTC, datetime
from time import monotonic
from typing import Literal

from marivo.analysis.attribution_contract import (
    DistinctAttributionBasisV1,
    QuantileAttributionBasisV1,
)
from marivo.analysis.errors import (
    AttributeAdmissionBlockedError,
    AttributionMaterializationError,
    CumulativeFrameUnsupportedError,
    SemanticKindMismatchError,
)
from marivo.analysis.evidence.identity import make_issue_id
from marivo.analysis.evidence.types import AnalysisScope, ComparabilityIssue
from marivo.analysis.frames._attribution_columns import ATTRIBUTION_PATH_COLUMN
from marivo.analysis.frames.attribution import (
    AttributionFrame,
    QuantileReplacementEvidenceV1,
)
from marivo.analysis.frames.delta import DeltaFrame, DeltaFrameMeta, _attribute_admission
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
    _effective_component_axis_column,
    _normalize_axis_boundary,
    _validate_attribution_semantics,
    decompose,
)
from marivo.analysis.session.core import Session, ensure_session_can_execute
from marivo.refs import DimensionKind, TimeDimensionKind
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
    if not isinstance(frame.meta, DeltaFrameMeta):
        raise SemanticKindMismatchError(
            message="generic attribute requires a metric DeltaFrame; DeltaFrame[funnel] "
            "attributes via session.attribute(<DeltaFrame[funnel]>, target=...)",
            context={"semantic_kind": frame.meta.semantic_kind},
        )
    if frame.meta.cumulative is not None:
        raise CumulativeFrameUnsupportedError(
            intent="attribute",
            frame_ref=frame.ref,
            metric_id=frame.meta.metric_id,
            cumulative=frame.meta.cumulative,
        )
    ensure_frame_in_session(frame, session=resolved_session, label="attribute frame")
    axis_ids = _normalize_attribute_axes(resolved_session, axes)
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
