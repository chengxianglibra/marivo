"""Prepare closed original source relations for Frame attribution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import TYPE_CHECKING

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo._definition.expressions import BuiltinSampleCoordinate, PredicateExpression
from marivo._definition.types import IntervalCoordinate
from marivo._kernel.validation import is_instance
from marivo.analysis.compiler.query_stages import (
    read_components,
)

if TYPE_CHECKING:
    from marivo._definition.observation_time import ObservationPeriod
    from marivo._definition.types import CalendarCoordinate
    from marivo.analysis.binding.attribute_rules import AttributeRule
    from marivo.analysis.compiler.optimization import InvocationMemo
    from marivo.analysis.compiler.query_stages import QueryPlan
    from marivo.analysis.core.frame_operations import AttributeFrameValues

from marivo._definition.graph import computation_fingerprint
from marivo._definition.observations import ContributionBinding, ObservationBinding
from marivo._definition.relations import (
    AttributeKeys,
    PopulationDependency,
    PopulationProgram,
)
from marivo._definition.types import (
    AxisBinding,
    CalendarCoordinate,
    EntityIdentityRead,
    FieldRead,
    coordinate_read_keys,
    field_axis,
)
from marivo.analysis.binding.graph import BoundGraph
from marivo.analysis.binding.membership import membership_reads
from marivo.analysis.compiler.attribute_inputs import (
    attribute_parent_key,
    attribute_preparation_key,
    bundle_attribute_plan,
    input_failure,
)
from marivo.analysis.compiler.calendar import bind_calendar
from marivo.analysis.compiler.observation import calendar_field, reduction_input
from marivo.analysis.compiler.populations import frame_key_groups
from marivo.analysis.compiler.predicate import lower_condition
from marivo.analysis.compiler.query_planning import plan_query
from marivo.analysis.compiler.query_program import FrameParameter
from marivo.analysis.compiler.query_stages import (
    SourceField,
    SourceReadStage,
    SourceSchema,
)
from marivo.analysis.materialization.prepared_fields import PreparedFields
from marivo.analysis.materialization.prepared_sources import PreparedSources
from marivo.analysis.materialization.query_preparation import (
    prepare_fields,
)
from marivo.analysis.materialization.source_qualification import qualify_original_source
from marivo.analysis.materialization.source_types import (
    _admitted,
)
from marivo.analysis.model.data_types import (
    DurationDataType,
)
from marivo.datasource.spi import (
    TableSourceIR,
)
from marivo.semantic.spi import (
    CompiledSemanticState,
    normalize_target_entity,
)


def _bind_attribute_calendar(
    table: ir.Table,
    columns: dict[str, str],
    time_column: str,
    calendar: CalendarCoordinate | IntervalCoordinate,
    window_key: int | datetime | None,
    slot: str,
    name: str,
) -> ir.Table:

    if window_key is not None:
        value = ibis.literal(
            window_key,
            type="timestamp('UTC', 6)" if isinstance(window_key, datetime) else "int64",
        )
        return table.mutate(**{name: value})
    if calendar_field(slot) in columns:
        return table.mutate(**{name: table[columns[calendar_field(slot)]]})
    return bind_calendar(table, table[columns[time_column]], calendar, name)


def prepare_attribute_endpoint(
    prepared: PreparedSources,
    graph: BoundGraph,
    state: CompiledSemanticState,
    mapping: AttributeFrameValues,
    side: int,
    rules: tuple[tuple[str, AttributeRule], ...],
    *,
    memo: InvocationMemo,
) -> tuple[PreparedSources, QueryPlan, frozenset[str]]:
    """Prepare and qualify this captured endpoint's original contribution inputs."""
    positions = mapping.left_parents if side == 0 else mapping.right_parents
    prepared, scope_columns, parent_checks, grouping_roots = prepare_attribute_sources(
        prepared, graph, state, len(positions)
    )
    original = plan_query(graph, prepared.schemas, memo=memo, require_original=True)
    reads = tuple(
        stage
        for stage in original.stages
        if isinstance(stage, SourceReadStage) and read_components(stage) is not None
    )
    for root in dict.fromkeys(stage.source_id for stage in reads):
        group = tuple(stage for stage in reads if stage.source_id == root)
        owner = prepared.sources.get(root, prepared.source)
        relations = (
            tuple(bound.relation for bound in prepared.bound.values())
            if owner.provider.query_policy.key_fusion == "local"
            else (prepared.relations[group[0].stage_id],)
        )
        for relation in relations:
            issue = qualify_original_source(prepared, group, relation, attribute=True)
            if issue is not None:
                input_failure("unqualified_contribution_route", issue)
    plan = bundle_attribute_plan(
        original,
        rules,
        positions,
        scope_columns,
        mapping.left_projection if side == 0 else mapping.right_projection,
        "left" if side == 0 else "right",
        parent_checks,
    )
    return prepared, plan, grouping_roots


