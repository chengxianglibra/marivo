"""Schema-only preparation of occurrence-specific fields and scalar relationships."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo._definition.expressions import (
    BuiltinSampleCoordinate,
    PredicateExpression,
    RowField,
    TimeBinding,
)
from marivo._definition.observations import CalendarGrouping, ObservationPeriod, SampleAxis
from marivo._definition.relations import MembershipBinding
from marivo._definition.types import (
    AttributeTime,
    AxisBinding,
    CalendarCoordinate,
    ExactRef,
    FieldRead,
    IdentityRead,
    RowAttributeTime,
    TimeInstantFieldRead,
    TimeScopeFieldRead,
    coordinate_read_keys,
    field_read_key,
)
from marivo.analysis.binding.observe import ContributionBinding
from marivo.analysis.compiler.member_version import version_predicate
from marivo.analysis.compiler.source_time import (
    TimePredicateUse,
    source_time,
    source_time_predicate,
)
from marivo.analysis.core.frame_interpretation import (
    FrameSourceTime,
    capture_source_times,
    merge_source_times,
)
from marivo.analysis.core.time_authority import SourceTimeAuthority
from marivo.datasource._temporal_expressions import localize, timestamp
from marivo.refs import SemanticKind, ref
from marivo.semantic._compiled_state import CompiledSemanticState
from marivo.semantic._expression_binding import evaluate_expression_body
from marivo.semantic._numeric_lowering import numeric_failure
from marivo.semantic._physical_contracts import validate_bound_field
from marivo.semantic._source_timezone import effective_source_timezone
from marivo.semantic.ir import (
    HourPrefixParse,
    StrptimeParse,
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
    source_times: tuple[FrameSourceTime, ...] = ()


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
    sample_axes: tuple[SampleAxis, ...]
    calendars: tuple[tuple[str, CalendarGrouping], ...]
    time_authorities: tuple[SourceTimeAuthority, ...]
    sample_calendar_reads: tuple[tuple[str, FieldRead], ...] = ()

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
        )


def share_source_projections(
    contributions: tuple[ContributionBinding, ...], contracts: tuple[SourceInputContract, ...]
) -> tuple[ContributionBinding, ...]:
    """Union root projections while preserving every relationship read and row domain."""
    groups: dict[SourceInputContract, list[int]] = {}
    for index, (contribution, contract) in enumerate(zip(contributions, contracts, strict=True)):
        if contribution.sample_axes or contribution.window_key is not None:
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
    zone = contribution.period.timezone if contribution.period is not None else consumption_timezone
    source_times: list[FrameSourceTime] = []

    def value(
        path: str,
        table: ir.Table,
        authority_zone: str | None = None,
        time_scope: TimeScopeFieldRead | None = None,
        instant_read: bool = False,
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
            consumed_zone = (
                time_scope.scope_timezone
                if time_scope is not None
                else consumption_timezone
                if instant_read
                else authority_zone or zone
            )
            prefix = (
                value(axis.parse.prefix, table, consumed_zone)
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
                source_timezone=effective_source_timezone(registry, axis),
                engine=engine,
                prefix=prefix,
            )
            raw_prefix = prefix_input[0] if prefix_input is not None else None
            prefix_axis = prefix_input[1] if prefix_input is not None else None
            time_encodings[path] = (raw, axis, authority, raw_prefix, prefix_axis)
            source_times.extend(
                capture_source_times((authority,), consumption_timezone=consumed_zone)
            )
            if authority.kind == "civil_date":
                from marivo.analysis.binding.windows import validate_date_contribution

                validate_date_contribution(contribution, path)
                if time_scope is not None:
                    from marivo._timezones import normalize_timezone
                    from marivo.analysis.core.model import reject

                    timezone = normalize_timezone(time_scope.scope_timezone).tz
                    if any(
                        bound.astimezone(timezone).time() != datetime.min.time()
                        for bound in (time_scope.scope_start, time_scope.scope_end)
                    ):
                        reject(
                            "civil DATE within a complete-day scope in its consumption timezone",
                            f"{path}: source timezone {authority.timezone}; "
                            f"scope timezone {time_scope.scope_timezone}",
                            "Use complete civil-day boundaries in the scope timezone, "
                            "or use a timestamp TimeField for a subday scope.",
                            "TimeField.within",
                        )
                if (
                    instant_read
                    or time_scope is not None
                    or (contribution.time is not None and contribution.time.field.ref.path == path)
                ):
                    result = localize(authority.timezone, timestamp(result))
            if authority.kind != "civil_date" and result.type().timezone is None:
                scale = result.type().scale
                if scale is not None and scale > 6:
                    from marivo.analysis.compiler.errors import compilation_error

                    raise compilation_error(
                        "exact native timezone conversion precision",
                        "submicrosecond conversion unsupported",
                    )
                result = localize("UTC", timestamp(result))
            if time_scope is not None:
                condition = source_time_predicate(
                    raw,
                    axis,
                    authority,
                    start=time_scope.scope_start,
                    end=time_scope.scope_end,
                    prefix=raw_prefix,
                    prefix_axis=prefix_axis,
                    engine=engine,
                )
                if condition is not None:
                    return condition
        return result

    reserved = {name for entity in entities.values() for name in entity.columns}

    def unique(name: str) -> str:
        while name in reserved:
            name += "_"
        reserved.add(name)
        return name

    table = entities[contribution.root.ref.path].view() if root_table is None else root_table
    reserved.update(table.columns)
    # Calendar membership is independent of the enclosing source read period.
    # Window bindings own their contribution period instead of the output cells.
    selected_calendars = tuple(
        calendar
        for _, calendar in contribution.calendars
        if not isinstance(calendar, BuiltinSampleCoordinate)
        and not (isinstance(calendar, CalendarCoordinate) and calendar.actual_only)
        and (contribution.window_key is None or calendar in contribution.sample_axes)
    )
    selected_owns_period = contribution.period is not None and any(
        all(
            (contribution.period.start is None or contribution.period.start <= start)
            and end <= contribution.period.end
            for start, end in (
                calendar.ranges
                if isinstance(calendar, CalendarCoordinate)
                else ((calendar.start, calendar.end),)
            )
        )
        for calendar in selected_calendars
    )

    def period_condition(
        time: ir.Value,
        start: datetime | None,
        end: datetime,
        zone: str,
        *,
        use: TimePredicateUse = "value",
    ) -> ir.BooleanValue:
        from marivo.analysis.compiler.observation import temporal_bound

        encoding = (
            time_encodings.get(contribution.time.field.ref.path) if contribution.time else None
        )
        if encoding is not None:
            raw, axis, authority, prefix, prefix_axis = encoding
            source_times.extend(capture_source_times((authority,), consumption_timezone=zone))
            condition = source_time_predicate(
                raw,
                axis,
                authority,
                engine=entity_engine(
                    registry, normalize_target_entity(registry, axis.entity_ref.path)
                ),
                start=start,
                end=end,
                prefix=prefix,
                prefix_axis=prefix_axis,
                use=use,
            )
            if condition is not None:
                return condition
        upper = time < temporal_bound(time, end, zone)
        return upper if start is None else upper & (time >= temporal_bound(time, start, zone))

    def selected_time(time: ir.Value, *, use: TimePredicateUse = "value") -> ir.BooleanValue:
        # Boolean literal seeds can produce incompatible Bool columns on older ClickHouse builds.
        condition: ir.BooleanValue | None = None
        for calendar in selected_calendars:
            intervals = (
                calendar.ranges
                if isinstance(calendar, CalendarCoordinate)
                else ((calendar.start, calendar.end),)
            )
            member: ir.BooleanValue | None = None
            for left, right in intervals:
                predicate = period_condition(time, left, right, calendar.timezone, use=use)
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
        table = table.filter(selected_time(time, use="filter"))
    if (
        contribution.period is not None
        and not selected_owns_period
        and contribution.time is not None
        and not contribution.time.path
        and contribution.time.at is None
    ):
        time = value(contribution.time.field.ref.path, table)
        table = table.filter(
            period_condition(
                time, contribution.period.start, contribution.period.end, zone, use="filter"
            )
        )
    predicate_conditions = (
        *contribution.intrinsic_conditions,
        *((contribution.common_condition,) if contribution.common_condition is not None else ()),
    )
    if any(isinstance(read.at, RowAttributeTime) for read in scalar_reads):
        from marivo._definition.graph import walk
        from marivo._definition.relations import PopulationContains
        from marivo.analysis.compiler.predicate import lower_condition

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
                    fields[field_read_key(read)] = value(
                        read.field.ref.path,
                        relation,
                        time_scope=read if isinstance(read, TimeScopeFieldRead) else None,
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
    columns: list[tuple[str, str]] = []
    originals: list[tuple[str, ir.Value]] = []
    checks: list[tuple[str, str, str]] = []
    match_flag = unique("__route_matched")
    routes: dict[
        tuple[tuple[ExactRef, ...], AttributeTime | None],
        tuple[ir.Table, dict[str, str]],
    ] = {}
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
            and not isinstance(read, (TimeScopeFieldRead, TimeInstantFieldRead))
            and contribution.time is not None
            and read.field == contribution.time.field
            and read.path == contribution.time.path
            and read.at == contribution.time.at
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
    pending_calendars: list[tuple[str, str, CalendarCoordinate]] = []
    day_clocks: dict[str, tuple[ir.Value, TargetDimensionContract, SourceTimeAuthority]] = {}
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
                    if isinstance(calendar, BuiltinSampleCoordinate):
                        continue
                    name = unique("__bucket_" + key.replace(".", "_"))
                    if isinstance(calendar, CalendarCoordinate):
                        extras[name] = ibis.null().cast("timestamp('UTC', 6)")
                        clock_key = read_key
                        pending_calendars.append((name, clock_key, calendar))
                        assert isinstance(read, FieldRead)
                        encoding = time_encodings.get(read.field.ref.path)
                        if (
                            encoding is not None
                            and encoding[2].kind != "instant"
                            and (
                                encoding[0].type().is_date()
                                or (
                                    isinstance(encoding[1].parse, StrptimeParse)
                                    and encoding[1].parse.format in ("%Y%m%d", "%Y-%m-%d")
                                )
                            )
                        ):
                            day_clocks[name] = encoding[:3]
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
                time_scope=read if isinstance(read, TimeScopeFieldRead) else None,
                instant_read=isinstance(read, TimeInstantFieldRead),
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
                final = step_index == len(steps) - 1
                route_key = (read.path[: step_index + 1], read.at if final else None)
                if route_key in routes:
                    # Match through retained columns, never a fresh target relation.
                    target, current = routes[route_key]
                    if final:
                        expression = read_value(target)
                        replacements = {
                            ops.Field(target.op(), name): table[current[name]].op()
                            for name in target.columns
                        }
                        source_node = expression.op()
                        expression = source_node.replace(replacements).to_expr()
                        assert isinstance(expression, ir.Value)
                        if not isinstance(source_node, ops.Field):
                            # A derived attribute exists only when its target row matched.
                            expression = table[current[match_flag]].ifelse(expression, ibis.null())
                        dependencies.append(expression)
                        originals.append((slot, expression))
                        table = table.mutate(**{slot: expression}, **time_columns(expression))
                    continue
                target = entities[target_path].view()
                row_condition: ir.BooleanValue | None = None
                for left, right in keys:
                    dependencies.extend((table[current[left]], target[right]))
                if target_path == read.entity.ref.path and read.at is not None:
                    contract = normalize_target_entity(registry, target_path)
                    if isinstance(read.at, RowAttributeTime):
                        from marivo.analysis.compiler.member_version import row_version_predicate

                        control = value(read.at.field.ref.path, table)
                        control_authority = time_encodings[read.at.field.ref.path][2]
                        row_condition = row_version_predicate(
                            target,
                            contract.version,
                            control,
                            control_authority,
                            engine=entity_engine(registry, contract),
                            state=state,
                            dependencies=dependencies,
                            source_times=source_times,
                        )
                    else:
                        selection = normalize_target_version_selection(
                            contract, boundary=read.at, interpretation="instant"
                        )
                        version_condition = version_predicate(
                            target,
                            contract.version,
                            selection,
                            state=state,
                            dependencies=dependencies,
                            source_times=source_times,
                        )
                        target = target.filter(version_condition)
                if final:
                    expression = read_value(target)
                    dependencies.append(expression)
                    originals.append((slot, expression))
                    target = target.mutate(**{slot: expression}, **time_columns(expression))
                prefix = f"__route_{index}_{step_index}_"
                grouped = target.mutate(**{match_flag: ibis.literal(True)})
                renamed = {name: unique(prefix + name) for name in grouped.columns}
                grouped = grouped.rename(**{new: old for old, new in renamed.items()})
                joined = table.left_join(
                    grouped,
                    [table[current[left]] == grouped[renamed[right]] for left, right in keys]
                    + ([] if row_condition is None else [row_condition]),
                )
                table = joined.select(table, *(grouped[name] for name in grouped.columns))
                current = renamed
                routes[route_key] = (target, renamed)
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

    for name, time_key, calendar in pending_calendars:
        day_clock = day_clocks.get(name)
        day_timezone = (
            day_clock[2].timezone
            if day_clock is not None and day_clock[2].kind != "instant"
            else None
        )
        date_timezone = (
            day_timezone if day_clock is not None and day_clock[0].type().is_date() else None
        )
        if contribution.time is not None:
            encoding = time_encodings.get(contribution.time.field.ref.path)
            if encoding is not None:
                source_times.extend(
                    capture_source_times((encoding[2],), consumption_timezone=calendar.timezone)
                )
        clock = dict(columns)[time_key]
        if (
            day_timezone is not None
            and contribution.time is not None
            and not contribution.time.path
            and contribution.time.at is None
            and contribution.window_key is None
            and contribution.window_rule is None
            and not contribution.sample_axes
            and not contribution.memberships
            and not contribution.population_reads
            and len(contribution.calendars) == 1
            and calendar in selected_calendars
        ):
            from marivo.analysis.materialization.date_day_keys import deferred_day_key

            assert day_clock is not None
            deferred = deferred_day_key(
                table,
                day_clock[0],
                calendar,
                day_timezone,
                entity_engine(
                    registry, normalize_target_entity(registry, contribution.root.ref.path)
                ),
                encoded=(day_clock[1], day_clock[2])
                if isinstance(day_clock[1].parse, StrptimeParse)
                else None,
            )
            if deferred is not None:
                table = table.mutate(**{name: deferred})
                continue
        table = bind_calendar(
            table,
            table[clock],
            calendar,
            name,
            date_timezone=date_timezone,
            engine=entity_engine(
                registry, normalize_target_entity(registry, contribution.root.ref.path)
            ),
        )
        if calendar in selected_calendars or calendar.actual_only:
            table = table.filter(table[name].notnull())
    prepared_columns = dict(columns)
    sample_calendars = dict(contribution.sample_calendar_reads)
    for key, sample_calendar in contribution.calendars:
        if not isinstance(sample_calendar, BuiltinSampleCoordinate):
            continue
        read = sample_calendars[key]
        _, axis, authority, _, _ = time_encodings[read.field.ref.path]
        source_times.extend(
            capture_source_times((authority,), consumption_timezone=sample_calendar.timezone)
        )
        name = unique("__sample_bucket_" + key.replace(".", "_"))
        clock = table[prepared_columns[field_read_key(read)]]
        if authority.kind == "civil_date" and clock.type().is_date():
            clock = localize(authority.timezone, timestamp(clock))
        table = bind_calendar(
            table,
            clock,
            sample_calendar,
            name,
            engine=entity_engine(registry, normalize_target_entity(registry, axis.entity_ref.path)),
        )
        originals.append((name, table[name]))
        columns.append((calendar_field(key), name))
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
        source_times=source_times,
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
    if predicate_conditions and not checks:
        from marivo.analysis.compiler.predicate import lower_condition

        fields = {key: table[slot] for key, slot in columns}
        scoped = {
            fields[field_read_key(read)].op(): read
            for read in reads
            if isinstance(read, TimeScopeFieldRead)
            and read.entity == contribution.root
            and not read.path
            and read.at is None
            and isinstance(registry.dimensions[read.field.ref.path].parse, HourPrefixParse)
        }
        required: dict[TimeScopeFieldRead, None] = {}
        for condition in predicate_conditions if scoped else ():
            lowered = lower_condition(condition, fields)
            if lowered.checks:
                continue
            pending = [lowered.value.op()]
            while pending:
                atom = pending.pop()
                scope_read = scoped.get(atom)
                if scope_read is not None:
                    required[scope_read] = None
                elif isinstance(atom, ops.And):
                    pending.extend((atom.left, atom.right))
        guards: list[ir.BooleanValue] = []
        for read in required:
            exact = value(read.field.ref.path, table, time_scope=read)
            encoded_raw, encoded_axis, encoded_authority, encoded_prefix, encoded_prefix_axis = (
                time_encodings[read.field.ref.path]
            )
            filtered = source_time_predicate(
                encoded_raw,
                encoded_axis,
                encoded_authority,
                start=read.scope_start,
                end=read.scope_end,
                prefix=encoded_prefix,
                prefix_axis=encoded_prefix_axis,
                engine=entity_engine(
                    registry, normalize_target_entity(registry, encoded_axis.entity_ref.path)
                ),
                use="filter",
            )
            if filtered is not None and not filtered.equals(exact):
                guards.append(filtered)
        if guards:
            # Keep the prepared Boolean values exact for all other consumers.
            # Only direct/top-level AND membership permits this earlier filter.
            table = table.filter(*guards)
    if predicate_conditions and checks:
        from marivo._definition.expressions import RowExpression
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
        merge_source_times(tuple(source_times)),
    )
