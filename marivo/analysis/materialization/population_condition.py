"""Prepare non-null complete-tuple membership markers without filtering facts."""

from __future__ import annotations

from collections.abc import Callable, Mapping

import ibis
import ibis.expr.types as ir

from marivo._definition.graph import walk
from marivo._definition.observations import ContributionBinding
from marivo._definition.relations import (
    AttributeKeys,
    PopulationDependency,
    condition_populations,
)
from marivo._definition.types import (
    AxisBinding,
    CalendarCoordinate,
    EntityIdentityRead,
    FieldRead,
    IntervalCoordinate,
    PathParameter,
    RelationshipStepParameter,
    coordinate_read_keys,
    field_read_key,
    fixed_attribute_time,
)
from marivo._kernel.temporal import InstantAnchor
from marivo.analysis.binding.membership import membership_reads
from marivo.analysis.compiler.member_version import prepare_version_filter
from marivo.analysis.compiler.predicate import population_key
from marivo.analysis.compiler.source_predicates import string_bytes
from marivo.analysis.materialization.population_preparation import _domain_table, prepare_population
from marivo.analysis.model.time_facts import FrameSourceTime
from marivo.semantic.spi import (
    CompiledSemanticState,
    normalize_target_entity,
    normalize_target_relationship,
    normalize_target_version_selection,
)


def population_time_ranges(
    axis: CalendarCoordinate | IntervalCoordinate,
) -> tuple[tuple[int | InstantAnchor, InstantAnchor, InstantAnchor], ...]:
    """Return the captured half-open time range for each complete temporal key."""
    if isinstance(axis, IntervalCoordinate):
        return tuple((key, start, end) for key, (start, end) in enumerate(axis.bounds))
    return tuple((key, start, end) for key, _, start, end in axis.cells)


def membership_time_axes(
    contribution: ContributionBinding,
) -> tuple[CalendarCoordinate | IntervalCoordinate, ...]:
    conditions = (
        *contribution.intrinsic_conditions,
        *((contribution.common_condition,) if contribution.common_condition is not None else ()),
    )
    return tuple(
        dict.fromkeys(
            axis
            for condition in conditions
            for domain in condition_populations(condition)
            for axis in domain.axes
            if isinstance(axis, (CalendarCoordinate, IntervalCoordinate))
        )
    )


