"""Relational identity images and bounded preimages over complete typed keys."""

from __future__ import annotations

from collections.abc import Mapping

import ibis
import ibis.expr.types as ir

from marivo._definition.relations import (
    PopulationClassify,
    PopulationImage,
    PopulationPreimage,
    PopulationProgram,
    SourceIdentities,
)
from marivo._definition.types import AxisBinding, fixed_attribute_time
from marivo.analysis.compiler.member_version import prepare_version_filter
from marivo.analysis.compiler.source_predicates import string_bytes
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
) -> PreparedPopulation:
    """Read scalar attributes only after limiting the complete requested identities."""
    from dataclasses import replace

    from marivo._definition.expressions import EntityCount
    from marivo._definition.observations import ContributionBinding
    from marivo._definition.types import field_read_key
    from marivo.analysis.materialization.population_preparation import _domain_table
    from marivo.analysis.materialization.query_preparation import prepare_fields
    from marivo.datasource.timezone import probe_engine_timezone

    identities = prepare_population(node.operand, entities, state)
    axis = node.operand.axes[0]
    assert isinstance(axis, AxisBinding)
    entity = axis.entity.ref.path
    original = entities[entity]
    source = original.view()
    contract = normalize_target_entity(state.registry, entity)
    fields = tuple(
        replace(read, at=read.at or axis.at) if read.entity.ref == axis.entity.ref else read
        for read in node.fields
    )
    identity_node = (
        node.operand.root
        if isinstance(node.operand, PopulationProgram)
        and isinstance(node.operand.root, SourceIdentities)
        and node.operand.root.entity.ref == axis.entity.ref
        and node.operand.root.at == axis.at
        else None
    )
    direct_members = identity_node is not None
    if identity_node is not None:
        source, _, _ = _identity_source(identity_node, entities, state)
    elif contract.version is not None:
        if axis.at is not None:
            anchor = fixed_attribute_time(axis.at)
            assert anchor is not None
            source = prepare_version_filter(
                source,
                contract.version,
                normalize_target_version_selection(
                    contract, boundary=anchor, interpretation="instant"
                ),
                state=state,
            )
        elif all(
            read.at is not None and not read.path and read.entity.ref == axis.entity.ref
            for read in fields
        ):
            source = source.select(*contract.primary_key).distinct()
        else:
            reject(
                "exact identity/attribute anchors for a versioned read",
                entity,
                "Select the identity at an instant or anchor every direct field explicitly.",
                "Session.read",
            )
    domain = _domain_table(identities)
    provider = original._find_backend().name

    def same_identity(left: ir.Value, right: ir.Value) -> ir.BooleanValue:
        if left.type().is_string() and right.type().is_string():
            left, right = string_bytes(left, provider), string_bytes(right, provider)
        result = left == right
        assert isinstance(result, ir.BooleanValue)
        return result

    # Complete Entity identities have non-null keys on every backend. Retained
    # identity checks below still reject invalid null keys before cropping.
    # Equality lets source planners join these keys without null-safe filters.
    if not direct_members:
        source = source.semi_join(
            domain,
            [
                same_identity(source[key], domain[slot])
                for key, slot in zip(contract.primary_key, identities.key_groups[0], strict=True)
            ],
        )
    contribution = ContributionBinding(
        (), EntityCount(axis.entity, None), axis.entity, (), None, fields, (), None, None
    )
    prepared = prepare_fields(
        contribution,
        entities,
        state,
        probe_engine_timezone(original._find_backend()),
        root_table=source,
    )
    table = prepared.table
    prepared_checks = prepared.checks
    columns = dict(prepared.columns)
    field_names = tuple(f"__classify_value_{index}" for index in range(len(fields)))
    attributes = table.select(
        *contract.primary_key,
        **{
            name: table[columns[field_read_key(read)]]
            for name, read in zip(field_names, fields, strict=True)
        },
        **{name: table[name] for name, _, _ in prepared_checks},
    )
    # Exact members use their predicate-filtered source at the same anchor;
    # reading their attributes needs no identity self-joins.
    joined = (
        attributes
        if direct_members
        else domain.left_join(
            attributes,
            [
                same_identity(domain[slot], attributes[key])
                for key, slot in zip(contract.primary_key, identities.key_groups[0], strict=True)
            ],
        )
    )
    values: dict[str, ir.Value] = {}
    field_index = 0
    retains_identity = False
    for output_axis, group in zip(definition.axes, groups, strict=True):
        assert isinstance(output_axis, AxisBinding)
        if output_axis.field is None:
            retains_identity = True
            values.update(
                (name, attributes[key] if direct_members else domain[slot])
                for name, key, slot in zip(
                    group, contract.primary_key, identities.key_groups[0], strict=True
                )
            )
        else:
            values[group[0]] = attributes[field_names[field_index]]
            field_index += 1
    output = joined.select(
        **values,
        **{name: attributes[name].fill_null(False) for name, _, _ in prepared_checks},
    )
    presence = None
    if identities.checks:
        summary = identities.table.aggregate(
            **{
                name: identities.table[name].any().fill_null(False)
                for name, _, _ in identities.checks
            }
        )
        presence = "__read_identity_present"
        output = summary.left_join(
            output.mutate(**{presence: ibis.literal(True)}), ibis.literal(True)
        )
    return PreparedPopulation(
        output if retains_identity else output.distinct(),
        groups,
        (
            *identities.checks,
            *prepared_checks,
        ),
        presence,
    )


