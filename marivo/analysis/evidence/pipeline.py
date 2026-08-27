"""Commit typed analysis artifacts and deterministic evidence projections."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast

import pandas as pd
from pydantic import BaseModel, ConfigDict, TypeAdapter

from marivo._compat import UTC
from marivo._temporal import (
    ComparisonTemporalContractV1,
    FrameTemporalContractV1,
    _trusted_time_scope_validation,
)
from marivo.analysis._cumulative import (
    AllHistoryLevelChangeV1,
    AllHistoryPairAlignmentV1,
    CumulativeAlignmentV1,
)
from marivo.analysis._semantic_persistence import SlicePredicateV1
from marivo.analysis.errors import (
    AnalysisRepair,
    FrameMetaInvalidError,
    SessionLockedByAnotherProcessError,
)
from marivo.analysis.evidence.digest import build_artifact_digest
from marivo.analysis.evidence.extraction.composition import (
    DecompositionExtractionContract,
    extract_decomposition_findings,
)
from marivo.analysis.evidence.extraction.correlation import extract_correlation_findings
from marivo.analysis.evidence.extraction.delta import extract_delta_findings
from marivo.analysis.evidence.extraction.event import (
    extract_event_funnel_finding,
    extract_event_journey_finding,
    extract_event_time_to_event_finding,
)
from marivo.analysis.evidence.extraction.forecast import extract_forecast_point_findings
from marivo.analysis.evidence.extraction.funnel import (
    extract_funnel_attribution_finding,
    extract_funnel_delta_finding,
)
from marivo.analysis.evidence.extraction.lifecycle import extract_lifecycle_finding
from marivo.analysis.evidence.extraction.observation import (
    extract_metric_value_findings,
    extract_observation_digest_finding,
)
from marivo.analysis.evidence.extraction.subject import extract_subject_set_finding
from marivo.analysis.evidence.extraction.test import extract_test_result_findings
from marivo.analysis.evidence.identity import (
    canonical_json,
    canonical_subject_key,
    make_artifact_id,
    make_issue_id,
    make_scope_fingerprint,
    to_microseconds_utc,
)
from marivo.analysis.evidence.store import EvidenceStore
from marivo.analysis.evidence.types import (
    AnalysisScope,
    ArtifactDigest,
    ArtifactIssue,
    EventSubject,
    EvidenceAvailabilityIssue,
    EvidenceScope,
    EvidenceSubject,
    Finding,
    IssueSeverity,
    LifecycleSubject,
    OperatorSemantics,
    QualitySummary,
    RawFallback,
    Subject,
    SubjectSetSubject,
    TimeWindow,
)
from marivo.analysis.frames._attribution_columns import (
    ATTRIBUTION_AXIS_COLUMN,
    ATTRIBUTION_DRIVER_COLUMN,
    ATTRIBUTION_LEVEL_COLUMN,
    ATTRIBUTION_PATH_COLUMN,
)
from marivo.analysis.frames._content_hash import (
    compute_file_content_hash,
    compute_frame_content_hash,
)
from marivo.analysis.frames._meta_defaults import (
    compute_analysis_scope,
    compute_quality_summary,
)
from marivo.analysis.frames.base import (
    CURRENT_ARTIFACT_SCHEMA_VERSION,
    BaseFrame,
    BaseFrameMeta,
    _FrameAuxiliaryReceipt,
)
from marivo.analysis.frames.lifecycle import LifecycleFrame, LifecycleFrameMetaVariant
from marivo.analysis.session._layout import _read_parquet_frame
from marivo.introspection.live.model import LiveHelpTarget
from marivo.refs import RefPayloadV1
from marivo.semantic.metric_graph import (
    CatalogMetricIdentity,
    CatalogMetricSubjectV1,
    DeltaComparisonIdentity,
    DeltaMetricSubjectV1,
    RuntimeExpressionIdentity,
    RuntimeExpressionSubjectV1,
    SemanticDependencyDigestV1,
    TypedEvidenceSubject,
)
from marivo.semantic.metric_graph_canonical import canonical_value
from marivo.telemetry import staged

if TYPE_CHECKING:
    from marivo.analysis.session.core import Session


class CommitInputs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    input_refs: list[str]


class _DeltaEvidenceMeta(Protocol):
    alignment: dict[str, Any]
    unit: str | None
    cumulative_change: AllHistoryLevelChangeV1 | None
    cumulative_alignment: CumulativeAlignmentV1 | None


class CommitParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    values: dict[str, Any]


class CommitSemanticAnchors(BaseModel):
    """Closed, role-preserving semantic input to artifact fingerprinting."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    anchor_schema: Literal["marivo.commit_semantic_anchors/v1"] = (
        "marivo.commit_semantic_anchors/v1"
    )
    catalog_definition_fingerprint: str | None = None
    semantic_dependency_digest: SemanticDependencyDigestV1 | None = None
    metric_identities: tuple[CatalogMetricIdentity | RuntimeExpressionIdentity, ...] = ()
    comparison_identity: DeltaComparisonIdentity | None = None
    axis_refs: tuple[RefPayloadV1, ...] = ()
    slice_predicates: tuple[SlicePredicateV1, ...] = ()

    @classmethod
    def from_frame(cls, frame: BaseFrame) -> CommitSemanticAnchors:
        meta = frame.meta
        identities = tuple(getattr(meta, "metric_identities", ()))
        if not identities:
            identity = getattr(meta, "metric_identity", None)
            if isinstance(identity, (CatalogMetricIdentity, RuntimeExpressionIdentity)):
                identities = (identity,)
        bindings = tuple(getattr(meta, "axis_bindings", ()))
        return cls(
            catalog_definition_fingerprint=getattr(meta, "catalog_definition_fingerprint", None),
            semantic_dependency_digest=getattr(meta, "semantic_dependency_digest", None),
            metric_identities=identities,
            comparison_identity=getattr(meta, "comparison_identity", None),
            axis_refs=tuple(binding.ref for binding in bindings),
            slice_predicates=tuple(getattr(meta, "slice_predicates", ())),
        )

    @classmethod
    def from_frames(cls, *frames: BaseFrame) -> CommitSemanticAnchors:
        anchors = tuple(cls.from_frame(frame) for frame in frames)
        identities = tuple(
            dict.fromkeys(identity for anchor in anchors for identity in anchor.metric_identities)
        )
        axis_refs = tuple(dict.fromkeys(ref for anchor in anchors for ref in anchor.axis_refs))
        predicate_values: list[SlicePredicateV1] = []
        for anchor in anchors:
            for predicate in anchor.slice_predicates:
                if predicate not in predicate_values:
                    predicate_values.append(predicate)
        predicates = tuple(predicate_values)
        return cls(
            catalog_definition_fingerprint=next(
                (
                    anchor.catalog_definition_fingerprint
                    for anchor in anchors
                    if anchor.catalog_definition_fingerprint is not None
                ),
                None,
            ),
            semantic_dependency_digest=next(
                (
                    anchor.semantic_dependency_digest
                    for anchor in anchors
                    if anchor.semantic_dependency_digest is not None
                ),
                None,
            ),
            metric_identities=identities,
            comparison_identity=next(
                (
                    anchor.comparison_identity
                    for anchor in anchors
                    if anchor.comparison_identity is not None
                ),
                None,
            ),
            axis_refs=axis_refs,
            slice_predicates=predicates,
        )

    @property
    def payload(self) -> dict[str, Any]:
        return cast("dict[str, Any]", canonical_value(self.model_dump(mode="python")))


