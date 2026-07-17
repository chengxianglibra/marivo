"""commit_result pipeline: orchestrates evidence persistence for operator outputs.

Handles three paths:
1. Complete: store available, all phases succeed -> evidence_status="complete"
2. Partial: store available, SAVEPOINT phase fails -> evidence_status="partial"
3. Unavailable: store=None -> evidence_status="unavailable"
"""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict

from marivo.analysis.evidence.assessment import (
    recompute_anomaly_assessment,
    recompute_association_assessment,
    recompute_change_assessment,
    recompute_driver_assessment,
    recompute_forecast_assessment,
    recompute_test_hypothesis_assessment,
)
from marivo.analysis.evidence.extraction.anomaly import (
    extract_anomaly_candidate_findings,
)
from marivo.analysis.evidence.extraction.composition import (
    extract_decomposition_findings,
)
from marivo.analysis.evidence.extraction.correlation import extract_correlation_findings
from marivo.analysis.evidence.extraction.delta import extract_delta_findings
from marivo.analysis.evidence.extraction.forecast import extract_forecast_point_findings
from marivo.analysis.evidence.extraction.observation import (
    extract_metric_value_findings,
    extract_observation_digest_finding,
)
from marivo.analysis.evidence.extraction.test import extract_test_result_findings
from marivo.analysis.evidence.followups import GenerationContext, generate_followups
from marivo.analysis.evidence.identity import (
    canonical_json,
    make_artifact_id,
    make_issue_id,
    to_microseconds_utc,
)
from marivo.analysis.evidence.knowledge import build_artifact_evidence_projection
from marivo.analysis.evidence.seeding import (
    seed_anomaly_proposition,
    seed_change_proposition,
    seed_correlation_proposition,
    seed_driver_proposition,
    seed_forecast_proposition,
    seed_test_hypothesis_proposition,
)
from marivo.analysis.evidence.store import JudgmentStore
from marivo.analysis.evidence.summary import build_artifact_evidence_summary
from marivo.analysis.evidence.types import (
    ArtifactEvidenceSummary,
    Finding,
    Proposition,
    QualitySummary,
    Subject,
    TriggeredByFollowup,
)
from marivo.analysis.followups import BlockingIssue, ConfidenceScope, FollowupAction
from marivo.analysis.frames._meta_defaults import compute_confidence_scope, compute_quality_summary
from marivo.analysis.frames.base import BaseFrame

# --- Public DTOs ---


class CommitInputs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    input_refs: list[str]


class CommitParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    values: dict[str, Any]


