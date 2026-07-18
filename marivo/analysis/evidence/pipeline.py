"""Commit typed analysis artifacts and deterministic evidence projections."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, TypeAdapter

from marivo.analysis.evidence.digest import build_artifact_digest
from marivo.analysis.evidence.extraction.anomaly import extract_anomaly_candidate_findings
from marivo.analysis.evidence.extraction.composition import extract_decomposition_findings
from marivo.analysis.evidence.extraction.correlation import extract_correlation_findings
from marivo.analysis.evidence.extraction.delta import extract_delta_findings
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
    to_microseconds_utc,
)
from marivo.analysis.evidence.store import EvidenceStore
from marivo.analysis.evidence.types import (
    AnalysisScope,
    ArtifactDigest,
    ArtifactIssue,
    EvidenceAvailabilityIssue,
    Finding,
    OperatorSemantics,
    QualitySummary,
    RawFallback,
    Subject,
)
from marivo.analysis.frames._content_hash import compute_frame_content_hash
from marivo.analysis.frames._meta_defaults import compute_analysis_scope, compute_quality_summary
from marivo.analysis.frames.base import BaseFrame
from marivo.telemetry import staged


class CommitInputs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    input_refs: list[str]


class CommitParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    values: dict[str, Any]


class CommitSemanticAnchors(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    values: dict[str, Any]


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
        semantic_anchors=semantic_anchors.values,
    )


def frame_exists_on_disk(frames_dir: Path, artifact_id: str) -> bool:
    """Return whether both canonical frame sidecar files are present."""
    frame_dir = frames_dir / artifact_id
    return all(
        path.is_file() and path.stat().st_size > 0
        for path in (frame_dir / "meta.json", frame_dir / "data.parquet")
    )


_ARTIFACT_SCHEMA_VERSION = "v2"
_EXTRACTOR_VERSION = "v2"
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


def _metric_entries(
    frame: BaseFrame, subject: Subject, df: pd.DataFrame
) -> list[tuple[Subject, str, str | None, bool]]:
    meta = frame.meta
    measures = getattr(meta, "measures", None)
    if measures:
        return [
            (
                subject.model_copy(update={"metric": entry["metric_id"]}),
                entry["column"],
                f"metric:{entry['metric_id']}",
                entry.get("additivity") == "additive",
            )
            for entry in measures
        ]
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
    return [(subject, str(column), None, getattr(meta, "additivity", None) == "additive")]


def _extract_findings(
    *,
    df: pd.DataFrame,
    artifact_id: str,
    session_id: str,
    subject: Subject,
    extractor_family: str,
    frame: BaseFrame,
    committed_at: datetime,
    scope: AnalysisScope,
) -> list[Finding]:
    meta = frame.meta
    semantic_kind = str(getattr(meta, "semantic_kind", "scalar"))
    if extractor_family == "metric_frame":
        findings: list[Finding] = []
        for entry_subject, column, prefix, additive in _metric_entries(frame, subject, df):
            time_column = (
                "bucket_start"
                if semantic_kind in {"time_series", "panel"} and "bucket_start" in df.columns
                else _time_column_from_meta(meta)
            )
            unit = getattr(meta, "unit", None)
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
        prepared = df.copy()
        driver_field = getattr(meta, "driver_field", None)
        contribution_column = getattr(meta, "contribution_column", None)
        if "dimension" not in prepared.columns and driver_field in prepared.columns:
            prepared["dimension"] = driver_field
        if "contribution_value" not in prepared.columns and contribution_column in prepared.columns:
            prepared["contribution_value"] = prepared[contribution_column]
        if (
            "contribution_share" not in prepared.columns
            and "share_of_total_delta" in prepared.columns
        ):
            prepared["contribution_share"] = prepared["share_of_total_delta"]
        reconciliation = getattr(meta, "reconciliation", None)
        if (
            "reconciliation_residual" not in prepared.columns
            and reconciliation is not None
            and reconciliation.residual is not None
        ):
            prepared["reconciliation_residual"] = reconciliation.residual
        findings = extract_decomposition_findings(
            df=prepared,
            artifact_id=artifact_id,
            session_id=session_id,
            subject=subject,
            committed_at=committed_at,
            scope_delta_ref=str(scope_delta_ref),
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
    elif extractor_family == "quality_report":
        findings = extract_quality_check_findings(
            df=df,
            artifact_id=artifact_id,
            session_id=session_id,
            subject=subject,
            committed_at=committed_at,
            evaluated_scope=scope,
            source_refs=tuple(str(ref) for ref in getattr(meta, "source_refs", ())),
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
    subject: Subject,
    lineage_payload: str,
    scope: AnalysisScope,
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
        persisted_meta = type(frame.meta).model_validate_json(meta_path.read_text(encoding="utf-8"))
        persisted_df = pd.read_parquet(parquet_path, engine="pyarrow", to_pandas_kwargs={})
    except Exception:
        return None
    if persisted_meta.artifact_id != artifact_id or persisted_meta.ref != artifact_id:
        return None
    frame.meta = persisted_meta
    frame._df = persisted_df
    return frame


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
    subject: Subject,
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
    scope = compute_analysis_scope(frame)
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


__all__ = [
    "CommitInputs",
    "CommitParams",
    "CommitSemanticAnchors",
    "commit_result",
    "compute_prospective_artifact_id",
    "frame_exists_on_disk",
]
