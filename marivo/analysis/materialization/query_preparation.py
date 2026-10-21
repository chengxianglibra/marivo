"""Schema-only preparation of occurrence-specific fields and scalar relationships."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo._definition.expressions import PredicateExpression, TimeBinding
from marivo._definition.observations import ObservationPeriod, SampleCoverage
from marivo._definition.relations import MembershipBinding
from marivo._definition.types import (
    AxisBinding,
    CalendarCoordinate,
    ExactRef,
    FieldRead,
    IdentityRead,
    IntervalCoordinate,
    coordinate_read_keys,
    field_read_key,
)
from marivo.analysis.binding.observe import ContributionBinding
from marivo.analysis.compiler.member_version import version_predicate
from marivo.analysis.compiler.source_time import (
    encoded_time_predicate,
    localize,
    source_time,
    timestamp,
)
from marivo.analysis.core.time_authority import SourceTimeAuthority, time_zone
from marivo.datasource.timezone import DatasourceEngineTimezone
from marivo.refs import SemanticKind, ref
from marivo.semantic._compiled_state import CompiledSemanticState
from marivo.semantic._expression_binding import evaluate_expression_body
from marivo.semantic._numeric_lowering import numeric_failure
from marivo.semantic.ir import (
    HourPrefixParse,
    TargetDimensionContract,
)
from marivo.semantic.validator import (
    normalize_target_dimension,
    normalize_target_entity,
    normalize_target_relationship,
    normalize_target_version_selection,
)


@dataclass(frozen=True, slots=True)
class PreparedFields:
    table: ir.Table
    columns: tuple[tuple[str, str], ...]
    checks: tuple[tuple[str, str, str], ...]
    originals: tuple[tuple[str, ir.Value], ...]
    dependencies: tuple[ir.Value, ...]
    coverage: tuple[SampleCoverage, ...] = ()
    time_authorities: tuple[SourceTimeAuthority, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceInputContract:
    """Explicit consumed source binding, independent of reduction occurrences."""

    source_owner: int
    datasource: str
    source_fingerprint: str
    root: ExactRef
    reads: tuple[FieldRead, ...]
    coordinates: tuple[FieldRead | IdentityRead, ...]
    time: TimeBinding | None
    period: ObservationPeriod | None
    predicates: tuple[PredicateExpression, ...]
    memberships: tuple[MembershipBinding, ...]
    population_reads: tuple[tuple[AxisBinding, FieldRead | IdentityRead], ...]
    window_key: int | None
    sample_axes: tuple[AxisBinding | CalendarCoordinate | IntervalCoordinate, ...]
    calendars: tuple[tuple[str, CalendarCoordinate | IntervalCoordinate], ...]
    coverage: tuple[SampleCoverage, ...]
    time_authorities: tuple[SourceTimeAuthority, ...]


def scalar_field(value: ir.Value) -> ir.Scalar:
    """Retain the single checked field row without a backend-specific FIRST aggregate."""
    assert isinstance(value, ir.Column)
    return value.any() if isinstance(value, ir.BooleanColumn) else value.min()


def prepare_fields(
    contribution: ContributionBinding,
    entities: Mapping[str, ir.Table],
    state: CompiledSemanticState,
    reader: DatasourceEngineTimezone,
    *,
    root_table: ir.Table | None = None,
) -> PreparedFields:
    """Prepare expressions and checked many-to-one joins without reading values."""
    registry = state.registry
    from marivo.analysis.compiler.source_time import entity_engine
    from marivo.analysis.core.domain_captures import DomainPreparationError

    root_datasource = registry.entities[contribution.root.ref.path].datasource
    scalar_reads = (
        *contribution.reads,
        *contribution.coordinates,
    )
    from marivo.analysis.binding.coordinates import identity_projection

    scalar_reads = (
        *scalar_reads,
        *(
            read
            for _, read in contribution.population_reads
            if not isinstance(read, IdentityRead)
            or identity_projection(read, contribution.root, registry) is None
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
    dependencies: list[ir.Value] = []
    time_encodings: dict[str, tuple[ir.Value, TargetDimensionContract, SourceTimeAuthority]] = {}
    zone = contribution.period.timezone if contribution.period is not None else "UTC"

    def value(
        path: str,
        table: ir.Table,
        authority_zone: str | None = None,
        validity_checks: list[ir.BooleanValue] | None = None,
        alternative_instants: list[ir.TimestampValue] | None = None,
    ) -> ir.Value:
        is_measure = path in registry.measures
        owner = registry.measures[path].entity if is_measure else registry.dimensions[path].entity
        target = (
            ref.measure(path)
            if is_measure
            else (
                ref.time_dimension(path)
                if registry.dimensions[path].is_time_dimension
                else ref.dimension(path)
            )
        )
        body = state.sidecar.bodies.get(target)
        if body is None:
            numeric_failure(f"a compiled physical field for {path}", "missing field body")
        result = evaluate_expression_body(
            catalog_definition_fingerprint=state.definition_fingerprint,
            expression_sidecar=state.sidecar,
            owning_ref=target,
            body=body,
            entity_refs=(ref.entity(owner),),
            aliases=(table,),
        )
        dependencies.append(result)
        if target.kind is SemanticKind.TIME_DIMENSION:
            axis = normalize_target_dimension(registry, path, prepared_column=path)
            prefix = (
                value(axis.parse.prefix, table, authority_zone, validity_checks)
                if isinstance(axis.parse, HourPrefixParse)
                else None
            )
            raw = result
            result, authority = source_time(
                result,
                axis,
                boundary_timezone="UTC",
                read_timezone=reader.engine_timezone_name,
                read_source=reader.read_tz_resolution,
                engine=entity_engine(
                    registry, normalize_target_entity(registry, contribution.root.ref.path)
                ),
                prefix=prefix,
                validity_checks=validity_checks,
                alternative_instants=alternative_instants,
            )
            time_encodings[path] = (raw, axis, authority)
            if authority.kind == "civil_date":
                from marivo.analysis.binding.windows import validate_date_contribution

                validate_date_contribution(contribution, path)
            if authority.kind != "civil_date" and result.type().timezone is None:
                scale = result.type().scale
                if scale is not None and scale > 6:
                    from marivo.analysis.compiler.errors import compilation_error

                    raise compilation_error(
                        "exact native timezone conversion precision",
                        "submicrosecond conversion unsupported",
                    )
                result = localize("UTC", timestamp(result))
        return result

    reserved = {name for entity in entities.values() for name in entity.columns}

    def unique(name: str) -> str:
        while name in reserved:
            name += "_"
        reserved.add(name)
        return name

    table = entities[contribution.root.ref.path].view() if root_table is None else root_table
    # Calendar membership is independent of the enclosing source read period.
    # Window bindings own their contribution period instead of the output cells.
    selected_calendars = tuple(
        calendar
        for _, calendar in contribution.calendars
        if contribution.window_key is None or calendar in contribution.sample_axes
    )

    def selected_time(time: ir.Value) -> ir.BooleanValue:
        from marivo.analysis.compiler.observation import temporal_bound

        condition: ir.BooleanValue = ibis.literal(True)
        for calendar in selected_calendars:
            cells = (
                calendar.cells
                if isinstance(calendar, CalendarCoordinate)
                else ((calendar.start, calendar.end, calendar.start, calendar.end),)
            )
            intervals: list[tuple[datetime, datetime]] = []
            for _, _, start, end in cells:
                if intervals and intervals[-1][1] == start:
                    intervals[-1] = (intervals[-1][0], end)
                else:
                    intervals.append((start, end))
            member: ir.BooleanValue = ibis.literal(False)
            for left, right in intervals:
                member = member | (
                    (time >= temporal_bound(time, left, calendar.timezone))
                    & (time < temporal_bound(time, right, calendar.timezone))
                )
            condition = condition & member
        return condition

    if (
        selected_calendars
        and contribution.time is not None
        and not contribution.time.path
        and contribution.time.at is None
    ):
        alternatives: list[ir.TimestampValue] = []
        time = value(contribution.time.field.ref.path, table, alternative_instants=alternatives)
        table = table.filter(ibis.or_(*(selected_time(point) for point in (time, *alternatives))))
    if (
        contribution.period is not None
        and contribution.time is not None
        and not contribution.time.path
        and contribution.time.at is None
    ):
        from marivo.analysis.compiler.observation import temporal_bound

        alternatives = []
        time = value(contribution.time.field.ref.path, table, alternative_instants=alternatives)
        encoding = time_encodings.get(contribution.time.field.ref.path)
        raw_condition = None
        if encoding is not None:
            raw, axis, authority = encoding
            raw_condition = encoded_time_predicate(
                raw,
                axis,
                authority,
                start=(
                    contribution.period.start.astimezone(time_zone(zone)).date()
                    if authority.kind == "civil_date"
                    else contribution.period.start
                )
                if contribution.period.start is not None
                else None,
                end=contribution.period.end.astimezone(time_zone(zone)).date()
                if authority.kind == "civil_date"
                else contribution.period.end,
            )
        if raw_condition is not None:
            table = table.filter(raw_condition)
        else:
            table = table.filter(
                ibis.or_(
                    *(
                        (point < temporal_bound(point, contribution.period.end, zone))
                        & (
                            point >= temporal_bound(point, contribution.period.start, zone)
                            if contribution.period.start is not None
                            else ibis.literal(True)
                        )
                        for point in (time, *alternatives)
                    )
                )
            )
    columns: list[tuple[str, str]] = []
    originals: list[tuple[str, ir.Value]] = []
    checks: list[tuple[str, str, str]] = []
    from marivo.analysis.binding.membership import membership_reads
    from marivo.analysis.materialization.membership_preparation import prepare_memberships

    member_reads = {
        read
        for binding in contribution.memberships
        for read in membership_reads(binding, contribution.root)
    }
    reads = tuple(
        read
        for read in dict.fromkeys(
            (
                *contribution.reads,
                *contribution.coordinates,
                *(read for _, read in contribution.population_reads),
            )
        )
        if read not in member_reads
    )
    time_read = next(
        (
            read
            for read in reads
            if isinstance(read, FieldRead)
            and contribution.time is not None
            and read.field == contribution.time.field
        ),
        None,
    )
    if time_read is not None and time_read in reads:
        reads = (time_read, *(read for read in reads if read != time_read))
    parts = tuple(
        (read, component, key)
        for read in reads
        for component, key in zip(
            read.components if isinstance(read, IdentityRead) else (None,),
            coordinate_read_keys(read),
            strict=True,
        )
    )
    for index, (read, component, read_key) in enumerate(parts):

        def read_value(
            relation: ir.Table,
            validity_checks: list[ir.BooleanValue],
            alternative_instants: list[ir.TimestampValue],
            component: str | None = component,
            read: FieldRead | IdentityRead = read,
        ) -> ir.Value:
            if component is not None:
                return relation[component]
            assert isinstance(read, FieldRead)
            return value(
                read.field.ref.path,
                relation,
                validity_checks=validity_checks,
                alternative_instants=alternative_instants,
            )

        slot = unique(f"__field_{index}")
        alternative_slots: list[str] = []
        from marivo.analysis.binding.coordinates import identity_projection

        projection = (
            identity_projection(read, contribution.root, registry)
            if isinstance(read, IdentityRead)
            else None
        )
        if projection is not None and component is not None:
            assert isinstance(read, IdentityRead)
            expression = table[projection[read.components.index(component)]]
            dependencies.append(expression)
            originals.append((slot, expression))
            table = table.mutate(**{slot: expression})
        elif not read.path and read.at is None:
            clock_checks: list[ir.BooleanValue] = []
            clock_alternatives: list[ir.TimestampValue] = []
            expression = read_value(table, clock_checks, clock_alternatives)
            dependencies.append(expression)
            originals.append((slot, expression))
            table = table.mutate(**{slot: expression})
            if read == time_read:
                alternative_slots = [
                    unique(f"__clock_alternative_{index}_{i}")
                    for i in range(len(clock_alternatives))
                ]
                table = table.mutate(
                    **dict(zip(alternative_slots, clock_alternatives, strict=True))
                )
            for invalid_clock in clock_checks:
                check = unique(f"__clock_check_{len(checks)}")
                table = table.mutate(**{check: invalid_clock})
                checks.append(
                    (check, "an unambiguous existing source civil time", field_read_key(read))
                )
        else:
            current = {name: name for name in entities[contribution.root.ref.path].columns}
            steps = read.path or (None,)
            for step_index, step in enumerate(steps):
                if step is None:
                    target_path = read.entity.ref.path
                    keys = tuple(
                        (name, name) for name in registry.entities[target_path].primary_key
                    )
                else:
                    relation = normalize_target_relationship(registry, step.ref.path)
                    target_path = relation.to_entity_ref.path
                    keys = relation.keys
                target = entities[target_path].view()
                target_clock_checks: list[ir.BooleanValue] = []
                target_alternatives: list[ir.TimestampValue] = []
                for left, right in keys:
                    dependencies.extend((table[current[left]], target[right]))
                if target_path == read.entity.ref.path and read.at is not None:
                    contract = normalize_target_entity(registry, target_path)
                    selection = normalize_target_version_selection(
                        contract, boundary=read.at, interpretation="instant"
                    )
                    version = contract.version
                    version_condition = version_predicate(
                        target,
                        version,
                        selection,
                        state=state,
                        reader=reader,
                        dependencies=dependencies,
                        validity_checks=target_clock_checks,
                    )
                    target = (
                        target.filter(version_condition | ibis.or_(*target_clock_checks))
                        if target_clock_checks
                        else target.filter(version_condition)
                    )
                final = step_index == len(steps) - 1
                if final:
                    expression = read_value(target, target_clock_checks, target_alternatives)
                    dependencies.append(expression)
                    originals.append((slot, expression))
                    target = target.mutate(**{slot: expression})
                    if read == time_read:
                        alternative_slots = [
                            unique(f"__clock_alternative_{index}_{i}")
                            for i in range(len(target_alternatives))
                        ]
                        target = target.mutate(
                            **dict(zip(alternative_slots, target_alternatives, strict=True))
                        )
                target_check_names = tuple(
                    unique(f"__clock_check_{len(checks)}_{i}")
                    for i in range(len(target_clock_checks))
                )
                if target_check_names:
                    target = target.mutate(
                        **dict(zip(target_check_names, target_clock_checks, strict=True))
                    )
                # Collapse before joining; the count makes duplicates an error rather than fanout.
                right_keys = tuple(right for _, right in keys)
                prefix = f"__route_{index}_{step_index}_"
                count = unique(prefix + "count")
                grouped = target.group_by(*right_keys).aggregate(
                    **{
                        name: scalar_field(target[name])
                        for name in target.columns
                        if name not in right_keys
                    },
                    **{count: target.count()},
                )
                renamed = {name: unique(prefix + name) for name in grouped.columns}
                grouped = grouped.rename(**{new: old for old, new in renamed.items()})
                joined = table.left_join(
                    grouped,
                    [table[current[left]] == grouped[renamed[right]] for left, right in keys],
                )
                table = joined.select(table, *(grouped[name] for name in grouped.columns))
                checks.extend(
                    (
                        renamed[name],
                        "an unambiguous existing source civil time",
                        field_read_key(read),
                    )
                    for name in target_check_names
                )
                check = unique(f"__prepare_check_{len(checks)}")
                table = table.mutate(**{check: table[renamed[count]].fill_null(0) != 1})
                checks.append(
                    (check, "exactly one matching scalar field row", field_read_key(read))
                )
                current = renamed
                if final:
                    table = table.mutate(**{slot: table[renamed[slot]]})
                    alternative_slots = [renamed[name] for name in alternative_slots]
        columns.append((read_key, slot))
        if read == time_read and selected_calendars:
            table = table.filter(
                ibis.or_(*(selected_time(table[name]) for name in (slot, *alternative_slots)))
            )
        if read == time_read and contribution.period is not None:
            from marivo.analysis.compiler.observation import temporal_bound

            table = table.filter(
                ibis.or_(
                    *(
                        (table[name] < temporal_bound(table[name], contribution.period.end, zone))
                        & (
                            table[name]
                            >= temporal_bound(table[name], contribution.period.start, zone)
                            if contribution.period.start is not None
                            else ibis.literal(True)
                        )
                        for name in (slot, *alternative_slots)
                    )
                )
            )
    for _, slot in columns:
        if table[slot].type().is_interval():
            from marivo.analysis.compiler.numeric_sql import duration_ticks, interval_part

            amount = table[slot]
            check = unique(f"__duration_check_{len(checks)}")
            invalid = (
                (interval_part(ibis.literal("year"), amount) != 0)
                | (interval_part(ibis.literal("month"), amount) != 0)
                | (interval_part(ibis.literal("day"), amount) != 0)
            )
            table = table.mutate(**{check: invalid, slot: duration_ticks(amount)})
            checks.append((check, "fixed elapsed Duration without calendar components", slot))
    members = prepare_memberships(
        table, contribution.root, contribution.memberships, entities, state, value, unique
    )
    table = members.table
    columns.extend(members.columns)
    originals.extend(members.originals)
    dependencies.extend(members.dependencies)
    checks.extend(members.checks)
    from marivo.analysis.materialization.population_condition import prepare_condition_memberships

    prepared_memberships = prepare_condition_memberships(
        table, contribution, dict(columns), entities, state
    )
    table = prepared_memberships[0]
    columns.extend(prepared_memberships[1])
    originals.extend((slot, table[slot]) for _, slot in prepared_memberships[1])
    predicate_conditions = (
        *contribution.intrinsic_conditions,
        *((contribution.common_condition,) if contribution.common_condition is not None else ()),
    )
    if predicate_conditions and checks:
        from marivo._definition.expressions import RowExpression, RowField
        from marivo._definition.graph import walk
        from marivo.analysis.compiler.predicate import lower_condition

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
    from marivo.analysis.binding.sampling import sampling_coverage

    authorities = {path: item[2] for path, item in time_encodings.items()}
    coverage = (
        sampling_coverage(contribution, state, calendar=None, authorities=authorities)
        if not any(
            isinstance(axis, CalendarCoordinate) and not axis.cells
            for _, axis in contribution.calendars
        )
        else ()
    )
    return PreparedFields(
        table,
        tuple(columns),
        tuple(checks),
        tuple(originals),
        tuple(dependencies),
        coverage,
        tuple(authorities.values()),
    )


def physical_fields(value: ir.Value) -> tuple[ops.Field, ...]:
    """Follow only consumed projection expressions through aliases to source fields."""
    found: dict[ops.Field, None] = {}

    def visit(node: ops.Value) -> None:
        if isinstance(node, ops.Field):
            definition = node.rel.values.get(node.name)
            if definition is not None:
                visit(definition)
            elif isinstance(node.rel, ops.Reference):
                visit(ops.Field(node.rel.parent, node.name))
            else:
                found[node] = None
        else:
            for child in node.args:
                if isinstance(child, ops.Value):
                    visit(child)
                elif isinstance(child, tuple):
                    for item in child:
                        if isinstance(item, ops.Value):
                            visit(item)

    visit(value.op())
    return tuple(found)
