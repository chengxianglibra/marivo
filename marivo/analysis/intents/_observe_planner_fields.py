"""Field resolution and relationship-graph helpers for the observe planner.

Internal to ``marivo.analysis.intents`` — extracted from ``observe_planner``.
"""

from __future__ import annotations

from typing import Any

import ibis

from marivo.analysis._semantic_types import AnalysisDimensionRef
from marivo.analysis.intents._observe_planner_catalog import (
    _details,
    _entity,
    _entity_id,
    _fields_for_datasets,
    _fields_for_entities,
    _fields_for_entity,
    _from_entity_id,
    _input_ref_id,
    _relationship_id,
    _to_entity_id,
    resolve_metric_root,
)
from marivo.analysis.intents._observe_planner_types import (
    FieldDetails,
    JoinSafety,
    RelationshipInfo,
    ResolvedObserveFields,
    _planned_field,
    _planned_relationship,
)
from marivo.analysis.intents.observe_errors import (
    RepairAction,
    RepairSafety,
    raise_observe_planning_error,
)
from marivo.introspection._fuzzy import did_you_mean
from marivo.semantic.catalog import RelationshipDetails, SemanticCatalog, SemanticKind
from marivo.semantic.ir import SnapshotVersioningIR, ValidityVersioningIR

_IBIS_BUILTIN_NAMES = frozenset(
    {
        "desc",
        "asc",
        "greatest",
        "least",
        "ifelse",
        "coalesce",
        "negate",
        "where",
        "nullif",
    }
)


def _all_entity_ids(catalog: SemanticCatalog) -> set[str]:
    return set(catalog._require_index().semantic_ids(SemanticKind.ENTITY))


def _relationship_details_for_entity(
    catalog: SemanticCatalog,
    entity_ref: str,
) -> tuple[RelationshipDetails, ...]:
    details = catalog._require_index().details_under(
        SemanticKind.RELATIONSHIP,
        scope_id=f"entity.{entity_ref}",
    )
    return tuple(item for item in details if isinstance(item, RelationshipDetails))


def _resolve_field_ref(
    catalog: SemanticCatalog,
    ref_id: str,
    *,
    scoped_dataset_ids: set[str],
    allow_qualified_outside_scope: bool,
    allow_unqualified_outside_scope: bool = False,
) -> FieldDetails:
    fields = _fields_for_entities(
        catalog,
        scoped_dataset_ids
        if not allow_qualified_outside_scope and not allow_unqualified_outside_scope
        else _all_entity_ids(catalog),
    )
    if "." in ref_id:
        matches = [f for f in fields if f.ref.id == ref_id]
        if matches and (
            allow_qualified_outside_scope or _entity_id(matches[0]) in scoped_dataset_ids
        ):
            return matches[0]
    else:
        scoped = _fields_for_datasets(catalog, scoped_dataset_ids)
        matches = [f for f in scoped if f.name == ref_id]
        if not matches and allow_unqualified_outside_scope:
            matches = [f for f in fields if f.name == ref_id]
    if not matches:
        all_field_ids = sorted(f.ref.id for f in fields)
        pool = all_field_ids if "." in ref_id else sorted({f.name for f in fields})
        suggestions = did_you_mean(ref_id, pool)
        repair_actions: list[RepairAction] = []
        if suggestions:
            repair_actions.append(
                RepairAction(
                    action="replace_field_ref",
                    target=ref_id,
                    arg="field_ref",
                    value=suggestions[0],
                    safety=RepairSafety.AUTO_SAFE,
                    why=f"closest match for {ref_id!r}",
                )
            )
        message = f"Field reference {ref_id!r} was not found in observe plan scope."
        candidates: dict[str, Any] = {
            "searched_datasets": sorted(scoped_dataset_ids),
            "available_field_ids": all_field_ids,
            "did_you_mean": suggestions,
        }
        if ref_id in _IBIS_BUILTIN_NAMES:
            ibis_hint = (
                f"{ref_id!r} is also an ibis expression function (ibis.{ref_id}()). "
                f"Use bracket notation in the semantic function body when a column shadows an ibis method."
            )
            message = f"{message} {ibis_hint}"
            candidates["ibis_builtin_hint"] = ibis_hint
        raise_observe_planning_error(
            code="field-ref-not-found",
            message=message,
            candidates=candidates,
            repair=repair_actions,
        )
    if len(matches) > 1:
        raise_observe_planning_error(
            code="field-ref-ambiguous",
            message=f"Field reference {ref_id!r} is ambiguous in observe plan scope.",
            candidates={"fields": sorted(f.ref.id for f in matches)},
            repair=[],
        )
    return matches[0]


