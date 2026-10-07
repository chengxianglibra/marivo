"""Sequential fixed selection preserves results while removing intermediate work."""

from __future__ import annotations

from dataclasses import replace
from typing import Literal, NoReturn

import pyarrow as pa
import pytest

from marivo.analysis.compiler.graph_lowering import LoweredLocal
from marivo.analysis.core import local_laws
from marivo.analysis.core.domain_captures import DomainPreparationError
from marivo.analysis.core.graph import Edge, MethodNode, method_node
from marivo.analysis.core.model import CoreRuleError
from marivo.analysis.core.predicates import ValuePredicate
from marivo.analysis.core.rules import CellDerive, PartsTransport
from marivo.analysis.materialization import execute_deadline
from marivo.analysis.materialization import graph_local_execution as local
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.graph_exchange import ExchangePart, ExchangeResult
from marivo.analysis.materialization.graph_protocol import freeze_graph, plan_digest
from marivo.analysis.methods.predicates import evaluate_leaf
from marivo.analysis.methods.registry import REGISTRY
from tests.analysis.graph.selection_fixtures import (
    selection,
    selection_chain,
    selection_input,
    selection_plan,
)


@pytest.mark.parametrize(
    "length,threshold,parts", [(2, 1, False), (8, 1, True), (2, 17, True), (8, 99, True)]
)
def test_selection_chain_matches_independent_domain_cells_parts_and_work(
    monkeypatch: pytest.MonkeyPatch, length: int, threshold: int, parts: bool
) -> None:
    leaf, verified = selection_input(parts=parts)
    root = selection_chain(leaf, length, threshold)
    prepared, lowered = selection_plan(root)
    before = root.fingerprint, freeze_graph(root), plan_digest(prepared.admitted)
    groups = local._selection_groups(lowered)
    assert tuple(len(group) for group in groups.values()) == (length,)
    visits: list[tuple[str, object]] = []
    indexes: list[int] = []
    boundaries: list[str] = []
    evaluate = local._selection_accepts
    index = local._index_rows
    boundary = local._transport_exchange

    def tracked(
        node: MethodNode,
        rows: list[dict[tuple[object, ...], dict[str, object]]],
        key: tuple[object, ...],
    ) -> bool:
        visits.append((node.identity, rows[0][key]["value"]))
        return evaluate(node, rows, key)

    def indexed(
        table: pa.Table, keys: tuple[str, ...]
    ) -> dict[tuple[object, ...], dict[str, object]]:
        indexes.append(table.num_rows)
        return index(table, keys)

    def finished(
        method: LoweredLocal,
        source: ExchangeResult,
        binding: str,
        primary: pa.Table,
        retained: tuple[ExchangePart, ...],
    ) -> ExchangeResult:
        boundaries.append(method.stage.node.identity)
        return boundary(method, source, binding, primary, retained)

    monkeypatch.setattr(local, "_selection_accepts", tracked)
    monkeypatch.setattr(local, "_index_rows", indexed)
    monkeypatch.setattr(local, "_transport_exchange", finished)
    result = local.execute_verified_fixed(prepared, lowered, (verified,))
    expected = [i for i in range(20) if i > threshold + length - 1]
    assert result.primary["value"].to_pylist() == expected
    assert result.primary["key_0"].to_pylist() == [i % 3 for i in expected]
    assert result.primary["key_1"].to_pylist() == list(map(str, expected))
    assert result.primary["cell_tag"].to_pylist() == ["defined"] * len(expected)
    assert result.primary["cell_reason"].to_pylist() == [None] * len(expected)
    assert result.contract.signature == root.signature
    assert result.completed_checks == ()
    for part in result.parts:
        assert part.table["key_1"].to_pylist() == list(map(str, expected))
    if parts:
        assert result.parts[1].table["original_state__sum"].to_pylist() == expected
        assert result.parts[2].table["coverage__complete"].to_pylist() == [True] * len(expected)
    fused_visits = tuple(visits)
    assert indexes == [20] and boundaries == [root.identity]
    visits.clear()
    monkeypatch.setattr(local, "_selection_groups", lambda plan: {})
    ordinary = local.execute_verified_fixed(prepared, lowered, (verified,))
    assert ordinary.contract == result.contract
    assert ordinary.primary.equals(result.primary)
    assert all(a.table.equals(b.table) for a, b in zip(ordinary.parts, result.parts, strict=True))
    assert tuple(visits) == fused_visits
    assert before == (root.fingerprint, freeze_graph(root), plan_digest(prepared.admitted))


