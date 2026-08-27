"""Shared helpers for analysis derived intents."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
import hashlib
import json
import secrets
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from time import monotonic
from typing import TYPE_CHECKING, Any, Literal, cast

import pandas as pd
from pandas.api.types import is_numeric_dtype

from marivo._compat import UTC
from marivo.analysis._semantic_persistence import job_semantics_from_frames
from marivo.analysis.attribution_contract import AttributionAxisBindingV1, AttributionMode
from marivo.analysis.candidate_lineage import CandidateOrigin, merge_candidate_origins
from marivo.analysis.errors import (
    AnalysisRepair,
    CrossSessionFrameError,
    SemanticKindMismatchError,
)
from marivo.analysis.evidence.pipeline import (
    CommitInputs,
    CommitParams,
    CommitSemanticAnchors,
    commit_result,
)
from marivo.analysis.evidence.types import ArtifactIssue, Subject
from marivo.analysis.frames.attribution import (
    AttributionFrame,
    AttributionFrameMeta,
    AttributionMethodEvidenceV1,
    AttributionReconciliation,
    AttributionTopKSelectionV1,
    CompleteHierarchyScopeV1,
    CumulativeBusinessAxisEvidenceV1,
    HierarchyResolutionEvidenceV1,
    RollupHierarchyEvidenceV1,
    cumulative_reconciliation_from_partitions,
    reconcile_cumulative_business_evidence,
    validate_cumulative_flow_attribution_rows,
    validate_generic_attribution_rows,
)
from marivo.analysis.frames.base import BaseFrame
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.analysis.session._runtime import (
    persist_job_record,
    register_frame_artifact,
    require_current_session,
)
from marivo.analysis.session.core import Session
from marivo.refs import RefPayloadV1
from marivo.refs import ref as ref_factory

if TYPE_CHECKING:
    from marivo.analysis.frames.metric import MetricFrame


@dataclass(frozen=True)
class ResolvedMetricValueColumn:
    """Public and internal names for one MetricFrame value column."""

    public_name: str
    internal_name: str


def resolve_session(session: Session | None) -> Session:
    return session if session is not None else require_current_session()


def gen_ref(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(4)}"


def params_digest(params: dict[str, Any]) -> str:
    body = json.dumps(params, sort_keys=True, default=str).encode("utf-8")
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


def ensure_frame_in_session(frame: BaseFrame, *, session: Session, label: str) -> None:
    if frame.meta.session_id != session.id:
        raise CrossSessionFrameError(
            message=(f"{label} belongs to session {frame.meta.session_id!r}, not {session.id!r}"),
        )


def require_numeric_column(
    df: pd.DataFrame,
    value: str | None,
    *,
    purpose: str,
    repair: AnalysisRepair | None = None,
) -> str:
    if value is not None:
        if value not in df.columns:
            raise SemanticKindMismatchError(
                message=f"{purpose} value column {value!r} does not exist",
                context={"columns": list(df.columns)},
                repair=repair,
            )
        if not is_numeric_dtype(df[value]):
            raise SemanticKindMismatchError(
                message=f"{purpose} value column {value!r} is not numeric",
                context={"column": value, "dtype": str(df[value].dtype)},
                repair=repair,
            )
        return value

    numeric = [column for column in df.columns if is_numeric_dtype(df[column])]
    if len(numeric) != 1:
        raise SemanticKindMismatchError(
            message=f"{purpose} requires exactly one numeric column when value is omitted",
            context={"numeric_columns": numeric},
            repair=repair,
        )
    return str(numeric[0])


def resolve_metric_value_column(
    frame: MetricFrame,
    df: pd.DataFrame,
    value: str | None,
    *,
    parameter: str,
    purpose: str,
) -> ResolvedMetricValueColumn:
    """Resolve one public MetricFrame value name to its internal storage column."""
    valid_values = frame.value_columns
    if len(valid_values) != 1:
        raise SemanticKindMismatchError(
            message=f"{purpose} requires exactly one frame value column",
            context={"parameter": parameter, "valid_values": list(valid_values)},
        )

    public_name = valid_values[0]
    public_columns = frame.columns
    internal_columns = [str(column) for column in df.columns]
    try:
        value_index = public_columns.index(public_name)
        internal_name = internal_columns[value_index]
    except (IndexError, ValueError) as exc:
        raise SemanticKindMismatchError(
            message=f"{purpose} frame value column {public_name!r} cannot be resolved",
            context={"parameter": parameter, "valid_values": list(valid_values)},
        ) from exc

    if value not in {None, public_name, internal_name}:
        raise SemanticKindMismatchError(
            message=(
                f"{purpose} value column {value!r} is invalid. "
                f"Valid {parameter} values: {valid_values!r}"
            ),
            context={
                "parameter": parameter,
                "received": value,
                "valid_values": list(valid_values),
            },
        )

    if internal_name not in df.columns or not is_numeric_dtype(df[internal_name]):
        dtype = str(df[internal_name].dtype) if internal_name in df.columns else None
        raise SemanticKindMismatchError(
            message=f"{purpose} value column {public_name!r} is not numeric",
            context={
                "parameter": parameter,
                "column": public_name,
                "dtype": dtype,
                "valid_values": list(valid_values),
            },
        )
    return ResolvedMetricValueColumn(
        public_name=public_name,
        internal_name=internal_name,
    )


def first_non_numeric_column(df: pd.DataFrame) -> str | None:
    for column in df.columns:
        if not is_numeric_dtype(df[column]):
            return str(column)
    return None


def compose_lineage(sources: Iterable[BaseFrame], *, step: LineageStep) -> Lineage:
    all_steps: list[LineageStep] = []
    external_inputs: set[str] = set()
    for source in sources:
        all_steps.extend(source.lineage.steps)
        external_inputs.update(source.lineage.external_inputs)
    all_steps.append(step)
    return Lineage(steps=all_steps, external_inputs=sorted(external_inputs))


def compose_candidate_origins(sources: Iterable[BaseFrame]) -> tuple[CandidateOrigin, ...]:
    """Merge persisted candidate origins in public source order."""
    return merge_candidate_origins(*(source.meta.candidate_origins for source in sources))


def persist_attribution_frame(
    *,
    session: Session,
    df: pd.DataFrame,
    intent: str,
    params: dict[str, Any],
    sources: list[BaseFrame],
    metric_ids: list[str],
    attribution_kind: str,
    driver_field: str | None,
    value_column: str | None,
    contribution_column: str | None,
    method: str,
    semantic_kind: str,
    semantic_model: str,
    started_at: datetime,
    started_monotonic: float,
    analysis_purpose: str | None = None,
    extra_issues: Sequence[ArtifactIssue] | None = None,
    reconciliation: AttributionReconciliation | None = None,
    axis_ids: list[str],
    axis_columns: list[str],
    mode: AttributionMode | None,
    bucket_column: str | None = None,
    method_evidence: AttributionMethodEvidenceV1 | None = None,
    resolution_evidence: HierarchyResolutionEvidenceV1 | None = None,
    top_k_selection: AttributionTopKSelectionV1 | None = None,
    row_contract_version: Literal[
        "generic-attribution-rows/v3", "cumulative-flow-attribution-rows/v1"
    ] = "generic-attribution-rows/v3",
) -> AttributionFrame:
    session._connection_runtime.begin_query_capture()
    if isinstance(method_evidence, CumulativeBusinessAxisEvidenceV1):
        method_evidence = reconcile_cumulative_business_evidence(
            method_evidence,
            df,
            hierarchy=mode == "hierarchy",
        )
        reconciliation = cumulative_reconciliation_from_partitions(
            method_evidence.partitions,
            one_sided_contribution_sum=(
                reconciliation.one_sided_contribution_sum if reconciliation is not None else None
            ),
        )
    if reconciliation is not None:
        params = {
            **params,
            "reconciliation": reconciliation.model_dump(mode="json"),
        }
    if mode == "hierarchy" and resolution_evidence is None:
        resolution_evidence = RollupHierarchyEvidenceV1(scope=CompleteHierarchyScopeV1())
    frame_ref = gen_ref("frame")
    job_ref = gen_ref("job")
    source_refs = [source.meta.artifact_id or source.ref for source in sources]
    finished_at = datetime.now(UTC)
    meta = AttributionFrameMeta(
        kind="attribution_frame",
        ref=frame_ref,
        session_id=session.id,
        project_root=str(session.project_root),
        produced_by_job=job_ref,
        analysis_purpose=analysis_purpose,
        created_at=finished_at,
        row_count=len(df),
        byte_size=0,
        lineage=compose_lineage(
            sources,
            step=LineageStep(
                intent=intent,
                job_ref=job_ref,
                inputs=source_refs,
                params_digest=params_digest(params),
                analysis_purpose=analysis_purpose,
            ),
        ),
        candidate_origins=compose_candidate_origins(sources),
        metric_ids=metric_ids,
        source_refs=source_refs,
        scope_delta_ref=source_refs[0] if source_refs else None,
        attribution_kind=attribution_kind,  # type: ignore[arg-type]
        driver_field=driver_field,
        value_column=value_column,
        contribution_column=contribution_column,
        method=method,
        params=params,
        semantic_kind=semantic_kind,  # type: ignore[arg-type]
        semantic_model=semantic_model,
        issues=tuple(extra_issues or ()),
        reconciliation=reconciliation,
        row_contract_version=row_contract_version,
        causal_claim="none",
        axis_bindings=tuple(
            AttributionAxisBindingV1(
                ref=RefPayloadV1.from_ref(
                    ref_factory.time_dimension(axis_id)
                    if session.catalog._require_index()
                    .registry.dimensions[axis_id]
                    .is_time_dimension
                    else ref_factory.dimension(axis_id)
                ),
                output_column=axis_column,
            )
            for axis_id, axis_column in zip(axis_ids, axis_columns, strict=True)
        ),
        attribution_mode=mode,
        bucket_column=bucket_column,
        method_evidence=method_evidence,
        resolution_evidence=resolution_evidence,
        top_k_selection=top_k_selection,
    )
    if isinstance(method_evidence, CumulativeBusinessAxisEvidenceV1):
        validate_generic_attribution_rows(meta, df)
    validate_cumulative_flow_attribution_rows(meta, df)
    frame = AttributionFrame(_df=df.copy(), meta=meta)
    source_ref_values = [source.meta.artifact_id or source.ref for source in sources]
    frame = cast(
        "AttributionFrame",
        commit_result(
            session=session,
            store=session._evidence_store(),
            frames_dir=session._layout.frames_dir,
            frame=frame,
            step_type=intent,
            inputs=CommitInputs(input_refs=source_ref_values),
            params=CommitParams(values=params),
            semantic_anchors=CommitSemanticAnchors.from_frames(*sources),
            subject=Subject(analysis_axis="decomposition"),
            extractor_family="attribution_frame",
            seeding_context={"observed_window": None},
        ),
    )
    register_frame_artifact(session, frame)
    _captured_queries = session._connection_runtime.take_captured_queries()
    # commit_result may reuse an already-committed immutable artifact: the
    # returned frame keeps the original producer/purpose, so this invocation
    # is recorded as a reuse rather than rewriting history (issue #38).
    reused_artifact = frame.meta.produced_by_job != job_ref
    persist_job_record(
        session,
        {
            "id": job_ref,
            "session_id": session.id,
            "intent": intent,
            **job_semantics_from_frames(*sources),
            "analysis_purpose": analysis_purpose,
            "params": params,
            "input_frame_refs": source_refs,
            "output_frame_ref": frame.meta.artifact_id or frame_ref,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_ms": int((monotonic() - started_monotonic) * 1000),
            "status": "succeeded",
            "reused_artifact": reused_artifact,
            "error": None,
            "semantic_project_root": str(session.catalog._project.semantic_root),
            "queries": [qe.to_dict() for qe in _captured_queries],
        },
    )
    return frame
