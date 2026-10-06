"""Independent closed-envelope and capture-identity tests for unified snapshots."""

from __future__ import annotations

import base64
import zlib
from dataclasses import replace

import pytest

from marivo.analysis.core.graph import (
    Edge,
    FixedLeaf,
    MethodNode,
    SourceDefinition,
    SourceLeaf,
    capture_graph,
    method_node,
    topology,
)
from marivo.analysis.core.model import (
    Binding,
    Coordinate,
    DomainSignature,
    ObservedQuantity,
    Signature,
)
from marivo.analysis.core.rules import CellDerive, PartsTransport, RowState
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.materialization.graph_protocol import encode, freeze_graph, thaw_graph
from marivo.analysis.materialization.graph_snapshot import (
    GRAPH,
    PREFIX,
    GraphDocument,
    InputReference,
    MethodRecord,
    graph_document,
)
from marivo.analysis.methods.physical import FixedShape, NoTime, ScalarType, SourceShape
from marivo.analysis.refs import ArtifactRef
from marivo.refs import ref


def _observation() -> MethodNode:
    binding = Binding("snapshot-session", "sales", "customers", "all")
    key = (Coordinate(ref.entity("sales.customer"), "id", "identity"),)
    domain = DomainSignature(binding, "entity", key, key, "customers")
    quantity = ObservedQuantity(
        "revenue",
        ref.metric("sales.revenue"),
        "revenue-v1",
        "CNY",
        "all",
        "orders",
        "strict",
        "sum@v1",
    )
    leaf = SourceLeaf(
        SourceDefinition(
            quantity.metric_ref,
            "revenue-v1",
            ref.datasource("db"),
            SourceShape("duckdb", "table", "native", NoTime()),
        ),
        Signature(domain, quantity),
        ScalarType("int64"),
    )
    target = DomainSignature(binding, "singleton", (), (), "all")
    return method_node(
        (Edge("quantity", leaf),),
        RowState("count", target, "count", "count_all"),
        value_type=ScalarType("int64"),
    )


def _pair(left: MethodNode, right: MethodNode) -> MethodNode:
    return method_node(
        (Edge("current", left), Edge("baseline", right)),
        CellDerive(
            "difference",
            "difference",
            "strict",
            "count",
            "all",
            "source.exact_pairing@v1",
            "source.finite_numeric@v1",
        ),
        value_type=left.value_type,
    )


def _wire(document: GraphDocument) -> str:
    return _compress(encode(document, GRAPH))


def _compress(body: str) -> str:
    return PREFIX + base64.b64encode(zlib.compress(body.encode(), level=9)).decode("ascii")


def test_shared_graph_has_one_record_and_one_restored_object_per_capture() -> None:
    root = _observation()
    for depth in range(8):
        root = _pair(root, root)
        document = graph_document(root)
        assert len(document.nodes) == depth + 3
        restored = thaw_graph(freeze_graph(root))
        assert isinstance(restored, MethodNode)
        assert restored.inputs[0].node is restored.inputs[1].node
        assert restored.fingerprint == root.fingerprint
        assert freeze_graph(restored) == freeze_graph(root)


def test_equal_definitions_keep_independent_capture_identities() -> None:
    july_first = _observation()
    july_second = replace(july_first, identity="independent-july")
    assert july_first.fingerprint == july_second.fingerprint
    root = _pair(_pair(_observation(), july_first), _pair(july_second, _observation()))
    restored = thaw_graph(freeze_graph(root))
    assert isinstance(restored, MethodNode)
    first, second = (edge.node for edge in restored.inputs)
    assert isinstance(first, MethodNode) and isinstance(second, MethodNode)
    assert first.inputs[1].node is not second.inputs[0].node
    assert first.inputs[1].node.identity != second.inputs[0].node.identity


def test_retained_closure_shares_definitions_but_does_not_schedule_them() -> None:
    original = _observation()
    fixed = FixedLeaf(
        ArtifactRef("artifact"),
        original.fingerprint,
        original.signature,
        original.value_type,
        FixedShape(NoTime()),
    )
    node = method_node(
        (Edge("current", fixed), Edge("baseline", fixed)),
        CellDerive(
            "difference",
            "difference",
            "strict",
            "count",
            "all",
            "source.exact_pairing@v1",
            "source.finite_numeric@v1",
        ),
        value_type=original.value_type,
        retained_endpoints=(original, replace(original)),
    )
    captured = capture_graph(node)
    assert captured.nodes == (fixed, node)
    assert len(captured.retained) == 5
    assert captured.unchanged(node, captured.registry)
    alias = node.retained_endpoints[1]
    object.__setattr__(alias, "derivation", replace(alias.derivation, eval_id="tampered"))
    assert not captured.unchanged(node, captured.registry)
    object.__setattr__(alias, "derivation", original.derivation)
    document = graph_document(node)
    assert len(document.nodes) == 4
    restored = thaw_graph(freeze_graph(node))
    assert isinstance(restored, MethodNode)
    assert len(topology(restored)) == 2
    assert restored.retained_endpoints[0] is restored.retained_endpoints[1]
    assert restored.retained_endpoints[0].fingerprint == original.fingerprint


