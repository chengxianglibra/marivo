"""Focused pandas contract checks for the private graph exchange."""

from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pyarrow as pa
import pytest

import marivo.semantic as ms
from marivo.analysis.core.model import (
    Binding,
    Coordinate,
    DerivedQuantity,
    DomainSignature,
    OriginalStatePart,
    Signature,
)
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.graph_exchange import (
    ExchangeContract,
    ExchangePart,
    PartContract,
    _pandas_records,
    from_pandas,
)
from marivo.analysis.methods.semantics import MethodKey


def _schema() -> pa.Schema:
    return pa.schema(
        [
            pa.field("key_0", pa.string()),
            pa.field("key_1", pa.int64()),
            pa.field("key_2", pa.int64()),
            pa.field("value", pa.int64()),
            pa.field("cell_tag", pa.string()),
            pa.field("cell_reason", pa.string()),
        ]
    )


def _primary(keys: list[tuple[str | None, int, int]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "key_0": [key[0] for key in keys],
            "key_1": [key[1] for key in keys],
            "key_2": [key[2] for key in keys],
            "value": [0] * len(keys),
            "cell_tag": ["defined"] * len(keys),
            "cell_reason": [None] * len(keys),
        }
    )


def _logical_records(frame: pd.DataFrame, contract: ExchangeContract) -> list[dict[str, object]]:
    return _pandas_records(frame, contract.schema)


def _contract(*, nullable: bool = False) -> ExchangeContract:
    binding = Binding("r96", "exchange", "bulk", "whole")
    owner = ms.ref.entity("cost.facts")
    keys = (
        Coordinate(owner, "attribution:axis:tenant" if nullable else "tenant", "group"),
        Coordinate(owner, "id", "identity"),
        Coordinate(owner, "revision", "identity"),
    )
    domain = DomainSignature(binding, "group" if nullable else "entity", keys, keys, "bulk")
    signature = Signature(
        domain, DerivedQuantity("input", "test@v1", ("raw",), None, "none", "input_owned")
    )
    return ExchangeContract(
        signature,
        MethodKey("parts_transport"),
        "bulk",
        _schema(),
        ("key_0", "key_1", "key_2"),
        cell_reasons=(
            ("null", ("empty_contribution",)),
            ("undefined", ("zero_denominator",)),
            ("unknown", ("insufficient_business_coverage",)),
        ),
    )


def _original(
    frame: pd.DataFrame,
) -> tuple[ExchangeContract, tuple[ExchangePart, ...], pd.DataFrame]:
    contract = _contract()
    state = OriginalStatePart(
        contract.signature.domain.binding,
        "input",
        "sum_zero@v1",
        "raw",
        ("sum", "non_null_count"),
        "v1",
    )
    keys = list(contract.key_fields)
    original = frame.loc[:, keys].copy()
    original["original_state__sum"] = frame["value"]
    original["original_state__non_null_count"] = 1
    status = frame.loc[:, keys].copy()
    status["status"] = frame["cell_tag"]
    state_schema = pa.schema(
        [*(contract.schema.field(key) for key in keys), pa.field("status", pa.string())]
    )
    part_schema = pa.schema(
        [
            *(contract.schema.field(key) for key in keys),
            pa.field("original_state__sum", pa.int64()),
            pa.field("original_state__non_null_count", pa.int64()),
        ]
    )
    contract = replace(
        contract,
        method=MethodKey("metric.sum_zero"),
        signature=replace(contract.signature, parts=(state,)),
        parts=(PartContract("original_state", part_schema, contract.key_fields),),
        state_kind="original_sum_zero",
        state_schema=state_schema,
    )
    return contract, (ExchangePart("original_state", original),), status


def test_pandas_exchange_preserves_large_complete_keys_and_empty_frames() -> None:
    frame = _primary([("a", 2**53 + index, index + 1) for index in range(1025)])
    contract = _contract()
    result = from_pandas(frame, contract)
    assert result.primary["key_1"].iloc[-1] == 2**53 + 1024
    assert _logical_records(result.primary, result.contract)[-1]["key_1"] == 2**53 + 1024
    assert len(from_pandas(frame.iloc[0:0], contract).primary) == 0


@pytest.mark.parametrize("corruption", ["duplicate", "null"])
def test_pandas_exchange_rejects_duplicate_or_illegal_null_keys(corruption: str) -> None:
    keys: list[tuple[str | None, int, int]] = [("a", 2**53 + index, 1) for index in range(4)]
    keys[-1] = keys[0] if corruption == "duplicate" else (None, 2**53 + 3, 1)
    with pytest.raises(MaterializationError, match="null or duplicate complete key"):
        from_pandas(_primary(keys), _contract())
    if corruption == "null":
        assert len(from_pandas(_primary(keys), _contract(nullable=True)).primary) == 4


def test_pandas_exchange_validates_all_four_cell_branches_and_exact_zero() -> None:
    frame = _primary([("a", 1, 1), ("a", 2, 1), ("a", 3, 1), ("a", 4, 1)])
    frame["value"] = [0, None, None, None]
    frame["cell_tag"] = ["defined", "null", "undefined", "unknown"]
    frame["cell_reason"] = [
        None,
        "empty_contribution",
        "zero_denominator",
        "insufficient_business_coverage",
    ]
    result = from_pandas(frame, _contract())
    rows = _logical_records(result.primary, result.contract)
    assert [row["cell_tag"] for row in rows] == ["defined", "null", "undefined", "unknown"]
    assert rows[0]["value"] == 0
    assert [rows[index]["cell_reason"] for index in range(1, 4)] == [
        "empty_contribution",
        "zero_denominator",
        "insufficient_business_coverage",
    ]


@pytest.mark.parametrize(
    ("value", "tag", "reason"),
    [
        (None, "defined", None),
        (0, "null", "empty_contribution"),
        (None, "unknown", None),
        (None, "other", "empty_contribution"),
    ],
)
def test_pandas_exchange_rejects_invalid_or_undeclared_cells(
    value: int | None, tag: str, reason: str | None
) -> None:
    frame = _primary([("a", 1, 1)])
    frame.loc[0, ["value", "cell_tag", "cell_reason"]] = [value, tag, reason]
    with pytest.raises(MaterializationError, match=r"invalid .*Defined Cell"):
        from_pandas(frame, _contract())


@pytest.mark.parametrize("corruption", ["part_key", "state_key", "sum"])
def test_pandas_exchange_verifies_independent_state_keys_and_numeric_state(
    corruption: str,
) -> None:
    frame = _primary([("a", 2**53 + 1, 1), ("a", 2**53 + 2, 1)])
    contract, parts, status = _original(frame)
    original = parts[0].table.copy()
    if corruption == "part_key":
        original["key_1"] = [2**53 + 1, 2**53 + 3]
    elif corruption == "state_key":
        status["key_1"] = [2**53 + 1, 2**53 + 3]
    else:
        original["original_state__sum"] = [0, 1]
    with pytest.raises(MaterializationError):
        from_pandas(
            frame,
            contract,
            parts=(ExchangePart("original_state", original),),
            method_state=status,
        )
