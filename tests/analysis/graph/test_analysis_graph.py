"""Pure graph oracles; synthetic implementations confer no production qualification."""

from dataclasses import FrozenInstanceError, replace
from unittest.mock import patch

import pytest

import marivo.analysis.core.graph as graph
import marivo.semantic as ms
from marivo.analysis.compiler.graph_plan import (
    RouteChoice,
    plan,
)
from marivo.analysis.core.graph import (
    Edge,
    SourceDefinition,
    SourceLeaf,
    capture_graph,
    method_node,
    topology,
)
from marivo.analysis.core.model import (
    Binding,
    Coordinate,
    CoreRuleError,
    DomainSignature,
    Evidence,
    ObservedQuantity,
    Signature,
    available_facts,
)
from marivo.analysis.core.rules import (
    CellDerive,
    OriginalReduce,
    RowState,
)
from marivo.analysis.methods.errors import MethodRegistrationError
from marivo.analysis.methods.physical import (
    Implementation,
    NoTime,
    QualificationKey,
    Qualified,
    ResourceRequirements,
    ScalarType,
    SourceShape,
    Unavailable,
)
from marivo.analysis.methods.registry import REGISTRY, MethodRegistry
from marivo.analysis.methods.semantics import MethodKey


def _source(*, datasource: str = "sales_db", owner: str = "sales") -> SourceLeaf:
    binding = Binding("r33", owner, "orders", "september")
    key = (Coordinate(ms.ref.entity("sales.customer"), "id", "identity"),)
    signature = Signature(
        DomainSignature(binding, "entity", key, key, "customers"),
        ObservedQuantity(
            "revenue",
            ms.ref.metric("sales.revenue"),
            "canonical-revenue-v1",
            "CNY",
            "september",
            "orders",
            "strict",
            "sum@v1",
        ),
    )
    return SourceLeaf(
        SourceDefinition(
            ms.ref.metric("sales.revenue"),
            "canonical-revenue-v1",
            ms.ref.datasource(datasource),
            SourceShape("duckdb", "table", "native", NoTime()),
        ),
        signature,
        ScalarType("int64"),
    )


def _mean(leaf: SourceLeaf):
    target = DomainSignature(leaf.signature.domain.binding, "singleton", (), (), "all")
    return method_node(
        (Edge("quantity", leaf),),
        RowState("mean", target, "mean", "strict", numeric_check_id="source.finite_numeric@v1"),
        value_type=ScalarType("float64"),
    )


def _difference(left, right):
    return method_node(
        (Edge("current", left), Edge("baseline", right)),
        CellDerive(
            "difference",
            "difference",
            "propagate",
            "CNY",
            "september",
            "source.exact_pairing@v1",
            "source.finite_numeric@v1",
        ),
        value_type=left.value_type,
    )


def _registry(shape, route="ibis", *, status=None):
    implementation = Implementation(
        QualificationKey(MethodKey("row.mean"), (ScalarType("int64"),), ("entity",), shape, route),
        ("source.finite_numeric@v1",),
        ("row_state",),
        "finite_float64",
        ResourceRequirements("stream", "producer", 1000),
        status or Qualified("test.mean", "test.consumer", "synthetic-only"),
    )
    return MethodRegistry(
        (replace(REGISTRY.lookup(MethodKey("row.mean")), implementations=(implementation,)),)
    )


def test_definition_identity_is_not_node_or_artifact_identity():
    left, right = (_source(), _source())
    assert left.identity != right.identity
    assert left.fingerprint == right.fingerprint
    assert len(topology(_difference(left, right))) == 3
    assert len(topology(_difference(left, left))) == 2
    with pytest.raises(FrozenInstanceError):
        left.identity = "changed"


def test_duplicate_identity_and_cycles_are_rejected():
    left = _source()
    with pytest.raises(CoreRuleError, match="one object per explicit node identity"):
        _difference(left, replace(left))
    root = _mean(left)
    object.__setattr__(root, "inputs", (Edge("quantity", root),))
    with pytest.raises(CoreRuleError, match="acyclic"):
        topology(root)


def test_roles_owners_and_version_are_checked():
    root = _mean(_source())
    with pytest.raises(CoreRuleError, match="ordered input roles"):
        replace(root, inputs=(Edge("subject", root.inputs[0].node),))
    with pytest.raises(CoreRuleError):
        _difference(_source(), _source(owner="another"))
    with pytest.raises(MethodRegistrationError):
        MethodKey("row.mean", 2)
    with pytest.raises(CoreRuleError, match="exact method version"):
        replace(root, method=MethodKey("row.sum"))


def test_source_ref_and_fingerprint_are_bound():
    leaf = _source()
    with pytest.raises(CoreRuleError, match="canonical graph fingerprint"):
        replace(leaf, definition=replace(leaf.definition, fingerprint="wrong"))
    with pytest.raises(CoreRuleError, match="canonical graph fingerprint"):
        replace(leaf, definition=replace(leaf.definition, ref=ms.ref.metric("sales.other")))


def test_live_source_rejects_prior_execution_evidence():
    leaf = _source()
    fact = _mean(leaf).derivation.obligations[0].fact
    prior = Evidence(fact, "observation", "previous-run")
    with pytest.raises(CoreRuleError, match="only static declaration or builder evidence"):
        replace(leaf, signature=replace(leaf.signature, evidence=(prior,)))


