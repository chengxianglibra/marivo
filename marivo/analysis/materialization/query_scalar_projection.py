"""Project qualified scalar relationship and identity reads onto a source relation."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TypeAlias

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo._definition.observations import ContributionBinding
from marivo._definition.types import (
    AttributeTime,
    EntityIdentityRead,
    ExactRef,
    FieldRead,
    RowAttributeTime,
    TimeInstantFieldRead,
    coordinate_read_keys,
)
from marivo.analysis.binding.coordinates import identity_projection
from marivo.analysis.binding.membership import membership_reads
from marivo.analysis.compiler.member_version import row_version_predicate, version_predicate
from marivo.analysis.compiler.numeric_sql import duration_ticks, interval_part
from marivo.analysis.compiler.source_time import entity_engine
from marivo.analysis.materialization.population_condition import requires_path_witness
from marivo.analysis.materialization.query_calendar_projection import QueryCalendarProjection
from marivo.analysis.materialization.query_field_scope import QueryFieldScope
from marivo.semantic.spi import (
    normalize_target_entity,
    normalize_target_relationship,
    normalize_target_version_selection,
)

ScalarRead: TypeAlias = FieldRead | EntityIdentityRead
Route: TypeAlias = tuple[tuple[ExactRef | None, AttributeTime | None], ...]


@dataclass(slots=True)
class ScalarProjection:
    table: ir.Table
    columns: list[tuple[str, str]] = field(default_factory=list)
    originals: list[tuple[str, ir.Value]] = field(default_factory=list)
    checks: list[tuple[str, str, str]] = field(default_factory=list)

    def check_durations(self, unique: Callable[[str], str]) -> None:
        table = self.table
        for _, slot in self.columns:
            if table[slot].type().is_interval():
                amount = table[slot]
                check = unique(f"__duration_check_{len(self.checks)}")
                invalid = (
                    (interval_part(ibis.literal("year"), amount) != 0)
                    | (interval_part(ibis.literal("month"), amount) != 0)
                    | (interval_part(ibis.literal("day"), amount) != 0)
                )
                table = table.mutate(**{check: invalid, slot: duration_ticks(amount)})
                self.checks.append(
                    (check, "fixed elapsed Duration without calendar components", slot)
                )
        self.table = table


def project_scalar_fields(
    table: ir.Table,
    scope: QueryFieldScope,
    entities: Mapping[str, ir.Table],
    calendar: QueryCalendarProjection,
) -> ScalarProjection:
    projected = ScalarProjection(table)
    projector = _ScalarFieldProjector(
        scope, entities, calendar, projected, scope.unique("__route_matched")
    )
    parts, time_read = _projection_reads(scope.contribution)
    for index, (read, component, read_key) in enumerate(parts):
        projector.project(index, read, component, read_key, time_read)
    projected.table = calendar.bind(projected.table, projected.columns, projected.originals)
    projected.check_durations(scope.unique)
    return projected


def _projection_reads(
    contribution: ContributionBinding,
) -> tuple[tuple[tuple[ScalarRead, str | None, str], ...], FieldRead | None]:
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
                *(
                    read
                    for _, read in contribution.population_reads
                    if not requires_path_witness(read)
                ),
            )
        )
        if read not in member_reads
    )
    time_read = next(
        (
            read
            for read in reads
            if isinstance(read, FieldRead)
            and not isinstance(read, TimeInstantFieldRead)
            and contribution.time is not None
            and read.field == contribution.time.field
            and read.path == contribution.time.path
            and read.at == contribution.time.at
            and read.source_at == contribution.time.source_at
            and read.step_ats == contribution.time.step_ats
        ),
        None,
    )
    if time_read is not None and time_read in reads:
        reads = (time_read, *(read for read in reads if read != time_read))
    parts = tuple(
        (read, component, key)
        for read in reads
        for component, key in zip(
            read.components if isinstance(read, EntityIdentityRead) else (None,),
            coordinate_read_keys(read),
            strict=True,
        )
    )
    return parts, time_read


@dataclass(slots=True)
class _ScalarFieldProjector:
    scope: QueryFieldScope
    entities: Mapping[str, ir.Table]
    calendar: QueryCalendarProjection
    projected: ScalarProjection
    match_flag: str
    routes: dict[Route, tuple[ir.Table, dict[str, str]]] = field(default_factory=dict)

    def project(
        self,
        index: int,
        read: ScalarRead,
        component: str | None,
        read_key: str,
        time_read: FieldRead | None,
    ) -> None:
        contribution, registry = self.scope.contribution, self.scope.registry
        table = self.projected.table
        extra_keys: dict[str, str] = {}

        def time_columns(expression: ir.Value) -> dict[str, ir.Value]:
            if read != time_read:
                return {}
            assert isinstance(read, FieldRead)
            extras, keys = self.calendar.project_clock(read, read_key, expression)
            self.projected.originals.extend(extras.items())
            extra_keys.update(keys)
            return extras

        def read_value(relation: ir.Table) -> ir.Value:
            if component is not None:
                return relation[component]
            assert isinstance(read, FieldRead)
            return self.scope.value(
                read.field.ref.path,
                relation,
                instant_read=isinstance(read, TimeInstantFieldRead),
            )

        slot = self.scope.unique(f"__field_{index}")
        projection = (
            identity_projection(read, contribution.root, registry)
            if isinstance(read, EntityIdentityRead)
            else None
        )
        if projection is not None and component is not None:
            assert isinstance(read, EntityIdentityRead)
            expression = table[projection[read.components.index(component)]]
            self.scope.dependencies.append(expression)
            self.projected.originals.append((slot, expression))
            table = table.mutate(**{slot: expression})
        elif not read.path and read.at is None and read.source_at is None:
            expression = read_value(table)
            self.scope.dependencies.append(expression)
            self.projected.originals.append((slot, expression))
            table = table.mutate(**{slot: expression}, **time_columns(expression))
        else:
            table = self._project_route(
                table, index, slot, read, component, read_value, time_columns
            )
        columns = self.projected.columns
        columns.append((read_key, slot))
        columns.extend((key, name) for name, key in extra_keys.items())
        if read == time_read:
            assert isinstance(read, FieldRead)
            table = self.calendar.filter_routed_clock(table, read, dict(columns))
        self.projected.table = table

    def _project_route(
        self,
        table: ir.Table,
        index: int,
        slot: str,
        read: ScalarRead,
        component: str | None,
        read_value: Callable[[ir.Table], ir.Value],
        time_columns: Callable[[ir.Value], dict[str, ir.Value]],
    ) -> ir.Table:
        contribution, registry, entities = (
            self.scope.contribution,
            self.scope.registry,
            self.entities,
        )
        match_flag = self.match_flag
        current = {name: name for name in entities[contribution.root.ref.path].columns}
        route: tuple[tuple[ExactRef | None, AttributeTime | None], ...] = tuple(
            (
                step,
                read.at
                if index == len(read.path) - 1
                else read.step_ats[index]
                if index < len(read.step_ats)
                else None,
            )
            for index, step in enumerate(read.path)
        ) or ((None, read.at),)
        if read.source_at is not None:
            route = ((None, read.source_at), *route)
        for step_index, (step, anchor) in enumerate(route):
            final = step_index == len(route) - 1
            if step is None:
                target_path = read.entity.ref.path if final else contribution.root.ref.path
                keys = tuple((name, name) for name in registry.entities[target_path].primary_key)
            else:
                relation = normalize_target_relationship(registry, step.ref.path)
                target_path = relation.to_entity_ref.path
                keys = relation.keys
                if final and isinstance(read, EntityIdentityRead):
                    foreign_keys = {right: left for left, right in keys}
                    if all(name in foreign_keys for name in read.components):
                        assert component is not None
                        expression = table[current[foreign_keys[component]]]
                        self.scope.dependencies.append(expression)
                        self.projected.originals.append((slot, expression))
                        table = table.mutate(**{slot: expression})
                        return table
            route_key = route[: step_index + 1]
            if route_key in self.routes:
                # Match through retained columns, never a fresh target relation.
                target, current = self.routes[route_key]
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
                    self.scope.dependencies.append(expression)
                    self.projected.originals.append((slot, expression))
                    table = table.mutate(**{slot: expression}, **time_columns(expression))
                continue
            target = entities[target_path].view()
            for left, right in keys:
                self.scope.dependencies.extend((table[current[left]], target[right]))
            target, row_condition = _versioned_target(
                self.scope, table, target, target_path, anchor
            )
            clock_columns: dict[str, ir.Value] = {}
            if final:
                expression = read_value(target)
                self.scope.dependencies.append(expression)
                self.projected.originals.append((slot, expression))
                clock_columns = time_columns(expression)
                target = target.mutate(**{slot: expression}, **clock_columns)
            prefix = f"__route_{index}_{step_index}_"
            grouped = target.mutate(**{match_flag: ibis.literal(True)})
            renamed = {name: self.scope.unique(prefix + name) for name in grouped.columns}
            grouped = grouped.rename(**{new: old for old, new in renamed.items()})
            joined = table.left_join(
                grouped,
                [table[current[left]] == grouped[renamed[right]] for left, right in keys]
                + ([] if row_condition is None else [row_condition]),
            )
            table = joined.select(table, *(grouped[name] for name in grouped.columns))
            current = renamed
            self.routes[route_key] = (target, renamed)
            if final:
                table = table.mutate(
                    **{slot: table[renamed[slot]]},
                    **{name: table[renamed[name]] for name in clock_columns},
                )
        return table


def _versioned_target(
    scope: QueryFieldScope,
    table: ir.Table,
    target: ir.Table,
    target_path: str,
    anchor: AttributeTime | None,
) -> tuple[ir.Table, ir.BooleanValue | None]:
    registry = scope.registry
    row_condition: ir.BooleanValue | None = None
    if anchor is not None:
        contract = normalize_target_entity(registry, target_path)
        if isinstance(anchor, RowAttributeTime):
            control = scope.value(anchor.field.ref.path, table)
            control_authority = scope.time_encodings[anchor.field.ref.path][2]
            row_condition = row_version_predicate(
                target,
                contract.version,
                control,
                control_authority,
                engine=entity_engine(registry, contract),
                state=scope.state,
                dependencies=scope.dependencies,
                source_times=scope.source_times,
            )
        else:
            selection = normalize_target_version_selection(
                contract, boundary=anchor, interpretation="instant"
            )
            version_condition = version_predicate(
                target,
                contract.version,
                selection,
                state=scope.state,
                dependencies=scope.dependencies,
                source_times=scope.source_times,
            )
            target = target.filter(version_condition)
    return target, row_condition