def prepare_condition_memberships(
    table: ir.Table,
    contribution: ContributionBinding,
    columns: Mapping[str, str],
    entities: Mapping[str, ir.Table],
    state: CompiledSemanticState,
    *,
    field_value: Callable[[str, ir.Table], ir.Value],
    source_times: list[FrameSourceTime],
) -> tuple[ir.Table, tuple[tuple[str, str], ...]]:
    """Classify every fact once while retaining the condition's original input domain."""
    facts = table
    reads = dict(contribution.population_reads)
    allocated = {
        read
        for membership in contribution.memberships
        for read in membership_reads(membership, contribution.root)
    }
    marks: dict[PopulationDependency, str] = {}
    conditions = (
        *contribution.intrinsic_conditions,
        *((contribution.common_condition,) if contribution.common_condition is not None else ()),
    )
    for condition in conditions:
        for domain in condition_populations(condition):
            if domain in marks:
                continue
            selected = prepare_population(domain, entities, state)
            index = len(marks)
            marker = f"__predicate_present_{index}"
            while marker in table.columns:
                marker += "_"
            renamed = {
                key: f"__predicate_{index}_{i}" for i, key in enumerate(selected.key_columns)
            }
            identity_columns = {
                key
                for axis, group in zip(domain.axes, selected.key_groups, strict=True)
                if isinstance(axis, AxisBinding) and axis.field is None
                for key in group
            }
            provider = table._find_backend().name
            allowed = _domain_table(selected).view()
            allowed = (
                allowed.select(
                    **{
                        new: (
                            string_bytes(allowed[old], provider)
                            if old in identity_columns and allowed[old].type().is_string()
                            else allowed[old]
                        )
                        for old, new in renamed.items()
                    }
                )
                if renamed
                else allowed
            )
            # Complete identity tuples already form a set. Native DISTINCT can
            # collapse byte-distinct string identities before their byte match.
            exact_keys = tuple(
                key
                for key in selected.key_columns
                if key in identity_columns
                or (
                    not allowed[renamed[key]].type().is_string()
                    and not allowed[renamed[key]].type().is_floating()
                )
            )
            if (
                domain.axes
                and not selected.unique_keys.proves(exact_keys)
                and not all(
                    isinstance(axis, AxisBinding) and axis.field is None for axis in domain.axes
                )
            ):
                allowed = allowed.distinct()
            private_endpoint = any(
                isinstance(node, AttributeKeys)
                for node in walk((domain,), resolve=lambda _: None).order
            )
            related = {
                index: (_witness_path(read), read if isinstance(read, FieldRead) else None)
                for index, axis in enumerate(domain.axes)
                if isinstance(axis, AxisBinding)
                and requires_path_witness(read := reads[axis])
                and not (private_endpoint and read in allocated)
            }
            ordinary = not related and all(isinstance(axis, AxisBinding) for axis in domain.axes)
            carrier = table if ordinary else facts.view()
            carrier, witness_keys, inputs = _related_columns(
                carrier,
                related,
                entities,
                state,
                field_value=field_value,
                source_times=source_times,
            )
            predicates: list[ir.BooleanValue] = []
            ranges: list[ir.BooleanValue] = []
            for position, (axis, group) in enumerate(
                zip(domain.axes, selected.key_groups, strict=True)
            ):
                if isinstance(axis, (CalendarCoordinate, IntervalCoordinate)):
                    from marivo.analysis.compiler.observation import calendar_field
                    from marivo.analysis.model.errors import reject

                    if contribution.time is None:
                        reject(
                            "a contribution clock for time coordinates in in_population",
                            contribution.root.ref.key,
                            "Use a Metric bound to the intended TimeField.",
                            "Metric.where",
                            canonical_id="in_population",
                        )
                    clock_read = FieldRead(
                        contribution.time.field,
                        contribution.root,
                        contribution.time.path,
                        contribution.time.at,
                        source_at=contribution.time.source_at,
                        step_ats=contribution.time.step_ats,
                    )
                    clock = columns[field_read_key(clock_read)]
                    time = carrier[clock]
                    calendar_slot = next(
                        (slot for slot, item in contribution.calendars if item == axis), None
                    )
                    if private_endpoint and contribution.window_key is not None:
                        buckets = ibis.literal(contribution.window_key)
                    elif (
                        private_endpoint
                        and calendar_slot is not None
                        and calendar_field(calendar_slot) in columns
                    ):
                        slot = columns[calendar_field(calendar_slot)]
                        inputs.add(slot)
                        buckets = carrier[slot]
                    else:
                        from marivo.analysis.compiler.observation import temporal_bound

                        target = allowed[renamed[group[0]]]
                        cells = population_time_ranges(axis)
                        inputs.add(clock)
                        if not cells:
                            ranges.append(ibis.literal(False))
                            continue
                        ranges.append(
                            ibis.or_(
                                *(
                                    (target == ibis.literal(key, type=target.type()))
                                    & (time >= temporal_bound(time, start, axis.timezone))
                                    & (time < temporal_bound(time, end, axis.timezone))
                                    for key, start, end in cells
                                )
                            )
                        )
                        continue
                    predicates.append(buckets == allowed[renamed[group[0]]])
                    continue
                assert isinstance(axis, AxisBinding)
                read = reads[axis]
                if position in witness_keys:
                    matches = witness_keys[position]
                else:
                    matches = tuple(columns[key] for key in coordinate_read_keys(read))
                    inputs.update(matches)
                for left, right in zip(matches, group, strict=True):
                    left_value, right_value = carrier[left], allowed[renamed[right]]
                    if axis.field is None:
                        predicate = _identity_value(left_value, provider) == right_value
                    else:
                        predicate = left_value.identical_to(right_value)
                    assert isinstance(predicate, ir.BooleanValue)
                    predicates.append(predicate)
            if ordinary:
                allowed = allowed.mutate(**{marker: ibis.literal(True)})
                table = table.left_join(allowed, predicates or ibis.literal(True)).select(
                    table, allowed[marker]
                )
            else:
                table = _mark_matching_facts(
                    table, carrier, allowed, predicates, ranges, tuple(sorted(inputs)), marker
                )
            table = table.mutate(**{marker: table[marker].fill_null(False)})
            if contribution.memberships and not private_endpoint:
                identity = normalize_target_entity(state.registry, contribution.root.ref.path)
                root_keys = identity.version_row_key or identity.primary_key
                matched = (
                    table[marker]
                    .any()
                    .over(ibis.window(group_by=[table[key] for key in root_keys]))
                )
                table = table.mutate(**{marker: matched})
            marks[domain] = marker
    return table, tuple((population_key(domain), marker) for domain, marker in marks.items())


