"""Schema-only preparation of occurrence-specific fields and scalar relationships."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo._definition.types import FieldRead, IdentityRead, coordinate_read_keys, field_read_key
from marivo.analysis.binding.observe import ContributionBinding
from marivo.analysis.compiler.member_version import version_predicate
from marivo.analysis.compiler.source_time import localize, source_time, timestamp
from marivo.datasource.timezone import DatasourceEngineTimezone
from marivo.refs import SemanticKind, ref
from marivo.semantic._compiled_state import CompiledSemanticState
from marivo.semantic._expression_binding import evaluate_expression_body
from marivo.semantic.ir import HourPrefixParse, TargetSnapshotVersion, TargetValidityVersion
from marivo.semantic.metric_graph_lowering import numeric_failure
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


def prepare_fields(
    contribution: ContributionBinding,
    entities: Mapping[str, ir.Table],
    state: CompiledSemanticState,
    reader: DatasourceEngineTimezone,
) -> PreparedFields:
    """Prepare expressions and checked many-to-one joins without reading values."""
    registry = state.registry
    dependencies: list[ir.Value] = []
    zone = contribution.period.timezone if contribution.period is not None else "UTC"

    def value(path: str, table: ir.Table, authority_zone: str | None = None) -> ir.Value:
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
            if result.type().is_date() and registry.dimensions[path].parse is None:
                return result
            axis = normalize_target_dimension(registry, path, prepared_column=path)
            prefix = (
                value(axis.parse.prefix, table) if isinstance(axis.parse, HourPrefixParse) else None
            )
            result, authority = source_time(
                result,
                axis,
                boundary_timezone=authority_zone or zone,
                read_timezone=authority_zone or reader.engine_timezone_name,
                read_source=reader.read_tz_resolution,
                engine="duckdb",
                prefix=prefix,
            )
            if authority.kind != "civil_date" and result.type().timezone is None:
                result = localize(authority_zone or zone, timestamp(result))
        return result

    reserved = {name for entity in entities.values() for name in entity.columns}

    def unique(name: str) -> str:
        while name in reserved:
            name += "_"
        reserved.add(name)
        return name

    table = entities[contribution.root.ref.path].view()
    if (
        contribution.period is not None
        and contribution.time is not None
        and not contribution.time.path
        and contribution.time.at is None
    ):
        from marivo.analysis.compiler.observation import temporal_bound

        time = value(contribution.time.field.ref.path, table)
        conditions = [time < temporal_bound(time, contribution.period.end, zone)]
        if contribution.period.start is not None:
            conditions.append(time >= temporal_bound(time, contribution.period.start, zone))
        table = table.filter(*conditions)
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
            component: str | None = component,
            read: FieldRead | IdentityRead = read,
        ) -> ir.Value:
            if component is not None:
                return relation[component]
            assert isinstance(read, FieldRead)
            return value(read.field.ref.path, relation)

        slot = unique(f"__field_{index}")
        if not read.path and read.at is None:
            expression = read_value(table)
            dependencies.append(expression)
            originals.append((slot, expression))
            table = table.mutate(**{slot: expression})
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
                    if isinstance(version, TargetSnapshotVersion):
                        slot_name = unique("__snapshot_coordinate")
                        target = target.mutate(
                            **{
                                slot_name: value(
                                    version.coordinate_ref.path, target, version.timezone or "UTC"
                                )
                            }
                        )
                        version = replace(version, source_column=slot_name)
                    elif isinstance(version, TargetValidityVersion):
                        start_name, end_name = unique("__valid_from"), unique("__valid_to")
                        target = target.mutate(
                            **{
                                start_name: value(
                                    version.valid_from_ref.path, target, version.timezone or "UTC"
                                ),
                                end_name: value(
                                    version.valid_to_ref.path, target, version.timezone or "UTC"
                                ),
                            }
                        )
                        version = replace(
                            version, valid_from_column=start_name, valid_to_column=end_name
                        )
                    target = target.filter(version_predicate(target, version, selection))
                final = step_index == len(steps) - 1
                if final:
                    expression = read_value(target)
                    dependencies.append(expression)
                    originals.append((slot, expression))
                    target = target.mutate(**{slot: expression})
                # Collapse before joining; the count makes duplicates an error rather than fanout.
                right_keys = tuple(right for _, right in keys)
                prefix = f"__route_{index}_{step_index}_"
                count = unique(prefix + "count")
                grouped = target.group_by(*right_keys).aggregate(
                    **{
                        name: target[name].arbitrary()
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
                check = unique(f"__prepare_check_{len(checks)}")
                table = table.mutate(**{check: table[renamed[count]].fill_null(0) != 1})
                checks.append(
                    (check, "exactly one matching scalar field row", field_read_key(read))
                )
                current = renamed
                if final:
                    table = table.mutate(**{slot: table[renamed[slot]]})
        columns.append((read_key, slot))
    members = prepare_memberships(
        table, contribution.root, contribution.memberships, entities, state, value, unique
    )
    table = members.table
    columns.extend(members.columns)
    originals.extend(members.originals)
    dependencies.extend(members.dependencies)
    if contribution.population_conditions:
        from marivo.analysis.materialization.population_condition import filter_contribution

        table = filter_contribution(table, contribution, dict(columns), entities, state)
    return PreparedFields(
        table, tuple(columns), tuple(checks), tuple(originals), tuple(dependencies)
    )


def physical_fields(value: ir.Value) -> tuple[ops.Field, ...]:
    """Follow only consumed projection expressions through aliases to source fields."""
    found: dict[ops.Field, None] = {}

    def visit(node: ops.Value) -> None:
        if isinstance(node, ops.Field):
            definition = node.rel.values.get(node.name)
            if definition is not None:
                visit(definition)
            elif isinstance(node.rel, ops.SelfReference):
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