def _attribute_scope_groups(
    root: str, leaves: list[ContributionBinding]
) -> tuple[list[ContributionBinding], ...]:
    scope_groups: dict[
        tuple[ObservationPeriod | None, int | datetime | None], list[ContributionBinding]
    ] = {}
    for leaf in leaves:
        scope_groups.setdefault((leaf.period, leaf.window_key), []).append(leaf)
    first_root = leaves[0]
    if any(
        (
            leaf.time,
            leaf.intrinsic_conditions,
            leaf.common_condition,
            leaf.coordinates,
            leaf.memberships,
            leaf.population_reads,
            leaf.calendars,
            leaf.sample_axes,
        )
        != (
            first_root.time,
            first_root.intrinsic_conditions,
            first_root.common_condition,
            first_root.coordinates,
            first_root.memberships,
            first_root.population_reads,
            first_root.calendars,
            first_root.sample_axes,
        )
        for leaf in leaves
    ):
        input_failure(
            "unqualified_contribution_route", f"{root}: incompatible non-temporal source scopes"
        )
    return tuple(scope_groups.values())


@dataclass(frozen=True, slots=True)
class _AttributeFiberScope:
    scoped: PreparedFields
    classified: PreparedFields
    parent_slots: tuple[tuple[str, str], ...]
    parent_reads: tuple[FieldRead | EntityIdentityRead, ...]
    has_memberships: bool
    shares_observed_rows: bool


