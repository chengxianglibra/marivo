"""Direct-key L8 preserves terminal state and eliminates intermediate finishing."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from decimal import Decimal
from fractions import Fraction
from typing import NoReturn

import pyarrow as pa
import pytest

from marivo.analysis.compiler.graph_lowering import LoweredLocal
from marivo.analysis.core.graph import Edge, method_node
from marivo.analysis.core.rules import CellDerive
from marivo.analysis.materialization import graph_local_execution as local
from marivo.analysis.materialization.cell_arrow import column as cell_column
from marivo.analysis.materialization.cell_arrow import logical_table
from marivo.analysis.materialization.cell_arrow import rows as cell_rows
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.graph_exchange import (
    ExchangePart,
    ExchangeResult,
    from_arrow,
    numeric_primary,
)
from marivo.analysis.materialization.graph_protocol import freeze_graph, plan_digest
from marivo.analysis.methods import numeric_state
from marivo.analysis.methods.physical import ScalarType
from tests.analysis.graph.reduction_fixtures import (
    Carrier,
    OriginalMethod,
    reduction,
    reduction_chain,
    reduction_input,
    reduction_plan,
)


@pytest.mark.parametrize("length", [2, 8])
@pytest.mark.parametrize(
    "method,carrier",
    [(m, "int64") for m in ("sum", "sum_zero", "count", "mean", "ratio", "weighted_mean", "linear")]
    + [
        (m, c)
        for m in ("sum", "mean", "ratio", "weighted_mean", "linear")
        for c in ("float64", "decimal")
    ]
    + [(m, "duration") for m in ("sum", "mean", "linear")],
)
def test_direct_reduction_cells_components_identity_and_work(
    monkeypatch: pytest.MonkeyPatch, method: OriginalMethod, carrier: Carrier, length: int
) -> None:
    leaf, verified = reduction_input(method=method, carrier=carrier)
    root = reduction_chain(leaf, length)
    prepared, lowered = reduction_plan(root)
    before = root.fingerprint, freeze_graph(root), plan_digest(prepared.admitted)
    assert [len(g) for g in local._reduction_groups(lowered).values()] == [length]
    visits: list[int] = []
    merge = numeric_state.merge_components
    boundary = from_arrow

    def merged(
        rows: Sequence[Mapping[str, object]],
        schema: pa.Schema,
        components: tuple[str, ...],
    ) -> dict[str, numeric_state.Number]:
        visits.append(len(rows))
        return merge(rows, schema, components)

    from unittest.mock import patch

    monkeypatch.setattr(numeric_state, "merge_components", merged)
    with patch.object(local, "from_arrow", wraps=boundary) as exchanges:
        result = local.execute_verified_fixed(prepared, lowered, (verified,))
        grouped_boundaries = exchanges.call_count
    assert visits == [12]
    with (
        patch.object(local, "_reduction_groups", return_value={}),
        patch.object(local, "from_arrow", wraps=boundary) as exchanges,
    ):
        ordinary = local.execute_verified_fixed(prepared, lowered, (verified,))
        ordinary_boundaries = exchanges.call_count
    assert grouped_boundaries == 2 and ordinary_boundaries == length + 1
    assert visits[1:] == [4, 4, 4, *([1] * (3 * (length - 2))), 3]
    assert logical_table(result.primary).equals(logical_table(ordinary.primary))
    assert result.contract == ordinary.contract
    assert result.method_state is not None and ordinary.method_state is not None
    assert result.method_state.equals(ordinary.method_state)
    assert [(p.role, p.table.to_pylist()) for p in result.parts] == [
        (p.role, p.table.to_pylist()) for p in ordinary.parts
    ]
    expected: int | float | Decimal = (
        24
        if method == "count"
        else 78 / 24
        if method == "mean"
        else 78 / 33
        if method == "ratio"
        else 220 / 33
        if method == "weighted_mean"
        else 0
        if method == "linear"
        else 78
    )
    if carrier == "decimal" and method not in ("ratio", "weighted_mean"):
        expected = Decimal(str(expected)).quantize(Decimal("0.01"))
    if carrier == "duration":
        expected = round(Fraction(78, 24)) if method == "mean" else int(expected)
    assert numeric_primary(result.primary)["value"].to_pylist() == [expected]
    assert cell_column(result.primary, "cell_tag").to_pylist() == ["defined"]
    state = next(p.table for p in result.parts if p.role == "original_state")
    for name in state.column_names:
        source = next(p.table for p in verified.result.parts if p.role == "original_state")
        if name.startswith("original_state__"):
            assert state[name].to_pylist() == [sum(source[name].to_pylist())]
    assert before == (root.fingerprint, freeze_graph(root), plan_digest(prepared.admitted))
    assert result.completed_checks == ()


@pytest.mark.parametrize(
    "method", ["sum", "sum_zero", "count", "mean", "ratio", "weighted_mean", "linear"]
)
def test_empty_state_is_complete_and_keeps_owning_finish(method: OriginalMethod) -> None:
    leaf, verified = reduction_input(0, method=method)
    prepared, lowered = reduction_plan(reduction_chain(leaf))
    result = local.execute_verified_fixed(prepared, lowered, (verified,))
    expected = (
        {"value": 0, "cell_tag": "defined", "cell_reason": None}
        if method in ("sum_zero", "count")
        else {"value": None, "cell_tag": "null", "cell_reason": "empty_contribution"}
    )
    assert cell_rows(result.primary) == [expected]
    assert all(
        value == 0
        for p in result.parts
        if p.role == "original_state"
        for value in p.table.to_pylist()[0].values()
    )


def test_shared_output_ends_group_and_is_consumed_once(monkeypatch: pytest.MonkeyPatch) -> None:
    leaf, verified = reduction_input()
    shared = reduction(leaf, keep=(0,))
    left, right = reduction(shared), reduction(shared)
    root = method_node(
        (Edge("current", left), Edge("baseline", right)),
        CellDerive(
            "difference",
            "difference",
            "strict",
            "units",
            "all",
            "source.exact_pairing@v1",
            "source.finite_numeric@v1",
        ),
        value_type=ScalarType("int64"),
    )
    prepared, lowered = reduction_plan(root)
    assert local._reduction_groups(lowered) == {}
    from unittest.mock import patch

    with patch.object(
        local, "_coordinate_rollup_stage", wraps=local._coordinate_rollup_stage
    ) as grouped:
        result = local.execute_verified_fixed(prepared, lowered, (verified,))
    assert grouped.call_count == 1
    assert result.primary["value"].to_pylist() == [0]


@pytest.mark.parametrize(
    "violation", ["duplicate", "missing", "subject", "column", "coverage", "overflow"]
)
def test_consumed_damage_fails_without_retry(
    monkeypatch: pytest.MonkeyPatch, violation: str
) -> None:
    leaf, verified = reduction_input(2)
    prepared, lowered = reduction_plan(reduction_chain(leaf))
    parts = list(verified.result.parts)
    if violation in ("missing", "subject"):
        role = "subject" if violation == "subject" else "original_state"
        parts = [p for p in parts if p.role != role]
    else:
        state = parts[1].table
        if violation == "duplicate":
            state = pa.concat_tables((state, state.slice(0, 1)))
        elif violation == "column":
            state = state.drop(["original_state__sum"])
        elif violation == "coverage":
            table = parts[2].table.set_column(2, "coverage__complete", pa.array([False, True]))
            parts[2] = ExchangePart("coverage", table)
        else:
            state = state.set_column(
                2, "original_state__sum", pa.array([2**63 - 1, 1], type=pa.int64())
            )
            primary = verified.result.primary.set_column(
                2, "value", pa.array([2**63 - 1, 1], type=pa.int64())
            )
            verified = replace(verified, result=replace(verified.result, primary=primary))
        parts[1] = ExchangePart("original_state", state)
    verified = replace(verified, result=replace(verified.result, parts=tuple(parts)))
    attempts: list[str] = []
    execute = local._reduction_group_result

    def grouped(
        group: tuple[LoweredLocal, ...], source: ExchangeResult, binding: str
    ) -> ExchangeResult:
        attempts.append("group")
        return execute(group, source, binding)

    monkeypatch.setattr(local, "_reduction_group_result", grouped)
    with pytest.raises(MaterializationError):
        local.execute_verified_fixed(prepared, lowered, (verified,))
    assert attempts == (["group"] if violation in ("duplicate", "coverage", "overflow") else [])


def test_execution_never_constructs_another_semantic_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    leaf, verified = reduction_input()
    prepared, lowered = reduction_plan(reduction_chain(leaf))

    def forbidden(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("L8 execution rebuilt a semantic graph")

    from marivo.analysis.core import graph

    monkeypatch.setattr(graph, "method_node", forbidden)
    assert local.execute_verified_fixed(prepared, lowered, (verified,)).primary[
        "value"
    ].to_pylist() == [78]


def test_terminal_groups_and_subject_transport_match_independent_keys() -> None:
    leaf, verified = reduction_input()
    root = reduction(reduction(leaf, keep=(0, 1)), keep=(0,))
    prepared, lowered = reduction_plan(root)
    result = local.execute_verified_fixed(prepared, lowered, (verified,))
    assert result.primary.select(("key_0", "value")).to_pylist() == [
        {"key_0": 0, "value": 22},
        {"key_0": 1, "value": 26},
        {"key_0": 2, "value": 30},
    ]
    assert result.contract.signature == root.signature
    identity = reduction(reduction(leaf, keep=(0, 1)), keep=(0, 1))
    prepared, lowered = reduction_plan(identity)
    result = local.execute_verified_fixed(prepared, lowered, (verified,))
    subject = next(p.table for p in result.parts if p.role == "subject")
    ordered = sorted(range(12), key=lambda i: (i % 3, str(i)))
    assert subject["subject__key_0"].to_pylist() == [i % 3 for i in ordered]
    assert subject["subject__key_1"].to_pylist() == [str(i) for i in ordered]


def test_coordinate_partition_is_an_explicit_fusion_boundary() -> None:
    import marivo.semantic as ms
    from marivo.analysis.core.model import CoordinateStatePart, OriginalStatePart

    leaf, _ = reduction_input()
    original = next(p for p in leaf.signature.parts if isinstance(p, OriginalStatePart))
    coordinate = CoordinateStatePart(
        original.binding,
        original.quantity_id,
        ms.ref.dimension("inventory.product.category"),
        ms.ref.entity("inventory.product"),
        original.components,
        "int64",
        "v1",
    )
    leaf = replace(
        leaf, signature=replace(leaf.signature, parts=(*leaf.signature.parts, coordinate))
    )
    root = reduction_chain(leaf)
    _, lowered = reduction_plan(root)
    assert local._reduction_groups(lowered) == {}


@pytest.mark.parametrize("method", ["ratio", "weighted_mean"])
def test_zero_denominator_preserves_distinct_cell_policies(method: OriginalMethod) -> None:
    leaf, verified = reduction_input(2, method=method)
    parts = list(verified.result.parts)
    state = parts[1].table
    denominator = (
        "original_state__denominator_sum" if method == "ratio" else "original_state__weight_sum"
    )
    state = state.set_column(
        state.schema.get_field_index(denominator), denominator, pa.array([0, 0], type=pa.int64())
    )
    parts[1] = ExchangePart("original_state", state)
    tag, reason = (
        ("undefined", "zero_denominator") if method == "ratio" else ("null", "zero_weight_sum")
    )
    from marivo.analysis.materialization.cell_arrow import compact_schema, from_rows

    primary = from_rows(
        (
            dict(row, value=None, cell_tag=tag, cell_reason=reason)
            for row in cell_rows(verified.result.primary)
        ),
        compact_schema(verified.result.primary.schema, ((tag, (reason,)),)),
    )
    verified = replace(
        verified,
        result=replace(
            verified.result,
            primary=primary,
            parts=tuple(parts),
            contract=replace(verified.result.contract, schema=primary.schema),
        ),
    )
    prepared, lowered = reduction_plan(reduction_chain(leaf))
    result = local.execute_verified_fixed(prepared, lowered, (verified,))
    assert cell_rows(result.primary) == [{"value": None, "cell_tag": tag, "cell_reason": reason}]


def test_float_regrouping_retains_magnitudes_and_native_rounding_contract() -> None:
    from unittest.mock import patch

    from marivo.analysis.methods.comparison import roundoff

    leaf, verified = reduction_input(4, carrier="float64")
    values = [1e16, 1.0, -1e16, 1.0]
    parts = list(verified.result.parts)
    state = parts[1].table
    for name, data in (("sum", values), ("absolute_sum", [abs(v) for v in values])):
        column = "original_state__" + name
        state = state.set_column(
            state.schema.get_field_index(column), column, pa.array(data, type=pa.float64())
        )
    parts[1] = ExchangePart("original_state", state)
    primary = verified.result.primary.set_column(2, "value", pa.array(values))
    verified = replace(
        verified, result=replace(verified.result, primary=primary, parts=tuple(parts))
    )
    prepared, lowered = reduction_plan(reduction_chain(leaf))
    grouped = local.execute_verified_fixed(prepared, lowered, (verified,))
    with patch.object(local, "_reduction_groups", return_value={}):
        ordinary = local.execute_verified_fixed(prepared, lowered, (verified,))
    oracle = sum(Fraction(v) for v in values)
    assert oracle == 2
    assert grouped.primary["value"].to_pylist() == [2.0]
    assert ordinary.primary["value"].to_pylist() == [1.0]
    assert grouped.contract == ordinary.contract
    for result in (grouped, ordinary):
        assert cell_column(result.primary, "cell_tag").to_pylist() == ["defined"]
        retained = next(p.table for p in result.parts if p.role == "original_state")
        magnitude = retained["original_state__absolute_sum"][0].as_py()
        assert magnitude == float(sum(Fraction(abs(v)) for v in values))
        assert retained["original_state__non_null_count"].to_pylist() == [7]
        assert abs(result.primary["value"][0].as_py() - float(oracle)) <= roundoff(magnitude)


def test_pending_checks_end_original_reduction_groups() -> None:
    from marivo.analysis.core.rules import OriginalReduce

    leaf, _ = reduction_input()
    params = reduction(leaf, keep=(0,)).parameters
    terminal = reduction(leaf).parameters
    assert isinstance(params, OriginalReduce) and isinstance(terminal, OriginalReduce)
    leaf = replace(leaf, signature=replace(leaf.signature, evidence=()))
    first = method_node(
        (Edge("quantity", leaf),),
        replace(
            params,
            partition_check_id="source.contribution_partition@v1",
            coverage_check_id="source.complete_coverage@v1",
        ),
        value_type=leaf.value_type,
    )
    root = method_node(
        (Edge("quantity", first),),
        replace(
            terminal,
            partition_check_id="source.contribution_partition@v1",
            coverage_check_id="source.complete_coverage@v1",
        ),
        value_type=leaf.value_type,
    )
    _, lowered = reduction_plan(root)
    assert lowered.admitted.checks
    assert local._reduction_groups(lowered) == {}


def test_group_preserves_each_node_proof_for_downstream_completed_checks() -> None:
    from unittest.mock import patch

    from marivo.analysis.core.rules import RowState

    leaf, verified = reduction_input()
    reduced = reduction_chain(leaf, 8)
    root = method_node(
        (Edge("quantity", reduced),),
        RowState(
            "mean",
            reduced.signature.domain,
            "current-row-mean",
            "strict",
            numeric_check_id="source.finite_numeric@v1",
        ),
        value_type=ScalarType("float64"),
    )
    prepared, lowered = reduction_plan(root)
    assert [len(group) for group in local._reduction_groups(lowered).values()] == [8]
    grouped = local.execute_verified_fixed(prepared, lowered, (verified,))
    with patch.object(local, "_reduction_groups", return_value={}):
        ordinary = local.execute_verified_fixed(prepared, lowered, (verified,))
    assert grouped.primary["value"].to_pylist() == [78.0]
    assert grouped.completed_checks and grouped.completed_checks == ordinary.completed_checks
    assert grouped.contract == ordinary.contract
