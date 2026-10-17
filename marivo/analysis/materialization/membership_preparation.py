"""Source-side membership allocation retaining complete contribution identities."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

import ibis.expr.types as ir

from marivo._definition.relations import MembershipBinding
from marivo._definition.types import ExactRef, field_read_key
from marivo.analysis.binding.membership import membership_path, membership_reads
from marivo.analysis.compiler.member_version import version_predicate
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
        for index, step in enumerate(path):
            relation = normalize_target_relationship(state.registry, step.ref.ref.path)
            target = entities[relation.to_entity_ref.path].view()
            contract = normalize_target_entity(state.registry, relation.to_entity_ref.path)
            if contract.version is not None:
                anchors = {field.at for field in binding.fields}
                if (
                    relation.to_entity_ref.path != binding.fields[0].entity.ref.path
                    or len(anchors) != 1
                    or None in anchors
                ):
                    reject(
                        "an exact member version anchor",
                        relation.to_entity_ref.path,
                        "Bind each member field at the same explicit instant.",
                        "membership.prepare",
                    )
                anchor = next(iter(anchors))
                assert anchor is not None
                selection = normalize_target_version_selection(
                    contract, boundary=anchor, interpretation="instant"
                )
                target = target.filter(version_predicate(target, contract.version, selection))
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
        pairs = member.select(*(member[slot] for slot in key_slots), **expressions).distinct()
        joined = table.inner_join(
            pairs, [table[key] == pairs[slot] for key, slot in zip(keys, key_slots, strict=True)]
        )
        table = joined.select(table, *(pairs[slot] for slot in slots))
        reads = membership_reads(binding, root)
        columns.extend(
            (field_read_key(read), slot) for read, slot in zip(reads, slots, strict=True)
        )
    return PreparedMemberships(table, tuple(columns), tuple(originals), tuple(dependencies))
