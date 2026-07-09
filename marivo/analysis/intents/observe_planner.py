"""Phase 1 base observe planner."""

from __future__ import annotations

import hashlib
import json
import operator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum
from functools import reduce
from types import SimpleNamespace
from typing import Any, Literal
from zoneinfo import ZoneInfo

import ibis
import ibis.expr.types as ir_types

from marivo.analysis.errors import WindowInvalidError
from marivo.analysis.executor.runner import (
    apply_slice_to_dataset,
    execute,
)
from marivo.analysis.executor.windowing import (
    apply_window_to_dataset,
    datasource_engine_profile,
    datasource_read_timezone,
)
from marivo.analysis.intents.observe_errors import (
    ObservePlanningError,
    RepairAction,
    RepairSafety,
    raise_observe_planning_error,
)
from marivo.analysis.intents.sampled_fold import ensure_status_time_dimension_matches
from marivo.analysis.semantic_inputs import DimensionInput
from marivo.analysis.windows.spec import is_date_only
from marivo.introspection._fuzzy import did_you_mean
from marivo.refs import SemanticRef
from marivo.semantic.catalog import (
    DerivedMetricDetails,
    DimensionDetails,
    EntityDetails,
    MetricDetails,
    RelationshipDetails,
    SemanticCatalog,
    SemanticKind,
    SimpleMetricDetails,
    TimeDimensionDetails,
)
from marivo.semantic.errors import ErrorKind, SemanticRuntimeError
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
    status_time_dimension: str | None = None
    time_fold: Any | None = None


@dataclass(frozen=True)
class ComponentPlan:
    component_metric_ir: Any
    role: str
    base_plan: BaseObservePlan | CumulativeObservePlan


@dataclass(frozen=True)
class CumulativeObservePlan:
    metric_ir: Any
    base_metric_ir: Any
    base_plan: BaseObservePlan
    over: str | None
    window: Any | None
    # Resolved CumulativeComposition (carries the real anchor) from the
    # metric IR. Present when the plan is built from a real MetricIR; absent
    # (None) for adapter-only construction paths.
    composition: Any = None

    @property
    def dimensions(self) -> list[PlannedDimension]:
        return self.base_plan.dimensions

    @property
    def where(self) -> list[PlannedWhere]:
        return self.base_plan.where

    @property
    def axes_metadata(self) -> dict[str, Any]:
        return self.base_plan.axes_metadata

    @property
    def lineage_metadata(self) -> dict[str, Any]:
        return self.base_plan.lineage_metadata

    @property
    def warnings(self) -> list[dict[str, Any]]:
        return self.base_plan.warnings

    @property
    def datasource_name(self) -> str:
        return self.base_plan.datasource_name

    @property
    def root_entity(self) -> str:
        return self.base_plan.root_entity

    @property
    def table(self) -> Any:
        return self.base_plan.table


@dataclass(frozen=True)
class DerivedObservePlan:
    metric_ir: Any
    component_plans: list[ComponentPlan]
    parent_axes: dict[str, Any]
    lineage_metadata: dict[str, Any]
    warnings: list[dict[str, Any]] = field(default_factory=list)


ObservePlan = BaseObservePlan | CumulativeObservePlan | DerivedObservePlan


@dataclass(frozen=True)
class ResolvedObserveFields:
    dimensions: list[Any] = field(default_factory=list)
    where_fields: dict[str, Any] = field(default_factory=dict)
    raw_root_where_keys: tuple[str, ...] = ()
    time_dimension: Any | None = None


FieldDetails = DimensionDetails | TimeDimensionDetails


@dataclass(frozen=True)
class _PlannedFieldDetails:
    details: FieldDetails

    @property
    def ref(self) -> Any:
        return self.details.ref

    @property
    def semantic_id(self) -> str:
        return self.details.ref.id

    @property
    def name(self) -> str:
        return self.details.name

    @property
    def entity(self) -> str:
        return self.details.entity.id

    def __getattr__(self, name: str) -> Any:
        return getattr(self.details, name)


def _planned_field(field: Any) -> _PlannedFieldDetails:
    if isinstance(field, _PlannedFieldDetails):
        return field
    return _PlannedFieldDetails(field)


@dataclass(frozen=True)
class _PlannedRelationshipDetails:
    details: RelationshipDetails

    @property
    def ref(self) -> Any:
        return self.details.ref

    @property
    def semantic_id(self) -> str:
        return self.details.ref.id

    @property
    def from_entity(self) -> str:
        return self.details.from_entity.id

    @property
    def to_entity(self) -> str:
        return self.details.to_entity.id

    @property
    def from_keys(self) -> tuple[str, ...]:
        return self.details.from_keys

    @property
    def to_keys(self) -> tuple[str, ...]:
        return self.details.to_keys

    def __getattr__(self, name: str) -> Any:
        return getattr(self.details, name)


