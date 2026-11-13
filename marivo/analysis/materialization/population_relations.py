"""Relational identity images and bounded preimages over complete typed keys."""

from __future__ import annotations

from collections.abc import Callable, Mapping

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo._definition.relations import (
    PopulationClassify,
    PopulationImage,
    PopulationPreimage,
    PopulationProgram,
    SourceIdentities,
)
from marivo._definition.types import AxisBinding, FieldRead, PathParameter
from marivo._temporal import InstantAnchor
from marivo.analysis.compiler.member_version import prepare_version_filter
from marivo.analysis.compiler.source_predicates import string_bytes
from marivo.analysis.core.frame_interpretation import FrameSourceTime
from marivo.analysis.core.model import reject
from marivo.analysis.materialization.population_preparation import (
    PreparedPopulation,
    _identity_source,
    prepare_population,
)
from marivo.semantic._compiled_state import CompiledSemanticState
from marivo.semantic.validator import (
    normalize_target_entity,
    normalize_target_relationship,
    normalize_target_version_selection,
)


def prepare_member_fields(
    definition: PopulationProgram,
    node: PopulationClassify,
    entities: Mapping[str, ir.Table],
    state: CompiledSemanticState,
    groups: tuple[tuple[str, ...], ...],
    *,
    source_times: list[FrameSourceTime] | None = None,
    consumption_timezone: str = "UTC",
) -> PreparedPopulation:
    """Extend complete input tuples from the selected captured subject keys."""
    from dataclasses import replace

    from marivo._definition.expressions import EntityCount
    from marivo._definition.observations import ContributionBinding
    from marivo._definition.types import IdentityRead, coordinate_read_keys
    from marivo.analysis.materialization.population_preparation import _domain_table, _retain_checks
    from marivo.analysis.materialization.query_preparation import prepare_fields

    operand = prepare_population(
        node.operand,
        entities,
        state,
        source_times=source_times,
        consumption_timezone=consumption_timezone,
    )
    axis = node.operand.axes[node.subject]
    assert isinstance(axis, AxisBinding)
    entity = axis.entity.ref.path
    original = entities[entity]
    contract = normalize_target_entity(state.registry, entity)
    domain = _domain_table(operand)
    provider = original._find_backend().name
    subject_keys = operand.key_groups[node.subject]
    reads = node.reads

    def same_identity(left: ir.Value, right: ir.Value) -> ir.BooleanValue:
        if left.type().is_string() and right.type().is_string():
            left, right = string_bytes(left, provider), string_bytes(right, provider)
        result = left == right
        assert isinstance(result, ir.BooleanValue)
        return result

    def subject_matches(keys: ir.Table, rows: ir.Table) -> list[ir.BooleanValue]:
        return [
            same_identity(keys[slot], rows[key])
            for key, slot in zip(contract.primary_key, subject_keys, strict=True)
        ]

    member_node = node.operand.root if isinstance(node.operand, PopulationProgram) else None
    reuse_members = isinstance(member_node, SourceIdentities) and (
        contract.version is None
        or all(not read.path and read.at == member_node.at for read in reads)
    )
    if isinstance(member_node, SourceIdentities) and reuse_members:
        source, _, _ = _identity_source(
            member_node,
            entities,
            state,
            consumption_timezone=consumption_timezone,
        )
        if contract.version is not None:
            reads = tuple(replace(read, at=None) for read in reads)
    elif contract.version is not None:
        # A versioned key is only a lookup input. Each scalar read supplies
        # its own explicit version, independent of how the key was selected.
        source = domain.select(
            **{
                key: domain[slot]
                for key, slot in zip(contract.primary_key, subject_keys, strict=True)
            }
        )
        if len(operand.key_groups) != 1:
            source = source.distinct()
    else:
        source = original.view()
        source = source.semi_join(domain, subject_matches(domain, source))

    contribution = ContributionBinding(
        (), EntityCount(axis.entity, None), axis.entity, (), None, (), reads, None, None
    )
    prepared = prepare_fields(
        contribution,
        entities,
        state,
        root_table=source,
        consumption_timezone=consumption_timezone,
    )
    if source_times is not None:
        source_times.extend(prepared.source_times)
    table = prepared.table
    columns = dict(prepared.columns)
    added = groups[len(operand.key_groups) :]
    field_slots = tuple(
        tuple(f"__classify_value_{index}_{part}" for part in range(len(group)))
        for index, group in enumerate(added)
    )
    attributes = table.select(
        *contract.primary_key,
        **{
            name: table[columns[key]]
            for read, slots in zip(reads, field_slots, strict=True)
            for name, key in zip(slots, coordinate_read_keys(read), strict=True)
        },
        **{name: table[name] for name, _, _ in prepared.checks},
    )
    joined = (
        attributes
        if reuse_members
        else domain.left_join(attributes, subject_matches(domain, attributes))
    )
    values = (
        {
            slot: attributes[key]
            for key, slot in zip(contract.primary_key, subject_keys, strict=True)
        }
        if reuse_members
        else {slot: domain[slot] for slot in operand.key_columns}
    )
    values.update(
        (name, attributes[slot])
        for group, slots in zip(added, field_slots, strict=True)
        for name, slot in zip(group, slots, strict=True)
    )
    checks = list(prepared.checks)
    flags = {name: attributes[name].fill_null(False) for name, _, _ in checks}
    for index, (read, slots) in enumerate(zip(reads, field_slots, strict=True)):
        if isinstance(read, IdentityRead):
            output_axis = definition.axes[len(operand.key_groups) + index]
            assert isinstance(output_axis, AxisBinding)
            from marivo._definition.population_builders import axis_description

            flag = f"__classify_identity_missing_{index}"
            flags[flag] = ibis.or_(*(attributes[slot].isnull() for slot in slots))
            checks.append(
                (
                    flag,
                    "a non-null complete identity for every input tuple",
                    axis_description(output_axis),
                )
            )
    result = PreparedPopulation(joined.select(**values, **flags), groups, tuple(checks))
    return _retain_checks(result, operand)


