"""Private scheduling and identity oracles; no product execution claim."""

from dataclasses import replace
from pathlib import Path

import pytest

import marivo.semantic as ms
from marivo.analysis.compiler.graph_plan import (
    CheckRequirement,
    RouteChoice,
    SourceMethodStage,
    Stage,
)
from marivo.analysis.core.graph import (
    Edge,
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
    SourceKeyBinding,
    graph_source_execution_key,
)
from marivo.analysis.materialization.graph_execution import prepare_graph
from marivo.analysis.methods.errors import MethodRegistrationError
from marivo.analysis.methods.physical import (
    NoTime,
    ScalarType,
    SourceShape,
)
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


def _pair(left: Node, right: Node, mode: MapMode = "exact_keys") -> MethodNode:
    return method_node(
        (Edge("subject", left), Edge("subject", right)),
        MapCorrespond(
            mode,
            replace(left.signature.domain, definition_id="paired"),
            "source.exact_pairing@v1" if mode == "exact_keys" else None,
        ),
        value_type=left.value_type,
    )


def _count(leaf: SourceLeaf) -> MethodNode:
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


def _routes(root: Node) -> tuple[RouteChoice, ...]:
    return tuple(
        RouteChoice(node.identity, "ibis")
        for node in topology(root)
        if isinstance(node, MethodNode)
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


def test_foreign_session_reject_before_business_io_or_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = DatasetRuntime.create(tmp_path, "r42")

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("business I/O or Run allocation happened")

    from marivo.analysis.materialization import graph_store

    monkeypatch.setattr(graph_store, "artifact", forbidden)
    monkeypatch.setattr(graph_store, "admit", forbidden)
    monkeypatch.setattr(SourceSession, "bind", forbidden)
    foreign = _count(_source("other_session", quantity=True))
    with pytest.raises(CoreRuleError, match="Session"):
        runtime._prepare_graph(foreign, _routes(foreign))
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
    assert first == "25e0b5a83f28a0c739e76948ca3ed08890bf563c9902e404fb96e1955b7d0125"
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
