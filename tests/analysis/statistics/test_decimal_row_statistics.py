"""Exact Decimal current-row arithmetic over admitted local Arrow exchanges."""

from dataclasses import replace
from decimal import Decimal
from typing import Literal

import pyarrow as pa
import pytest

import marivo.semantic as ms
from marivo.analysis.compiler.graph_lowering import LoweredLocal, lower
from marivo.analysis.compiler.graph_plan import RouteChoice, plan
from marivo.analysis.core.graph import Edge, FixedLeaf, method_node
from marivo.analysis.core.model import (
    Binding,
    Coordinate,
    DomainSignature,
    Evidence,
    ObservedQuantity,
    Signature,
)
from marivo.analysis.core.rules import RowState
from marivo.analysis.materialization.graph_exchange import (
    ExchangeContract,
    ExchangeResult,
    from_arrow,
)
from marivo.analysis.materialization.graph_local_execution import _row_result
from marivo.analysis.methods.errors import MethodRegistrationError
from marivo.analysis.methods.numeric_state import checked_sum, finish_division
from marivo.analysis.methods.physical import DecimalType, FixedShape, NoTime
from marivo.analysis.methods.semantics import MethodKey
from marivo.analysis.refs import ArtifactRef


def _input(values: tuple[Decimal, ...], *, reverse: bool = False) -> ExchangeResult:
    binding = Binding("r94", "sales", "customers", "none")
    coordinate = Coordinate(ms.ref.entity("sales.customer"), "id", "identity")
    domain = DomainSignature(binding, "entity", (coordinate,), (coordinate,), "decimal_members")
    quantity = ObservedQuantity(
        "decimal_amount",
        ms.ref.metric("sales.amount"),
        "v1",
        "CNY",
        "none",
        "orders",
        "strict",
        "percentile@v1",
    )
    schema = pa.schema(
        (
            pa.field("key_0", pa.string()),
            pa.field("value", pa.decimal128(18, 2)),
            pa.field("cell_tag", pa.string()),
            pa.field("cell_reason", pa.string()),
        )
    )
    ordered = tuple(reversed(values)) if reverse else values
    table = pa.Table.from_arrays(
        (
            pa.array([str(index) for index in range(len(ordered))], type=pa.string()),
            pa.array(ordered, type=pa.decimal128(18, 2)),
            pa.array(["defined"] * len(ordered), type=pa.string()),
            pa.array([None] * len(ordered), type=pa.string()),
        ),
        schema=schema,
    )
    # A split, chunked carrier exercises the same row state across batch boundaries.
    split = max(1, len(ordered) // 2)
    table = pa.Table.from_batches(table.to_batches(max_chunksize=split), schema=schema)
    return from_arrow(
        table,
        ExchangeContract(
            Signature(domain, quantity),
            MethodKey("bind_project"),
            "artifact_decimal_input",
            schema,
            ("key_0",),
        ),
    )


def _reduce(
    source: ExchangeResult,
    method: Literal["sum", "mean"],
    input_type: DecimalType,
    *,
    merge: bool = False,
) -> ExchangeResult:
    if merge:
        proven = tuple(
            Evidence(check.requirement.obligation.fact, "check", check.result_digest)
            for check in source.completed_checks
        )
        assert tuple(check.requirement.obligation for check in source.completed_checks) == (
            source.contract.signature.obligations
        )
        signature = replace(
            source.contract.signature,
            obligations=(),
            evidence=(*source.contract.signature.evidence, *proven),
        )
        source = replace(source, contract=replace(source.contract, signature=signature))
    leaf = FixedLeaf(
        ArtifactRef(ref="artifact_decimal_input"),
        "decimal_input",
        source.contract.signature,
        input_type,
        FixedShape(NoTime()),
    )
    quantity = source.contract.signature.quantity
    assert quantity is not None
    domain = DomainSignature(source.contract.signature.domain.binding, "singleton", (), (), "all")
    node = method_node(
        (Edge("quantity", leaf),),
        RowState(
            method,
            domain,
            quantity.definition_id if merge else "decimal_" + method,
            "strict",
            merge=merge,
            numeric_check_id=None if merge else "source.finite_numeric@v1",
        ),
        value_type=DecimalType(
            38, max(input_type.scale, 6) if method == "mean" else input_type.scale
        ),
    )
    admitted = plan(node, routes=(RouteChoice(node.identity, "artifact_python"),))
    lowered = lower(admitted, bindings=())
    local = next(stage for stage in lowered.stages if isinstance(stage, LoweredLocal))
    return _row_result(
        local, source, "decimal_numeric_input", "artifact_decimal_input", admitted.checks
    )


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("method", ["sum", "mean"])
def test_decimal_row_statistics_preserve_large_fractional_values_and_sum_scale(
    method: Literal["sum", "mean"],
    reverse: bool,
) -> None:
    values = (Decimal("9007199254740993.01"), Decimal("9007199254740993.02"), Decimal("0.01"))
    result = _reduce(_input(values, reverse=reverse), method, DecimalType(18, 2))
    expected = (
        Decimal("18014398509481986.04") if method == "sum" else Decimal("6004799503160662.013333")
    )
    assert result.primary["value"].to_pylist() == [expected]
    output_type = pa.decimal128(38, 2 if method == "sum" else 6)
    assert result.primary.schema.field("value").type == output_type
    state = result.parts[0].table
    assert state.schema.field("row_state__sum").type == pa.decimal128(38, 2)
    assert state["row_state__sum"].to_pylist() == [Decimal("18014398509481986.04")]
    assert state["row_state__count"].to_pylist() == [3]
    merged = _reduce(result, method, DecimalType(38, 2 if method == "sum" else 6), merge=True)
    assert merged.primary.equals(result.primary)
    assert merged.parts[0].table.equals(state)


@pytest.mark.parametrize("total,expected", [("0.01", "0.000000"), ("0.03", "0.000002")])
def test_decimal_mean_rounds_half_even_once_from_exact_sum_count(total: str, expected: str) -> None:
    values = (Decimal(total), *(Decimal("0.00") for _ in range(19_999)))
    result = _reduce(_input(values, reverse=True), "mean", DecimalType(18, 2))
    assert result.primary["value"].to_pylist() == [Decimal(expected)]
    state = result.parts[0].table
    assert state["row_state__sum"].to_pylist() == [Decimal(total)]
    assert state["row_state__count"].to_pylist() == [20_000]
    assert state.schema.field("row_state__sum").type == pa.decimal128(38, 2)
    merged = _reduce(result, "mean", DecimalType(38, 6), merge=True)
    assert merged.primary.equals(result.primary)
    assert merged.parts[0].table.equals(state)


@pytest.mark.parametrize("method", ["sum", "mean"])
def test_decimal_empty_row_statistics_and_retained_merge(method: Literal["sum", "mean"]) -> None:
    result = _reduce(_input(()), method, DecimalType(18, 2))
    assert result.primary.to_pylist() == [
        {
            "value": Decimal("0.00") if method == "sum" else None,
            "cell_tag": "defined" if method == "sum" else "undefined",
            "cell_reason": None if method == "sum" else "empty_mean",
        }
    ]
    merged = _reduce(result, method, DecimalType(38, 2 if method == "sum" else 6), merge=True)
    assert merged.primary.equals(result.primary)
    assert merged.parts[0].table.equals(result.parts[0].table)


def test_decimal_numeric_state_rejects_sum_and_finish_precision_overflow() -> None:
    maximum = Decimal("999999999999999999999999999999999999.99")
    with pytest.raises(OverflowError, match="Decimal state precision overflow"):
        checked_sum((maximum, Decimal("0.01")), pa.decimal128(38, 2))
    with pytest.raises(OverflowError, match="Decimal finish precision overflow"):
        finish_division(Decimal("100000000000000000000000000000000"), 1, DecimalType(38, 6))


def test_decimal_row_registration_keeps_unproved_scales_closed() -> None:
    source = _input((Decimal("1.01"),))
    with pytest.raises(MethodRegistrationError, match="qualified exact key"):
        _reduce(source, "sum", DecimalType(18, 3))
