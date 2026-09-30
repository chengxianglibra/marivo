"""Private R4.2 scheduling and identity oracles; no product execution claim."""

from dataclasses import replace
from pathlib import Path

import pytest

import marivo.semantic as ms
from marivo.analysis.compiler.graph_plan import (
    ArtifactReadStage,
    CheckRequirement,
    LocalMethodStage,
    RouteChoice,
    SourceInputStage,
    SourceMethodStage,
    Stage,
    plan,
)
from marivo.analysis.core.graph import (
    Edge,
    FixedLeaf,
    MethodNode,
    Node,
    SourceDefinition,
    SourceLeaf,
    method_node,
    topology,
)
from marivo.analysis.core.model import (
    Binding,
    Coordinate,
    CoreRuleError,
    DomainSignature,
    ObservedQuantity,
    Signature,
)
from marivo.analysis.core.rules import MapCorrespond, MapMode, RowState
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.materialization.execution_key import (
    FixedKeyInput,
    FixedPartKey,
    SourceKeyBinding,
    graph_fixed_execution_key,
    graph_source_execution_key,
)
from marivo.analysis.materialization.graph_execution import PreparedGraph, prepare_graph
from marivo.analysis.methods.builtin import CHECKS, PARTS
from marivo.analysis.methods.errors import MethodRegistrationError
from marivo.analysis.methods.physical import (
    FixedShape,
    Implementation,
    NoTime,
    QualificationKey,
    Qualified,
    ResourceRequirements,
    Route,
    ScalarType,
    SourceShape,
)
from marivo.analysis.methods.registry import REGISTRY, MethodRegistry
from marivo.analysis.methods.semantics import MethodKey
from marivo.analysis.refs import ArtifactRef
from marivo.datasource.adapters import SourceSession


def _source(session_ref: str, *, quantity: bool = False) -> SourceLeaf:
    binding = Binding(session_ref, "sales", "customers", "current")
    entity = ms.ref.entity("sales.customer")
    key = (Coordinate(entity, "id", "identity"),)
    domain = DomainSignature(binding, "entity", key, key, "customers")
    observed = (
        ObservedQuantity(
            "revenue",
            ms.ref.metric("sales.revenue"),
            "revenue-v1",
            "CNY",
            "current",
            "orders",
            "strict",
            "sum@v1",
        )
        if quantity
        else None
    )
    return SourceLeaf(
        SourceDefinition(
            ms.ref.metric("sales.revenue") if quantity else entity,
            "revenue-v1" if quantity else "customer-v1",
            ms.ref.datasource("sales_db"),
            SourceShape("duckdb", "table", "native", NoTime()),
        ),
        Signature(domain, observed),
        ScalarType("int64"),
    )


def _fixed(session_ref: str, ref: str = "artifact_a", *, quantity: bool = True) -> FixedLeaf:
    source = _source(session_ref, quantity=quantity)
    return FixedLeaf(
        ArtifactRef(ref=ref),
        source.fingerprint,
        source.signature,
        source.value_type,
        FixedShape(NoTime()),
    )


def _pair(
    left: Node,
    right: Node,
    mode: MapMode = "exact_keys",
) -> MethodNode:
    return method_node(
        (Edge("subject", left), Edge("subject", right)),
        MapCorrespond(
            mode,
            replace(left.signature.domain, definition_id="paired"),
            "source.exact_pairing@v1" if mode == "exact_keys" else None,
        ),
        value_type=left.value_type,
    )


def _count(leaf: SourceLeaf | FixedLeaf) -> MethodNode:
    target = DomainSignature(leaf.signature.domain.binding, "singleton", (), (), "all")
    return method_node(
        (Edge("quantity", leaf),),
        RowState("count", target, "count", "count_all"),
        value_type=ScalarType("int64"),
    )


def _mean(leaf: SourceLeaf) -> MethodNode:
    target = DomainSignature(leaf.signature.domain.binding, "singleton", (), (), "all")
    return method_node(
        (Edge("quantity", leaf),),
        RowState("mean", target, "mean", "strict", numeric_check_id="source.finite_numeric@v1"),
        value_type=ScalarType("float64"),
    )


