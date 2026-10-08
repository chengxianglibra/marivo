"""Typed in-memory fixed inputs for selection execution and cost probes."""

from __future__ import annotations

from typing import Literal

import pyarrow as pa

import marivo.semantic as ms
from marivo.analysis.compiler.graph_lowering import LoweredPlan, lower
from marivo.analysis.compiler.graph_plan import RouteChoice
from marivo.analysis.core.graph import Edge, FixedLeaf, MethodNode, Node, method_node, topology
from marivo.analysis.core.model import (
    Binding,
    Coordinate,
    CoveragePart,
    DomainSignature,
    ObservedQuantity,
    OriginalStatePart,
    Signature,
    SubjectPart,
    part_role,
)
from marivo.analysis.core.predicates import ValuePredicate
from marivo.analysis.core.rules import PartsTransport
from marivo.analysis.materialization.contracts import FileEntry, LocalReceipt
from marivo.analysis.materialization.graph_exchange import (
    ExchangeContract,
    ExchangePart,
    PartContract,
    VerifiedFixedInput,
    from_arrow,
)
from marivo.analysis.materialization.graph_execution import PreparedGraph, prepare_graph
from marivo.analysis.methods.physical import FixedShape, NoTime, ScalarType
from marivo.analysis.methods.semantics import MethodKey
from marivo.analysis.refs import ArtifactRef


def selection_input(
    count: int = 20, *, parts: bool = False, dtype: Literal["int64", "float64"] = "int64"
) -> tuple[FixedLeaf, VerifiedFixedInput]:
    """Model an already-read producer; no Store or source qualification is claimed."""
    binding = Binding("selection-session", "inventory", "products", "all")
    entity = ms.ref.entity("inventory.product")
    keys = (Coordinate(entity, "tenant", "identity"), Coordinate(entity, "id", "identity"))
    domain = DomainSignature(binding, "entity", keys, keys, "products")
    quantity = ObservedQuantity(
        "amount",
        ms.ref.metric("inventory.total"),
        "metric-v1",
        "units",
        "all",
        "contributions",
        "strict",
        "sum@v1",
    )
    retained = (
        (
            SubjectPart(binding, entity, keys, keys, True, True, "v1"),
            OriginalStatePart(
                binding, "amount", "sum@v1", "contributions", ("sum", "non_null_count"), "v1"
            ),
            CoveragePart(binding, "amount", "all", "v1"),
        )
        if parts
        else ()
    )
    signature = Signature(domain, quantity, retained)
    leaf = FixedLeaf(
        ArtifactRef(ref="selection-input"),
        "fixed-v1",
        signature,
        ScalarType(dtype),
        FixedShape(NoTime()),
    )
    primary = pa.table(
        {
            "key_0": pa.array([i % 3 for i in range(count)], type=pa.int64()),
            "key_1": pa.array([str(i) for i in range(count)], type=pa.string()),
            "value": pa.array(range(count), type=pa.int64() if dtype == "int64" else pa.float64()),
            "cell_tag": pa.array(["defined"] * count, type=pa.string()),
            "cell_reason": pa.array([None] * count, type=pa.string()),
        }
    )
    saved_parts = (
        (
            ExchangePart("subject", primary.select(("key_0", "key_1"))),
            ExchangePart(
                "original_state",
                primary.select(("key_0", "key_1"))
                .append_column("original_state__sum", primary["value"])
                .append_column(
                    "original_state__non_null_count", pa.array([1] * count, type=pa.int64())
                ),
            ),
            ExchangePart(
                "coverage",
                primary.select(("key_0", "key_1")).append_column(
                    "coverage__complete", pa.array([True] * count, type=pa.bool_())
                ),
            ),
        )
        if parts
        else ()
    )
    contract = ExchangeContract(
        signature,
        MethodKey("parts_transport"),
        leaf.artifact.ref,
        primary.schema,
        ("key_0", "key_1"),
        tuple(
            PartContract(part.role, part.table.schema, ("key_0", "key_1")) for part in saved_parts
        ),
    )
    result = from_arrow(primary, contract, parts=saved_parts, validate=False)
    receipt = LocalReceipt("selection.parquet", (FileEntry("selection.parquet", 1),), count, 1)
    return leaf, VerifiedFixedInput(leaf.artifact.ref, receipt, result)


def selection(
    node: Node, threshold: int, *, unknown: Literal["drop", "reject"] = "reject"
) -> MethodNode:
    return method_node(
        (Edge("quantity", node),),
        PartsTransport(
            "where",
            node.signature.domain,
            tuple(part_role(p) for p in node.signature.parts),
            True,
            (ValuePredicate(node.signature.domain.binding, "gt", threshold, unknown),),
        ),
        value_type=node.value_type,
    )


def selection_chain(node: Node, length: int, threshold: int) -> MethodNode:
    root = selection(node, threshold)
    for index in range(1, length):
        root = selection(root, threshold + index)
    return root


def selection_plan(root: MethodNode) -> tuple[PreparedGraph, LoweredPlan]:
    routes = tuple(
        RouteChoice(node.identity, "artifact_python")
        for node in topology(root)
        if isinstance(node, MethodNode)
    )
    prepared = prepare_graph(
        root, session_ref=root.signature.domain.binding.session_id, routes=routes
    )
    return prepared, lower(prepared.admitted, bindings=())
