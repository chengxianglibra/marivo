"""Commit typed analysis artifacts and deterministic evidence projections."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
import hashlib
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

import pandas as pd
from pydantic import BaseModel, ConfigDict, TypeAdapter

from marivo.analysis._semantic_persistence import SlicePredicateV1
from marivo.analysis.errors import FrameMetaInvalidError
from marivo.analysis.evidence.digest import build_artifact_digest
from marivo.analysis.evidence.extraction.anomaly import extract_anomaly_candidate_findings
from marivo.analysis.evidence.extraction.composition import (
    DecompositionExtractionContract,
    extract_decomposition_findings,
)
from marivo.analysis.evidence.extraction.correlation import extract_correlation_findings
from marivo.analysis.evidence.extraction.delta import extract_delta_findings
from marivo.analysis.evidence.extraction.event import extract_event_journey_finding
from marivo.analysis.evidence.extraction.forecast import extract_forecast_point_findings
from marivo.analysis.evidence.extraction.observation import (
    extract_metric_value_findings,
    extract_observation_digest_finding,
)
from marivo.analysis.evidence.extraction.quality import extract_quality_check_findings
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
    OperatorSemantics,
    QualitySummary,
    RawFallback,
    Subject,
)
from marivo.analysis.frames._content_hash import compute_frame_content_hash
from marivo.analysis.frames._meta_defaults import compute_analysis_scope, compute_quality_summary
from marivo.analysis.frames.base import CURRENT_ARTIFACT_SCHEMA_VERSION, BaseFrame
from marivo.refs import RefPayloadV1
from marivo.semantic.metric_graph import (
    CatalogMetricIdentity,
    CatalogMetricSubjectV1,
    DeltaComparisonIdentityV1,
    DeltaMetricSubjectV1,
    RuntimeExpressionIdentity,
    RuntimeExpressionSubjectV1,
    SemanticDependencyDigestV1,
    TypedEvidenceSubject,
)
from marivo.semantic.metric_graph_canonical import canonical_value
from marivo.telemetry import staged


class CommitInputs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    input_refs: list[str]


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
    comparison_identity: DeltaComparisonIdentityV1 | None = None
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


def _atomic_write_parquet(df: pd.DataFrame, dest: Path) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(suffix=".tmp", dir=str(dest.parent))
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        df.to_parquet(tmp_path, index=False)
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
    measures = getattr(meta, "measures", None)
    if measures:
        identities = tuple(getattr(meta, "metric_identities", ()))
        entries: list[tuple[Subject, str, str | None, bool, str | None]] = []
        for index, entry in enumerate(measures):
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
            entries.append(
                (
                    subject.model_copy(update=updates),
                    entry["column"],
                    f"metric:{entry['metric_id']}",
                    entry.get("additivity") == "additive",
                    entry.get("unit") if isinstance(entry.get("unit"), str) else None,
                )
            )
        return entries
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
    if extractor_family == "quality_report":
        return extract_quality_check_findings(
            df=df,
            artifact_id=artifact_id,
            session_id=session_id,
            subject=subject,
            committed_at=committed_at,
            evaluated_scope=scope,
            source_refs=tuple(str(ref) for ref in getattr(meta, "source_refs", ())),
        )
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
        findings = extract_delta_findings(
            df=df,
            artifact_id=artifact_id,
            session_id=session_id,
            subject=subject,
            semantic_kind=semantic_kind,
            committed_at=committed_at,
            dimension_columns=_dimension_columns_from_meta(meta),
            unit=getattr(meta, "unit", None),
        )
    elif extractor_family == "attribution_frame":
        refs = getattr(meta, "source_refs", [])
        scope_delta_ref = getattr(meta, "scope_delta_ref", None) or (refs[0] if refs else None)
        if scope_delta_ref is None:
            return []
        driver_field = getattr(meta, "driver_field", None)
        contribution_column = getattr(meta, "contribution_column", None)
        reconciliation = getattr(meta, "reconciliation", None)
        params = getattr(meta, "params", {})
        axis_columns = params.get("axis_columns", []) if isinstance(params, dict) else []
        key_columns = [
            str(column)
            for column in axis_columns
            if isinstance(column, str) and column in df.columns
        ]
        if isinstance(params, dict) and params.get("mode") == "hierarchy":
            key_columns = [
                column for column in ("level", "axis", "driver", "path") if column in df.columns
            ] + key_columns
        bucket_column = params.get("bucket_column") if isinstance(params, dict) else None
        if isinstance(bucket_column, str) and bucket_column in df.columns:
            key_columns.insert(0, bucket_column)
        if not key_columns and isinstance(driver_field, str) and driver_field in df.columns:
            key_columns.append(driver_field)
        if not isinstance(contribution_column, str) or contribution_column not in df.columns:
            return []
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
            ),
        )
    elif extractor_family == "candidate_set":
        objective = getattr(meta, "discovery_objective", None) or getattr(meta, "objective", None)
        findings = (
            extract_anomaly_candidate_findings(
                df=df,
                artifact_id=artifact_id,
                session_id=session_id,
                subject=subject,
                committed_at=committed_at,
            )
            if objective == "point_anomalies"
            else []
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
    return _FINDINGS_ADAPTER.validate_python(findings)


def _operator_for(step_type: str, extractor_family: str) -> str:
    if step_type in {"transform", "select_metric"}:
        return step_type
    return {
        "metric_frame": "observe",
        "delta_frame": "compare",
        "attribution_frame": "attribute",
        "candidate_set": "discover",
        "association_result": "correlate",
        "hypothesis_test_result": "hypothesis_test",
        "forecast_frame": "forecast",
        "quality_report": "assess_quality",
        "event_frame": "events.match",
    }.get(extractor_family, step_type)


def _issue(
    artifact_id: str,
    kind: Literal["evidence_partial", "evidence_store_unavailable", "evidence_digest_unavailable"],
    failed_stage: Literal["extract", "digest", "store"],
    findings_available: bool,
    stable_error_category: str,
) -> EvidenceAvailabilityIssue:
    return EvidenceAvailabilityIssue(
        issue_id=make_issue_id(artifact_id=artifact_id, kind=kind, source_refs=(artifact_id,)),
        kind=kind,
        severity="blocking",
        source_refs=(artifact_id,),
        failed_stage=failed_stage,
        findings_available=findings_available,
        fallback=RawFallback(
            artifact_ref=artifact_id,
            findings_available=findings_available,
            rows_available=True,
            recommended_when=("partial_evidence",),
        ),
        stable_error_category=stable_error_category,
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
            """INSERT OR REPLACE INTO artifacts
               (artifact_id, session_id, step_type, artifact_type,
                artifact_schema_version, subject_payload, lineage_payload,
                analysis_scope, quality_summary, evidence_status,
                frame_path, frame_sha, committed_at_us)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
) -> BaseFrame | None:
    """Return an already committed immutable artifact without rewriting it."""
    if not parquet_path.is_file() or not meta_path.is_file():
        return None
    if store is not None:
        row = (
            store.read()
            .execute("SELECT 1 FROM artifacts WHERE artifact_id = ?", (artifact_id,))
            .fetchone()
        )
        if row is None:
            return None
    try:
        persisted_payload = json.loads(meta_path.read_text(encoding="utf-8"))
        if persisted_payload.get("artifact_schema_version") != CURRENT_ARTIFACT_SCHEMA_VERSION:
            raise FrameMetaInvalidError(
                message=(
                    f"artifact {artifact_id!r} uses a non-current schema; "
                    "recreate the analysis session"
                ),
                context={
                    "artifact_id": artifact_id,
                    "got": persisted_payload.get("artifact_schema_version"),
                    "expected": CURRENT_ARTIFACT_SCHEMA_VERSION,
                },
            )
        persisted_meta = (
            type(frame.meta).model_validate_json(json.dumps(persisted_payload))
            if getattr(frame.meta, "kind", None) == "event_frame"
            else type(frame.meta).model_validate(persisted_payload)
        )
        persisted_df = pd.read_parquet(parquet_path, engine="pyarrow", to_pandas_kwargs={})
    except FrameMetaInvalidError:
        raise
    except Exception:
        return None
    if persisted_meta.artifact_id != artifact_id or persisted_meta.ref != artifact_id:
        return None
    frame.meta = persisted_meta
    frame._df = persisted_df
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
    if isinstance(comparison_identity, DeltaComparisonIdentityV1):
        typed_subject = DeltaMetricSubjectV1(
            kind="delta_metric",
            session_id=frame.meta.session_id,
            comparison=comparison_identity,
        )
    if typed_subject is None:
        return subject
    return subject.model_copy(update={"typed_metric_subject": typed_subject})


