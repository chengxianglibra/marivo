"""Relational identity images and bounded preimages over complete typed keys."""

from __future__ import annotations

from collections.abc import Mapping

import ibis
import ibis.expr.types as ir

from marivo._definition.relations import PopulationImage, PopulationPreimage, PopulationProgram
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
