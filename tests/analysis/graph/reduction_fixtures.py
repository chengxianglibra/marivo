"""Complete typed fixed states for original reduction behavior and cost probes."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from fractions import Fraction
from typing import Literal, TypeAlias

import pyarrow as pa

from marivo.analysis.compiler.graph_lowering import LoweredPlan
from marivo.analysis.core.graph import Edge, FixedLeaf, MethodNode, Node, method_node
from marivo.analysis.core.model import (
    DomainSignature,
    Evidence,
    Fact,
    FactKind,
    ObservedQuantity,
    OriginalStatePart,
)
from marivo.analysis.core.rules import OriginalReduce
from marivo.analysis.materialization.graph_exchange import (
    ExchangeContract,
    ExchangePart,
    PartContract,
    VerifiedFixedInput,
    from_arrow,
)
from marivo.analysis.materialization.graph_execution import PreparedGraph
from marivo.analysis.methods.physical import DecimalType, DurationType, ScalarType
from tests.analysis.graph.selection_fixtures import selection_input, selection_plan

OriginalMethod: TypeAlias = Literal[
    "sum", "sum_zero", "count", "mean", "ratio", "weighted_mean", "linear"
]
Carrier: TypeAlias = Literal["int64", "float64", "decimal", "duration"]


def reduction_input(
    count: int = 12, *, method: OriginalMethod = "sum", carrier: Carrier = "int64"
) -> tuple[FixedLeaf, VerifiedFixedInput]:
    """Model an already-read state; input Cells have an independent arithmetic oracle."""
    leaf, saved = selection_input(count, parts=True)
    output = (
        DecimalType(38, 2)
        if carrier == "decimal" and method not in ("ratio", "weighted_mean", "count")
        else DurationType("ns")
        if carrier == "duration"
        else ScalarType("float64")
        if carrier == "float64" or method in ("mean", "ratio", "weighted_mean")
        else ScalarType("int64")
    )
    physical = (
        pa.decimal128(38, 2)
        if carrier == "decimal"
        else pa.float64()
        if carrier == "float64"
        else pa.int64()
    )
    base = saved.result.primary.select(("key_0", "key_1"))
    components: dict[str, list[int | float | Decimal]] = {}
    values: list[int | float | Decimal] = []
    for i in range(count):
        amount = (
            Decimal(i + 1)
            if carrier == "decimal"
            else float(i + 1)
            if carrier == "float64"
            else i + 1
        )
        support = i % 3 + 1
        weight = i % 5 + 1
        row: dict[str, int | float | Decimal]
        value: int | float | Decimal
        if method == "count":
            row, value = {"count": support}, support
        elif method == "mean":
            row = {"sum": amount, "non_null_count": support, "row_count": support + 1}
            value = (
                round(Fraction(amount) / support)
                if carrier == "duration"
                else (Decimal(amount) / support).quantize(Decimal("0.01"))
                if carrier == "decimal"
                else float(amount) / support
            )
        elif method == "ratio":
            row = {
                "numerator_sum": amount,
                "numerator_non_null_count": support,
                "denominator_sum": weight,
                "denominator_non_null_count": 1,
            }
            value = float(amount) / weight
        elif method == "weighted_mean":
            row = {
                "weighted_numerator": amount * weight,
                "weight_sum": weight,
                "non_null_pair_count": support,
                "row_count": support + 1,
            }
            value = float(amount)
        elif method == "linear":
            row = {
                "plus_0_sum": amount,
                "plus_0_non_null_count": support,
                "minus_1_sum": amount,
                "minus_1_non_null_count": support,
            }
            value = (
                Decimal(0) if isinstance(amount, Decimal) else 0.0 if type(amount) is float else 0
            )
        else:
            row, value = {"sum": amount, "non_null_count": support}, amount
        if carrier == "float64":
            if method == "weighted_mean":
                row["absolute_weight_sum"] = float(weight)
            for name, magnitude in tuple(row.items()):
                if type(magnitude) is float and "absolute" not in name:
                    absolute = (
                        "absolute_weighted_numerator"
                        if name == "weighted_numerator"
                        else name.removesuffix("sum") + "absolute_sum"
                    )
                    row[absolute] = abs(magnitude)
        for name, magnitude in row.items():
            components.setdefault(name, []).append(magnitude)
        values.append(value)
    # Keep the same component layout even when there are no actual rows.
    if count == 0:
        _, nonempty = reduction_input(1, method=method, carrier=carrier)
        components = {
            name.removeprefix("original_state__"): []
            for name in nonempty.result.parts[1].table.column_names
            if name.startswith("original_state__")
        }
    names = tuple(components)
    original = next(p for p in leaf.signature.parts if isinstance(p, OriginalStatePart))
    quantity = leaf.signature.quantity
    assert isinstance(quantity, ObservedQuantity)
    quantity = replace(quantity, method_version=method + "@v1")
    original = replace(
        original,
        method_version=method + "@v1",
        components=names,
        empty_rules=("null", "null") if method in ("ratio", "linear") else (),
    )
    facts: tuple[tuple[FactKind, str], ...] = (
        ("contribution_partition", quantity.contribution_id),
        ("complete_coverage", quantity.definition_id),
    )
    evidence = tuple(
        Evidence(Fact(kind, leaf.signature.domain.binding, subject, "v1"), "check", "input-oracle")
        for kind, subject in facts
    )
    signature = replace(
        leaf.signature,
        quantity=quantity,
        parts=(leaf.signature.parts[0], original, leaf.signature.parts[2]),
        evidence=evidence,
    )
    leaf = replace(leaf, signature=signature, value_type=output)
    value_physical = (
        pa.duration("ns")
        if carrier == "duration"
        else pa.decimal128(38, 2)
        if isinstance(output, DecimalType)
        else pa.float64()
        if output == ScalarType("float64")
        else pa.int64()
    )
    primary = base.append_column("value", pa.array(values, type=value_physical))
    primary = primary.append_column("cell_tag", pa.array(["defined"] * count, type=pa.string()))
    primary = primary.append_column("cell_reason", pa.array([None] * count, type=pa.string()))
    state = base
    for name, magnitudes in components.items():
        dtype = (
            pa.int64()
            if name.endswith("count") or name in ("count", "weight_sum", "denominator_sum")
            else physical
        )
        state = state.append_column("original_state__" + name, pa.array(magnitudes, type=dtype))
    parts = (saved.result.parts[0], ExchangePart("original_state", state), saved.result.parts[2])
    contract = ExchangeContract(
        signature,
        saved.result.contract.method,
        leaf.artifact.ref,
        primary.schema,
        ("key_0", "key_1"),
        tuple(PartContract(p.role, p.table.schema, ("key_0", "key_1")) for p in parts),
    )
    return leaf, replace(saved, result=from_arrow(primary, contract, parts=parts, validate=False))


def reduction(node: Node, *, keep: tuple[int, ...] = ()) -> MethodNode:
    """Project complete retained keys; no new mapping or quantity is introduced."""
    original = next(p for p in node.signature.parts if isinstance(p, OriginalStatePart))
    coordinates = tuple(node.signature.domain.instance_key[i] for i in keep)
    target = DomainSignature(
        node.signature.domain.binding,
        "group" if keep else "singleton",
        coordinates,
        coordinates,
        "reduce-" + node.identity,
    )
    methods: dict[str, OriginalMethod] = {
        "sum@v1": "sum",
        "sum_zero@v1": "sum_zero",
        "count@v1": "count",
        "mean@v1": "mean",
        "ratio@v1": "ratio",
        "weighted_mean@v1": "weighted_mean",
        "linear@v1": "linear",
    }
    method = methods[original.method_version]
    params = OriginalReduce(target, method=method, coordinates=coordinates)
    return method_node((Edge("quantity", node),), params, value_type=node.value_type)


def reduction_chain(node: Node, length: int = 2) -> MethodNode:
    root = reduction(node, keep=(0,))
    for _ in range(length - 2):
        root = reduction(root, keep=(0,))
    return reduction(root)


def reduction_plan(root: MethodNode) -> tuple[PreparedGraph, LoweredPlan]:
    return selection_plan(root)