def _prepare_fiber_scope(
    leaves: list[ContributionBinding],
    root: str,
    prepared: PreparedSources,
    observation: ObservationBinding,
    state: CompiledSemanticState,
    parent_count: int,
) -> _AttributeFiberScope:
    first = leaves[0]

    if first.sample_axes:
        input_failure(
            "missing_component_proof", f"{root}: unproved calendar/window/sample sequence"
        )
    merged = replace(
        first, reads=tuple(dict.fromkeys(read for leaf in leaves for read in leaf.reads))
    )
    parent_axes = (
        ()
        if observation.coordinates.parent_scope is None
        else observation.coordinates.parent_scope.axes
    )
    scope = observation.coordinates.parent_scope
    fixed_parent_axes = (
        tuple(axis for axis, _ in scope.root.fixed)
        if isinstance(scope, PopulationProgram) and isinstance(scope.root, AttributeKeys)
        else ()
    )
    parent_memberships = tuple(
        rule
        for rule in first.memberships
        if all(field_axis(read) in (*parent_axes, *fixed_parent_axes) for read in rule.fields)
    )
    new_memberships = tuple(
        binding for binding in first.memberships if binding not in parent_memberships
    )
    member_reads = {
        read for binding in new_memberships for read in membership_reads(binding, first.root)
    }
    if any(read in member_reads for read in merged.reads):
        input_failure(
            "unqualified_contribution_route",
            f"{root}: a new membership field is also an input/filter value",
        )
    parent_reads = first.coordinates[: parent_count - len(first.calendars)]
    baseline = replace(
        merged,
        reads=tuple(read for read in merged.reads if read not in member_reads),
        coordinates=parent_reads,
        memberships=parent_memberships,
    )
    scoped = prepare_fields(baseline, prepared.entities, state)
    classified = prepare_fields(merged, prepared.entities, state)
    scoped_map, assigned_map = dict(scoped.columns), dict(classified.columns)
    parent_paths = tuple(path for read in parent_reads for path in coordinate_read_keys(read))
    parent_calendar = first.calendars[0][1] if first.calendars else None
    assert not isinstance(parent_calendar, BuiltinSampleCoordinate)
    reduction = reduction_input(first, "attribute.value", parent_calendar)
    parent_slots = tuple(
        (slot, scoped_map[path]) for slot, path in reduction.axes if path in parent_paths
    )
    for slot, calendar in first.calendars:
        assert reduction.time_column is not None
        assert not isinstance(calendar, BuiltinSampleCoordinate)
        name = "__attribute_calendar_" + slot.replace(".", "_")

        scoped = replace(
            scoped,
            table=_bind_attribute_calendar(
                scoped.table,
                scoped_map,
                reduction.time_column,
                calendar,
                first.window_key,
                slot,
                name,
            ),
        )
        classified = replace(
            classified,
            table=_bind_attribute_calendar(
                classified.table,
                assigned_map,
                reduction.time_column,
                calendar,
                first.window_key,
                slot,
                name,
            ),
        )
        parent_slots = (*parent_slots, (slot, name))
    parent_slots = tuple(sorted(parent_slots, key=lambda item: int(item[0].split(".")[1])))

    return _AttributeFiberScope(
        scoped,
        classified,
        parent_slots,
        parent_reads,
        bool(new_memberships),
        _shares_observed_rows(first, classified, parent_reads),
    )


def _shares_observed_rows(
    first: ContributionBinding,
    classified: PreparedFields,
    parent_reads: tuple[FieldRead | EntityIdentityRead, ...],
) -> bool:
    # A newly projected direct root field or root identity cannot change the
    # admitted rows. Both scopes otherwise prepare the same reads, conditions,
    # owner, contribution clock and selection. Path/version/membership additions
    # cannot use this projection proof.
    classifications = first.coordinates[len(parent_reads) :]
    columns = dict(classified.columns)
    original_columns = dict(classified.originals)
    return (
        bool(classifications)
        and not first.memberships
        and all(read in parent_reads for _, read in first.population_reads)
        and not first.calendars
        and all(
            read.entity == first.root
            and not read.path
            and read.at is None
            and read.source_at is None
            and not read.step_ats
            and (not isinstance(read, FieldRead) or not read.versioned)
            and all(
                (value := original_columns.get(columns[path])) is not None
                and isinstance(value.op(), ops.Field)
                for path in coordinate_read_keys(read)
            )
            for read in classifications
        )
    )