class CommitSemanticAnchors(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    values: dict[str, Any]


# --- Public cache helpers ---


def compute_prospective_artifact_id(
    *,
    step_type: str,
    inputs: CommitInputs,
    params: CommitParams,
    semantic_anchors: CommitSemanticAnchors,
) -> str:
    """Compute the deterministic artifact_id that :func:`commit_result` would assign.

    Uses the same :func:`make_artifact_id` call as ``commit_result``, so the
    result is identical for the same inputs.  Callers use this to check whether
    a frame already exists on disk before executing an expensive backend query.
    """
    return make_artifact_id(
        step_type=step_type,
        normalized_inputs=inputs.input_refs,
        normalized_params=params.values,
        semantic_anchors=semantic_anchors.values,
    )


def frame_exists_on_disk(frames_dir: Path, artifact_id: str) -> bool:
    """Return True if both meta.json and data.parquet exist and are non-empty."""
    frame_dir = frames_dir / artifact_id
    meta_file = frame_dir / "meta.json"
    parquet_file = frame_dir / "data.parquet"
    return (
        meta_file.is_file()
        and meta_file.stat().st_size > 0
        and parquet_file.is_file()
        and parquet_file.stat().st_size > 0
    )


# --- Internal helpers ---

_ARTIFACT_SCHEMA_VERSION = "v1"
_EXTRACTOR_VERSION = "v1"


def _dimension_columns_from_meta(meta: Any) -> list[str] | None:
    """Extract dimension column names from frame meta axes or alignment."""
    # Try axes directly (MetricFrameMeta)
    axes = getattr(meta, "axes", None)
    if not axes or not isinstance(axes, dict):
        # Try alignment.axes (DeltaFrameMeta stores axes in alignment dict)
        alignment = getattr(meta, "alignment", None)
        if isinstance(alignment, dict):
            axes = alignment.get("axes")
    if not axes or not isinstance(axes, dict):
        return None
    columns: list[str] = []
    for axis in axes.values():
        if not isinstance(axis, dict):
            continue
        if axis.get("role") != "dimension":
            continue
        column = axis.get("column")
        if isinstance(column, str) and column:
            columns.append(column)
    return sorted(columns) if columns else None


def _time_column_from_meta(meta: Any) -> str | None:
    axes = getattr(meta, "axes", None)
    if isinstance(axes, dict):
        for axis in axes.values():
            if isinstance(axis, dict) and axis.get("role") == "time":
                column = axis.get("column") or axis.get("field")
                if isinstance(column, str) and column:
                    return column
        time_axis = axes.get("time")
        if isinstance(time_axis, dict):
            column = time_axis.get("column") or time_axis.get("field")
            if isinstance(column, str) and column:
                return column
    return None


def _atomic_write_parquet(df: pd.DataFrame, dest: Path) -> str:
    """Write DataFrame to Parquet atomically via .tmp + fsync + os.replace.

    Returns the SHA-256 hex digest of the written file.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(suffix=".tmp", dir=str(dest.parent))
    os.close(fd)
    tmp_path = Path(tmp_path_str)
    try:
        df.to_parquet(tmp_path, index=False)
        # fsync the file
        with open(tmp_path, "rb") as f:
            content = f.read()
            os.fsync(f.fileno())
        sha = hashlib.sha256(content).hexdigest()
        os.replace(str(tmp_path), str(dest))
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return sha


def _write_meta_json(meta_path: Path, meta_dict: dict[str, Any]) -> None:
    """Write meta.json alongside data.parquet."""
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(meta_dict, indent=2, default=str)
    meta_path.write_text(content, encoding="utf-8")


def _extract_findings(
    *,
    df: pd.DataFrame,
    artifact_id: str,
    session_id: str,
    subject: Subject,
    extractor_family: str,
    frame: BaseFrame,
    committed_at: datetime,
) -> list[Finding]:
    """Dispatch to the appropriate finding extractor based on family."""
    meta = frame.meta
    semantic_kind = getattr(meta, "semantic_kind", "scalar")
    if extractor_family == "metric_frame":
        measures_meta = getattr(meta, "measures", None)
        if measures_meta:
            entries: list[tuple[Subject, str, str | None, bool]] = [
                (
                    subject.model_copy(update={"metric": entry["metric_id"]}),
                    entry["column"],
                    f"metric:{entry['metric_id']}",
                    entry.get("additivity") == "additive",
                )
                for entry in measures_meta
            ]
        else:
            # Legacy single-measure resolution, unchanged.
            measure = getattr(meta, "measure", {})
            measure_column = (
                measure.get("name") or measure.get("column") or measure.get("field") or "value"
                if isinstance(measure, dict)
                else "value"
            )
            if measure_column not in df.columns:
                axis_columns = set(_dimension_columns_from_meta(meta) or [])
                axis_columns.add("bucket_start")
                non_axis = [c for c in df.columns if c not in axis_columns]
                if "value" in non_axis:
                    measure_column = "value"
                else:
                    measure_column = non_axis[0] if non_axis else "value"
            entries = [
                (subject, measure_column, None, getattr(meta, "additivity", None) == "additive")
            ]
        time_column: str | None = None
        if semantic_kind == "time_series" and "bucket_start" in df.columns:
            time_column = "bucket_start"
        elif semantic_kind == "time_series":
            time_column = _time_column_from_meta(meta)
        digest_time_column = time_column
        if semantic_kind == "panel":
            digest_time_column = (
                "bucket_start" if "bucket_start" in df.columns else _time_column_from_meta(meta)
            )
        findings: list[Finding] = []
        for entry_subject, entry_column, key_prefix, additive in entries:
            findings.extend(
                extract_metric_value_findings(
                    df=df,
                    artifact_id=artifact_id,
                    session_id=session_id,
                    subject=entry_subject,
                    semantic_kind=semantic_kind,
                    measure_column=entry_column,
                    committed_at=committed_at,
                    time_column=time_column,
                    item_key_prefix=key_prefix,
                )
            )
            findings.append(
                extract_observation_digest_finding(
                    df=df,
                    artifact_id=artifact_id,
                    session_id=session_id,
                    subject=entry_subject,
                    semantic_kind=semantic_kind,
                    measure_column=entry_column,
                    committed_at=committed_at,
                    time_column=digest_time_column,
                    dimension_columns=_dimension_columns_from_meta(meta),
                    window=getattr(meta, "window", None),
                    analysis_purpose=getattr(meta, "analysis_purpose", None),
                    additive=additive,
                    item_key_prefix=key_prefix,
                )
            )
        return findings
    if extractor_family == "delta_frame":
        dimension_columns = _dimension_columns_from_meta(meta)
        return extract_delta_findings(
            df=df,
            artifact_id=artifact_id,
            session_id=session_id,
            subject=subject,
            semantic_kind=semantic_kind,
            committed_at=committed_at,
            dimension_columns=dimension_columns,
            unit=getattr(meta, "unit", None),
        )
    if extractor_family == "attribution_frame":
        scope_delta_ref = getattr(meta, "scope_delta_ref", None)
        if not scope_delta_ref:
            source_refs = getattr(meta, "source_refs", [])
            scope_delta_ref = source_refs[0] if source_refs else None
        if not scope_delta_ref:
            return []
        decomp_df = df
        driver_field = getattr(meta, "driver_field", None)
        contribution_column = getattr(meta, "contribution_column", None)
        if (
            "dimension" not in decomp_df.columns
            and isinstance(driver_field, str)
            and driver_field in decomp_df.columns
            and isinstance(contribution_column, str)
            and contribution_column in decomp_df.columns
        ):
            decomp_df = decomp_df.copy()
            decomp_df["dimension"] = driver_field
            decomp_df["contribution_value"] = decomp_df[contribution_column]
            share_column = "contribution_share"
            if share_column not in decomp_df.columns:
                if "pct_contribution" in decomp_df.columns:
                    share_column = "pct_contribution"
                elif "contribution_share" in decomp_df.columns:
                    share_column = "contribution_share"
            if share_column in decomp_df.columns:
                decomp_df["contribution_share"] = decomp_df[share_column]
            if "direction" not in decomp_df.columns:
                decomp_df["direction"] = decomp_df["contribution_value"].map(
                    lambda value: "increase" if value > 0 else "decrease" if value < 0 else "flat"
                )
        return extract_decomposition_findings(
            df=decomp_df,
            artifact_id=artifact_id,
            session_id=session_id,
            subject=subject,
            committed_at=committed_at,
            scope_delta_ref=str(scope_delta_ref),
        )
    if extractor_family == "candidate_set":
        objective = getattr(meta, "discovery_objective", None) or getattr(meta, "objective", None)
        if objective != "point_anomalies":
            return []
        return extract_anomaly_candidate_findings(
            df=df,
            artifact_id=artifact_id,
            session_id=session_id,
            subject=subject,
            committed_at=committed_at,
        )
    if extractor_family == "association_result":
        assoc_df = df
        if "coefficient" not in assoc_df.columns and "correlation" in assoc_df.columns:
            assoc_df = assoc_df.copy()
            source_refs = getattr(meta, "source_refs", [])
            assoc_df["left_ref"] = source_refs[0] if len(source_refs) > 0 else None
            assoc_df["right_ref"] = source_refs[1] if len(source_refs) > 1 else None
            assoc_df["coefficient"] = assoc_df["correlation"]
            if "join_basis" not in assoc_df.columns:
                alignment = getattr(meta, "alignment", {})
                assoc_df["join_basis"] = (
                    alignment.get("kind") if isinstance(alignment, dict) else None
                )
        return extract_correlation_findings(
            df=assoc_df,
            artifact_id=artifact_id,
            session_id=session_id,
            subject=subject,
            committed_at=committed_at,
        )
    if extractor_family == "hypothesis_test_result":
        test_df = df
        if "reject_null" not in test_df.columns and "rejected" in test_df.columns:
            test_df = test_df.copy()
            source_refs = getattr(meta, "source_refs", [])
            test_df["current_ref"] = source_refs[0] if len(source_refs) > 0 else None
            test_df["baseline_ref"] = source_refs[1] if len(source_refs) > 1 else None
            test_df["method"] = getattr(meta, "method", None)
            test_df["estimate_value"] = test_df.get("mean_diff")
            test_df["statistic_name"] = "t"
            test_df["statistic_value"] = test_df.get("test_statistic")
            test_df["reject_null"] = test_df["rejected"]
            test_df["alpha"] = getattr(meta, "alpha", None)
        return extract_test_result_findings(
            df=test_df,
            artifact_id=artifact_id,
            session_id=session_id,
            subject=subject,
            committed_at=committed_at,
        )
    if extractor_family == "forecast_frame":
        forecast_df = df
        if "predicted_value" not in forecast_df.columns and "predicted" in forecast_df.columns:
            forecast_df = forecast_df.copy()
            if "bucket_start" not in forecast_df.columns and "time" in forecast_df.columns:
                forecast_df["bucket_start"] = forecast_df["time"]
            if "bucket_end" not in forecast_df.columns and "time" in forecast_df.columns:
                forecast_df["bucket_end"] = forecast_df["time"]
            forecast_df["predicted_value"] = forecast_df["predicted"]
        return extract_forecast_point_findings(
            df=forecast_df,
            artifact_id=artifact_id,
            session_id=session_id,
            subject=subject,
            committed_at=committed_at,
        )
    if extractor_family == "quality_report":
        return []
    # Unknown families (e.g. "projection") are intentionally finding-free:
    # select_metric projections inherit evidence from the parent observe.
    return []


def _seed_for_finding(
    *,
    finding: Finding,
    step_type: str,
    comparison_window: dict[str, Any] | None,
    comparison_basis: str | None,
    seeding_context: dict[str, Any] | None,
) -> tuple[Proposition | None, Any]:
    """Return the proposition and assessment recompute function for a finding."""
    ctx = seeding_context or {}
    if finding.finding_type == "delta" and step_type == "compare":
        prop = seed_change_proposition(
            finding=finding,
            comparison_window=comparison_window or {},
            comparison_basis=comparison_basis or "left_vs_right",
        )
        return prop, recompute_change_assessment
    if finding.finding_type == "decomposition_item":
        prop = seed_driver_proposition(
            finding=finding,
            observed_window=ctx.get("observed_window"),
        )
        return prop, recompute_driver_assessment
    if finding.finding_type == "anomaly_candidate":
        prop = seed_anomaly_proposition(
            finding=finding,
            observed_window=ctx.get("observed_window"),
        )
        return prop, recompute_anomaly_assessment
    if finding.finding_type == "correlation_result":
        prop = seed_correlation_proposition(
            finding=finding,
            aligned_window=ctx.get("aligned_window"),
            left_subject=ctx.get("left_subject", {}),
            right_subject=ctx.get("right_subject", {}),
        )
        return prop, recompute_association_assessment
    if finding.finding_type == "test_result":
        prop = seed_test_hypothesis_proposition(
            finding=finding,
            left_subject=ctx.get("left_subject", {}),
            right_subject=ctx.get("right_subject", {}),
            alternative=ctx.get("alternative", "two_sided"),
        )
        return prop, recompute_test_hypothesis_assessment
    if finding.finding_type == "forecast_point":
        prop = seed_forecast_proposition(finding=finding)
        return prop, recompute_forecast_assessment
    return None, None


def _insert_artifact(
    tx: Any,
    *,
    artifact_id: str,
    session_id: str,
    step_type: str,
    artifact_type: str,
    subject: Subject,
    lineage_payload: str,
    confidence_scope: ConfidenceScope | None,
    quality_summary: QualitySummary | None,
    evidence_status: str,
    frame_path: str | None,
    frame_sha: str | None,
    committed_at: datetime,
    triggered_by_followup: TriggeredByFollowup | None = None,
) -> None:
    """Insert a single artifact row."""
    triggered_json: str | None = None
    if triggered_by_followup is not None:
        triggered_json = canonical_json(triggered_by_followup.model_dump(mode="json"))
    tx.execute(
        """INSERT OR REPLACE INTO artifacts
           (artifact_id, session_id, step_type, artifact_type,
            artifact_schema_version, subject_payload, lineage_payload,
            confidence_scope, quality_summary, evidence_status,
            frame_path, frame_sha, triggered_by_followup, committed_at_us)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            artifact_id,
            session_id,
            step_type,
            artifact_type,
            _ARTIFACT_SCHEMA_VERSION,
            canonical_json(subject.model_dump(mode="json")),
            lineage_payload,
            canonical_json(confidence_scope.model_dump(mode="json")) if confidence_scope else None,
            canonical_json(quality_summary.model_dump(mode="json")) if quality_summary else None,
            evidence_status,
            frame_path,
            frame_sha,
            triggered_json,
            to_microseconds_utc(committed_at),
        ),
    )