def event_subject_for_frame(frame: BaseFrame) -> EventSubject:
    """Build the identity-safe evidence subject for an Event Journey frame."""
    meta = cast("Any", frame.meta)
    if getattr(meta, "kind", None) != "event_frame":
        raise TypeError("event_subject_for_frame requires EventFrame")
    return EventSubject(
        subject_entity_ref=meta.subject_entity_ref,
        subject_identity_signature=tuple(meta.subject_identity),
    )


@staged("evidence")
def commit_result(
    *,
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
    )
    if reused is not None:
        return reused
    df = frame._dataframe_copy()
    frame_sha = _atomic_write_parquet(df, parquet_path)
    scope = frame.meta.analysis_scope or compute_analysis_scope(frame)
    slice_predicates = getattr(frame.meta, "slice_predicates", ())
    if not slice_predicates:
        slice_predicates = semantic_anchors.slice_predicates
    if isinstance(subject, Subject) and isinstance(slice_predicates, tuple) and slice_predicates:
        subject = subject.model_copy(update={"slice_predicates": slice_predicates})
    if getattr(frame.meta, "kind", None) == "event_frame":
        subject = event_subject_for_frame(frame)
    elif isinstance(subject, Subject) and isinstance(scope, AnalysisScope):
        subject = _bind_typed_metric_subject(
            frame=frame,
            subject=subject,
            artifact_id=artifact_id,
            scope=scope,
            semantic_anchors=semantic_anchors,
        )
    quality = compute_quality_summary(frame)
    findings: list[Finding] = []
    digest: ArtifactDigest | None = None
    issues: list[ArtifactIssue] = list(frame.meta.issues)
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
                _issue(
                    artifact_id,
                    "evidence_partial",
                    failed_stage="extract",
                    findings_available=False,
                    stable_error_category=type(exc).__name__,
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
                    _issue(
                        artifact_id,
                        "evidence_digest_unavailable",
                        failed_stage="digest",
                        findings_available=True,
                        stable_error_category=type(exc).__name__,
                    )
                )
    elif emit_evidence and store is None:
        issues.append(
            _issue(
                artifact_id,
                "evidence_store_unavailable",
                failed_stage="store",
                findings_available=False,
                stable_error_category="store_unavailable",
            )
        )

    projection_inserted = False
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
            projection_inserted = True
        except Exception as exc:
            status = "unavailable"
            findings = []
            digest = None
            issues.append(
                _issue(
                    artifact_id,
                    "evidence_store_unavailable",
                    failed_stage="store",
                    findings_available=False,
                    stable_error_category=type(exc).__name__,
                )
            )

    meta_update: dict[str, Any] = {
        "ref": artifact_id,
        "artifact_id": artifact_id,
        "evidence_status": status,
        "analysis_scope": scope,
        "quality_summary": quality,
        "evidence_digest": digest,
        "issues": tuple(issues),
    }
    if getattr(frame.meta, "expression_graph", None) is not None:
        meta_update.update(
            {
                "expression_graph_ref": f"{artifact_id}#expression-graph",
                "presentation_ref": f"{artifact_id}#presentation",
                "replay_graph_ref": f"{artifact_id}#replay-graph",
                "quality_ref": f"{artifact_id}#quality",
            }
        )
    if getattr(frame.meta, "comparable_value_semantics", None) is not None:
        meta_update["comparable_value_semantics_ref"] = f"{artifact_id}#comparable-value-semantics"
    if hasattr(frame.meta, "affordances"):
        meta_update["affordances"] = []
    new_meta = frame.meta.model_copy(update=meta_update)
    new_meta = new_meta.model_copy(
        update={"content_hash": compute_frame_content_hash(meta=new_meta, data_path=parquet_path)}
    )

    try:
        _atomic_write_meta(meta_path, new_meta.model_dump(mode="json"))
    except BaseException:
        if store is not None and projection_inserted:
            _remove_projection(store, artifact_id)
        raise
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
    "rollback_committed_result",
]
