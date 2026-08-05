"""Load persisted analysis frames."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from pydantic import ValidationError

from marivo.analysis._cumulative import (
    authored_comparable_period_anchor,
    cumulative_compare_anchor,
    cumulative_equivalent_comparison_semantics,
)
from marivo.analysis.attribution_contract import basis_fingerprint
from marivo.analysis.candidate_identity import (
    validate_candidate_frame_identity,
    validate_semantic_hypothesis_frame_integrity,
)
from marivo.analysis.errors import (
    AnalysisRepair,
    CrossSessionFrameError,
    FrameCacheCorruptedError,
    FrameMetaInvalidError,
    FrameRefNotFound,
)
from marivo.analysis.frames._content_hash import (
    compute_file_content_hash,
    compute_frame_content_hash,
)
from marivo.analysis.frames.association import AssociationResult, AssociationResultMeta
from marivo.analysis.frames.attribution import (
    AttributionFrame,
    AttributionFrameMeta,
    FunnelAttributionFrameMeta,
    validate_generic_attribution_rows,
)
from marivo.analysis.frames.base import CURRENT_ARTIFACT_SCHEMA_VERSION, BaseFrame
from marivo.analysis.frames.candidate import (
    CandidateSet,
    CandidateSetMeta,
    SemanticHypothesisCandidateSetMeta,
)
from marivo.analysis.frames.component import ComponentFrame, ComponentFrameMeta
from marivo.analysis.frames.coverage import CoverageFrame, CoverageFrameMeta
from marivo.analysis.frames.delta import DeltaFrame, DeltaFrameMeta, FunnelDeltaFrameMeta
from marivo.analysis.frames.event import (
    EventFrame,
    EventFrameMeta,
    EventFunnelFrameMeta,
    EventTimeToEventFrameMeta,
)
from marivo.analysis.frames.forecast import ForecastFrame, ForecastFrameMeta
from marivo.analysis.frames.hypothesis import HypothesisTestResult, HypothesisTestResultMeta
from marivo.analysis.frames.lifecycle import (
    LifecycleDistributionFrameMeta,
    LifecycleDwellFrameMeta,
    LifecycleFrame,
    LifecycleHistoryFrameMeta,
    LifecycleTransitionsFrameMeta,
    LifecycleViolationsFrameMeta,
)
from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.frames.quality import QualityReport, QualityReportMeta
from marivo.analysis.frames.subject import SubjectSet, SubjectSetMeta
from marivo.analysis.intents._candidate_columns import (
    CANDIDATE_COLUMNS,
    validate_shape_columns,
)
from marivo.analysis.policies import AlignmentPolicy
from marivo.analysis.refs import ArtifactRef
from marivo.introspection.live.model import LiveHelpTarget
from marivo.semantic.metric_graph import (
    DeltaComparisonSemantics,
    ExactComparisonSemanticsV1,
)
from marivo.semantic.metric_graph_canonical import (
    MetricGraphContractError,
    fingerprint,
    validate_graph,
)

if TYPE_CHECKING:
    from marivo.analysis.session.core import Session

_FRAME_CLASSES = {
    "metric_frame": (MetricFrame, MetricFrameMeta),
    "delta_frame": (DeltaFrame, DeltaFrameMeta),
    "attribution_frame": (AttributionFrame, AttributionFrameMeta),
    "candidate_set": (CandidateSet, CandidateSetMeta),
    "association_result": (AssociationResult, AssociationResultMeta),
    "hypothesis_test_result": (HypothesisTestResult, HypothesisTestResultMeta),
    "forecast_frame": (ForecastFrame, ForecastFrameMeta),
    "event_frame": (EventFrame, EventFrameMeta),
    "lifecycle_frame": (LifecycleFrame, LifecycleHistoryFrameMeta),
    "subject_set": (SubjectSet, SubjectSetMeta),
    "quality_report": (QualityReport, QualityReportMeta),
    "component_frame": (ComponentFrame, ComponentFrameMeta),
    "coverage_frame": (CoverageFrame, CoverageFrameMeta),
}

_CURRENT_METRIC_FRAME_FIELDS = frozenset(
    {
        "metric_identity",
        "metric_identities",
        "catalog_definition_fingerprint",
        "expression_graph_ref",
        "expression_graph",
        "expression_fingerprint",
        "semantic_dependency_digest",
        "presentation_ref",
        "presentation",
        "presentation_fingerprint",
        "artifact_identity",
        "key_schema",
        "source_compatibility_domain",
        "component_graph_ref",
        "replay_graph_ref",
        "comparable_value_semantics_ref",
        "comparable_value_semantics",
        "execution_stats",
        "unit_state",
        "axis_bindings",
        "slice_predicates",
        "status_time_dimension_ref",
    }
)


def _current_metric_state_error(
    ref: str,
    *,
    path: str,
    reason: str,
) -> FrameMetaInvalidError:
    return FrameMetaInvalidError(
        message=f"frame '{ref}' has corrupt current-schema metric state at {path}",
        context={
            "ref": ref,
            "artifact_schema_version": CURRENT_ARTIFACT_SCHEMA_VERSION,
            "path": path,
            "reason": reason,
        },
    )


def _validate_current_replay_payload(ref: str, meta: MetricFrameMeta) -> None:
    from marivo.analysis.runtime_metric import from_replay_payload

    observe_step = next(
        (step for step in reversed(meta.lineage.steps) if step.intent == "observe"),
        None,
    )
    if observe_step is None or not isinstance(observe_step.params, dict):
        raise _current_metric_state_error(
            ref,
            path="lineage.observe.params",
            reason="typed observe replay params are missing",
        )
    replay_step = (
        meta.lineage.steps[-1]
        if meta.lineage.steps and meta.lineage.steps[-1].intent == "select_metric"
        else observe_step
    )
    if not isinstance(replay_step.params, dict):
        raise _current_metric_state_error(
            ref,
            path=f"lineage.{replay_step.intent}.params",
            reason="typed replay params are missing",
        )
    replay_expression = replay_step.params.get("replay_expression")
    replay_expressions = replay_step.params.get("replay_expressions")
    try:
        if len(meta.metric_identities) == 1:
            if replay_expression is None or replay_expressions is not None:
                raise ValueError("arity-one frame requires exactly replay_expression")
            from_replay_payload(replay_expression)
        else:
            if replay_expression is not None or not isinstance(replay_expressions, list):
                raise ValueError("multi-root frame requires exactly replay_expressions")
            if len(replay_expressions) != len(meta.metric_identities):
                raise ValueError("replay expression count does not match metric root count")
            for item in replay_expressions:
                from_replay_payload(item)
    except (TypeError, ValueError) as exc:
        raise _current_metric_state_error(
            ref,
            path="lineage.observe.params.replay_expression",
            reason=str(exc),
        ) from exc


def _validate_current_metric_state(ref: str, meta: MetricFrameMeta) -> None:
    graph = meta.expression_graph
    assert graph is not None
    try:
        validate_graph(graph)
    except MetricGraphContractError as exc:
        raise _current_metric_state_error(
            ref,
            path="expression_graph",
            reason=str(exc),
        ) from exc

    if len(graph.roots) != len(meta.metric_identities):
        raise _current_metric_state_error(
            ref,
            path="expression_graph.roots",
            reason="root count does not match metric identity count",
        )
    expected_expression_fingerprint = (
        graph.roots[0] if len(graph.roots) == 1 else fingerprint(graph.roots)
    )
    if meta.expression_fingerprint != expected_expression_fingerprint:
        raise _current_metric_state_error(
            ref,
            path="expression_fingerprint",
            reason="fingerprint does not match the canonical graph roots",
        )
    if meta.presentation is None or meta.presentation_fingerprint != fingerprint(meta.presentation):
        raise _current_metric_state_error(
            ref,
            path="presentation_fingerprint",
            reason="fingerprint does not match the persisted presentation",
        )
    if meta.key_schema is None or meta.key_schema.fingerprint != fingerprint(
        meta.key_schema.fields
    ):
        raise _current_metric_state_error(
            ref,
            path="key_schema.fingerprint",
            reason="fingerprint does not match the persisted key fields",
        )

    artifact_identity = meta.artifact_identity
    dependency_digest = meta.semantic_dependency_digest
    source_domain = meta.source_compatibility_domain
    if artifact_identity is None or dependency_digest is None or source_domain is None:
        raise _current_metric_state_error(
            ref,
            path="artifact_identity",
            reason="artifact identity dependencies are incomplete",
        )
    artifact_mismatches = {
        "metric_identities": artifact_identity.metric_identities != meta.metric_identities,
        "dependency_fingerprint": (
            artifact_identity.dependency_fingerprint != dependency_digest.digest
        ),
        "source_domain_fingerprint": (
            artifact_identity.source_domain_fingerprint != source_domain.profile_fingerprint
        ),
        "presentation_fingerprint": (
            artifact_identity.presentation_fingerprint != meta.presentation_fingerprint
        ),
        "artifact_schema_version": (
            artifact_identity.artifact_schema_version != meta.artifact_schema_version
        ),
    }
    if meta.artifact_schema_version == "analysis-artifact/v7":
        artifact_mismatches["attribution_basis_fingerprint"] = (
            artifact_identity.attribution_basis_fingerprint
            != basis_fingerprint(meta.attribution_basis)
        )
    elif meta.attribution_basis is not None:
        artifact_mismatches["attribution_basis"] = True
    failed_artifact_fields = sorted(
        field for field, mismatched in artifact_mismatches.items() if mismatched
    )
    if failed_artifact_fields:
        raise _current_metric_state_error(
            ref,
            path="artifact_identity",
            reason=f"identity fields do not match frame state: {failed_artifact_fields}",
        )
    artifact_identity_payload: dict[str, object] = {
        "metric_identities": artifact_identity.metric_identities,
        "scope_fingerprint": artifact_identity.scope_fingerprint,
        "source_domain_fingerprint": artifact_identity.source_domain_fingerprint,
        "dependency_fingerprint": artifact_identity.dependency_fingerprint,
        "snapshot_fingerprint": artifact_identity.snapshot_fingerprint,
        "coverage_fingerprint": artifact_identity.coverage_fingerprint,
        "presentation_fingerprint": artifact_identity.presentation_fingerprint,
        "artifact_schema_version": artifact_identity.artifact_schema_version,
    }
    if meta.artifact_schema_version == "analysis-artifact/v7":
        artifact_identity_payload["attribution_basis_fingerprint"] = (
            artifact_identity.attribution_basis_fingerprint
        )
    if artifact_identity.fingerprint != fingerprint(artifact_identity_payload):
        raise _current_metric_state_error(
            ref,
            path="artifact_identity.fingerprint",
            reason="fingerprint does not match the persisted artifact identity",
        )

    comparable = meta.comparable_value_semantics
    assert comparable is not None
    comparable_payload = {
        "expression_fingerprint": comparable.expression_fingerprint,
        "evaluator_contracts": comparable.evaluator_contracts,
        "global_slice": comparable.global_slice,
        "key_schema_fingerprint": comparable.key_schema_fingerprint,
        "unit": comparable.unit,
        "fold": comparable.fold,
        "source_domain_fingerprint": comparable.source_domain_fingerprint,
        "definition_transform_fingerprint": comparable.definition_transform_fingerprint,
    }
    if comparable.fingerprint != fingerprint(comparable_payload):
        raise _current_metric_state_error(
            ref,
            path="comparable_value_semantics.fingerprint",
            reason="fingerprint does not match comparable semantics",
        )
    if comparable.key_schema_fingerprint != meta.key_schema.fingerprint:
        raise _current_metric_state_error(
            ref,
            path="comparable_value_semantics.key_schema_fingerprint",
            reason="key schema fingerprint does not match the frame key schema",
        )

    expected_refs = {
        "expression_graph_ref": f"{meta.ref}#expression-graph",
        "presentation_ref": f"{meta.ref}#presentation",
        "replay_graph_ref": f"{meta.ref}#replay-graph",
        "comparable_value_semantics_ref": f"{meta.ref}#comparable-value-semantics",
    }
    for field, expected in expected_refs.items():
        if getattr(meta, field) != expected:
            raise _current_metric_state_error(
                ref,
                path=field,
                reason=f"expected {expected!r}",
            )
    _validate_current_replay_payload(ref, meta)


def _delta_identity_recovery_error(ref: str, *, reason: str) -> FrameMetaInvalidError:
    return FrameMetaInvalidError(
        message=f"frame '{ref}' has an invalid delta comparison identity",
        repair=AnalysisRepair(
            kind="retry",
            action="Re-run session.compare(current, baseline, alignment=...) from the source MetricFrames.",
            help_target=LiveHelpTarget(surface="analysis", canonical_id="compare"),
            snippet="delta = session.compare(current, baseline, alignment=alignment)",
        ),
        context={
            "ref": ref,
            "artifact_schema_version": CURRENT_ARTIFACT_SCHEMA_VERSION,
            "reason": reason,
        },
    )


def _validate_delta_comparison_state(
    ref: str,
    meta: DeltaFrameMeta,
    *,
    session: Session,
) -> None:
    """Recompute persisted delta identity semantics from its two source frames."""

    current = load_frame(meta.source_current_ref, session=session)
    baseline = load_frame(meta.source_baseline_ref, session=session)
    if not isinstance(current, MetricFrame) or not isinstance(baseline, MetricFrame):
        raise _delta_identity_recovery_error(
            ref,
            reason="delta sources must both recover as MetricFrames",
        )
    identity = meta.comparison_identity
    current_identity = current.meta.metric_identity
    baseline_identity = baseline.meta.metric_identity
    current_comparable = current.meta.comparable_value_semantics
    baseline_comparable = baseline.meta.comparable_value_semantics
    if current_identity is None or baseline_identity is None:
        raise _delta_identity_recovery_error(ref, reason="source metric identity is missing")
    if current_comparable is None or baseline_comparable is None:
        raise _delta_identity_recovery_error(
            ref,
            reason="source comparable semantics are missing",
        )
    mismatches: list[str] = []
    if identity.current != current_identity:
        mismatches.append("current metric identity")
    if identity.baseline != baseline_identity:
        mismatches.append("baseline metric identity")
    if identity.current_artifact_id != (current.meta.artifact_id or current.ref):
        mismatches.append("current artifact id")
    if identity.baseline_artifact_id != (baseline.meta.artifact_id or baseline.ref):
        mismatches.append("baseline artifact id")

    policy_fields = {
        key: meta.alignment[key]
        for key in ("kind", "calendar", "period", "fallback", "mode", "strict_lengths")
        if key in meta.alignment
    }
    try:
        policy = AlignmentPolicy.model_validate(policy_fields)
    except ValidationError as exc:
        raise _delta_identity_recovery_error(
            ref,
            reason=f"persisted alignment policy is invalid: {exc}",
        ) from exc
    if identity.alignment_policy_fingerprint != fingerprint(policy.model_dump(mode="json")):
        mismatches.append("alignment policy fingerprint")

    cumulative_alignment = meta.cumulative_alignment
    expected_semantics: DeltaComparisonSemantics
    if cumulative_alignment is None:
        expected_semantics = ExactComparisonSemanticsV1(
            schema="exact-comparison-semantics/v1",
            comparable_semantics_fingerprint=current_comparable.fingerprint,
        )
        if current_comparable.fingerprint != baseline_comparable.fingerprint:
            mismatches.append("exact source comparable semantics")
    else:
        current_anchor = cumulative_compare_anchor(current.meta.cumulative)
        baseline_anchor = cumulative_compare_anchor(baseline.meta.cumulative)
        if not isinstance(current_anchor, tuple) or not isinstance(baseline_anchor, tuple):
            raise _delta_identity_recovery_error(
                ref,
                reason="comparable-period delta sources have invalid cumulative anchors",
            )
        if cumulative_alignment.current_authored_anchor != authored_comparable_period_anchor(
            current_anchor
        ):
            mismatches.append("current authored cumulative anchor")
        if cumulative_alignment.baseline_authored_anchor != authored_comparable_period_anchor(
            baseline_anchor
        ):
            mismatches.append("baseline authored cumulative anchor")
        current_graph = current.meta.expression_graph
        baseline_graph = baseline.meta.expression_graph
        if current_graph is None or baseline_graph is None:
            raise _delta_identity_recovery_error(
                ref,
                reason="comparable-period delta sources are missing expression graphs",
            )
        try:
            expected_semantics = cumulative_equivalent_comparison_semantics(
                current_graph=current_graph,
                baseline_graph=baseline_graph,
                current_comparable=current_comparable,
                baseline_comparable=baseline_comparable,
                current_anchor=current_anchor,
                baseline_anchor=baseline_anchor,
            )
        except (TypeError, ValueError) as exc:
            raise _delta_identity_recovery_error(
                ref,
                reason=f"source cumulative semantics cannot be recomputed: {exc}",
            ) from exc
    if identity.semantics != expected_semantics:
        mismatches.append("comparison semantics")
    if mismatches:
        raise _delta_identity_recovery_error(
            ref,
            reason=f"identity fields do not match recovered source state: {sorted(mismatches)}",
        )


def load_frame(ref: str | ArtifactRef, *, session: Session) -> BaseFrame:
    """Load a persisted analysis frame by ref from the given or active session."""
    import json

    if isinstance(ref, ArtifactRef):
        ref = ref.ref

    # Check the store first — the artifacts table is the source of truth.
    artifact_row = session._store.get_artifact(session.id, ref)
    if artifact_row is not None:
        # Use store-registered paths to locate the on-disk data.
        meta_path = session.project_root / artifact_row["meta_path"]
        if not meta_path.is_file():
            raise FrameCacheCorruptedError(
                message=f"frame '{ref}' is registered but meta file is missing",
                context={"ref": ref, "meta_path": str(meta_path)},
            )
        data_path = session.project_root / artifact_row["path"]
        if not data_path.is_file():
            raise FrameCacheCorruptedError(
                message=f"frame '{ref}' is registered but data file is missing",
                context={"ref": ref, "data_path": str(data_path)},
            )
        try:
            import pandas as pd

            df = pd.read_parquet(data_path, engine="pyarrow", to_pandas_kwargs={})
            meta = json.loads(meta_path.read_text())
        except Exception as exc:
            raise FrameCacheCorruptedError(
                message=f"frame '{ref}' exists on disk but cannot be loaded",
                context={"ref": ref, "cause": str(exc)},
            ) from exc
    else:
        # No store row — the frame is not registered in the session's artifacts
        # table, so it cannot be loaded through this session.
        raise FrameRefNotFound(
            message=f"no frame '{ref}' under session {session.id!r}",
            context={"session_id": session.id, "ref": ref},
        )

    artifact_schema_version = meta.get("artifact_schema_version")
    if artifact_schema_version not in {
        "analysis-artifact/v6",
        CURRENT_ARTIFACT_SCHEMA_VERSION,
    }:
        raise FrameMetaInvalidError(
            message=(
                f"frame '{ref}' uses unsupported artifact schema "
                f"{artifact_schema_version!r}; recreate the analysis session"
            ),
            context={
                "ref": ref,
                "got": artifact_schema_version,
                "expected": (
                    "analysis-artifact/v6",
                    CURRENT_ARTIFACT_SCHEMA_VERSION,
                ),
            },
        )
    if meta.get("session_id") != session.id:
        raise CrossSessionFrameError(
            message=(
                f"frame '{ref}' belongs to session {meta.get('session_id')!r} "
                f"but was loaded through session {session.id!r}"
            ),
        )
    kind = meta["kind"]
    if kind not in _FRAME_CLASSES:
        raise FrameRefNotFound(message=f"unknown frame kind '{kind}' for ref '{ref}'")
    frame_cls, meta_cls = _FRAME_CLASSES[kind]
    if kind == "event_frame":
        semantic_kind = meta.get("semantic_kind")
        event_meta_classes = {
            "journey": EventFrameMeta,
            "funnel": EventFunnelFrameMeta,
            "time_to_event": EventTimeToEventFrameMeta,
        }
        if semantic_kind not in event_meta_classes:
            raise FrameMetaInvalidError(
                message=f"frame '{ref}' has an unsupported Event semantic shape",
                context={
                    "ref": ref,
                    "artifact_schema_version": CURRENT_ARTIFACT_SCHEMA_VERSION,
                    "got_semantic_kind": semantic_kind,
                    "expected_semantic_kinds": tuple(event_meta_classes),
                },
            )
        meta_cls = event_meta_classes[semantic_kind]
    if kind == "lifecycle_frame":
        semantic_kind = meta.get("semantic_kind")
        lifecycle_meta_classes = {
            "history": LifecycleHistoryFrameMeta,
            "distribution": LifecycleDistributionFrameMeta,
            "transitions": LifecycleTransitionsFrameMeta,
            "dwell": LifecycleDwellFrameMeta,
            "violations": LifecycleViolationsFrameMeta,
        }
        if semantic_kind not in lifecycle_meta_classes:
            raise FrameMetaInvalidError(
                message=f"frame '{ref}' has an unsupported Lifecycle semantic shape",
                context={
                    "ref": ref,
                    "artifact_schema_version": CURRENT_ARTIFACT_SCHEMA_VERSION,
                    "got_semantic_kind": semantic_kind,
                    "expected_semantic_kinds": tuple(lifecycle_meta_classes),
                },
            )
        meta_cls = lifecycle_meta_classes[semantic_kind]
        if semantic_kind == "history":
            manifest_payload = meta.get("violation_trace")
            filename = (
                manifest_payload.get("filename") if isinstance(manifest_payload, dict) else None
            )
            if isinstance(filename, str):
                frame_dir = data_path.parent.resolve()
                trace_path = (frame_dir / filename).resolve()
                if trace_path.parent != frame_dir:
                    raise FrameCacheCorruptedError(
                        message=f"frame '{ref}' has an escaped Lifecycle trace path",
                        context={
                            "ref": ref,
                            "cause": "auxiliary trace path is outside the artifact directory",
                        },
                    )
    if kind == "delta_frame" and meta.get("semantic_kind") == "funnel":
        meta_cls = FunnelDeltaFrameMeta
    if kind == "attribution_frame" and meta.get("semantic_kind") == "funnel_loss_rate":
        meta_cls = FunnelAttributionFrameMeta
    if kind == "candidate_set" and meta.get("shape") == "semantic_hypothesis":
        meta_cls = SemanticHypothesisCandidateSetMeta
    if (
        kind == "delta_frame"
        and meta.get("semantic_kind") != "funnel"
        and "comparison_identity" not in meta
    ):
        raise FrameMetaInvalidError(
            message=f"frame '{ref}' is missing its required delta identity",
            context={
                "ref": ref,
                "artifact_schema_version": CURRENT_ARTIFACT_SCHEMA_VERSION,
                "missing_state": ["comparison_identity"],
            },
        )
    if kind == "delta_frame" and meta.get("semantic_kind") != "funnel":
        comparison_payload = meta.get("comparison_identity")
        if isinstance(comparison_payload, dict) and comparison_payload.get("schema") != (
            "delta-comparison/v2"
        ):
            raise _delta_identity_recovery_error(
                ref,
                reason=(
                    "only delta-comparison/v2 is supported; persisted V1 deltas must be "
                    "re-created from their source MetricFrames"
                ),
            )
    if kind == "metric_frame":
        required_metric_fields = _CURRENT_METRIC_FRAME_FIELDS | (
            {"attribution_basis"}
            if artifact_schema_version == CURRENT_ARTIFACT_SCHEMA_VERSION
            else set()
        )
        missing_fields = sorted(required_metric_fields - set(meta))
        if missing_fields:
            raise FrameMetaInvalidError(
                message=f"frame '{ref}' has a corrupt current-schema metadata payload",
                context={
                    "ref": ref,
                    "artifact_schema_version": CURRENT_ARTIFACT_SCHEMA_VERSION,
                    "missing_fields": missing_fields,
                },
            )
    if (
        kind == "delta_frame"
        and meta.get("semantic_kind") != "funnel"
        and artifact_schema_version == CURRENT_ARTIFACT_SCHEMA_VERSION
        and "attribution_basis" not in meta
    ):
        raise FrameMetaInvalidError(
            message=f"frame '{ref}' is missing its v7 attribution basis field",
            context={
                "ref": ref,
                "artifact_schema_version": artifact_schema_version,
                "missing_state": ["attribution_basis"],
            },
        )
    try:
        parsed_meta = (
            cast("Any", meta_cls).model_validate_json(json.dumps(meta))
            if kind
            in {
                "event_frame",
                "lifecycle_frame",
                "subject_set",
            }
            or (kind == "delta_frame" and meta.get("semantic_kind") == "funnel")
            or (kind == "attribution_frame" and meta.get("semantic_kind") == "funnel_loss_rate")
            or (kind == "candidate_set" and meta.get("shape") == "semantic_hypothesis")
            # CandidateOrigin contains the sealed SemanticEdgeRef, whose
            # persisted dict form is intentionally accepted only in JSON mode.
            or bool(meta.get("candidate_origins"))
            else meta_cls(**meta)
        )
    except ValidationError as exc:
        raise FrameMetaInvalidError(
            message=f"frame '{ref}' has a corrupt current-schema metadata payload",
            context={
                "ref": ref,
                "artifact_schema_version": CURRENT_ARTIFACT_SCHEMA_VERSION,
                "validation_errors": exc.errors(),
            },
        ) from exc
    if isinstance(parsed_meta, MetricFrameMeta):
        last_intent = parsed_meta.lineage.steps[-1].intent if parsed_meta.lineage.steps else None
        if last_intent in {"observe", "select_metric"}:
            metric_required_state: dict[str, object | None] = {
                "metric_identities": parsed_meta.metric_identities or None,
                "catalog_definition_fingerprint": (parsed_meta.catalog_definition_fingerprint),
                "expression_graph": parsed_meta.expression_graph,
                "expression_fingerprint": parsed_meta.expression_fingerprint,
                "semantic_dependency_digest": parsed_meta.semantic_dependency_digest,
                "presentation": parsed_meta.presentation,
                "presentation_fingerprint": parsed_meta.presentation_fingerprint,
                "artifact_identity": parsed_meta.artifact_identity,
                "key_schema": parsed_meta.key_schema,
                "source_compatibility_domain": parsed_meta.source_compatibility_domain,
                "component_graph_ref": parsed_meta.component_graph_ref,
                "comparable_value_semantics": parsed_meta.comparable_value_semantics,
                "execution_stats": parsed_meta.execution_stats,
                "unit_state": (
                    parsed_meta.unit_state
                    if len(parsed_meta.metric_identities) == 1
                    else (
                        tuple(
                            binding.unit_state
                            for binding in parsed_meta.measure_bindings
                        )
                        if parsed_meta.measure_bindings
                        else parsed_meta.measures
                    )
                ),
            }
            missing_state = sorted(
                name for name, value in metric_required_state.items() if value is None
            )
            if missing_state:
                raise FrameMetaInvalidError(
                    message=f"frame '{ref}' has incomplete current-schema metric state",
                    context={
                        "ref": ref,
                        "artifact_schema_version": CURRENT_ARTIFACT_SCHEMA_VERSION,
                        "missing_state": missing_state,
                    },
                )
            _validate_current_metric_state(ref, parsed_meta)
    if isinstance(parsed_meta, DeltaFrameMeta):
        expected_basis_fingerprint = basis_fingerprint(parsed_meta.attribution_basis)
        if parsed_meta.artifact_schema_version == "analysis-artifact/v7":
            if (
                parsed_meta.comparison_identity.attribution_basis_fingerprint
                != expected_basis_fingerprint
            ):
                raise FrameMetaInvalidError(
                    message=f"frame '{ref}' has a mismatched attribution basis identity",
                    context={
                        "ref": ref,
                        "artifact_schema_version": artifact_schema_version,
                        "expected_basis_fingerprint": expected_basis_fingerprint,
                    },
                )
        elif parsed_meta.attribution_basis is not None:
            raise FrameMetaInvalidError(
                message=f"legacy frame '{ref}' cannot carry a v7 attribution basis",
                context={"ref": ref, "artifact_schema_version": artifact_schema_version},
            )
        delta_required_state: dict[str, object | None] = {
            "metric_identity": parsed_meta.metric_identity,
            "baseline_metric_identity": parsed_meta.baseline_metric_identity,
            "comparison_identity": parsed_meta.comparison_identity,
        }
        missing_state = sorted(
            name for name, value in delta_required_state.items() if value is None
        )
        if missing_state:
            raise FrameMetaInvalidError(
                message=f"frame '{ref}' has incomplete current-schema delta identity",
                context={
                    "ref": ref,
                    "artifact_schema_version": CURRENT_ARTIFACT_SCHEMA_VERSION,
                    "missing_state": missing_state,
                },
            )
        _validate_delta_comparison_state(ref, parsed_meta, session=session)
    if isinstance(parsed_meta, AttributionFrameMeta):
        try:
            validate_generic_attribution_rows(parsed_meta, df)
        except ValueError as exc:
            raise FrameMetaInvalidError(
                message=f"frame '{ref}' has corrupt generic attribution rows",
                context={
                    "ref": ref,
                    "artifact_schema_version": artifact_schema_version,
                    "reason": str(exc),
                },
            ) from exc
    if isinstance(parsed_meta, (CandidateSetMeta, SemanticHypothesisCandidateSetMeta)):
        if list(df.columns) != CANDIDATE_COLUMNS:
            raise FrameMetaInvalidError(
                message=f"frame '{ref}' has a non-canonical CandidateSet column layout",
                context={
                    "ref": ref,
                    "got_columns": list(df.columns),
                    "expected_columns": CANDIDATE_COLUMNS,
                },
            )
        validate_shape_columns(parsed_meta.shape, df)
        validate_candidate_frame_identity(
            shape=parsed_meta.shape,
            source_artifact_ref=parsed_meta.source_ref,
            dataframe=df,
        )
        if isinstance(parsed_meta, SemanticHypothesisCandidateSetMeta):
            validate_semantic_hypothesis_frame_integrity(
                dataframe=df,
                edge_contexts=parsed_meta.edge_contexts,
                readiness_fingerprints={
                    binding.metric_ref: binding.fingerprint
                    for binding in parsed_meta.readiness_bindings
                },
                exclusions=parsed_meta.resolution_summary.exclusions,
            )
    auxiliary_frames: dict[str, Any] = {}
    if isinstance(parsed_meta, LifecycleHistoryFrameMeta):
        manifest = parsed_meta.violation_trace
        if manifest.content_hash is None:
            raise FrameMetaInvalidError(
                message=f"frame '{ref}' is missing its Lifecycle trace content hash",
                context={
                    "ref": ref,
                    "artifact_schema_version": CURRENT_ARTIFACT_SCHEMA_VERSION,
                    "missing_state": ["violation_trace.content_hash"],
                },
            )
        frame_dir = data_path.parent.resolve()
        trace_path = (frame_dir / manifest.filename).resolve()
        if trace_path.parent != frame_dir:
            raise FrameCacheCorruptedError(
                message=f"frame '{ref}' has an escaped Lifecycle trace path",
                context={
                    "ref": ref,
                    "cause": "auxiliary trace path is outside the artifact directory",
                },
            )
        if not trace_path.is_file():
            raise FrameCacheCorruptedError(
                message=f"frame '{ref}' Lifecycle trace is missing",
                context={"ref": ref, "cause": f"missing {manifest.filename}"},
            )
        if compute_file_content_hash(trace_path) != manifest.content_hash:
            raise FrameCacheCorruptedError(
                message=f"frame '{ref}' Lifecycle trace hash does not match metadata",
                context={"ref": ref, "cause": "auxiliary trace content hash mismatch"},
            )
        try:
            import pandas as pd

            trace = pd.read_parquet(
                trace_path,
                engine="pyarrow",
                to_pandas_kwargs={},
            )
        except Exception as exc:
            raise FrameCacheCorruptedError(
                message=f"frame '{ref}' Lifecycle trace cannot be loaded",
                context={"ref": ref, "cause": str(exc)},
            ) from exc
        if len(trace) != manifest.row_count:
            raise FrameCacheCorruptedError(
                message=f"frame '{ref}' Lifecycle trace row count does not match metadata",
                context={"ref": ref, "cause": "auxiliary trace row count mismatch"},
            )
        expected_parent_hash = compute_frame_content_hash(
            meta=parsed_meta,
            data_path=data_path,
        )
        if (
            parsed_meta.content_hash != expected_parent_hash
            or artifact_row["content_hash"] != expected_parent_hash
        ):
            raise FrameCacheCorruptedError(
                message=f"frame '{ref}' Lifecycle content identity is corrupt",
                context={"ref": ref, "cause": "parent artifact content hash mismatch"},
            )
        auxiliary_frames[manifest.filename] = trace
    return cast(
        "BaseFrame",
        frame_cls(
            _df=df,
            meta=parsed_meta,
            _auxiliary_frames=auxiliary_frames,
        ),
    )