def _routes(root: Node, *, fixed: bool = False) -> tuple[RouteChoice, ...]:
    route: Route = "artifact_python" if fixed else "ibis"
    return tuple(
        RouteChoice(node.identity, route)
        for node in topology(root)
        if not isinstance(node, (SourceLeaf, FixedLeaf))
    )


class _Recorder:
    def __init__(self, *, fail_at: str | None = None, fail_check: bool = False) -> None:
        self.events: list[tuple[str, str]] = []
        self.fail_at = fail_at
        self.fail_check = fail_check

    def run_stage(self, stage: Stage, inputs: tuple[object, ...]) -> object:
        self.events.append(("stage", stage.output))
        if self.fail_at == stage.output:
            raise RuntimeError("selected stage failed")
        return (stage.output, inputs)

    def run_check(
        self, requirement: CheckRequirement, inputs: tuple[object, ...], output: object | None
    ) -> None:
        self.events.append((requirement.obligation.before, requirement.stage_output))
        if self.fail_check:
            raise RuntimeError("selected check failed")
        assert requirement.obligation.fact.inputs is not None
        if requirement.obligation.before == "consume":
            assert output is None
        else:
            assert output is not None


def test_mixed_and_foreign_session_reject_before_business_io_or_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = DatasetRuntime.create(tmp_path, "r42")

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("business I/O or Run allocation happened")

    monkeypatch.setattr(runtime.store, "artifact", forbidden)
    monkeypatch.setattr(runtime.store, "admit", forbidden)
    monkeypatch.setattr(SourceSession, "bind", forbidden)
    foreign = _count(_source("other_session", quantity=True))
    with pytest.raises(CoreRuleError, match="Session"):
        runtime._prepare_graph(foreign, _routes(foreign))
    live = _source(runtime.session_ref)
    saved = _fixed(runtime.session_ref, quantity=False)
    for root in (_pair(live, saved), _pair(saved, live)):
        with pytest.raises(CoreRuleError, match="mixed"):
            runtime._prepare_graph(root, _routes(root))
    assert runtime.last_run_ref is None


def test_unqualified_method_rejects_before_any_stage_consumer() -> None:
    source = _source("session_r42", quantity=True)
    source = replace(
        source,
        definition=replace(
            source.definition, shape=replace(source.definition.shape, backend="postgres")
        ),
    )
    root = _mean(source)
    with pytest.raises(MethodRegistrationError, match="qualified exact key"):
        prepare_graph(root, session_ref="session_r42", routes=_routes(root))


def test_explicit_sharing_and_equal_independent_nodes_have_distinct_work() -> None:
    leaves = tuple(_source("session_r42") for _ in range(4))
    first = _pair(leaves[0], leaves[1])
    shared = _pair(first, first, "union_keys")
    independent = _pair(first, _pair(leaves[2], leaves[3]), "union_keys")
    counts: list[tuple[int, int]] = []
    for root in (shared, independent):
        prepared = prepare_graph(root, session_ref="session_r42", routes=_routes(root))
        recorder = _Recorder()
        assert prepared.run(recorder) is not None
        stages = prepared.admitted.stages
        counts.append(
            (
                sum(isinstance(stage, SourceInputStage) for stage in stages),
                sum(isinstance(stage, SourceMethodStage) for stage in stages),
            )
        )
        assert len([event for event in recorder.events if event[0] == "stage"]) == len(stages)
        assert len([event for event in recorder.events if event[0] != "stage"]) == len(
            prepared.admitted.checks
        )
    assert counts == [(2, 2), (4, 3)]


