"""Pure graph oracles; synthetic implementations confer no production qualification."""

from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
from unittest.mock import patch

import pytest

import marivo.analysis as mv
import marivo.analysis.core.graph as graph
import marivo.semantic as ms
from marivo.analysis.compiler.graph_plan import (
    ArtifactReadStage,
    LocalMethodStage,
    RouteChoice,
    SourceMethodStage,
    classify_inputs,
    plan,
)
from marivo.analysis.core.graph import (
    Edge,
    FixedLeaf,
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
    BindProject,
    CellDerive,
    DisplayTable,
    OriginalReduce,
    PartsTransport,
    RowState,
)
from marivo.analysis.core.time_grid import bind_grid
from marivo.analysis.methods.errors import MethodRegistrationError
from marivo.analysis.methods.physical import (
    FixedShape,
    Implementation,
    NoTime,
    QualificationKey,
    Qualified,
    ResourceRequirements,
    ScalarType,
    SourceShape,
    TimeShape,
    Unavailable,
)
from marivo.analysis.methods.registry import REGISTRY, MethodRegistry
from marivo.analysis.methods.semantics import MethodKey
from marivo.analysis.refs import ArtifactRef
from marivo.refs import RefPayloadV1, SemanticKind
from marivo.semantic.ir import TargetDimensionContract


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


def _fixed() -> FixedLeaf:
    source = _source()
    return FixedLeaf(
        ArtifactRef("history/revenue"),
        source.fingerprint,
        source.signature,
        source.value_type,
        FixedShape(NoTime()),
    )


def _mean(leaf: SourceLeaf | FixedLeaf):
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
            "strict",
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
        ResourceRequirements(
            "stream", "caller" if route == "artifact_python" else "producer", 1000
        ),
        status or Qualified("test.mean", "test.consumer", "synthetic-only"),
    )
    return MethodRegistry(
        (replace(REGISTRY.lookup(MethodKey("row.mean")), implementations=(implementation,)),)
    )


def test_definition_identity_is_not_node_or_artifact_identity():
    left, right = _source(), _source()
    assert left.identity != right.identity
    assert left.fingerprint == right.fingerprint
    assert len(topology(_difference(left, right))) == 3
    assert len(topology(_difference(left, left))) == 2
    assert _fixed().identity != _fixed().artifact.ref
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


@pytest.mark.parametrize("basis", ["check", "observation", "deduction"])
def test_live_source_rejects_prior_execution_evidence(basis):
    leaf = _source()
    fact = _mean(leaf).derivation.obligations[0].fact
    dependencies = (fact,) if basis == "deduction" else ()
    prior = Evidence(fact, basis, "previous-run", dependencies)
    with pytest.raises(CoreRuleError, match="only static declaration or builder evidence"):
        replace(leaf, signature=replace(leaf.signature, evidence=(prior,)))


def test_result_type_cannot_forge_successor_qualification():
    root = _mean(_source())
    with pytest.raises(MethodRegistrationError, match="result type"):
        replace(root, value_type=ScalarType("int64"))


def test_classification_has_only_reachable_data_leaves():
    _source(datasource="unrelated_db")
    assert classify_inputs(_mean(_source())).kind == "source"
    fixed = _fixed()
    classified = classify_inputs(_mean(fixed))
    assert classified.kind == "artifact"
    assert classified.sources == ()
    assert classified.artifacts == (fixed,)
    assert classify_inputs(_difference(_mean(_source()), _mean(fixed))).kind == "mixed"


def test_bare_fixed_leaf_cannot_authorize_artifact_read():
    with pytest.raises(CoreRuleError, match="bare fixed Artifact leaf"):
        plan(_fixed(), routes=())


def test_mixed_and_multisource_reject_before_route_selection():
    for root, reason in (
        (_difference(_source(), _fixed()), "mixed live"),
        (_difference(_source(), _source(datasource="other_db")), "multiple live"),
    ):
        with pytest.raises(CoreRuleError, match=reason):
            plan(root, routes=())