def _insert_findings(tx: Any, findings: list[Finding], *, session_id: str) -> None:
    """Insert finding rows."""
    for f in findings:
        tx.execute(
            """INSERT OR REPLACE INTO findings
               (finding_id, session_id, artifact_id, finding_type,
                canonical_item_key, subject_axis, subject_payload,
                observed_window_start_us, observed_window_end_us,
                quality_status, payload, artifact_schema_version,
                extractor_version, committed_at_us)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                f.finding_id,
                session_id,
                f.artifact_id,
                f.finding_type,
                f.canonical_item_key,
                f.subject.analysis_axis,
                canonical_json(f.subject.model_dump(mode="json")),
                None,
                None,
                f.quality_status,
                canonical_json(f.payload),
                _ARTIFACT_SCHEMA_VERSION,
                _EXTRACTOR_VERSION,
                to_microseconds_utc(f.committed_at),
            ),
        )


def _insert_proposition(tx: Any, prop: Proposition) -> None:
    """Insert a proposition row."""
    tx.execute(
        """INSERT OR REPLACE INTO propositions
           (proposition_id, session_id, proposition_type, origin_kind,
            derivation_version, subject_key, payload, seed_finding_refs,
            created_at_us)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            prop.proposition_id,
            prop.session_id,
            prop.proposition_type,
            prop.origin_kind,
            prop.derivation_version,
            prop.subject_key,
            canonical_json(prop.payload),
            canonical_json(prop.seed_finding_refs),
            to_microseconds_utc(prop.created_at),
        ),
    )