def compute_prospective_artifact_id(
    *,
    step_type: str,
    inputs: CommitInputs,
    params: CommitParams,
    semantic_anchors: CommitSemanticAnchors,
) -> str:
    """Compute the deterministic identity assigned by :func:`commit_result`."""
    return make_artifact_id(
        step_type=step_type,
        normalized_inputs=inputs.input_refs,
        normalized_params=params.values,
        semantic_anchors=semantic_anchors.payload,
    )


def frame_exists_on_disk(frames_dir: Path, artifact_id: str) -> bool:
    """Return whether both canonical frame sidecar files are present."""
    frame_dir = frames_dir / artifact_id
    return all(
        path.is_file() and path.stat().st_size > 0
        for path in (frame_dir / "meta.json", frame_dir / "data.parquet")
    )


_ARTIFACT_SCHEMA_VERSION = "v4"
_EXTRACTOR_VERSION = "v4"
_FINDINGS_ADAPTER = TypeAdapter(list[Finding])


def _dimension_columns_from_meta(meta: Any) -> list[str] | None:
    axes = getattr(meta, "axes", None)
    if not isinstance(axes, dict):
        alignment = getattr(meta, "alignment", None)
        axes = alignment.get("axes") if isinstance(alignment, dict) else None
    if not isinstance(axes, dict):
        return None
    columns = [
        str(axis["column"])
        for axis in axes.values()
        if isinstance(axis, dict)
        and axis.get("role") == "dimension"
        and isinstance(axis.get("column"), str)
    ]
    return sorted(columns) or None


def _time_column_from_meta(meta: Any) -> str | None:
    axes = getattr(meta, "axes", None)
    if not isinstance(axes, dict):
        alignment = getattr(meta, "alignment", None)
        axes = alignment.get("axes") if isinstance(alignment, dict) else None
    if not isinstance(axes, dict):
        return None
    for axis in axes.values():
        if isinstance(axis, dict) and axis.get("role") == "time":
            column = axis.get("column") or axis.get("field")
            if isinstance(column, str):
                return column
    time_axis = axes.get("time")
    if isinstance(time_axis, dict):
        column = time_axis.get("column") or time_axis.get("field")
        return str(column) if column else None
    return None


def _delta_time_columns(df: pd.DataFrame, meta: Any) -> tuple[str | None, str | None]:
    """Resolve the actual current and baseline coordinates in comparison rows."""

    alignment = getattr(meta, "alignment", None)
    configured_current = (
        alignment.get("current_bucket_column") if isinstance(alignment, dict) else None
    )
    configured_baseline = (
        alignment.get("baseline_bucket_column") if isinstance(alignment, dict) else None
    )
    current = configured_current if isinstance(configured_current, str) else None
    baseline = configured_baseline if isinstance(configured_baseline, str) else None

    if current is None:
        declared = _time_column_from_meta(meta)
        if declared in df.columns:
            current = declared
        elif "bucket_start_a" in df.columns:
            current = "bucket_start_a"
        else:
            current = declared

    if baseline is None:
        if "bucket_start_b" in df.columns:
            baseline = "bucket_start_b"
        elif current is not None and f"{current}_b" in df.columns:
            baseline = f"{current}_b"

    return current, baseline


def _comparison_time_windows(meta: Any) -> tuple[TimeWindow | None, TimeWindow | None]:
    """Project exact comparison scopes without inferring relative period labels."""
    contract = getattr(meta, "temporal_contract", None)
    if not isinstance(contract, ComparisonTemporalContractV1):
        return None, None
    field = _time_column_from_meta(meta) or "time_scope"

    def project(frame_contract: FrameTemporalContractV1) -> TimeWindow | None:
        scope = frame_contract.time_scope
        if scope is None:
            return None
        return TimeWindow(
            field=field,
            start=scope.start.isoformat(),
            end=scope.end.isoformat(),
        )

    return project(contract.current), project(contract.baseline)