def prepare_relationship_population(
    definition: PopulationProgram,
    node: PopulationImage | PopulationPreimage,
    entities: Mapping[str, ir.Table],
    state: CompiledSemanticState,
    key_groups: tuple[tuple[str, ...], ...],
) -> PreparedPopulation:
    """Keep source identity separately while traversing declared key mappings."""
    operand = prepare_population(node.operand, entities, state)
    axis = node.operand.axes[0]
    assert isinstance(axis, AxisBinding)
    contract = normalize_target_entity(state.registry, axis.entity.ref.path)
    source = entities[axis.entity.ref.path].view()
    identity_node = (
        node.operand.root
        if isinstance(node.operand, PopulationProgram)
        and isinstance(node.operand.root, SourceIdentities)
        and node.operand.root.entity.ref == axis.entity.ref
        and node.operand.root.at == axis.at
        else None
    )
    if identity_node is not None:
        source, _, _ = _identity_source(identity_node, entities, state)
    elif contract.version is not None:
        if axis.at is None:
            reject(
                "an exact source identity anchor",
                axis.entity.ref.key,
                "Select the identity domain with at=.",
                "population.image",
            )
        source = prepare_version_filter(
            source,
            contract.version,
            normalize_target_version_selection(
                contract, boundary=fixed_attribute_time(axis.at), interpretation="instant"
            ),
            state=state,
        )
    # The receiver limits the relation before traversal; this never discovers
    # additional source identities outside the requested finite domain.
    allocation = (
        source.mutate(
            **{
                slot: source[key]
                for key, slot in zip(contract.primary_key, operand.key_groups[0], strict=True)
            }
        )
        if identity_node is not None
        else source.inner_join(
            operand.table,
            [
                source[key] == operand.table[slot]
                for key, slot in zip(contract.primary_key, operand.key_groups[0], strict=True)
            ],
        ).select(source, operand.table)
    )
    origin = operand.key_groups[0]
    current = {name: name for name in source.columns}
    result_columns: tuple[str, ...] = ()
    for index, step in enumerate(node.path):
        relation = normalize_target_relationship(state.registry, step.ref.ref.path)
        target_contract = normalize_target_entity(state.registry, relation.to_entity_ref.path)
        target_keys = target_contract.primary_key
        mapping = {right: current[left] for left, right in relation.keys}
        final = index == len(node.path) - 1
        if final and set(target_keys).issubset(mapping):
            # Foreign keys already determine the complete target identity.
            # Do not join the target profile and lose dangling references.
            result_columns = tuple(mapping[key] for key in target_keys)
            allocation = allocation.filter(
                *(allocation[column].notnull() for column in result_columns)
            )
            break
        target = entities[relation.to_entity_ref.path].view()
        if target_contract.version is not None:
            anchor = node.at if isinstance(node, PopulationImage) and final else None
            if isinstance(node, PopulationPreimage) and final:
                target_axis = node.targets.axes[0]
                assert isinstance(target_axis, AxisBinding)
                anchor = fixed_attribute_time(target_axis.at)
            if anchor is None:
                reject(
                    "an exact version anchor for every required attribute read",
                    relation.to_entity_ref.path,
                    "Provide at= for the target or use a path with fully determined keys.",
                    "population.image",
                )
            target = prepare_version_filter(
                target,
                target_contract.version,
                normalize_target_version_selection(
                    target_contract, boundary=anchor, interpretation="instant"
                ),
                state=state,
            )
        renamed = {name: f"__image_{index}_{name}" for name in target.columns}
        target = target.rename(**{new: old for old, new in renamed.items()})
        allocation = allocation.inner_join(
            target,
            [allocation[current[left]] == target[renamed[right]] for left, right in relation.keys],
        ).select(allocation, target)
        current = renamed
        result_columns = tuple(current[key] for key in target_keys)
    if isinstance(node, PopulationPreimage):
        targets = prepare_population(node.targets, entities, state)
        renamed_targets = targets.table.select(
            **{
                f"__image_target_{index}": targets.table[column]
                for index, column in enumerate(targets.key_groups[0])
            }
        )
        allocation = allocation.semi_join(
            renamed_targets,
            [
                allocation[column] == renamed_targets[f"__image_target_{index}"]
                for index, column in enumerate(result_columns)
            ],
        )
        result_columns = origin
    output = allocation.select(
        **{
            slot: allocation[column]
            for slot, column in zip(key_groups[0], result_columns, strict=True)
        },
    ).distinct()
    presence = None
    if operand.checks:
        summary = operand.table.aggregate(
            **{name: operand.table[name].any().fill_null(False) for name, _, _ in operand.checks}
        )
        presence = "__population_relation_present"
        output = summary.left_join(
            output.mutate(**{presence: ibis.literal(True)}), ibis.literal(True)
        )
    return PreparedPopulation(
        output,
        key_groups,
        operand.checks,
        presence,
    )