def test_conflicting_identity_rejects_even_below_equal_parent_records() -> None:
    original = _observation()
    other = replace(original)
    leaf = original.inputs[0].node
    assert isinstance(leaf, SourceLeaf)
    object.__setattr__(
        other,
        "inputs",
        (
            Edge(
                "quantity", replace(leaf, identity=leaf.identity, value_type=ScalarType("float64"))
            ),
        ),
    )
    root = _pair(original, original)
    object.__setattr__(root, "inputs", (Edge("current", original), Edge("baseline", other)))
    with pytest.raises(IntegrityError, match="different frozen definitions"):
        freeze_graph(root)


@pytest.mark.parametrize(
    "damage",
    [
        "duplicate",
        "missing",
        "cycle",
        "unreachable",
        "source_kind",
        "endpoint_kind",
        "order",
        "root",
        "roles",
        "endpoint_count",
        "endpoint_fingerprint",
    ],
)
def test_invalid_envelopes_reject_before_restore(damage: str) -> None:
    root = _pair(_observation(), _observation())
    document = graph_document(root)
    method = next(n for n in document.nodes if n.identity == root.identity)
    assert isinstance(method, MethodRecord)
    altered = method
    if damage == "duplicate":
        document = replace(document, nodes=(*document.nodes, method))
    elif damage == "unreachable":
        document = replace(
            document,
            nodes=tuple(
                sorted(
                    (*document.nodes, replace(method, identity="injected")),
                    key=lambda n: n.identity,
                )
            ),
        )
    elif damage == "order":
        document = replace(document, nodes=tuple(reversed(document.nodes)))
    elif damage == "root":
        document = replace(document, root="absent")
    else:
        if damage == "missing":
            altered = replace(
                method, inputs=(InputReference("current", "absent"), *method.inputs[1:])
            )
        elif damage == "cycle":
            altered = replace(
                method, inputs=(InputReference("current", method.identity), *method.inputs[1:])
            )
        elif damage == "source_kind":
            altered = replace(method, sources=(method.inputs[0].node,))
        elif damage == "endpoint_kind":
            source = next(n.identity for n in document.nodes if n.kind == "source")
            altered = replace(method, retained_endpoints=(source,))
        elif damage == "roles":
            altered = replace(
                method, inputs=tuple(replace(edge, role="quantity") for edge in method.inputs)
            )
        elif damage == "endpoint_count":
            altered = replace(method, retained_endpoints=(method.inputs[0].node,))
        else:
            child = next(n for n in document.nodes if n.identity == method.inputs[0].node)
            assert isinstance(child, MethodRecord)
            altered = replace(
                method, retained_endpoints=(method.inputs[0].node, method.inputs[1].node)
            )
            # Retain a different but valid row definition for the first endpoint.
            injected = (
                replace(
                    child,
                    identity="different",
                    parameters=replace(child.parameters, definition_id="different"),
                )
                if isinstance(child.parameters, RowState)
                else child
            )
            altered = replace(
                altered, retained_endpoints=(injected.identity, method.inputs[1].node)
            )
            document = replace(
                document, nodes=tuple(sorted((*document.nodes, injected), key=lambda n: n.identity))
            )
        document = replace(
            document,
            nodes=tuple(altered if n.identity == method.identity else n for n in document.nodes),
        )
    with pytest.raises(IntegrityError):
        thaw_graph(_wire(document))