@pytest.mark.parametrize("route", ["ibis", "ibis_python", "artifact_python"])
def test_explicit_routes_produce_exact_stages_and_pending_checks(route):
    leaf = _fixed() if route == "artifact_python" else _source()
    shape = leaf.shape if isinstance(leaf, FixedLeaf) else leaf.definition.shape
    root = _mean(leaf)
    result = plan(
        root, routes=(RouteChoice(root.identity, route),), registry=_registry(shape, route)
    )
    assert result.primary_output == result.stages[-1].output
    assert len(result.stages) == (3 if route == "ibis_python" else 2)
    assert isinstance(result.stages[-1], SourceMethodStage if route == "ibis" else LocalMethodStage)
    if route == "artifact_python":
        assert isinstance(result.stages[0], ArtifactReadStage)
    if route == "ibis_python":
        assert result.stages[1].operation == "prepare"
        assert result.stages[2].inputs == (result.stages[1].output,)
    assert tuple(check.obligation for check in result.checks) == root.derivation.obligations
    assert result.checks[0].obligation.fact.binding == leaf.signature.domain.binding
    assert result.checks[0].obligation.before == "consume"
    assert result.checks[0].stage_output == result.stages[1].output
    assert result.physical_requirements[0].implementation.resources.max_rows == 1000
    assert not set(root.derivation.pre) <= available_facts(root.signature)
    assert (
        plan(root, routes=(RouteChoice(root.identity, route),), registry=_registry(shape, route))
        == result
    )


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
    object.__setattr__(parent, "derivation", replace(parent.derivation, obligations=()))
    with pytest.raises(CoreRuleError, match="registered semantic derivation"):
        topology(parent)


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


def test_zero_io_on_construction_planning_and_refusal(monkeypatch):
    import sqlite3

    import duckdb

    from marivo.analysis.materialization import graph_store, storage
    from marivo.analysis.materialization.store import SessionStore
    from marivo.datasource.adapters import SourceSession

    def forbidden(*args, **kwargs):
        raise AssertionError("Pure graph work attempted I/O")

    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(duckdb, "connect", forbidden)
    monkeypatch.setattr(SourceSession, "__init__", forbidden)
    monkeypatch.setattr(SourceSession, "bind", forbidden)
    monkeypatch.setattr(SourceSession, "batches", forbidden)
    monkeypatch.setattr(SourceSession, "collect_bounded", forbidden)
    monkeypatch.setattr(SessionStore, "__init__", forbidden)
    monkeypatch.setattr(graph_store, "admit", forbidden)
    monkeypatch.setattr(graph_store, "artifact", forbidden)
    monkeypatch.setattr(storage, "_open_payload", forbidden)
    for leaf, route in (
        (_source(), "ibis"),
        (_source(), "ibis_python"),
        (_fixed(), "artifact_python"),
    ):
        root = _mean(leaf)
        shape = leaf.shape if isinstance(leaf, FixedLeaf) else leaf.definition.shape
        plan(root, routes=(RouteChoice(root.identity, route),), registry=_registry(shape, route))
    with pytest.raises(CoreRuleError):
        plan(_difference(_source(), _fixed()), routes=())


def test_binding_method_declares_sources_even_with_fixed_subjects():
    base = _source()
    signature = replace(base.signature, quantity=None)
    entity = ms.ref.entity("sales.customer")
    source = SourceLeaf(
        SourceDefinition(entity, "customer-v1", base.definition.datasource, base.definition.shape),
        signature,
        ScalarType("int64"),
    )
    fixed = FixedLeaf(
        ArtifactRef("customer-history"),
        "customer-v1",
        signature,
        ScalarType("int64"),
        FixedShape(NoTime()),
    )
    field = TargetDimensionContract(
        RefPayloadV1(
            schema="marivo.semantic_ref/v1",
            kind=SemanticKind.DIMENSION,
            path="sales.customer.region",
        ),
        RefPayloadV1(
            schema="marivo.semantic_ref/v1", kind=SemanticKind.ENTITY, path="sales.customer"
        ),
        "region",
        "string",
        False,
        False,
        None,
        False,
        None,
    )
    params = BindProject(ms.ref.dimension("sales.customer.region"), entity, field, None, (), ())
    with pytest.raises(MethodRegistrationError, match="declared string result type"):
        method_node(
            (Edge("subject", source),),
            params,
            value_type=ScalarType("int64"),
            sources=(source,),
        )
    with pytest.raises(CoreRuleError, match="explicit source bindings"):
        method_node((Edge("subject", fixed),), params, value_type=ScalarType("string"))
    node = method_node(
        (Edge("subject", fixed),), params, value_type=ScalarType("string"), sources=(source,)
    )
    assert classify_inputs(node).kind == "mixed"
    with pytest.raises(CoreRuleError, match="mixed live"):
        plan(node, routes=())
    wrong_scope = replace(
        source,
        signature=replace(
            signature,
            domain=replace(
                signature.domain, binding=replace(signature.domain.binding, scope_id="other-month")
            ),
        ),
    )
    with pytest.raises(CoreRuleError, match="exact Session, owner and scope"):
        method_node(
            (Edge("subject", fixed),),
            params,
            value_type=ScalarType("string"),
            sources=(wrong_scope,),
        )
    with pytest.raises(CoreRuleError, match="explicit source bindings"):
        method_node(
            (Edge("subject", fixed),),
            params,
            value_type=ScalarType("string"),
            sources=(source, source),
        )