def _planned_relationship(relationship: RelationshipDetails) -> _PlannedRelationshipDetails:
    return _PlannedRelationshipDetails(relationship)


RelationshipInfo = RelationshipDetails | _PlannedRelationshipDetails
PlannerField = FieldDetails | _PlannedFieldDetails


@dataclass(frozen=True)
class _MetricDetailsAdapter:
    details: MetricDetails

    @property
    def semantic_id(self) -> str:
        return self.details.ref.id

    @property
    def name(self) -> str:
        return self.details.name

    @property
    def root_entity(self) -> str | None:
        return self.details.root_entity.id if self.details.root_entity is not None else None

    @property
    def entities(self) -> tuple[str, ...]:
        return tuple(entity.id for entity in self.details.entities)

    @property
    def additivity(self) -> str | None:
        return self.details.additivity

    @property
    def fanout_policy(self) -> str:
        return self.details.fanout_policy

    @property
    def metric_type(self) -> str:
        return self.details.metric_type

    @property
    def composition(self) -> Any:
        if not isinstance(self.details, DerivedMetricDetails):
            return None
        components = {
            role: (ref.id if isinstance(ref, SemanticRef) else str(ref))
            for role, ref in self.details.components
        }
        return SimpleNamespace(
            kind=self.details.composition,
            components=components,
            signs=(
                dict(self.details.linear_terms)
                if self.details.composition == "linear" and self.details.linear_terms
                else None
            ),
            # Cumulative-specific fields.  The adapter wraps
            # DerivedMetricDetails, which carries composition as a string
            # and components as role-ref pairs; the real CumulativeComposition
            # IR (with resolved over) lives on MetricIR.  When over is not
            # available from the details, default to None — the real
            # MetricIR path provides the resolved value.
            base=components.get("base") if self.details.composition == "cumulative" else None,
            over=None,
            anchor="all_history" if self.details.composition == "cumulative" else None,
        )

    @property
    def linear_terms(self) -> tuple[tuple[str, str], ...]:
        if isinstance(self.details, DerivedMetricDetails):
            return self.details.linear_terms
        return ()

    @property
    def aggregation(self) -> Any:
        if isinstance(self.details, SimpleMetricDetails):
            return self.details.aggregation
        return None

    @property
    def measure(self) -> str | None:
        if isinstance(self.details, SimpleMetricDetails):
            return self.details.measure.id if self.details.measure else None
        return None

    @property
    def time_fold(self) -> Any | None:
        if self.details.fold is None:
            return None
        return _TimeFoldDetailsAdapter(self.details.fold)

    @property
    def status_time_dimension(self) -> str | None:
        return self.details.status_time_dimension

    @property
    def unit(self) -> str | None:
        return self.details.unit


@dataclass(frozen=True)
class _TimeFoldDetailsAdapter:
    value: str

    @property
    def kind(self) -> str:
        if self.value.startswith("percentile("):
            return "percentile"
        return self.value

    @property
    def q(self) -> float | None:
        if not self.value.startswith("percentile("):
            return None
        return float(self.value.removeprefix("percentile(").removesuffix(")"))

    def label(self) -> str:
        return self.value


def _planned_metric(details: MetricDetails) -> _MetricDetailsAdapter:
    return _MetricDetailsAdapter(details)


def _composition_kind(metric_ir: Any) -> str | None:
    """Return the composition kind string (e.g. 'ratio', 'cumulative') or None."""
    composition = getattr(metric_ir, "composition", None)
    if composition is None:
        return None
    kind = getattr(composition, "kind", None)
    return str(kind) if kind is not None else None


def _is_cumulative_metric(metric_ir: Any) -> bool:
    """True when the metric's composition kind is 'cumulative'."""
    return _composition_kind(metric_ir) == "cumulative"


def _catalog_id(ref: str, kind: SemanticKind) -> str:
    return f"{kind.value}.{ref}"


def _details(catalog: SemanticCatalog, ref: str) -> Any:
    for kind in (
        SemanticKind.METRIC,
        SemanticKind.ENTITY,
        SemanticKind.DIMENSION,
        SemanticKind.TIME_DIMENSION,
        SemanticKind.RELATIONSHIP,
        SemanticKind.MEASURE,
    ):
        try:
            return catalog.get(_catalog_id(ref, kind)).details()
        except SemanticRuntimeError as exc:
            if exc.kind != ErrorKind.NOT_FOUND.value:
                raise
    raise_observe_planning_error(
        code="path-missing",
        message=f"Semantic reference {ref!r} was not found.",
        candidates={"ref": ref},
        repair=[],
    )


