"""Replay helpers for deterministic analysis intent materialization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from marivo.analysis._semantic_types import AnalysisDimensionRef
from marivo.analysis.errors import AttributionMaterializationError, JobNotFoundError
from marivo.analysis.frames.delta import DeltaFrame
from marivo.analysis.frames.metric import MetricFrame
from marivo.analysis.policies import AlignmentPolicy
from marivo.analysis.runtime_metric import MetricExprInput, from_replay_payload
from marivo.analysis.session.core import Session
from marivo.analysis.windows.spec import TimeScopeInput
from marivo.semantic.refs import DimensionRef, TimeDimensionRef

_ALIGNMENT_POLICY_FIELDS = {
    "kind",
    "calendar",
    "period",
    "fallback",
    "mode",
    "strict_lengths",
}

type ReplayMetricInput = MetricExprInput | tuple[MetricExprInput, ...]


@dataclass(frozen=True)
class ObserveReplay:
    metric: ReplayMetricInput
    time_scope: TimeScopeInput
    grain: str | None
    dimensions: tuple[str, ...]
    slice_by: dict[str, Any]
    time_dimension: str | None
    dependency_fingerprint: str | None = None

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
            dependency_fingerprint=self.dependency_fingerprint,
        )

    def call_observe(self, session: Session) -> MetricFrame:
        """Invoke ``observe`` with this replay's recovered parameters."""
        from marivo.analysis.intents.observe import observe

        dimensions: list[AnalysisDimensionRef] = [
            _dimension_ref(session, dimension_id) for dimension_id in self.dimensions
        ]
        time_dimension: TimeDimensionRef | None = (
            _time_dimension_ref(session, self.time_dimension)
            if self.time_dimension is not None
            else None
        )
        slice_by: dict[AnalysisDimensionRef, Any] = {
            _dimension_ref(session, dimension_id): value
            for dimension_id, value in self.slice_by.items()
        }
        result = observe(
            self.metric,
            time_scope=self.time_scope,
            grain=self.grain,
            dimensions=dimensions or None,
            slice_by=slice_by or None,
            time_dimension=time_dimension,
            session=session,
        )
        stats = result.meta.execution_stats
        if stats is not None:
            result.meta = result.meta.model_copy(
                update={"execution_stats": stats.model_copy(update={"replay_used": True})}
            )
            from marivo.telemetry import _add_operation_attributes

            _add_operation_attributes(
                {
                    "marivo.analysis.metric_graph.replay_used": True,
                    "marivo.analysis.metric_graph.cache_hit": stats.cache_hit,
                    "marivo.analysis.metric_graph.artifact_deduplicated": (
                        stats.artifact_deduplicated
                    ),
                    "marivo.analysis.metric_graph.cse_used": (stats.cse_reused_occurrences > 0),
                }
            )
        actual_digest = result.meta.semantic_dependency_digest
        if self.dependency_fingerprint is not None and (
            actual_digest is None or actual_digest.fingerprint != self.dependency_fingerprint
        ):
            raise AttributionMaterializationError(
                message="Metric expression dependencies changed since the source observation",
                context={
                    "recoverability_status": "semantic_dependency_changed",
                    "expected_dependency_fingerprint": self.dependency_fingerprint,
                    "actual_dependency_fingerprint": (
                        actual_digest.fingerprint if actual_digest is not None else None
                    ),
                },
            )
        return result