def test_noncohort_fixed_shapes_remain_exact() -> None:
    left = _fixed()
    right = replace(
        left,
        identity="other-fixed-input",
        artifact=ArtifactRef("history/other"),
        shape=FixedShape(TimeShape("instant", "us", "UTC")),
    )
    root = _difference(left, right)
    with pytest.raises(CoreRuleError, match="different time or source shapes"):
        plan(root, routes=(RouteChoice(root.identity, "artifact_python"),))


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("temporal_category", [False, True])
def test_fixed_grid_display_admits_only_time_neutral_categories(
    reverse: bool, temporal_category: bool
) -> None:
    grid = bind_grid(
        mv.time_scope(start="2026-07-01", end="2026-10-01"),
        mv.grain("month"),
        report_timezone="UTC",
    )
    original = _fixed()
    domain = original.signature.domain
    coord = Coordinate(domain.instance_key[0].entity_ref, "time:" + grid.identity, "anchor")
    domain = replace(
        domain,
        instance_key=(*domain.instance_key, coord),
        target_key=(*domain.target_key, coord),
        time_grid=grid,
    )
    value = replace(
        original,
        signature=replace(original.signature, domain=domain),
        shape=FixedShape(TimeShape("instant", "us", "UTC")),
    )
    category = replace(
        value,
        identity="category",
        artifact=ArtifactRef("history/category"),
        signature=replace(value.signature, quantity=None),
        value_type=ScalarType("string"),
        shape=FixedShape(TimeShape("instant", "ms", "UTC") if temporal_category else NoTime()),
    )
    inputs = (category, value) if reverse else (value, category)
    root = method_node(
        tuple(Edge("subject" if leaf is category else "quantity", leaf) for leaf in inputs),
        DisplayTable(
            ("category", "amount") if reverse else ("amount", "category"),
            ("string", "int64") if reverse else ("int64", "string"),
            tuple(leaf.identity for leaf in inputs),
        ),
        value_type=inputs[0].value_type,
    )
    routes = (RouteChoice(root.identity, "artifact_python"),)
    if temporal_category:
        with pytest.raises(CoreRuleError, match="different time or source shapes"):
            plan(root, routes=routes)
    else:
        admitted = plan(root, routes=routes)
        assert admitted.physical_requirements[-1].key.shape == value.shape
        assert tuple(
            stage.leaf.shape for stage in admitted.stages if isinstance(stage, ArtifactReadStage)
        ) == tuple(leaf.shape for leaf in inputs)


def test_time_shape_is_exact_and_fixed_obligations_are_not_recovered():
    source = _source()
    changed = replace(
        source,
        definition=replace(
            source.definition,
            shape=replace(source.definition.shape, time=TimeShape("instant", "us", "UTC")),
        ),
    )
    root = _mean(changed)
    with pytest.raises(MethodRegistrationError, match="blocked"):
        plan(
            root,
            routes=(RouteChoice(root.identity, "ibis"),),
            registry=_registry(source.definition.shape),
        )
    pending = _mean(source).signature.obligations
    fixed = _fixed()
    fixed = replace(fixed, signature=replace(fixed.signature, obligations=pending))
    with pytest.raises(CoreRuleError, match="pending Artifact obligations"):
        plan(fixed, routes=())


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