def resolve_observe_fields(
    catalog: SemanticCatalog,
    metric_ir: Any,
    *,
    dimensions: list[AnalysisDimensionRef] | None,
    where: dict[Any, Any] | None,
    time_dimension: str | None,
    allow_unqualified_outside_scope: bool = False,
) -> ResolvedObserveFields:
    root = resolve_metric_root(metric_ir)
    scoped_dataset_ids = {root, *tuple(metric_ir.entities)}
    resolved_dimensions = [
        _planned_field(
            _resolve_field_ref(
                catalog,
                _input_ref_id(dimension),
                scoped_dataset_ids=scoped_dataset_ids,
                allow_qualified_outside_scope=True,
                allow_unqualified_outside_scope=allow_unqualified_outside_scope,
            )
        )
        for dimension in dimensions or []
    ]
    where_fields: dict[str, Any] = {}
    raw_root_where_keys: list[str] = []
    all_fields = _fields_for_entities(catalog, scoped_dataset_ids)
    for raw_key in where or {}:
        key = _input_ref_id(raw_key)
        if "." in key:
            where_fields[key] = _planned_field(
                _resolve_field_ref(
                    catalog,
                    key,
                    scoped_dataset_ids=scoped_dataset_ids,
                    allow_qualified_outside_scope=True,
                )
            )
            continue
        # Unqualified where key: prefer a semantic field declared on the
        # root entity; otherwise try non-root entities in scope; otherwise
        # treat as a root-phase raw key forwarded to apply_slice_to_dataset
        # so the legacy physical-column fallback can resolve it.
        root_match = next(
            (f for f in all_fields if _entity_id(f) == root and f.name == key),
            None,
        )
        if root_match is not None:
            where_fields[key] = _planned_field(root_match)
            continue
        non_root_matches = [
            f
            for f in all_fields
            if _entity_id(f) in scoped_dataset_ids and _entity_id(f) != root and f.name == key
        ]
        if len(non_root_matches) == 1:
            where_fields[key] = _planned_field(non_root_matches[0])
            continue
        if len(non_root_matches) > 1:
            raise_observe_planning_error(
                code="field-ref-ambiguous",
                message=f"Field reference {key!r} is ambiguous in observe plan scope.",
                candidates={"fields": sorted(f.ref.id for f in non_root_matches)},
                repair=[],
            )
        raw_root_where_keys.append(key)
    resolved_time_dimension = None
    if time_dimension is not None:
        resolved_time_dimension_details = _resolve_field_ref(
            catalog,
            time_dimension,
            scoped_dataset_ids={root},
            allow_qualified_outside_scope=False,
        )
        if _entity_id(resolved_time_dimension_details) != root:
            raise_observe_planning_error(
                code="non-root-time-dimension",
                message="observe time_dimension must belong to the metric root entity.",
                candidates={"root_entity": root, "field": resolved_time_dimension_details.ref.id},
                repair=[],
            )
        resolved_time_dimension = _planned_field(resolved_time_dimension_details)
    return ResolvedObserveFields(
        dimensions=resolved_dimensions,
        where_fields=where_fields,
        raw_root_where_keys=tuple(raw_root_where_keys),
        time_dimension=resolved_time_dimension,
    )


def _relationship_neighbors(
    catalog: SemanticCatalog, dataset_id: str
) -> list[tuple[str, RelationshipInfo]]:
    neighbors: list[tuple[str, RelationshipInfo]] = []
    relationships: list[RelationshipInfo] = []
    for details in _relationship_details_for_entity(catalog, dataset_id):
        relationships.append(_planned_relationship(details))
    for relationship in relationships:
        if _from_entity_id(relationship) == dataset_id:
            neighbors.append((_to_entity_id(relationship), relationship))
        elif _to_entity_id(relationship) == dataset_id:
            neighbors.append((_from_entity_id(relationship), relationship))
    return neighbors


def unique_shortest_relationship_path(
    catalog: SemanticCatalog, start_dataset: str, end_dataset: str
) -> list[RelationshipInfo]:
    if start_dataset == end_dataset:
        return []
    queue: list[tuple[str, list[RelationshipInfo]]] = [(start_dataset, [])]
    paths: list[list[RelationshipInfo]] = []
    shortest_len: int | None = None
    while queue:
        current, path = queue.pop(0)
        if shortest_len is not None and len(path) >= shortest_len:
            continue
        for next_dataset, relationship in _relationship_neighbors(catalog, current):
            if any(
                _relationship_id(relationship) == _relationship_id(existing) for existing in path
            ):
                continue
            next_path = [*path, relationship]
            if next_dataset == end_dataset:
                shortest_len = len(next_path)
                paths.append(next_path)
            else:
                queue.append((next_dataset, next_path))
    if not paths:
        raise_observe_planning_error(
            code="path-missing",
            message=f"No relationship path from {start_dataset!r} to {end_dataset!r}.",
            candidates={"from_dataset": start_dataset, "to_dataset": end_dataset},
            repair=[],
        )
    shortest_paths = [p for p in paths if len(p) == shortest_len]
    if len(shortest_paths) > 1:
        raise_observe_planning_error(
            code="path-ambiguous",
            message=f"Multiple shortest relationship paths from {start_dataset!r} to {end_dataset!r}.",
            candidates={"paths": [[_relationship_id(rel) for rel in p] for p in shortest_paths]},
            repair=[],
        )
    return shortest_paths[0]


