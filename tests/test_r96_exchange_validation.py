"""Bounded bulk validation and invocation-owned primary key indexes."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from typing import Literal

import pyarrow as pa
import pytest

import marivo.semantic as ms
from marivo.analysis.core.domain_captures import DomainPreparationError
from marivo.analysis.core.model import (
    Binding,
    Coordinate,
    DerivedQuantity,
    DomainSignature,
    OriginalStatePart,
    Signature,
)
from marivo.analysis.materialization import execute_deadline
from marivo.analysis.materialization import graph_exchange as exchange
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.graph_exchange import (
    CheckedStream,
    ExchangeContract,
    ExchangePart,
    PartContract,
    collect,
    from_arrow,
)
from marivo.analysis.methods.semantics import MethodKey


class _Stream:
    def __init__(self, schema: pa.Schema, batches: tuple[pa.RecordBatch, ...]) -> None:
        self.schema = schema
        self.batches = batches
        self.close_calls = 0

    def __iter__(self) -> Iterator[pa.RecordBatch]:
        return iter(self.batches)

    def close(self) -> None:
        self.close_calls += 1


class _IterationFailure(_Stream):
    def __iter__(self) -> Iterator[pa.RecordBatch]:
        yield self.batches[0]
        raise RuntimeError("iteration failed")


class _CloseFailure(_Stream):
    def close(self) -> None:
        super().close()
        raise RuntimeError("close failed")


def _primary(keys: list[tuple[str | None, int, int]]) -> pa.Table:
    return pa.table(
        {
            "key_0": pa.array([key[0] for key in keys], type=pa.string()),
            "key_1": pa.array([key[1] for key in keys], type=pa.int64()),
            "key_2": pa.array([key[2] for key in keys], type=pa.int64()),
            "value": pa.array([0] * len(keys), type=pa.int64()),
            "cell_tag": pa.array(["defined"] * len(keys), type=pa.string()),
            "cell_reason": pa.array([None] * len(keys), type=pa.string()),
        }
    )


def _contract(primary: pa.Table, *, nullable: bool = False) -> ExchangeContract:
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
        primary.schema,
        ("key_0", "key_1", "key_2"),
        cell_reasons=(
            ("null", ("empty_contribution",)),
            ("undefined", ("zero_denominator",)),
            ("unknown", ("insufficient_business_coverage",)),
        ),
    )


def _original(
    primary: pa.Table,
) -> tuple[ExchangeContract, tuple[ExchangePart, ...], pa.Table]:
    contract = _contract(primary)
    state = OriginalStatePart(
        contract.signature.domain.binding,
        "input",
        "sum_zero@v1",
        "raw",
        ("sum", "non_null_count"),
        "v1",
    )
    original = primary.select(contract.key_fields).append_column(
        "original_state__sum", primary["value"]
    )
    original = original.append_column(
        "original_state__non_null_count", pa.array([1] * primary.num_rows, type=pa.int64())
    )
    status = primary.select(contract.key_fields).append_column("status", primary["cell_tag"])
    contract = replace(
        contract,
        method=MethodKey("metric.sum_zero"),
        signature=replace(contract.signature, parts=(state,)),
        parts=(PartContract("original_state", original.schema, contract.key_fields),),
        state_kind="original_sum_zero",
        state_schema=status.schema,
    )
    return contract, (ExchangePart("original_state", original),), status


def test_bulk_complete_keys_preserve_int64_versions_across_batches_and_empty_input() -> None:
    keys: list[tuple[str | None, int, int]] = [
        ("a", 2**53 + index // 2, index % 2 + 1) for index in range(1025)
    ]
    primary = _primary(keys)
    source = _Stream(primary.schema, tuple(primary.to_batches(max_chunksize=513)))
    result = collect(source, _contract(primary))
    assert result.primary.equals(primary)
    assert source.close_calls == 1
    assert exchange._table_keys(primary, _contract(primary).key_fields) == set(keys)
    empty = primary.slice(0, 0)
    source = _Stream(empty.schema, ())
    assert collect(source, _contract(empty)).primary.equals(empty)
    assert source.close_calls == 1


def test_bulk_keys_preserve_unkeyed_cardinality_and_empty_field_validation() -> None:
    singleton = pa.table({"value": [1]})
    assert exchange._table_keys(singleton, ()) == {()}
    assert exchange._table_keys(singleton.slice(0, 0), ()) == set()
    with pytest.raises(MaterializationError, match="null or duplicate complete part key"):
        exchange._table_keys(pa.table({"value": [1, 2]}), ())
    with pytest.raises(KeyError):
        exchange._table_keys(singleton.slice(0, 0), ("missing",))


@pytest.mark.parametrize("invalid", ["duplicate", "null"])
def test_bulk_keys_reject_cross_batch_duplicates_and_illegal_nulls(invalid: str) -> None:
    keys: list[tuple[str | None, int, int]] = [("a", 2**53 + index, 1) for index in range(1025)]
    keys[-1] = keys[0] if invalid == "duplicate" else (None, 2**53 + 1024, 1)
    primary = _primary(keys)
    source = _Stream(primary.schema, tuple(primary.to_batches(max_chunksize=1024)))
    with pytest.raises(MaterializationError, match="null or duplicate complete key"):
        collect(source, _contract(primary))
    assert source.close_calls == 1
    with pytest.raises(MaterializationError, match="null or duplicate complete part key"):
        exchange._table_keys(primary, _contract(primary).key_fields)


def test_completed_key_index_does_not_cross_nullable_policy_or_source_invocations() -> None:
    primary = _primary([(None, 2**53 + 7, 1), ("a", 2**53 + 7, 2)])
    assert from_arrow(primary, _contract(primary, nullable=True)).primary.equals(primary)
    with pytest.raises(MaterializationError, match="null or duplicate complete key"):
        from_arrow(primary, _contract(primary))
    with pytest.raises(MaterializationError, match="null or duplicate complete part key"):
        exchange._table_keys(primary, _contract(primary).key_fields)


def test_bulk_cells_preserve_all_four_branches_and_exact_defined_zero() -> None:
    primary = _primary([("a", 2**53 + index, 1) for index in range(4)])
    primary = primary.set_column(3, "value", pa.array([0, None, None, None], type=pa.int64()))
    primary = primary.set_column(
        4, "cell_tag", pa.array(["defined", "null", "undefined", "unknown"], type=pa.string())
    )
    primary = primary.set_column(
        5,
        "cell_reason",
        pa.array(
            [None, "empty_contribution", "zero_denominator", "insufficient_business_coverage"],
            type=pa.string(),
        ),
    )
    result = from_arrow(primary, _contract(primary))
    assert result.primary.equals(primary)
    assert result.primary.schema.field("value").type == pa.int64()


@pytest.mark.parametrize(
    ("value", "tag", "reason"),
    [
        (None, "defined", None),
        (0, "defined", "empty_contribution"),
        (0, "null", "empty_contribution"),
        (None, "undefined", "empty_contribution"),
        (None, "unknown", None),
        (None, "other", "empty_contribution"),
    ],
)
def test_bulk_cells_reject_invalid_payloads_and_undeclared_reasons(
    value: int | None, tag: str, reason: str | None
) -> None:
    primary = _primary([("a", 2**53 + 1, 1)])
    primary = primary.set_column(3, "value", pa.array([value], type=pa.int64()))
    primary = primary.set_column(4, "cell_tag", pa.array([tag], type=pa.string()))
    primary = primary.set_column(5, "cell_reason", pa.array([reason], type=pa.string()))
    source = _Stream(primary.schema, tuple(primary.to_batches()))
    with pytest.raises(MaterializationError, match=r"invalid .*Defined Cell"):
        collect(source, _contract(primary))
    assert source.close_calls == 1


def test_collect_reuses_only_its_completed_primary_key_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary = _primary([("a", 2**53 + 1, 1), ("a", 2**53 + 1, 2)])
    contract, parts, status = _original(primary)
    calls: list[tuple[str, ...]] = []
    table_keys = exchange._table_keys

    def independent(
        table: pa.Table, fields: tuple[str, ...], nullable: frozenset[str] = frozenset()
    ) -> set[tuple[object, ...]]:
        calls.append(tuple(table.column_names))
        return table_keys(table, fields, nullable)

    monkeypatch.setattr(exchange, "_table_keys", independent)
    source = _Stream(primary.schema, tuple(primary.to_batches(max_chunksize=1)))
    result = collect(source, contract, parts=parts, method_state=status)
    assert result.primary.equals(primary)
    assert calls == [tuple(parts[0].table.column_names), tuple(status.column_names)]
    assert source.close_calls == 1


@pytest.mark.parametrize("corruption", ["part_key", "duplicate", "null", "state_key", "sum"])
def test_independent_parts_and_numerical_state_still_reject_corruption(corruption: str) -> None:
    primary = _primary([("a", 2**53 + 1, 1), ("a", 2**53 + 2, 1)])
    contract, parts, status = _original(primary)
    original = parts[0].table
    if corruption == "part_key":
        original = original.set_column(
            1, "key_1", pa.array([2**53 + 1, 2**53 + 3], type=pa.int64())
        )
    elif corruption == "duplicate":
        original = original.set_column(1, "key_1", pa.array([2**53 + 1] * 2, type=pa.int64()))
    elif corruption == "null":
        original = original.set_column(0, "key_0", pa.array(["a", None], type=pa.string()))
    elif corruption == "state_key":
        status = status.set_column(1, "key_1", pa.array([2**53 + 1, 2**53 + 3], type=pa.int64()))
    else:
        original = original.set_column(3, "original_state__sum", pa.array([0, 1], type=pa.int64()))
    with pytest.raises(MaterializationError):
        from_arrow(
            primary,
            contract,
            parts=(ExchangePart("original_state", original),),
            method_state=status,
        )


@pytest.mark.parametrize("failure", ["schema", "iteration", "close", "early_stop"])
def test_key_index_requires_successful_exhaustion_and_close(failure: str) -> None:
    primary = _primary([("a", 2**53 + 1, 1), ("a", 2**53 + 2, 1)])
    batches = tuple(primary.to_batches(max_chunksize=1))
    source: _Stream
    if failure == "schema":
        changed = batches[1].set_column(1, "key_1", pa.array([1.0], type=pa.float64()))
        source = _Stream(primary.schema, (batches[0], changed))
    elif failure == "iteration":
        source = _IterationFailure(primary.schema, batches)
    elif failure == "close":
        source = _CloseFailure(primary.schema, batches)
    else:
        source = _Stream(primary.schema, batches)
    checked = CheckedStream(source, primary.schema, _contract(primary).key_fields)
    if failure == "early_stop":
        iterator = iter(checked)
        next(iterator)
        checked.close()
    else:
        with pytest.raises((MaterializationError, RuntimeError)):
            tuple(checked)
    assert source.close_calls == 1
    assert not checked.completed
    with pytest.raises(MaterializationError, match="producer key validation did not complete"):
        checked._complete_key_index()


@pytest.mark.parametrize("boundary", ["stream", "part"])
def test_bulk_key_validation_keeps_deadline_checks_and_stream_release(
    boundary: Literal["stream", "part"],
) -> None:
    primary = _primary([("a", 2**53 + index, 1) for index in range(4)])
    source = _Stream(primary.schema, tuple(primary.to_batches()))
    calls = 0

    def clock() -> float:
        nonlocal calls
        calls += 1
        return 601.0 if calls >= 4 else 0.0

    token = execute_deadline.CURRENT.set(execute_deadline.ExecuteDeadline(0.0, clock=clock))
    committed = execute_deadline.COMMITTED.set(False)
    try:
        with pytest.raises(DomainPreparationError, match="execute deadline exceeded"):
            if boundary == "stream":
                collect(source, _contract(primary))
            else:
                exchange._table_keys(primary, _contract(primary).key_fields)
    finally:
        execute_deadline.COMMITTED.reset(committed)
        execute_deadline.CURRENT.reset(token)
    assert calls == 4
    assert source.close_calls == (1 if boundary == "stream" else 0)