@pytest.mark.parametrize("depth", [10, 20, 40])
def test_chain_construction_derives_and_checks_only_new_nodes(depth: int) -> None:
    root = _source()
    with (
        patch.object(graph, "_validate_method", wraps=graph._validate_method) as checks,
        patch.object(
            MethodRegistry, "derive", autospec=True, side_effect=MethodRegistry.derive
        ) as derivations,
    ):
        for _ in range(depth):
            root = method_node(
                (Edge("quantity", root),),
                PartsTransport("view", root.signature.domain, (), True),
                value_type=root.value_type,
            )
    assert checks.call_count == derivations.call_count == depth
    routes = tuple(
        RouteChoice(node.identity, "ibis")
        for node in graph.retained_nodes(root)
        if isinstance(node, graph.MethodNode)
    )
    with (
        patch.object(graph, "_validate_method", wraps=graph._validate_method) as checks,
        patch.object(
            MethodRegistry, "derive", autospec=True, side_effect=MethodRegistry.derive
        ) as derivations,
    ):
        admitted = plan(root, routes=routes)
    assert checks.call_count == derivations.call_count == depth
    assert len(admitted.stages) == depth + 1


def test_complete_entry_validates_shared_nodes_once() -> None:
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
    assert checks.call_count == derivations.call_count == 2
    assert captured.fingerprints[root.identity] == root.fingerprint


def test_deep_identity_collision_is_rejected_at_complete_entry() -> None:
    leaf = _source()
    root = _difference(_mean(leaf), _mean(replace(leaf)))
    with pytest.raises(CoreRuleError, match="one object per explicit node identity"):
        capture_graph(root)


def test_forged_ancestor_is_rejected_at_complete_entry() -> None:
    child = _mean(_source())
    object.__setattr__(child, "derivation", replace(child.derivation, obligations=()))
    root = method_node(
        (Edge("quantity", child),),
        PartsTransport("view", child.signature.domain, (), True),
        value_type=child.value_type,
    )
    with pytest.raises(CoreRuleError, match="registered semantic derivation"):
        capture_graph(root)


def test_retained_source_definitions_do_not_classify_as_execution_inputs() -> None:
    original = _mean(_source())
    fixed = FixedLeaf(
        ArtifactRef("retained"),
        original.fingerprint,
        original.signature,
        original.value_type,
        FixedShape(NoTime()),
    )
    root = method_node(
        (Edge("current", fixed), Edge("baseline", fixed)),
        _difference(original, original).parameters,
        value_type=original.value_type,
        retained_endpoints=(original, original),
    )
    captured = capture_graph(root)
    assert captured.nodes == (fixed, root)
    assert original in captured.retained
    assert classify_inputs(root).kind == "artifact"
    object.__setattr__(original, "inputs", (Edge("quantity", root),))
    with pytest.raises(CoreRuleError, match="acyclic"):
        capture_graph(root)


@pytest.mark.parametrize(
    ("kind", "fingerprint", "plan_hash", "snapshot_hash"),
    [
        (
            "source",
            "0a1e646715c236c47868c100789678b876bce40c8b6018eb31462ba56266a8c3",
            "06b2581d02ba5a1e5cb1c681dacbb20d97b153bf8984c5961747d157ef509569",
            "a99884eaa30f16ba30c2ffc55215601627ef25c4bea4243f056e0c8372339582",
        ),
        (
            "fixed",
            "a066402e9a43f7286a25b22db241d28aba0fb7037013ba63bf5afb750d341ed7",
            "8d89e0408cfa5ba941c792cee611f4b23bfdacb895091f5af759b68b53fab353",
            "62b182d640bce5df77c3d627e95d0cc8f19290c0c1fb80fa4e6e73ced4a3a7db",
        ),
    ],
)
def test_persisted_identity_pins_v2_premise_contract(
    kind: str, fingerprint: str, plan_hash: str, snapshot_hash: str
) -> None:
    from marivo.analysis.materialization.graph_protocol import freeze_graph, plan_digest

    # Pin the v2 premise contract and v4 snapshot with deterministic capture identities.
    root = _source() if kind == "source" else _fixed()
    root = replace(root, identity="leaf")
    routes = []
    for index in range(2):
        root = method_node(
            (Edge("quantity", root),),
            PartsTransport("view", root.signature.domain, (), True),
            value_type=root.value_type,
        )
        root = replace(root, identity=f"view-{index}")
        routes.append(RouteChoice(root.identity, "ibis" if kind == "source" else "artifact_python"))
    admitted = plan(root, routes=tuple(routes))
    assert root.fingerprint == fingerprint
    assert plan_digest(admitted) == plan_hash
    assert sha256(freeze_graph(root).encode()).hexdigest() == snapshot_hash