def _field_names(catalog: SemanticCatalog, field_ids: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(_details(catalog, fid).name for fid in field_ids)


def _effective_key(catalog: SemanticCatalog, dataset_id: str) -> tuple[str, ...]:
    dataset = _entity(catalog, dataset_id)
    versioning = dataset.versioning
    if isinstance(versioning, SnapshotVersioningIR):
        partition_name = versioning.partition_field.rsplit(".", 1)[-1]
        return tuple(key for key in dataset.primary_key if key != partition_name)
    if isinstance(versioning, ValidityVersioningIR):
        valid_from_local = versioning.valid_from.rsplit(".", 1)[-1]
        valid_to_local = versioning.valid_to.rsplit(".", 1)[-1]
        interval_locals = {valid_from_local, valid_to_local}
        return tuple(key for key in dataset.primary_key if key not in interval_locals)
    return tuple(dataset.primary_key)


def _effective_key_semantic_ids(catalog: SemanticCatalog, dataset_id: str) -> frozenset[str]:
    col_names = set(_effective_key(catalog, dataset_id))
    if not col_names:
        return frozenset()
    all_dataset_fields = _fields_for_entity(catalog, dataset_id)
    by_name = frozenset(f.ref.id for f in all_dataset_fields if f.name in col_names)
    if len(by_name) == len(col_names):
        return by_name
    dataset = _entity(catalog, dataset_id)
    schema = dict.fromkeys(dataset.primary_key or (), "int64")
    if not schema:
        return frozenset()
    try:
        dummy = ibis.table(schema, name=dataset_id.rsplit(".", 1)[-1])
    except Exception:
        return frozenset()
    resolver = catalog._resolver(connections=_NoConnectionService())
    result: set[str] = set()
    for field_detail in all_dataset_fields:
        try:
            expr = resolver.dimension_on(field_detail.ref, dummy)
            out_name = expr.get_name()
        except Exception:
            continue
        if out_name in col_names:
            result.add(field_detail.ref.id)
    return frozenset(result)


class _NoConnectionService:
    def session_backend(self, name: str) -> Any:
        raise RuntimeError(f"planner dummy resolver must not open datasource {name!r}")


def resolved_edge_safety(
    catalog: SemanticCatalog, relationship: RelationshipInfo, *, from_entity: str
) -> JoinSafety:
    if from_entity == _from_entity_id(relationship):
        source_fields = relationship.from_keys
        target_entity = _to_entity_id(relationship)
        target_fields = relationship.to_keys
        source_entity = _from_entity_id(relationship)
    else:
        source_fields = relationship.to_keys
        target_entity = _from_entity_id(relationship)
        target_fields = relationship.from_keys
        source_entity = _to_entity_id(relationship)
    # Compare by field name first (fast path for the common case where field
    # names match primary key column names), then fall back to semantic_id
    # comparison to handle aliased fields (e.g. profile_user_id -> user_id).
    source_field_names = set(_field_names(catalog, tuple(source_fields)))
    target_field_names = set(_field_names(catalog, tuple(target_fields)))
    source_key_names = set(_effective_key(catalog, source_entity))
    target_key_names = set(_effective_key(catalog, target_entity))
    source_is_one = source_field_names == source_key_names
    target_is_one = target_field_names == target_key_names
    if not source_is_one:
        # Try semantic_id comparison
        source_key_sids = _effective_key_semantic_ids(catalog, source_entity)
        source_is_one = frozenset(source_fields) == source_key_sids
    if not target_is_one:
        # Try semantic_id comparison
        target_key_sids = _effective_key_semantic_ids(catalog, target_entity)
        target_is_one = frozenset(target_fields) == target_key_sids
    if source_is_one and target_is_one:
        return JoinSafety.ONE_TO_ONE
    if target_is_one:
        return JoinSafety.MANY_TO_ONE
    if source_is_one:
        return JoinSafety.ONE_TO_MANY
    return JoinSafety.UNKNOWN