def _atomic_write_parquet(df: pd.DataFrame, dest: Path) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(suffix=".tmp", dir=str(dest.parent))
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        df.to_parquet(
            tmp_path,
            index=False,
            use_dictionary=False,
        )
        content = tmp_path.read_bytes()
        with tmp_path.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(tmp_path, dest)
        return hashlib.sha256(content).hexdigest()
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _atomic_write_meta(meta_path: Path, meta_dict: dict[str, Any]) -> None:
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(suffix=".tmp", dir=str(meta_path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(meta_dict, handle, indent=2, default=str)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, meta_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    """Atomically restore an already-serialized artifact file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _typed_subject_for_identity(
    *,
    identity: object,
    frame: BaseFrame,
    artifact_id: str,
    scope: AnalysisScope,
) -> TypedEvidenceSubject | None:
    scope_fingerprint = make_scope_fingerprint(scope)
    if isinstance(identity, CatalogMetricIdentity):
        return CatalogMetricSubjectV1(
            kind="catalog_metric",
            session_id=frame.meta.session_id,
            metric_ref=identity.metric_ref,
            artifact_id=artifact_id,
            scope_fingerprint=scope_fingerprint,
        )
    if isinstance(identity, RuntimeExpressionIdentity):
        return RuntimeExpressionSubjectV1(
            kind="runtime_expression",
            session_id=frame.meta.session_id,
            expression_fingerprint=identity.expression_fingerprint,
            artifact_id=artifact_id,
            scope_fingerprint=scope_fingerprint,
        )
    return None


def _metric_entries(
    frame: BaseFrame,
    subject: Subject,
    df: pd.DataFrame,
    *,
    artifact_id: str,
    scope: AnalysisScope,
) -> list[tuple[Subject, str, str | None, bool, str | None]]:
    meta = frame.meta
    bindings = getattr(meta, "measure_bindings", ())
    if bindings:
        identities = tuple(getattr(meta, "metric_identities", ()))
        entries: list[tuple[Subject, str, str | None, bool, str | None]] = []
        for index, binding in enumerate(bindings):
            updates: dict[str, object] = {}
            if index < len(identities):
                typed_subject = _typed_subject_for_identity(
                    identity=identities[index],
                    frame=frame,
                    artifact_id=artifact_id,
                    scope=scope,
                )
                if typed_subject is not None:
                    updates["typed_metric_subject"] = typed_subject
            prefix = (
                None
                if len(bindings) == 1
                else (
                    f"metric:{binding.identity.metric_ref.path}"
                    if isinstance(binding.identity, CatalogMetricIdentity)
                    else f"metric:runtime:{binding.identity.expression_fingerprint}"
                )
            )
            entries.append(
                (
                    subject.model_copy(update=updates),
                    binding.value_column,
                    prefix,
                    binding.additivity == "additive",
                    binding.unit if isinstance(binding.unit, str) else None,
                )
            )
        return entries
    measures = getattr(meta, "measures", None)
    if measures:
        identities = tuple(getattr(meta, "metric_identities", ()))
        legacy_entries: list[tuple[Subject, str, str | None, bool, str | None]] = []
        for index, entry in enumerate(measures):
            entry_updates: dict[str, object] = {}
            if index < len(identities):
                typed_subject = _typed_subject_for_identity(
                    identity=identities[index],
                    frame=frame,
                    artifact_id=artifact_id,
                    scope=scope,
                )
                if typed_subject is not None:
                    entry_updates["typed_metric_subject"] = typed_subject
            legacy_entries.append(
                (
                    subject.model_copy(update=entry_updates),
                    entry["column"],
                    f"metric:{entry['metric_id']}",
                    entry.get("additivity") == "additive",
                    entry.get("unit") if isinstance(entry.get("unit"), str) else None,
                )
            )
        return legacy_entries
    measure = getattr(meta, "measure", {})
    column = (
        measure.get("name") or measure.get("column") or measure.get("field") or "value"
        if isinstance(measure, dict)
        else "value"
    )
    if column not in df.columns:
        excluded = set(_dimension_columns_from_meta(meta) or ()) | {"bucket_start"}
        candidates = [candidate for candidate in df.columns if candidate not in excluded]
        column = "value" if "value" in candidates else candidates[0] if candidates else "value"
    return [
        (
            subject,
            str(column),
            None,
            getattr(meta, "additivity", None) == "additive",
            getattr(meta, "unit", None),
        )
    ]


def _extract_findings(
    *,
    df: pd.DataFrame,
    artifact_id: str,
    session_id: str,
    subject: EvidenceSubject,
    extractor_family: str,
    frame: BaseFrame,
    committed_at: datetime,
    scope: EvidenceScope,
) -> list[Finding]:
    meta = frame.meta
    semantic_kind = str(getattr(meta, "semantic_kind", "scalar"))
    if extractor_family == "event_frame":
        if not isinstance(subject, EventSubject):
            raise TypeError("event_frame evidence requires EventSubject")
        event_meta = cast("Any", meta)
        if semantic_kind == "funnel":
            return [
                extract_event_funnel_finding(
                    df=df,
                    artifact_id=artifact_id,
                    session_id=session_id,
                    subject=subject,
                    committed_at=committed_at,
                    step_order=tuple(step.key for step in event_meta.pattern.steps),
                    axis_columns=tuple(axis.output_column for axis in event_meta.axes),
                    reconciliation_passed=(
                        event_meta.grouped_reconciliation.status == "pass"
                        and event_meta.grouped_reconciliation.ungrouped_hash
                        == event_meta.grouped_reconciliation.grouped_hash
                    ),
                    source_unused_event_count=event_meta.source_unused_event_count,
                    source_refs=(event_meta.source_journey_ref,),
                )
            ]
        if semantic_kind == "time_to_event":
            return [
                extract_event_time_to_event_finding(
                    df=df,
                    artifact_id=artifact_id,
                    session_id=session_id,
                    subject=subject,
                    committed_at=committed_at,
                    source_unused_end_count=event_meta.source_unused_end_count,
                    source_refs=(event_meta.source_journey_ref,),
                )
            ]
        return [
            extract_event_journey_finding(
                df=df,
                artifact_id=artifact_id,
                session_id=session_id,
                subject=subject,
                committed_at=committed_at,
                unused_event_count=int(getattr(meta, "unused_event_count", 0)),
                source_refs=tuple(sorted(getattr(meta, "event_fingerprints", {}))),
            )
        ]
    if extractor_family == "lifecycle_frame":
        if not isinstance(subject, LifecycleSubject):
            raise TypeError("lifecycle_frame evidence requires LifecycleSubject")
        return [
            extract_lifecycle_finding(
                df=df,
                artifact_id=artifact_id,
                session_id=session_id,
                subject=subject,
                committed_at=committed_at,
                meta=cast("LifecycleFrameMetaVariant", meta),
            )
        ]
    if extractor_family == "subject_set":
        if not isinstance(subject, SubjectSetSubject):
            raise TypeError("subject_set evidence requires SubjectSetSubject")
        subject_meta = cast("Any", meta)
        return [
            extract_subject_set_finding(
                df=df,
                artifact_id=artifact_id,
                session_id=session_id,
                subject=subject,
                committed_at=committed_at,
                excluded_coverage_censored_count=(subject_meta.excluded_coverage_censored_count),
                coverage_status=subject_meta.coverage_status,
                source_refs=(subject_meta.source.artifact_ref,),
            )
        ]
    if extractor_family == "delta_frame" and semantic_kind == "funnel":
        if not isinstance(subject, EventSubject):
            raise TypeError("DeltaFrame[funnel] evidence requires EventSubject")
        funnel_meta = cast("Any", meta)
        return [
            extract_funnel_delta_finding(
                df=df,
                artifact_id=artifact_id,
                session_id=session_id,
                subject=subject,
                committed_at=committed_at,
                step_count=len(funnel_meta.aligned_step_keys),
                axis_count=len(funnel_meta.axes),
                zero_filled_tuple_count=funnel_meta.zero_filled_tuple_count,
                current_coverage_basis=funnel_meta.current_coverage_basis,
                baseline_coverage_basis=funnel_meta.baseline_coverage_basis,
                source_refs=(
                    funnel_meta.source_current_ref,
                    funnel_meta.source_baseline_ref,
                ),
            )
        ]
    if extractor_family == "attribution_frame" and semantic_kind == "funnel_loss_rate":
        if not isinstance(subject, EventSubject):
            raise TypeError("funnel attribution evidence requires EventSubject")
        funnel_attribution_meta = cast("Any", meta)
        return [
            extract_funnel_attribution_finding(
                df=df,
                artifact_id=artifact_id,
                session_id=session_id,
                subject=subject,
                committed_at=committed_at,
                target_step_key=funnel_attribution_meta.target.step.key,
                positive_pool=funnel_attribution_meta.reconciliation.positive_pool,
                negative_pool=funnel_attribution_meta.reconciliation.negative_pool,
                residual=funnel_attribution_meta.reconciliation.residual,
                reconciliation_status=funnel_attribution_meta.reconciliation.status,
                source_delta_ref=funnel_attribution_meta.source_delta_ref,
            )
        ]
    if not isinstance(subject, Subject) or not isinstance(scope, AnalysisScope):
        raise TypeError(f"{extractor_family} evidence requires metric subject and scope")
    if extractor_family == "metric_frame":
        findings: list[Finding] = []
        for entry_subject, column, prefix, additive, unit in _metric_entries(
            frame,
            subject,
            df,
            artifact_id=artifact_id,
            scope=scope,
        ):
            time_column = (
                "bucket_start"
                if semantic_kind in {"time_series", "panel"} and "bucket_start" in df.columns
                else _time_column_from_meta(meta)
            )
            findings.extend(
                extract_metric_value_findings(
                    df=df,
                    artifact_id=artifact_id,
                    session_id=session_id,
                    subject=entry_subject,
                    semantic_kind=semantic_kind,
                    measure_column=column,
                    committed_at=committed_at,
                    time_column=time_column,
                    dimension_columns=_dimension_columns_from_meta(meta),
                    item_key_prefix=prefix,
                    unit=unit,
                )
            )
            findings.append(
                extract_observation_digest_finding(
                    df=df,
                    artifact_id=artifact_id,
                    session_id=session_id,
                    subject=entry_subject,
                    semantic_kind=semantic_kind,
                    measure_column=column,
                    committed_at=committed_at,
                    time_column=time_column,
                    dimension_columns=_dimension_columns_from_meta(meta),
                    window=getattr(meta, "window", None),
                    analysis_purpose=getattr(meta, "analysis_purpose", None),
                    additive=additive,
                    item_key_prefix=prefix,
                    unit=unit,
                )
            )
        return _FINDINGS_ADAPTER.validate_python(findings)
    if extractor_family == "delta_frame":
        delta_meta = cast("_DeltaEvidenceMeta", meta)
        time_column, baseline_time_column = _delta_time_columns(df, meta)
        current_window, baseline_window = _comparison_time_windows(meta)
        cumulative_pairs = (
            delta_meta.cumulative_alignment.pairs
            if delta_meta.cumulative_alignment is not None
            else AllHistoryPairAlignmentV1.model_validate(delta_meta.alignment["cumulative_pairs"])
            if delta_meta.cumulative_change is not None
            else None
        )
        findings = extract_delta_findings(
            df=df,
            artifact_id=artifact_id,
            session_id=session_id,
            subject=subject,
            semantic_kind=semantic_kind,
            committed_at=committed_at,
            dimension_columns=_dimension_columns_from_meta(meta),
            time_column=time_column,
            baseline_time_column=baseline_time_column,
            unit=delta_meta.unit,
            current_window=current_window,
            baseline_window=baseline_window,
            cumulative_pairs=cumulative_pairs,
            cumulative_change=delta_meta.cumulative_change,
        )
    elif extractor_family == "attribution_frame":
        refs = getattr(meta, "source_refs", [])
        scope_delta_ref = getattr(meta, "scope_delta_ref", None) or (refs[0] if refs else None)
        if scope_delta_ref is None:
            return []
        driver_field = getattr(meta, "driver_field", None)
        contribution_column = getattr(meta, "contribution_column", None)
        reconciliation = getattr(meta, "reconciliation", None)
        axis_bindings = getattr(meta, "axis_bindings", ())
        key_columns = [
            str(binding.output_column)
            for binding in axis_bindings
            if isinstance(binding.output_column, str) and binding.output_column in df.columns
        ]
        attribution_mode = getattr(meta, "attribution_mode", None)
        if attribution_mode == "hierarchy":
            key_columns = [
                column
                for column in (
                    ATTRIBUTION_LEVEL_COLUMN,
                    ATTRIBUTION_AXIS_COLUMN,
                    ATTRIBUTION_DRIVER_COLUMN,
                    ATTRIBUTION_PATH_COLUMN,
                )
                if column in df.columns
            ] + key_columns
        bucket_column = getattr(meta, "bucket_column", None)
        if isinstance(bucket_column, str) and bucket_column in df.columns:
            key_columns.insert(0, bucket_column)
        if not key_columns and isinstance(driver_field, str) and driver_field in df.columns:
            key_columns.append(driver_field)
        if not isinstance(contribution_column, str) or contribution_column not in df.columns:
            return []
        method_evidence = getattr(meta, "method_evidence", None)
        resolution_evidence = getattr(meta, "resolution_evidence", None)
        source_error_bound = (
            method_evidence.source_error_bound
            if method_evidence is not None
            and getattr(method_evidence, "kind", None) == "quantile_replacement"
            else None
        )
        findings = extract_decomposition_findings(
            df=df,
            artifact_id=artifact_id,
            session_id=session_id,
            subject=subject,
            committed_at=committed_at,
            scope_delta_ref=str(scope_delta_ref),
            contract=DecompositionExtractionContract(
                dimension_name=str(driver_field or ""),
                key_columns=tuple(dict.fromkeys(key_columns)),
                contribution_column=contribution_column,
                contribution_share_column=(
                    "share_of_total_delta" if "share_of_total_delta" in df.columns else None
                ),
                direction="undefined",
                decomposition_method=str(getattr(meta, "method", "algebraic_decomposition")),
                reconciliation_residual=(
                    reconciliation.residual if reconciliation is not None else None
                ),
                ordered_axis_refs=tuple(binding.ref for binding in axis_bindings),
                ordered_prefix_rows=attribution_mode == "hierarchy",
                rollup_safe=(
                    resolution_evidence.rollup_safe if resolution_evidence is not None else True
                ),
                causal_claim=getattr(meta, "causal_claim", "none"),
                source_error_bound=source_error_bound,
            ),
        )
    elif extractor_family == "association_result":
        prepared = df.copy()
        if "coefficient" not in prepared.columns and "correlation" in prepared.columns:
            refs = getattr(meta, "source_refs", [])
            prepared["left_ref"] = refs[0] if len(refs) > 0 else None
            prepared["right_ref"] = refs[1] if len(refs) > 1 else None
            prepared["coefficient"] = prepared["correlation"]
            alignment = getattr(meta, "alignment", {})
            prepared["join_basis"] = alignment.get("kind") if isinstance(alignment, dict) else None
        # The evidence item must bind to the SAME lag the summary represents
        # (meta.selected_lag_offset / meta.best_lag), never the first table row.
        selected_lag = getattr(meta, "selected_lag_offset", None)
        if selected_lag is not None and "lag_offset" in prepared.columns:
            selected = prepared[prepared["lag_offset"] == selected_lag]
            if not selected.empty:
                prepared = selected
        prepared["lag"] = selected_lag
        if "n" not in prepared.columns and "aligned_row_count" in prepared.columns:
            prepared["n"] = prepared["aligned_row_count"]
        findings = extract_correlation_findings(
            df=prepared,
            artifact_id=artifact_id,
            session_id=session_id,
            subject=subject,
            committed_at=committed_at,
        )
    elif extractor_family == "hypothesis_test_result":
        prepared = df.copy()
        if "reject_null" not in prepared.columns and "rejected" in prepared.columns:
            refs = getattr(meta, "source_refs", [])
            prepared["current_ref"] = refs[0] if len(refs) > 0 else None
            prepared["baseline_ref"] = refs[1] if len(refs) > 1 else None
            prepared["method"] = getattr(meta, "method", None)
            prepared["estimate_value"] = prepared.get("mean_diff")
            prepared["statistic_name"] = "t"
            prepared["statistic_value"] = prepared.get("test_statistic")
            prepared["reject_null"] = prepared["rejected"]
            prepared["alpha"] = getattr(meta, "alpha", None)
        findings = extract_test_result_findings(
            df=prepared,
            artifact_id=artifact_id,
            session_id=session_id,
            subject=subject,
            committed_at=committed_at,
            alternative=str(getattr(meta, "alternative", "two_sided")),
        )
    elif extractor_family == "forecast_frame":
        prepared = df.copy()
        if "predicted_value" not in prepared.columns and "predicted" in prepared.columns:
            if "bucket_start" not in prepared.columns and "time" in prepared.columns:
                prepared["bucket_start"] = prepared["time"]
            if "bucket_end" not in prepared.columns and "time" in prepared.columns:
                prepared["bucket_end"] = prepared["time"]
            prepared["predicted_value"] = prepared["predicted"]
        findings = extract_forecast_point_findings(
            df=prepared,
            artifact_id=artifact_id,
            session_id=session_id,
            subject=subject,
            committed_at=committed_at,
            model=str(
                getattr(meta, "method", None) or getattr(meta, "model", None) or "unknown_model"
            ),
            training_scope=scope,
        )
    else:
        findings = []
    if extractor_family in {"association_result", "hypothesis_test_result"}:
        candidate_origins = tuple(getattr(meta, "candidate_origins", ()))
        if candidate_origins:
            findings = [
                finding.model_copy(
                    update={
                        "derivation": finding.derivation.model_copy(
                            update={"candidate_origins": candidate_origins}
                        )
                    }
                )
                for finding in findings
            ]
    return _FINDINGS_ADAPTER.validate_python(findings)


def _operator_for(step_type: str, extractor_family: str) -> str:
    if step_type in {
        "transform",
        "select_metric",
        "compare.funnel",
        "attribute.funnel_loss_rate",
    }:
        return step_type
    if extractor_family in {"event_frame", "lifecycle_frame"}:
        return step_type
    return {
        "metric_frame": "observe",
        "delta_frame": "compare",
        "attribution_frame": "attribute",
        "candidate_set": "discover",
        "association_result": "correlate",
        "hypothesis_test_result": "hypothesis_test",
        "forecast_frame": "forecast",
        "subject_set": "select_subjects",
    }.get(extractor_family, step_type)


def _help_canonical_id(operator: str) -> str:
    """Return the resolvable help-target canonical id for an operator name.

    ``_operator_for`` pass-throughs point-like step_type names (compare.funnel,
    attribute.funnel_loss_rate, select_metric) that are used verbatim in action
    text but are not registered on the analysis help surface; their parent ids
    (compare, attribute, MetricFrame.metric) are. Normalize to the parent so a
    repair's help_target never resolves to a MarivoHelpTargetError.
    """
    return {
        "compare.funnel": "compare",
        "attribute.funnel_loss_rate": "attribute",
        "select_metric": "MetricFrame.metric",
    }.get(operator, operator)


def _issue(
    artifact_id: str,
    kind: Literal["evidence_partial", "evidence_store_unavailable", "evidence_digest_unavailable"],
    failed_stage: Literal["extract", "digest", "store"],
    findings_available: bool,
    stable_error_category: str,
    *,
    severity: IssueSeverity = "blocking",
    rows_available: bool = True,
    repair: AnalysisRepair | None = None,
) -> EvidenceAvailabilityIssue:
    return EvidenceAvailabilityIssue(
        issue_id=make_issue_id(artifact_id=artifact_id, kind=kind, source_refs=(artifact_id,)),
        kind=kind,
        severity=severity,
        source_refs=(artifact_id,),
        failed_stage=failed_stage,
        findings_available=findings_available,
        fallback=RawFallback(
            artifact_ref=artifact_id,
            findings_available=findings_available,
            rows_available=rows_available,
            recommended_when=("partial_evidence",),
        ),
        stable_error_category=stable_error_category,
        repair=repair,
    )


def _attribution_reconciliation_verified(frame: BaseFrame) -> bool:
    """Return whether an attribution frame carries verified reconciliation.

    The raw rows are always materialized before extraction (parquet written at
    the top of :func:`commit_result`), so reconciliation presence is the signal
    that the rows are safe to reference even when findings extraction fails.
    """
    meta = frame.meta
    if getattr(meta, "kind", None) != "attribution_frame":
        return False
    reconciliation = getattr(meta, "reconciliation", None)
    return reconciliation is not None and getattr(reconciliation, "status", None) == "reconciled"


def _extract_failure_issue(
    *,
    artifact_id: str,
    frame: BaseFrame,
    exc: Exception,
    step_type: str,
    extractor_family: str,
) -> EvidenceAvailabilityIssue:
    """Build an evidence_partial issue for a finding-extraction failure.

    Issue #68: readable/reconciled attribution rows must not surface as a
    blocking evidence_partial with repair=None. When the rows are materialized
    and reconciliation is verified the issue is downgraded to warning (the rows
    stay referenceable) and always carries a typed repair preserving the real
    stable error category.

    The repair's operator and help_target follow the frame's own family via
    :func:`_operator_for`; the attribution-specific reconciliation wording is
    limited to attribution frames (commit_result is the shared commit path for
    every frame family).
    """
    error_category = type(exc).__name__
    operator = _operator_for(step_type, extractor_family)
    reconciled = _attribution_reconciliation_verified(frame)
    if reconciled:
        severity: IssueSeverity = "warning"
        action = (
            "Attribution rows are materialized and reconciliation verified, so "
            f"the raw rows remain safely referenceable; findings extraction "
            f"failed ({error_category}). Use frame.to_pandas() to read the "
            "attribution rows, frame.show() to inspect, or re-run attribute to "
            "retry extraction."
        )
    else:
        severity = "blocking"
        if operator == "attribute":
            action = (
                f"findings extraction failed ({error_category}) and attribution "
                "reconciliation is not verified; re-run attribute and reference the "
                "attribution result only once extraction succeeds."
            )
        else:
            action = (
                f"findings extraction failed ({error_category}); re-run {operator} "
                "and reference the result only once extraction succeeds."
            )
    return _issue(
        artifact_id,
        "evidence_partial",
        failed_stage="extract",
        findings_available=False,
        stable_error_category=error_category,
        severity=severity,
        rows_available=True,
        repair=AnalysisRepair(
            kind="inspect",
            action=action,
            help_target=LiveHelpTarget(
                surface="analysis",
                canonical_id=_help_canonical_id(operator),
            ),
        ),
    )


def _digest_failure_issue(
    *,
    artifact_id: str,
    exc: Exception,
    operator: str,
) -> EvidenceAvailabilityIssue:
    """Build an evidence_digest_unavailable issue carrying a typed repair.

    Issue #73: the digest stage of the shared commit path failed after
    findings extraction succeeded, so the issue carries the real stable error
    category and an executable repair pointing at the frame's own operator.
    """
    error_category = type(exc).__name__
    return _issue(
        artifact_id,
        "evidence_digest_unavailable",
        failed_stage="digest",
        findings_available=True,
        stable_error_category=error_category,
        repair=AnalysisRepair(
            kind="inspect",
            action=(
                f"digest construction failed ({error_category}) after findings "
                f"extraction; re-run {operator} so the typed digest is rebuilt "
                "and reference the result only once the digest is available."
            ),
            help_target=LiveHelpTarget(
                surface="analysis",
                canonical_id=_help_canonical_id(operator),
            ),
        ),
    )


def _store_failure_issue(
    *,
    artifact_id: str,
    operator: str,
    stable_error_category: str,
    integrity_failure: bool = False,
) -> EvidenceAvailabilityIssue:
    """Build an evidence_store_unavailable issue carrying a typed repair.

    Integrity failures are deterministic projection failures, not evidence-store
    permission failures. Other categories retain the environment repair because
    the store is absent or could not complete a write.
    """
    repair = (
        AnalysisRepair(
            kind="retry",
            action=(
                f"evidence projection integrity failed ({stable_error_category}); "
                f"re-run {operator} with the current Marivo build so the artifact "
                "projection and findings are regenerated."
            ),
            help_target=LiveHelpTarget(
                surface="analysis",
                canonical_id=_help_canonical_id(operator),
            ),
        )
        if integrity_failure
        else AnalysisRepair(
            kind="environment",
            action=(
                f"evidence store is unavailable ({stable_error_category}); ensure "
                "the evidence store is configured and writable, then re-run "
                f"{operator} so the artifact projection and findings are persisted."
            ),
            help_target=LiveHelpTarget(
                surface="analysis",
                canonical_id=_help_canonical_id(operator),
            ),
        )
    )
    return _issue(
        artifact_id,
        "evidence_store_unavailable",
        failed_stage="store",
        findings_available=False,
        stable_error_category=stable_error_category,
        repair=repair,
    )


def _insert_projection(
    store: EvidenceStore,
    *,
    artifact_id: str,
    session_id: str,
    step_type: str,
    extractor_family: str,
    subject: EvidenceSubject,
    lineage_payload: str,
    scope: EvidenceScope,
    quality: QualitySummary,
    status: str,
    frame_path: str,
    frame_sha: str,
    findings: list[Finding],
    digest: ArtifactDigest | None,
    issues: list[ArtifactIssue],
    committed_at: datetime,
) -> None:
    committed_at_us = to_microseconds_utc(committed_at)
    with store.transaction(immediate=True) as tx:
        tx.execute(
            """INSERT INTO artifacts
               (artifact_id, session_id, step_type, artifact_type,
                artifact_schema_version, subject_payload, lineage_payload,
                analysis_scope, quality_summary, evidence_status,
                frame_path, frame_sha, committed_at_us)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(artifact_id) DO UPDATE SET
                 session_id = excluded.session_id,
                 step_type = excluded.step_type,
                 artifact_type = excluded.artifact_type,
                 artifact_schema_version = excluded.artifact_schema_version,
                 subject_payload = excluded.subject_payload,
                 lineage_payload = excluded.lineage_payload,
                 analysis_scope = excluded.analysis_scope,
                 quality_summary = excluded.quality_summary,
                 evidence_status = excluded.evidence_status,
                 frame_path = excluded.frame_path,
                 frame_sha = excluded.frame_sha,
                 committed_at_us = excluded.committed_at_us""",
            (
                artifact_id,
                session_id,
                step_type,
                extractor_family,
                _ARTIFACT_SCHEMA_VERSION,
                canonical_json(subject),
                lineage_payload,
                canonical_json(scope),
                canonical_json(quality),
                status,
                frame_path,
                frame_sha,
                committed_at_us,
            ),
        )
        tx.execute("DELETE FROM findings WHERE artifact_id = ?", (artifact_id,))
        for finding in findings:
            tx.execute(
                """INSERT INTO findings
                   (finding_id, session_id, artifact_id, finding_type,
                    epistemic_kind, canonical_item_key, subject_axis,
                    subject_payload, observed_window_payload, quality_status,
                    value_kind, value_payload, derivation_payload,
                    source_refs_payload, artifact_schema_version,
                    extractor_version, committed_at_us)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    finding.finding_id,
                    session_id,
                    artifact_id,
                    finding.finding_type,
                    finding.epistemic_kind,
                    finding.canonical_item_key,
                    finding.subject.analysis_axis,
                    canonical_json(finding.subject),
                    canonical_json(finding.observed_window) if finding.observed_window else None,
                    finding.quality_status,
                    finding.value.kind,
                    canonical_json(finding.value),
                    canonical_json(finding.derivation),
                    canonical_json(finding.source_refs),
                    finding.artifact_schema_version,
                    finding.extractor_version,
                    to_microseconds_utc(finding.committed_at),
                ),
            )
        tx.execute("DELETE FROM artifact_digests WHERE artifact_id = ?", (artifact_id,))
        if digest is not None:
            tx.execute(
                """INSERT INTO artifact_digests
                   (artifact_id, session_id, operator, subject_key,
                    digest_payload, fingerprint, committed_at_us)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    artifact_id,
                    session_id,
                    digest.operator.operator,
                    canonical_subject_key(subject),
                    canonical_json(digest),
                    digest.fingerprint,
                    committed_at_us,
                ),
            )
        tx.execute("DELETE FROM artifact_issues WHERE artifact_id = ?", (artifact_id,))
        for issue in issues:
            tx.execute(
                """INSERT INTO artifact_issues
                   (issue_id, session_id, artifact_id, kind, severity,
                    issue_payload, created_at_us)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    issue.issue_id,
                    session_id,
                    artifact_id,
                    issue.kind,
                    issue.severity,
                    canonical_json(issue),
                    committed_at_us,
                ),
            )


