"""Exact Decimal current-row arithmetic over admitted local Arrow exchanges."""

from decimal import Decimal

import pyarrow as pa
import pytest

import marivo.semantic as ms
from marivo.analysis.core.model import (
    Binding,
    Coordinate,
    DomainSignature,
    ObservedQuantity,
    Signature,
)
from marivo.analysis.materialization.graph_exchange import (
    ExchangeContract,
    ExchangeResult,
    from_arrow,
)
from marivo.analysis.methods.numeric_state import checked_sum, finish_division
from marivo.analysis.methods.physical import DecimalType
from marivo.analysis.methods.semantics import MethodKey


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


@pytest.mark.parametrize("total,expected", [("0.01", "0.000000"), ("0.03", "0.000002")])
def test_decimal_mean_rounds_half_even_once_from_exact_sum_count(total: str, expected: str) -> None:
    # Keep the half-unit quotient without expanding zero-valued rows. The
    # source row state and retained merge are exercised by the cases above.
    exact_sum = checked_sum((Decimal(total), Decimal("0.00")), pa.decimal128(38, 2))
    assert exact_sum == Decimal(total)
    assert finish_division(exact_sum, 20_000, DecimalType(38, 6)) == Decimal(expected)


def test_decimal_numeric_state_rejects_sum_and_finish_precision_overflow() -> None:
    maximum = Decimal("999999999999999999999999999999999999.99")
    with pytest.raises(OverflowError, match="Decimal state precision overflow"):
        checked_sum((maximum, Decimal("0.01")), pa.decimal128(38, 2))
    with pytest.raises(OverflowError, match="Decimal finish precision overflow"):
        finish_division(Decimal("100000000000000000000000000000000"), 1, DecimalType(38, 6))