def _entity(catalog: SemanticCatalog, ref: str) -> EntityDetails:
    details = _details(catalog, ref)
    if not isinstance(details, EntityDetails):
        raise_observe_planning_error(
            code="path-missing",
            message=f"Entity reference {ref!r} was not found.",
            candidates={"ref": ref},
            repair=[],
        )
    return details


def _metric(catalog: SemanticCatalog, ref: str) -> MetricDetails:
    details = _details(catalog, ref)
    if not isinstance(details, (SimpleMetricDetails, DerivedMetricDetails)):
        raise_observe_planning_error(
            code="derived-shared-planner-unsupported",
            message=f"Metric reference {ref!r} was not found.",
            candidates={"ref": ref},
            repair=[],
        )
    return details


def _fields_for_entity(catalog: SemanticCatalog, entity_ref: str) -> list[FieldDetails]:
    fields: list[FieldDetails] = []
    for kind in (SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION):
        for obj in catalog.list(str(kind), scope=f"entity.{entity_ref}"):
            details = obj.details()
            if isinstance(details, (DimensionDetails, TimeDimensionDetails)):
                fields.append(details)
    return fields


def _fields_for_entities(catalog: SemanticCatalog, entity_refs: set[str]) -> list[FieldDetails]:
    fields: list[FieldDetails] = []
    for entity_ref in sorted(entity_refs):
        fields.extend(_fields_for_entity(catalog, entity_ref))
    return fields


def _ref_id(value: Any) -> str:
    ref = getattr(value, "ref", None)
    if isinstance(ref, str):
        return ref
    nested = getattr(ref, "ref", None)
    if isinstance(nested, str):
        return nested
    semantic_id = getattr(value, "semantic_id", None)
    if isinstance(semantic_id, str):
        return semantic_id
    return str(value)


def _entity_id(field: Any) -> str:
    return _ref_id(field.entity)


def _input_ref_id(value: Any) -> str:
    return _ref_id(value)


def _relationship_id(relationship: Any) -> str:
    return _ref_id(relationship)


def _from_entity_id(relationship: Any) -> str:
    return _ref_id(relationship.from_entity)


def _to_entity_id(relationship: Any) -> str:
    return _ref_id(relationship.to_entity)


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


def _fields_for_datasets(catalog: SemanticCatalog, entity_refs: set[str]) -> list[FieldDetails]:
    return _fields_for_entities(catalog, entity_refs)


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
        else {
            obj.ref.id
            for domain in catalog.list("domain")
            for obj in catalog.list("entity", scope=f"domain.{domain.ref.id}")
        },
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
    dimensions: list[DimensionInput] | None,
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
    for obj in catalog.list("relationship", scope=f"entity.{dataset_id}"):
        details = obj.details()
        if isinstance(details, RelationshipDetails):
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
    qualifying = (
        root_time_dimension is not None
        and getattr(root_time_dimension, "data_type", None)
        in {
            "date",
            "timestamp",
        }
    ) or (
        root_time_dimension is not None
        and getattr(root_time_dimension, "data_type", None) is None
        and getattr(root_time_dimension, "parse_kind", None) is None
    )
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
    catalog: SemanticCatalog, root_entity_id: str, *, explicit_time_dimension: Any | None
) -> PlannerField | None:
    if explicit_time_dimension is not None:
        return _planned_field(explicit_time_dimension)
    candidates = [
        field
        for field in _fields_for_entity(catalog, root_entity_id)
        if isinstance(field, TimeDimensionDetails)
    ]
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
        cache=session._connection_runtime,
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
        cache=session._connection_runtime,
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