def _remove_projection(store: EvidenceStore, artifact_id: str) -> None:
    with store.transaction(immediate=True) as tx:
        tx.execute("DELETE FROM artifact_issues WHERE artifact_id = ?", (artifact_id,))
        tx.execute("DELETE FROM artifact_digests WHERE artifact_id = ?", (artifact_id,))
        tx.execute("DELETE FROM findings WHERE artifact_id = ?", (artifact_id,))
        tx.execute("DELETE FROM artifacts WHERE artifact_id = ?", (artifact_id,))


def _reuse_committed_result(
    *,
    store: EvidenceStore | None,
    frame: BaseFrame,
    artifact_id: str,
    parquet_path: Path,
    meta_path: Path,
    retry_store_failure: bool,
) -> BaseFrame | None:
    """Return an already committed immutable artifact without rewriting it."""
    if not parquet_path.is_file() or not meta_path.is_file():
        return None
    if store is not None:
        row = (
            store.read()
            .execute(
                "SELECT evidence_status FROM artifacts WHERE artifact_id = ?",
                (artifact_id,),
            )
            .fetchone()
        )
        if row is None:
            return None
        if retry_store_failure and row["evidence_status"] == "unavailable":
            issue = (
                store.read()
                .execute(
                    "SELECT 1 FROM artifact_issues "
                    "WHERE artifact_id = ? AND kind = 'evidence_store_unavailable' LIMIT 1",
                    (artifact_id,),
                )
                .fetchone()
            )
            if issue is not None:
                return None
    try:
        persisted_payload = json.loads(meta_path.read_text(encoding="utf-8"))
        if persisted_payload.get("artifact_schema_version") != CURRENT_ARTIFACT_SCHEMA_VERSION:
            raise FrameMetaInvalidError(
                message=(
                    f"artifact {artifact_id!r} uses a non-current schema; "
                    "recreate the analysis session"
                ),
                location=f"artifact {artifact_id!r} schema version",
                expected=CURRENT_ARTIFACT_SCHEMA_VERSION,
                received=persisted_payload.get("artifact_schema_version"),
                repair=AnalysisRepair(
                    kind="retry",
                    action=(
                        f"artifact {artifact_id!r} uses a non-current schema. "
                        "Re-run the analysis so the artifact is regenerated "
                        "under the current contract."
                    ),
                    help_target=LiveHelpTarget(surface="analysis", canonical_id="observe"),
                ),
                context={"artifact_id": artifact_id},
            )
        with _trusted_time_scope_validation():
            persisted_meta = (
                type(frame.meta).model_validate_json(json.dumps(persisted_payload))
                if getattr(frame.meta, "kind", None) in {"event_frame", "lifecycle_frame"}
                else type(frame.meta).model_validate(persisted_payload)
            )
        persisted_df = _read_parquet_frame(parquet_path)
    except FrameMetaInvalidError:
        raise
    except Exception:
        return None
    if persisted_meta.artifact_id != artifact_id or persisted_meta.ref != artifact_id:
        return None
    auxiliary_frames: dict[str, pd.DataFrame] = {}
    if (
        getattr(persisted_meta, "kind", None) == "lifecycle_frame"
        and getattr(persisted_meta, "semantic_kind", None) == "history"
    ):
        lifecycle_meta = cast("Any", persisted_meta)
        manifest = lifecycle_meta.violation_trace
        if manifest.content_hash is None:
            return None
        artifact_dir = parquet_path.parent.resolve()
        trace_path = (artifact_dir / manifest.filename).resolve()
        if trace_path.parent != artifact_dir or not trace_path.is_file():
            return None
        if compute_file_content_hash(trace_path) != manifest.content_hash:
            return None
        try:
            trace = _read_parquet_frame(trace_path)
        except Exception:
            return None
        if len(trace) != manifest.row_count:
            return None
        if lifecycle_meta.content_hash != compute_frame_content_hash(
            meta=lifecycle_meta, data_path=parquet_path
        ):
            return None
        auxiliary_frames[manifest.filename] = trace
    frame.meta = persisted_meta
    frame._df = persisted_df
    frame._auxiliary_frames = auxiliary_frames
    restore_persisted_columns = getattr(
        frame,
        "_restore_persisted_identity_columns",
        None,
    )
    if callable(restore_persisted_columns):
        restore_persisted_columns()
    return frame


