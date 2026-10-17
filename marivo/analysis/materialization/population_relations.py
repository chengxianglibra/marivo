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
)
from marivo._definition.types import AxisBinding
from marivo.analysis.compiler.member_version import version_predicate
from marivo.analysis.core.model import reject
from marivo.analysis.materialization.population_preparation import (
    PreparedPopulation,
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
    if contract.version is not None:
        if axis.at is not None:
            source = source.filter(
                version_predicate(
                    source,
                    contract.version,
                    normalize_target_version_selection(
                        contract, boundary=axis.at, interpretation="instant"
                    ),
                )
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
    source = source.semi_join(
        domain,
        [
            source[key].identical_to(domain[slot])
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
    columns = dict(prepared.columns)
    field_names = tuple(f"__classify_value_{index}" for index in range(len(fields)))
    grouped = table.group_by(*contract.primary_key).aggregate(
        **{
            name: table[columns[field_read_key(read)]].arbitrary()
            for name, read in zip(field_names, fields, strict=True)
        },
        __classify_count=table.count(),
        **{name: table[name].any().fill_null(False) for name, _, _ in prepared.checks},
    )
    joined = domain.left_join(
        grouped,
        [
            domain[slot].identical_to(grouped[key])
            for key, slot in zip(contract.primary_key, identities.key_groups[0], strict=True)
        ],
    )
    values: dict[str, ir.Value] = {}
    field_index = 0
    for output_axis, group in zip(definition.axes, groups, strict=True):
        assert isinstance(output_axis, AxisBinding)
        if output_axis.field is None:
            values.update(
                (name, domain[slot])
                for name, slot in zip(group, identities.key_groups[0], strict=True)
            )
        else:
            values[group[0]] = grouped[field_names[field_index]]
            field_index += 1
    check = "__population_classify_invalid"
    output = joined.select(
        **values,
        **{check: grouped.__classify_count.fill_null(0) != 1},
        **{name: grouped[name].fill_null(False) for name, _, _ in prepared.checks},
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
        output.distinct(),
        groups,
        (
            *identities.checks,
            (check, "exactly one attribute row for every requested identity", entity),
            *prepared.checks,
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
    if contract.version is not None:
        if axis.at is None:
            reject(
                "an exact source identity anchor",
                axis.entity.ref.key,
                "Select the identity domain with at=.",
                "population.image",
            )
        source = source.filter(
            version_predicate(
                source,
                contract.version,
                normalize_target_version_selection(
                    contract, boundary=axis.at, interpretation="instant"
                ),
            )
        )
    missing = operand.table.anti_join(
        source,
        [
            operand.table[slot] == source[key]
            for key, slot in zip(contract.primary_key, operand.key_groups[0], strict=True)
        ],
    )
    # The receiver limits the relation before traversal; this never discovers
    # additional source identities outside the requested finite domain.
    allocation = source.inner_join(
        operand.table,
        [
            source[key] == operand.table[slot]
            for key, slot in zip(contract.primary_key, operand.key_groups[0], strict=True)
        ],
    ).select(source, operand.table)
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
                anchor = target_axis.at
            if anchor is None:
                reject(
                    "an exact version anchor for every required attribute read",
                    relation.to_entity_ref.path,
                    "Provide at= for the target or use a path with fully determined keys.",
                    "population.image",
                )
            target = target.filter(
                version_predicate(
                    target,
                    target_contract.version,
                    normalize_target_version_selection(
                        target_contract, boundary=anchor, interpretation="instant"
                    ),
                )
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
        }
    ).distinct()
    check = "__population_image_missing_source"
    invalid_rows = missing.select(
        **{slot: ibis.literal(None, type=output[slot].type()) for slot in key_groups[0]},
        **{check: ibis.literal(True)},
    )
    output = output.mutate(**{check: ibis.literal(False)}).union(invalid_rows)
    return PreparedPopulation(
        output,
        key_groups,
        ((check, "a source relationship row for every requested identity", axis.entity.ref.key),),
    )
