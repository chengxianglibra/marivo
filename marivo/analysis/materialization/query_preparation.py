"""Schema-only preparation of occurrence-specific fields and scalar relationships."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo._definition.expressions import (
    PredicateExpression,
    RowExpression,
    RowField,
)
from marivo._definition.graph import walk
from marivo._definition.observation_time import ContributionClockBinding, ObservationPeriod
from marivo._definition.observations import CalendarGrouping, SampleAxis, SampleIntervalRanges
from marivo._definition.relations import MembershipBinding, PopulationContains, PopulationDependency
from marivo._definition.types import (
    AxisBinding,
    EntityIdentityRead,
    ExactRef,
    FieldRead,
    RowAttributeTime,
    TimeInstantFieldRead,
    coordinate_read_keys,
    field_read_key,
)
from marivo._kernel.refs import SemanticKind
from marivo.analysis.binding.coordinates import identity_projection
from marivo.analysis.binding.membership import membership_reads
from marivo.analysis.binding.observe import ContributionBinding
from marivo.analysis.compiler.observation import reduction_input
from marivo.analysis.compiler.predicate import lower_condition
from marivo.analysis.compiler.source_time import (
    entity_engine,
    source_time_predicate,
)
from marivo.analysis.materialization.membership_preparation import prepare_memberships
from marivo.analysis.materialization.population_condition import (
    prepare_condition_memberships,
    requires_path_witness,
)
from marivo.analysis.materialization.population_domains import PreparedPopulation
from marivo.analysis.materialization.population_preparation import bind_population
from marivo.analysis.materialization.predicate_time_ranges import _required_time_ranges
from marivo.analysis.materialization.prepared_fields import PreparedFields
from marivo.analysis.materialization.query_calendar_projection import (
    QueryCalendarProjection,
)
from marivo.analysis.materialization.query_field_scope import QueryFieldScope
from marivo.analysis.materialization.query_scalar_projection import (
    ScalarProjection,
    ScalarRead,
    project_scalar_fields,
)
from marivo.analysis.model.domain_captures import DomainPreparationError
from marivo.analysis.model.time_authority import SourceTimeAuthority
from marivo.analysis.model.time_facts import (
    FrameSourceTime,
    merge_source_times,
)
from marivo.semantic.spi import (
    CompiledSemanticState,
    HourPrefixParse,
    Registry,
    normalize_target_entity,
    normalize_target_relationship,
)


def prepare_population(
    definition: PopulationDependency,
    entities: Mapping[str, ir.Table],
    state: CompiledSemanticState,
    *,
    source_times: list[FrameSourceTime] | None = None,
    consumption_timezone: str = "UTC",
    prepared: dict[str, PreparedPopulation] | None = None,
) -> PreparedPopulation:
    """Bind Population and field preparation within the same source scope."""
    return bind_population(
        definition,
        entities,
        state,
        prepare_fields,
        source_times=source_times,
        consumption_timezone=consumption_timezone,
        prepared=prepared,
    )


@dataclass(frozen=True, slots=True)
class SourceInputContract:
    """Explicit consumed source binding, independent of reduction occurrences."""

    source_owner: int
    datasource: str
    source_fingerprint: str
    root: ExactRef
    reads: tuple[FieldRead, ...]
    coordinates: tuple[FieldRead | EntityIdentityRead, ...]
    time: ContributionClockBinding | None
    period: ObservationPeriod | None
    predicates: tuple[PredicateExpression, ...]
    memberships: tuple[MembershipBinding, ...]
    population_reads: tuple[tuple[AxisBinding, FieldRead | EntityIdentityRead], ...]
    window_key: int | datetime | None
    sample_axes: tuple[SampleAxis, ...]
    calendars: tuple[tuple[str, CalendarGrouping], ...]
    time_authorities: tuple[SourceTimeAuthority, ...]
    sample_calendar_reads: tuple[tuple[str, FieldRead], ...] = ()
    window_coordinates: tuple[tuple[str, int | datetime], ...] = ()
    sample_time_slots: tuple[str, ...] = ()
    sample_interval_ranges: tuple[tuple[str, SampleIntervalRanges], ...] = ()

    @classmethod
    def capture(
        cls,
        contribution: ContributionBinding,
        *,
        source_owner: int,
        datasource: str,
        source_fingerprint: str,
    ) -> SourceInputContract:
        """Capture a row domain and its requested projection before preparation."""
        return cls(
            source_owner,
            datasource,
            source_fingerprint,
            contribution.root,
            contribution.reads,
            contribution.coordinates,
            contribution.time,
            contribution.period,
            (
                *contribution.intrinsic_conditions,
                *(
                    (contribution.common_condition,)
                    if contribution.common_condition is not None
                    else ()
                ),
            ),
            contribution.memberships,
            contribution.population_reads,
            contribution.window_key,
            contribution.sample_axes,
            contribution.calendars,
            (),
            sample_calendar_reads=contribution.sample_calendar_reads,
            window_coordinates=contribution.window_coordinates,
            sample_time_slots=contribution.sample_time_slots,
            sample_interval_ranges=contribution.sample_interval_ranges,
        )


def share_source_projections(
    contributions: tuple[ContributionBinding, ...], contracts: tuple[SourceInputContract, ...]
) -> tuple[ContributionBinding, ...]:
    """Union root projections while preserving every relationship read and row domain."""
    groups: dict[SourceInputContract, list[int]] = {}
    for index, (contribution, contract) in enumerate(zip(contributions, contracts, strict=True)):
        if contribution.window_key is not None or any(
            not isinstance(axis, AxisBinding)
            or axis.field is None
            or axis.field.ref.kind is not SemanticKind.DIMENSION
            for axis in contribution.sample_axes
        ):
            continue
        # Only direct root fields are projections. Path/version reads retain
        # their exact identity because adding a join can change contributions.
        domain = replace(
            contract,
            reads=tuple(
                read
                for read in contract.reads
                if read.entity != contract.root or read.path or read.at is not None
            ),
        )
        groups.setdefault(domain, []).append(index)
    result = list(contributions)
    for indexes in groups.values():
        reads = tuple(
            dict.fromkeys(read for index in indexes for read in contributions[index].reads)
        )
        for index in indexes:
            result[index] = replace(result[index], reads=reads)
    return tuple(result)


def prepare_fields(
    contribution: ContributionBinding,
    entities: Mapping[str, ir.Table],
    state: CompiledSemanticState,
    *,
    root_table: ir.Table | None = None,
    consumption_timezone: str = "UTC",
) -> PreparedFields:
    """Prepare expressions and declared many-to-one joins without reading values."""

    scalar_reads = _validate_scalar_sources(contribution, state.registry)
    table = entities[contribution.root.ref.path].view() if root_table is None else root_table
    reserved = {name for entity in entities.values() for name in entity.columns}
    reserved.update(table.columns)
    scope = QueryFieldScope(contribution, state, consumption_timezone, reserved)
    calendar = QueryCalendarProjection.capture(scope)
    table = calendar.filter_root(table)
    predicate_conditions = (
        *contribution.intrinsic_conditions,
        *((contribution.common_condition,) if contribution.common_condition is not None else ()),
    )
    if any(isinstance(read.at, RowAttributeTime) for read in scalar_reads):
        table = _filter_before_version_joins(table, scope, entities, predicate_conditions)
    physical_axes = reduction_input(contribution, "", None).axes
    projected = project_scalar_fields(table, scope, entities, calendar)
    _prepare_memberships(projected, scope, entities, physical_axes)
    if predicate_conditions:
        if projected.checks:
            _scope_projection_checks(projected, contribution, predicate_conditions)
        else:
            _filter_encoded_ranges(projected, scope, predicate_conditions)
    return PreparedFields(
        projected.table,
        tuple(projected.columns),
        tuple(projected.checks),
        tuple(projected.originals),
        tuple(scope.dependencies),
        tuple(item[2] for item in scope.time_encodings.values()),
        merge_source_times(tuple(scope.source_times)),
    )


def _validate_scalar_sources(
    contribution: ContributionBinding, registry: Registry
) -> tuple[ScalarRead, ...]:
    root_datasource = registry.entities[contribution.root.ref.path].datasource
    scalar_reads = (
        *contribution.reads,
        *contribution.coordinates,
    )

    scalar_reads = (
        *scalar_reads,
        *(
            read
            for _, read in contribution.population_reads
            if not requires_path_witness(read)
            and (
                not isinstance(read, EntityIdentityRead)
                or identity_projection(read, contribution.root, registry) is None
            )
        ),
    )
    for read in scalar_reads:
        if registry.entities[read.entity.ref.path].datasource != root_datasource:
            raise DomainPreparationError(
                "query.scalar_datasource",
                "prepare",
                "scalar relationship endpoints on one datasource",
                read.entity.ref.path,
                "Read and compute the other datasource independently, then call session.population_from(frame, on=...) with explicit complete identity axes.",
            )
        for path_ref in read.path:
            scalar_relationship = normalize_target_relationship(registry, path_ref.ref.path)
            if any(
                registry.entities[endpoint.path].datasource != root_datasource
                for endpoint in (
                    scalar_relationship.from_entity_ref,
                    scalar_relationship.to_entity_ref,
                )
            ):
                raise DomainPreparationError(
                    "query.scalar_datasource",
                    "prepare",
                    "scalar relationship endpoints on one datasource",
                    read.entity.ref.path,
                    "Compute an independent Frame and use session.population_from(frame, on=...) across datasources instead of a scalar relationship read.",
                )
    return scalar_reads


def _filter_before_version_joins(
    table: ir.Table,
    scope: QueryFieldScope,
    entities: Mapping[str, ir.Table],
    predicate_conditions: tuple[PredicateExpression, ...],
) -> ir.Table:
    contribution = scope.contribution
    # Split only top-level AND. An OR containing a target label remains
    # intact. Keep the full condition downstream as the authoritative filter.
    for condition in predicate_conditions:
        if any(isinstance(leaf, PopulationContains) for leaf in condition.inputs):
            continue
        fields = {}
        for node in walk((condition,), resolve=lambda _: None).order:
            if isinstance(node, RowField) and node.read is not None:
                read = node.read
                native = read.entity == contribution.root and not read.path and read.at is None
                relation = table if native else entities[read.entity.ref.path].view()
                fields[field_read_key(read)] = scope.value(
                    read.field.ref.path,
                    relation,
                    instant_read=isinstance(read, TimeInstantFieldRead),
                )
        lowered = lower_condition(condition, fields)
        if lowered.checks:
            continue
        pending = [lowered.value.op()]
        conjuncts: list[ir.BooleanValue] = []
        while pending:
            atom = pending.pop()
            if isinstance(atom, ops.And):
                pending.extend((atom.left, atom.right))
            elif atom.relations <= {table.op()}:
                expression = atom.to_expr()
                assert isinstance(expression, ir.BooleanValue)
                conjuncts.append(expression)
        if conjuncts:
            table = table.filter(*conjuncts)
    return table


def _prepare_memberships(
    projected: ScalarProjection,
    scope: QueryFieldScope,
    entities: Mapping[str, ir.Table],
    physical_axes: tuple[tuple[str, str], ...],
) -> None:
    contribution, state = scope.contribution, scope.state
    table, columns, originals = projected.table, projected.columns, projected.originals
    outer_reads = contribution.coordinates[: contribution.output_axis_count]
    sample_positions = {}
    for binding in contribution.memberships:
        allocated = membership_reads(binding, contribution.root)
        if any(read in outer_reads for read in allocated):
            continue
        paths = {path for read in allocated for path in coordinate_read_keys(read)}
        positions = tuple(int(slot.split(".")[1]) for slot, path in physical_axes if path in paths)
        # A fixed parent membership can survive only in the source condition.
        # It still owns allocation, but has no remaining sample/output axis.
        if positions:
            sample_positions[binding] = min(positions)
    members = prepare_memberships(
        table,
        contribution.root,
        contribution.memberships,
        entities,
        state,
        scope.value,
        scope.unique,
        sample_positions=sample_positions,
        source_times=scope.source_times,
    )
    table = members.table
    columns.extend(members.columns)
    originals.extend(members.originals)
    scope.dependencies.extend(members.dependencies)

    prepared_memberships = prepare_condition_memberships(
        table,
        contribution,
        dict(columns),
        entities,
        state,
        field_value=scope.value,
        prepare_domain=lambda domain: prepare_population(domain, entities, state),
        source_times=scope.source_times,
    )
    table = prepared_memberships[0]
    columns.extend(prepared_memberships[1])
    originals.extend((slot, table[slot]) for _, slot in prepared_memberships[1])
    projected.table = table


def _filter_encoded_ranges(
    projected: ScalarProjection,
    scope: QueryFieldScope,
    predicate_conditions: tuple[PredicateExpression, ...],
) -> None:
    table, columns = projected.table, projected.columns
    contribution, registry = scope.contribution, scope.registry
    fields = {key: table[slot] for key, slot in columns}
    # A check on any condition owns the unfiltered source domain. Pushing a
    # different condition's range first could hide its domain errors.
    eligible = (
        ()
        if any(lower_condition(condition, fields).checks for condition in predicate_conditions)
        else predicate_conditions
    )
    guards: list[ir.BooleanValue] = []
    for read, (start, end) in _required_time_ranges(eligible).items():
        if (
            read.entity != contribution.root
            or read.path
            or read.at is not None
            or not isinstance(registry.dimensions[read.field.ref.path].parse, HourPrefixParse)
        ):
            continue
        scope.value(read.field.ref.path, table, instant_read=True)
        encoded_raw, encoded_axis, encoded_authority, encoded_prefix, encoded_prefix_axis = (
            scope.time_encodings[read.field.ref.path]
        )
        filtered = source_time_predicate(
            encoded_raw,
            encoded_axis,
            encoded_authority,
            start=start,
            end=end,
            prefix=encoded_prefix,
            prefix_axis=encoded_prefix_axis,
            engine=entity_engine(
                registry, normalize_target_entity(registry, encoded_axis.entity_ref.path)
            ),
            use="filter",
        )
        if filtered is not None:
            guards.append(filtered)
    if guards:
        # Keep the original comparisons as the authoritative condition.
        table = table.filter(*guards)
    projected.table = table


def _scope_projection_checks(
    projected: ScalarProjection,
    contribution: ContributionBinding,
    predicate_conditions: tuple[PredicateExpression, ...],
) -> None:
    table, columns, checks = projected.table, projected.columns, projected.checks
    # Condition inputs must be valid on the original domain. Output and
    # reduction-only scalar lookups retain their selected-contribution scope.
    condition_keys = {
        field_read_key(node.read)
        for condition in predicate_conditions
        for leaf in condition.inputs
        if isinstance(leaf, RowExpression)
        for node in walk((leaf,), resolve=lambda _: None).order
        if isinstance(node, RowField) and node.read is not None
    }
    condition_keys.update(
        key for _, read in contribution.population_reads for key in coordinate_read_keys(read)
    )
    condition_keys.update(slot for key, slot in columns if key in condition_keys)
    fields = {key: table[slot] for key, slot in columns}
    selected = ibis.and_(
        *(lower_condition(condition, fields).value for condition in predicate_conditions)
    )
    table = table.mutate(
        **{
            name: table[name] & selected.fill_null(False)
            for name, _, received in checks
            if received not in condition_keys
        }
    )
    projected.table = table