def _bind_typed_metric_subject(
    *,
    frame: BaseFrame,
    subject: Subject,
    artifact_id: str,
    scope: AnalysisScope,
    semantic_anchors: CommitSemanticAnchors,
) -> Subject:
    """Bind current artifact/session ownership to a typed metric subject."""

    metric_identity = getattr(frame.meta, "metric_identity", None)
    typed_subject = _typed_subject_for_identity(
        identity=metric_identity,
        frame=frame,
        artifact_id=artifact_id,
        scope=scope,
    )
    comparison_identity = getattr(frame.meta, "comparison_identity", None)
    if metric_identity is None and len(semantic_anchors.metric_identities) == 1:
        metric_identity = semantic_anchors.metric_identities[0]
        typed_subject = _typed_subject_for_identity(
            identity=metric_identity,
            frame=frame,
            artifact_id=artifact_id,
            scope=scope,
        )
    if comparison_identity is None:
        comparison_identity = semantic_anchors.comparison_identity
    if isinstance(comparison_identity, DeltaComparisonIdentity):
        typed_subject = DeltaMetricSubjectV1(
            kind="delta_metric",
            session_id=frame.meta.session_id,
            comparison=comparison_identity,
        )
    if typed_subject is None:
        return subject
    return subject.model_copy(update={"typed_metric_subject": typed_subject})