def test_composite_predicates_keep_all_leaf_consumption(monkeypatch: pytest.MonkeyPatch) -> None:
    leaf, verified = selection_input()
    inner = selection(leaf, 10)
    binding = leaf.signature.domain.binding
    tree = ValuePredicate(
        binding,
        "any_of",
        0,
        children=(ValuePredicate(binding, "gt", 12), ValuePredicate(binding, "lt", 15)),
    )
    assert isinstance(inner.parameters, PartsTransport)
    root = method_node(
        (Edge("quantity", inner),),
        replace(inner.parameters, predicates=(tree,)),
        value_type=leaf.value_type,
    )
    prepared, lowered = selection_plan(root)
    consumed: list[tuple[str, object]] = []
    evaluate = evaluate_leaf

    def tracked(
        predicate: ValuePredicate, left: dict[str, object], right: dict[str, object] | None
    ) -> bool | None:
        consumed.append((predicate.operator, left["value"]))
        return evaluate(predicate, left, right)

    monkeypatch.setattr(local, "evaluate_leaf", tracked)
    result = local.execute_verified_fixed(prepared, lowered, (verified,))
    assert result.primary["value"].to_pylist() == list(range(11, 20))
    assert consumed[20:] == [(op, i) for i in range(11, 20) for op in ("gt", "lt")]


def test_shared_intermediate_is_materialized_and_used_once(monkeypatch: pytest.MonkeyPatch) -> None:
    leaf, verified = selection_input()
    shared = selection(leaf, 3)
    selected = selection(shared, 8)
    baseline = selection(shared, 8)
    root = method_node(
        (Edge("current", selected), Edge("baseline", baseline)),
        CellDerive(
            "difference",
            "difference",
            "strict",
            "units",
            "all",
            "source.exact_pairing@v1",
            "source.finite_numeric@v1",
        ),
        value_type=leaf.value_type,
    )
    prepared, lowered = selection_plan(root)
    assert local._selection_groups(lowered) == {}
    calls: list[str] = []
    execute = local._transport_stage

    def transported(
        stage: LoweredLocal,
        source: ExchangeResult,
        binding: str,
        predicates: tuple[ExchangeResult, ...] = (),
    ) -> ExchangeResult:
        calls.append(stage.stage.node.identity)
        return execute(stage, source, binding, predicates)

    monkeypatch.setattr(local, "_transport_stage", transported)
    result = local.execute_verified_fixed(prepared, lowered, (verified,))
    assert calls == [shared.identity, selected.identity, baseline.identity]
    assert result.primary["key_1"].to_pylist() == list(map(str, range(9, 20)))
    assert result.primary["value"].to_pylist() == [0] * 11
    assert all(check.requirement in lowered.admitted.checks for check in result.completed_checks)


@pytest.mark.parametrize("change", ["tag", "unknown", "float", "external", "projection"])
def test_nonordinary_or_different_selections_do_not_group(
    change: Literal["tag", "unknown", "float", "external", "projection"],
) -> None:
    leaf, verified = selection_input(dtype="float64" if change == "float" else "int64")
    inner = selection(leaf, 2)
    root = selection(inner, 4)
    params = root.parameters
    assert isinstance(params, PartsTransport)
    edges: tuple[Edge, ...] = (Edge("quantity", inner),)
    if change == "tag":
        params = replace(
            params, predicates=(ValuePredicate(leaf.signature.domain.binding, "is_defined", 0),)
        )
    elif change == "unknown":
        params = replace(params, predicates=(replace(params.predicates[0], unknown="drop"),))
    elif change == "external":
        params = replace(
            params,
            external_predicate=True,
            inclusion_inputs=(1,),
            predicates=(replace(params.predicates[0], input_index=1),),
        )
        edges = (*edges, Edge("quantity", leaf))
    elif change == "projection":
        params = replace(params, mode="projection", predicates=())
    root = method_node(edges, params, value_type=leaf.value_type)
    prepared, lowered = selection_plan(root)
    assert local._selection_groups(lowered) == {}
    result = local.execute_verified_fixed(prepared, lowered, (verified,))
    expected = list(range(3 if change in ("tag", "projection") else 5, 20))
    assert result.primary["value"].to_pylist() == expected


