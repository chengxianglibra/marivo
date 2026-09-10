"""Exact pandas Metric row operations and keyed retained-role transformations."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from functools import cmp_to_key

import pandas as pd
import pyarrow as pa

from marivo.analysis.compiler.errors import compilation_error
from marivo.analysis.datasets.descriptors import (
    DatasetRowContract,
    DatasetRowSetContract,
    _bool_tuple_value,
    _OrderedOrdering,
)
from marivo.analysis.datasets.handles import CanonicalValue
from marivo.analysis.domains.contracts import EventFunnelSemantics, EventTimeToEventSemantics
from marivo.analysis.observation.contracts import (
    EntityPresentMetricSemantics,
    EntityReducedMetricSemantics,
    RankSpec,
)
from marivo.analysis.observation.fold_contracts import FoldSpecV1
from marivo.analysis.observation.predicates import BoundPredicate
from marivo.analysis.operators.row_values import (
    _missing as _missing,
)
from marivo.analysis.operators.row_values import (
    compare_value as compare_value,
)
from marivo.analysis.operators.row_values import (
    frame_keys as frame_keys,
)
from marivo.analysis.operators.row_values import (
    row_key_names as row_key_names,
)


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
    fold: FoldSpecV1 | None = None


@dataclass(frozen=True, slots=True, repr=False)
class PartFrame:
    """One private, completely validated retained role in the local suffix."""

    role: str
    contract_id: str
    contract_version: int
    schema: pa.Schema
    keys: tuple[str, ...]
    frame: pd.DataFrame


def aligned_part_positions(
    part: PartFrame, keys: tuple[str, ...], expected: list[tuple[object, ...]]
) -> dict[tuple[object, ...], int]:
    """Require one exact state row per primary contribution key and return its position."""
    available = frame_keys(part.frame, part.keys)
    if (
        part.keys != keys
        or len(set(available)) != len(available)
        or set(available) != set(expected)
    ):
        raise compilation_error(
            "retained state for exactly the current row keys", "part key alignment differs"
        )
    return {key: position for position, key in enumerate(available)}


def select_parts(
    primary: pd.DataFrame,
    output: pd.DataFrame,
    call: RowCall,
    parts: tuple[PartFrame, ...],
) -> tuple[PartFrame, ...]:
    """Apply precisely the primary row selection to each computational role."""
    from marivo.analysis.observation.fold_contracts import fold_part_role

    semantics = call.output_row.family_semantics
    from marivo.analysis.operators.association_contracts import AssociationSemantics
    from marivo.analysis.operators.attribution_contracts import (
        AttributionSemantics,
        delta_part_authorities,
    )
    from marivo.analysis.operators.candidate_contracts import CandidateSemantics
    from marivo.analysis.operators.contracts import DeltaSemantics
    from marivo.analysis.operators.forecast_contracts import ForecastSemantics

    if isinstance(semantics, (EventFunnelSemantics, EventTimeToEventSemantics)):
        return tuple(part for part in parts if part.role == "population_sampling_state")
    if isinstance(
        semantics,
        (AttributionSemantics, AssociationSemantics, ForecastSemantics, CandidateSemantics),
    ):
        return ()
    if isinstance(semantics, DeltaSemantics):
        retained_roles = {role for role, _ in delta_part_authorities(call.input_row)}
        keys = row_key_names(call.input_row)
        expected = frame_keys(primary, keys)
        selected = frame_keys(output, keys)
        result: list[PartFrame] = []
        for part in parts:
            if part.role == "population_sampling_state":
                result.append(part)
            elif part.role in retained_roles:
                positions = aligned_part_positions(part, keys, expected)
                selected_frame = part.frame.iloc[[positions[key] for key in selected]].reset_index(
                    drop=True
                )
                result.append(
                    PartFrame(
                        part.role,
                        part.contract_id,
                        part.contract_version,
                        part.schema,
                        keys,
                        selected_frame,
                    )
                )
        return tuple(result)
    if not isinstance(semantics, (EntityPresentMetricSemantics, EntityReducedMetricSemantics)):
        raise compilation_error("Metric retained row semantics", "invalid row selection output")
    retained_roles = {fold_part_role(item) for item in semantics.metric_folds}
    keys = row_key_names(call.input_row)
    expected = frame_keys(primary, keys)
    selected = frame_keys(output, keys)
    result = []
    for part in parts:
        if part.role == "population_sampling_state":
            result.append(part)
            continue
        if part.role not in retained_roles:
            continue
        positions = aligned_part_positions(part, keys, expected)
        frame = part.frame.iloc[[positions[key] for key in selected]].reset_index(drop=True)
        result.append(
            PartFrame(part.role, part.contract_id, part.contract_version, part.schema, keys, frame)
        )
    return tuple(result)


def _literal(
    value: CanonicalValue,
) -> bool | int | float | str | Decimal | date | datetime | tuple[bool, ...]:
    if not isinstance(value, tuple) or len(value) != 2:
        raise compilation_error("canonical scalar literal", "invalid local predicate")
    kind, body = value
    if kind == "bool_tuple" and isinstance(body, tuple):
        mask = _bool_tuple_value(body)
        if mask is not None:
            return mask
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
        literals = [_literal(item) for item in predicate.literal]
        if any(isinstance(value, tuple) for value in literals):
            result = column.map(
                lambda value: any(compare_value(value, candidate) == 0 for candidate in literals)
            )
        else:
            result = column.isin(literals)
    else:
        literal = _literal(predicate.literal)
        if isinstance(literal, tuple):
            result = column.map(
                lambda value: (
                    compare_value(tuple(value) if isinstance(value, list) else value, literal) == 0
                )
            )
            if predicate.kind == "not_eq":
                result = ~result
        elif predicate.kind == "eq":
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


def frame_comparator(
    frame: pd.DataFrame, row: DatasetRowContract, rows: DatasetRowSetContract
) -> Callable[[int, int], int]:
    """Own exact scalar, direction and null ordering for sorting and validation."""
    names = {field.field_id: field.name for field in row.schema.columns}
    terms = tuple((names[key], "ascending", "last") for key in row.key_field_ids)
    if isinstance(rows.ordering, _OrderedOrdering):
        terms = tuple((names[t.field_id], t.direction, t.nulls) for t in rows.ordering.terms)
    columns = {name: frame[name].tolist() for name, _, _ in terms}
    from marivo.analysis.operators.association_contracts import association_orders

    authored = association_orders(row, rows)
    if isinstance(row.family_semantics, EventFunnelSemantics):
        authored["step_key"] = tuple(
            step.key for step in row.family_semantics.journey.pattern.steps
        )
    for name, values in authored.items():
        columns[name] = [values.index(value) for value in columns[name]]

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
    if (
        call.method
        in (
            "metric.where",
            "delta.where",
            "attribution.where",
            "association.where",
            "forecast.where",
            "candidate.where",
            "event.where",
        )
        and call.predicate is not None
    ):
        result = frame.loc[predicate_mask(frame, call.predicate).fillna(False)].copy(deep=True)
    elif call.method == "metric.metric":
        result = frame.loc[:, [field.name for field in call.output_row.schema.columns]].copy(
            deep=True
        )
    elif call.method in (
        "metric.rank",
        "delta.rank",
        "attribution.rank",
        "association.rank",
        "forecast.rank",
        "candidate.rank",
    ):
        result = _rank(frame, call)
    elif (
        call.method
        in (
            "metric.limit",
            "delta.limit",
            "attribution.limit",
            "association.limit",
            "forecast.limit",
            "candidate.limit",
        )
        and call.limit is not None
    ):
        result = frame.iloc[: call.limit].copy(deep=True)
    else:
        raise compilation_error(
            "a complete registered pandas row invocation", "invalid local method"
        )
    return ordered(result, call.output_row, call.output_rows)