def _field_fn(catalog: SemanticCatalog, field_id: str) -> Any:
    resolver = catalog._resolver(connections=_NoConnectionService())
    missing_ref_kinds = {
        ErrorKind.DIMENSION_NOT_FOUND,
        ErrorKind.NOT_FOUND,
    }

    def _resolve(table: Any) -> Any:
        try:
            return _validate_field_expr(resolver.dimension_on(field_id, table), field_id=field_id)
        except SemanticRuntimeError as exc:
            message = str(exc)
            if (
                exc.kind == ErrorKind.MATERIALIZE_FAILED
                and "instead of an ibis expression" in message
            ):
                raise_observe_planning_error(
                    code="field-expr-type-error",
                    message=message,
                    candidates={"field_id": field_id, "actual_type": "unknown"},
                    repair=[],
                )
            if exc.kind in missing_ref_kinds:
                raise_observe_planning_error(
                    code="field-ref-not-found",
                    message=f"Field reference {field_id!r} was not found in observe plan scope.",
                    candidates={"field_id": field_id},
                    repair=[],
                )
            raise

    return _resolve


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
    catalog: SemanticCatalog,
    relationship: RelationshipInfo,
    current_entity: str,
    extra_predicates: list[Any] | None = None,
    join_type: Literal["left", "inner"] = "left",
) -> tuple[Any, str]:
    if _from_entity_id(relationship) == current_entity:
        next_entity = _to_entity_id(relationship)
        left_fields = relationship.from_keys
        right_fields = relationship.to_keys
    else:
        next_entity = _from_entity_id(relationship)
        left_fields = relationship.to_keys
        right_fields = relationship.from_keys
    predicates = [
        _field_fn(catalog, left_field)(current_table) == _field_fn(catalog, right_field)(next_table)
        for left_field, right_field in zip(left_fields, right_fields, strict=True)
    ]
    if extra_predicates:
        predicates.extend(extra_predicates)
    return current_table.join(next_table, predicates, how=join_type), next_entity


def _resolve_snapshot_as_of_root_time(
    *,
    catalog: SemanticCatalog,
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
    time_field_fn = _field_fn(catalog, root_time_dimension.ref.id)
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
    catalog: SemanticCatalog,
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
        catalog=catalog,
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
    catalog: SemanticCatalog,
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
    anchor = _field_fn(catalog, root_time_dimension.ref.id)(current_table).cast("date")
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
    catalog: SemanticCatalog,
    metric_ir: Any,
    unsafe_dataset_id: str,
    relationship: RelationshipInfo,
    from_dataset: str,
    dataset_fns: dict[str, Any],
    backend: Any,
    resolved_fields: ResolvedObserveFields,
    dataset_ir: Any,
    where_values: dict[str, Any],
) -> tuple[Any, dict[str, Any]]:
    """Reduce the unsafe-side dataset to the merge grain before joining.

    Merge grain = (join key on unsafe side) ∪ (requested non-root dimensions
    targeting unsafe_dataset_id). Where predicates targeting unsafe_dataset_id
    (``where_values``, keyed by field name) filter the unsafe-side table before
    the distinct reduction and stay out of the grain, so a where slice keeps
    semi-join membership semantics: a root row that has at least one matching
    row on the many side is counted exactly once, even when the predicate
    matches several of its rows. Each grain entry projects through
    ``_field_fn`` so the resulting table keeps the physical column names that
    downstream field bodies expect.
    """
    if _from_entity_id(relationship) == unsafe_dataset_id:
        join_field_ids: tuple[str, ...] = tuple(relationship.from_keys)
    else:
        join_field_ids = tuple(relationship.to_keys)

    grain_field_ids: list[str] = []
    seen_ids: set[str] = set()
    for fid in join_field_ids:
        if fid not in seen_ids:
            grain_field_ids.append(fid)
            seen_ids.add(fid)
    for f in resolved_fields.dimensions:
        if _entity_id(f) != unsafe_dataset_id:
            continue
        field_id = f.ref.id
        if field_id not in seen_ids:
            grain_field_ids.append(field_id)
            seen_ids.add(field_id)

    table = dataset_fns[unsafe_dataset_id](backend)
    if where_values:
        table = apply_slice_to_dataset(table, where_values, dataset_ir=dataset_ir)
    projections: list[Any] = []
    grain_meta_entries: list[dict[str, Any]] = []
    join_field_id_set = set(join_field_ids)
    seen_columns: set[str] = set()
    for fid in grain_field_ids:
        expr = _field_fn(catalog, fid)(table)
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
        "relationship": _relationship_id(relationship),
        "from_dataset": from_dataset,
        "merge_grain": grain_meta_entries,
        "pre_applied_where": sorted(where_values),
    }
    return pre_aggregated, merge_grain_meta


