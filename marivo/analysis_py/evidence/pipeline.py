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

from marivo.analysis_py.evidence.assessment import recompute_change_assessment
from marivo.analysis_py.evidence.extraction.delta import extract_delta_findings
from marivo.analysis_py.evidence.extraction.observation import extract_metric_value_findings
from marivo.analysis_py.evidence.followups import GenerationContext, generate_followups
from marivo.analysis_py.evidence.identity import (
    canonical_json,
    make_artifact_id,
    make_issue_id,
    to_microseconds_utc,
)
from marivo.analysis_py.evidence.seeding import seed_change_proposition
from marivo.analysis_py.evidence.store import JudgmentStore
from marivo.analysis_py.evidence.types import Finding, Proposition, Subject, TriggeredByFollowup
from marivo.analysis_py.followups import BlockingIssue, FollowupAction
from marivo.analysis_py.frames.base import BaseFrame

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


def _atomic_write_parquet(df: pd.DataFrame, dest: Path) -> str:
    """Write DataFrame to Parquet atomically via .tmp + fsync + os.replace.

    Returns the SHA-256 hex digest of the written file.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(
        suffix=".tmp", dir=str(dest.parent)
    )
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
        measure = getattr(meta, "measure", {})
        measure_column = measure.get("name", "value") if isinstance(measure, dict) else "value"
        # Fall back if measure_column is not in the DataFrame
        if measure_column not in df.columns:
            non_time = [c for c in df.columns if c != "bucket_start"]
            measure_column = non_time[0] if non_time else "value"
        time_column: str | None = None
        if semantic_kind == "time_series" and "bucket_start" in df.columns:
            time_column = "bucket_start"
        return extract_metric_value_findings(
            df=df,
            artifact_id=artifact_id,
            session_id=session_id,
            subject=subject,
            semantic_kind=semantic_kind,
            measure_column=measure_column,
            committed_at=committed_at,
            time_column=time_column,
        )
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
        )
    return []


def _insert_artifact(
    tx: Any,
    *,
    artifact_id: str,
    session_id: str,
    step_type: str,
    artifact_type: str,
    subject: Subject,
    lineage_payload: str,
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
            None,
            None,
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
    triggered_by_followup: TriggeredByFollowup | None = None,
) -> BaseFrame:
    """Commit a computed frame to the evidence store.

    Orchestrates: deterministic artifact_id, atomic Parquet write,
    SQLite transaction (artifact + findings), SAVEPOINT (seeding +
    assessment + followups), partial-failure handling, and Surface 1
    field population on frame.meta.
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
    df = frame.to_pandas()
    frame_sha = _atomic_write_parquet(df, parquet_path)

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
        new_meta = frame.meta.model_copy(
            update={
                "ref": artifact_id,
                "artifact_id": artifact_id,
                "evidence_status": "unavailable",
                "blocking_issues": [issue],
                "recommended_followups": [],
            }
        )
        frame.meta = new_meta
        _write_meta_json(
            artifact_dir / "meta.json",
            new_meta.model_dump(mode="json"),
        )
        return frame

    # 4. Extract findings
    findings = _extract_findings(
        df=df,
        artifact_id=artifact_id,
        session_id=session_id,
        subject=subject,
        extractor_family=extractor_family,
        frame=frame,
        committed_at=now,
    )

    # 5. Open transaction: insert artifact + findings
    # Then SAVEPOINT for seeding + assessment + followups
    evidence_status = "complete"
    blocking_issues: list[BlockingIssue] = []
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
            evidence_status="complete",
            frame_path=str(parquet_path),
            frame_sha=frame_sha,
            committed_at=now,
            triggered_by_followup=triggered_by_followup,
        )

        # Insert findings
        _insert_findings(tx, findings, session_id=session_id)

        # SAVEPOINT: seeding + assessment + followups
        try:
            with tx.savepoint("evidence_phase2"):
                # Seed propositions (compare step only)
                if step_type == "compare" and findings:
                    for finding in findings:
                        if finding.finding_type == "delta":
                            prop = seed_change_proposition(
                                finding=finding,
                                comparison_window=comparison_window or {},
                                comparison_basis=comparison_basis or "left_vs_right",
                            )
                            if prop is not None:
                                _insert_proposition(tx, prop)
                                # Recompute assessment
                                assessment, edges = recompute_change_assessment(
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
                    blocking_issues=blocking_issues,
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

    # 7. Update frame.meta with Surface 1 fields
    new_meta = frame.meta.model_copy(
        update={
            "ref": artifact_id,
            "artifact_id": artifact_id,
            "evidence_status": evidence_status,
            "blocking_issues": blocking_issues,
            "recommended_followups": followups,
        }
    )
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