def traverse_identity_paths(
    allocation: ir.Table,
    source_columns: Mapping[str, str],
    paths: tuple[PathParameter, ...],
    entities: Mapping[str, ir.Table],
    state: CompiledSemanticState,
    *,
    source_times: list[FrameSourceTime] | None = None,
) -> tuple[ir.Table, tuple[tuple[str, ...], ...]]:
    """Traverse identity paths, including any terminal keys carried by the previous row."""
    return traverse_membership_paths(
        allocation,
        source_columns,
        tuple((path, None) for path in paths),
        entities,
        state,
        source_times=source_times,
    )


def traverse_membership_paths(
    allocation: ir.Table,
    source_columns: Mapping[str, str],
    paths: tuple[tuple[PathParameter, FieldRead | None], ...],
    entities: Mapping[str, ir.Table],
    state: CompiledSemanticState,
    *,
    field_value: Callable[[str, ir.Table], ir.Value] | None = None,
    source_times: list[FrameSourceTime] | None = None,
) -> tuple[ir.Table, tuple[tuple[str, ...], ...]]:
    """Share each anchored prefix, including scalar attributes of a related identity."""
    prefixes: dict[
        tuple[tuple[str, InstantAnchor | None, bool], ...], tuple[Mapping[str, str], str]
    ] = {}
    result_groups: list[tuple[str, ...]] = []
    required_keys: dict[str, None] = {}
    for path, read in paths:
        prefix: tuple[tuple[str, InstantAnchor | None, bool], ...] = ()
        current = source_columns
        remaining_fanouts = sum(
            step.relationship.cardinality in ("one_to_many", "many_to_many") for step in path.steps
        )
        terminal_keys = normalize_target_entity(state.registry, path.target.ref.path).primary_key
        matched: str | None = None
        for index, step in enumerate(path.steps):
            relationship = step.relationship.ref.ref.path
            relation = normalize_target_relationship(state.registry, relationship)
            contract = normalize_target_entity(state.registry, relation.to_entity_ref.path)
            mapping = {right: current[left] for left, right in relation.keys}
            many = relation.cardinality in ("one_to_many", "many_to_many")
            remaining_fanouts -= many
            final = index == len(path.steps) - 1
            if read is None and final and set(terminal_keys).issubset(mapping):
                if step.target_at is not None:
                    reject(
                        "a terminal relationship with no unused anchor",
                        relation.to_entity_ref.path,
                        "Remove the terminal relationship.at(...). Use members at that time to check existence.",
                        "population.image",
                    )
                current = mapping
                continue
            optional = read is not None and not many and not remaining_fanouts
            prefix = (*prefix, (relationship, step.target_at, optional))
            captured = prefixes.get(prefix)
            if captured is not None:
                current, matched = captured
                continue
            target = entities[relation.to_entity_ref.path].view()
            if contract.version is not None:
                if step.target_at is None:
                    reject(
                        "an exact version instant for the required path node",
                        f"node {index + 1} ({relation.to_entity_ref.path})",
                        "Set relationship.at(session.instant(...)) on that step.",
                        "population.image",
                    )
                target = prepare_version_filter(
                    target,
                    contract.version,
                    normalize_target_version_selection(
                        contract, boundary=step.target_at, interpretation="instant"
                    ),
                    state=state,
                    source_times=source_times,
                )
            name_prefix = f"__path_node_{len(prefixes)}_"
            match_flag = "__path_matched"
            while match_flag in target.columns:
                match_flag += "_"
            target = target.mutate(**{match_flag: ibis.literal(True)})
            while any(name_prefix + name in allocation.columns for name in target.columns):
                name_prefix += "_"
            renamed = {name: name_prefix + name for name in target.columns}
            target = target.rename(**{new: old for old, new in renamed.items()})
            allocation = allocation.join(
                target,
                [allocation[column] == target[renamed[key]] for key, column in mapping.items()],
                how="left" if optional else "inner",
            ).select(allocation, target)
            current = {name: alias for name, alias in renamed.items() if name != match_flag}
            matched = renamed[match_flag]
            prefixes[prefix] = current, matched
        if read is None:
            keys = tuple(current[key] for key in terminal_keys)
            result_groups.append(keys)
            required_keys.update(dict.fromkeys(keys))
        else:
            assert field_value is not None and matched is not None
            terminal = allocation.select(
                **{name: allocation[alias] for name, alias in current.items()}
            )
            value = field_value(read.field.ref.path, terminal)
            expression = (
                value.op()
                .replace(
                    {
                        ops.Field(terminal.op(), name): allocation[alias].op()
                        for name, alias in current.items()
                    }
                )
                .to_expr()
            )
            assert isinstance(expression, ir.Value)
            slot = f"__path_value_{len(result_groups)}"
            while slot in allocation.columns:
                slot += "_"
            allocation = allocation.mutate(
                **{slot: allocation[matched].fill_null(False).ifelse(expression, ibis.null())}
            )
            result_groups.append((slot,))
    if required_keys:
        allocation = allocation.filter(*(allocation[key].notnull() for key in required_keys))
    return allocation, tuple(result_groups)