def test_checks_keep_origin_and_failure_does_not_run_an_alternative() -> None:
    root = _pair(_source("session_r42"), _source("session_r42"))
    prepared = prepare_graph(root, session_ref="session_r42", routes=_routes(root))
    assert prepared.admitted.checks
    assert all(check.node_id == root.identity for check in prepared.admitted.checks)
    assert all(len(check.obligation.fact.inputs) == 2 for check in prepared.admitted.checks)
    target = next(
        stage.output for stage in prepared.admitted.stages if isinstance(stage, SourceMethodStage)
    )
    recorder = _Recorder(fail_at=target)
    with pytest.raises(RuntimeError, match="selected stage failed"):
        prepared.run(recorder)
    assert recorder.events.count(("stage", target)) == 1
    assert not any(event[0] == "publish" for event in recorder.events)
    rejected = _Recorder(fail_check=True)
    with pytest.raises(RuntimeError, match="selected check failed"):
        prepared.run(rejected)
    assert ("stage", target) not in rejected.events


def test_each_invocation_replays_source_stages_without_a_cross_run_cache() -> None:
    root = _count(_source("session_r42", quantity=True))
    prepared = prepare_graph(root, session_ref="session_r42", routes=_routes(root))
    first, second = _Recorder(), _Recorder()
    prepared.run(first)
    prepared.run(second)
    assert first.events == second.events
    assert any(event[0] == "stage" for event in second.events)


def test_source_key_is_fresh_per_run_and_binds_selected_plan() -> None:
    leaf = _source("session_r42", quantity=True)
    root = _count(leaf)
    prepared = prepare_graph(root, session_ref="session_r42", routes=_routes(root))
    binding = SourceKeyBinding(
        leaf, leaf.definition.shape, "semantic-digest", "selected-source-binding"
    )
    first = graph_source_execution_key(prepared.admitted, (binding,), "run_a")
    second = graph_source_execution_key(prepared.admitted, (binding,), "run_b")
    assert first != second
    assert first == "42bd15d675279ca064d12a78bffcfd671f5b7975e04b1d7f73bfe7eede9d8abf"
    assert first == graph_source_execution_key(prepared.admitted, (binding,), "run_a")
    equivalent_leaf = _source("session_r42", quantity=True)
    equivalent_root = _count(equivalent_leaf)
    equivalent = prepare_graph(
        equivalent_root, session_ref="session_r42", routes=_routes(equivalent_root)
    )
    assert equivalent_leaf.identity != leaf.identity
    assert (
        graph_source_execution_key(
            equivalent.admitted,
            (
                SourceKeyBinding(
                    equivalent_leaf,
                    equivalent_leaf.definition.shape,
                    "semantic-digest",
                    "selected-source-binding",
                ),
            ),
            "run_a",
        )
        == first
    )
    selected = prepared.admitted.physical_requirements[0]
    changed_plan = replace(
        prepared.admitted,
        physical_requirements=(
            replace(selected, implementation=replace(selected.implementation, contract_version=2)),
        ),
    )
    assert graph_source_execution_key(changed_plan, (binding,), "run_a") != first
    assert first != graph_source_execution_key(
        prepared.admitted, (replace(binding, selected_binding_fingerprint="changed"),), "run_a"
    )
    with pytest.raises(IntegrityError):
        graph_source_execution_key(prepared.admitted, (), "run_a")
    with pytest.raises(IntegrityError):
        graph_source_execution_key(
            prepared.admitted,
            (
                replace(
                    binding, physical_shape=SourceShape("duckdb", "parquet", "parquet", NoTime())
                ),
            ),
            "run_a",
        )


