"""Compare the reference's observable result with both production policy routes."""

from __future__ import annotations

from ast import Attribute, Call, Dict, List, Name, expr, literal_eval, parse
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from fractions import Fraction
from typing import Literal, TypeAlias

import pandas as pd
import pyarrow as pa

from marivo._kernel.errors import MarivoError
from marivo.analysis import AnalysisFrame
from marivo.analysis.core.frame_data import MemoryFrameBacking
from marivo.analysis.model.data_types import DataType
from marivo.analysis.model.errors import AnalysisError
from marivo.analysis.model.execution_errors import MaterializationError
from marivo.analysis.model.frame_contracts import frame_backing
from marivo.analysis.model.optimization_policy import OptimizationPolicy
from marivo.semantic.errors import SemanticError
from tests.formal.reference.model import FailureKind, Key, Result, Value, valid_nan
from tests.support.optimization import compute_with_policy
from tests.support.source_trace import SourceTrace


@dataclass(frozen=True, slots=True)
class Cell:
    validity: Literal["valid", "nan", "missing"]
    value: Value = None


def _cell(value: Value) -> Cell:
    if value is None:
        return Cell("missing")
    if valid_nan(value):
        return Cell("nan")
    return Cell("valid", value)


def _scalar(value: object) -> Value:
    assert value is None or isinstance(value, (int, float, str, date, Decimal, Fraction)), value
    return value


@dataclass(frozen=True, slots=True)
class ObservedFrame:
    keys: tuple[tuple[Cell, ...], ...]
    key_types: tuple[DataType, ...]
    values: Mapping[str, tuple[Cell, ...]]
    value_types: Mapping[str, DataType]
    presence: Mapping[str, tuple[bool, ...]]


@dataclass(frozen=True, slots=True)
class ObservedFailure:
    category: FailureKind | None
    witnesses: tuple[Key, ...]
    stage: str
    expected: str | None
    received: str | None
    cause: tuple[str, str] | None = None


Outcome: TypeAlias = ObservedFrame | ObservedFailure


def capture_frame(result: AnalysisFrame) -> ObservedFrame:
    """Read exact checked cells in their existing row order, including presence slots."""
    backing = frame_backing(result)
    assert isinstance(backing, MemoryFrameBacking), backing
    schema = backing.data.schema
    rows = backing.data.copy_rows()
    arrays = {
        column.slot_id: tuple(
            _cell(_scalar(value.as_py()) if value.is_valid else None)
            for value in pa.array(rows[column.slot_id], from_pandas=False)
        )
        for column in schema.columns
    }
    names = tuple(".".join(path) for path in schema.value_paths)
    presence = {}
    for path, column in zip(schema.presence_paths, schema.presence, strict=True):
        cells = arrays[column.slot_id]
        assert all(cell.validity == "valid" and type(cell.value) is bool for cell in cells)
        presence[".".join(path)] = tuple(cell.value is True for cell in cells)
    key_fields = {column.slot_id: column.dtype for column in schema.columns}
    keys = schema.key_fields
    return ObservedFrame(
        tuple(tuple(arrays[slot][index] for slot in keys) for index in range(len(rows))),
        tuple(key_fields[slot] for slot in keys),
        dict(zip(names, (arrays[column.slot_id] for column in schema.values), strict=True)),
        dict(zip(names, (column.dtype for column in schema.values), strict=True)),
        presence,
    )


def _coordinate(node: expr) -> Value:
    if (
        isinstance(node, Call)
        and isinstance(node.func, Attribute)
        and isinstance(node.func.value, Name)
        and node.func.value.id == "datetime"
        and node.func.attr == "date"
    ):
        year, month, day = (_coordinate(argument) for argument in node.args)
        assert (
            not node.keywords
            and isinstance(year, int)
            and isinstance(month, int)
            and isinstance(day, int)
        )
        return date(year, month, day)
    if isinstance(node, Call) and isinstance(node.func, Name) and node.func.id == "Decimal":
        (value,) = node.args
        fixed: object = literal_eval(value)
        assert not node.keywords and isinstance(fixed, str)
        return Decimal(fixed)
    if isinstance(node, Call) and isinstance(node.func, Name) and node.func.id == "Timestamp":
        (value,) = node.args
        stamp: object = literal_eval(value)
        assert isinstance(stamp, str)
        if node.keywords:
            (zone,) = node.keywords
            timezone: object = literal_eval(zone.value)
            assert zone.arg == "tz" and isinstance(timezone, str)
            return pd.Timestamp(stamp, tz=timezone)
        return pd.Timestamp(stamp)
    return _scalar(literal_eval(node))