def test_fused_selection_reports_actual_conflicts_and_original_predicate_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    leaf, verified = selection_input()
    root = selection_chain(leaf, 2, 1)
    prepared, lowered = selection_plan(root)
    duplicate = pa.concat_tables((verified.result.primary, verified.result.primary.slice(0, 1)))
    with pytest.raises(MaterializationError, match="duplicate complete keys"):
        local.execute_verified_fixed(
            prepared,
            lowered,
            (replace(verified, result=replace(verified.result, primary=duplicate)),),
        )
    bad = verified.result.primary.set_column(3, "cell_tag", pa.array(["null"] + ["defined"] * 19))
    with pytest.raises(CoreRuleError, match="Cell tag null") as error:
        local.execute_verified_fixed(
            prepared, lowered, (replace(verified, result=replace(verified.result, primary=bad)),)
        )
    inner = root.inputs[0].node
    assert error.value.location == f"analysis.predicate.{inner.identity}"
    assert error.value.expected and error.value.repair


def test_deadline_remains_checked_during_survivor_evaluation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    leaf, verified = selection_input()
    root = selection_chain(leaf, 8, 1)
    prepared, lowered = selection_plan(root)
    ticks = [0.0]
    evaluate = local._selection_accepts

    def delayed(
        node: MethodNode,
        inputs: list[dict[tuple[object, ...], dict[str, object]]],
        key: tuple[object, ...],
    ) -> bool:
        ticks[0] += 100.0
        return evaluate(node, inputs, key)

    monkeypatch.setattr(local, "_selection_accepts", delayed)
    deadline = execute_deadline.ExecuteDeadline(0.0, clock=lambda: ticks[0])
    token = execute_deadline.CURRENT.set(deadline)
    try:
        with pytest.raises(DomainPreparationError, match="execute deadline exceeded"):
            local.execute_verified_fixed(prepared, lowered, (verified,))
    finally:
        execute_deadline.CURRENT.reset(token)


def test_part_order_is_preserved() -> None:
    leaf, verified = selection_input(parts=True)
    root = selection_chain(leaf, 2, 10)
    prepared, lowered = selection_plan(root)
    original = verified.result.parts[1]
    reversed_part = replace(original, table=original.table.take(list(reversed(range(20)))))
    verified = replace(
        verified,
        result=replace(
            verified.result,
            parts=(verified.result.parts[0], reversed_part, verified.result.parts[2]),
        ),
    )
    result = local.execute_verified_fixed(prepared, lowered, (verified,))
    assert result.primary["value"].to_pylist() == list(range(12, 20))
    assert result.parts[1].table["original_state__sum"].to_pylist() == list(reversed(range(12, 20)))


def test_missing_retained_part_fails_before_fused_predicate_evaluation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    leaf, verified = selection_input(parts=True)
    prepared, lowered = selection_plan(selection_chain(leaf, 2, 1))
    verified = replace(verified, result=replace(verified.result, parts=verified.result.parts[:1]))

    def forbidden(*args: object) -> NoReturn:
        pytest.fail("missing parts were consumed by fused execution")

    monkeypatch.setattr(local, "_selection_accepts", forbidden)
    with pytest.raises(MaterializationError, match="retained transport part is absent"):
        local.execute_verified_fixed(prepared, lowered, (verified,))


def test_physical_grouping_never_constructs_or_derives_another_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    leaf, verified = selection_input(parts=True)
    root = selection_chain(leaf, 8, 1)
    prepared, lowered = selection_plan(root)

    def forbidden(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("physical grouping rewrote the logical graph")

    monkeypatch.setattr(local_laws, "method_node", forbidden)
    monkeypatch.setattr(type(REGISTRY), "derive", forbidden)
    result = local.execute_verified_fixed(prepared, lowered, (verified,))
    assert result.contract.signature is root.signature
    assert result.primary["value"].to_pylist() == list(range(9, 20))
