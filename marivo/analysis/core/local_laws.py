"""Explicit local transformations; state equations never grant source placement."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Literal, TypeAlias

from marivo._definition.expressions import (
    DeclaredMetricRef,
    EntityCount,
    MetricNode,
    SourceAggregate,
)
from marivo._definition.query import FilterValues, RollupValues
from marivo._definition.types import ExactRef
from marivo.analysis.core.data_types import DataColumn, DataType, DecimalDataType, ScalarDataType
from marivo.analysis.core.graph import Edge, MethodNode, method_node, topology
from marivo.analysis.core.model import (
    Binding,
    CoveragePart,
    ObservedQuantity,
    OriginalStatePart,
    RolledQuantity,
    Signature,
    SubjectPart,
    reject,
    require_part,
)
from marivo.analysis.core.predicates import leaves
from marivo.analysis.core.rules import OriginalReduce, PartsTransport
from marivo.analysis.methods.physical import ScalarType
from marivo.analysis.methods.registry import REGISTRY, MethodRegistry
from marivo.analysis.methods.semantics import MethodKey


def query_selection_fusion_issue(
    first: FilterValues,
    second: FilterValues,
    value_columns: tuple[DataColumn, ...],
    columns: tuple[DataColumn, ...],
    key_groups: tuple[tuple[str, ...], ...],
    *,
    single_consumer: bool,
    same_layout: bool,
    pending_checks: bool,
    original_boundary: bool,
    registry: MethodRegistry = REGISTRY,
) -> tuple[str, str] | None:
    """L1 adapter for the current Query's same-mask nullable int64 comparisons.

    Both filters retain only True, so a common nullable comparison mask has
    exactly the same selected keys under sequential application and Kleene
    conjunction. Arithmetic, presence selection and independent domains remain
    outside this bounded law.
    """
    from marivo._definition.query import (
        CoordinateSlot,
        ValueBinary,
        ValueExpression,
        ValueLiteral,
        ValueSlot,
    )

    if "L1" not in registry.lookup(MethodKey("parts_transport")).semantics.local_laws:
        return "a method-registered selection law", "L1"
    if not single_consumer:
        return "one consumer of the intermediate selection", "multiple consumers"
    if pending_checks:
        return "completed checks before selection fusion", "a pending check boundary"
    if original_boundary:
        return "an unchanged original fine-grid completion boundary", "original completion"
    if not same_layout or first.domains or second.domains:
        return "same-layout comparisons on the same actual input", "different domains or layout"
    by_slot = {column.slot_id: column for column in columns}

    def operand(expression: ValueExpression) -> bool:
        if isinstance(expression, ValueLiteral):
            return type(expression.value) is int and -(2**63) <= expression.value < 2**63
        if isinstance(expression, ValueSlot):
            return 0 <= expression.index < len(value_columns) and value_columns[
                expression.index
            ].dtype == ScalarDataType("int64")
        if isinstance(expression, CoordinateSlot):
            return (
                0 <= expression.index < len(key_groups)
                and all(
                    by_slot[slot].dtype == ScalarDataType("int64")
                    for slot in key_groups[expression.index]
                )
                and len(key_groups[expression.index]) == 1
            )
        return False

    def predicate(expression: object) -> bool:
        if not isinstance(expression, ValueBinary):
            return False
        if expression.operation in ("and", "or"):
            return predicate(expression.left) and predicate(expression.right)
        return (
            expression.operation in ("eq", "ne", "lt", "le", "gt", "ge")
            and operand(expression.left)
            and operand(expression.right)
        )

    if not predicate(first.predicate) or not predicate(second.predicate):
        return (
            "closed int64 comparisons with the same drop-NA mask policy",
            "unqualified predicate or numeric operation",
        )
    return None


def query_original_fusion_issue(
    first: RollupValues,
    second: RollupValues,
    outputs: tuple[MetricNode, ...],
    declared: Mapping[ExactRef, MetricNode],
    dtypes: tuple[DataType, ...],
    *,
    single_consumer: bool,
    same_components: bool,
    pending_checks: bool,
    ordinary: bool,
    registry: MethodRegistry = REGISTRY,
) -> tuple[str, str] | None:
    """L8 ordered direct-key group with every original finish retained.

    Unlike eliminating a fixed-state finish, the group executes both checked
    reductions in their original order. Thus no regrouping bound is inferred
    from a final total, and intermediate int64/Decimal failures still occur.
    """
    if "L8" not in registry.lookup(MethodKey("state_rollup")).semantics.local_laws:
        return "a method-registered original state law", "L8"
    if not single_consumer:
        return "one consumer of the intermediate original reduction", "multiple consumers"
    if pending_checks:
        return "fixed external check positions", "a pending check boundary"
    if not same_components or not ordinary or first.calendar_maps or second.calendar_maps:
        return (
            "identical ordinary components and direct retained axes",
            "time/sample/component boundary",
        )
    if not all(
        dtype == ScalarDataType("int64") or isinstance(dtype, DecimalDataType) for dtype in dtypes
    ):
        return "exact int64 or Decimal original quantities", "unqualified numeric finish"

    def leaf(node: MetricNode) -> bool:
        if isinstance(node, DeclaredMetricRef):
            return leaf(declared[node.ref])
        return isinstance(node, EntityCount) or (
            isinstance(node, SourceAggregate) and node.reducer in ("sum", "count")
        )

    if not outputs or not all(leaf(node) for node in outputs):
        return "direct exact source sum/count finishes", "formula/per/window finish boundary"
    return None


Key: TypeAlias = tuple[int, ...]


def _fail(expected: str, received: str) -> None:
    reject(
        expected,
        received,
        "Preserve the registered law's fixed input, roles, state and scope.",
        "analysis.local_law",
    )


def _registered(method: MethodKey, law: str, registry: MethodRegistry) -> None:
    if law not in registry.lookup(method).semantics.local_laws:
        _fail("a method-registered local law", law)


def selection_fusion_issue(
    node: MethodNode, *, registry: MethodRegistry = REGISTRY
) -> tuple[str, str] | None:
    """Inspect captured L1 conditions without deriving or constructing a new graph."""
    if "L1" not in registry.lookup(node.method).semantics.local_laws:
        return "a method-registered local law", "L1"
    if node.value_type != ScalarType("int64"):
        return "the qualified int64 selection law", repr(node.value_type)
    outer = node.parameters
    inner = node.inputs[0].node if len(node.inputs) == 1 else None
    if not isinstance(outer, PartsTransport) or not isinstance(inner, MethodNode):
        return "two adjacent registered selections", node.identity
    prior = inner.parameters
    if (
        not isinstance(prior, PartsTransport)
        or inner.value_type != node.value_type
        or len(inner.inputs) != 1
        or node.inputs[0].role != inner.inputs[0].role
        or "L1" not in registry.lookup(inner.method).semantics.local_laws
        or prior.mode != "where"
        or outer.mode != "where"
        or not prior.predicates
        or not outer.predicates
        or not prior.keep_quantity
        or prior.external_predicate
        or outer.external_predicate
        or replace(prior, predicates=()) != replace(outer, predicates=())
        or len({p.unknown for p in (*prior.predicates, *outer.predicates)}) != 1
        or inner.signature.quantity != node.signature.quantity
        or inner.signature.parts != node.signature.parts
        or inner.derivation.part_transform != node.derivation.part_transform
    ):
        return "same-domain selections with identical parts and unknown policy", node.identity
    if any(
        leaf.operator == "is_defined"
        for tree in (*prior.predicates, *outer.predicates)
        for leaf in leaves(tree)
    ):
        return (
            "ordinary predicates on a common fully Defined domain",
            "tag selection changes the consumption domain",
        )
    return None


def fuse_selection(node: MethodNode, *, registry: MethodRegistry = REGISTRY) -> MethodNode:
    """L1 for closed total int64 comparisons, preserving the exact output contract."""
    topology(node)
    issue = selection_fusion_issue(node, registry=registry)
    if issue is not None:
        _fail(*issue)
    outer = node.parameters
    inner = node.inputs[0].node
    assert isinstance(outer, PartsTransport) and isinstance(inner, MethodNode)
    prior = inner.parameters
    assert isinstance(prior, PartsTransport)
    combined = method_node(
        (Edge(inner.inputs[0].role, inner.inputs[0].node),),
        replace(outer, predicates=(*prior.predicates, *outer.predicates)),
        value_type=node.value_type,
        registry=registry,
    )
    if (
        replace(
            combined.signature, node_id=node.identity, key_domain_id=node.signature.key_domain_id
        )
        != node.signature
        or registry.continuations(combined.signature) != registry.continuations(node.signature)
        or combined.derivation.part_transform != node.derivation.part_transform
    ):
        _fail("identical quantity, parts, evidence, obligations and K", node.identity)
    return combined


def original_reduction_fusion_issue(
    node: MethodNode, *, registry: MethodRegistry = REGISTRY
) -> tuple[str, str] | None:
    """Inspect L8 direct-key composition without rewriting the captured graph."""
    outer = node.parameters
    inner = node.inputs[0].node if len(node.inputs) == 1 else None
    if not isinstance(outer, OriginalReduce) or not isinstance(inner, MethodNode):
        return "two adjacent original-state reductions", node.identity
    prior = inner.parameters
    if (
        not isinstance(prior, OriginalReduce)
        or len(inner.inputs) != 1
        or node.inputs[0].role != inner.inputs[0].role
        or prior.method != outer.method
        or outer.method
        not in ("sum", "sum_zero", "count", "mean", "ratio", "weighted_mean", "linear")
        or prior.time_mapping
        or outer.time_mapping
        or inner.value_type != node.value_type
        or any("L8" not in registry.lookup(n.method).semantics.local_laws for n in (inner, node))
    ):
        return "same-method direct-key reductions with the captured finish", node.identity
    for current in (inner, node):
        source = current.inputs[0].node.signature
        params = current.parameters
        assert isinstance(params, OriginalReduce)
        if (
            not set(params.coordinates) <= set(source.domain.instance_key)
            or any(
                not isinstance(p, (SubjectPart, OriginalStatePart, CoveragePart))
                for p in source.parts
            )
            or any(
                isinstance(p, CoveragePart) and p.business_windows is not None for p in source.parts
            )
        ):
            return "complete direct-key mappings and ordinary original parts", current.identity
    first = next((p for p in inner.signature.parts if isinstance(p, OriginalStatePart)), None)
    second = next((p for p in node.signature.parts if isinstance(p, OriginalStatePart)), None)
    if first is None or first != second or first.fold_kind is not None:
        return "identical complete original components, bindings and empty policy", node.identity
    return None


@dataclass(frozen=True, slots=True)
class FixedMapping:
    """A complete fixed coordinate function; roles survive composition."""

    binding: Binding
    source_id: str
    target_id: str
    roles: tuple[str, ...]
    rows: tuple[tuple[Key, Key], ...]

    def __post_init__(self) -> None:
        if (
            type(self.binding) is not Binding
            or type(self.rows) is not tuple
            or type(self.roles) is not tuple
            or not self.source_id
            or not self.target_id
            or not self.roles
            or not all(self.roles)
            or len({a for a, _ in self.rows}) != len(self.rows)
            or any(
                not a or not b or any(type(v) is not int for v in (*a, *b)) for a, b in self.rows
            )
        ):
            _fail("a single-valued complete fixed mapping", self.source_id)

    @property
    def injective(self) -> bool:
        return len({b for _, b in self.rows}) == len(self.rows)


def compose_mapping(
    first: FixedMapping, second: FixedMapping, *, registry: MethodRegistry = REGISTRY
) -> FixedMapping:
    """L7 coordinate composition only; non-injectivity grants no rollup capability."""
    _registered(MethodKey("map_correspond"), "L7", registry)
    if first.binding != second.binding or first.target_id != second.source_id:
        _fail("the same fixed input and connected role path", second.source_id)
    lookup = dict(second.rows)
    if any(target not in lookup for _, target in first.rows):
        _fail("total mapping on the intermediate image", second.source_id)
    return FixedMapping(
        first.binding,
        first.source_id,
        second.target_id,
        (*first.roles, *second.roles),
        tuple((key, lookup[target]) for key, target in first.rows),
    )


@dataclass(frozen=True, slots=True)
class SumState:
    total: int
    non_null_count: int
    contributions: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            type(self.total) is not int
            or type(self.contributions) is not tuple
            or type(self.non_null_count) is not int
            or not -(2**63) <= self.total < 2**63
            or self.non_null_count < 0
            or self.non_null_count > len(self.contributions)
            or (self.non_null_count == 0 and self.total != 0)
            or len(set(self.contributions)) != len(self.contributions)
            or any(not c for c in self.contributions)
        ):
            _fail("complete checked-int64 sum state with distinct contributions", repr(self))


@dataclass(frozen=True, slots=True)
class FixedStates:
    signature: Signature
    rows: tuple[tuple[Key, SumState], ...]

    def __post_init__(self) -> None:
        quantity = self.signature.quantity
        state = require_part(self.signature, "original_state")
        coverage = require_part(self.signature, "coverage")
        if (
            not isinstance(coverage, CoveragePart)
            or coverage.scope_id != self.signature.domain.binding.scope_id
            or not isinstance(quantity, (ObservedQuantity, RolledQuantity))
            or not isinstance(state, OriginalStatePart)
            or quantity.method_version != state.method_version
            or state.method_version != "sum@v1"
            or quantity.contribution_id != state.contribution_id
            or state.components != ("sum", "non_null_count")
            or state.version != "v1"
            or self.signature.obligations
            or len({k for k, _ in self.rows}) != len(self.rows)
            or any(
                len(k) != len(self.signature.domain.instance_key)
                or any(type(v) is not int for v in k)
                for k, _ in self.rows
            )
        ):
            _fail("completed fixed original sum state", repr(quantity))
        contributions = tuple(c for _, s in self.rows for c in s.contributions)
        if len(set(contributions)) != len(contributions):
            _fail("disjoint original contributions", "overlapping components")
        # This sufficient bound makes every regrouping safe, not just the final sum.
        if sum(abs(s.total) for _, s in self.rows) >= 2**63:
            _fail(
                "int64-safe state under every permitted regrouping",
                "possible intermediate overflow",
            )


def _merge(
    rows: tuple[tuple[Key, SumState], ...], mapping: FixedMapping, targets: tuple[Key, ...]
) -> tuple[tuple[Key, SumState], ...]:
    assignments = dict(mapping.rows)
    if (
        set(assignments) != {k for k, _ in rows}
        or len(set(targets)) != len(targets)
        or not set(assignments.values()) <= set(targets)
    ):
        _fail("a total assignment into the explicit target domain", mapping.target_id)
    return tuple(
        (
            target,
            SumState(
                sum(s.total for key, s in rows if assignments[key] == target),
                sum(s.non_null_count for key, s in rows if assignments[key] == target),
                tuple(
                    sorted(
                        c for key, s in rows if assignments[key] == target for c in s.contributions
                    )
                ),
            ),
        )
        for target in targets
    )


@dataclass(frozen=True, slots=True)
class StateEquation:
    """Only state equality. No semantic graph rewrite, K grant or source pushdown."""

    law: Literal["L8", "L9"]
    input_signature: Signature
    targets: tuple[Key, ...]
    left: tuple[tuple[Key, SumState], ...]
    right: tuple[tuple[Key, SumState], ...]
    comparison: Literal["state"] = "state"


def layered_state(
    states: FixedStates,
    first: FixedMapping,
    second: FixedMapping,
    intermediate: tuple[Key, ...],
    targets: tuple[Key, ...],
    *,
    registry: MethodRegistry = REGISTRY,
) -> StateEquation:
    """L8 merges complete components, including explicit empty intermediate groups."""
    _registered(MethodKey("state_rollup"), "L8", registry)
    if (
        states.signature.domain.binding != first.binding
        or states.signature.domain.definition_id != first.source_id
    ):
        _fail("the original fixed input scope and domain", first.source_id)
    composed = compose_mapping(first, second, registry=registry)
    left = _merge(_merge(states.rows, first, intermediate), second, targets)
    right = _merge(states.rows, composed, targets)
    return StateEquation("L8", states.signature, targets, left, right)


def restrict_state(
    states: FixedStates,
    mapping: FixedMapping,
    targets: tuple[Key, ...],
    selected: tuple[Key, ...],
    *,
    registry: MethodRegistry = REGISTRY,
) -> StateEquation:
    """L9 keeps the selected target domain, even when every selected group is empty."""
    _registered(MethodKey("state_rollup"), "L9", registry)
    if (
        states.signature.domain.binding != mapping.binding
        or states.signature.domain.definition_id != mapping.source_id
        or len(set(selected)) != len(selected)
        or not set(selected) <= set(targets)
    ):
        _fail("the same fixed scope and explicit selected target domain", mapping.source_id)
    full = dict(_merge(states.rows, mapping, targets))
    selected_mapping = replace(
        mapping, rows=tuple((a, b) for a, b in mapping.rows if b in selected)
    )
    selected_keys = {a for a, _ in selected_mapping.rows}
    restricted = tuple((k, s) for k, s in states.rows if k in selected_keys)
    return StateEquation(
        "L9",
        states.signature,
        selected,
        tuple((k, full[k]) for k in selected),
        _merge(restricted, selected_mapping, selected),
    )
