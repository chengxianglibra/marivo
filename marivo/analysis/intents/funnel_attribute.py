"""Loss-rate attribution orchestration over persisted funnel journey membership."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from time import monotonic
from typing import Any, Literal, cast

import numpy as np
import pandas as pd

from marivo._compat import UTC
from marivo.analysis._semantic_persistence import job_semantics_from_frames
from marivo.analysis.errors import (
    AnalysisRepair,
    FrameRefNotFound,
    FunnelAttributionUnsupportedError,
    InvalidSubjectAxisError,
    PatternStepMismatchError,
    SemanticKindMismatchError,
    SessionLockedByAnotherProcessError,
)
from marivo.analysis.evidence.pipeline import (
    CommitInputs,
    CommitParams,
    CommitSemanticAnchors,
    commit_result,
    compute_prospective_artifact_id,
    frame_exists_on_disk,
    rollback_committed_result,
)
from marivo.analysis.evidence.types import EventSubject
from marivo.analysis.frames._attribution_columns import (
    ATTRIBUTION_AXIS_COLUMN,
    ATTRIBUTION_DRIVER_COLUMN,
    ATTRIBUTION_LEVEL_COLUMN,
    ATTRIBUTION_PATH_COLUMN,
)
from marivo.analysis.frames.attribution import (
    FUNNEL_ATTRIBUTION_COLUMNS,
    AttributionFrame,
    FunnelAttributionFrameMeta,
    FunnelAttributionReconciliation,
)
from marivo.analysis.frames.delta import DeltaFrame, FunnelDeltaFrameMeta
from marivo.analysis.frames.event import EventFrame, EventFrameMeta
from marivo.analysis.funnel import FunnelLossRate
from marivo.analysis.intents._attribution_mode import (
    AttributionMode,
    validate_attribution_mode,
)
from marivo.analysis.intents._derived import gen_ref, params_digest
from marivo.analysis.intents._event_funnel import reduce_event_funnel
from marivo.analysis.intents._event_subject_axes import (
    materialize_subject_axes,
    resolve_subject_axes,
)
from marivo.analysis.intents._funnel_attribution import (
    LossRateDecomposition,
    decompose_loss_rate,
)
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.analysis.session._load import load_frame
from marivo.analysis.session._runtime import (
    persist_job_record,
    persist_reused_artifact_job,
    register_frame_artifact,
)
from marivo.analysis.session.core import Session, ensure_session_can_execute
from marivo.introspection.live.model import LiveHelpTarget
from marivo.refs import ref as ref_factory

_RESERVED_FUNNEL_ATTRIBUTION_COLUMNS = frozenset(
    {
        *FUNNEL_ATTRIBUTION_COLUMNS,
        ATTRIBUTION_LEVEL_COLUMN,
        ATTRIBUTION_AXIS_COLUMN,
        ATTRIBUTION_DRIVER_COLUMN,
        ATTRIBUTION_PATH_COLUMN,
    }
)


def _repair(
    *,
    action: str,
    kind: str = "user_choice",
    candidates: tuple[str, ...] = (),
) -> AnalysisRepair:
    return AnalysisRepair(
        kind=cast("Any", kind),
        action=action,
        help_target=LiveHelpTarget(surface="analysis", canonical_id="attribute"),
        candidates=candidates,
    )


def _attribute_axis_error(error: InvalidSubjectAxisError) -> InvalidSubjectAxisError:
    """Retarget the shared subject-axis repair to the attribute entrypoint."""
    repair = error.repair
    return InvalidSubjectAxisError(
        message=error.message,
        expected=error.expected,
        received=error.received,
        location=(error.location or "session.attribute(axes)").replace(
            "session.events.funnel",
            "session.attribute",
        ),
        repair=_repair(
            kind=repair.kind if repair is not None else "user_choice",
            action=(
                repair.action
                if repair is not None
                else "Choose a governed fanout-safe subject Dimension."
            ),
            candidates=repair.candidates if repair is not None else (),
        ),
    )


def _require_ungrouped_delta(delta: DeltaFrame) -> FunnelDeltaFrameMeta:
    if delta.meta.semantic_kind != "funnel":
        raise FunnelAttributionUnsupportedError(
            message="funnel attribution requires a DeltaFrame[funnel]",
            expected="an ungrouped DeltaFrame[funnel]",
            received=f"semantic_kind={delta.meta.semantic_kind!r}",
            location="session.attribute(frame)",
            repair=_repair(action="Compare two ungrouped EventFrame[funnel] artifacts first."),
        )
    meta = delta.meta
    if meta.axes:
        raise FunnelAttributionUnsupportedError(
            message="funnel attribution does not accept an already grouped delta",
            expected="an ungrouped DeltaFrame[funnel]",
            received=f"axes={tuple(axis.output_column for axis in meta.axes)!r}",
            location="session.attribute(frame)",
            repair=_repair(
                action=(
                    "Compare the corresponding ungrouped funnels first, then pass "
                    "the desired driver axes to session.attribute(...)."
                )
            ),
        )
    return meta


def _require_target(
    meta: FunnelDeltaFrameMeta,
    target: FunnelLossRate | None,
) -> tuple[FunnelLossRate, str]:
    if type(target) is not FunnelLossRate:
        raise FunnelAttributionUnsupportedError(
            message="funnel attribution requires one exact loss-rate target",
            expected="target=mv.funnel_loss_rate(step=<PatternStep>)",
            received=type(target).__name__,
            location="session.attribute(target)",
            repair=_repair(action="Choose one retained non-initial PatternStep."),
        )
    fingerprints = tuple(step.fingerprint for step in meta.pattern.steps)
    if fingerprints.count(target.step.fingerprint) != 1:
        raise PatternStepMismatchError(
            message="funnel attribution target is not retained by the compared pattern",
            expected="one exact PatternStep from delta.meta.pattern",
            received=target.step.key,
            location="session.attribute(target)",
            repair=_repair(action="Choose the exact retained PatternStep from the source pattern."),
        )
    index = fingerprints.index(target.step.fingerprint)
    if index == 0:
        raise FunnelAttributionUnsupportedError(
            message="the initial funnel step has no prior-step loss rate",
            expected="a target PatternStep with an immediately preceding PatternStep",
            received=target.step.key,
            location="session.attribute(target)",
            repair=_repair(action="Choose a non-initial retained PatternStep."),
        )
    return target, meta.pattern.steps[index - 1].key


def _validate_mode(
    axis_ids: list[str], mode: AttributionMode | None
) -> Literal["joint", "hierarchy"] | None:
    try:
        return validate_attribution_mode(axis_ids, mode, intent="session.attribute")
    except SemanticKindMismatchError as exc:
        raise FunnelAttributionUnsupportedError(
            message=str(exc),
            expected="mode='joint' or mode='hierarchy' for multiple axes",
            received=f"mode={mode!r}",
            location="session.attribute(mode)",
            repair=_repair(action="Choose mode='joint' or mode='hierarchy'."),
        ) from exc


def _target_components(
    *,
    session: Session,
    journey: EventFrame,
    target_step_key: str,
    resolved_axes: tuple[Any, ...],
) -> tuple[pd.DataFrame, tuple[Any, ...], tuple[dict[str, object], ...]]:
    meta = cast("EventFrameMeta", journey.meta)
    rows = journey._dataframe_copy()
    subject_entity = ref_factory.entity(meta.subject_entity_ref.path)
    materialized = materialize_subject_axes(
        session,
        journey_rows=rows,
        first_step_key=meta.pattern.steps[0].key,
        subject_entity=subject_entity,
        subject_identity=meta.subject_identity,
        axes=resolved_axes,
    )
    axis_columns = tuple(binding.output_column for binding in materialized.bindings)
    reduction = reduce_event_funnel(
        rows,
        pattern=meta.pattern,
        event_coverage_complete={
            item.event_ref.path: item.basis != "unknown" for item in meta.input_coverage
        },
        axis_values=materialized.values,
        axis_columns=axis_columns,
    )
    selected = reduction.rows.loc[
        reduction.rows["step_key"].astype(str) == target_step_key,
        [*axis_columns, "lost_count", "resolved_entry_count"],
    ].copy()
    return selected, materialized.bindings, materialized.lineage


def _combine_components(
    current: pd.DataFrame,
    baseline: pd.DataFrame,
    *,
    axis_columns: tuple[str, ...],
) -> pd.DataFrame:
    merged = current.merge(
        baseline,
        how="outer",
        on=list(axis_columns),
        suffixes=("__current", "__baseline"),
    )
    output = merged[list(axis_columns)].copy()
    for source, target in (
        ("lost_count__current", "current_lost_count"),
        ("resolved_entry_count__current", "current_resolved_entry_count"),
        ("lost_count__baseline", "baseline_lost_count"),
        ("resolved_entry_count__baseline", "baseline_resolved_entry_count"),
    ):
        output[target] = merged[source].fillna(0).astype("int64")
    return output


def _with_shares(rows: pd.DataFrame, *, total_delta: float) -> pd.DataFrame:
    output = rows.copy()
    values = output["contribution"].to_numpy(dtype="float64")
    positive = float(values[values > 0].sum())
    negative = float(values[values < 0].sum())
    total_shares = np.full(values.shape, np.nan, dtype="float64")
    if total_delta != 0:
        total_shares[:] = values / total_delta
    positive_shares = np.full(values.shape, np.nan, dtype="float64")
    if positive != 0:
        positive_mask = values > 0
        positive_shares[positive_mask] = values[positive_mask] / positive
    negative_shares = np.full(values.shape, np.nan, dtype="float64")
    if negative != 0:
        negative_mask = values < 0
        negative_shares[negative_mask] = values[negative_mask] / negative
    output["share_of_total_delta"] = total_shares
    output["share_of_positive_pool"] = positive_shares
    output["share_of_negative_pool"] = negative_shares
    return output


def _hierarchy_layout(
    decomposition: LossRateDecomposition,
    *,
    axis_columns: tuple[str, ...],
) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    for level in range(1, len(axis_columns) + 1):
        prefix = axis_columns[:level]
        grouped = (
            decomposition.rows.groupby(
                [*prefix, "contribution_kind"],
                dropna=False,
            )["contribution"]
            .sum()
            .reset_index()
        )
        grouped = _with_shares(grouped, total_delta=decomposition.total_delta)
        grouped.insert(0, ATTRIBUTION_LEVEL_COLUMN, level)
        grouped.insert(1, ATTRIBUTION_AXIS_COLUMN, axis_columns[level - 1])
        grouped.insert(2, ATTRIBUTION_DRIVER_COLUMN, grouped[axis_columns[level - 1]])
        grouped.insert(
            3,
            ATTRIBUTION_PATH_COLUMN,
            grouped.apply(
                lambda row, columns=prefix: " > ".join(str(row[column]) for column in columns),
                axis=1,
            ),
        )
        rows.append(
            grouped[
                [
                    ATTRIBUTION_LEVEL_COLUMN,
                    ATTRIBUTION_AXIS_COLUMN,
                    ATTRIBUTION_DRIVER_COLUMN,
                    ATTRIBUTION_PATH_COLUMN,
                    *FUNNEL_ATTRIBUTION_COLUMNS,
                ]
            ]
        )
    return pd.concat(rows, ignore_index=True)


def _rollback(
    *,
    session: Session,
    evidence_store: Any,
    artifact_id: str,
    job_ref: str,
    preserve_artifact: bool,
) -> None:
    cleanup: list[Callable[[], object]] = [
        lambda: session._store.delete_job(session.id, job_ref),
        lambda: (session._layout.jobs_dir / f"{job_ref}.json").unlink(missing_ok=True),
    ]
    if not preserve_artifact:
        cleanup.extend(
            [
                lambda: session._store.delete_artifact(session.id, artifact_id),
                lambda: rollback_committed_result(
                    store=evidence_store,
                    frames_dir=session._layout.frames_dir,
                    artifact_id=artifact_id,
                ),
            ]
        )
    for action in cleanup:
        try:
            action()
        except BaseException:
            continue


def validate_funnel_attribute_admission(delta: DeltaFrame, *, session: Session) -> None:
    """Validate funnel shape and same-Session ownership before Run admission."""
    ensure_session_can_execute(session)
    meta = _require_ungrouped_delta(delta)
    if meta.session_id != session.id:
        raise FunnelAttributionUnsupportedError(
            message="funnel delta belongs to a different session",
            expected="a same-session DeltaFrame[funnel]",
            received=f"session_id={meta.session_id!r}",
            location="session.attribute(frame)",
            repair=_repair(action="Load or rebuild the delta in this session."),
        )


def attribute_funnel(
    delta: DeltaFrame,
    *,
    axes: Sequence[object],
    mode: AttributionMode | None,
    target: FunnelLossRate | None,
    analysis_purpose: str | None,
    session: Session,
) -> AttributionFrame:
    """Attribute one persisted funnel loss-rate delta over governed subject axes."""
    ensure_session_can_execute(session)
    started_at = datetime.now(UTC)
    started = monotonic()
    meta = _require_ungrouped_delta(delta)
    if meta.session_id != session.id:
        raise FunnelAttributionUnsupportedError(
            message="funnel delta belongs to a different session",
            expected="a same-session DeltaFrame[funnel]",
            received=f"session_id={meta.session_id!r}",
            location="session.attribute(frame)",
            repair=_repair(action="Load or rebuild the delta in this session."),
        )
    resolved_target, preceding_step_key = _require_target(meta, target)
    validated_mode = _validate_mode(
        [getattr(getattr(axis, "ref", axis), "key", repr(axis)) for axis in axes],
        mode,
    )
    try:
        resolved_axes = resolve_subject_axes(
            session,
            subject_entity=ref_factory.entity(meta.subject_entity_ref.path),
            axes=axes,
            _reserved_columns=_RESERVED_FUNNEL_ATTRIBUTION_COLUMNS,
        )
    except InvalidSubjectAxisError as exc:
        raise _attribute_axis_error(exc) from exc
    if not resolved_axes:
        raise FunnelAttributionUnsupportedError(
            message="funnel attribution requires at least one driver axis",
            expected="one or more governed subject Dimensions",
            received="axes=[]",
            location="session.attribute(axes)",
            repair=_repair(action="Choose at least one current-catalog Dimension."),
        )
    source_journeys: list[EventFrame | object] = []
    for label, source_ref in (
        ("current", meta.source_current_journey_ref),
        ("baseline", meta.source_baseline_journey_ref),
    ):
        try:
            source_journeys.append(session.get_frame(source_ref))
        except FrameRefNotFound as exc:
            raise FunnelAttributionUnsupportedError(
                message=f"persisted {label} source journey membership is unavailable",
                expected="two retained EventFrame[journey] source artifacts",
                received=f"{label}_source_journey_ref={source_ref!r} was not found",
                location="session.attribute(frame)",
                repair=_repair(
                    kind="inspect",
                    action="Rebuild the source funnels and compare them again.",
                ),
            ) from exc
    current_journey, baseline_journey = source_journeys
    if (
        type(current_journey) is not EventFrame
        or current_journey.meta.semantic_kind != "journey"
        or type(baseline_journey) is not EventFrame
        or baseline_journey.meta.semantic_kind != "journey"
    ):
        raise FunnelAttributionUnsupportedError(
            message="persisted source journey membership is unavailable",
            expected="two retained EventFrame[journey] source artifacts",
            received="source journey shape mismatch",
            location="session.attribute(frame)",
            repair=_repair(kind="inspect", action="Rebuild the source funnels and compare again."),
        )
    try:
        current, bindings, current_lineage = _target_components(
            session=session,
            journey=current_journey,
            target_step_key=resolved_target.step.key,
            resolved_axes=resolved_axes,
        )
        baseline, baseline_bindings, baseline_lineage = _target_components(
            session=session,
            journey=baseline_journey,
            target_step_key=resolved_target.step.key,
            resolved_axes=resolved_axes,
        )
    except InvalidSubjectAxisError as exc:
        raise _attribute_axis_error(exc) from exc
    if bindings != baseline_bindings:
        raise FunnelAttributionUnsupportedError(
            message="driver-axis materialization differed between source journeys",
            expected="identical subject-axis bindings on both sides",
            received="axis bindings differ",
            location="session.attribute(axes)",
            repair=_repair(kind="inspect", action="Reload the catalog and rebuild the comparison."),
        )
    axis_columns = tuple(binding.output_column for binding in bindings)
    components = _combine_components(current, baseline, axis_columns=axis_columns)
    try:
        decomposition = decompose_loss_rate(
            components=components,
            axis_columns=axis_columns,
        )
    except ValueError as exc:
        raise FunnelAttributionUnsupportedError(
            message="funnel loss-rate attribution could not reconcile",
            expected="positive resolved-entry denominators and exact additive components",
            received=str(exc),
            location="session.attribute(frame)",
            repair=_repair(kind="inspect", action="Inspect the target step populations."),
        ) from exc
    output = (
        _hierarchy_layout(decomposition, axis_columns=axis_columns)
        if validated_mode == "hierarchy"
        else decomposition.rows[[*axis_columns, *FUNNEL_ATTRIBUTION_COLUMNS]]
    )
    finished_at = datetime.now(UTC)
    job_ref = gen_ref("job")
    params: dict[str, Any] = {
        "source_delta_ref": meta.artifact_id or meta.ref,
        "target": resolved_target.model_dump(mode="json"),
        "preceding_step_key": preceding_step_key,
        "axes": [binding.model_dump(mode="json") for binding in bindings],
        "mode": validated_mode,
    }
    inputs = CommitInputs(input_refs=[meta.artifact_id or meta.ref])
    commit_params = CommitParams(values=params)
    anchors = CommitSemanticAnchors(
        catalog_definition_fingerprint=meta.catalog_definition_fingerprint,
    )
    prospective_id = compute_prospective_artifact_id(
        step_type="attribute.funnel_loss_rate",
        inputs=inputs,
        params=commit_params,
        semantic_anchors=anchors,
    )
    if frame_exists_on_disk(session._layout.frames_dir, prospective_id):
        reused_attribution = cast("AttributionFrame", load_frame(prospective_id, session=session))
        persist_reused_artifact_job(
            session,
            intent="attribute.funnel_loss_rate",
            analysis_purpose=analysis_purpose,
            params={**params, "axis_lineage": [*current_lineage, *baseline_lineage]},
            input_frame_refs=list(inputs.input_refs),
            output_frame_ref=reused_attribution.meta.artifact_id or reused_attribution.ref,
            semantics=job_semantics_from_frames(reused_attribution),
            started_at=started_at,
            started_monotonic=started,
            semantic_project_root=str(session.catalog.semantic_root),
        )
        return reused_attribution
    if not meta.content_hash:
        raise FunnelAttributionUnsupportedError(
            message="funnel delta must be persisted before attribution",
            expected="DeltaFrame[funnel] with content_hash",
            received="content_hash=None",
            location="session.attribute(frame)",
            repair=_repair(action="Compare the source funnels again in this session."),
        )
    step = LineageStep(
        intent="attribute.funnel_loss_rate",
        job_ref=job_ref,
        inputs=list(inputs.input_refs),
        params_digest=params_digest(params),
        params={"axis_refs": [axis.ref.key for axis in resolved_axes]},
        analysis_purpose=analysis_purpose,
    )
    frame = AttributionFrame(
        _df=output,
        meta=FunnelAttributionFrameMeta(
            ref=gen_ref("frame"),
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job=job_ref,
            analysis_purpose=analysis_purpose,
            created_at=finished_at,
            row_count=len(output),
            byte_size=0,
            lineage=Lineage(
                steps=[*delta.lineage.steps, step],
                external_inputs=delta.lineage.external_inputs,
            ),
            catalog_definition_fingerprint=meta.catalog_definition_fingerprint,
            source_delta_ref=inputs.input_refs[0],
            source_delta_fingerprint=meta.content_hash,
            source_current_journey_ref=meta.source_current_journey_ref,
            source_baseline_journey_ref=meta.source_baseline_journey_ref,
            subject_entity_ref=meta.subject_entity_ref,
            subject_identity=meta.subject_identity,
            source_pattern_fingerprint=meta.pattern.fingerprint,
            matching=meta.matching,
            coverage_basis=(
                meta.current_coverage_basis
                if meta.current_coverage_basis == meta.baseline_coverage_basis
                else "mixed"
            ),
            target=resolved_target,
            preceding_step_key=preceding_step_key,
            axes=bindings,
            mode=validated_mode,
            reconciliation=FunnelAttributionReconciliation(
                target_loss_rate_delta=decomposition.total_delta,
                contribution_sum=decomposition.contribution_sum,
                positive_pool=decomposition.positive_pool,
                negative_pool=decomposition.negative_pool,
                residual=decomposition.residual,
                max_abs_residual=abs(decomposition.residual),
            ),
        ),
    )
    evidence_store = session._evidence_store()
    preexisting = session._store.get_artifact(session.id, prospective_id) is not None
    try:
        committed = cast(
            "AttributionFrame",
            commit_result(
                session=session,
                store=evidence_store,
                frames_dir=session._layout.frames_dir,
                frame=frame,
                step_type="attribute.funnel_loss_rate",
                inputs=inputs,
                params=commit_params,
                semantic_anchors=anchors,
                subject=EventSubject(
                    subject_entity_ref=meta.subject_entity_ref,
                    subject_identity_signature=meta.subject_identity,
                    analysis_axis="funnel_loss_rate",
                ),
                extractor_family="attribution_frame",
            ),
        )
        register_frame_artifact(session, committed)
        persist_job_record(
            session,
            {
                "id": job_ref,
                "session_id": session.id,
                "intent": "attribute.funnel_loss_rate",
                **job_semantics_from_frames(committed),
                "analysis_purpose": analysis_purpose,
                "params": {
                    **params,
                    "axis_lineage": [*current_lineage, *baseline_lineage],
                },
                "input_frame_refs": list(inputs.input_refs),
                "output_frame_ref": committed.meta.artifact_id or committed.ref,
                "started_at": started_at.isoformat(),
                "finished_at": finished_at.isoformat(),
                "duration_ms": int((monotonic() - started) * 1000),
                "status": "succeeded",
                "reused_artifact": False,
                "error": None,
                "semantic_project_root": str(session.catalog.semantic_root),
            },
        )
    except SessionLockedByAnotherProcessError:
        raise
    except BaseException:
        _rollback(
            session=session,
            evidence_store=evidence_store,
            artifact_id=prospective_id,
            job_ref=job_ref,
            preserve_artifact=preexisting,
        )
        raise
    return committed


__all__ = ["attribute_funnel"]