def event_subject_for_frame(frame: BaseFrame) -> EventSubject:
    """Build the identity-safe evidence subject for an Event frame."""
    meta = cast("Any", frame.meta)
    if getattr(meta, "kind", None) != "event_frame":
        raise TypeError("event_subject_for_frame requires EventFrame")
    return EventSubject(
        subject_entity_ref=meta.subject_entity_ref,
        subject_identity_signature=tuple(meta.subject_identity),
        analysis_axis=meta.semantic_kind,
    )


def lifecycle_subject_for_frame(frame: BaseFrame) -> LifecycleSubject:
    """Build the identity-safe evidence subject for a Lifecycle frame."""
    meta = cast("Any", frame.meta)
    if getattr(meta, "kind", None) != "lifecycle_frame":
        raise TypeError("lifecycle_subject_for_frame requires LifecycleFrame")
    return LifecycleSubject(
        subject_entity_ref=meta.subject_entity_ref,
        subject_identity_signature=tuple(meta.subject_identity),
        analysis_axis=meta.semantic_kind,
    )


def subject_set_subject_for_frame(frame: BaseFrame) -> SubjectSetSubject:
    """Build the identity-safe evidence subject for a SubjectSet."""
    meta = cast("Any", frame.meta)
    if getattr(meta, "kind", None) != "subject_set":
        raise TypeError("subject_set_subject_for_frame requires SubjectSet")
    return SubjectSetSubject(
        subject_entity_ref=meta.subject_entity_ref,
        subject_identity_signature=tuple(meta.subject_identity),
    )


