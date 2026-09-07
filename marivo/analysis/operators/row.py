"""Exact pandas implementations of primary-only Metric row operations."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from functools import cmp_to_key

import pandas as pd

from marivo.analysis.compiler.errors import compilation_error
from marivo.analysis.datasets.descriptors import (
    DatasetRowContract,
    DatasetRowSetContract,
    _OrderedOrdering,
)
from marivo.analysis.datasets.handles import CanonicalValue
from marivo.analysis.observation.contracts import RankSpec
from marivo.analysis.observation.predicates import BoundPredicate


@dataclass(frozen=True, slots=True, repr=False)
class RowCall:
    method: str
    input_row: DatasetRowContract
    input_rows: DatasetRowSetContract
    output_row: DatasetRowContract
    output_rows: DatasetRowSetContract
    predicate: BoundPredicate | None = None
    rank: RankSpec | None = None
    limit: int | None = None


def _literal(value: CanonicalValue) -> bool | int | float | str | Decimal | date | datetime:
    if not isinstance(value, tuple) or len(value) != 2:
        raise compilation_error("canonical scalar literal", "invalid local predicate")
    kind, body = value
    if kind == "decimal" and isinstance(body, str):
        return Decimal(body)
    if kind == "date" and isinstance(body, str):
        return date.fromisoformat(body)
    if kind == "instant" and isinstance(body, str):
        return datetime.fromisoformat(body)
    if kind in ("integer", "floating", "boolean", "string") and isinstance(
        body, (bool, int, float, str)
    ):
        return body
    raise compilation_error("registered typed scalar", "unsupported local literal")


def predicate_mask(frame: pd.DataFrame, predicate: BoundPredicate) -> pd.Series:
    if predicate.kind in ("all_of", "any_of", "not_"):
        masks = [predicate_mask(frame, item) for item in predicate.children]
        if predicate.kind == "not_" and len(masks) == 1:
            return ~masks[0]
        if len(masks) < 2:
            raise compilation_error("complete Boolean predicate", "invalid children")
        result = masks[0]
        for mask in masks[1:]:
            result = result & mask if predicate.kind == "all_of" else result | mask
        return result
    if predicate.field is None or predicate.field.name not in frame:
        raise compilation_error("exact retained predicate field", "missing local field")
    column = frame[predicate.field.name]
    if predicate.kind == "is_null":
        return column.isna().astype("boolean")
    if predicate.kind == "is_not_null":
        return column.notna().astype("boolean")
    if predicate.kind == "is_in":
        if not isinstance(predicate.literal, tuple):
            raise compilation_error("typed membership literals", "invalid local membership")
        result = column.isin([_literal(item) for item in predicate.literal])
    else:
        literal = _literal(predicate.literal)
        if predicate.kind == "eq":
            result = column == literal
        elif predicate.kind == "not_eq":
            result = column != literal
        elif predicate.kind == "lt":
            result = column < literal
        elif predicate.kind == "lte":
            result = column <= literal
        elif predicate.kind == "gt":
            result = column > literal
        elif predicate.kind == "gte":
            result = column >= literal
        else:
            raise compilation_error("closed local predicate kind", "unsupported predicate")
    # The admitted source adapter orders NaN above every finite or infinite value.
    # Predicate literals are finite, so equality and membership never match NaN.
    if predicate.kind in ("eq", "not_eq", "lt", "lte", "gt", "gte"):
        nan = column.map(lambda value: isinstance(value, float) and math.isnan(value))
        result = result.mask(nan, predicate.kind in ("not_eq", "gt", "gte"))
    # pandas isin and NumPy comparisons otherwise turn SQL UNKNOWN into False.
    return result.astype("boolean").mask(column.isna(), pd.NA)


def _missing(value: object) -> bool:
    return value is None or value is pd.NA or value is pd.NaT


def compare_value(left: object, right: object) -> int:
    if _missing(left) or _missing(right):
        return 0 if _missing(left) and _missing(right) else (1 if _missing(left) else -1)
    if isinstance(left, tuple) and isinstance(right, tuple):
        for a, b in zip(left, right, strict=True):
            result = compare_value(a, b)
            if result:
                return result
        return 0
    if type(left) is not type(right):
        raise compilation_error("one exact scalar type", "mixed local ordering types")
    if isinstance(left, (bool, int, float, Decimal)) and isinstance(
        right, (bool, int, float, Decimal)
    ):
        return int(left > right) - int(left < right)
    if isinstance(left, str) and isinstance(right, str):
        return int(left > right) - int(left < right)
    if isinstance(left, datetime) and isinstance(right, datetime):
        return int(left > right) - int(left < right)
    if isinstance(left, date) and isinstance(right, date):
        return int(left > right) - int(left < right)
    raise compilation_error("comparable exact retained scalars", "unsupported local ordering")


def frame_comparator(
    frame: pd.DataFrame, row: DatasetRowContract, rows: DatasetRowSetContract
) -> Callable[[int, int], int]:
    """Own exact scalar, direction and null ordering for sorting and validation."""
    names = {field.field_id: field.name for field in row.schema.columns}
    terms = tuple((names[key], "ascending", "last") for key in row.key_field_ids)
    if isinstance(rows.ordering, _OrderedOrdering):
        terms = tuple((names[t.field_id], t.direction, t.nulls) for t in rows.ordering.terms)
    columns = {name: frame[name].tolist() for name, _, _ in terms}

    def compare(a: int, b: int) -> int:
        for name, direction, nulls in terms:
            left, right = columns[name][a], columns[name][b]
            result = compare_value(left, right)
            if _missing(left) or _missing(right):
                if nulls == "first":
                    result = -result
            elif direction == "descending":
                result = -result
            if result:
                return result
        return 0

    return compare


def ordered(
    frame: pd.DataFrame, row: DatasetRowContract, rows: DatasetRowSetContract
) -> pd.DataFrame:
    indices = sorted(range(len(frame)), key=cmp_to_key(frame_comparator(frame, row, rows)))
    return frame.iloc[indices].reset_index(drop=True)


def _rank(frame: pd.DataFrame, call: RowCall) -> pd.DataFrame:
    spec = call.rank
    if spec is None:
        raise compilation_error("exact registered rank specification", "missing rank")
    fields = {field.field_id: field.name for field in call.input_row.schema.columns}
    keys = tuple(fields[key] for key in call.input_row.key_field_ids)
    values = frame[spec.by.name].tolist()
    key_columns = [frame[key].tolist() for key in keys]
    partitions = [frame[field.name].tolist() for field in spec.partition_fields]
    groups: dict[tuple[object, ...], list[int]] = {}
    for index, value in enumerate(values):
        if _missing(value) or (isinstance(value, (float, Decimal)) and not math.isfinite(value)):
            continue
        key = tuple(None if _missing(column[index]) else column[index] for column in partitions)
        groups.setdefault(key, []).append(index)

    def compare(a: int, b: int) -> int:
        result = compare_value(values[a], values[b])
        if result:
            return result if spec.order == "ascending" else -result
        for column in key_columns:
            result = compare_value(column[a], column[b])
            if result:
                return result
        return 0

    ranks: list[int | None] = [None] * len(frame)
    for indices in groups.values():
        indices.sort(key=cmp_to_key(compare))
        start = 0
        dense = 0
        while start < len(indices):
            end = start + 1
            while (
                end < len(indices)
                and compare_value(values[indices[start]], values[indices[end]]) == 0
            ):
                end += 1
            dense += 1
            for position in range(start, end):
                ranks[indices[position]] = (
                    position + 1
                    if spec.ties == "ordinal"
                    else dense
                    if spec.ties == "dense"
                    else start + 1
                    if spec.ties == "min"
                    else end
                )
            start = end
    result = frame.copy(deep=True)
    result["rank"] = pd.Series(ranks, dtype="int64[pyarrow]")
    return result


def execute_row(frame: pd.DataFrame, call: RowCall) -> pd.DataFrame:
    """Consume a validated private frame without mutating or serializing it."""
    if call.method == "metric.where" and call.predicate is not None:
        result = frame.loc[predicate_mask(frame, call.predicate).fillna(False)].copy(deep=True)
    elif call.method == "metric.metric":
        result = frame.loc[:, [field.name for field in call.output_row.schema.columns]].copy(
            deep=True
        )
    elif call.method == "metric.rank":
        result = _rank(frame, call)
    elif call.method == "metric.limit" and call.limit is not None:
        result = frame.iloc[: call.limit].copy(deep=True)
    else:
        raise compilation_error(
            "a complete registered pandas row invocation", "invalid local method"
        )
    return ordered(result, call.output_row, call.output_rows)