def recover_observe_replay(frame: MetricFrame, *, session: Session) -> ObserveReplay:
    observe_index = next(
        (
            index
            for index in range(len(frame.lineage.steps) - 1, -1, -1)
            if frame.lineage.steps[index].intent == "observe"
        ),
        None,
    )
    if observe_index is not None:
        later_intents = tuple(step.intent for step in frame.lineage.steps[observe_index + 1 :])
        if later_intents and later_intents != ("select_metric",):
            raise AttributionMaterializationError(
                message="MetricFrame replay cannot discard post-observe transformations",
                context={
                    "recoverability_status": "transformed_replay_state_unavailable",
                    "source_ref": frame.ref,
                    "post_observe_intents": later_intents,
                },
            )
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

    replay_params = params
    if observe_index is not None and len(frame.lineage.steps) > observe_index + 1:
        select_step = frame.lineage.steps[observe_index + 1]
        if select_step.intent == "select_metric" and isinstance(select_step.params, dict):
            replay_params = select_step.params
    replay_expression = replay_params.get("replay_expression")
    replay_expressions = replay_params.get("replay_expressions")
    try:
        has_replay_expression = replay_expression is not None
        has_replay_expressions = isinstance(replay_expressions, list) and bool(replay_expressions)
        if has_replay_expression == has_replay_expressions:
            raise ValueError("observe replay requires exactly one typed replay payload shape")
        if has_replay_expressions:
            assert isinstance(replay_expressions, list)
            metric: ReplayMetricInput | None = tuple(
                from_replay_payload(item) for item in replay_expressions
            )
        elif has_replay_expression:
            metric = from_replay_payload(replay_expression)
        else:
            metric = None
    except (TypeError, ValueError) as exc:
        raise AttributionMaterializationError(
            message="MetricFrame observe replay expression is invalid",
            context={
                "recoverability_status": "observe_expression_invalid",
                "source_ref": frame.ref,
            },
        ) from exc
    if metric is None:
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
    if dimensions is None:
        dimension_ids: tuple[str, ...] = ()
    elif isinstance(dimensions, list):
        extracted_dimensions = tuple(_extract_dimension_id(item) for item in dimensions)
        if any(item is None for item in extracted_dimensions):
            raise AttributionMaterializationError(
                message="MetricFrame observe replay dimensions are invalid",
                context={
                    "recoverability_status": "observe_dimensions_invalid",
                    "source_ref": frame.ref,
                },
            )
        dimension_ids = tuple(cast("str", item) for item in extracted_dimensions)
    else:
        raise AttributionMaterializationError(
            message="MetricFrame observe replay dimensions are invalid",
            context={
                "recoverability_status": "observe_dimensions_invalid",
                "source_ref": frame.ref,
            },
        )
    where = params.get("where")
    slice_by = dict(cast("dict[str, Any]", where)) if isinstance(where, dict) else {}
    grain = resolved_timescope.get("grain")
    time_dimension = resolved_timescope.get("time_dimension")
    dependency_digest = params.get("semantic_dependency_digest")
    dependency_fingerprint = (
        dependency_digest.get("fingerprint")
        if isinstance(dependency_digest, dict)
        and isinstance(dependency_digest.get("fingerprint"), str)
        else None
    )

    return ObserveReplay(
        metric=metric,
        time_scope=original_timescope,
        grain=str(grain) if isinstance(grain, str) and grain else None,
        dimensions=dimension_ids,
        slice_by=slice_by,
        time_dimension=str(time_dimension)
        if isinstance(time_dimension, str) and time_dimension
        else None,
        dependency_fingerprint=dependency_fingerprint,
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


def _dimension_ref(session: Session, semantic_id: str) -> AnalysisDimensionRef:
    dimension = session.catalog._require_index().registry.dimensions.get(semantic_id)
    if dimension is not None and dimension.is_time_dimension:
        return TimeDimensionRef(semantic_id)
    return DimensionRef(semantic_id)


def _time_dimension_ref(session: Session, semantic_id: str) -> TimeDimensionRef:
    del session
    return TimeDimensionRef(semantic_id)


def _extract_dimension_id(item: object) -> str | None:
    """Extract a dimension semantic_id from a current typed lineage item."""
    if isinstance(item, dict):
        semantic_id = item.get("semantic_id")
        if set(item) == {"semantic_id"} and isinstance(semantic_id, str) and semantic_id:
            return semantic_id
        return None
    return None