def test_result_type_cannot_forge_successor_qualification():
    root = _mean(_source())
    with pytest.raises(MethodRegistrationError, match="result type"):
        replace(root, value_type=ScalarType("int64"))


def test_production_is_blocked_and_does_not_borrow_test_qualifications():
    leaf = _source()
    leaf = replace(
        leaf,
        definition=replace(
            leaf.definition, shape=replace(leaf.definition.shape, backend="postgres")
        ),
    )
    root = _mean(replace(leaf, value_type=ScalarType("float64")))
    with pytest.raises(MethodRegistrationError, match="blocked"):
        plan(root, routes=(RouteChoice(root.identity, "ibis"),))


@pytest.mark.parametrize("status", ["unsupported", "unverified", "blocked"])
def test_unavailable_status_is_preserved(status):
    leaf = _source()
    root = _mean(leaf)
    registry = _registry(
        leaf.definition.shape, status=Unavailable(status, "gap", "Qualify the exact key.")
    )
    with pytest.raises(MethodRegistrationError, match=status):
        plan(root, routes=(RouteChoice(root.identity, "ibis"),), registry=registry)


def test_exact_type_shape_and_route_matching_no_retry():
    leaf = _source()
    root = _mean(leaf)
    registry = _registry(leaf.definition.shape)
    with pytest.raises(MethodRegistrationError, match="blocked"):
        plan(root, routes=(RouteChoice(root.identity, "ibis_python"),), registry=registry)
    for changed in (
        replace(leaf, value_type=ScalarType("float64")),
        replace(
            leaf,
            definition=replace(
                leaf.definition, shape=replace(leaf.definition.shape, table_kind="other")
            ),
        ),
    ):
        node = _mean(changed)
        with pytest.raises(MethodRegistrationError, match="blocked"):
            plan(node, routes=(RouteChoice(node.identity, "ibis"),), registry=registry)


def test_missing_original_parts_reject_during_construction():
    leaf = _source()
    target = DomainSignature(leaf.signature.domain.binding, "singleton", (), (), "all")
    with pytest.raises(CoreRuleError):
        method_node((Edge("quantity", leaf),), OriginalReduce(target), value_type=leaf.value_type)


def test_child_obligations_survive_and_post_is_not_evidence():
    child = _mean(_source())
    parent = _difference(child, child)
    assert set(child.derivation.obligations) <= set(parent.derivation.obligations)
    assert child.derivation.post
    assert not set(child.derivation.pre) <= available_facts(child.signature)
    assert not set(parent.derivation.pre) <= available_facts(parent.signature)


def test_route_choices_must_cover_exact_reachable_methods():
    root = _mean(_source())
    for routes in (
        (),
        (RouteChoice("unrelated", "ibis"),),
        (RouteChoice(root.identity, "ibis"), RouteChoice(root.identity, "ibis")),
    ):
        with pytest.raises(CoreRuleError, match="one route"):
            plan(root, routes=routes)


def test_local_predecessor_cannot_be_implicitly_sent_to_source():
    leaf = _source()
    child = _mean(leaf)
    root = _difference(child, child)
    registry = MethodRegistry(
        (
            *_registry(leaf.definition.shape, "ibis_python").registrations,
            REGISTRY.lookup(MethodKey("cell.difference")),
        )
    )
    with pytest.raises(CoreRuleError, match="local predecessor"):
        plan(
            root,
            routes=(RouteChoice(child.identity, "ibis_python"), RouteChoice(root.identity, "ibis")),
            registry=registry,
        )


def test_same_node_emits_one_stage_but_independent_nodes_do_not_merge():
    left = _source()
    for right, expected_count in ((left, 2), (_source(), 3)):
        root = _difference(left, right)
        implementation = Implementation(
            QualificationKey(
                MethodKey("cell.difference"),
                (ScalarType("int64"),) * 2,
                ("entity",) * 2,
                left.definition.shape,
                "ibis",
            ),
            ("source.exact_pairing@v1", "source.finite_numeric@v1", "source.cell_policy@v1"),
            ("current_endpoint", "baseline_endpoint", "correspondence"),
            "checked_int64",
            ResourceRequirements("stream", "producer", None),
            Qualified("test.difference", "test.consumer", "synthetic-only"),
        )
        registry = MethodRegistry(
            (
                replace(
                    REGISTRY.lookup(MethodKey("cell.difference")), implementations=(implementation,)
                ),
            )
        )
        result = plan(root, routes=(RouteChoice(root.identity, "ibis"),), registry=registry)
        assert len(result.stages) == expected_count
        assert (result.stages[-1].inputs[0] == result.stages[-1].inputs[1]) == (left is right)


def test_complete_entry_captures_shared_nodes_without_rederivation() -> None:
    child = _mean(_source())
    root = _difference(child, child)
    with (
        patch.object(graph, "_validate_method", wraps=graph._validate_method) as checks,
        patch.object(
            MethodRegistry, "derive", autospec=True, side_effect=MethodRegistry.derive
        ) as derivations,
    ):
        captured = capture_graph(root)
    assert len(captured.nodes) == 3
    assert checks.call_count == derivations.call_count == 0
    assert captured.fingerprints[root.identity] == root.fingerprint


def test_deep_identity_collision_is_rejected_at_complete_entry() -> None:
    leaf = _source()
    root = _difference(_mean(leaf), _mean(replace(leaf)))
    with pytest.raises(CoreRuleError, match="one object per explicit node identity"):
        capture_graph(root)
