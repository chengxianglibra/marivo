"""Shared helpers for analysis derived intents."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
import hashlib
import json
import secrets
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from time import monotonic
from typing import Any, cast

import pandas as pd
from pandas.api.types import is_numeric_dtype

from marivo.analysis._semantic_persistence import job_semantics_from_frames
from marivo.analysis.errors import CrossSessionFrameError, SemanticKindMismatchError
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
    AttributionReconciliation,
)
from marivo.analysis.frames.base import BaseFrame
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.analysis.session._runtime import (
    persist_job_record,
    register_frame_artifact,
    require_current_session,
)
from marivo.analysis.session.core import Session


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


def require_numeric_column(df: pd.DataFrame, value: str | None, *, purpose: str) -> str:
    if value is not None:
        if value not in df.columns:
            raise SemanticKindMismatchError(
                message=f"{purpose} value column {value!r} does not exist",
                context={"columns": list(df.columns)},
            )
        if not is_numeric_dtype(df[value]):
            raise SemanticKindMismatchError(
                message=f"{purpose} value column {value!r} is not numeric",
                context={"column": value, "dtype": str(df[value].dtype)},
            )
        return value

    numeric = [column for column in df.columns if is_numeric_dtype(df[column])]
    if len(numeric) != 1:
        raise SemanticKindMismatchError(
            message=f"{purpose} requires exactly one numeric column when value is omitted",
            context={"numeric_columns": numeric},
        )
    return str(numeric[0])


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
) -> AttributionFrame:
    session._connection_runtime.begin_query_capture()
    if reconciliation is not None:
        params = {
            **params,
            "reconciliation": reconciliation.model_dump(mode="json"),
        }
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
    )
    frame = AttributionFrame(_df=df.copy(), meta=meta)
    source_ref_values = [source.meta.artifact_id or source.ref for source in sources]
    frame = cast(
        "AttributionFrame",
        commit_result(
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
            "error": None,
            "semantic_project_root": str(session.catalog._project.semantic_root),
            "queries": [qe.to_dict() for qe in _captured_queries],
        },
    )
    return frame