def plan_base_observe(
    *,
    catalog: SemanticCatalog | None = None,
    session: Any,
    metric_ir: Any,
    dataset_irs: dict[str, Any],
    dataset_fns: dict[str, Any],
    dimensions: list[Any] | None,
    where: dict[Any, Any] | None,
    resolved_window: Any | None,
    time_dimension: str | None,
    allow_unqualified_outside_scope: bool = False,
) -> BaseObservePlan:
    if catalog is None:
        catalog = session.catalog
    root = resolve_metric_root(metric_ir)
    ensure_status_time_dimension_matches(metric_ir, time_dimension)
    if metric_ir.additivity is None:
        raise_observe_planning_error(
            code="missing-additivity",
            message=f"Base metric {metric_ir.semantic_id!r} must declare additivity.",
            candidates={"metric": metric_ir.semantic_id},
            repair=[],
        )
    resolved_fields = resolve_observe_fields(
        catalog,
        metric_ir,
        dimensions=dimensions,
        where=where,
        time_dimension=time_dimension,
        allow_unqualified_outside_scope=allow_unqualified_outside_scope,
    )
    root_time_dimension = _root_time_dimension(
        catalog, root, explicit_time_dimension=resolved_fields.time_dimension
    )
    required_datasets = {root, *metric_ir.entities}
    required_datasets.update(_entity_id(field) for field in resolved_fields.dimensions)
    required_datasets.update(_entity_id(field) for field in resolved_fields.where_fields.values())

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
        session._connection_runtime.get_or_create(datasource_name),
        session._connection_runtime.get_or_create(datasource_name),
    )
    root_table = dataset_fns[root](backend)
    root_table = apply_window_to_dataset(
        root_table,
        resolved_window,
        dataset_ir=dataset_irs[root],
        report_tz=session.report_tz,
        datasource_read_tz=datasource_read_timezone(
            session._connection_runtime, dataset_irs[root].datasource_name
        ),
        profile=datasource_engine_profile(
            session._connection_runtime, dataset_irs[root].datasource_name
        ),
    )

    planned_where: list[PlannedWhere] = []
    root_where: dict[str, Any] = {}
    joined_where: dict[str, Any] = {}
    raw_root_keys = set(resolved_fields.raw_root_where_keys)
    for raw_key, value in (where or {}).items():
        key = _input_ref_id(raw_key)
        if key in raw_root_keys:
            # Root-phase raw key: forwarded as-is so apply_slice_to_dataset
            # resolves it via the dataset_ir physical-column fallback.
            root_where[key] = value
            continue
        field = resolved_fields.where_fields[key]
        phase: Literal["root", "joined"] = "root" if _entity_id(field) == root else "joined"
        planned_where.append(
            PlannedWhere(original_key=key, field=_planned_field(field), value=value, phase=phase)
        )
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
    fanout_join_types: dict[str, Literal["left", "inner"]] = {}
    for dataset_id in sorted(required_datasets - {root}):
        current_dataset = root
        for relationship in unique_shortest_relationship_path(catalog, root, dataset_id):
            safety = resolved_edge_safety(catalog, relationship, from_entity=current_dataset)
            if safety == JoinSafety.ONE_TO_MANY:
                policy = getattr(metric_ir, "fanout_policy", "block")
                if policy == "aggregate_then_join":
                    unsafe_dataset_id = (
                        _to_entity_id(relationship)
                        if _from_entity_id(relationship) == current_dataset
                        else _from_entity_id(relationship)
                    )
                    # Where predicates on the unsafe side must filter before the
                    # distinct reduction; leaving them for the post-join slice
                    # would keep one merge-grain row per matching many-side
                    # value and double-count the root measure.
                    unsafe_where: dict[str, Any] = {}
                    for where_field in resolved_fields.where_fields.values():
                        if (
                            _entity_id(where_field) == unsafe_dataset_id
                            and where_field.name in joined_where
                        ):
                            unsafe_where[where_field.name] = joined_where.pop(where_field.name)
                    pre_table, merge_grain_meta = _aggregate_then_join_pre_aggregate(
                        catalog=catalog,
                        metric_ir=metric_ir,
                        unsafe_dataset_id=unsafe_dataset_id,
                        relationship=relationship,
                        from_dataset=current_dataset,
                        dataset_fns=dataset_fns,
                        backend=backend,
                        resolved_fields=resolved_fields,
                        dataset_ir=dataset_irs[unsafe_dataset_id],
                        where_values=unsafe_where,
                    )
                    pre_aggregated_tables[unsafe_dataset_id] = pre_table
                    # A pre-applied where slice means semi-join membership:
                    # roots without any matching many-side row must drop, so
                    # the reduced table joins inner instead of left. setdefault
                    # keeps the first traversal's type: later traversals of an
                    # already-joined dataset find joined_where drained.
                    fanout_join_types.setdefault(
                        unsafe_dataset_id, "inner" if unsafe_where else "left"
                    )
                    fanout_meta_collector.append(merge_grain_meta)
                    safety = JoinSafety.MANY_TO_ONE
                else:
                    candidate_safe_roots = sorted(
                        {_from_entity_id(relationship), _to_entity_id(relationship)}
                        - {current_dataset}
                    )
                    raise_observe_planning_error(
                        code="unsafe-fanout",
                        message=(
                            f"Traversal through {_relationship_id(relationship)!r} is one-to-many; "
                            "the metric must re-root, remodel the entity key, or opt into "
                            "fanout_policy='aggregate_then_join'."
                        ),
                        candidates={
                            "relationship": _relationship_id(relationship),
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
                        f"Join safety for {_relationship_id(relationship)!r} cannot be derived "
                        "from dataset keys; planning fails."
                    ),
                    candidates={"relationship": _relationship_id(relationship)},
                    repair=[],
                )
            next_dataset = (
                _to_entity_id(relationship)
                if _from_entity_id(relationship) == current_dataset
                else _from_entity_id(relationship)
            )
            if next_dataset not in materialized:
                next_table = pre_aggregated_tables.get(next_dataset)
                if next_table is None:
                    next_table = dataset_fns[next_dataset](backend)
                next_dataset_meta = _entity(catalog, next_dataset)
                versioning = next_dataset_meta.versioning
                mapping: dict[date, date] | None = None
                if isinstance(versioning, SnapshotVersioningIR):
                    next_table, version_meta, mapping = _resolve_snapshot_versioning(
                        catalog=catalog,
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
                            catalog=catalog,
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
                        catalog=catalog,
                        relationship=relationship,
                        current_entity=current_dataset,
                        extra_predicates=extra_predicates,
                    )
                    materialized[next_dataset] = widened_table
                    edge_metadata.append(
                        {
                            "relationship": _relationship_id(relationship),
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
                    anchor_expr = _field_fn(catalog, root_time_dimension.ref.id)(
                        widened_table
                    ).cast("date")
                    extra_predicates = [anchor_expr == next_table.anchor_date]
                pre_join_dataset = current_dataset
                widened_table, current_dataset = _join_table(
                    widened_table,
                    next_table,
                    catalog=catalog,
                    relationship=relationship,
                    current_entity=current_dataset,
                    extra_predicates=extra_predicates,
                    join_type=fanout_join_types.get(next_dataset, "left"),
                )
                materialized[next_dataset] = widened_table
            else:
                pre_join_dataset = current_dataset
                current_dataset = next_dataset
            edge_metadata.append(
                {
                    "relationship": _relationship_id(relationship),
                    "from_dataset": pre_join_dataset,
                    "to_dataset": next_dataset,
                    "join_safety": safety.value,
                    "join_type": fanout_join_types.get(next_dataset, "left"),
                }
            )
    if joined_where:
        widened_table = apply_slice_to_dataset(
            widened_table, joined_where, dataset_ir=dataset_irs[root]
        )

    planned_dimensions = [
        PlannedDimension(field=_planned_field(field), column=field.name)
        for field in resolved_fields.dimensions
    ]
    for planned_dimension in planned_dimensions:
        widened_table = widened_table.mutate(
            **{
                planned_dimension.column: _validate_field_expr(
                    _field_fn(catalog, planned_dimension.field.semantic_id)(widened_table),
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
            "time_fold": metric_ir.time_fold.label() if metric_ir.time_fold is not None else None,
            "status_time_dimension": metric_ir.status_time_dimension,
        },
        warnings=plan_warnings,
        datasource_name=datasource_name,
        status_time_dimension=metric_ir.status_time_dimension,
        time_fold=metric_ir.time_fold,
    )


def plan_observe(
    *,
    catalog: SemanticCatalog | None = None,
    session: Any,
    metric_ir: Any,
    dataset_irs: dict[str, Any],
    dataset_fns: dict[str, Any],
    dimensions: Any,
    where: Any,
    resolved_window: Any,
    time_dimension: Any,
    component_metric_irs: dict[str, Any] | None = None,
) -> ObservePlan:
    if catalog is None:
        catalog = session.catalog
    if metric_ir.metric_type != "derived":
        return plan_base_observe(
            catalog=catalog,
            session=session,
            metric_ir=metric_ir,
            dataset_irs=dataset_irs,
            dataset_fns=dataset_fns,
            dimensions=dimensions,
            where=where,
            resolved_window=resolved_window,
            time_dimension=time_dimension,
        )
    if _is_cumulative_metric(metric_ir):
        return _plan_cumulative_observe(
            catalog=catalog,
            session=session,
            metric_ir=metric_ir,
            dataset_irs=dataset_irs,
            dataset_fns=dataset_fns,
            dimensions=dimensions,
            where=where,
            resolved_window=resolved_window,
            time_dimension=time_dimension,
            component_metric_irs=component_metric_irs,
        )
    return _plan_derived_observe(
        catalog=catalog,
        session=session,
        metric_ir=metric_ir,
        dataset_irs=dataset_irs,
        dataset_fns=dataset_fns,
        dimensions=dimensions,
        where=where,
        resolved_window=resolved_window,
        time_dimension=time_dimension,
        component_metric_irs=component_metric_irs,
    )


def _component_dataset_adapters(
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
        if entity_id not in component_dataset_irs:
            raise_observe_planning_error(
                code="derived-shared-planner-unsupported",
                message=(
                    f"entity {entity_id!r} adapter not provided for component metric "
                    f"{component_ir.semantic_id!r}"
                ),
                candidates={"entity": entity_id},
                repair=[],
            )
        if entity_id not in component_dataset_fns:
            component_dataset_fns[entity_id] = component_dataset_irs[entity_id].fn
    return component_dataset_irs, component_dataset_fns


def _accumulate_unreachable_ref(
    exc: ObservePlanningError,
    component_id: str,
    *,
    dimensions: list[Any] | None,
    where: dict[Any, Any] | None,
    axes_acc: dict[str, list[str]],
    where_acc: dict[str, list[str]],
) -> None:
    """Classify a field-ref-not-found/ambiguous error as a missing axis or missing filter."""
    msg = exc.message or ""
    for dim in dimensions or []:
        dim_id = _input_ref_id(dim)
        if f"{dim_id!r}" in msg:
            axes_acc.setdefault(dim_id, []).append(component_id)
            return
    for raw_key in where or {}:
        key = _input_ref_id(raw_key)
        if f"{key!r}" in msg:
            where_acc.setdefault(key, []).append(component_id)
            return
    raise exc


def _accumulate_path_unreachable(
    exc: ObservePlanningError,
    component_id: str,
    *,
    dimensions: list[Any] | None,
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
        dim_id = _input_ref_id(dim)
        # The dimension id may be qualified (e.g. 'sales.country') or unqualified.
        # We match on the local name part.
        local_name = dim_id.rsplit(".", 1)[-1]
        if to_dataset is not None and local_name in to_dataset:
            axes_acc.setdefault(dim_id, []).append(component_id)
            return
    # Fallback: attribute to the first dimension if any
    for dim in dimensions or []:
        axes_acc.setdefault(_input_ref_id(dim), []).append(component_id)
        return
    # Try to match to a where filter
    for raw_key in where or {}:
        key = _input_ref_id(raw_key)
        where_acc.setdefault(key, []).append(component_id)
        return
    raise exc


def _raise_component_axis_unreachable(
    missing_map: dict[str, list[str]],
    component_plans: list[ComponentPlan],
    parent_dimensions: list[Any] | None,
) -> None:
    dim_id, components_missing = next(iter(missing_map.items()))
    resolved = []
    target = next((p for p in (parent_dimensions or []) if _input_ref_id(p) == dim_id), None)
    for cp in component_plans:
        for d in cp.base_plan.dimensions:
            if target is not None and d.column == _input_ref_id(target).rsplit(".", 1)[-1]:
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
    parent_dimensions: list[Any] | None,
) -> None:
    for dim in parent_dimensions or []:
        dim_id = _input_ref_id(dim)
        col = dim_id.rsplit(".", 1)[-1]
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
                message=f"Dimension {dim_id!r} resolves to different field ids across components.",
                candidates={
                    "dimension": dim_id,
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
        key = _input_ref_id(raw_key)
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


def _plan_cumulative_observe(
    *,
    catalog: SemanticCatalog,
    session: Any,
    metric_ir: Any,
    dataset_irs: dict[str, Any],
    dataset_fns: dict[str, Any],
    dimensions: list[Any] | None,
    where: dict[Any, Any] | None,
    resolved_window: Any | None,
    time_dimension: Any,
    component_metric_irs: dict[str, Any] | None,
) -> CumulativeObservePlan:
    """Plan a cumulative observe by delegating to the base metric's plan_base_observe.

    The cumulative metric's composition carries ``base`` (the metric to
    accumulate) and ``over`` (the time axis to accumulate along).  The base
    metric is planned via ``plan_base_observe`` using the cumulative's
    ``over`` as the time dimension when available.
    """
    component = metric_ir.composition
    base_ref = _ref_id(component.base)
    base_details = _metric(catalog, base_ref)
    base_ir = component_metric_irs.get(base_ref) if component_metric_irs is not None else None
    if base_ir is None:
        base_ir = _planned_metric(base_details)
    base_dataset_irs, base_dataset_fns = _component_dataset_adapters(
        base_ir,
        dataset_irs,
        dataset_fns,
    )
    # Use the cumulative's over axis as the time dimension for the base plan
    # when it is available; fall back to the caller-supplied time_dimension.
    cumulative_over = getattr(component, "over", None)
    # Resolve the real CumulativeComposition (carrying the anchor) from the
    # registry. metric_ir.composition here is the _MetricDetailsAdapter
    # composition, which defaults over=None and anchor='all_history'; the real
    # IR with the resolved anchor lives on the registry.
    resolved_composition = component
    if catalog._reg is not None:
        real_ir = catalog._reg.metrics.get(metric_ir.semantic_id)
        if real_ir is not None and real_ir.composition is not None:
            resolved_composition = real_ir.composition
            if cumulative_over is None:
                cumulative_over = getattr(resolved_composition, "over", None)
    base_time_dimension = cumulative_over or time_dimension
    base_plan = plan_base_observe(
        catalog=catalog,
        session=session,
        metric_ir=base_ir,
        dataset_irs=base_dataset_irs,
        dataset_fns=base_dataset_fns,
        dimensions=dimensions,
        where=where,
        resolved_window=resolved_window,
        time_dimension=base_time_dimension,
        allow_unqualified_outside_scope=True,
    )
    return CumulativeObservePlan(
        metric_ir=metric_ir,
        base_metric_ir=base_ir,
        base_plan=base_plan,
        over=cumulative_over,
        window=resolved_window,
        composition=resolved_composition,
    )


def _plan_derived_observe(
    *,
    catalog: SemanticCatalog,
    session: Any,
    metric_ir: Any,
    dataset_irs: dict[str, Any],
    dataset_fns: dict[str, Any],
    dimensions: list[Any] | None,
    where: dict[Any, Any] | None,
    resolved_window: Any | None,
    time_dimension: Any,
    component_metric_irs: dict[str, Any] | None,
) -> DerivedObservePlan:
    component_plans: list[ComponentPlan] = []
    component_unreachable_axes: dict[str, list[str]] = {}
    component_unreachable_where: dict[str, list[str]] = {}

    for role, component_id in metric_ir.composition.components.items():
        component_ref = _ref_id(component_id)
        component_details = _metric(catalog, component_ref)
        component_ir = (
            component_metric_irs.get(component_ref) if component_metric_irs is not None else None
        )
        if component_ir is None:
            component_ir = _planned_metric(component_details)
        if component_ir.metric_type == "derived" and not _is_cumulative_metric(component_ir):
            raise_observe_planning_error(
                code="nested-derived-unsupported",
                message=(
                    f"component metric {component_ref!r} is itself derived; "
                    "nested derived is unsupported."
                ),
                candidates={"metric": component_ref},
                repair=[],
            )
        component_dataset_irs, component_dataset_fns = _component_dataset_adapters(
            component_ir,
            dataset_irs,
            dataset_fns,
        )
        # When a component metric has a status_time_dimension, use it as the
        # time dimension for planning so the planner resolves the correct
        # time axis when the entity has multiple time dimensions.
        component_time_dimension = time_dimension
        if (
            getattr(component_ir, "status_time_dimension", None) is not None
            and component_time_dimension is None
        ):
            component_time_dimension = component_ir.status_time_dimension
        try:
            if _is_cumulative_metric(component_ir):
                base_plan: BaseObservePlan | CumulativeObservePlan = _plan_cumulative_observe(
                    catalog=catalog,
                    session=session,
                    metric_ir=component_ir,
                    dataset_irs=component_dataset_irs,
                    dataset_fns=component_dataset_fns,
                    dimensions=dimensions,
                    where=where,
                    resolved_window=resolved_window,
                    time_dimension=component_time_dimension,
                    component_metric_irs=component_metric_irs,
                )
            else:
                base_plan = plan_base_observe(
                    catalog=catalog,
                    session=session,
                    metric_ir=component_ir,
                    dataset_irs=component_dataset_irs,
                    dataset_fns=component_dataset_fns,
                    dimensions=dimensions,
                    where=where,
                    resolved_window=resolved_window,
                    time_dimension=component_time_dimension,
                    allow_unqualified_outside_scope=True,
                )
        except WindowInvalidError as _win_exc:
            # Component root has no time field; skip window for this component.
            if "has no @ms.time_dimension" not in (_win_exc.message or ""):
                raise
            if _is_cumulative_metric(component_ir):
                base_plan = _plan_cumulative_observe(
                    catalog=catalog,
                    session=session,
                    metric_ir=component_ir,
                    dataset_irs=component_dataset_irs,
                    dataset_fns=component_dataset_fns,
                    dimensions=dimensions,
                    where=where,
                    resolved_window=None,
                    time_dimension=component_time_dimension,
                    component_metric_irs=component_metric_irs,
                )
            else:
                base_plan = plan_base_observe(
                    catalog=catalog,
                    session=session,
                    metric_ir=component_ir,
                    dataset_irs=component_dataset_irs,
                    dataset_fns=component_dataset_fns,
                    dimensions=dimensions,
                    where=where,
                    resolved_window=None,
                    time_dimension=component_time_dimension,
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
