"""Governed subject-axis planning and cohort-entry enrichment for Event funnels."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast
from zoneinfo import ZoneInfo

import ibis
import pandas as pd

from marivo.analysis.errors import InvalidSubjectAxisError, RepairKind
from marivo.analysis.event import _event_repair
from marivo.analysis.executor.runner import execute
from marivo.analysis.frames.event import SubjectAxisBinding
from marivo.analysis.intents._event_funnel import (
    FUNNEL_ADDITIVE_COLUMNS,
    FUNNEL_RATE_COLUMNS,
    _identity_tuple,
)
from marivo.analysis.intents._observe_planner_fields import resolved_edge_safety
from marivo.analysis.intents._observe_planner_joins import _field_fn, _join_table
from marivo.analysis.intents._observe_planner_types import (
    JoinSafety,
    _planned_relationship,
)
from marivo.analysis.session.core import Session
from marivo.refs import (
    DimensionKind,
    EntityKind,
    FieldKind,
    Ref,
    RefPayloadV1,
    SemanticKind,
)
from marivo.refs import ref as ref_factory
from marivo.semantic.catalog import (
    CatalogEntry,
    DimensionDetails,
    DimensionEntry,
    EntityDetails,
    RelationshipDetails,
    SemanticCatalog,
    _normalize_semantic_input,
)
from marivo.semantic.errors import SemanticRuntimeError
from marivo.semantic.ir import SnapshotVersioningIR, ValidityVersioningIR

_RESERVED_FUNNEL_COLUMNS = frozenset(
    {
        "step_key",
        *FUNNEL_ADDITIVE_COLUMNS,
        *FUNNEL_RATE_COLUMNS,
    }
)


@dataclass(frozen=True)
class ResolvedSubjectAxis:
    """One current-catalog axis plus its executable directed relationship path."""

    ref: Ref[DimensionKind]
    owner: Ref[EntityKind]
    path: tuple[RelationshipDetails, ...]
    binding: SubjectAxisBinding
    datasource_name: str


@dataclass(frozen=True)
class SubjectAxisMaterialization:
    """Exactly one axis tuple per journey subject plus bounded query lineage."""

    values: pd.DataFrame
    bindings: tuple[SubjectAxisBinding, ...]
    query_refs: tuple[str, ...]
    lineage: tuple[dict[str, object], ...]


def _axis_error(
    message: str,
    *,
    expected: str,
    received: str,
    location: str,
    repair_kind: RepairKind = "user_choice",
    action: str,
    candidates: tuple[str, ...] = (),
) -> InvalidSubjectAxisError:
    return InvalidSubjectAxisError(
        message=message,
        expected=expected,
        received=received,
        location=location,
        repair=_event_repair(
            kind=repair_kind,
            action=action,
            help_target="events.funnel",
            candidates=candidates,
        ),
    )


def _entity_details(catalog: SemanticCatalog, path: str) -> EntityDetails:
    entry = catalog.require(ref_factory.entity(path))
    if entry is None:
        raise _axis_error(
            f"subject axis Entity {path!r} is not loaded",
            expected="an Entity in the active catalog",
            received=path,
            location="session.events.funnel.axes",
            repair_kind="inspect",
            action="Inspect current catalog Entities and rebuild the journey session.",
        )
    details = entry.details()
    if not isinstance(details, EntityDetails):
        raise AssertionError(f"Entity ref resolved to {type(details).__name__}")
    return details


def _directed_relationship_paths(
    catalog: SemanticCatalog,
    *,
    start: str,
    end: str,
) -> tuple[tuple[RelationshipDetails, ...], ...]:
    if start == end:
        return ((),)
    registry = catalog._require_index().registry
    outgoing: dict[str, list[RelationshipDetails]] = {}
    for relationship_id in sorted(registry.relationships):
        entry = catalog.require(ref_factory.relationship(relationship_id))
        if entry is None:
            continue
        details = entry.details()
        if not isinstance(details, RelationshipDetails):
            continue
        outgoing.setdefault(details.from_entity.path, []).append(details)

    paths: list[tuple[RelationshipDetails, ...]] = []
    queue: deque[tuple[str, tuple[RelationshipDetails, ...], frozenset[str]]] = deque(
        [(start, (), frozenset({start}))]
    )
    while queue:
        current, path, visited = queue.popleft()
        for relationship in outgoing.get(current, ()):
            next_entity = relationship.to_entity.path
            if next_entity in visited:
                continue
            next_path = (*path, relationship)
            if next_entity == end:
                paths.append(next_path)
                if len(paths) > 1:
                    return tuple(paths)
                continue
            queue.append((next_entity, next_path, visited | {next_entity}))
    return tuple(paths)


def _versioning_resolution(
    details: EntityDetails,
) -> str:
    versioning = details.versioning
    if versioning is None:
        return "ordinary"
    if isinstance(versioning, SnapshotVersioningIR):
        return "snapshot"
    if isinstance(versioning, ValidityVersioningIR):
        return "validity"
    kind = getattr(versioning, "kind", None)
    if kind == "changes":
        return "changes"
    raise _axis_error(
        f"axis Entity {details.ref.key!r} has unsupported versioning",
        expected="ordinary, snapshot, changes, or validity Entity versioning",
        received=repr(kind),
        location="session.events.funnel.axes",
        repair_kind="semantic_authoring",
        action="Repair the axis Entity versioning contract before retrying.",
    )


def resolve_subject_axes(
    session: Session,
    *,
    subject_entity: Ref[EntityKind],
    axes: Sequence[object],
    _reserved_columns: frozenset[str] = _RESERVED_FUNNEL_COLUMNS,
) -> tuple[ResolvedSubjectAxis, ...]:
    """Resolve exact Dimension inputs to unique directed to-one subject paths."""

    if type(subject_entity) is not Ref or subject_entity.kind is not SemanticKind.ENTITY:
        raise TypeError("subject_entity must be an exact Entity Ref")
    catalog = session.catalog
    subject_details = _entity_details(catalog, subject_entity.path)
    available = tuple(item.ref.key for item in catalog.dimensions.items[:6])
    resolved: list[ResolvedSubjectAxis] = []
    seen_refs: set[Ref[DimensionKind]] = set()
    seen_columns: set[str] = set()
    for index, value in enumerate(axes):
        location = f"session.events.funnel.axes[{index}]"
        try:
            normalized = _normalize_semantic_input(
                catalog,
                cast("Any", value),
                allowed_kinds=frozenset({SemanticKind.DIMENSION}),
                location=location,
            )
        except SemanticRuntimeError as exc:
            received_ref = value.ref if isinstance(value, CatalogEntry) else value
            raise _axis_error(
                "funnel axes accept only current-catalog Dimension entries or exact refs",
                expected="DimensionEntry | Ref[dimension]",
                received=(received_ref.key if type(received_ref) is Ref else type(value).__name__),
                location=location,
                repair_kind="inspect",
                action="Inspect current catalog Dimensions and choose an exact subject axis.",
                candidates=available,
            ) from exc
        if normalized.kind is not SemanticKind.DIMENSION:
            raise _axis_error(
                "funnel axes do not accept TimeDimensions or other semantic kinds",
                expected="DimensionEntry | Ref[dimension]",
                received=normalized.key,
                location=location,
                action="Choose a categorical Dimension from the current catalog.",
                candidates=available,
            )
        dimension_ref = cast("Ref[DimensionKind]", normalized)
        if dimension_ref in seen_refs:
            raise _axis_error(
                "funnel axes repeat the same Dimension",
                expected="unique Dimension refs in caller-declared order",
                received=dimension_ref.key,
                location=location,
                action="Remove the repeated axis or choose a different Dimension.",
                candidates=available,
            )
        seen_refs.add(dimension_ref)
        entry = catalog.require(dimension_ref)
        if not isinstance(entry, DimensionEntry):
            raise AssertionError(f"Dimension ref resolved to {type(entry).__name__}")
        details = entry.details()
        if not isinstance(details, DimensionDetails):
            raise AssertionError(f"Dimension entry carried {type(details).__name__}")
        output_column = details.name
        if output_column in _reserved_columns or output_column in seen_columns:
            raise _axis_error(
                "funnel axis output column collides with another public column",
                expected="unique Dimension expression names outside the funnel row contract",
                received=output_column,
                location=location,
                action="Choose Dimensions whose resolved output names are unique.",
                candidates=available,
            )
        seen_columns.add(output_column)
        owner = cast("Ref[EntityKind]", details.entity)
        paths = _directed_relationship_paths(
            catalog,
            start=subject_entity.path,
            end=owner.path,
        )
        if not paths:
            raise _axis_error(
                "axis owner is not reachable from the journey subject",
                expected="one directed to-one path from subject Entity to axis Entity",
                received=f"{subject_entity.key} -> {owner.key}",
                location=location,
                repair_kind="semantic_authoring",
                action="Author a governed directed relationship path for this subject axis.",
            )
        if len(paths) != 1:
            candidates = tuple(
                " -> ".join(relationship.ref.key for relationship in path) for path in paths[:6]
            )
            raise _axis_error(
                "axis owner has multiple directed paths from the journey subject",
                expected="one unique directed to-one relationship path",
                received=f"{len(paths)} paths",
                location=location,
                repair_kind="semantic_authoring",
                action="Remove the ambiguous relationship path or choose an unambiguous axis.",
                candidates=candidates,
            )
        path = paths[0]
        current_entity = subject_entity.path
        datasource_name = subject_details.datasource.path
        for relationship in path:
            safety = resolved_edge_safety(
                catalog,
                _planned_relationship(relationship),
                from_entity=current_entity,
            )
            if safety not in {JoinSafety.MANY_TO_ONE, JoinSafety.ONE_TO_ONE}:
                raise _axis_error(
                    "axis relationship path is not to-one from the journey subject",
                    expected="every directed edge is many-to-one or one-to-one",
                    received=f"{relationship.ref.key}: {safety.value}",
                    location=location,
                    repair_kind="semantic_authoring",
                    action="Repair relationship keys or choose a fanout-safe subject axis.",
                )
            current_entity = relationship.to_entity.path
            current_details = _entity_details(catalog, current_entity)
            if current_details.datasource.path != datasource_name:
                raise _axis_error(
                    "axis relationship path crosses datasource execution boundaries",
                    expected=f"all axis Entities on datasource {datasource_name!r}",
                    received=current_details.datasource.key,
                    location=location,
                    repair_kind="semantic_authoring",
                    action="Author an executable same-datasource subject axis path.",
                )
        owner_details = _entity_details(catalog, owner.path)
        versioning_resolution = _versioning_resolution(owner_details)
        resolved.append(
            ResolvedSubjectAxis(
                ref=dimension_ref,
                owner=owner,
                path=path,
                binding=SubjectAxisBinding(
                    dimension_ref=RefPayloadV1.from_ref(dimension_ref),
                    output_column=output_column,
                    relationship_path=tuple(
                        RefPayloadV1.from_ref(cast("Ref[Any]", relationship.ref))
                        for relationship in path
                    ),
                    versioning_resolution=cast("Any", versioning_resolution),
                ),
                datasource_name=datasource_name,
            )
        )
    return tuple(resolved)


def _field_ref(catalog: SemanticCatalog, path: str) -> Ref[FieldKind]:
    dimension = catalog._require_index().registry.dimensions.get(path)
    if dimension is None:
        raise _axis_error(
            f"axis execution field {path!r} is missing",
            expected="a current governed Dimension or TimeDimension",
            received=path,
            location="session.events.funnel.axes",
            repair_kind="inspect",
            action="Inspect the current catalog and rebuild the journey session.",
        )
    if dimension.is_time_dimension:
        return cast("Ref[FieldKind]", ref_factory.time_dimension(path))
    return cast("Ref[FieldKind]", ref_factory.dimension(path))


def _snapshot_partition_expr(
    table: Any,
    *,
    catalog: SemanticCatalog,
    versioning: SnapshotVersioningIR,
) -> Any:
    raw = _field_fn(catalog, versioning.partition_field)(table)
    if versioning.format:
        return raw.cast("string").as_date(versioning.format)
    return raw.cast("date")


def _validity_predicate(
    *,
    current: Any,
    target: Any,
    catalog: SemanticCatalog,
    versioning: ValidityVersioningIR,
    anchor_column: str,
) -> Any:
    anchor = current[anchor_column].cast("timestamp")
    valid_from = _field_fn(catalog, versioning.valid_from)(target).cast("timestamp")
    valid_to_raw = _field_fn(catalog, versioning.valid_to)(target)
    valid_to = valid_to_raw.cast("timestamp")
    open_end = valid_to_raw.isnull()
    for sentinel in versioning.open_end:
        if sentinel is not None:
            open_end = open_end | (valid_to_raw == sentinel)
    upper = valid_to > anchor if versioning.interval == "closed_open" else valid_to >= anchor
    return (valid_from <= anchor) & (open_end | upper)


def _anchor_rows(
    journey_rows: pd.DataFrame,
    *,
    first_step_key: str,
    identity_width: int,
) -> pd.DataFrame:
    first = journey_rows.loc[journey_rows["step_key"] == first_step_key].copy()
    if first.empty and journey_rows.empty:
        return pd.DataFrame(
            columns=(
                *(f"__subject_identity_{index}" for index in range(identity_width)),
                "__cohort_entry_time",
            )
        )
    if first["journey_id"].duplicated(keep=False).any():
        raise ValueError("journey rows must contain one first-step row per journey")
    records: list[dict[str, object]] = []
    for row in first.to_dict("records"):
        identity = _identity_tuple(row["subject_identity"])
        if len(identity) != identity_width:
            raise ValueError("journey subject identity width does not match metadata")
        occurred_at = row["occurred_at"]
        if pd.isna(occurred_at):
            raise ValueError("canonical journey first step must have occurred_at")
        records.append(
            {
                **{f"__subject_identity_{index}": value for index, value in enumerate(identity)},
                "__cohort_entry_time": pd.Timestamp(occurred_at),
            }
        )
    anchors = pd.DataFrame.from_records(records)
    identity_columns = [f"__subject_identity_{index}" for index in range(identity_width)]
    if anchors.duplicated(subset=identity_columns, keep=False).any():
        raise ValueError("subject-axis funnel requires at most one journey per subject")
    return anchors


def _materialize_one_axis(
    session: Session,
    *,
    anchors: pd.DataFrame,
    subject_entity: Ref[EntityKind],
    subject_identity: tuple[str, ...],
    axis: ResolvedSubjectAxis,
) -> tuple[pd.DataFrame, tuple[str, ...], dict[str, object]]:
    catalog = session.catalog
    resolver = catalog._semantic_resolver(connections=session._connection_runtime)
    prepared_anchors = anchors.copy()
    for path_index, relationship in enumerate(axis.path):
        target_details = _entity_details(catalog, relationship.to_entity.path)
        versioning = target_details.versioning
        timezone = ZoneInfo(getattr(versioning, "timezone", None) or "UTC")
        if isinstance(versioning, SnapshotVersioningIR):
            prepared_anchors[f"__axis_anchor_date_{path_index}"] = (
                pd.to_datetime(
                    prepared_anchors["__cohort_entry_time"],
                    utc=True,
                )
                .dt.tz_convert(timezone)
                .dt.date
            )
        elif isinstance(versioning, ValidityVersioningIR):
            prepared_anchors[f"__axis_anchor_time_{path_index}"] = (
                pd.to_datetime(
                    prepared_anchors["__cohort_entry_time"],
                    utc=True,
                )
                .dt.tz_convert(timezone)
                .dt.tz_localize(None)
            )
    anchor_table = ibis.memtable(prepared_anchors)
    subject_table = resolver.entity(subject_entity)
    identity_columns = tuple(
        f"__subject_identity_{index}" for index in range(len(subject_identity))
    )
    predicates = [
        resolver.dimension_on(
            _field_ref(catalog, identity_ref),
            subject_table,
        )
        == anchor_table[column]
        for identity_ref, column in zip(subject_identity, identity_columns, strict=True)
    ]
    current = anchor_table.inner_join(subject_table, predicates)
    current_entity = subject_entity.path
    snapshot_markers: list[str] = []
    validity_markers: list[str] = []
    versioning_records: list[dict[str, object]] = []
    for path_index, relationship in enumerate(axis.path):
        target_entity = relationship.to_entity.path
        target_details = _entity_details(catalog, target_entity)
        target = resolver.entity(cast("Ref[EntityKind]", relationship.to_entity))
        extra_predicates: list[Any] = []
        versioning = target_details.versioning
        marker_name: str | None = None
        if isinstance(versioning, SnapshotVersioningIR):
            marker_name = f"__snapshot_anchor_{path_index}"
            partition = _snapshot_partition_expr(
                target,
                catalog=catalog,
                versioning=versioning,
            )
            extra_predicates.append(partition <= current[f"__axis_anchor_date_{path_index}"])
            target = target.mutate(**{marker_name: partition})
            snapshot_markers.append(marker_name)
            versioning_records.append(
                {
                    "entity": target_entity,
                    "kind": "snapshot",
                    "mode": "as_of_cohort_entry",
                }
            )
        elif isinstance(versioning, ValidityVersioningIR):
            marker_name = f"__validity_present_{path_index}"
            target = target.mutate(**{marker_name: ibis.literal(True)})
            validity_markers.append(marker_name)
            extra_predicates.append(
                _validity_predicate(
                    current=current,
                    target=target,
                    catalog=catalog,
                    versioning=versioning,
                    anchor_column=f"__axis_anchor_time_{path_index}",
                )
            )
            versioning_records.append(
                {
                    "entity": target_entity,
                    "kind": "validity",
                    "mode": "as_of_cohort_entry",
                }
            )
        current, current_entity = _join_table(
            current,
            target,
            catalog=catalog,
            relationship=_planned_relationship(relationship),
            current_entity=current_entity,
            extra_predicates=extra_predicates or None,
            join_type="left",
        )
        if marker_name is not None and marker_name not in current.columns:
            raise AssertionError("snapshot marker was not retained through axis join")

    axis_expression = resolver.dimension_on(
        cast("Ref[FieldKind]", axis.ref),
        current,
    ).name(axis.binding.output_column)
    selected_columns = [
        *(current[column] for column in identity_columns),
        current["__cohort_entry_time"],
        *(current[marker] for marker in snapshot_markers),
        *(current[marker] for marker in validity_markers),
        axis_expression,
    ]
    session._connection_runtime.begin_query_capture()
    try:
        result = execute(
            current.select(*selected_columns),
            datasource_name=axis.datasource_name,
            cache=session._connection_runtime,
            session_id=session.id,
        )
    except BaseException:
        session._connection_runtime.take_captured_queries()
        raise
    captured = session._connection_runtime.take_captured_queries()
    query_refs: tuple[str, ...] = tuple(
        str(query.query_id) for query in captured if getattr(query, "query_id", None) is not None
    )
    values = result.df.copy()
    if snapshot_markers:
        if values[list(snapshot_markers)].isna().any(axis=None):
            raise _axis_error(
                "snapshot axis has no point-in-time row for at least one subject",
                expected="one snapshot row at or before each cohort-entry timestamp",
                received="missing snapshot point-in-time value",
                location=f"session.events.funnel.axes[{axis.ref.key}]",
                repair_kind="inspect",
                action="Inspect snapshot coverage at the journey cohort-entry timestamps.",
            )
        values = values.sort_values(
            [*identity_columns, *snapshot_markers],
            ascending=[True] * len(identity_columns) + [False] * len(snapshot_markers),
            kind="stable",
        ).drop_duplicates(subset=list(identity_columns), keep="first")
    if validity_markers and values[list(validity_markers)].isna().any(axis=None):
        raise _axis_error(
            "validity axis has no point-in-time row for at least one subject",
            expected="one validity interval containing each cohort-entry timestamp",
            received="missing validity point-in-time value",
            location=f"session.events.funnel.axes[{axis.ref.key}]",
            repair_kind="inspect",
            action="Inspect validity coverage at the journey cohort-entry timestamps.",
        )
    if values.duplicated(subset=list(identity_columns), keep=False).any():
        raise _axis_error(
            "subject axis materialization produced multiple values for one subject",
            expected="exactly one axis row per journey subject",
            received="duplicate subject-axis rows",
            location=f"session.events.funnel.axes[{axis.ref.key}]",
            repair_kind="inspect",
            action="Inspect relationship keys and versioning intervals for duplicate matches.",
        )
    expected_count = len(anchors)
    if len(values) != expected_count:
        raise _axis_error(
            "subject axis materialization did not preserve every journey subject",
            expected=f"{expected_count} subject-axis rows",
            received=f"{len(values)} rows",
            location=f"session.events.funnel.axes[{axis.ref.key}]",
            repair_kind="inspect",
            action="Inspect subject keys and temporal coverage for missing axis rows.",
        )
    values["subject_identity"] = values.apply(
        lambda row: tuple(row[column] for column in identity_columns),
        axis=1,
    )
    output = values.loc[:, ["subject_identity", axis.binding.output_column]]
    lineage: dict[str, object] = {
        "dimension_ref": axis.ref.key,
        "relationship_path": tuple(item.ref.key for item in axis.path),
        "anchor": "cohort_entry",
        "versioning": tuple(versioning_records)
        or ({"entity": axis.owner.path, "kind": "ordinary", "mode": "one_row_per_key"},),
        "query_refs": query_refs,
    }
    return output, query_refs, lineage


def materialize_subject_axes(
    session: Session,
    *,
    journey_rows: pd.DataFrame,
    first_step_key: str,
    subject_entity: Ref[EntityKind],
    subject_identity: tuple[str, ...],
    axes: tuple[ResolvedSubjectAxis, ...],
) -> SubjectAxisMaterialization:
    """Materialize one deterministic declared-order axis tuple per subject."""

    anchors = _anchor_rows(
        journey_rows,
        first_step_key=first_step_key,
        identity_width=len(subject_identity),
    )
    if anchors.empty:
        return SubjectAxisMaterialization(
            values=pd.DataFrame(
                columns=(
                    "subject_identity",
                    *(axis.binding.output_column for axis in axes),
                )
            ),
            bindings=tuple(axis.binding for axis in axes),
            query_refs=(),
            lineage=(),
        )
    identity_values = pd.DataFrame(
        {
            "subject_identity": [
                tuple(row[f"__subject_identity_{index}"] for index in range(len(subject_identity)))
                for row in anchors.to_dict("records")
            ]
        }
    )
    query_refs: list[str] = []
    lineage: list[dict[str, object]] = []
    combined = identity_values
    for axis in axes:
        values, axis_query_refs, axis_lineage = _materialize_one_axis(
            session,
            anchors=anchors,
            subject_entity=subject_entity,
            subject_identity=subject_identity,
            axis=axis,
        )
        combined = combined.merge(
            values,
            on="subject_identity",
            how="left",
            validate="one_to_one",
            sort=False,
        )
        query_refs.extend(axis_query_refs)
        lineage.append(axis_lineage)
    return SubjectAxisMaterialization(
        values=combined,
        bindings=tuple(axis.binding for axis in axes),
        query_refs=tuple(query_refs),
        lineage=tuple(lineage),
    )


__all__ = [
    "ResolvedSubjectAxis",
    "SubjectAxisMaterialization",
    "materialize_subject_axes",
    "resolve_subject_axes",
]
