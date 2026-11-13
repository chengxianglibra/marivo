"""Source-side membership allocation retaining complete contribution identities."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

import ibis
import ibis.expr.types as ir

from marivo._definition.population_builders import membership_anchor
from marivo._definition.relations import MembershipBinding
from marivo._definition.types import ExactRef, field_read_key
from marivo.analysis.binding.membership import membership_path, membership_reads
from marivo.analysis.compiler.member_version import prepare_version_filter
from marivo.analysis.core.frame_interpretation import FrameSourceTime
from marivo.analysis.core.model import reject
from marivo.semantic._compiled_state import CompiledSemanticState
from marivo.semantic.validator import (
    normalize_target_entity,
    normalize_target_relationship,
    normalize_target_version_selection,
)


@dataclass(frozen=True, slots=True)
class PreparedMemberships:
    table: ir.Table
    columns: tuple[tuple[str, str], ...]
    originals: tuple[tuple[str, ir.Value], ...]
    dependencies: tuple[ir.Value, ...]


def prepare_memberships(
    table: ir.Table,
    root: ExactRef,
    bindings: tuple[MembershipBinding, ...],
    entities: Mapping[str, ir.Table],
    state: CompiledSemanticState,
    value: Callable[[str, ir.Table], ir.Value],
    unique: Callable[[str], str],
    *,
    sample_positions: Mapping[MembershipBinding, int] | None = None,
    source_times: list[FrameSourceTime] | None = None,
) -> PreparedMemberships:
    """Join one set of complete root/bucket pairs per membership factor.

    Correlated fields are read from one final member row. Distinct is applied
    only to complete root identity and bucket tuples, never to numeric values.
    """
    root_contract = normalize_target_entity(state.registry, root.ref.path)
    keys = root_contract.version_row_key or root_contract.primary_key
    columns: list[tuple[str, str]] = []
    originals: list[tuple[str, ir.Value]] = []
    dependencies: list[ir.Value] = []
    for group, binding in enumerate(bindings):
        path = membership_path(binding, root)
        source = entities[root.ref.path].view()
        key_slots = tuple(unique(f"__member_{group}_key_{index}") for index in range(len(keys)))
        allocation = source.mutate(
            **{slot: source[key] for key, slot in zip(keys, key_slots, strict=True)}
        )
        current = {name: name for name in source.columns}
        if path is not None and path.source_at is not None:
            assert root_contract.version is not None
            anchored = prepare_version_filter(
                source.view(),
                root_contract.version,
                normalize_target_version_selection(
                    root_contract, boundary=path.source_at, interpretation="instant"
                ),
                state=state,
                dependencies=dependencies,
                source_times=source_times,
            )
            root_columns = {
                name: unique(f"__member_{group}_source_{name}") for name in source.columns
            }
            anchored = anchored.rename(**{new: old for old, new in root_columns.items()})
            allocation = allocation.join(
                anchored,
                [
                    allocation[key] == anchored[root_columns[key]]
                    for key in root_contract.primary_key
                ],
                how="left" if binding.unmatched == "null" else "inner",
            ).select(allocation, anchored)
            current = root_columns
        steps = () if path is None else path.steps
        for index, step in enumerate(steps):
            assert path is not None
            relation = normalize_target_relationship(state.registry, step.relationship.ref.ref.path)
            target = entities[relation.to_entity_ref.path].view()
            contract = normalize_target_entity(state.registry, relation.to_entity_ref.path)
            if contract.version is not None:
                anchor = (
                    membership_anchor(binding.fields, path)
                    if index == len(steps) - 1
                    else step.target_at
                )
                if anchor is None:
                    reject(
                        "an exact member version anchor",
                        relation.to_entity_ref.path,
                        "Anchor this Path step with relationship.at(session.instant(...)).",
                        "membership.prepare",
                    )
                selection = normalize_target_version_selection(
                    contract, boundary=anchor, interpretation="instant"
                )
                target = prepare_version_filter(
                    target,
                    contract.version,
                    selection,
                    state=state,
                    dependencies=dependencies,
                    source_times=source_times,
                )
            renamed = {name: unique(f"__member_{group}_{index}_{name}") for name in target.columns}
            target = target.rename(**{new: old for old, new in renamed.items()})
            predicates = [
                allocation[current[left]] == target[renamed[right]] for left, right in relation.keys
            ]
            dependencies.extend(allocation[current[left]] for left, _ in relation.keys)
            joined = allocation.join(
                target, predicates, how="left" if binding.unmatched == "null" else "inner"
            )
            allocation = joined.select(allocation, *(target[name] for name in target.columns))
            current = renamed
        # Restore the final member's original schema for its captured field body.
        member = allocation.select(
            **{name: allocation[slot] for name, slot in current.items()},
            **{slot: allocation[slot] for slot in key_slots},
        )
        slots: list[str] = []
        expressions: dict[str, ir.Value] = {}
        for index, field in enumerate(binding.fields):
            slot = unique(f"__member_{group}_field_{index}")
            expression = value(field.field.ref.path, member)
            slots.append(slot)
            expressions[slot] = expression
            originals.append((slot, expression))
            dependencies.append(expression)
        pairs = member.select(
            *(member[slot] for slot in key_slots),
            **expressions,
        ).distinct()
        # Internal allocation must retain the incoming parent coordinates even
        # when no sample bucket exists. Its marker keeps that carrier row out
        # of the numeric contribution and sample domains below this layer.
        sample_position = None if sample_positions is None else sample_positions.get(binding)
        assigned = unique(f"__member_{group}_assigned") if sample_position is not None else None
        if assigned is not None:
            pairs = pairs.mutate(**{assigned: ibis.literal(True)})
        joined = table.join(
            pairs,
            [table[key] == pairs[slot] for key, slot in zip(keys, key_slots, strict=True)],
            how="left" if assigned is not None else "inner",
        )
        table = joined.select(
            table,
            *(pairs[slot] for slot in slots),
            *((pairs[assigned],) if assigned is not None else ()),
        )
        if assigned is not None:
            table = table.mutate(**{assigned: table[assigned].fill_null(False)})
            columns.append((f"__sample_assigned_{sample_position}", assigned))
            originals.append((assigned, table[assigned]))
        reads = membership_reads(binding, root)
        columns.extend(
            (field_read_key(read), slot) for read, slot in zip(reads, slots, strict=True)
        )
    return PreparedMemberships(table, tuple(columns), tuple(originals), tuple(dependencies))