def requires_path_witness(read: FieldRead | EntityIdentityRead) -> bool:
    """Identify a captured explicit role with a fanout occurrence."""
    return any(step.cardinality in ("one_to_many", "many_to_many") for step in read.path_facts)


def _witness_path(read: FieldRead | EntityIdentityRead) -> PathParameter:
    anchors = (
        *read.step_ats,
        *(None for _ in range(len(read.path_facts) - len(read.step_ats) - 1)),
        fixed_attribute_time(read.at),
    )
    return PathParameter(
        tuple(
            RelationshipStepParameter(step, anchor)
            for step, anchor in zip(read.path_facts, anchors, strict=True)
        ),
        read.source_at,
    )


def _identity_value(value: ir.Value, provider: str) -> ir.Value:
    return string_bytes(value, provider) if value.type().is_string() else value


def _related_columns(
    carrier: ir.Table,
    related: Mapping[int, tuple[PathParameter, FieldRead | None]],
    entities: Mapping[str, ir.Table],
    state: CompiledSemanticState,
    *,
    field_value: Callable[[str, ir.Table], ir.Value],
    source_times: list[FrameSourceTime],
) -> tuple[ir.Table, dict[int, tuple[str, ...]], set[str]]:
    """Attach witnesses to a detached relation, sharing rows for anchored path prefixes."""
    from marivo.analysis.materialization.population_relations import traverse_membership_paths

    groups: dict[InstantAnchor | None, list[tuple[int, PathParameter, FieldRead | None]]] = {}
    for index, (path, field) in related.items():
        groups.setdefault(path.source_at, []).append((index, path, field))
    inputs: set[str] = set()
    result: dict[int, tuple[str, ...]] = {}
    for anchor, group in groups.items():
        start = group[0][1].source.ref.path
        source = entities[start]
        if anchor is None:
            current = {name: name for name in source.columns}
            for _, path, _ in group:
                first = normalize_target_relationship(
                    state.registry, path.steps[0].relationship.ref.ref.path
                )
                inputs.update(left for left, _ in first.keys)
        else:
            contract = normalize_target_entity(state.registry, start)
            assert contract.version is not None
            target = prepare_version_filter(
                source.view(),
                contract.version,
                normalize_target_version_selection(
                    contract, boundary=anchor, interpretation="instant"
                ),
                state=state,
                source_times=source_times,
            )
            prefix = "__predicate_version_"
            while any(prefix + name in carrier.columns for name in target.columns):
                prefix += "_"
            current = {name: prefix + name for name in target.columns}
            target = target.rename(**{alias: name for name, alias in current.items()})
            provider = carrier._find_backend().name
            carrier = carrier.inner_join(
                target,
                [
                    _identity_value(carrier[key], provider)
                    == _identity_value(target[current[key]], provider)
                    for key in contract.primary_key
                ],
            ).select(carrier, target)
            inputs.update(contract.primary_key)
        carrier, keys = traverse_membership_paths(
            carrier,
            current,
            tuple((path, field) for _, path, field in group),
            entities,
            state,
            field_value=field_value,
            source_times=source_times,
        )
        result.update((index, columns) for (index, _, _), columns in zip(group, keys, strict=True))
    return carrier, result, inputs


def _mark_matching_facts(
    facts: ir.Table,
    candidates: ir.Table,
    allowed: ir.Table,
    predicates: list[ir.BooleanValue],
    ranges: list[ir.BooleanValue],
    inputs: tuple[str, ...],
    marker: str,
) -> ir.Table:
    """Keep one witness marker per complete set of physical membership inputs."""
    matches = candidates.inner_join(allowed, predicates or ibis.literal(True))
    if ranges:
        matches = matches.filter(*ranges)
    provider = facts._find_backend().name
    aliases = tuple(f"{marker}_{i}" for i in range(len(inputs)))
    hits = matches.select(
        **{
            alias: _identity_value(matches[name], provider)
            for name, alias in zip(inputs, aliases, strict=True)
        },
        **{marker: ibis.literal(True)},
    ).distinct()
    return facts.left_join(
        hits,
        [
            _identity_value(facts[name], provider).identical_to(hits[alias])
            for name, alias in zip(inputs, aliases, strict=True)
        ]
        or ibis.literal(True),
    ).select(facts, hits[marker])