def _insert_assessment(tx: Any, assessment: Any, edges: list[tuple[str, str]]) -> None:
    """Insert an assessment snapshot and its edges."""
    tx.execute(
        """INSERT OR REPLACE INTO assessment_snapshots
           (snapshot_id, proposition_id, session_id, supersedes_id,
            status, confidence, confidence_basis, payload,
            created_at_us, is_latest)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            assessment.snapshot_id,
            assessment.proposition_id,
            assessment.session_id,
            assessment.supersedes_id,
            assessment.status,
            assessment.confidence,
            assessment.confidence_basis,
            canonical_json(assessment.payload),
            to_microseconds_utc(assessment.created_at),
            1 if assessment.is_latest else 0,
        ),
    )
    for finding_id, role in edges:
        tx.execute(
            """INSERT OR REPLACE INTO assessment_edges
               (snapshot_id, finding_id, role) VALUES (?, ?, ?)""",
            (assessment.snapshot_id, finding_id, role),
        )


def _insert_followups(
    tx: Any,
    followups: list[FollowupAction],
    *,
    session_id: str,
    source_artifact_id: str,
    committed_at: datetime,
) -> None:
    """Insert followup action rows."""
    for f in followups:
        tx.execute(
            """INSERT OR REPLACE INTO followups
               (followup_id, session_id, source_artifact_id, category,
                source_issue_id, operator, payload, executed_step_id,
                created_at_us)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                f.action_id,
                session_id,
                source_artifact_id,
                f.category or "dag_continuation",
                f.source_issue_id,
                f.operator,
                canonical_json(f.model_dump(mode="json")),
                None,
                to_microseconds_utc(committed_at),
            ),
        )


