"""Live preconditions for the optional ontology discovery continuation."""

from __future__ import annotations

from pathlib import Path

from marivo.analysis.errors import AnalysisRepair
from marivo.analysis.frames.base import (
    ArtifactAffordance,
    ArtifactContract,
    ArtifactPrecondition,
    BaseFrame,
)
from marivo.analysis.session._runtime import get_process_current
from marivo.semantic.metric_graph import CatalogMetricIdentity


def _repair(
    *, kind: str, action: str, surface: str, target: str, snippet: str | None = None
) -> AnalysisRepair:
    return AnalysisRepair.model_validate(
        {
            "kind": kind,
            "action": action,
            "help_target": {"surface": surface, "canonical_id": target},
            "snippet": snippet,
        }
    )


def _live_preconditions(frame: BaseFrame) -> tuple[ArtifactPrecondition, ...]:
    session = get_process_current()
    if (
        session is None
        or session.id != frame.meta.session_id
        or session.project_root.resolve() != Path(frame.meta.project_root).resolve()
    ):
        return (
            ArtifactPrecondition(
                check="session_binding",
                status="fail",
                reason="ontology discovery requires the artifact's live owning Session",
                repair=_repair(
                    kind="environment",
                    action="Attach the artifact's owning Session before ontology discovery.",
                    surface="analysis",
                    target="session.get_or_create",
                ),
            ),
        )

    preconditions: list[ArtifactPrecondition] = []
    artifact_ref = frame.meta.artifact_id or frame.meta.ref
    persisted = session._store.get_artifact(session.id, artifact_ref) is not None
    preconditions.append(
        ArtifactPrecondition(
            check="persisted_source",
            status="pass" if persisted else "fail",
            reason=(
                "source is a committed Session artifact"
                if persisted
                else "ontology discovery accepts only a committed source artifact"
            ),
            repair=(
                None
                if persisted
                else _repair(
                    kind="retry",
                    action="Recreate the source through a Session analysis operator.",
                    surface="analysis",
                    target="observe",
                    snippet="source = session.observe(metric)",
                )
            ),
        )
    )
    ontology_state = session._ontology_state
    if ontology_state == "ready":
        preconditions.append(
            ArtifactPrecondition(
                check="ontology_binding",
                status="pass",
                reason="the owning Session has a validated ontology binding",
            )
        )
    elif ontology_state == "absent":
        preconditions.append(
            ArtifactPrecondition(
                check="ontology_binding",
                status="fail",
                reason="models/ontology.py is not configured",
                repair=_repair(
                    kind="semantic_authoring",
                    action="Author models/ontology.py with typed ontology constructors.",
                    surface="ontology",
                    target="authoring",
                ),
            )
        )
    else:
        preconditions.append(
            ArtifactPrecondition(
                check="ontology_binding",
                status="fail",
                reason="the configured ontology failed validation for this Session catalog",
                repair=_repair(
                    kind="inspect",
                    action="Inspect current ontology validation issues.",
                    surface="ontology",
                    target="authoring",
                    snippet="mo.load(semantic=session.catalog)",
                ),
            )
        )

    comparison = getattr(frame.meta, "comparison_identity", None)
    if comparison is not None:
        identities = (comparison.current, comparison.baseline)
        same_catalog_metric = (
            all(isinstance(identity, CatalogMetricIdentity) for identity in identities)
            and identities[0].metric_ref == identities[1].metric_ref
        )
        preconditions.append(
            ArtifactPrecondition(
                check="single_metric_lineage",
                status="pass" if same_catalog_metric else "fail",
                reason=(
                    "DeltaFrame retains one shared catalog Metric identity"
                    if same_catalog_metric
                    else "DeltaFrame must compare observations of the same catalog Metric"
                ),
                repair=(
                    None
                    if same_catalog_metric
                    else _repair(
                        kind="retry",
                        action="Compare two observations of one exact catalog Metric.",
                        surface="analysis",
                        target="compare",
                        snippet="delta = session.compare(current, baseline)",
                    )
                ),
            )
        )
    return tuple(preconditions)


def attach_ontology_discovery_preconditions(
    frame: BaseFrame, contract: ArtifactContract
) -> ArtifactContract:
    """Attach live, repairable gates to the ontology-discovery affordance."""
    updated: list[ArtifactAffordance] = []
    for affordance in contract.affordances:
        if affordance.capability_id == "discover.semantic_hypotheses":
            affordance = affordance.model_copy(
                update={
                    "preconditions": (
                        *affordance.preconditions,
                        *_live_preconditions(frame),
                    )
                }
            )
        updated.append(affordance)
    return contract.model_copy(update={"affordances": tuple(updated)})


__all__ = ["attach_ontology_discovery_preconditions"]