@staged("evidence")
def commit_result(
    *,
    session: Session | None,
    store: EvidenceStore | None,
    frames_dir: Path,
    frame: BaseFrame,
    step_type: str,
    inputs: CommitInputs,
    params: CommitParams,
    semantic_anchors: CommitSemanticAnchors,
    subject: EvidenceSubject,
    extractor_family: str,
    comparison_window: dict[str, Any] | None = None,
    comparison_basis: str | None = None,
    seeding_context: dict[str, Any] | None = None,
    emit_evidence: bool = True,
    quality_source_frames: tuple[BaseFrame, ...] = (),
) -> BaseFrame:
    """Persist one artifact and its typed findings/digest without judgment stages."""
    del comparison_window, comparison_basis, seeding_context
    now = datetime.now(UTC)
    artifact_id = compute_prospective_artifact_id(
        step_type=step_type,
        inputs=inputs,
        params=params,
        semantic_anchors=semantic_anchors,
    )
    artifact_dir = frames_dir / artifact_id
    parquet_path = artifact_dir / "data.parquet"
    meta_path = artifact_dir / "meta.json"
    reused = _reuse_committed_result(
        store=store,
        frame=frame,
        artifact_id=artifact_id,
        parquet_path=parquet_path,
        meta_path=meta_path,
        retry_store_failure=emit_evidence,
    )
    if reused is not None:
        if session is not None and session._store.get_artifact(session.id, artifact_id) is None:
            return session.get_frame(artifact_id)
        return reused
    source_history = next(
        (
            source
            for source in quality_source_frames
            if isinstance(source, LifecycleFrame) and source.meta.semantic_kind == "history"
        ),
        None,
    )
    quality_evaluation = frame._evaluate_construction_quality(
        artifact_id=artifact_id,
        source_history=source_history,
    )
    previous_projection_exists = False
    previous_meta_bytes: bytes | None = None
    if store is not None:
        with suppress(sqlite3.DatabaseError):
            previous_projection_exists = (
                store.read()
                .execute(
                    "SELECT 1 FROM artifacts WHERE artifact_id = ?",
                    (artifact_id,),
                )
                .fetchone()
                is not None
            )
    if previous_projection_exists and meta_path.is_file():
        with suppress(OSError):
            previous_meta_bytes = meta_path.read_bytes()
    df = frame._dataframe_copy()
    frame_sha = _atomic_write_parquet(df, parquet_path)
    quality_manifest = None
    if quality_evaluation is not None:
        quality_path = artifact_dir / "quality.parquet"
        quality_sha = _atomic_write_parquet(quality_evaluation.dataframe, quality_path)
        quality_manifest = quality_evaluation.build_manifest(
            content_hash=f"sha256:{quality_sha}",
        )
    auxiliary_receipts: list[_FrameAuxiliaryReceipt] = []
    auxiliary_filenames: set[str] = set()
    for table in frame._auxiliary_tables():
        if table.filename in auxiliary_filenames:
            raise ValueError("frame auxiliary table filenames must be unique")
        auxiliary_filenames.add(table.filename)
        path = artifact_dir / table.filename
        auxiliary_sha = _atomic_write_parquet(table.dataframe, path)
        auxiliary_receipts.append(
            _FrameAuxiliaryReceipt(
                filename=table.filename,
                row_count=len(table.dataframe),
                byte_size=path.stat().st_size,
                content_hash=f"sha256:{auxiliary_sha}",
            )
        )
    scope = frame.meta.analysis_scope or compute_analysis_scope(frame)
    slice_predicates = getattr(frame.meta, "slice_predicates", ())
    if not slice_predicates:
        slice_predicates = semantic_anchors.slice_predicates
    if isinstance(subject, Subject) and isinstance(slice_predicates, tuple) and slice_predicates:
        subject = subject.model_copy(update={"slice_predicates": slice_predicates})
    if getattr(frame.meta, "kind", None) == "event_frame":
        subject = event_subject_for_frame(frame)
    elif getattr(frame.meta, "kind", None) == "lifecycle_frame":
        subject = lifecycle_subject_for_frame(frame)
    elif getattr(frame.meta, "kind", None) == "subject_set":
        subject = subject_set_subject_for_frame(frame)
    elif isinstance(subject, Subject) and isinstance(scope, AnalysisScope):
        subject = _bind_typed_metric_subject(
            frame=frame,
            subject=subject,
            artifact_id=artifact_id,
            scope=scope,
            semantic_anchors=semantic_anchors,
        )
    quality = (
        quality_evaluation.summary
        if quality_evaluation is not None
        else compute_quality_summary(frame)
    )
    findings: list[Finding] = []
    digest: ArtifactDigest | None = None
    issues: list[ArtifactIssue] = list(frame.meta.issues)
    if quality_evaluation is not None:
        issues.extend(quality_evaluation.issues)
    status = "unavailable" if not emit_evidence or store is None else "complete"

    if emit_evidence and store is not None:
        try:
            operator_name = _operator_for(step_type, extractor_family)
            findings = (
                []
                if operator_name in {"transform", "select_metric"}
                else _extract_findings(
                    df=df,
                    artifact_id=artifact_id,
                    session_id=frame.meta.session_id,
                    subject=subject,
                    extractor_family=extractor_family,
                    frame=frame,
                    committed_at=now,
                    scope=scope,
                )
            )
        except Exception as exc:
            status = "partial"
            issues.append(
                _extract_failure_issue(
                    artifact_id=artifact_id,
                    frame=frame,
                    exc=exc,
                    step_type=step_type,
                    extractor_family=extractor_family,
                )
            )
        else:
            try:
                digest = build_artifact_digest(
                    artifact_ref=artifact_id,
                    operator=OperatorSemantics(
                        operator=operator_name,
                        operator_version="v1",
                        artifact_family=extractor_family,
                        semantic_shape=str(getattr(frame.meta, "semantic_kind", "")) or None,
                    ),
                    subject=subject,
                    scope=scope,
                    findings=findings,
                    quality=quality,
                    rows_available=True,
                )
            except Exception as exc:
                status = "partial"
                issues.append(
                    _digest_failure_issue(
                        artifact_id=artifact_id,
                        exc=exc,
                        operator=operator_name,
                    )
                )
    elif emit_evidence and store is None:
        issues.append(
            _store_failure_issue(
                artifact_id=artifact_id,
                operator=_operator_for(step_type, extractor_family),
                stable_error_category="store_unavailable",
            )
        )

    def build_meta() -> BaseFrameMeta:
        meta_update: dict[str, Any] = {
            "ref": artifact_id,
            "artifact_id": artifact_id,
            "evidence_status": status,
            "analysis_scope": scope,
            "quality_summary": quality,
            "quality_ref": (f"{artifact_id}#quality" if quality_manifest is not None else None),
            "quality_report": quality_manifest,
            "evidence_digest": digest,
            "issues": tuple(issues),
        }
        if getattr(frame.meta, "expression_graph", None) is not None:
            meta_update.update(
                {
                    "expression_graph_ref": f"{artifact_id}#expression-graph",
                    "presentation_ref": f"{artifact_id}#presentation",
                    "replay_graph_ref": f"{artifact_id}#replay-graph",
                }
            )
        if getattr(frame.meta, "comparable_value_semantics", None) is not None:
            meta_update["comparable_value_semantics_ref"] = (
                f"{artifact_id}#comparable-value-semantics"
            )
        if hasattr(frame.meta, "affordances"):
            meta_update["affordances"] = []
        result = frame.meta.model_copy(update=meta_update)
        result = frame._bind_auxiliary_receipts(result, tuple(auxiliary_receipts))
        result = result.model_copy(
            update={
                "byte_size": parquet_path.stat().st_size
                + sum(item.byte_size for item in auxiliary_receipts)
                + (
                    (artifact_dir / quality_manifest.filename).stat().st_size
                    if quality_manifest is not None
                    else 0
                )
            }
        )
        return result.model_copy(
            update={"content_hash": compute_frame_content_hash(meta=result, data_path=parquet_path)}
        )

    # A ledger row is the publication marker. Withdraw an older Session Store
    # registration for the same deterministic ref before publishing a new
    # complete sidecar, otherwise a retry could expose that sidecar through the
    # stale registration before its canonical evidence exists.
    new_meta = build_meta()
    withdrawn_registration: dict[str, object] | None = None
    if session is not None:
        existing_registration = session._store.get_artifact(session.id, artifact_id)
        if existing_registration is not None:
            withdrawn_registration = dict(existing_registration)
            session._store.delete_artifact(session.id, artifact_id)

    def restore_withdrawn_registration() -> None:
        if session is None or withdrawn_registration is None:
            return
        session._store.record_artifact(
            session_id=str(withdrawn_registration["session_id"]),
            artifact_id=str(withdrawn_registration["artifact_id"]),
            kind=str(withdrawn_registration["kind"]),
            path=str(withdrawn_registration["path"]),
            meta_path=str(withdrawn_registration["meta_path"]),
            content_hash=cast("str | None", withdrawn_registration["content_hash"]),
            produced_by_job=cast("str | None", withdrawn_registration["produced_by_job"]),
            evidence_status=str(withdrawn_registration["evidence_status"]),
            created_at=str(withdrawn_registration["created_at"]),
        )

    def restore_previous_publication() -> None:
        if not previous_projection_exists or previous_meta_bytes is None:
            return
        _atomic_write_bytes(meta_path, previous_meta_bytes)
        restore_withdrawn_registration()

    try:
        _atomic_write_meta(meta_path, new_meta.model_dump(mode="json"))
    except BaseException:
        restore_withdrawn_registration()
        raise

    if store is not None:
        try:
            _insert_projection(
                store,
                artifact_id=artifact_id,
                session_id=frame.meta.session_id,
                step_type=step_type,
                extractor_family=extractor_family,
                subject=subject,
                lineage_payload=canonical_json(frame.meta.lineage),
                scope=scope,
                quality=quality,
                status=status,
                frame_path=str(parquet_path),
                frame_sha=frame_sha,
                findings=findings,
                digest=digest,
                issues=issues,
                committed_at=now,
            )
        except SessionLockedByAnotherProcessError:
            restore_previous_publication()
            raise
        except Exception as exc:
            try:
                status = "unavailable"
                findings = []
                digest = None
                store_issue = _store_failure_issue(
                    artifact_id=artifact_id,
                    operator=_operator_for(step_type, extractor_family),
                    stable_error_category=type(exc).__name__,
                    integrity_failure=isinstance(exc, sqlite3.IntegrityError),
                )
                issues.append(store_issue)
                new_meta = build_meta()
                _atomic_write_meta(meta_path, new_meta.model_dump(mode="json"))
            except BaseException:
                restore_previous_publication()
                raise
            try:
                _insert_projection(
                    store,
                    artifact_id=artifact_id,
                    session_id=frame.meta.session_id,
                    step_type=step_type,
                    extractor_family=extractor_family,
                    subject=subject,
                    lineage_payload=canonical_json(frame.meta.lineage),
                    scope=scope,
                    quality=quality,
                    status=status,
                    frame_path=str(parquet_path),
                    frame_sha=frame_sha,
                    findings=[],
                    digest=None,
                    issues=issues,
                    committed_at=now,
                )
            except SessionLockedByAnotherProcessError:
                restore_previous_publication()
                raise
            except Exception:
                if previous_projection_exists:
                    restore_previous_publication()
                    raise
                # The sidecar remains the only durable failure record when the
                # evidence store cannot even commit its unavailable projection.
                pass
    frame.meta = new_meta
    return frame


def rollback_committed_result(
    *,
    store: EvidenceStore | None,
    frames_dir: Path,
    artifact_id: str,
) -> None:
    """Remove a partially committed result from evidence and frame storage."""

    if store is not None:
        _remove_projection(store, artifact_id)
    artifact_dir = frames_dir / artifact_id
    if artifact_dir.is_dir():
        shutil.rmtree(artifact_dir)


__all__ = [
    "CommitInputs",
    "CommitParams",
    "CommitSemanticAnchors",
    "commit_result",
    "compute_prospective_artifact_id",
    "event_subject_for_frame",
    "frame_exists_on_disk",
    "lifecycle_subject_for_frame",
    "rollback_committed_result",
    "subject_set_subject_for_frame",
]