def prepare_relationship_population(
    node: PopulationImage | PopulationPreimage,
    entities: Mapping[str, ir.Table],
    state: CompiledSemanticState,
    key_groups: tuple[tuple[str, ...], ...],
    *,
    source_times: list[FrameSourceTime] | None = None,
    consumption_timezone: str = "UTC",
) -> PreparedPopulation:
    """Traverse distinct subject keys and retain complete tuples for a preimage."""
    from marivo.analysis.materialization.population_preparation import _domain_table, _retain_checks

    operand = prepare_population(
        node.operand,
        entities,
        state,
        source_times=source_times,
        consumption_timezone=consumption_timezone,
    )
    source_entity = node.path.source.ref.path
    contract = normalize_target_entity(state.registry, source_entity)
    source = entities[source_entity].view()
    provider = source._find_backend().name

    def same_identity(left: ir.Value, right: ir.Value) -> ir.BooleanValue:
        if left.type().is_string() and right.type().is_string():
            left, right = string_bytes(left, provider), string_bytes(right, provider)
        predicate = left == right
        assert isinstance(predicate, ir.BooleanValue)
        return predicate

    if contract.version is not None:
        if node.path.source_at is None:
            reject(
                "an exact path source version instant",
                source_entity,
                "Set source_at=session.instant(...) on mv.path(...).",
                "population.image",
            )
        source = prepare_version_filter(
            source,
            contract.version,
            normalize_target_version_selection(
                contract, boundary=node.path.source_at, interpretation="instant"
            ),
            state=state,
            source_times=source_times,
        )
    domain = _domain_table(operand)
    origin = operand.key_groups[node.subject]
    origin_names = tuple(f"__path_source_{index}" for index in range(len(origin)))
    while set(origin_names).intersection(source.columns):
        origin_names = tuple(name + "_" for name in origin_names)
    selection = node.operand.root if isinstance(node.operand, PopulationProgram) else None
    direct_source = (
        isinstance(selection, SourceIdentities)
        and selection.entity.ref == node.path.source.ref
        and selection.predicate is None
        and selection.at == node.path.source_at
    )
    if direct_source:
        # The path's bound source is exactly the unfiltered identity selection.
        # Keep its check summary, but avoid joining the same keys back twice.
        source = source.filter(*(source[key].notnull() for key in contract.primary_key))
        allocation = source.mutate(
            **{
                name: source[key]
                for key, name in zip(contract.primary_key, origin_names, strict=True)
            }
        )
    else:
        identities = domain.select(
            **{name: domain[slot] for name, slot in zip(origin_names, origin, strict=True)}
        ).distinct()
        allocation = source.inner_join(
            identities,
            [
                same_identity(source[key], identities[name])
                for key, name in zip(contract.primary_key, origin_names, strict=True)
            ],
        ).select(source, identities)
    allocation, (reached,) = traverse_identity_paths(
        allocation,
        {name: name for name in source.columns},
        (node.path,),
        entities,
        state,
        source_times=source_times,
    )
    if isinstance(node, PopulationImage):
        output = allocation.select(
            **{
                slot: allocation[column]
                for slot, column in zip(key_groups[0], reached, strict=True)
            }
        ).distinct()
        return _retain_checks(PreparedPopulation(output, key_groups), operand)

    targets = prepare_population(
        node.targets,
        entities,
        state,
        source_times=source_times,
        consumption_timezone=consumption_timezone,
    )
    target_rows = _domain_table(targets).view()
    selected = (
        allocation.inner_join(
            target_rows,
            [
                same_identity(allocation[left], target_rows[right])
                for left, right in zip(reached, targets.key_groups[0], strict=True)
            ],
        )
        .select(*(allocation[name] for name in origin_names))
        .distinct()
    )
    output = (
        selected.select(
            **{slot: selected[name] for slot, name in zip(origin, origin_names, strict=True)}
        )
        if direct_source
        else domain.inner_join(
            selected,
            [
                same_identity(domain[left], selected[right])
                for left, right in zip(origin, origin_names, strict=True)
            ],
        ).select(domain)
    )
    return _retain_checks(PreparedPopulation(output, key_groups), operand, targets)
