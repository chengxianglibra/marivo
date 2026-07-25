"""Private validation and pre-aggregation lowering for typed SubjectSet cohorts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import ibis

from marivo.analysis.errors import (
    AnalysisRepair,
    EventCoverageUnknownError,
    SubjectSetMismatchError,
)
from marivo.analysis.frames.subject import SubjectCohortBinding, SubjectSet
from marivo.analysis.intents._observe_planner_catalog import (
    _entity,
    _from_entity_id,
    _to_entity_id,
)
from marivo.analysis.intents._observe_planner_fields import (
    resolved_edge_safety,
    unique_shortest_relationship_path,
)
from marivo.analysis.intents._observe_planner_joins import _field_fn
from marivo.analysis.intents._observe_planner_types import JoinSafety
from marivo.analysis.intents.observe_errors import ObservePlanningError
from marivo.introspection.live.model import LiveHelpTarget
from marivo.refs import RefPayloadV1

if TYPE_CHECKING:
    from marivo.analysis.session.core import Session
    from marivo.semantic.catalog import SemanticCatalog


@dataclass(frozen=True)
class ResolvedSubjectCohort:
    """Validated binding plus transient authorized identity membership."""

    binding: SubjectCohortBinding
    subject_entity_ref: RefPayloadV1
    subject_identity: tuple[str, ...]
    identities: tuple[tuple[object, ...], ...]


def _repair(
    *,
    consumer: str,
    action: str,
    kind: Literal["inspect", "semantic_authoring"] = "inspect",
) -> AnalysisRepair:
    return AnalysisRepair(
        kind=kind,
        action=action,
        help_target=LiveHelpTarget(surface="analysis", canonical_id=consumer),
    )


def _mismatch(
    *,
    consumer: str,
    message: str,
    expected: str,
    received: str,
    location: str,
    action: str,
    candidates: tuple[str, ...] = (),
    repair_kind: Literal["inspect", "semantic_authoring"] = "inspect",
) -> SubjectSetMismatchError:
    repair = _repair(
        consumer=consumer,
        action=action,
        kind=repair_kind,
    )
    if candidates:
        repair = repair.model_copy(update={"candidates": candidates[:5]})
    return SubjectSetMismatchError(
        message=message,
        expected=expected,
        received=received,
        location=location,
        repair=repair,
    )


def _current_identity_signature(
    catalog: SemanticCatalog,
    *,
    entity_path: str,
) -> tuple[str, ...]:
    registry = catalog._require_index().registry
    entity = registry.entities.get(entity_path)
    if entity is None:
        return ()
    return tuple(
        (
            component
            if component in registry.dimensions
            else (
                f"{entity_path}.{component}"
                if f"{entity_path}.{component}" in registry.dimensions
                else component
            )
        )
        for component in entity.primary_key
    )


def _require_current_artifact(
    *,
    session: Session,
    artifact_ref: str,
    artifact_fingerprint: str,
    consumer: str,
    location: str,
    role: str,
) -> None:
    row = session._store.get_artifact(session.id, artifact_ref)
    persisted_fingerprint = row["content_hash"] if row is not None else None
    if row is None or persisted_fingerprint != artifact_fingerprint:
        raise _mismatch(
            consumer=consumer,
            message=f"The SubjectSet {role} artifact is missing or stale in this session.",
            expected="a currently registered artifact with the retained content fingerprint",
            received=f"{role}=unavailable_or_changed",
            location=location,
            action="Inspect the source artifact in this session and rebuild the SubjectSet.",
        )


def resolve_subject_cohort(
    *,
    session: Session,
    cohort: SubjectSet | None,
    consumer: str,
    expected_subject_entity: RefPayloadV1 | None = None,
    expected_subject_identity: tuple[str, ...] | None = None,
) -> ResolvedSubjectCohort | None:
    """Validate one exact SubjectSet without copying identities into metadata."""

    if cohort is None:
        return None
    if type(cohort) is not SubjectSet:
        raise _mismatch(
            consumer=consumer,
            message=f"{consumer} cohort must be an exact SubjectSet.",
            expected="SubjectSet",
            received=type(cohort).__name__,
            location=f"session.{consumer}.cohort",
            action="Pass a SubjectSet returned by session.select_subjects(...).",
        )
    if cohort.meta.session_id != session.id:
        raise _mismatch(
            consumer=consumer,
            message="The SubjectSet belongs to a different analysis session.",
            expected="a SubjectSet owned by the current session",
            received="different_session",
            location=f"session.{consumer}.cohort",
            action="Load or rebuild the SubjectSet in the current session.",
        )
    if Path(cohort.meta.project_root).resolve() != session.project_root.resolve():
        raise _mismatch(
            consumer=consumer,
            message="The SubjectSet belongs to a different Marivo project.",
            expected="a SubjectSet owned by the current project",
            received="different_project",
            location=f"session.{consumer}.cohort",
            action="Rebuild the SubjectSet from an artifact in the current project.",
        )
    if cohort.meta.catalog_definition_fingerprint != session.catalog.definition_fingerprint:
        raise _mismatch(
            consumer=consumer,
            message="The SubjectSet was produced from a different catalog definition.",
            expected="a SubjectSet built from the active catalog fingerprint",
            received="catalog_definition_changed",
            location=f"session.{consumer}.cohort",
            action="Reload the active catalog and rebuild the SubjectSet.",
        )
    if cohort.meta.coverage_status != "ready":
        raise EventCoverageUnknownError(
            message="The SubjectSet contains coverage-censored selection truth.",
            expected="SubjectSet coverage_status='ready'",
            received=f"coverage_status={cohort.meta.coverage_status!r}",
            location=f"session.{consumer}.cohort",
            repair=_repair(
                consumer=consumer,
                action=(
                    "Inspect the source journey coverage and rebuild the SubjectSet "
                    "after complete follow-up is available."
                ),
            ),
        )

    artifact_ref = cohort.meta.artifact_id or cohort.meta.ref
    artifact_fingerprint = cohort.meta.content_hash
    if not artifact_fingerprint:
        raise _mismatch(
            consumer=consumer,
            message="The SubjectSet has no persisted content fingerprint.",
            expected="a persisted SubjectSet with content_hash",
            received="content_hash=missing",
            location=f"session.{consumer}.cohort",
            action="Reload or rebuild the SubjectSet before using it as a cohort.",
        )
    _require_current_artifact(
        session=session,
        artifact_ref=artifact_ref,
        artifact_fingerprint=artifact_fingerprint,
        consumer=consumer,
        location=f"session.{consumer}.cohort",
        role="cohort",
    )
    _require_current_artifact(
        session=session,
        artifact_ref=cohort.meta.source.artifact_ref,
        artifact_fingerprint=cohort.meta.source.artifact_fingerprint,
        consumer=consumer,
        location=f"session.{consumer}.cohort.source",
        role="source",
    )

    current_signature = _current_identity_signature(
        session.catalog,
        entity_path=cohort.meta.subject_entity_ref.path,
    )
    if current_signature != cohort.meta.subject_identity:
        raise _mismatch(
            consumer=consumer,
            message="The SubjectSet identity signature is stale against the current catalog.",
            expected=repr(current_signature),
            received=repr(cohort.meta.subject_identity),
            location=f"session.{consumer}.cohort.subject_identity",
            action="Reload the current catalog and rebuild the SubjectSet.",
        )
    if (
        expected_subject_entity is not None
        and cohort.meta.subject_entity_ref != expected_subject_entity
    ):
        raise _mismatch(
            consumer=consumer,
            message="The SubjectSet subject Entity does not match the consumer subject.",
            expected=expected_subject_entity.path,
            received=cohort.meta.subject_entity_ref.path,
            location=f"session.{consumer}.cohort.subject_entity_ref",
            action="Choose a SubjectSet with the same governed subject Entity.",
            candidates=(expected_subject_entity.path,),
        )
    if (
        expected_subject_identity is not None
        and cohort.meta.subject_identity != expected_subject_identity
    ):
        raise _mismatch(
            consumer=consumer,
            message="The SubjectSet identity signature does not match the consumer subject.",
            expected=repr(expected_subject_identity),
            received=repr(cohort.meta.subject_identity),
            location=f"session.{consumer}.cohort.subject_identity",
            action="Rebuild the SubjectSet from a journey with the same subject identity.",
        )

    values = cohort._dataframe_copy()["subject_identity"].tolist()
    identities: list[tuple[object, ...]] = []
    for value in values:
        identity = value if isinstance(value, tuple) else tuple(value)
        if len(identity) != len(cohort.meta.subject_identity):
            raise _mismatch(
                consumer=consumer,
                message="The SubjectSet rows do not match the retained identity signature.",
                expected=f"{len(cohort.meta.subject_identity)} identity components",
                received="row_identity_arity_mismatch",
                location=f"session.{consumer}.cohort.rows",
                action="Inspect and rebuild the persisted SubjectSet artifact.",
            )
        identities.append(identity)
    if len(set(identities)) != len(identities):
        raise _mismatch(
            consumer=consumer,
            message="The SubjectSet contains duplicate subject identities.",
            expected="unique subject_identity rows",
            received="duplicate_subject_identity",
            location=f"session.{consumer}.cohort.rows",
            action="Inspect and rebuild the persisted SubjectSet artifact.",
        )

    binding = SubjectCohortBinding(
        artifact_ref=artifact_ref,
        artifact_fingerprint=artifact_fingerprint,
        subject_entity_ref=cohort.meta.subject_entity_ref,
        subject_identity=cohort.meta.subject_identity,
        source_artifact_ref=cohort.meta.source.artifact_ref,
        selection_fingerprint=cohort.meta.selection_fingerprint,
    )
    return ResolvedSubjectCohort(
        binding=binding,
        subject_entity_ref=cohort.meta.subject_entity_ref,
        subject_identity=cohort.meta.subject_identity,
        identities=tuple(identities),
    )


def validate_metric_cohort_path(
    *,
    catalog: SemanticCatalog,
    root_entity: str,
    cohort: ResolvedSubjectCohort,
) -> None:
    """Require one unique to-one path from a metric root to the cohort subject."""

    subject_entity = cohort.subject_entity_ref.path
    try:
        path = unique_shortest_relationship_path(
            catalog,
            root_entity,
            subject_entity,
        )
    except ObservePlanningError as exc:
        code = str(exc._context.get("code", "relationship_path_invalid"))
        raw_candidates = exc._context.get("candidates")
        raw_paths = raw_candidates.get("paths", ()) if isinstance(raw_candidates, dict) else ()
        candidates = tuple(
            " -> ".join(str(component) for component in raw_path)
            for raw_path in raw_paths
            if isinstance(raw_path, list)
        )
        raise _mismatch(
            consumer="observe",
            message="The metric root has no unique governed path to the cohort subject.",
            expected=(
                "one unique directed path from the metric root to the SubjectSet "
                "subject containing only to-one edges"
            ),
            received=f"relationship_path={code}",
            location="session.observe.cohort",
            action=(
                "Author or disambiguate a governed to-one Relationship path "
                "between the metric root and SubjectSet subject."
            ),
            candidates=candidates,
            repair_kind="semantic_authoring",
        ) from exc
    current = root_entity
    for relationship in path:
        safety = resolved_edge_safety(catalog, relationship, from_entity=current)
        if safety not in {JoinSafety.ONE_TO_ONE, JoinSafety.MANY_TO_ONE}:
            raise _mismatch(
                consumer="observe",
                message="Metric cohort traversal is not fanout-safe.",
                expected="a unique path containing only one-to-one or many-to-one edges",
                received=f"join_safety={safety.value!r}",
                location="session.observe.cohort",
                action="Use a cohort reachable from every metric root through a governed to-one path.",
                candidates=(root_entity, subject_entity),
            )
        current = (
            _to_entity_id(relationship)
            if _from_entity_id(relationship) == current
            else _from_entity_id(relationship)
        )

    root_datasource = _entity(catalog, root_entity).datasource
    subject_datasource = _entity(catalog, subject_entity).datasource
    if root_datasource != subject_datasource:
        raise _mismatch(
            consumer="observe",
            message="Metric cohort traversal crosses datasource boundaries.",
            expected="metric root and cohort subject on one executable datasource",
            received="cross_datasource_subject_path",
            location="session.observe.cohort",
            action="Use a SubjectSet whose subject is reachable within the metric datasource.",
            candidates=(root_entity, subject_entity),
        )


def apply_subject_membership(
    *,
    catalog: SemanticCatalog,
    table: Any,
    cohort: ResolvedSubjectCohort,
) -> Any:
    """Apply one exact SubjectSet as an Ibis semi-join before aggregation."""

    identity_exprs = [_field_fn(catalog, component)(table) for component in cohort.subject_identity]
    membership_columns = tuple(
        f"__marivo_cohort_identity_{index}" for index in range(len(identity_exprs))
    )
    schema = ibis.schema(
        {
            column: expression.type()
            for column, expression in zip(
                membership_columns,
                identity_exprs,
                strict=True,
            )
        }
    )
    rows = [dict(zip(membership_columns, identity, strict=True)) for identity in cohort.identities]
    membership = ibis.memtable(rows, schema=schema)
    predicates = [
        expression == membership[column]
        for expression, column in zip(identity_exprs, membership_columns, strict=True)
    ]
    return table.semi_join(membership, predicates)


def apply_event_subject_membership(
    *,
    table: Any,
    cohort: ResolvedSubjectCohort,
    participant_names: tuple[str, ...],
) -> Any:
    """Semi-join materialized Event rows to any selected participant role."""

    if not participant_names:
        raise ValueError("Event cohort lowering requires at least one participant role")
    role_exprs = tuple(
        tuple(
            table[f"__subject_{participant_name}_identity_{index}"]
            for index in range(len(cohort.subject_identity))
        )
        for participant_name in participant_names
    )
    membership_columns = tuple(
        f"__marivo_cohort_identity_{index}" for index in range(len(cohort.subject_identity))
    )
    schema = ibis.schema(
        {
            column: expression.type()
            for column, expression in zip(
                membership_columns,
                role_exprs[0],
                strict=True,
            )
        }
    )
    membership = ibis.memtable(
        [dict(zip(membership_columns, identity, strict=True)) for identity in cohort.identities],
        schema=schema,
    )
    role_predicates = []
    for expressions in role_exprs:
        components = [
            expression == membership[column]
            for expression, column in zip(
                expressions,
                membership_columns,
                strict=True,
            )
        ]
        predicate = components[0]
        for component in components[1:]:
            predicate = predicate & component
        role_predicates.append(predicate)
    predicate = role_predicates[0]
    for role_predicate in role_predicates[1:]:
        predicate = predicate | role_predicate
    return table.semi_join(membership, [predicate])


__all__ = [
    "ResolvedSubjectCohort",
    "apply_event_subject_membership",
    "apply_subject_membership",
    "resolve_subject_cohort",
    "validate_metric_cohort_path",
]
