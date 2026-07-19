"""Load persisted analysis frames."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from pydantic import ValidationError

from marivo.analysis.errors import (
    CrossSessionFrameError,
    FrameCacheCorruptedError,
    FrameMetaInvalidError,
    FrameRefNotFound,
)
from marivo.analysis.frames.association import AssociationResult, AssociationResultMeta
from marivo.analysis.frames.attribution import AttributionFrame, AttributionFrameMeta
from marivo.analysis.frames.base import CURRENT_ARTIFACT_SCHEMA_VERSION, BaseFrame
from marivo.analysis.frames.candidate import CandidateSet, CandidateSetMeta
from marivo.analysis.frames.component import ComponentFrame, ComponentFrameMeta
from marivo.analysis.frames.coverage import CoverageFrame, CoverageFrameMeta
from marivo.analysis.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis.frames.forecast import ForecastFrame, ForecastFrameMeta
from marivo.analysis.frames.hypothesis import HypothesisTestResult, HypothesisTestResultMeta
from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.frames.quality import QualityReport, QualityReportMeta
from marivo.analysis.refs import ArtifactRef
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
    "quality_report": (QualityReport, QualityReportMeta),
    "component_frame": (ComponentFrame, ComponentFrameMeta),
    "coverage_frame": (CoverageFrame, CoverageFrameMeta),
}

_CURRENT_METRIC_FRAME_FIELDS = frozenset(
    {
        "metric_identity",
        "metric_identities",
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

    dimensions = observe_step.params.get("dimensions")
    if dimensions is not None and (
        not isinstance(dimensions, list)
        or any(
            not isinstance(item, dict)
            or not isinstance(item.get("semantic_id"), str)
            or not item["semantic_id"]
            or set(item) != {"semantic_id"}
            for item in dimensions
        )
    ):
        raise _current_metric_state_error(
            ref,
            path="lineage.observe.params.dimensions",
            reason="dimensions must be typed semantic_id objects",
        )


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
            artifact_identity.dependency_fingerprint != dependency_digest.fingerprint
        ),
        "source_domain_fingerprint": (
            artifact_identity.source_domain_fingerprint != source_domain.profile_fingerprint
        ),
        "presentation_fingerprint": (
            artifact_identity.presentation_fingerprint != meta.presentation_fingerprint
        ),
        "artifact_schema_version": (
            artifact_identity.artifact_schema_version != CURRENT_ARTIFACT_SCHEMA_VERSION
        ),
    }
    failed_artifact_fields = sorted(
        field for field, mismatched in artifact_mismatches.items() if mismatched
    )
    if failed_artifact_fields:
        raise _current_metric_state_error(
            ref,
            path="artifact_identity",
            reason=f"identity fields do not match frame state: {failed_artifact_fields}",
        )
    artifact_identity_payload = {
        "metric_identities": artifact_identity.metric_identities,
        "scope_fingerprint": artifact_identity.scope_fingerprint,
        "source_domain_fingerprint": artifact_identity.source_domain_fingerprint,
        "dependency_fingerprint": artifact_identity.dependency_fingerprint,
        "snapshot_fingerprint": artifact_identity.snapshot_fingerprint,
        "coverage_fingerprint": artifact_identity.coverage_fingerprint,
        "presentation_fingerprint": artifact_identity.presentation_fingerprint,
        "artifact_schema_version": artifact_identity.artifact_schema_version,
    }
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


def load_frame(ref: str | ArtifactRef, *, session: Session) -> BaseFrame:
    """Load a persisted analysis frame by ref from the given or active session."""
    import json

    if isinstance(ref, ArtifactRef):
        ref = ref.id

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
    if artifact_schema_version != CURRENT_ARTIFACT_SCHEMA_VERSION:
        raise FrameMetaInvalidError(
            message=(
                f"frame '{ref}' uses unsupported artifact schema "
                f"{artifact_schema_version!r}; recreate the analysis session"
            ),
            context={
                "ref": ref,
                "got": artifact_schema_version,
                "expected": CURRENT_ARTIFACT_SCHEMA_VERSION,
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
    if kind == "metric_frame":
        missing_fields = sorted(_CURRENT_METRIC_FRAME_FIELDS - set(meta))
        if missing_fields:
            raise FrameMetaInvalidError(
                message=f"frame '{ref}' has a corrupt current-schema metadata payload",
                context={
                    "ref": ref,
                    "artifact_schema_version": CURRENT_ARTIFACT_SCHEMA_VERSION,
                    "missing_fields": missing_fields,
                },
            )
    try:
        parsed_meta = meta_cls(**meta)
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
                    else parsed_meta.measures
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
        last_intent = parsed_meta.lineage.steps[-1].intent if parsed_meta.lineage.steps else None
        if last_intent == "compare":
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
    return cast("BaseFrame", frame_cls(_df=df, meta=parsed_meta))
