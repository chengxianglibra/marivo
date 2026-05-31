"""Phase 1 base observe planner."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal
from zoneinfo import ZoneInfo

from marivo.analysis.executor.runner import apply_slice_to_dataset, apply_window_to_dataset
from marivo.analysis.intents.observe_errors import (
    RepairAction,
    RepairSafety,
    raise_observe_planning_error,
)
from marivo.analysis.refs import DimensionRef


class JoinSafety(StrEnum):
    MANY_TO_ONE = "many_to_one"
    ONE_TO_ONE = "one_to_one"
    ONE_TO_MANY = "one_to_many"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PlannedDimension:
    field: Any
    column: str


@dataclass(frozen=True)
class PlannedWhere:
    original_key: str
    field: Any
    value: Any
    phase: Literal["root", "joined"]


@dataclass(frozen=True)
class BaseObservePlan:
    root_dataset: str
    additivity: str
    table: Any
    dataset_tables: dict[str, Any]
    dimensions: list[PlannedDimension]
    where: list[PlannedWhere]
    axes_metadata: dict[str, Any]
    lineage_metadata: dict[str, Any]
    warnings: list[dict[str, Any]]
    datasource_name: str


@dataclass(frozen=True)
class ResolvedObserveFields:
    dimensions: list[Any] = field(default_factory=list)
    where_fields: dict[str, Any] = field(default_factory=dict)
    raw_root_where_keys: tuple[str, ...] = ()
    time_field: Any | None = None


def resolve_metric_root(metric_ir: Any) -> str:
    root = getattr(metric_ir, "root_dataset", None)
    if isinstance(root, str) and root:
        return root
    datasets = tuple(getattr(metric_ir, "datasets", ()))
    if len(datasets) == 1:
        return datasets[0]  # type: ignore[no-any-return]
    if not datasets:
        raise_observe_planning_error(
            code="empty-base-datasets",
            message=f"Base metric {metric_ir.semantic_id!r} references no datasets.",
            candidates={},
            repair=[],
        )
    raise_observe_planning_error(
        code="missing-root",
        message=f"Multi-dataset base metric {metric_ir.semantic_id!r} must declare root_dataset.",
        candidates={"datasets": sorted(datasets)},
        repair=[
            RepairAction(
                action="set_metric_root",
                target=metric_ir.semantic_id,
                arg="root_dataset",
                value=datasets[0],
                safety=RepairSafety.MODELING_DECISION,
                why="the root defines preserved rows and the observe time axis",
            )
        ],
    )


def _all_fields(project: Any) -> list[Any]:
    return [*project.list_fields(), *project.list_time_fields()]


def _fields_for_datasets(project: Any, dataset_ids: set[str]) -> list[Any]:
    return [f for f in _all_fields(project) if f.dataset in dataset_ids]


def _resolve_field_ref(
    project: Any,
    ref_id: str,
    *,
    scoped_dataset_ids: set[str],
    allow_qualified_outside_scope: bool,
) -> Any:
    fields = _all_fields(project)
    if "." in ref_id:
        matches = [f for f in fields if f.semantic_id == ref_id]
        if matches and (allow_qualified_outside_scope or matches[0].dataset in scoped_dataset_ids):
            return matches[0]
    else:
        scoped = _fields_for_datasets(project, scoped_dataset_ids)
        matches = [f for f in scoped if f.name == ref_id]
    if not matches:
        raise_observe_planning_error(
            code="field-ref-not-found",
            message=f"Field reference {ref_id!r} was not found in observe plan scope.",
            candidates={"searched_datasets": sorted(scoped_dataset_ids)},
            repair=[],
        )
    if len(matches) > 1:
        raise_observe_planning_error(
            code="field-ref-ambiguous",
            message=f"Field reference {ref_id!r} is ambiguous in observe plan scope.",
            candidates={"fields": sorted(f.semantic_id for f in matches)},
            repair=[],
        )
    return matches[0]


def resolve_observe_fields(
    project: Any,
    metric_ir: Any,
    *,
    dimensions: list[DimensionRef] | None,
    where: dict[Any, Any] | None,
    time_field: str | None,
) -> ResolvedObserveFields:
    root = resolve_metric_root(metric_ir)
    scoped_dataset_ids = {root, *tuple(metric_ir.datasets)}
    resolved_dimensions = [
        _resolve_field_ref(
            project,
            dimension.id,
            scoped_dataset_ids=scoped_dataset_ids,
            allow_qualified_outside_scope=True,
        )
        for dimension in dimensions or []
    ]
    where_fields: dict[str, Any] = {}
    raw_root_where_keys: list[str] = []
    all_fields = _all_fields(project)
    for raw_key in where or {}:
        key = raw_key.id if isinstance(raw_key, DimensionRef) else str(raw_key)
        if "." in key:
            where_fields[key] = _resolve_field_ref(
                project,
                key,
                scoped_dataset_ids=scoped_dataset_ids,
                allow_qualified_outside_scope=True,
            )
            continue
        # Unqualified where key: prefer a semantic field declared on the
        # root dataset; otherwise try non-root datasets in scope; otherwise
        # treat as a root-phase raw key forwarded to apply_slice_to_dataset
        # so the legacy physical-column fallback can resolve it.
        root_match = next(
            (f for f in all_fields if f.dataset == root and f.name == key),
            None,
        )
        if root_match is not None:
            where_fields[key] = root_match
            continue
        non_root_matches = [
            f
            for f in all_fields
            if f.dataset in scoped_dataset_ids and f.dataset != root and f.name == key
        ]
        if len(non_root_matches) == 1:
            where_fields[key] = non_root_matches[0]
            continue
        if len(non_root_matches) > 1:
            raise_observe_planning_error(
                code="field-ref-ambiguous",
                message=f"Field reference {key!r} is ambiguous in observe plan scope.",
                candidates={"fields": sorted(f.semantic_id for f in non_root_matches)},
                repair=[],
            )
        raw_root_where_keys.append(key)
    resolved_time_field = None
    if time_field is not None:
        resolved_time_field = _resolve_field_ref(
            project,
            time_field,
            scoped_dataset_ids={root},
            allow_qualified_outside_scope=False,
        )
        if resolved_time_field.dataset != root:
            raise_observe_planning_error(
                code="non-root-time-field",
                message="observe time_field must belong to the metric root dataset.",
                candidates={"root_dataset": root, "field": resolved_time_field.semantic_id},
                repair=[],
            )
    return ResolvedObserveFields(
        dimensions=resolved_dimensions,
        where_fields=where_fields,
        raw_root_where_keys=tuple(raw_root_where_keys),
        time_field=resolved_time_field,
    )


def _relationship_neighbors(project: Any, dataset_id: str) -> list[tuple[str, Any]]:
    neighbors: list[tuple[str, Any]] = []
    for relationship in project.list_relationships():
        if relationship.from_dataset == dataset_id:
            neighbors.append((relationship.to_dataset, relationship))
        elif relationship.to_dataset == dataset_id:
            neighbors.append((relationship.from_dataset, relationship))
    return neighbors


def unique_shortest_relationship_path(
    project: Any, start_dataset: str, end_dataset: str
) -> list[Any]:
    if start_dataset == end_dataset:
        return []
    queue: list[tuple[str, list[Any]]] = [(start_dataset, [])]
    paths: list[list[Any]] = []
    shortest_len: int | None = None
    while queue:
        current, path = queue.pop(0)
        if shortest_len is not None and len(path) >= shortest_len:
            continue
        for next_dataset, relationship in _relationship_neighbors(project, current):
            if any(relationship.semantic_id == existing.semantic_id for existing in path):
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
            candidates={"paths": [[rel.semantic_id for rel in p] for p in shortest_paths]},
            repair=[],
        )
    return shortest_paths[0]


def _field_names(project: Any, field_ids: tuple[str, ...]) -> tuple[str, ...]:
    fields = {f.semantic_id: f for f in _all_fields(project)}
    return tuple(fields[fid].name for fid in field_ids)


def _effective_key(project: Any, dataset_id: str) -> tuple[str, ...]:
    dataset = project.get_dataset(dataset_id)
    if dataset is None:
        return ()
    versioning = getattr(dataset, "versioning", None)
    if versioning is not None and getattr(versioning, "kind", None) == "snapshot":
        partition_name = versioning.partition_field.rsplit(".", 1)[-1]
        return tuple(key for key in dataset.primary_key if key != partition_name)
    return tuple(dataset.primary_key)


def _anchor_date(resolved_window: Any | None, timezone: str | None) -> date:
    if resolved_window is not None and getattr(resolved_window, "end", None) is not None:
        end = resolved_window.end
        if isinstance(end, datetime):
            return end.astimezone(ZoneInfo(timezone or "UTC")).date()
        if isinstance(end, date):
            return end
        return datetime.fromisoformat(str(end)).date()
    return datetime.now(ZoneInfo(timezone or "UTC")).date()


def _format_snapshot_partition(anchor: date, fmt: str | None) -> Any:
    if fmt is None:
        return anchor
    return anchor.strftime(fmt)


def resolved_edge_safety(project: Any, relationship: Any, *, from_dataset: str) -> JoinSafety:
    if from_dataset == relationship.from_dataset:
        source_fields = relationship.from_fields
        target_dataset = relationship.to_dataset
        target_fields = relationship.to_fields
        source_dataset = relationship.from_dataset
    else:
        source_fields = relationship.to_fields
        target_dataset = relationship.from_dataset
        target_fields = relationship.from_fields
        source_dataset = relationship.to_dataset
    source_is_one = set(_field_names(project, tuple(source_fields))) == set(
        _effective_key(project, source_dataset)
    )
    target_is_one = set(_field_names(project, tuple(target_fields))) == set(
        _effective_key(project, target_dataset)
    )
    if source_is_one and target_is_one:
        return JoinSafety.ONE_TO_ONE
    if target_is_one:
        return JoinSafety.MANY_TO_ONE
    if source_is_one:
        return JoinSafety.ONE_TO_MANY
    return JoinSafety.UNKNOWN


def _field_fn(project: Any, field_id: str) -> Any:
    sidecar = project.sidecar()
    fn = sidecar.get(field_id) if sidecar else None
    if fn is None:
        raise_observe_planning_error(
            code="field-ref-not-found",
            message=f"Field callable {field_id!r} was not found.",
            candidates={"field": field_id},
            repair=[],
        )
    return fn


def _join_table(
    current_table: Any,
    next_table: Any,
    *,
    project: Any,
    relationship: Any,
    current_dataset: str,
) -> tuple[Any, str]:
    if relationship.from_dataset == current_dataset:
        next_dataset = relationship.to_dataset
        left_fields = relationship.from_fields
        right_fields = relationship.to_fields
    else:
        next_dataset = relationship.from_dataset
        left_fields = relationship.to_fields
        right_fields = relationship.from_fields
    predicates = [
        _field_fn(project, left_field)(current_table) == _field_fn(project, right_field)(next_table)
        for left_field, right_field in zip(left_fields, right_fields, strict=True)
    ]
    return current_table.left_join(next_table, predicates), next_dataset


def plan_base_observe(
    *,
    project: Any,
    session: Any,
    metric_ir: Any,
    dataset_irs: dict[str, Any],
    dataset_fns: dict[str, Any],
    dimensions: list[DimensionRef] | None,
    where: dict[Any, Any] | None,
    resolved_window: Any | None,
    time_field: str | None,
) -> BaseObservePlan:
    root = resolve_metric_root(metric_ir)
    if metric_ir.additivity is None:
        raise_observe_planning_error(
            code="missing-additivity",
            message=f"Base metric {metric_ir.semantic_id!r} must declare additivity.",
            candidates={"metric": metric_ir.semantic_id},
            repair=[],
        )
    resolved_fields = resolve_observe_fields(
        project,
        metric_ir,
        dimensions=dimensions,
        where=where,
        time_field=time_field,
    )
    required_datasets = {root, *metric_ir.datasets}
    required_datasets.update(field.dataset for field in resolved_fields.dimensions)
    required_datasets.update(field.dataset for field in resolved_fields.where_fields.values())

    datasource_names = {dataset_irs[dataset_id].datasource_name for dataset_id in required_datasets}
    if len(datasource_names) != 1:
        raise_observe_planning_error(
            code="cross-datasource-plan",
            message="A base observe plan must use one datasource.",
            candidates={"datasources": sorted(datasource_names)},
            repair=[],
        )
    datasource_name = next(iter(datasource_names))
    _, backend = (
        session.backend_cache.get_or_create(datasource_name),
        session.backend_cache.get_or_create(datasource_name),
    )
    root_table = dataset_fns[root](backend)
    root_table = apply_window_to_dataset(
        root_table, resolved_window, dataset_ir=dataset_irs[root], session_tz=session.tz
    )

    planned_where: list[PlannedWhere] = []
    root_where: dict[str, Any] = {}
    joined_where: dict[str, Any] = {}
    raw_root_keys = set(resolved_fields.raw_root_where_keys)
    for raw_key, value in (where or {}).items():
        key = raw_key.id if isinstance(raw_key, DimensionRef) else str(raw_key)
        if key in raw_root_keys:
            # Root-phase raw key: forwarded as-is so apply_slice_to_dataset
            # resolves it via the dataset_ir physical-column fallback.
            root_where[key] = value
            continue
        field = resolved_fields.where_fields[key]
        phase: Literal["root", "joined"] = "root" if field.dataset == root else "joined"
        planned_where.append(PlannedWhere(original_key=key, field=field, value=value, phase=phase))
        if phase == "root":
            root_where[field.name] = value
        else:
            joined_where[field.name] = value
    if root_where:
        root_table = apply_slice_to_dataset(root_table, root_where, dataset_ir=dataset_irs[root])

    widened_table = root_table
    materialized: dict[str, Any] = {root: widened_table}
    edge_metadata: list[dict[str, Any]] = []
    snapshot_metadata: list[dict[str, Any]] = []
    for dataset_id in sorted(required_datasets - {root}):
        current_dataset = root
        for relationship in unique_shortest_relationship_path(project, root, dataset_id):
            safety = resolved_edge_safety(project, relationship, from_dataset=current_dataset)
            if safety == JoinSafety.ONE_TO_MANY:
                raise_observe_planning_error(
                    code="unsafe-fanout",
                    message=f"Traversal through {relationship.semantic_id!r} is one-to-many.",
                    candidates={"relationship": relationship.semantic_id, "safe_root": dataset_id},
                    repair=[],
                )
            if safety == JoinSafety.UNKNOWN:
                raise_observe_planning_error(
                    code="unknown-join-safety",
                    message=f"Join safety for {relationship.semantic_id!r} cannot be derived from dataset keys.",
                    candidates={"relationship": relationship.semantic_id},
                    repair=[],
                )
            next_dataset = (
                relationship.to_dataset
                if relationship.from_dataset == current_dataset
                else relationship.from_dataset
            )
            if next_dataset not in materialized:
                next_table = dataset_fns[next_dataset](backend)
                next_dataset_meta = project.get_dataset(next_dataset)
                versioning = (
                    getattr(next_dataset_meta, "versioning", None)
                    if next_dataset_meta is not None
                    else None
                )
                if versioning is not None and getattr(versioning, "kind", None) == "snapshot":
                    anchor = _anchor_date(resolved_window, versioning.timezone)
                    partition_value = _format_snapshot_partition(anchor, versioning.format)
                    partition_name = versioning.partition_field.rsplit(".", 1)[-1]
                    next_table = apply_slice_to_dataset(
                        next_table,
                        {partition_name: partition_value},
                        dataset_ir=dataset_irs[next_dataset],
                    )
                    snapshot_metadata.append(
                        {
                            "dataset": next_dataset,
                            "mode": "latest",
                            "anchor": str(anchor),
                            "partition_field": partition_name,
                            "resolved_partition": partition_value,
                        }
                    )
                widened_table, current_dataset = _join_table(
                    widened_table,
                    next_table,
                    project=project,
                    relationship=relationship,
                    current_dataset=current_dataset,
                )
                materialized[next_dataset] = widened_table
            else:
                current_dataset = next_dataset
            edge_metadata.append(
                {
                    "relationship": relationship.semantic_id,
                    "from_dataset": current_dataset,
                    "to_dataset": next_dataset,
                    "join_safety": safety.value,
                    "join_type": "left",
                }
            )
    if joined_where:
        widened_table = apply_slice_to_dataset(
            widened_table, joined_where, dataset_ir=dataset_irs[root]
        )

    planned_dimensions = [
        PlannedDimension(field=field, column=field.name) for field in resolved_fields.dimensions
    ]
    for planned_dimension in planned_dimensions:
        widened_table = widened_table.mutate(
            **{
                planned_dimension.column: _field_fn(project, planned_dimension.field.semantic_id)(
                    widened_table
                ).name(planned_dimension.column)
            }
        )
    dataset_tables = dict.fromkeys(metric_ir.datasets, widened_table)
    return BaseObservePlan(
        root_dataset=root,
        additivity=metric_ir.additivity,
        table=widened_table,
        dataset_tables=dataset_tables,
        dimensions=planned_dimensions,
        where=planned_where,
        axes_metadata={
            dimension.column: {"role": "dimension", "column": dimension.column}
            for dimension in planned_dimensions
        },
        lineage_metadata={
            "root_dataset": root,
            "additivity": metric_ir.additivity,
            "relationships": edge_metadata,
            "snapshots": snapshot_metadata,
        },
        warnings=[],
        datasource_name=datasource_name,
    )
