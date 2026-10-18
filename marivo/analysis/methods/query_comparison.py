"""Pandas alignment, endpoint comparison and complete-key reference transport."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from numbers import Integral, Real
from typing import Literal

import pandas as pd
import pyarrow as pa

from marivo._definition.correspondence import ComparisonAxis
from marivo._definition.types import CalendarCoordinate, literal_token
from marivo.analysis.binding.correspondence import (
    BoundCorrespondence,
    BroadcastMapping,
    ComparisonEndpoint,
    PresenceBinding,
    ProjectionBinding,
    broadcast_mapping,
    fail,
)
from marivo.analysis.core.data_types import (
    DataColumn,
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
)
from marivo.analysis.methods.numeric_state import nullable_arithmetic
from marivo.semantic.errors import SemanticExpressionError


@dataclass(frozen=True, slots=True)
class ComparisonOutput:
    layout: ComparisonEndpoint
    frame: pd.DataFrame

    @property
    def columns(self) -> tuple[DataColumn, ...]:
        return self.layout.columns

    @property
    def keys(self) -> tuple[str, ...]:
        return self.layout.keys

    @property
    def axes(self) -> tuple[ComparisonAxis, ...]:
        return self.layout.axes

    @property
    def key_groups(self) -> tuple[tuple[str, ...], ...]:
        return self.layout.key_groups

    @property
    def presence(self) -> tuple[PresenceBinding, ...]:
        return self.layout.presence

    @property
    def projections(self) -> tuple[ProjectionBinding, ...]:
        return self.layout.projections


def _missing(value: object) -> bool:
    return (
        value is None
        or value is pd.NA
        or value is pd.NaT
        or (isinstance(value, Real) and math.isnan(float(value)))
    )


def _token(value: object) -> tuple[str, str]:
    if _missing(value):
        return ("null", "")
    if isinstance(value, bool):
        return literal_token(value)
    if isinstance(value, Integral):
        return literal_token(int(value))
    if isinstance(value, Real):
        if math.isnan(float(value)):
            return ("null", "")
        return literal_token(float(value))
    if isinstance(value, (str, Decimal, date, datetime)):
        return literal_token(value)
    fail("an exact typed scalar coordinate", repr(value))
    raise AssertionError


def _key(row: dict[str, object], keys: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    return tuple(_token(row[key]) for key in keys)


def _rows(endpoint: ComparisonEndpoint, frame: pd.DataFrame) -> list[dict[str, object]]:
    if tuple(frame.columns) != tuple(column.slot_id for column in endpoint.columns):
        fail("the complete prepared endpoint column schema", repr(tuple(frame.columns)))
    rows = [
        dict(zip(frame.columns, values, strict=True))
        for values in frame.itertuples(index=False, name=None)
    ]
    seen: set[tuple[tuple[str, str], ...]] = set()
    for row in rows:
        key = _key(row, endpoint.keys)
        if key in seen:
            fail("unique complete endpoint keys", repr(key))
        seen.add(key)
        for axis, group in zip(endpoint.axes, endpoint.key_groups, strict=True):
            if isinstance(axis, CalendarCoordinate):
                value = row[group[0]]
                if (
                    len(group) != 1
                    or isinstance(value, bool)
                    or not isinstance(value, Integral)
                    or not 0 <= int(value) < len(axis.cells)
                ):
                    fail("original frozen calendar bucket keys", repr(value))
    return rows


def _relation_images(
    binding: BoundCorrespondence,
    frame: pd.DataFrame | None,
) -> tuple[dict[tuple[tuple[str, str], ...], str], dict[tuple[tuple[str, str], ...], str]]:
    requirement = binding.relation_requirement
    if requirement is None:
        if frame is not None:
            fail(
                "mapping data only for an explicit relation dependency", "unexpected mapping frame"
            )
        return {}, {}
    if frame is None or tuple(frame.columns) != tuple(
        column.slot_id for column in requirement.columns
    ):
        fail("the explicit complete left/right identity mapping schema", repr(requirement))
    left: dict[tuple[tuple[str, str], ...], str] = {}
    right: dict[tuple[tuple[str, str], ...], str] = {}
    for values in frame.itertuples(index=False, name=None):
        row: dict[str, object] = dict(zip(frame.columns, values, strict=True))
        a, b = _key(row, requirement.left_keys), _key(row, requirement.right_keys)
        if any(token[0] == "null" for token in (*a, *b)) or a in left or b in right:
            fail("a unique nonnull one-to-one full identity mapping", repr((a, b)))
        token = repr((a, b))
        left[a], right[b] = token, token
    return left, right


def _indices(
    binding: BoundCorrespondence,
    rows: list[dict[str, object]],
    side: int,
    relation_images: dict[tuple[tuple[str, str], ...], str],
) -> dict[tuple[str, ...], dict[str, object]]:
    endpoint = binding.left if side == 0 else binding.right
    result: dict[tuple[str, ...], dict[str, object]] = {}
    calendar_images = {
        pair[side]: repr(pair) for pair in binding.calendar_pairs if pair[side] is not None
    }
    for row in rows:
        components: list[str] = []
        for i, j in binding.pairs:
            index = i if side == 0 else j
            group = endpoint.key_groups[index]
            values = _key(row, group)
            if binding.transformed == (i, j):
                if binding.calendar_pairs:
                    value = row[group[0]]
                    assert isinstance(value, Integral)
                    component = calendar_images[int(value)]
                elif binding.relation is not None and binding.relation.direct:
                    ordered = tuple(sorted(binding.relation.direct))
                    component = repr(tuple(values[a if side == 0 else b] for a, b in ordered))
                else:
                    # Missing relation values remain genuinely unmatched, even if raw keys agree.
                    component = relation_images.get(values, repr((binding.roles[side], values)))
            else:
                component = repr(values)
            components.append(component)
        image = tuple(components)
        if image in result:
            fail("a one-to-one image of complete endpoint keys", repr(image))
        result[image] = row
    return result


def _paired_rows(
    binding: BoundCorrespondence,
    left: pd.DataFrame,
    right: pd.DataFrame,
    relation_frame: pd.DataFrame | None,
) -> list[tuple[tuple[str, ...], dict[str, object] | None, dict[str, object] | None]]:
    a, b = _relation_images(binding, relation_frame)
    current = _indices(binding, _rows(binding.left, left), 0, a)
    baseline = _indices(binding, _rows(binding.right, right), 1, b)
    if binding.how == "exact" and set(current) != set(baseline):
        fail(
            "equal complete endpoint key images for exact alignment",
            f"{binding.roles[0]} only={tuple(sorted(set(current) - set(baseline)))[:5]!r}; {binding.roles[1]} only={tuple(sorted(set(baseline) - set(current)))[:5]!r}",
        )
    return [
        (key, current.get(key), baseline.get(key)) for key in sorted(set(current) | set(baseline))
    ]


def _base_row(
    binding: BoundCorrespondence,
    key: tuple[str, ...],
    left: dict[str, object] | None,
    right: dict[str, object] | None,
) -> dict[str, object]:
    row: dict[str, object] = {}
    for position, (i, j) in enumerate(binding.pairs):
        output_group = binding.layout.key_groups[position]
        if binding.transformed == (i, j) and binding.time_axis == "paired":
            row[output_group[0]] = key[position]
        elif left is not None:
            row.update(
                zip(
                    output_group,
                    (left[source] for source in binding.left.key_groups[i]),
                    strict=True,
                )
            )
        elif binding.transformed == (i, j) and binding.time_axis == "current":
            fail("an actual current date on every output row", "baseline-only complete key")
        else:
            assert right is not None
            row.update(
                zip(
                    output_group,
                    (right[source] for source in binding.right.key_groups[j]),
                    strict=True,
                )
            )
    projection_position = 0
    for role, endpoint, source in zip(
        binding.roles, (binding.left, binding.right), (left, right), strict=True
    ):
        for group in (*endpoint.key_groups, *(item.columns for item in endpoint.projections)):
            projection = binding.layout.projections[projection_position]
            projection_position += 1
            row.update(
                zip(
                    projection.columns,
                    (source[key] if source is not None else None for key in group),
                    strict=True,
                )
            )
        row[f"presence.{role}"] = source is not None
        for index, nested in enumerate(endpoint.presence):
            row[f"presence.{role}.{index}"] = (
                bool(source[nested.column]) if source is not None else False
            )
    return row


def _typed_frame(endpoint: ComparisonEndpoint, rows: list[dict[str, object]]) -> pd.DataFrame:
    """Preserve integer/Decimal/time values before the shared stage normalizer."""
    return pd.DataFrame(
        {
            column.slot_id: pd.Series(
                [
                    None
                    if column.dtype == ScalarDataType("float64") and _missing(row[column.slot_id])
                    else row[column.slot_id]
                    for row in rows
                ],
                dtype=pd.ArrowDtype(pa.duration(column.dtype.unit))
                if isinstance(column.dtype, DurationDataType)
                else "boolean"
                if column.dtype == ScalarDataType("boolean")
                else "float64"
                if column.dtype == ScalarDataType("float64")
                else "object",
            )
            for column in endpoint.columns
        }
    )


def align_frames(
    binding: BoundCorrespondence,
    left: pd.DataFrame,
    right: pd.DataFrame,
    *,
    relation_frame: pd.DataFrame | None = None,
) -> ComparisonOutput:
    """Align checked complete keys and preserve independent endpoint presence."""
    if binding.operation != "align":
        fail("an align binding for align_frames", binding.operation)
    output: list[dict[str, object]] = []
    for key, a, b in _paired_rows(binding, left, right, relation_frame):
        row = _base_row(binding, key, a, b)
        for role, endpoint, source in zip(
            binding.roles, (binding.left, binding.right), (a, b), strict=True
        ):
            row.update(
                (f"value.{role}.{index}", source[column.slot_id] if source is not None else None)
                for index, column in enumerate(endpoint.values)
            )
        output.append(row)
    return ComparisonOutput(binding.layout, _typed_frame(binding.layout, output))


def compare_frames(
    binding: BoundCorrespondence,
    current: pd.DataFrame,
    baseline: pd.DataFrame,
    *,
    relation_frame: pd.DataFrame | None = None,
    zero_denominator: Literal["null", "error"] = "null",
) -> ComparisonOutput:
    """Calculate absolute/relative changes after both endpoint values are complete."""
    if binding.operation != "compare" or zero_denominator not in ("null", "error"):
        fail(
            "a compare binding and null/error denominator policy",
            repr((binding.operation, zero_denominator)),
        )
    paired = _paired_rows(binding, current, baseline, relation_frame)
    rows = [_base_row(binding, key, a, b) for key, a, b in paired]
    right_values = {column.name: column for column in binding.right.values}
    for index, column in enumerate(binding.left.values):
        other = right_values[column.name]
        a = pd.Series(
            [left[column.slot_id] if left is not None else None for _, left, _ in paired],
            dtype=object,
        )
        b = pd.Series(
            [right[other.slot_id] if right is not None else None for _, _, right in paired],
            dtype=object,
        )
        a = pd.Series([value if pd.notna(value) else pd.NA for value in a], dtype=object)
        b = pd.Series([value if pd.notna(value) else pd.NA for value in b], dtype=object)
        # Only complete non-NA endpoint pairs demand the new relative denominator.
        denominator = pd.Series(
            [
                (value.copy_abs() if isinstance(value, Decimal) else abs(value))
                if pd.notna(value) and pd.notna(a.iloc[position])
                else pd.NA
                for position, value in enumerate(b)
            ],
            dtype=object,
        )
        try:
            absolute = nullable_arithmetic("subtract", a, b)
            relative = nullable_arithmetic(
                "divide", absolute, denominator, zero_denominator=zero_denominator
            )
        except SemanticExpressionError as error:
            fail(
                error.expected or "valid numeric compare arithmetic",
                f"current/baseline {column.name}: {error.received}",
            )
        output_type = binding.layout.column(f"value.{index}.absolute").dtype
        for position, row in enumerate(rows):
            delta = absolute.iloc[position]
            if pd.isna(delta):
                delta = None
            if (
                pd.notna(delta)
                and isinstance(output_type, DecimalDataType)
                and not isinstance(delta, Decimal)
            ):
                if not isinstance(delta, (int, float)):
                    fail("an exact numeric absolute difference", repr(delta))
                delta = Decimal(delta)
            if output_type == ScalarDataType("float64") and pd.notna(delta):
                delta = float(delta)
            change = relative.iloc[position]
            row[f"value.{index}.current"] = a.iloc[position]
            row[f"value.{index}.baseline"] = b.iloc[position]
            row[f"value.{index}.absolute"] = delta
            row[f"value.{index}.relative"] = float(change) if pd.notna(change) else math.nan
    return ComparisonOutput(binding.layout, _typed_frame(binding.layout, rows))


def broadcast_series(
    reference: ComparisonEndpoint,
    reference_frame: pd.DataFrame,
    target: ComparisonEndpoint,
    target_frame: pd.DataFrame,
    *,
    value_slot: str,
    mapping: BroadcastMapping | None = None,
) -> pd.Series:
    """Transport one value over a complete, unique, covering target-key mapping."""
    if value_slot not in {column.slot_id for column in reference.values}:
        fail("a value column on the reference Query", value_slot)
    bound = broadcast_mapping(reference, target) if mapping is None else mapping
    if tuple(destination for _, destination in bound.keys) != reference.keys:
        fail("a complete ordered mapping to every reference key", repr(bound))
    if any(source not in {column.slot_id for column in target.columns} for source, _ in bound.keys):
        fail("an explicit target projection in the prepared schema", repr(bound))
    refs, targets = _rows(reference, reference_frame), _rows(target, target_frame)
    if not reference.keys and len(refs) != 1:
        fail("exactly one zero-axis reference key", str(len(refs)))
    by_key = {_key(row, reference.keys): row[value_slot] for row in refs}
    values: list[object] = []
    calendars = {item.destination: item.buckets for item in bound.calendars}
    for row in targets:
        if any(_missing(row[column]) or not bool(row[column]) for column in bound.presence):
            fail("a present endpoint for every demanded broadcast projection", repr(bound.presence))
        projected: list[tuple[str, str]] = []
        for source, destination in bound.keys:
            value = row[source]
            if destination in calendars:
                image = calendars[destination]
                if (
                    isinstance(value, bool)
                    or not isinstance(value, Integral)
                    or not 0 <= int(value) < len(image)
                ):
                    fail("an original target bucket in the prepared calendar image", repr(value))
                value = image[int(value)]
            projected.append(_token(value))
        key = tuple(projected)
        if key not in by_key:
            fail("reference coverage for every complete target key", repr(key))
        values.append(by_key[key])
    return pd.Series(values, dtype=reference_frame[value_slot].dtype).reset_index(drop=True)