def _select_attribute_parent(
    prepared: PreparedSources,
    graph: BoundGraph,
    parent_scope: PopulationDependency | None,
    scope: _AttributeFiberScope,
    first: ContributionBinding,
    source_predicates: tuple[PredicateExpression, ...],
) -> tuple[ir.Table, ir.Table | None, list[tuple[str, str]]]:
    scoped = scope.scoped
    scoped_map = dict(scoped.columns)
    parent_reads = scope.parent_reads
    parent_slots = scope.parent_slots
    base = scoped.table
    base_fields = {path: base[column] for path, column in scoped.columns}
    if source_predicates:
        base = base.filter(
            *(lower_condition(predicate, base_fields).value for predicate in source_predicates)
        )
    # Parent selection is a typed complete-key relation. Temporal keys are
    # not legal ordinary predicates and must retain their joint fibers here.
    parameter_relation: ir.Table | None = None
    parent_pairs: list[tuple[str, str]] = []
    if parent_scope is not None:
        assert isinstance(parent_scope, PopulationProgram) and isinstance(
            parent_scope.root, AttributeKeys
        )
        parameter_relation = prepared.entities[
            "population-parameter:" + computation_fingerprint(parent_scope)
        ]
        parent_predicates: list[ir.BooleanValue] = []
        source_columns: tuple[str, ...]
        for axis, group in zip(parent_scope.axes, frame_key_groups(parent_scope.root), strict=True):
            if isinstance(axis, (CalendarCoordinate, IntervalCoordinate)):
                slot = next(slot for slot, calendar in first.calendars if calendar == axis)
                source_columns = (dict(parent_slots)[slot],)
            else:
                ordinary_axes = tuple(
                    a for a in graph.signature.axes if isinstance(a, AxisBinding)
                )[: len(parent_reads)]
                read = parent_reads[ordinary_axes.index(axis)]
                source_columns = tuple(scoped_map[path] for path in coordinate_read_keys(read))
            parent_pairs.extend(zip(source_columns, group, strict=True))
            parent_predicates.extend(
                base[column].identical_to(parameter_relation[name])
                for column, name in zip(source_columns, group, strict=True)
            )
        base = (
            base.semi_join(parameter_relation, parent_predicates)
            if parent_predicates
            else base.filter(parameter_relation.count() > 0)
        )
    return base, parameter_relation, parent_pairs


def _join_fine_classifications(
    scope: _AttributeFiberScope,
    base: ir.Table,
    parameter_relation: ir.Table | None,
    parent_pairs: list[tuple[str, str]],
    source_predicates: tuple[PredicateExpression, ...],
    root: str,
    state: CompiledSemanticState,
    fiber: int,
) -> tuple[ir.Table, ir.Table, dict[str, str]]:
    scoped, classified = scope.scoped, scope.classified
    scoped_map, assigned_map = dict(scoped.columns), dict(classified.columns)
    parent_slots = scope.parent_slots
    mapping = dict(scoped_map)
    classification_columns = tuple(
        (path, name) for path, name in assigned_map.items() if path not in mapping
    )
    if scope.has_memberships:
        fine = classified.table.mutate(__attribute_assigned=ibis.literal(True))
        renamed = {name: f"__attribute_fine_{index}" for index, name in enumerate(fine.columns)}
        fine = fine.rename(**{new: old for old, new in renamed.items()})
        identity = (
            normalize_target_entity(state.registry, root).version_row_key
            or normalize_target_entity(state.registry, root).primary_key
        )
        predicates = [base[name].identical_to(fine[renamed[name]]) for name in identity]
        predicates.extend(
            base[column].identical_to(fine[renamed[column]]) for _, column in parent_slots
        )
        joined = base.left_join(fine, predicates)
        mapping = dict(scoped_map)
        projections = {name: base[name] for name in base.columns}
        classification_columns = tuple(
            (path, name) for path, name in assigned_map.items() if path not in mapping
        )
        for path, source_name in classification_columns:
            name = f"__attribute_member_{len(mapping)}"
            projections[name] = fine[renamed[source_name]]
            mapping[path] = name
        projections["__attribute_assigned"] = fine[renamed["__attribute_assigned"]].fill_null(False)
        raw = joined.select(**projections).mutate(__attribute_fiber=ibis.literal(fiber))
    else:
        # Ordinary scalar classification trusts identity, relationship and
        # version declarations. Apply the same scope without a root self-join.
        fine = classified.table
        fine_fields = {path: fine[column] for path, column in classified.columns}
        if source_predicates:
            fine = fine.filter(*(lower_condition(p, fine_fields).value for p in source_predicates))
        if parameter_relation is not None:
            fine_parent_columns = {column: assigned_map[path] for path, column in scoped.columns}
            fine_predicates = [
                fine[fine_parent_columns.get(column, column)].identical_to(parameter_relation[name])
                for column, name in parent_pairs
            ]
            fine = (
                fine.semi_join(parameter_relation, fine_predicates)
                if fine_predicates
                else fine.filter(parameter_relation.count() > 0)
            )
        for path, _ in classification_columns:
            mapping[path] = f"__attribute_member_{len(mapping)}"
        raw = fine.select(
            *(
                fine[name]
                for name in scoped.table.columns
                if name not in {v for _, v in scoped.columns}
            ),
            **{name: fine[assigned_map[path]] for path, name in mapping.items()},
        ).mutate(__attribute_fiber=ibis.literal(fiber))
    parent = base.mutate(
        **{
            mapping[path]: ibis.null().cast(classified.table[name].type().copy(nullable=True))
            for path, name in classification_columns
        },
        __attribute_fiber=ibis.literal(fiber),
    )
    return raw, parent, mapping


