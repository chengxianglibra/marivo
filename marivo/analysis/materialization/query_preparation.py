"""Schema-only preparation of occurrence-specific fields and scalar relationships."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime

import ibis
import ibis.expr.types as ir

from marivo._definition.expressions import (
    EntityCount,
    PredicateExpression,
    SourceAggregate,
    TimeBinding,
)
from marivo._definition.observations import ObservationPeriod
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
    localize,
    source_time,
    source_time_predicate,
    timestamp,
)
from marivo.analysis.core.time_authority import SourceTimeAuthority, time_zone
from marivo.datasource.timezone import DatasourceEngineTimezone
from marivo.refs import SemanticKind, ref
from marivo.semantic._compiled_state import CompiledSemanticState
from marivo.semantic._expression_binding import evaluate_expression_body
from marivo.semantic._numeric_lowering import numeric_failure
from marivo.semantic._physical_contracts import validate_bound_field
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
    window_key: int | datetime | None
    sample_axes: tuple[AxisBinding | CalendarCoordinate | IntervalCoordinate, ...]
    calendars: tuple[tuple[str, CalendarCoordinate | IntervalCoordinate], ...]
    time_authorities: tuple[SourceTimeAuthority, ...]

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
        )


def share_source_projections(
    contributions: tuple[ContributionBinding, ...], contracts: tuple[SourceInputContract, ...]
) -> tuple[ContributionBinding, ...]:
    """Union root projections while preserving every relationship read and row domain."""
    groups: dict[SourceInputContract, list[int]] = {}
    for index, (contribution, contract) in enumerate(zip(contributions, contracts, strict=True)):
        node = contribution.definition
        if (
            contribution.sample_axes
            or contribution.window_key is not None
            or not (
                isinstance(node, EntityCount)
                or (
                    isinstance(node, SourceAggregate)
                    and node.reducer in ("sum", "count", "mean", "min", "max")
                )
            )
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
    reader: DatasourceEngineTimezone,
    *,
    root_table: ir.Table | None = None,
) -> PreparedFields:
    """Prepare expressions and declared many-to-one joins without reading values."""
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
    time_encodings: dict[
        str,
        tuple[
            ir.Value,
            TargetDimensionContract,
            SourceTimeAuthority,
            ir.Value | None,
            TargetDimensionContract | None,
        ],
    ] = {}
    zone = contribution.period.timezone if contribution.period is not None else "UTC"

    def value(
        path: str,
        table: ir.Table,
        authority_zone: str | None = None,
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
        validate_bound_field(result, ref=target, registry=registry)
        from marivo._numeric_types import exact_unsigned

        if target.kind is not SemanticKind.TIME_DIMENSION:
            result = exact_unsigned(result)
        dependencies.append(result)
        if target.kind is SemanticKind.TIME_DIMENSION:
            axis = normalize_target_dimension(registry, path, prepared_column=path)
            prefix = (
                value(axis.parse.prefix, table, authority_zone)
                if isinstance(axis.parse, HourPrefixParse)
                else None
            )
            prefix_input = (
                time_encodings.get(axis.parse.prefix)
                if isinstance(axis.parse, HourPrefixParse)
                else None
            )
            raw = result
            engine = entity_engine(
                registry, normalize_target_entity(registry, axis.entity_ref.path)
            )
            result, authority = source_time(
                result,
                axis,
                boundary_timezone="UTC",
                read_timezone=reader.engine_timezone_name,
                read_source=reader.read_tz_resolution,
                engine=engine,
                prefix=prefix,
                prefix_timezone=reader.engine_timezone_name if prefix is not None else None,
            )
            raw_prefix = prefix_input[0] if prefix_input is not None else None
            prefix_axis = prefix_input[1] if prefix_input is not None else None
            time_encodings[path] = (raw, axis, authority, raw_prefix, prefix_axis)
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
    selected_owns_period = contribution.period is not None and any(
        all(
            (contribution.period.start is None or contribution.period.start <= start)
            and end <= contribution.period.end
            for _, _, start, end in (
                calendar.cells
                if isinstance(calendar, CalendarCoordinate)
                else ((calendar.start, calendar.end, calendar.start, calendar.end),)
            )
        )
        for calendar in selected_calendars
    )

    def period_condition(
        time: ir.Value, start: datetime | None, end: datetime, zone: str
    ) -> ir.BooleanValue:
        from marivo.analysis.compiler.observation import temporal_bound

        encoding = (
            time_encodings.get(contribution.time.field.ref.path) if contribution.time else None
        )
        if encoding is not None:
            raw, axis, authority, prefix, prefix_axis = encoding
            condition = source_time_predicate(
                raw,
                axis,
                authority,
                start=start.astimezone(time_zone(zone)).replace(tzinfo=None)
                if start is not None and authority.kind == "civil_date"
                else start,
                end=end.astimezone(time_zone(zone)).replace(tzinfo=None)
                if authority.kind == "civil_date"
                else end,
                prefix=prefix,
                prefix_axis=prefix_axis,
            )
            if condition is not None:
                return condition
        upper = time < temporal_bound(time, end, zone)
        return upper if start is None else upper & (time >= temporal_bound(time, start, zone))

    def selected_time(time: ir.Value) -> ir.BooleanValue:
        # Boolean literal seeds can produce incompatible Bool columns on older ClickHouse builds.
        condition: ir.BooleanValue | None = None
        for calendar in selected_calendars:
            cells = (
                calendar.cells
                if isinstance(calendar, CalendarCoordinate)
                else ((calendar.start, calendar.end, calendar.start, calendar.end),)
            )
            intervals = [(cells[0][2], cells[-1][3])] if cells else []
            member: ir.BooleanValue | None = None
            for left, right in intervals:
                predicate = period_condition(time, left, right, calendar.timezone)
                member = predicate if member is None else member | predicate
            if member is None:
                return ibis.literal(False)
            condition = member if condition is None else condition & member
        return ibis.literal(True) if condition is None else condition

    if (
        selected_calendars
        and contribution.time is not None
        and not contribution.time.path
        and contribution.time.at is None
    ):
        time = value(contribution.time.field.ref.path, table)
        table = table.filter(selected_time(time))
    if (
        contribution.period is not None
        and not selected_owns_period
        and contribution.time is not None
        and not contribution.time.path
        and contribution.time.at is None
    ):
        time = value(contribution.time.field.ref.path, table)
        table = table.filter(
            period_condition(time, contribution.period.start, contribution.period.end, zone)
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
    from marivo.analysis.compiler.observation import calendar_field, reduction_input

    physical_axes = reduction_input(contribution, "", None).axes
    parts = tuple(
        (read, component, key)
        for read in reads
        for component, key in zip(
            read.components if isinstance(read, IdentityRead) else (None,),
            coordinate_read_keys(read),
            strict=True,
        )
    )
    pending_calendars: list[tuple[str, str, CalendarCoordinate, str | None]] = []
    for index, (read, component, read_key) in enumerate(parts):
        extras: dict[str, ir.Value] = {}
        extra_keys: dict[str, str] = {}

        def time_columns(
            expression: ir.Value,
            read: FieldRead | IdentityRead = read,
            read_key: str = read_key,
            index: int = index,
            extras: dict[str, ir.Value] = extras,
            extra_keys: dict[str, str] = extra_keys,
        ) -> dict[str, ir.Value]:
            if read == time_read:
                for key, calendar in contribution.calendars:
                    name = unique("__bucket_" + key.replace(".", "_"))
                    if isinstance(calendar, CalendarCoordinate):
                        extras[name] = ibis.null().cast("timestamp('UTC', 6)")
                        clock_key, wall_zone = read_key, None
                        encoded = (
                            time_encodings.get(read.field.ref.path)
                            if isinstance(read, FieldRead)
                            else None
                        )
                        if encoded is not None and calendar.cells:
                            from marivo.analysis.compiler.source_time import calendar_wall_clock

                            raw, time_axis, authority, prefix, prefix_axis = encoded
                            engine = entity_engine(
                                registry,
                                normalize_target_entity(registry, time_axis.entity_ref.path),
                            )
                            neutral = calendar_wall_clock(
                                raw,
                                time_axis,
                                authority,
                                bound=calendar.cells[0][2],
                                engine=engine,
                                prefix=prefix,
                                prefix_axis=prefix_axis,
                            )
                            if neutral is not None:
                                clock_key = unique("__calendar_wall")
                                extras[clock_key] = neutral
                                extra_keys[clock_key] = clock_key
                                wall_zone = authority.read_timezone
                        pending_calendars.append((name, clock_key, calendar, wall_zone))
                    else:
                        extras[name] = period_condition(
                            expression, calendar.start, calendar.end, calendar.timezone
                        ).ifelse(ibis.literal(0).cast("!int64"), ibis.null())
                    extra_keys[name] = calendar_field(key)
                if selected_calendars:
                    name = unique("__time_selected")
                    extras[name] = selected_time(expression)
                    extra_keys[name] = "__time_selected"
                if contribution.period is not None:
                    name = unique("__time_period")
                    extras[name] = period_condition(
                        expression, contribution.period.start, contribution.period.end, zone
                    )
                    extra_keys[name] = "__time_period"
            originals.extend(extras.items())
            return extras

        def read_value(
            relation: ir.Table,
            component: str | None = component,
            read: FieldRead | IdentityRead = read,
        ) -> ir.Value:
            if component is not None:
                return relation[component]
            assert isinstance(read, FieldRead)
            return value(
                read.field.ref.path,
                relation,
            )

        slot = unique(f"__field_{index}")
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
            expression = read_value(table)
            dependencies.append(expression)
            originals.append((slot, expression))
            table = table.mutate(**{slot: expression}, **time_columns(expression))
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
                    )
                    target = target.filter(version_condition)
                final = step_index == len(steps) - 1
                if final:
                    expression = read_value(target)
                    dependencies.append(expression)
                    originals.append((slot, expression))
                    target = target.mutate(**{slot: expression}, **time_columns(expression))
                prefix = f"__route_{index}_{step_index}_"
                grouped = target
                renamed = {name: unique(prefix + name) for name in grouped.columns}
                grouped = grouped.rename(**{new: old for old, new in renamed.items()})
                joined = table.left_join(
                    grouped,
                    [table[current[left]] == grouped[renamed[right]] for left, right in keys],
                )
                table = joined.select(table, *(grouped[name] for name in grouped.columns))
                current = renamed
                if final:
                    table = table.mutate(
                        **{slot: table[renamed[slot]]},
                        **{name: table[renamed[name]] for name in extras},
                    )
        columns.append((read_key, slot))
        columns.extend((key, name) for name, key in extra_keys.items())
        if read == time_read and (read.path or read.at is not None) and selected_calendars:
            table = table.filter(table[dict(columns)["__time_selected"]])
        if (
            read == time_read
            and (read.path or read.at is not None)
            and contribution.period is not None
            and not selected_owns_period
        ):
            table = table.filter(table[dict(columns)["__time_period"]])
    from marivo.analysis.compiler.calendar import bind_calendar

    for name, time_key, calendar, wall_zone in pending_calendars:
        clock = dict(columns)[time_key]
        if wall_zone is not None:
            from marivo.analysis.compiler.calendar import bind_wall_clock

            normalized = unique("__calendar_instant")
            table = bind_wall_clock(table, table[clock], wall_zone, calendar, normalized)
            clock = normalized
        table = bind_calendar(table, table[clock], calendar, name)
        if calendar in selected_calendars:
            table = table.filter(table[name].notnull())
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
        value,
        unique,
        sample_positions=sample_positions,
    )
    table = members.table
    columns.extend(members.columns)
    originals.extend(members.originals)
    dependencies.extend(members.dependencies)
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
    return PreparedFields(
        table,
        tuple(columns),
        tuple(checks),
        tuple(originals),
        tuple(dependencies),
        tuple(item[2] for item in time_encodings.values()),
    )
