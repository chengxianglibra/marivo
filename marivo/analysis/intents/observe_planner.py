"""Phase 1 base observe planner."""

from __future__ import annotations

import hashlib
import json
import operator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum
from functools import reduce
from typing import Any, Literal
from zoneinfo import ZoneInfo

import ibis
import ibis.expr.types as ir_types

from marivo.analysis.errors import WindowInvalidError
from marivo.analysis.executor.runner import apply_slice_to_dataset, apply_window_to_dataset, execute
from marivo.analysis.intents.observe_errors import (
    ObservePlanningError,
    RepairAction,
    RepairSafety,
    raise_observe_planning_error,
)
from marivo.analysis.refs import DimensionRef
from marivo.analysis.windows.spec import is_date_only
from marivo.introspection._fuzzy import did_you_mean
from marivo.semantic.ir import SnapshotVersioningIR, ValidityVersioningIR


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
    root_entity: str
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
class ComponentPlan:
    component_metric_ir: Any
    role: str
    base_plan: BaseObservePlan


@dataclass(frozen=True)
class DerivedObservePlan:
    metric_ir: Any
    component_plans: list[ComponentPlan]
    parent_axes: dict[str, Any]
    lineage_metadata: dict[str, Any]
    warnings: list[dict[str, Any]] = field(default_factory=list)


ObservePlan = BaseObservePlan | DerivedObservePlan


@dataclass(frozen=True)
class ResolvedObserveFields:
    dimensions: list[Any] = field(default_factory=list)
    where_fields: dict[str, Any] = field(default_factory=dict)
    raw_root_where_keys: tuple[str, ...] = ()
    time_dimension: Any | None = None


def resolve_metric_root(metric_ir: Any) -> str:
    root = getattr(metric_ir, "root_entity", None)
    if isinstance(root, str) and root:
        return root
    entities = tuple(getattr(metric_ir, "entities", ()))
    if len(entities) == 1:
        return entities[0]  # type: ignore[no-any-return]
    if not entities:
        raise_observe_planning_error(
            code="empty-base-entities",
            message=f"Base metric {metric_ir.semantic_id!r} references no entities.",
            candidates={},
            repair=[],
        )
    raise_observe_planning_error(
        code="missing-root",
        message=f"Multi-entity base metric {metric_ir.semantic_id!r} must declare root_entity.",
        candidates={"entities": sorted(entities)},
        repair=[
            RepairAction(
                action="set_metric_root",
                target=metric_ir.semantic_id,
                arg="root_entity",
                value=entities[0],
                safety=RepairSafety.MODELING_DECISION,
                why="the root defines preserved rows and the observe time axis",
            )
        ],
    )


def _all_fields(project: Any) -> list[Any]:
    return [*project.list_dimensions(), *project.list_time_dimensions()]


def _fields_for_datasets(project: Any, dataset_ids: set[str]) -> list[Any]:
    return [f for f in _all_fields(project) if f.entity in dataset_ids]