def _attribute_field_types(
    root: str, prepared: PreparedSources, raw: ir.Table, mapping: Mapping[str, str]
) -> tuple[SourceField, ...]:
    physical = (ops.DatabaseTable, ops.UnboundTable, ops.InMemoryTable, ops.SQLQueryResult)
    allowed = {
        id(leaf) for bound in prepared.bound.values() for leaf in bound.relation.op().find(physical)
    }
    parameters = tuple(
        parameter
        for schema in prepared.schemas
        for parameter in schema.parameters
        if is_instance(parameter, FrameParameter)
    )
    for parameter in parameters:
        relation = prepared.entities.get(
            "population-parameter:" + computation_fingerprint(parameter.definition)
        )
        if relation is not None:
            allowed.add(id(relation.op()))
    if any(id(leaf) not in allowed for leaf in raw.op().find(physical)):
        input_failure(
            "unqualified_contribution_route",
            f"{root}: an unprepared physical or parameter leaf",
        )
    schema = raw.schema().to_pyarrow()
    original_types = {
        field.field: field.dtype
        for item in prepared.schemas
        if item.source_id == root
        for field in item.fields
    }
    fields = tuple(
        replace(_admitted(schema, path, name), dtype=original_types[path])
        if isinstance(original_types.get(path), DurationDataType)
        else _admitted(schema, path, name)
        for path, name in mapping.items()
    )
    return fields


@dataclass(frozen=True, slots=True)
class _AttributeFiber:
    values: ir.Table
    parent: ir.Table
    checks: ir.Table
    parent_checks: ir.Table
    fields: tuple[SourceField, ...]
    scope: _AttributeFiberScope


def _prepare_attribute_fiber(
    fiber: int,
    leaves: list[ContributionBinding],
    root: str,
    prepared: PreparedSources,
    graph: BoundGraph,
    observation: ObservationBinding,
    state: CompiledSemanticState,
    parent_count: int,
) -> _AttributeFiber:
    scope = _prepare_fiber_scope(leaves, root, prepared, observation, state, parent_count)
    first = leaves[0]
    source_predicates = (
        *first.intrinsic_conditions,
        *((first.common_condition,) if first.common_condition is not None else ()),
    )
    base, parameter, parent_pairs = _select_attribute_parent(
        prepared, graph, observation.coordinates.parent_scope, scope, first, source_predicates
    )
    raw, parent, mapping = _join_fine_classifications(
        scope, base, parameter, parent_pairs, source_predicates, root, state, fiber
    )
    fields = _attribute_field_types(root, prepared, raw, mapping)
    scoped, classified = scope.scoped, scope.classified
    assigned_map = dict(classified.columns)
    parent_slots = scope.parent_slots
    fine_preparation = classified.table.select(
        **{name: classified.table[assigned_map[path]] for path, name in mapping.items()},
        **{
            name: classified.table[name] for _, name in parent_slots if name not in mapping.values()
        },
        **{name: classified.table[name] for name, _, _ in classified.checks},
    )
    return _AttributeFiber(
        raw,
        parent,
        fine_preparation.mutate(__attribute_fiber=ibis.literal(fiber)),
        scoped.table.mutate(__attribute_fiber=ibis.literal(fiber)),
        fields,
        scope,
    )


