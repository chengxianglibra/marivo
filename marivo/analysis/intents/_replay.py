"""Replay helpers for deterministic analysis intent materialization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from marivo.analysis.errors import AttributionMaterializationError, JobNotFoundError
from marivo.analysis.frames.delta import DeltaFrame
from marivo.analysis.frames.metric import MetricFrame
from marivo.analysis.policies import AlignmentPolicy
from marivo.analysis.semantic_inputs import DimensionInput
from marivo.analysis.session.core import Session
from marivo.analysis.windows.spec import TimeScopeInput
from marivo.refs import SemanticRef
from marivo.semantic.catalog import SemanticKind
from marivo.semantic.refs import make_ref

_ALIGNMENT_POLICY_FIELDS = {
    "kind",
    "calendar",
    "period",
    "fallback",
    "mode",
    "strict_lengths",
}


@dataclass(frozen=True)
class ObserveReplay:
    metric: str
    time_scope: TimeScopeInput
    grain: str | None
    dimensions: tuple[str, ...]
    slice_by: dict[str, Any]
    time_dimension: str | None

    def with_dimensions(self, axis_ids: list[str]) -> ObserveReplay:
        dimensions = list(self.dimensions)
        for axis_id in axis_ids:
            if axis_id not in dimensions and axis_id != self.time_dimension:
                dimensions.append(axis_id)
        return ObserveReplay(
            metric=self.metric,
            time_scope=self.time_scope,
            grain=self.grain,
            dimensions=tuple(dimensions),
            slice_by=dict(self.slice_by),
            time_dimension=self.time_dimension,
        )

    def call_observe(self, session: Session) -> MetricFrame:
        """Invoke ``observe`` with this replay's recovered parameters."""
        from marivo.analysis.intents.observe import observe

        dimensions: list[DimensionInput] = [
            _dimension_ref(session, dimension_id) for dimension_id in self.dimensions
        ]
        time_dimension: DimensionInput | None = (
            _time_dimension_ref(session, self.time_dimension)
            if self.time_dimension is not None
            else None
        )
        slice_by: dict[DimensionInput, Any] = {
            _dimension_ref(session, dimension_id): value
            for dimension_id, value in self.slice_by.items()
        }
        return observe(
            make_ref(self.metric, SemanticKind.METRIC),
            time_scope=self.time_scope,
            grain=self.grain,
            dimensions=dimensions or None,
            slice_by=slice_by or None,
            time_dimension=time_dimension,
            session=session,
        )


def recover_observe_replay(frame: MetricFrame, *, session: Session) -> ObserveReplay:
    params = _observe_params_from_lineage(frame)
    if not params:
        params = _observe_params_from_job(frame, session=session)
    if not params:
        raise AttributionMaterializationError(
            message="MetricFrame does not carry recoverable observe params",
            context={
                "recoverability_status": "observe_params_missing",
                "source_ref": frame.ref,
                "source_job_ref": frame.meta.produced_by_job,
            },
        )

    metric = params.get("metric")
    if not isinstance(metric, str) or not metric:
        raise AttributionMaterializationError(
            message="MetricFrame observe replay is missing metric",
            context={
                "recoverability_status": "observe_params_missing",
                "source_ref": frame.ref,
                "missing_param": "metric",
            },
        )

    timescope = params.get("timescope")
    original_timescope: TimeScopeInput = None
    resolved_timescope: dict[str, Any] = {}
    if isinstance(timescope, dict):
        original = timescope.get("original")
        if isinstance(original, dict):
            original_timescope = original
        resolved = timescope.get("resolved")
        if isinstance(resolved, dict):
            resolved_timescope = resolved

    dimensions = params.get("dimensions")
    dimension_ids = (
        tuple(
            sid for sid in (_extract_dimension_id(item) for item in dimensions) if sid is not None
        )
        if isinstance(dimensions, list)
        else ()
    )
    where = params.get("where")
    slice_by = dict(cast("dict[str, Any]", where)) if isinstance(where, dict) else {}
    grain = resolved_timescope.get("grain")
    time_dimension = resolved_timescope.get("time_dimension")

    return ObserveReplay(
        metric=metric,
        time_scope=original_timescope,
        grain=str(grain) if isinstance(grain, str) and grain else None,
        dimensions=dimension_ids,
        slice_by=slice_by,
        time_dimension=str(time_dimension)
        if isinstance(time_dimension, str) and time_dimension
        else None,
    )


def recover_alignment_policy(delta: DeltaFrame) -> AlignmentPolicy:
    raw_alignment = delta.meta.alignment
    if not isinstance(raw_alignment, dict):
        raise AttributionMaterializationError(
            message="DeltaFrame alignment metadata is not recoverable",
            context={
                "recoverability_status": "alignment_policy_missing",
                "delta_ref": delta.ref,
            },
        )
    policy_payload = {
        key: value for key, value in raw_alignment.items() if key in _ALIGNMENT_POLICY_FIELDS
    }
    try:
        return AlignmentPolicy(**policy_payload)
    except Exception as exc:
        raise AttributionMaterializationError(
            message="DeltaFrame alignment policy is not replayable",
            context={
                "recoverability_status": "alignment_policy_invalid",
                "delta_ref": delta.ref,
                "alignment_keys": sorted(str(key) for key in raw_alignment),
            },
        ) from exc


def _observe_params_from_lineage(frame: MetricFrame) -> dict[str, Any]:
    for step in reversed(frame.lineage.steps):
        if step.intent == "observe" and step.params:
            return dict(step.params)
    return {}


def _observe_params_from_job(frame: MetricFrame, *, session: Session) -> dict[str, Any]:
    job_ref = frame.meta.produced_by_job
    if not job_ref:
        return {}
    try:
        record = session.job(job_ref)
    except JobNotFoundError:
        return {}
    params = record.get("params") if isinstance(record, dict) else None
    return dict(cast("dict[str, Any]", params)) if isinstance(params, dict) else {}


def _dimension_ref(session: Session, semantic_id: str) -> SemanticRef:
    try:
        return session.catalog.get(f"dimension.{semantic_id}").ref
    except Exception:
        return make_ref(semantic_id, SemanticKind.DIMENSION)


def _time_dimension_ref(session: Session, semantic_id: str) -> SemanticRef:
    try:
        return session.catalog.get(f"time_dimension.{semantic_id}").ref
    except Exception:
        return make_ref(semantic_id, SemanticKind.TIME_DIMENSION)


def _extract_dimension_id(item: object) -> str | None:
    """Extract a dimension semantic_id from a stored lineage item.

    ``observe()`` stores dimensions as ``[{"semantic_id": "..."}]`` via
    ``_dump_dimensions``.  Plain strings are accepted as a fallback for
    any legacy frames that predate the dict format.
    """
    if isinstance(item, dict):
        semantic_id = item.get("semantic_id")
        if isinstance(semantic_id, str) and semantic_id:
            return semantic_id
        return None
    if isinstance(item, str):
        return item
    return None