def _resolve_field_ref(
    project: Any,
    ref_id: str,
    *,
    scoped_dataset_ids: set[str],
    allow_qualified_outside_scope: bool,
    allow_unqualified_outside_scope: bool = False,
) -> Any:
    fields = _all_fields(project)
    if "." in ref_id:
        matches = [f for f in fields if f.semantic_id == ref_id]
        if matches and (allow_qualified_outside_scope or matches[0].entity in scoped_dataset_ids):
            return matches[0]
    else:
        scoped = _fields_for_datasets(project, scoped_dataset_ids)
        matches = [f for f in scoped if f.name == ref_id]
        if not matches and allow_unqualified_outside_scope:
            # Fall back to all project fields so that dimensions on related
            # datasets (reachable via relationships) can be resolved.
            matches = [f for f in fields if f.name == ref_id]
    if not matches:
        all_field_ids = sorted(f.semantic_id for f in fields)
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
        raise_observe_planning_error(
            code="field-ref-not-found",
            message=f"Field reference {ref_id!r} was not found in observe plan scope.",
            candidates={
                "searched_datasets": sorted(scoped_dataset_ids),
                "available_field_ids": all_field_ids,
                "did_you_mean": suggestions,
            },
            repair=repair_actions,
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
    time_dimension: str | None,
    allow_unqualified_outside_scope: bool = False,
) -> ResolvedObserveFields:
    root = resolve_metric_root(metric_ir)
    scoped_dataset_ids = {root, *tuple(metric_ir.entities)}
    resolved_dimensions = [
        _resolve_field_ref(
            project,
            dimension.semantic_id,
            scoped_dataset_ids=scoped_dataset_ids,
            allow_qualified_outside_scope=True,
            allow_unqualified_outside_scope=allow_unqualified_outside_scope,
        )
        for dimension in dimensions or []
    ]
    where_fields: dict[str, Any] = {}
    raw_root_where_keys: list[str] = []
    all_fields = _all_fields(project)
    for raw_key in where or {}:
        key = raw_key.semantic_id if isinstance(raw_key, DimensionRef) else str(raw_key)
        if "." in key:
            where_fields[key] = _resolve_field_ref(
                project,
                key,
                scoped_dataset_ids=scoped_dataset_ids,
                allow_qualified_outside_scope=True,
            )
            continue
        # Unqualified where key: prefer a semantic field declared on the
        # root entity; otherwise try non-root entities in scope; otherwise
        # treat as a root-phase raw key forwarded to apply_slice_to_dataset
        # so the legacy physical-column fallback can resolve it.
        root_match = next(
            (f for f in all_fields if f.entity == root and f.name == key),
            None,
        )
        if root_match is not None:
            where_fields[key] = root_match
            continue
        non_root_matches = [
            f
            for f in all_fields
            if f.entity in scoped_dataset_ids and f.entity != root and f.name == key
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
    resolved_time_dimension = None
    if time_dimension is not None:
        resolved_time_dimension = _resolve_field_ref(
            project,
            time_dimension,
            scoped_dataset_ids={root},
            allow_qualified_outside_scope=False,
        )
        if resolved_time_dimension.entity != root:
            raise_observe_planning_error(
                code="non-root-time-dimension",
                message="observe time_dimension must belong to the metric root entity.",
                candidates={"root_entity": root, "field": resolved_time_dimension.semantic_id},
                repair=[],
            )
    return ResolvedObserveFields(
        dimensions=resolved_dimensions,
        where_fields=where_fields,
        raw_root_where_keys=tuple(raw_root_where_keys),
        time_dimension=resolved_time_dimension,
    )


def _relationship_neighbors(project: Any, dataset_id: str) -> list[tuple[str, Any]]:
    neighbors: list[tuple[str, Any]] = []
    for relationship in project.list_relationships():
        if relationship.from_entity == dataset_id:
            neighbors.append((relationship.to_entity, relationship))
        elif relationship.to_entity == dataset_id:
            neighbors.append((relationship.from_entity, relationship))
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
    dataset = project.get_entity(dataset_id)
    if dataset is None:
        return ()
    versioning = getattr(dataset, "versioning", None)
    if isinstance(versioning, SnapshotVersioningIR):
        partition_name = versioning.partition_field.rsplit(".", 1)[-1]
        return tuple(key for key in dataset.primary_key if key != partition_name)
    if isinstance(versioning, ValidityVersioningIR):
        valid_from_local = versioning.valid_from.rsplit(".", 1)[-1]
        valid_to_local = versioning.valid_to.rsplit(".", 1)[-1]
        interval_locals = {valid_from_local, valid_to_local}
        return tuple(key for key in dataset.primary_key if key not in interval_locals)
    return tuple(dataset.primary_key)


def _effective_key_semantic_ids(project: Any, dataset_id: str) -> frozenset[str]:
    """Return the semantic_ids of the fields that form the effective primary key.

    This is used by resolved_edge_safety to compare against relationship field
    semantic_ids, handling the case where a field's name differs from the
    physical column name it maps to.

    Two strategies are tried in order:
    1. Name match: field.name == physical key column name (fast path).
    2. Expression match: call the field function on a dummy ibis table built
       from the primary key columns and check the output column name.  This
       handles aliased fields (e.g. profile_user_id -> user_id).
    """
    col_names = set(_effective_key(project, dataset_id))
    if not col_names:
        return frozenset()
    all_dataset_fields = [f for f in _all_fields(project) if f.entity == dataset_id]
    # Strategy 1: name match
    by_name = frozenset(f.semantic_id for f in all_dataset_fields if f.name in col_names)
    if len(by_name) == len(col_names):
        return by_name
    # Strategy 2: expression match via sidecar
    sidecar = project._sidecar
    if sidecar is None:
        return frozenset()
    # Build a dummy ibis table with the primary key columns so we can call
    # each field function and inspect the output column name.
    dataset = project.get_entity(dataset_id)
    if dataset is None:
        return frozenset()
    schema = dict.fromkeys(dataset.primary_key or [], "int64")
    if not schema:
        return frozenset()
    try:
        dummy = ibis.table(schema, name=dataset_id.rsplit(".", 1)[-1])
    except Exception:
        return frozenset()
    result: set[str] = set()
    for field_ir in all_dataset_fields:
        fn = sidecar.get(field_ir.semantic_id)
        if fn is None:
            continue
        try:
            expr = fn(dummy)
            out_name = expr.get_name()
        except Exception:
            continue
        if out_name in col_names:
            result.add(field_ir.semantic_id)
    return frozenset(result)


def _anchor_date(resolved_window: Any | None, timezone: str | None) -> date:
    if resolved_window is not None and getattr(resolved_window, "end", None) is not None:
        end = resolved_window.end
        if isinstance(end, datetime):
            return end.astimezone(ZoneInfo(timezone or "UTC")).date()
        if isinstance(end, date):
            return end
        end_str = str(end)
        anchor = datetime.fromisoformat(end_str).date()
        if is_date_only(end_str):
            anchor -= timedelta(days=1)
        return anchor
    return datetime.now(ZoneInfo(timezone or "UTC")).date()


def _utc_now() -> datetime:
    """Indirection so tests can monkeypatch plan-time anchor."""
    return datetime.now(tz=ZoneInfo("UTC"))


def _resolved_target_timezone(target_versioning: Any) -> str:
    return getattr(target_versioning, "timezone", None) or "UTC"


def _derive_version_mode(
    *,
    root_time_dimension: Any | None,
    target_versioning: Any,
    resolved_window: Any | None,
) -> tuple[
    Literal["latest", "as_of_root_time"],
    Literal["timescope_end", "as_of_current_time", "root"],
    date | None,
]:
    qualifying = root_time_dimension is not None and getattr(
        root_time_dimension, "data_type", None
    ) in {
        "date",
        "timestamp",
    }
    if qualifying:
        return ("as_of_root_time", "root", None)
    target_tz = ZoneInfo(_resolved_target_timezone(target_versioning))
    if resolved_window is not None and getattr(resolved_window, "end", None) is not None:
        end = resolved_window.end
        if isinstance(end, datetime):
            anchor = end.astimezone(target_tz).date()
        elif isinstance(end, date):
            anchor = end
        else:
            end_str = str(end)
            anchor = datetime.fromisoformat(end_str).date()
            if is_date_only(end_str):
                anchor -= timedelta(days=1)
        return ("latest", "timescope_end", anchor)
    return ("latest", "as_of_current_time", _utc_now().astimezone(target_tz).date())


def _format_snapshot_partition(anchor: date, fmt: str | None) -> Any:
    if fmt is None:
        return anchor
    return anchor.strftime(fmt)


def _root_time_dimension(
    project: Any, root_entity_id: str, *, explicit_time_dimension: Any | None
) -> Any | None:
    if explicit_time_dimension is not None:
        return explicit_time_dimension
    candidates = [tf for tf in project.list_time_dimensions() if tf.entity == root_entity_id]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    defaults = [tf for tf in candidates if getattr(tf, "is_default", False)]
    if len(defaults) == 1:
        return defaults[0]
    return candidates[0]


def _parse_partition_value(raw: Any, *, fmt: str | None) -> date:
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    # Handle pandas Timestamp (has .date() method but is not a datetime subclass)
    if hasattr(raw, "date") and callable(raw.date):
        result = raw.date()
        if isinstance(result, date):
            return result
        return datetime.fromisoformat(str(result)).date()
    if fmt is not None:
        return datetime.strptime(str(raw), fmt).date()
    return datetime.fromisoformat(str(raw)).date()


def _discover_anchor_dates(
    *,
    root_table: Any,
    time_field_expr: Any,
    datasource_name: str,
    session: Any,
) -> list[date]:
    expr = time_field_expr.cast("timestamp").cast("date").name("anchor_date")
    df = execute(
        root_table.select(expr).distinct(),
        datasource_name=datasource_name,
        cache=session.backend_cache,
        session_id=session.id,
    ).df
    result: list[date] = []
    for raw in df["anchor_date"].tolist():
        if raw is None:
            continue
        if isinstance(raw, datetime):
            result.append(raw.date())
        elif isinstance(raw, date):
            result.append(raw)
        else:
            # pandas Timestamp or similar
            result.append(_parse_partition_value(raw, fmt=None))
    return sorted(set(result))


def _discover_available_partitions(
    *,
    snapshot_table: Any,
    partition_field_local: str,
    fmt: str | None,
    datasource_name: str,
    session: Any,
) -> list[date]:
    df = execute(
        snapshot_table.select(snapshot_table[partition_field_local].name("p")).distinct(),
        datasource_name=datasource_name,
        cache=session.backend_cache,
        session_id=session.id,
    ).df
    return sorted({_parse_partition_value(p, fmt=fmt) for p in df["p"].tolist() if p is not None})


def _build_anchor_partition_mapping(
    anchor_dates: list[date],
    available_partitions: list[date],
    *,
    snapshot_dataset_id: str,
) -> dict[date, date]:
    if not available_partitions:
        raise_observe_planning_error(
            code="snapshot-partition-missing",
            message=f"Snapshot dataset {snapshot_dataset_id!r} has no available partitions.",
            candidates={
                "dataset": snapshot_dataset_id,
                "missing_anchors": [str(a) for a in anchor_dates],
                "min_available_partition": None,
                "max_available_partition": None,
            },
            repair=[],
        )
    sorted_partitions = sorted(available_partitions)
    mapping: dict[date, date] = {}
    missing: list[date] = []
    for anchor in anchor_dates:
        eligible = [p for p in sorted_partitions if p <= anchor]
        if not eligible:
            missing.append(anchor)
            continue
        mapping[anchor] = eligible[-1]
    if missing:
        raise_observe_planning_error(
            code="snapshot-partition-missing",
            message=(
                f"No partition <= anchor exists for snapshot {snapshot_dataset_id!r}: "
                f"missing {len(missing)} anchor(s)."
            ),
            candidates={
                "dataset": snapshot_dataset_id,
                "missing_anchors": [str(a) for a in missing],
                "min_available_partition": str(sorted_partitions[0]),
                "max_available_partition": str(sorted_partitions[-1]),
            },
            repair=[],
        )
    return mapping


def _mapping_digest(mapping: dict[date, date]) -> str:
    payload = json.dumps(
        sorted([(str(k), str(v)) for k, v in mapping.items()]),
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


def resolved_edge_safety(project: Any, relationship: Any, *, from_entity: str) -> JoinSafety:
    if from_entity == relationship.from_entity:
        source_fields = relationship.from_dimensions
        target_entity = relationship.to_entity
        target_fields = relationship.to_dimensions
        source_entity = relationship.from_entity
    else:
        source_fields = relationship.to_dimensions
        target_entity = relationship.from_entity
        target_fields = relationship.from_dimensions
        source_entity = relationship.to_entity
    # Compare by field name first (fast path for the common case where field
    # names match primary key column names), then fall back to semantic_id
    # comparison to handle aliased fields (e.g. profile_user_id -> user_id).
    source_field_names = set(_field_names(project, tuple(source_fields)))
    target_field_names = set(_field_names(project, tuple(target_fields)))
    source_key_names = set(_effective_key(project, source_entity))
    target_key_names = set(_effective_key(project, target_entity))
    source_is_one = source_field_names == source_key_names
    target_is_one = target_field_names == target_key_names
    if not source_is_one:
        # Try semantic_id comparison
        source_key_sids = _effective_key_semantic_ids(project, source_entity)
        source_is_one = frozenset(source_fields) == source_key_sids
    if not target_is_one:
        # Try semantic_id comparison
        target_key_sids = _effective_key_semantic_ids(project, target_entity)
        target_is_one = frozenset(target_fields) == target_key_sids
    if source_is_one and target_is_one:
        return JoinSafety.ONE_TO_ONE
    if target_is_one:
        return JoinSafety.MANY_TO_ONE
    if source_is_one:
        return JoinSafety.ONE_TO_MANY
    return JoinSafety.UNKNOWN


def _field_fn(project: Any, field_id: str) -> Any:
    sidecar = project._sidecar
    fn = sidecar.get(field_id) if sidecar else None
    if fn is None:
        sidecar_keys = sorted(sidecar.keys()) if sidecar else []
        suggestions = did_you_mean(field_id, sidecar_keys)
        repair_actions: list[RepairAction] = []
        if suggestions:
            repair_actions.append(
                RepairAction(
                    action="replace_field_ref",
                    target=field_id,
                    arg="field_ref",
                    value=suggestions[0],
                    safety=RepairSafety.AUTO_SAFE,
                    why=f"closest match for {field_id!r}",
                )
            )
        raise_observe_planning_error(
            code="field-ref-not-found",
            message=f"Field callable {field_id!r} was not found.",
            candidates={
                "field": field_id,
                "available_field_ids": sidecar_keys,
                "did_you_mean": suggestions,
            },
            repair=repair_actions,
        )
    return fn


def _validate_field_expr(value: Any, *, field_id: str) -> Any:
    """Validate that a sidecar callable returned an ibis expression, not a method/function."""
    if isinstance(value, (ir_types.Value, ir_types.Table)):
        return value
    col_name = field_id.rsplit(".", 1)[-1]
    actual_type = type(value).__name__
    raise_observe_planning_error(
        code="field-expr-type-error",
        message=(
            f"Field callable for {field_id!r} returned {actual_type!r} "
            f"instead of an ibis expression. This usually happens when a "
            f"dimension name shadows an ibis Table method (e.g., 'schema', "
            f"'count', 'select'). Use bracket notation in the function body: "
            f'table["{col_name}"] instead of table.{col_name}.'
        ),
        candidates={"field_id": field_id, "actual_type": actual_type},
        repair=[],
    )


def _join_table(
    current_table: Any,
    next_table: Any,
    *,
    project: Any,
    relationship: Any,
    current_entity: str,
    extra_predicates: list[Any] | None = None,
) -> tuple[Any, str]:
    if relationship.from_entity == current_entity:
        next_entity = relationship.to_entity
        left_fields = relationship.from_dimensions
        right_fields = relationship.to_dimensions
    else:
        next_entity = relationship.from_entity
        left_fields = relationship.to_dimensions
        right_fields = relationship.from_dimensions
    predicates = [
        _field_fn(project, left_field)(current_table) == _field_fn(project, right_field)(next_table)
        for left_field, right_field in zip(left_fields, right_fields, strict=True)
    ]
    if extra_predicates:
        predicates.extend(extra_predicates)
    return current_table.left_join(next_table, predicates), next_entity


def _resolve_snapshot_as_of_root_time(
    *,
    project: Any,
    session: Any,
    datasource_name: str,
    snapshot_dataset_id: str,
    snapshot_versioning: Any,
    snapshot_table: Any,
    root_table: Any,
    root_time_dimension: Any | None,
    anchor_source: str,
) -> tuple[Any, dict[str, Any], dict[date, date]]:
    if root_time_dimension is None:
        raise_observe_planning_error(
            code="unsupported-as-of-root-time",
            message=(
                f"Snapshot {snapshot_dataset_id!r} as_of_root_time requires a "
                "day-level root time field."
            ),
            candidates={"snapshot_dataset": snapshot_dataset_id},
            repair=[],
        )
    target_tz = _resolved_target_timezone(snapshot_versioning)
    time_field_fn = _field_fn(project, root_time_dimension.semantic_id)
    time_field_expr = time_field_fn(root_table)
    anchor_dates = _discover_anchor_dates(
        root_table=root_table,
        time_field_expr=time_field_expr,
        datasource_name=datasource_name,
        session=session,
    )
    partition_local = snapshot_versioning.partition_field.rsplit(".", 1)[-1]
    available = _discover_available_partitions(
        snapshot_table=snapshot_table,
        partition_field_local=partition_local,
        fmt=snapshot_versioning.format,
        datasource_name=datasource_name,
        session=session,
    )
    mapping = _build_anchor_partition_mapping(
        anchor_dates, available, snapshot_dataset_id=snapshot_dataset_id
    )
    encoded = {
        anchor: _format_snapshot_partition(part, snapshot_versioning.format)
        for anchor, part in mapping.items()
    }
    schema: dict[str, str] = {
        "anchor_date": "date",
        "partition_value": "string" if snapshot_versioning.format else "date",
    }
    mapping_table = ibis.memtable(
        [{"anchor_date": a, "partition_value": p} for a, p in encoded.items()],
        schema=schema,
    )
    digest = _mapping_digest(mapping)
    annotated_snapshot = snapshot_table.inner_join(
        mapping_table,
        snapshot_table[partition_local] == mapping_table.partition_value,
    ).drop("partition_value")
    meta: dict[str, Any] = {
        "dataset": snapshot_dataset_id,
        "kind": "snapshot",
        "mode": "as_of_root_time",
        "anchor_source": anchor_source,
        "anchor_value": None,
        "resolved_partition": None,
        "resolved_partition_summary": {
            "anchor_count": len(anchor_dates),
            "min_anchor": str(min(anchor_dates)) if anchor_dates else None,
            "max_anchor": str(max(anchor_dates)) if anchor_dates else None,
            "partition_count": len(set(mapping.values())),
        },
        "anchor_to_partition_mapping_digest": digest,
        "resolved_interval_predicate": None,
        "timezone": target_tz,
    }
    return annotated_snapshot, meta, mapping


def _resolve_snapshot_versioning(
    *,
    project: Any,
    session: Any,
    datasource_name: str,
    snapshot_dataset_id: str,
    snapshot_versioning: Any,
    snapshot_table: Any,
    snapshot_dataset_ir: Any,
    root_table: Any,
    root_time_dimension: Any,
    resolved_window: Any,
) -> tuple[Any, dict[str, Any], dict[date, date] | None]:
    mode, anchor_source, anchor_value = _derive_version_mode(
        root_time_dimension=root_time_dimension,
        target_versioning=snapshot_versioning,
        resolved_window=resolved_window,
    )
    partition_local = snapshot_versioning.partition_field.rsplit(".", 1)[-1]
    target_tz = _resolved_target_timezone(snapshot_versioning)
    if mode == "latest":
        assert anchor_value is not None, "latest mode always provides an anchor_value"
        partition_value = _format_snapshot_partition(anchor_value, snapshot_versioning.format)
        next_table = apply_slice_to_dataset(
            snapshot_table,
            {partition_local: partition_value},
            dataset_ir=snapshot_dataset_ir,
        )
        meta: dict[str, Any] = {
            "dataset": snapshot_dataset_id,
            "kind": "snapshot",
            "mode": "latest",
            "anchor_source": anchor_source,
            "anchor_value": str(anchor_value),
            "resolved_partition": partition_value,
            "resolved_partition_summary": None,
            "anchor_to_partition_mapping_digest": None,
            "resolved_interval_predicate": None,
            "timezone": target_tz,
        }
        return next_table, meta, None
    return _resolve_snapshot_as_of_root_time(
        project=project,
        session=session,
        datasource_name=datasource_name,
        snapshot_dataset_id=snapshot_dataset_id,
        snapshot_versioning=snapshot_versioning,
        snapshot_table=snapshot_table,
        root_table=root_table,
        root_time_dimension=root_time_dimension,
        anchor_source=anchor_source,
    )


def _validity_open_end_predicate(table: Any, versioning: ValidityVersioningIR) -> Any:
    """Boolean predicate that selects validity rows currently open (matching any open_end sentinel)."""
    valid_to_local = versioning.valid_to.rsplit(".", 1)[-1]
    column = table[valid_to_local]
    parts: list[Any] = []
    for sentinel in versioning.open_end:
        if sentinel is None:
            parts.append(column.isnull())
        else:
            parts.append(column == sentinel)
    # defense-in-depth: empty open_end is rejected by validity() author-time but reduce() needs an initial value
    return reduce(operator.or_, parts, ibis.literal(False))


def _resolve_validity_as_of_predicate(
    *,
    project: Any,
    current_table: Any,
    root_time_dimension: Any | None,
    validity_table: Any,
    validity_versioning: ValidityVersioningIR,
    validity_dataset_id: str,
) -> Any:
    """Return a per-row boolean predicate for as_of_root_time validity joins.

    The predicate checks that the root row's time field falls within the
    validity interval.  Key equalities are handled separately by _join_table.
    """
    # Defense-in-depth: _derive_version_mode only picks as_of_root_time when root_time_dimension is qualifying. This guard is unreachable on the current call path.
    if root_time_dimension is None:
        raise_observe_planning_error(
            code="unsupported-as-of-root-time",
            message=(
                f"Validity {validity_dataset_id!r} as_of_root_time requires a "
                "day-level root time field."
            ),
            candidates={"validity_dataset": validity_dataset_id},
            repair=[],
        )
    valid_from_local = validity_versioning.valid_from.rsplit(".", 1)[-1]
    valid_to_local = validity_versioning.valid_to.rsplit(".", 1)[-1]
    anchor = _field_fn(project, root_time_dimension.semantic_id)(current_table).cast("date")
    valid_from = validity_table[valid_from_local]
    valid_to_raw = validity_table[valid_to_local]
    open_end = _validity_open_end_predicate(validity_table, validity_versioning)
    if validity_versioning.interval == "closed_open":
        upper = open_end | (valid_to_raw > anchor)
    else:
        upper = open_end | (valid_to_raw >= anchor)
    lower = valid_from <= anchor
    return lower & upper


def _resolve_validity_versioning(
    *,
    root_table: Any,  # root_table is used in the as_of_root_time branch; the latest branch ignores it
    root_time_dimension: Any | None,
    validity_table: Any,
    validity_versioning: ValidityVersioningIR,
    validity_dataset_id: str,
    resolved_window: Any | None,
) -> tuple[Any, dict[str, Any], bool]:
    """Resolve validity versioning for the join.

    Returns (table, version_meta, is_as_of) where is_as_of is True only when the as_of_root_time branch ran.
    The latest branch filters by `_validity_open_end_predicate`; the as_of branch returns the
    unfiltered table for caller-side interval-predicate composition.
    """
    mode, anchor_source, anchor_value = _derive_version_mode(
        root_time_dimension=root_time_dimension,
        target_versioning=validity_versioning,
        resolved_window=resolved_window,
    )
    target_tz = _resolved_target_timezone(validity_versioning)
    if mode == "latest":
        next_table = validity_table.filter(
            _validity_open_end_predicate(validity_table, validity_versioning)
        )
        meta: dict[str, Any] = {
            "dataset": validity_dataset_id,
            "kind": "validity",
            "mode": "latest",
            "anchor_source": anchor_source,
            "anchor_value": str(anchor_value) if anchor_value else None,
            "resolved_partition": None,
            "resolved_partition_summary": None,
            "anchor_to_partition_mapping_digest": None,
            "resolved_interval_predicate": "open_end_only",
            "timezone": target_tz,
        }
        return next_table, meta, False
    # as_of_root_time: return unfiltered table; caller appends the interval predicate
    meta = {
        "dataset": validity_dataset_id,
        "kind": "validity",
        "mode": "as_of_root_time",
        "anchor_source": anchor_source,
        "anchor_value": None,
        "resolved_partition": None,
        "resolved_partition_summary": None,
        "anchor_to_partition_mapping_digest": None,
        "resolved_interval_predicate": validity_versioning.interval,
        "timezone": target_tz,
    }
    return validity_table, meta, True


def _aggregate_then_join_pre_aggregate(
    *,
    project: Any,
    metric_ir: Any,
    unsafe_dataset_id: str,
    relationship: Any,
    from_dataset: str,
    dataset_fns: dict[str, Any],
    backend: Any,
    resolved_fields: ResolvedObserveFields,
) -> tuple[Any, dict[str, Any]]:
    """Reduce the unsafe-side dataset to the merge grain before joining.

    Merge grain = (join key on unsafe side) ∪ (requested non-root dimensions
    targeting unsafe_dataset_id) ∪ (where fields targeting unsafe_dataset_id).
    Each grain entry projects through ``_field_fn`` so the resulting table keeps
    the physical column names that downstream field bodies expect.
    """
    if relationship.from_entity == unsafe_dataset_id:
        join_field_ids: tuple[str, ...] = tuple(relationship.from_dimensions)
    else:
        join_field_ids = tuple(relationship.to_dimensions)

    grain_field_ids: list[str] = []
    seen_ids: set[str] = set()
    for fid in join_field_ids:
        if fid not in seen_ids:
            grain_field_ids.append(fid)
            seen_ids.add(fid)
    other_fields = [f for f in resolved_fields.dimensions if f.entity == unsafe_dataset_id] + [
        f for f in resolved_fields.where_fields.values() if f.entity == unsafe_dataset_id
    ]
    for f in other_fields:
        if f.semantic_id not in seen_ids:
            grain_field_ids.append(f.semantic_id)
            seen_ids.add(f.semantic_id)

    table = dataset_fns[unsafe_dataset_id](backend)
    projections: list[Any] = []
    grain_meta_entries: list[dict[str, Any]] = []
    join_field_id_set = set(join_field_ids)
    seen_columns: set[str] = set()
    for fid in grain_field_ids:
        expr = _field_fn(project, fid)(table)
        column_name = expr.get_name()
        if column_name in seen_columns:
            continue
        seen_columns.add(column_name)
        projections.append(expr)
        grain_meta_entries.append({"name": column_name, "from_join_key": fid in join_field_id_set})
    pre_aggregated = table.select(*projections).distinct()

    merge_grain_meta = {
        "policy": "aggregate_then_join",
        "unsafe_dataset": unsafe_dataset_id,
        "relationship": relationship.semantic_id,
        "from_dataset": from_dataset,
        "merge_grain": grain_meta_entries,
    }
    return pre_aggregated, merge_grain_meta


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
    time_dimension: str | None,
    allow_unqualified_outside_scope: bool = False,
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
        time_dimension=time_dimension,
        allow_unqualified_outside_scope=allow_unqualified_outside_scope,
    )
    root_time_dimension = _root_time_dimension(
        project, root, explicit_time_dimension=resolved_fields.time_dimension
    )
    required_datasets = {root, *metric_ir.entities}
    required_datasets.update(field.entity for field in resolved_fields.dimensions)
    required_datasets.update(field.entity for field in resolved_fields.where_fields.values())

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
        key = raw_key.semantic_id if isinstance(raw_key, DimensionRef) else str(raw_key)
        if key in raw_root_keys:
            # Root-phase raw key: forwarded as-is so apply_slice_to_dataset
            # resolves it via the dataset_ir physical-column fallback.
            root_where[key] = value
            continue
        field = resolved_fields.where_fields[key]
        phase: Literal["root", "joined"] = "root" if field.entity == root else "joined"
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
    version_resolutions: list[dict[str, Any]] = []
    plan_warnings: list[dict[str, Any]] = []
    fanout_meta_collector: list[dict[str, Any]] = []
    pre_aggregated_tables: dict[str, Any] = {}
    for dataset_id in sorted(required_datasets - {root}):
        current_dataset = root
        for relationship in unique_shortest_relationship_path(project, root, dataset_id):
            safety = resolved_edge_safety(project, relationship, from_entity=current_dataset)
            if safety == JoinSafety.ONE_TO_MANY:
                policy = getattr(metric_ir, "fanout_policy", "block")
                if policy == "aggregate_then_join":
                    unsafe_dataset_id = (
                        relationship.to_entity
                        if relationship.from_entity == current_dataset
                        else relationship.from_entity
                    )
                    pre_table, merge_grain_meta = _aggregate_then_join_pre_aggregate(
                        project=project,
                        metric_ir=metric_ir,
                        unsafe_dataset_id=unsafe_dataset_id,
                        relationship=relationship,
                        from_dataset=current_dataset,
                        dataset_fns=dataset_fns,
                        backend=backend,
                        resolved_fields=resolved_fields,
                    )
                    pre_aggregated_tables[unsafe_dataset_id] = pre_table
                    fanout_meta_collector.append(merge_grain_meta)
                    safety = JoinSafety.MANY_TO_ONE
                else:
                    candidate_safe_roots = sorted(
                        {relationship.from_entity, relationship.to_entity} - {current_dataset}
                    )
                    raise_observe_planning_error(
                        code="unsafe-fanout",
                        message=(
                            f"Traversal through {relationship.semantic_id!r} is one-to-many; "
                            "the metric must re-root, remodel the entity key, or opt into "
                            "fanout_policy='aggregate_then_join'."
                        ),
                        candidates={
                            "relationship": relationship.semantic_id,
                            "safe_roots": candidate_safe_roots,
                            "fanout_policies": ["aggregate_then_join"],
                        },
                        repair=[
                            RepairAction(
                                action="set_metric_root",
                                target=metric_ir.semantic_id,
                                arg="root_entity",
                                value=candidate_safe_roots[0] if candidate_safe_roots else None,
                                safety=RepairSafety.MODELING_DECISION,
                                why=(
                                    "the substantive measure may live on the many side; "
                                    "re-root makes the metric definition match its measure space"
                                ),
                            ),
                            RepairAction(
                                action="set_fanout_policy",
                                target=metric_ir.semantic_id,
                                arg="fanout_policy",
                                value="aggregate_then_join",
                                safety=RepairSafety.MODELING_DECISION,
                                why=(
                                    "keep the current root and reduce the many side to merge "
                                    "grain before join; only correct if the merge grain has "
                                    "business meaning and every measure is additive there"
                                ),
                            ),
                        ],
                    )
            if safety == JoinSafety.UNKNOWN:
                raise_observe_planning_error(
                    code="unknown-join-safety",
                    message=(
                        f"Join safety for {relationship.semantic_id!r} cannot be derived "
                        "from dataset keys; planning fails."
                    ),
                    candidates={"relationship": relationship.semantic_id},
                    repair=[],
                )
            next_dataset = (
                relationship.to_entity
                if relationship.from_entity == current_dataset
                else relationship.from_entity
            )
            if next_dataset not in materialized:
                next_table = pre_aggregated_tables.get(next_dataset)
                if next_table is None:
                    next_table = dataset_fns[next_dataset](backend)
                next_dataset_meta = project.get_entity(next_dataset)
                versioning = (
                    getattr(next_dataset_meta, "versioning", None)
                    if next_dataset_meta is not None
                    else None
                )
                mapping: dict[date, date] | None = None
                if isinstance(versioning, SnapshotVersioningIR):
                    next_table, version_meta, mapping = _resolve_snapshot_versioning(
                        project=project,
                        session=session,
                        datasource_name=datasource_name,
                        snapshot_dataset_id=next_dataset,
                        snapshot_versioning=versioning,
                        snapshot_table=next_table,
                        snapshot_dataset_ir=dataset_irs[next_dataset],
                        root_table=root_table,
                        root_time_dimension=root_time_dimension,
                        resolved_window=resolved_window,
                    )
                    snapshot_metadata.append(version_meta)
                    version_resolutions.append(version_meta)
                elif isinstance(versioning, ValidityVersioningIR):
                    next_table, version_meta, is_as_of = _resolve_validity_versioning(
                        root_table=root_table,
                        root_time_dimension=root_time_dimension,
                        validity_table=next_table,
                        validity_versioning=versioning,
                        validity_dataset_id=next_dataset,
                        resolved_window=resolved_window,
                    )
                    version_resolutions.append(version_meta)
                    if is_as_of:
                        plan_warnings.append(
                            {"code": "validity_overlap_unverified", "dataset": next_dataset}
                        )
                        validity_predicate = _resolve_validity_as_of_predicate(
                            project=project,
                            current_table=widened_table,
                            root_time_dimension=root_time_dimension,
                            validity_table=next_table,
                            validity_versioning=versioning,
                            validity_dataset_id=next_dataset,
                        )
                        extra_predicates = [validity_predicate]
                    else:
                        extra_predicates = None
                    pre_join_dataset = current_dataset
                    widened_table, current_dataset = _join_table(
                        widened_table,
                        next_table,
                        project=project,
                        relationship=relationship,
                        current_entity=current_dataset,
                        extra_predicates=extra_predicates,
                    )
                    materialized[next_dataset] = widened_table
                    edge_metadata.append(
                        {
                            "relationship": relationship.semantic_id,
                            "from_dataset": pre_join_dataset,
                            "to_dataset": next_dataset,
                            "join_safety": safety.value,
                            "join_type": "left",
                        }
                    )
                    continue
                extra_predicates = None
                if mapping is not None:
                    # mapping is non-None only in as_of_root_time mode, which
                    # requires root_time_dimension to be non-None.
                    assert root_time_dimension is not None
                    anchor_expr = _field_fn(project, root_time_dimension.semantic_id)(
                        widened_table
                    ).cast("date")
                    extra_predicates = [anchor_expr == next_table.anchor_date]
                pre_join_dataset = current_dataset
                widened_table, current_dataset = _join_table(
                    widened_table,
                    next_table,
                    project=project,
                    relationship=relationship,
                    current_entity=current_dataset,
                    extra_predicates=extra_predicates,
                )
                materialized[next_dataset] = widened_table
            else:
                pre_join_dataset = current_dataset
                current_dataset = next_dataset
            edge_metadata.append(
                {
                    "relationship": relationship.semantic_id,
                    "from_dataset": pre_join_dataset,
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
                planned_dimension.column: _validate_field_expr(
                    _field_fn(project, planned_dimension.field.semantic_id)(widened_table),
                    field_id=planned_dimension.field.semantic_id,
                ).name(planned_dimension.column)
            }
        )
    dataset_tables = dict.fromkeys(metric_ir.entities, widened_table)
    # Populate axes_metadata with a "time" entry when this plan will produce a
    # time-series bucket at execution time.  This lets _execute_derived detect
    # per-component time availability without re-running the planner.
    has_time_axis = (
        root_time_dimension is not None
        and resolved_window is not None
        and getattr(resolved_window, "grain", None) is not None
    )
    axes_meta: dict[str, Any] = {
        dimension.column: {"role": "dimension", "column": dimension.column}
        for dimension in planned_dimensions
    }
    if has_time_axis:
        _grain_token = (
            resolved_window.grain.to_token()
            if resolved_window is not None and resolved_window.grain is not None
            else None
        )
        axes_meta["time"] = {
            "role": "time",
            "column": "bucket_start",
            "grain": _grain_token,
            "time_dimension": root_time_dimension.name,  # type: ignore[union-attr]
        }
    return BaseObservePlan(
        root_entity=root,
        additivity=metric_ir.additivity,
        table=widened_table,
        dataset_tables=dataset_tables,
        dimensions=planned_dimensions,
        where=planned_where,
        axes_metadata=axes_meta,
        lineage_metadata={
            "root_entity": root,
            "additivity": metric_ir.additivity,
            "fanout_policy": metric_ir.fanout_policy,
            "fanouts": fanout_meta_collector,
            "relationships": edge_metadata,
            "snapshots": snapshot_metadata,
            "version_resolutions": version_resolutions,
        },
        warnings=plan_warnings,
        datasource_name=datasource_name,
    )


def plan_observe(
    *,
    project: Any,
    session: Any,
    metric_ir: Any,
    dataset_irs: dict[str, Any],
    dataset_fns: dict[str, Any],
    dimensions: Any,
    where: Any,
    resolved_window: Any,
    time_dimension: Any,
) -> ObservePlan:
    if not metric_ir.is_derived:
        return plan_base_observe(
            project=project,
            session=session,
            metric_ir=metric_ir,
            dataset_irs=dataset_irs,
            dataset_fns=dataset_fns,
            dimensions=dimensions,
            where=where,
            resolved_window=resolved_window,
            time_dimension=time_dimension,
        )
    return _plan_derived_observe(
        project=project,
        session=session,
        metric_ir=metric_ir,
        dataset_irs=dataset_irs,
        dataset_fns=dataset_fns,
        dimensions=dimensions,
        where=where,
        resolved_window=resolved_window,
        time_dimension=time_dimension,
    )


def _component_dataset_adapters(
    project: Any,
    session: Any,
    component_ir: Any,
    parent_dataset_irs: dict[str, Any],
    parent_dataset_fns: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Re-adapt the component's own datasets, reusing parent adapters when they already exist.

    Returns a dataset_irs/fns map that includes ALL parent datasets so the planner
    can resolve qualified dimension/filter refs that land on datasets outside the
    component's own dataset list.  The component's own datasets are always included;
    parent datasets are merged in so relationship-path resolution can proceed.
    """
    component_dataset_irs: dict[str, Any] = dict(parent_dataset_irs)
    component_dataset_fns: dict[str, Any] = dict(parent_dataset_fns)
    for entity_id in component_ir.entities:
        if entity_id in component_dataset_irs:
            continue
        # _build_dataset_adapter lives in observe.py; import lazily to avoid
        # circular import at module load time.
        from marivo.analysis.intents.observe import _build_dataset_adapter

        ds_ir = project.get_entity(entity_id)
        if ds_ir is None:
            raise_observe_planning_error(
                code="derived-shared-planner-unsupported",
                message=f"entity {entity_id!r} not found for component metric {component_ir.semantic_id!r}",
                candidates={"entity": entity_id},
                repair=[],
            )
        adapter = _build_dataset_adapter(project, ds_ir)
        component_dataset_irs[entity_id] = adapter
        component_dataset_fns[entity_id] = adapter.fn
    return component_dataset_irs, component_dataset_fns


def _accumulate_unreachable_ref(
    exc: ObservePlanningError,
    component_id: str,
    *,
    dimensions: list[DimensionRef] | None,
    where: dict[Any, Any] | None,
    axes_acc: dict[str, list[str]],
    where_acc: dict[str, list[str]],
) -> None:
    """Classify a field-ref-not-found/ambiguous error as a missing axis or missing filter."""
    msg = exc.message or ""
    for dim in dimensions or []:
        if f"{dim.semantic_id!r}" in msg:
            axes_acc.setdefault(dim.semantic_id, []).append(component_id)
            return
    for raw_key in where or {}:
        key = raw_key.semantic_id if isinstance(raw_key, DimensionRef) else str(raw_key)
        if f"{key!r}" in msg:
            where_acc.setdefault(key, []).append(component_id)
            return
    raise exc


def _accumulate_path_unreachable(
    exc: ObservePlanningError,
    component_id: str,
    *,
    dimensions: list[DimensionRef] | None,
    where: dict[Any, Any] | None,
    axes_acc: dict[str, list[str]],
    where_acc: dict[str, list[str]],
) -> None:
    """Classify a path-missing/path-ambiguous error as a missing axis or missing filter.

    When a component cannot reach a dimension's dataset via its relationship graph,
    we attribute the failure to the first dimension or filter whose dataset matches
    the unreachable target.  If no match is found, re-raise.
    """
    details = exc.details or {}
    candidates = details.get("candidates", {}) if isinstance(details, dict) else {}
    to_dataset = candidates.get("to_dataset") if isinstance(candidates, dict) else None
    # Try to match to a dimension
    for dim in dimensions or []:
        # The dimension id may be qualified (e.g. 'sales.country') or unqualified.
        # We match on the local name part.
        local_name = dim.semantic_id.rsplit(".", 1)[-1]
        if to_dataset is not None and local_name in to_dataset:
            axes_acc.setdefault(dim.semantic_id, []).append(component_id)
            return
    # Fallback: attribute to the first dimension if any
    for dim in dimensions or []:
        axes_acc.setdefault(dim.semantic_id, []).append(component_id)
        return
    # Try to match to a where filter
    for raw_key in where or {}:
        key = raw_key.semantic_id if isinstance(raw_key, DimensionRef) else str(raw_key)
        where_acc.setdefault(key, []).append(component_id)
        return
    raise exc


def _raise_component_axis_unreachable(
    missing_map: dict[str, list[str]],
    component_plans: list[ComponentPlan],
    parent_dimensions: list[DimensionRef] | None,
) -> None:
    dim_id, components_missing = next(iter(missing_map.items()))
    resolved = []
    target = next((p for p in (parent_dimensions or []) if p.semantic_id == dim_id), None)
    for cp in component_plans:
        for d in cp.base_plan.dimensions:
            if target is not None and d.column == target.semantic_id.rsplit(".", 1)[-1]:
                resolved.append(
                    {
                        "metric": cp.component_metric_ir.semantic_id,
                        "resolved_field_id": d.field.semantic_id,
                    }
                )
    raise_observe_planning_error(
        code="component-axis-unreachable",
        message=f"Parent dimension {dim_id!r} cannot be resolved by every component.",
        candidates={
            "dimension": dim_id,
            "missing_components": components_missing,
            "resolved_components": resolved,
        },
        repair=[],
    )


def _raise_component_filter_unreachable(
    missing_map: dict[str, list[str]],
    component_plans: list[ComponentPlan],
    parent_where: dict[Any, Any] | None,
) -> None:
    key, components_missing = next(iter(missing_map.items()))
    resolved = []
    for cp in component_plans:
        for pw in cp.base_plan.where:
            if pw.original_key == key:
                resolved.append(
                    {
                        "metric": cp.component_metric_ir.semantic_id,
                        "resolved_field_id": pw.field.semantic_id,
                    }
                )
    raise_observe_planning_error(
        code="component-filter-unreachable",
        message=f"Parent filter {key!r} cannot be applied by every component.",
        candidates={
            "filter_key": key,
            "missing_components": components_missing,
            "resolved_components": resolved,
        },
        repair=[],
    )


def _check_axis_comparability(
    component_plans: list[ComponentPlan],
    parent_dimensions: list[DimensionRef] | None,
) -> None:
    for dim in parent_dimensions or []:
        col = dim.semantic_id.rsplit(".", 1)[-1]
        per_component: dict[str, list[Any]] = {
            cp.component_metric_ir.semantic_id: [
                d.field for d in cp.base_plan.dimensions if d.column == col
            ]
            for cp in component_plans
        }
        ids = {fields[0].semantic_id for fields in per_component.values() if fields}
        if len(ids) > 1:
            raise_observe_planning_error(
                code="component-axis-field-mismatch",
                message=f"Dimension {dim.semantic_id!r} resolves to different field ids across components.",
                candidates={
                    "dimension": dim.semantic_id,
                    "components": [
                        {"metric": cid, "resolved_field_id": fields[0].semantic_id}
                        for cid, fields in per_component.items()
                        if fields
                    ],
                },
                repair=[],
            )


def _check_filter_comparability(
    component_plans: list[ComponentPlan],
    parent_where: dict[Any, Any] | None,
) -> None:
    for raw_key in parent_where or {}:
        key = raw_key.semantic_id if isinstance(raw_key, DimensionRef) else str(raw_key)
        applied: dict[str, list[PlannedWhere]] = {
            cp.component_metric_ir.semantic_id: [
                pw for pw in cp.base_plan.where if pw.original_key == key
            ]
            for cp in component_plans
        }
        field_ids = {applied[cid][0].field.semantic_id for cid in applied if applied[cid]}
        if len(field_ids) > 1:
            raise_observe_planning_error(
                code="component-filter-field-mismatch",
                message=f"Filter {key!r} resolves to different field ids across components.",
                candidates={
                    "filter_key": key,
                    "components": [
                        {"metric": cid, "resolved_field_id": pws[0].field.semantic_id}
                        for cid, pws in applied.items()
                        if pws
                    ],
                },
                repair=[],
            )


def _check_version_comparability(component_plans: list[ComponentPlan]) -> None:
    by_dataset: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for cp in component_plans:
        for vmeta in cp.base_plan.lineage_metadata.get("version_resolutions", []):
            by_dataset.setdefault(vmeta["dataset"], []).append(
                (cp.component_metric_ir.semantic_id, vmeta)
            )
    for dataset_id, entries in by_dataset.items():
        if len(entries) < 2:
            continue
        keys = {
            (
                v["mode"],
                v["anchor_source"],
                v.get("anchor_value"),
                v.get("resolved_partition"),
                v.get("resolved_interval_predicate"),
                v.get("anchor_to_partition_mapping_digest"),
            )
            for _cid, v in entries
        }
        if len(keys) > 1:
            raise_observe_planning_error(
                code="component-version-mismatch",
                message=f"Versioned dataset {dataset_id!r} differs across components.",
                candidates={
                    "versioned_dataset": dataset_id,
                    "components": [
                        {
                            "metric": cid,
                            "mode": v["mode"],
                            "anchor_source": v["anchor_source"],
                            "anchor_value": v.get("anchor_value"),
                            "resolved_partition_or_predicate": (
                                v.get("resolved_partition") or v.get("resolved_interval_predicate")
                            ),
                            "mapping_digest": v.get("anchor_to_partition_mapping_digest"),
                        }
                        for cid, v in entries
                    ],
                },
                repair=[],
            )


def _plan_derived_observe(
    *,
    project: Any,
    session: Any,
    metric_ir: Any,
    dataset_irs: dict[str, Any],
    dataset_fns: dict[str, Any],
    dimensions: list[DimensionRef] | None,
    where: dict[Any, Any] | None,
    resolved_window: Any | None,
    time_dimension: Any,
) -> DerivedObservePlan:
    component_plans: list[ComponentPlan] = []
    component_unreachable_axes: dict[str, list[str]] = {}
    component_unreachable_where: dict[str, list[str]] = {}

    for role, component_id in metric_ir.decomposition.components.items():
        component_ir = project.get_metric(component_id)
        if component_ir is None:
            raise_observe_planning_error(
                code="derived-shared-planner-unsupported",
                message=f"component metric {component_id!r} not found",
                candidates={"metric": component_id},
                repair=[],
            )
        if component_ir.is_derived:
            raise_observe_planning_error(
                code="nested-derived-unsupported",
                message=f"component metric {component_id!r} is itself derived; nested derived is unsupported.",
                candidates={"metric": component_id},
                repair=[],
            )
        component_dataset_irs, component_dataset_fns = _component_dataset_adapters(
            project,
            session,
            component_ir,
            dataset_irs,
            dataset_fns,
        )
        try:
            base_plan = plan_base_observe(
                project=project,
                session=session,
                metric_ir=component_ir,
                dataset_irs=component_dataset_irs,
                dataset_fns=component_dataset_fns,
                dimensions=dimensions,
                where=where,
                resolved_window=resolved_window,
                time_dimension=time_dimension,
                allow_unqualified_outside_scope=True,
            )
        except WindowInvalidError as _win_exc:
            # Component root has no time field; skip window for this component.
            if "has no @ms.time_dimension" not in (_win_exc.message or ""):
                raise
            base_plan = plan_base_observe(
                project=project,
                session=session,
                metric_ir=component_ir,
                dataset_irs=component_dataset_irs,
                dataset_fns=component_dataset_fns,
                dimensions=dimensions,
                where=where,
                resolved_window=None,
                time_dimension=time_dimension,
                allow_unqualified_outside_scope=True,
            )
        except ObservePlanningError as exc:
            details = exc.details
            code = details.get("code") if isinstance(details, dict) else None
            if code in ("field-ref-not-found", "field-ref-ambiguous"):
                _accumulate_unreachable_ref(
                    exc,
                    component_id,
                    dimensions=dimensions,
                    where=where,
                    axes_acc=component_unreachable_axes,
                    where_acc=component_unreachable_where,
                )
                continue
            if code in ("path-missing", "path-ambiguous"):
                # The component cannot reach a dimension or filter dataset via
                # its relationship graph.  Classify as unreachable axis/filter.
                _accumulate_path_unreachable(
                    exc,
                    component_id,
                    dimensions=dimensions,
                    where=where,
                    axes_acc=component_unreachable_axes,
                    where_acc=component_unreachable_where,
                )
                continue
            raise
        component_plans.append(
            ComponentPlan(component_metric_ir=component_ir, role=role, base_plan=base_plan)
        )

    if component_unreachable_axes:
        _raise_component_axis_unreachable(component_unreachable_axes, component_plans, dimensions)
    if component_unreachable_where:
        _raise_component_filter_unreachable(component_unreachable_where, component_plans, where)

    _check_axis_comparability(component_plans, dimensions)
    _check_filter_comparability(component_plans, where)
    _check_version_comparability(component_plans)

    parent_axes = component_plans[0].base_plan.axes_metadata if component_plans else {}
    lineage_metadata: dict[str, Any] = {
        "metric": metric_ir.semantic_id,
        "components": [
            {
                "component_metric_id": cp.component_metric_ir.semantic_id,
                "role": cp.role,
                "datasource": cp.base_plan.datasource_name,
                "lineage_metadata": cp.base_plan.lineage_metadata,
            }
            for cp in component_plans
        ],
        "component_datasources": [
            (cp.component_metric_ir.semantic_id, cp.base_plan.datasource_name)
            for cp in component_plans
        ],
    }
    return DerivedObservePlan(
        metric_ir=metric_ir,
        component_plans=component_plans,
        parent_axes=parent_axes,
        lineage_metadata=lineage_metadata,
        warnings=[w for cp in component_plans for w in cp.base_plan.warnings],
    )