def _replace_attribute_schema(
    schemas: list[SourceSchema],
    prepared: PreparedSources,
    observation: ObservationBinding,
    root: str,
    leaf: ContributionBinding,
    fiber: _AttributeFiber,
) -> None:
    for index, item in enumerate(schemas):
        if item.source_id == root and item.occurrence == leaf.occurrence:
            schemas[index] = replace(
                item,
                fields=fiber.fields,
                preparation_checks=fiber.scope.classified.checks,
                parameters=tuple(
                    dict.fromkeys(
                        (
                            *item.parameters,
                            *(
                                parameter
                                for schema in prepared.schemas
                                for parameter in schema.parameters
                                if observation.coordinates.parent_scope is not None
                                and parameter.definition == observation.coordinates.parent_scope
                            ),
                        )
                    )
                ),
            )


def _union_fibers(fibers: tuple[ir.Table, ...]) -> ir.Table:
    return fibers[0].union(*fibers[1:], distinct=False) if len(fibers) > 1 else fibers[0]


def prepare_attribute_sources(
    prepared: PreparedSources,
    graph: BoundGraph,
    state: CompiledSemanticState,
    parent_count: int,
) -> tuple[
    PreparedSources,
    dict[str, tuple[tuple[str, str], ...]],
    dict[str, tuple[tuple[str, str, str], ...]],
    frozenset[str],
]:
    """Prepare independent unclassified parent and classified fine root streams."""

    observation = graph.signature.observation
    assert observation is not None
    if any(not isinstance(bound.source, TableSourceIR) for bound in prepared.bound.values()):
        input_failure(
            "unqualified_contribution_route",
            "Attribute requires native physical tables",
        )
    # ContributionBinding remains the only source scope authority.
    contributions: dict[str, list[ContributionBinding]] = {}
    for contribution in observation.contributions:
        contributions.setdefault(contribution.root.ref.path, []).append(contribution)
    relations = dict(prepared.relations)
    schemas = list(prepared.schemas)
    scoped_columns: dict[str, tuple[tuple[str, str], ...]] = {}
    parent_checks: dict[str, tuple[tuple[str, str, str], ...]] = {}
    grouping_roots: set[str] = set()
    for root, leaves in contributions.items():
        fibers = []
        for index, group in enumerate(_attribute_scope_groups(root, leaves)):
            fiber = _prepare_attribute_fiber(
                index, group, root, prepared, graph, observation, state, parent_count
            )
            for leaf in group:
                _replace_attribute_schema(schemas, prepared, observation, root, leaf, fiber)
            fibers.append(fiber)
        scoped_columns[root] = fibers[-1].scope.parent_slots
        parent_checks[root] = fibers[-1].scope.scoped.checks
        if len(fibers) == 1 and fibers[0].scope.shares_observed_rows:
            grouping_roots.add(root)
        raw = _union_fibers(tuple(fiber.values for fiber in fibers))
        parent = _union_fibers(tuple(fiber.parent for fiber in fibers))
        checks = _union_fibers(tuple(fiber.checks for fiber in fibers))
        parent_preparation = _union_fibers(tuple(fiber.parent_checks for fiber in fibers))
        for leaf in leaves:
            stage_id = "read:" + ".".join(map(str, leaf.occurrence))
            relations[stage_id] = raw
            relations[attribute_parent_key(stage_id)] = parent
            relations[attribute_preparation_key(stage_id)] = checks
            relations[attribute_preparation_key(attribute_parent_key(stage_id))] = (
                parent_preparation
            )
    return (
        replace(prepared, schemas=tuple(schemas), relations=relations),
        scoped_columns,
        parent_checks,
        frozenset(grouping_roots),
    )