def _witnesses(received: str) -> tuple[Key, ...]:
    """Read complete key tuples from the structured coordinate diagnostic."""
    _, found, text = received.partition("coordinates=")
    if not found:
        return ()
    records = parse(text, mode="eval").body
    assert isinstance(records, List), received
    keys: list[Key] = []
    for record in records.elts:
        if isinstance(record, Dict):
            keys.append(tuple(_coordinate(value) for value in record.values))
        else:
            assert literal_eval(record) == (), received
            keys.append(())
    return tuple(keys)


def capture_failure(error: MarivoError) -> ObservedFailure:
    """Capture the owning category, key witnesses and original typed error fields."""
    expected = error.expected if isinstance(error, (AnalysisError, SemanticError)) else None
    received = error.received if isinstance(error, (AnalysisError, SemanticError)) else None
    stage = error.stage if isinstance(error, MaterializationError) else type(error).__name__
    category: FailureKind | None = None
    if stage == "population_coverage":
        category = "coverage"
    elif stage == "population_identity":
        category = "missing_identity"
    elif "nulls='error'" in (received or ""):
        category = "strict_null"
    elif "duplicate time keys" in (received or ""):
        category = "duplicate_order_key"
    elif "calendar correspondence" in (expected or ""):
        category = "invalid_correspondence"
    cause = error.__cause__
    return ObservedFailure(
        category,
        _witnesses(received or ""),
        stage,
        expected,
        received,
        (type(cause).__name__, str(cause)) if cause is not None else None,
    )


def run_frame(
    query: AnalysisFrame, policy: OptimizationPolicy, *, trace: SourceTrace | None = None
) -> Outcome:
    """Execute a pending public Frame with the selected internal optimization policy."""
    try:
        completed = compute_with_policy(query, policy)
    except MarivoError as error:
        return capture_failure(error)
    if trace is not None:
        trace.record(completed)
    return capture_frame(completed)


def _expected_cell(expected: Value, actual: Cell) -> Cell:
    assert isinstance(expected, bool) == isinstance(actual.value, bool), (expected, actual)
    if isinstance(expected, Fraction) and isinstance(actual.value, float):
        return _cell(float(expected))
    return _cell(expected)


def assert_equivalent(
    expected: Result,
    actual: Outcome,
    *,
    value_types: Mapping[str, DataType] | None = None,
    key_types: tuple[DataType, ...] | None = None,
) -> None:
    """Compare row order, full keys, validity, values, presence and required failures."""
    if expected.obligations:
        assert isinstance(actual, ObservedFailure), actual
        assert actual.category is not None and actual.expected and actual.received, actual
        witness = expected.witness(actual.category)
        assert witness is not None, (expected.obligations, actual)
        assert actual.witnesses and actual.witnesses[0] == witness, (witness, actual)
        return
    assert isinstance(actual, ObservedFrame), actual
    assert len(expected.keys) == len(actual.keys), (expected.keys, actual.keys)
    keys = tuple(
        tuple(_expected_cell(value, cell) for value, cell in zip(key, row, strict=True))
        for key, row in zip(expected.keys, actual.keys, strict=True)
    )
    assert actual.keys == keys, (expected.keys, actual.keys)
    assert tuple(actual.values) == tuple(expected.columns)
    if value_types is not None:
        assert actual.value_types == value_types
    if key_types is not None:
        assert actual.key_types == key_types
    for name, values in actual.values.items():
        reference = expected.columns[name]
        assert values == tuple(
            _expected_cell(reference.at(key), cell)
            for key, cell in zip(expected.keys, values, strict=True)
        ), (name, expected, actual)
    assert actual.presence == {
        name: tuple(key in present for key in expected.keys)
        for name, present in expected.presence.items()
    }


def assert_policies_agree(control: Outcome, optimized: Outcome) -> None:
    """Compare physical schema and output, or the same required structured failure."""
    if isinstance(control, ObservedFailure):
        assert isinstance(optimized, ObservedFailure), optimized
        assert control.category is not None and control.category == optimized.category
        assert control.stage == optimized.stage
        assert control.witnesses[:1] == optimized.witnesses[:1]
    else:
        assert control == optimized
