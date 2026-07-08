"""frame.metric(id): project one measure out of a multi-metric MetricFrame."""

from __future__ import annotations

from datetime import UTC, datetime
from time import monotonic
from typing import cast

from marivo.analysis.errors import CrossSessionFrameError, MetricArityError
from marivo.analysis.evidence.pipeline import (
    CommitInputs,
    CommitParams,
    CommitSemanticAnchors,
    commit_result,
    compute_prospective_artifact_id,
    frame_exists_on_disk,
)
from marivo.analysis.evidence.types import Subject
from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.intents.observe import (
    _analysis_axis_for_kind,
    _gen_ref,
    _params_digest,
)
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.analysis.session._load import load_frame
from marivo.analysis.session._runtime import (
    persist_job_record,
    register_frame_artifact,
    require_current_session,
)


def project_metric(frame: MetricFrame, metric_id: str) -> MetricFrame:
    """Project one metric out of a multi-metric frame as an arity-1 MetricFrame.

    Args:
        frame: The source MetricFrame (arity >= 1).
        metric_id: Bare metric id (e.g. ``"sales.revenue"``) carried by the frame.

    Returns:
        An arity-1 MetricFrame with the shared axes and the projected metric's
        values in the canonical ``value`` column. On an arity-1 frame, returns
        ``self`` when the id matches. On a cache hit, returns the persisted
        artifact without re-computing.

    Raises:
        MetricArityError: When ``metric_id`` is not carried by the frame.
        CrossSessionFrameError: When the frame's owning session is not current.
    """
    entries = frame.measures_meta()
    by_id = {entry["metric_id"]: entry for entry in entries}
    if metric_id not in by_id:
        raise MetricArityError(
            message=f"frame carries no metric {metric_id!r}",
            hint=f"available metrics: {sorted(by_id)!r}",
            details={"metric": metric_id, "metrics": sorted(by_id)},
        )
    if frame.arity == 1:
        return frame

    session = require_current_session()
    if frame.meta.session_id != session.id:
        raise CrossSessionFrameError(
            message=(f"frame belongs to session {frame.meta.session_id!r}, not {session.id!r}"),
        )

    entry = by_id[metric_id]
    parent_artifact = frame.meta.artifact_id or frame.meta.ref
    params = {"metric": metric_id}
    anchors = {"metric_id": metric_id, "model": metric_id.split(".", 1)[0]}
    prospective_id = compute_prospective_artifact_id(
        step_type="select_metric",
        inputs=CommitInputs(input_refs=[parent_artifact]),
        params=CommitParams(values=params),
        semantic_anchors=CommitSemanticAnchors(values=anchors),
    )
    if frame_exists_on_disk(session._layout.frames_dir, prospective_id):
        return cast("MetricFrame", load_frame(prospective_id, session=session))

    axis_columns = [axis["column"] for axis in frame.meta.axes.values() if "column" in axis]
    df = frame.to_pandas()[[*axis_columns, entry["column"]]].rename(
        columns={entry["column"]: MetricFrame.VALUE_COLUMN}
    )

    started_at = datetime.now(UTC)
    started = monotonic()
    frame_ref = _gen_ref("frame")
    job_ref = _gen_ref("job")
    grain_token: str | None = None
    window = frame.meta.window
    if isinstance(window, dict):
        grain_token = window.get("grain")
    meta = MetricFrameMeta(
        kind="metric_frame",
        ref=frame_ref,
        session_id=session.id,
        project_root=frame.meta.project_root,
        produced_by_job=job_ref,
        analysis_purpose=frame.meta.analysis_purpose,
        created_at=started_at,
        row_count=len(df),
        byte_size=0,
        lineage=Lineage(
            steps=[
                *frame.meta.lineage.steps,
                LineageStep(
                    intent="select_metric",
                    job_ref=job_ref,
                    inputs=[parent_artifact],
                    params_digest=_params_digest(params),
                    analysis_purpose=frame.meta.analysis_purpose,
                    params=params,
                ),
            ]
        ),
        metric_id=metric_id,
        axes=frame.meta.axes,
        measure={"name": entry["name"]},
        window=frame.meta.window,
        where=frame.meta.where,
        semantic_kind=frame.meta.semantic_kind,
        semantic_model=anchors["model"],
        unit=entry["unit"],
        reaggregatable=bool(entry["reaggregatable"]),
        additivity=entry["additivity"],
        cumulative=frame.meta.cumulative,
    )
    projected = MetricFrame(_df=df, meta=meta)
    result = cast(
        "MetricFrame",
        commit_result(
            store=session._evidence_store(),
            frames_dir=session._layout.frames_dir,
            frame=projected,
            step_type="select_metric",
            inputs=CommitInputs(input_refs=[parent_artifact]),
            params=CommitParams(values=params),
            semantic_anchors=CommitSemanticAnchors(values=anchors),
            subject=Subject(
                metric=metric_id,
                slice=frame.meta.where or {},
                grain=grain_token,
                analysis_axis=_analysis_axis_for_kind(frame.meta.semantic_kind),
            ),
            extractor_family="projection",
        ),
    )
    register_frame_artifact(session, result)
    finished_at = datetime.now(UTC)
    persist_job_record(
        session,
        {
            "id": job_ref,
            "session_id": session.id,
            "intent": "select_metric",
            "analysis_purpose": frame.meta.analysis_purpose,
            "params": params,
            "input_frame_refs": [parent_artifact],
            "output_frame_ref": result.meta.artifact_id or result.ref,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_ms": int((monotonic() - started) * 1000),
            "status": "succeeded",
            "error": None,
            "semantic_project_root": str(session.catalog.semantic_root),
            "semantic_model": anchors["model"],
            "queries": [],
        },
    )
    return result