def _insert_blocking_issue(
    tx: Any,
    issue: BlockingIssue,
    *,
    session_id: str,
    artifact_id: str,
    committed_at: datetime,
) -> None:
    """Insert a blocking issue row."""
    tx.execute(
        """INSERT OR REPLACE INTO blocking_issues
           (issue_id, session_id, artifact_id, kind, severity,
            payload, resolved_by_step_id, created_at_us)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            issue.issue_id,
            session_id,
            artifact_id,
            issue.kind,
            issue.severity,
            canonical_json({"message": issue.message, "source_refs": issue.source_refs}),
            None,
            to_microseconds_utc(committed_at),
        ),
    )


# --- Main entry point ---


def commit_result(
    *,
    store: JudgmentStore | None,
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
    triggered_by_followup: TriggeredByFollowup | None = None,
    emit_evidence: bool = True,
) -> BaseFrame:
    """Commit a computed frame to the evidence store.

    Orchestrates: deterministic artifact_id, atomic Parquet write,
    SQLite transaction (artifact + findings), SAVEPOINT (seeding +
    assessment + followups), partial-failure handling, and Surface 1
    field population on frame.meta.

    When ``emit_evidence`` is ``False``, findings extraction, proposition
    seeding, assessment, and followup generation are all skipped; the
    artifact row is still inserted with its true ``extractor_family`` and
    ``evidence_status`` remains ``"complete"``.
    """
    now = datetime.now(UTC)
    session_id = frame.meta.session_id

    # 1. Deterministic artifact ID
    artifact_id = make_artifact_id(
        step_type=step_type,
        normalized_inputs=inputs.input_refs,
        normalized_params=params.values,
        semantic_anchors=semantic_anchors.values,
    )

    # 2. Atomic Parquet write
    artifact_dir = frames_dir / artifact_id
    parquet_path = artifact_dir / "data.parquet"
    df = frame._dataframe_copy()
    frame_sha = _atomic_write_parquet(df, parquet_path)

    # 2c. Compute confidence_scope and quality_summary (step 4c)
    confidence_scope = compute_confidence_scope(frame)
    quality_summary = compute_quality_summary(frame)

    # 3. Handle unavailable path (store=None)
    if store is None:
        issue_id = make_issue_id(
            artifact_id=artifact_id,
            kind="evidence_store_unavailable",
            source_refs=[artifact_id],
        )
        issue = BlockingIssue(
            issue_id=issue_id,
            kind="evidence_store_unavailable",
            severity="blocking",
            source_refs=[artifact_id],
            message="evidence store not available; evidence pipeline skipped",
        )
        unavailable_update: dict[str, Any] = {
            "ref": artifact_id,
            "artifact_id": artifact_id,
            "evidence_status": "unavailable",
            "blocking_issues": [issue],
            "confidence_scope": confidence_scope,
            "quality_summary": quality_summary,
        }
        if hasattr(frame.meta, "affordances"):
            unavailable_update["affordances"] = []
        new_meta = frame.meta.model_copy(update=unavailable_update)
        frame.meta = new_meta
        _write_meta_json(
            artifact_dir / "meta.json",
            new_meta.model_dump(mode="json"),
        )
        return frame

    # 4. Extract findings (suppressed when emit_evidence=False)
    findings = (
        _extract_findings(
            df=df,
            artifact_id=artifact_id,
            session_id=session_id,
            subject=subject,
            extractor_family=extractor_family,
            frame=frame,
            committed_at=now,
        )
        if emit_evidence
        else []
    )

    # 5. Open transaction: insert artifact + findings
    # Then SAVEPOINT for seeding + assessment + followups
    evidence_status = "complete"
    blocking_issues: list[BlockingIssue] = list(frame.meta.blocking_issues)
    followups: list[FollowupAction] = []

    lineage_payload = canonical_json(
        {"steps": [], "external_inputs": []}
        if not frame.meta.lineage.steps
        else {
            "steps": [
                {
                    "intent": s.intent,
                    "job_ref": s.job_ref,
                    "inputs": s.inputs,
                    "params_digest": s.params_digest,
                }
                for s in frame.meta.lineage.steps
            ],
            "external_inputs": frame.meta.lineage.external_inputs,
        }
    )

    with store.transaction(immediate=True) as tx:
        # Insert artifact
        _insert_artifact(
            tx,
            artifact_id=artifact_id,
            session_id=session_id,
            step_type=step_type,
            artifact_type=extractor_family,
            subject=subject,
            lineage_payload=lineage_payload,
            confidence_scope=confidence_scope,
            quality_summary=quality_summary,
            evidence_status="complete",
            frame_path=str(parquet_path),
            frame_sha=frame_sha,
            committed_at=now,
            triggered_by_followup=triggered_by_followup,
        )

        # Insert findings
        _insert_findings(tx, findings, session_id=session_id)

        # SAVEPOINT: seeding + assessment + followups (skipped when emit_evidence=False)
        if emit_evidence:
            try:
                with tx.savepoint("evidence_phase2"):
                    if findings:
                        for finding in findings:
                            prop, assessment_fn = _seed_for_finding(
                                finding=finding,
                                step_type=step_type,
                                comparison_window=comparison_window,
                                comparison_basis=comparison_basis,
                                seeding_context=seeding_context,
                            )
                            if prop is None or assessment_fn is None:
                                continue
                            _insert_proposition(tx, prop)
                            assessment, edges = assessment_fn(
                                proposition=prop,
                                seed_findings=[finding],
                                snapshot_seq=1,
                                previous=None,
                            )
                            _insert_assessment(tx, assessment, edges)

                    # Generate followups
                    semantic_kind = getattr(frame.meta, "semantic_kind", "scalar")
                    ctx = GenerationContext(
                        source_artifact_id=artifact_id,
                        source_family=extractor_family,
                        source_semantic_kind=semantic_kind,
                        blocking_issues=[]
                        if extractor_family == "quality_report"
                        else blocking_issues,
                    )
                    followups = generate_followups(ctx)

                    # Insert followups
                    _insert_followups(
                        tx,
                        followups,
                        session_id=session_id,
                        source_artifact_id=artifact_id,
                        committed_at=now,
                    )
            except Exception:
                # SAVEPOINT rolled back; artifact + findings retained
                evidence_status = "partial"
                issue_id = make_issue_id(
                    artifact_id=artifact_id,
                    kind="evidence_partial",
                    source_refs=[artifact_id],
                )
                partial_issue = BlockingIssue(
                    issue_id=issue_id,
                    kind="evidence_partial",
                    severity="warning",
                    source_refs=[artifact_id],
                    message="evidence pipeline phase 2 failed; artifact and findings retained",
                )
                blocking_issues = [partial_issue]
                followups = []
                _insert_blocking_issue(
                    tx,
                    partial_issue,
                    session_id=session_id,
                    artifact_id=artifact_id,
                    committed_at=now,
                )
                # Update artifact evidence_status
                tx.execute(
                    "UPDATE artifacts SET evidence_status = ? WHERE artifact_id = ?",
                    ("partial", artifact_id),
                )

    # 6. Mark followup as executed (after main transaction)
    if triggered_by_followup is not None:
        with store.transaction() as tx:
            tx.execute(
                "UPDATE followups SET executed_step_id=? WHERE followup_id=?",
                (artifact_id, triggered_by_followup.action_id),
            )

    # 6b. Build commit-time evidence summary (frame-local, non-canonical)
    evidence_summary: ArtifactEvidenceSummary | None = None
    if emit_evidence:
        try:
            projection = build_artifact_evidence_projection(
                db_path=store.db_path,
                session_id=session_id,
                artifact_id=artifact_id,
            )
            evidence_summary = build_artifact_evidence_summary(projection)
        except Exception:
            summary_issue = BlockingIssue(
                issue_id=make_issue_id(
                    artifact_id=artifact_id,
                    kind="evidence_summary_unavailable",
                    source_refs=[artifact_id],
                ),
                kind="evidence_summary_unavailable",
                severity="warning",
                source_refs=[artifact_id],
                message=(
                    "commit-time evidence summary unavailable; use session.evidence "
                    "to inspect canonical records"
                ),
            )
            blocking_issues = [*blocking_issues, summary_issue]

    # 7. Update frame.meta with Surface 1 fields
    meta_update: dict[str, Any] = {
        "ref": artifact_id,
        "artifact_id": artifact_id,
        "evidence_status": evidence_status,
        "blocking_issues": blocking_issues,
        "confidence_scope": confidence_scope,
        "quality_summary": quality_summary,
        "evidence_summary": evidence_summary,
    }
    # CandidateSetMeta declares affordances; other metas do not have it.
    if hasattr(frame.meta, "affordances"):
        import importlib

        from marivo.analysis.frames.base import ArtifactAffordance, ArtifactParamTemplate

        registry_module = importlib.import_module("marivo.analysis._capabilities.registry")
        registry = registry_module.REGISTRY
        affordances = []
        for f in followups:
            cap_id = f.operator or ""
            public_entrypoint = ""
            help_target = ""
            try:
                desc = registry.by_id(cap_id)
                public_entrypoint = desc.public_entrypoint
                help_target = desc.help_target
            except KeyError:
                public_entrypoint = cap_id
                help_target = cap_id
            affordances.append(
                ArtifactAffordance(
                    capability_id=cap_id,
                    public_entrypoint=public_entrypoint,
                    help_target=help_target,
                    required_inputs=f.input_refs,
                    preconditions=[],
                    param_template=ArtifactParamTemplate(
                        deterministic_slots=f.params,
                        judgment_slots=[],
                    ),
                    expected_output_family=f.expected_output_family,
                )
            )
        meta_update["affordances"] = affordances
    new_meta = frame.meta.model_copy(update=meta_update)
    frame.meta = new_meta

    # Write meta.json
    _write_meta_json(
        artifact_dir / "meta.json",
        new_meta.model_dump(mode="json"),
    )

    return frame


__all__ = [
    "CommitInputs",
    "CommitParams",
    "CommitSemanticAnchors",
    "commit_result",
]