@pytest.mark.parametrize(
    "damage",
    [
        "extra",
        "missing",
        "duplicate_key",
        "whitespace",
        "role",
        "version",
        "trailing",
        "base64",
        "old",
        "expanded",
    ],
)
def test_malformed_and_noncanonical_encodings(damage: str) -> None:
    body = encode(graph_document(_pair(_observation(), _observation())), GRAPH)
    if damage == "extra":
        body = body[:-1] + ',"extra":true}'
    elif damage == "missing":
        body = body.replace('"sources":[],', "", 1)
    elif damage == "duplicate_key":
        body = body.replace('"root":', '"root":"ignored","root":', 1)
    elif damage == "whitespace":
        body += " "
    elif damage == "role":
        body = body.replace('"current"', '"invalid-role"')
    elif damage == "version":
        body = body.replace("graph_dag/v1", "graph_dag/v999")
    elif damage == "expanded":
        body = " " * (4 * 1024 * 1024 + 1)
    text = _compress(body)
    if damage == "trailing":
        text = (
            PREFIX + base64.b64encode(zlib.compress(body.encode(), level=9) + b"trailing").decode()
        )
    elif damage == "base64":
        text += "!"
    elif damage == "old":
        text = "comparison-v2:" + text.removeprefix(PREFIX)
    with pytest.raises(IntegrityError):
        thaw_graph(text)


def test_node_reference_and_encoded_budgets_are_symmetric(monkeypatch: pytest.MonkeyPatch) -> None:
    from marivo.analysis.materialization import graph_snapshot as snapshot

    root = _pair(_observation(), _observation())
    text = freeze_graph(root)
    for name, maximum in (
        ("MAX_NODES", 4),
        ("MAX_REFERENCES", 3),
        ("MAX_ENCODED_BYTES", len(text.encode()) - 1),
        ("MAX_EXPANDED_BYTES", 10),
    ):
        with monkeypatch.context() as patch:
            patch.setattr(snapshot, name, maximum)
            with pytest.raises(IntegrityError):
                freeze_graph(root)
            with pytest.raises(IntegrityError):
                thaw_graph(text)


def test_depth_limit_accepts_boundary_and_rejects_one_more() -> None:
    root = _observation()
    for _ in range(126):
        root = method_node(
            (Edge("quantity", root),),
            PartsTransport("materialize", root.signature.domain, (), True),
            value_type=root.value_type,
        )
    assert len(graph_document(root).nodes) == 128
    assert thaw_graph(freeze_graph(root)).fingerprint == root.fingerprint
    root = method_node(
        (Edge("quantity", root),),
        PartsTransport("materialize", root.signature.domain, (), True),
        value_type=root.value_type,
    )
    with pytest.raises(IntegrityError, match="depth budget"):
        freeze_graph(root)


def test_old_continuation_has_explicit_reexecution_repair() -> None:
    from marivo.analysis.materialization.graph_protocol import SNAPSHOT, Continuation, decode

    current = Continuation(
        "marivo.analysis.continuation/v2",
        freeze_graph(_observation()),
        (),
        (),
        (),
        (),
        "input",
        "primary",
        (),
        "state",
    )
    old = encode(current, SNAPSHOT).replace("continuation/v2", "continuation/v1")
    with pytest.raises(IntegrityError, match="Re-execute the source analysis"):
        decode(old, SNAPSHOT)


def test_retained_only_cycle_rejects_without_recursive_object_construction() -> None:
    root = _pair(_observation(), _observation())
    document = graph_document(root)
    record = next(n for n in document.nodes if n.identity == root.identity)
    assert isinstance(record, MethodRecord)
    altered = replace(record, retained_endpoints=(record.identity, record.inputs[1].node))
    document = replace(
        document,
        nodes=tuple(altered if n.identity == record.identity else n for n in document.nodes),
    )
    with pytest.raises(IntegrityError, match="cyclic"):
        thaw_graph(_wire(document))


def test_duplicate_identity_cannot_hide_equal_but_differently_typed_literals() -> None:
    from marivo.analysis.core.predicates import ValuePredicate
    from marivo.analysis.materialization.graph_snapshot import same_node_definition

    original = _observation()
    predicate = ValuePredicate(original.signature.domain.binding, "eq", 1, "drop")
    first = method_node(
        (Edge("quantity", original),),
        PartsTransport("where", original.signature.domain, (), True, (predicate,)),
        value_type=original.value_type,
    )
    second = replace(first)
    assert isinstance(first.parameters, PartsTransport)
    # Equal Python dataclass values must not mask different canonical metadata.
    parameters = replace(first.parameters, predicates=(replace(predicate, value=1.0),))
    assert parameters == first.parameters
    object.__setattr__(second, "parameters", parameters)
    assert not same_node_definition(first, second)
    root = _pair(first, first)
    object.__setattr__(root, "inputs", (Edge("current", first), Edge("baseline", second)))
    with pytest.raises(IntegrityError, match="different frozen definitions"):
        freeze_graph(root)