def test_fixed_key_binds_exact_reference_receipts_parts_state_and_snapshot() -> None:
    leaf = _fixed("session_r42")
    root = _count(leaf)
    prepared = prepare_graph(root, session_ref="session_r42", routes=_routes(root, fixed=True))
    original = FixedKeyInput(
        leaf,
        "session_r42",
        "run_input",
        "primary-receipt",
        (FixedPartKey("row_state", "row.count", 1, "part-receipt"),),
        "input-binding",
        "row.count",
        1,
        "snapshot-digest",
    )
    key = graph_fixed_execution_key(prepared.admitted, (original,))
    assert key == "6b5e1f81650e201c6fd12fdeb9f8e52808c1af1f8b25ab4c36e3bf62cc03a50d"
    variants = (
        replace(original, producing_run_ref="run_other"),
        replace(original, primary_receipt_digest="different-primary"),
        replace(original, ordered_parts=()),
        replace(
            original,
            ordered_parts=(replace(original.ordered_parts[0], receipt_digest="different-part"),),
        ),
        replace(original, ordered_parts=(replace(original.ordered_parts[0], contract_version=2),)),
        replace(original, input_binding="different-binding"),
        replace(original, method_state_contract_id="other-state"),
        replace(original, method_state_version=2),
        replace(original, snapshot_digest="different-snapshot"),
    )
    assert all(graph_fixed_execution_key(prepared.admitted, (item,)) != key for item in variants)
    with pytest.raises(IntegrityError):
        graph_fixed_execution_key(prepared.admitted, (replace(original, session_ref="foreign"),))
    assert isinstance(prepared, PreparedGraph)
    assert isinstance(prepared.admitted.stages[0], ArtifactReadStage)
    assert isinstance(prepared.admitted.stages[-1], LocalMethodStage)
    recorder = _Recorder()
    prepared.run(recorder)
    assert [event[0] for event in recorder.events] == ["stage", "stage"]
    other_leaf = _fixed("session_r42", "artifact_other")
    other_root = _count(other_leaf)
    other_plan = prepare_graph(
        other_root, session_ref="session_r42", routes=_routes(other_root, fixed=True)
    )
    assert (
        graph_fixed_execution_key(other_plan.admitted, (replace(original, leaf=other_leaf),)) != key
    )


def test_fixed_key_preserves_ordered_endpoint_occurrences() -> None:
    left = _fixed("session_r42", "artifact_left", quantity=False)
    right = _fixed("session_r42", "artifact_right", quantity=False)
    method = MethodKey("map_correspond")
    implementation = Implementation(
        QualificationKey(
            method,
            (ScalarType("int64"), ScalarType("int64")),
            ("entity", "entity"),
            FixedShape(NoTime()),
            "artifact_python",
        ),
        CHECKS,
        PARTS,
        "exact",
        ResourceRequirements("complete", "caller", None),
        Qualified("test.fixed.pair", "test.consumer", "synthetic-only"),
    )
    registry = MethodRegistry(
        (replace(REGISTRY.lookup(method), implementations=(implementation,)),)
    )

    def selected(ordered: tuple[FixedLeaf, FixedLeaf]) -> str:
        root = _pair(*ordered)
        admitted = plan(root, routes=_routes(root, fixed=True), registry=registry)
        inputs = tuple(
            FixedKeyInput(
                leaf,
                "session_r42",
                "run_input",
                "primary-receipt",
                (),
                "binding",
                "subject",
                1,
                "snapshot-digest",
            )
            for leaf in admitted.classification.artifacts
        )
        return graph_fixed_execution_key(admitted, inputs)

    assert selected((left, right)) != selected((right, left))
    shared_root = _pair(left, left)
    shared_plan = plan(shared_root, routes=_routes(shared_root, fixed=True), registry=registry)
    shared_input = FixedKeyInput(
        left,
        "session_r42",
        "run_input",
        "primary-receipt",
        (),
        "binding",
        "subject",
        1,
        "snapshot-digest",
    )
    assert sum(isinstance(stage, ArtifactReadStage) for stage in shared_plan.stages) == 1
    assert isinstance(shared_plan.stages[-1], LocalMethodStage)
    assert len(shared_plan.stages[-1].inputs) == 2
    with pytest.raises(IntegrityError):
        graph_fixed_execution_key(shared_plan, (shared_input,))
    current = replace(shared_input, input_binding="current")
    baseline = replace(shared_input, input_binding="baseline")
    assert graph_fixed_execution_key(shared_plan, (current, baseline)) != (
        graph_fixed_execution_key(shared_plan, (baseline, current))
    )
    nested_root = _pair(shared_root, shared_root, "union_keys")
    nested_plan = plan(nested_root, routes=_routes(nested_root, fixed=True), registry=registry)
    assert graph_fixed_execution_key(nested_plan, (current, baseline))
    with pytest.raises(IntegrityError):
        graph_fixed_execution_key(nested_plan, (current, baseline, current, baseline))
