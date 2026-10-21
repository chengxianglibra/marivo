"""Immutable explanatory Attribute facts, without executable source authority.

These records validate closed data structure and associations. Numerical consistency
with source execution remains with the producer's existing arithmetic policies.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import date
from decimal import Decimal
from itertools import islice
from math import isfinite
from typing import Literal, NoReturn, TypeAlias

import pandas as pd
import pyarrow as pa

from marivo.analysis.core.data_schema import (
    CalendarDataAxis,
    DataSchema,
    IntervalDataAxis,
    PairedDataAxis,
)
from marivo.analysis.core.data_types import (
    DataColumn,
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
    TimestampDataType,
)
from marivo.analysis.core.frozen_calendar import (
    FrozenAttachment,
    FrozenCalendarDirectory,
    FrozenIntervalDirectory,
    coordinate_attachment_schemas,
    frozen_key_rows,
    read_calendar_directory,
    read_interval_directory,
    validate_paired_mapping,
)
from marivo.analysis.core.model import reject
from marivo.semantic.unit_algebra import ratio_unit

_MethodScalar: TypeAlias = bool | int | float | str | date | Decimal | None
_Method: TypeAlias = "FrameAdditiveMethod | FrameRatioMethod | FrameLinearMethod"


def _fail(expected: str, received: str, location: str = "frame.method_facts") -> NoReturn:
    reject(
        expected,
        received,
        "Retain the exact closed facts from the completed Attribute result.",
        location,
    )


def _text(value: str) -> None:
    if type(value) is not str or not value or any(c in value for c in "\n\r\t"):
        _fail("a nonempty single-line fact identity", type(value).__name__)


def _short(value: str, limit: int = 100) -> str:
    return value if len(value) <= limit else value[: limit - 3] + "..."


def _cell(value: _MethodScalar) -> str:
    if isinstance(value, str):
        return repr(_short(value, 70))
    if isinstance(value, Decimal):
        sign, digits, exponent = value.as_tuple()
        if len(digits) > 80:
            return f"Decimal(sign={sign}, digits={len(digits)}, exponent={exponent})"
    return repr(value)


def _coefficient_text(value: int | float | Decimal) -> str:
    if isinstance(value, int) and value.bit_length() > 256:
        return f"int(bits={value.bit_length()})"
    return _cell(value)


def _quotient_unit(numerator: str | None, denominator: str | None) -> str | None:
    try:
        return ratio_unit(numerator, denominator)
    except ValueError:
        _fail("governed ratio component units", "invalid numerator or denominator unit")


def _scalar(column: DataColumn, value: _MethodScalar) -> None:
    if type(column) is not DataColumn:
        _fail("a concrete DataColumn", type(column).__name__)
    if value is None:
        if not column.nullable:
            _fail("a nonnull value for a nonnullable column", column.name)
        return
    dtype = column.dtype
    valid = False
    if isinstance(dtype, ScalarDataType):
        if dtype.name == "int64":
            valid = type(value) is int and -(2**63) <= value < 2**63
        elif dtype.name == "float64":
            valid = type(value) is float and isfinite(value)
        elif dtype.name == "boolean":
            valid = type(value) is bool
        elif dtype.name == "string":
            valid = type(value) is str
        else:
            valid = type(value) is date
    elif isinstance(dtype, (TimestampDataType, DurationDataType)):
        valid = type(value) is int and -(2**63) <= value < 2**63
    elif type(value) is Decimal and value.is_finite():
        _, digits, exponent = value.as_tuple()
        assert isinstance(exponent, int)
        # Check representability without rounding or changing the original Decimal.
        trailing = max(0, -exponent - dtype.scale)
        valid = (not trailing or all(d == 0 for d in digits[-trailing:])) and (
            value.is_zero() or len(digits) + exponent <= dtype.precision - dtype.scale
        )
    if not valid:
        _fail("an exact finite scalar matching column type, precision and time unit", column.name)


class _FactRecord:
    __slots__ = ()

    def _summary(self) -> str:
        raise NotImplementedError

    def _lines(self) -> Iterator[str]:
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"{type(self).__name__}({_short(self._summary(), 120)}; .show())"

    def show(self, *, n: int = 20) -> None:
        """Print a bounded view of already-owned facts.

        Args:
            n: Exact nonnegative int excluding bool; zero shows fields and counts.
        Returns:
            None; prints at most n detail lines, with each line bounded.
        Example:
            ``facts.show(n=0)``.
        Constraints: Reads only these immutable records; performs no source or file I/O.
        """
        if type(n) is not int or n < 0:
            _fail("a nonnegative integer preview count", repr(n), "frame.method_facts.show")
        print(f"{type(self).__name__}: {_short(self._summary(), 180)}")
        assert is_dataclass(self)
        print("Fields: " + ", ".join(field.name for field in fields(self)))
        for line in islice(self._lines(), n):
            print(_short(line, 240))


@dataclass(frozen=True, slots=True, repr=False)
class FrameMethodValue(_FactRecord):
    """One exact physical value. Args: column and value; e.g. ``FrameMethodValue(c, 1)``.

    The frozen result preserves nullable values, Decimal precision and integer time ticks.
    """

    column: DataColumn = field(
        metadata={
            "meaning": "Read method_value.column for this saved value's exact physical dtype, unit and nullability. This is read-only saved metadata; nested dtype implementations are not public constructors."
        }
    )
    value: bool | int | float | str | date | Decimal | None

    def __post_init__(self) -> None:
        _scalar(self.column, self.value)

    def _summary(self) -> str:
        return f"kind=value, column={_short(self.column.name)!r}"

    def _lines(self) -> Iterator[str]:
        yield f"{_short(self.column.name)} = {_cell(self.value)}; dtype={self.column.dtype!r}; unit={self.column.unit!r}"


@dataclass(frozen=True, slots=True, repr=False)
class FrameMethodCoordinate(_FactRecord):
    """Own a named finite typed snapshot. Args: name, schema, rows; e.g. ``FrameMethodCoordinate('c', s, ())``.

    Returns a frozen record; rows must be immutable exact-width scalar tuples.
    """

    name: str
    schema: DataSchema = field(
        metadata={
            "meaning": "Read coordinate.schema for the complete saved coordinate layout. Read-only schema metadata; nested axes and bindings do not acquire public construction authority."
        }
    )
    rows: tuple[tuple[bool | int | float | str | date | Decimal | None, ...], ...]

    def __post_init__(self) -> None:
        _text(self.name)
        if type(self.schema) is not DataSchema or type(self.rows) is not tuple:
            _fail("a pure schema and immutable coordinate rows", type(self).__name__)
        for row in self.rows:
            if type(row) is not tuple or len(row) != len(self.schema.columns):
                _fail("complete immutable rows in schema column order", self.name)
            for column, value in zip(self.schema.columns, row, strict=True):
                _scalar(column, value)

    def _summary(self) -> str:
        return f"kind=coordinate, name={_short(self.name)!r}, rows={len(self.rows)}, columns={len(self.schema.columns)}"

    def _lines(self) -> Iterator[str]:
        for row in self.rows:
            yield " | ".join(_cell(value) for value in row)


@dataclass(frozen=True, slots=True, repr=False)
class FrameMethodSupport(_FactRecord):
    """Explain a local sample root; counts certify no source qualification.

    Args: root_id, policy and four exact counts. Example: ``FrameMethodSupport('r', 'rows', 2, 2, 2, 2)``.
    Returns a frozen record; effective counts cannot exceed input counts.
    """

    root_id: str
    policy: Literal[
        "rows", "non_null_value", "co_valid_value_weight", "identity", "preserved_sample"
    ]
    left_input_count: int
    right_input_count: int
    left_effective_count: int
    right_effective_count: int

    def __post_init__(self) -> None:
        _text(self.root_id)
        if type(self.policy) is not str or self.policy not in (
            "rows",
            "non_null_value",
            "co_valid_value_weight",
            "identity",
            "preserved_sample",
        ):
            _fail("a closed sample policy", repr(self.policy))
        counts = (
            self.left_input_count,
            self.right_input_count,
            self.left_effective_count,
            self.right_effective_count,
        )
        if any(type(count) is not int or count < 0 for count in counts) or (
            self.left_effective_count > self.left_input_count
            or self.right_effective_count > self.right_input_count
        ):
            _fail(
                "nonnegative exact counts with effective <= input on each side",
                ", ".join(_coefficient_text(count) for count in counts),
            )

    def _summary(self) -> str:
        return f"kind=support, root={_short(self.root_id)!r}, policy={self.policy}"

    def _lines(self) -> Iterator[str]:
        yield (
            f"left input/effective={_coefficient_text(self.left_input_count)}/{_coefficient_text(self.left_effective_count)}; "
            f"right input/effective={_coefficient_text(self.right_input_count)}/{_coefficient_text(self.right_effective_count)}"
        )


def _total(value: FrameMethodValue) -> None:
    if (
        type(value) is not FrameMethodValue
        or value.value is None
        or not (
            isinstance(value.column.dtype, (DecimalDataType, DurationDataType))
            or value.column.dtype in (ScalarDataType("int64"), ScalarDataType("float64"))
        )
    ):
        _fail("a nonnull typed numeric or Duration parent total", type(value).__name__)


def _totals(left: FrameMethodValue, right: FrameMethodValue, change: FrameMethodValue) -> None:
    for value in (left, right, change):
        _total(value)
    if not left.column.unit == right.column.unit == change.column.unit:
        _fail("matching total and change units", "inconsistent parent total units")
    durations = tuple(isinstance(v.column.dtype, DurationDataType) for v in (left, right, change))
    if any(durations) and not (
        all(durations) and left.column.dtype == right.column.dtype == change.column.dtype
    ):
        _fail("matching exact Duration units or numeric totals", "inconsistent total types")


def _supports(support: tuple[FrameMethodSupport, ...]) -> None:
    if (
        type(support) is not tuple
        or not support
        or any(type(s) is not FrameMethodSupport for s in support)
    ):
        _fail("a nonempty immutable tuple of exact root supports", type(support).__name__)
    if len({s.root_id for s in support}) != len(support):
        _fail("unique root supports within a method", "duplicate root_id")


def _method(value: _Method) -> None:
    if type(value) not in (FrameAdditiveMethod, FrameRatioMethod, FrameLinearMethod):
        _fail("a closed additive, ratio_component or linear method", type(value).__name__)


def _method_supports(method: _Method) -> Iterator[FrameMethodSupport]:
    if isinstance(method, FrameLinearMethod):
        for term in method.terms:
            yield from _method_supports(term.method)
    else:
        yield from method.support


def same_method_facts(left: FrameMethodFacts, right: FrameMethodFacts) -> bool:
    """Compare complete typed payloads, including preserved scalar representations."""

    def method_values(method: _Method) -> Iterator[str]:
        for value in (method.left, method.right, method.change):
            yield repr(value.value)
        if isinstance(method, FrameRatioMethod):
            for value in (
                method.numerator_left,
                method.numerator_right,
                method.denominator_left,
                method.denominator_right,
            ):
                yield repr(value.value)
        if isinstance(method, FrameLinearMethod):
            for term in method.terms:
                yield repr(term.coefficient)
                yield from method_values(term.method)

    def scalar_values(facts: FrameMethodFacts) -> Iterator[str]:
        for parent in facts.parents:
            for key in parent.key:
                yield repr(key.value)
            for metric in parent.metrics:
                yield from method_values(metric.method)
        for coordinate in facts.coordinates:
            for row in coordinate.rows:
                for value in row:
                    yield repr(value)

    return left == right and tuple(scalar_values(left)) == tuple(scalar_values(right))


def _method_lines(method: _Method) -> Iterator[str]:
    yield f"{method.kind}: left={_cell(method.left.value)}, right={_cell(method.right.value)}, change={_cell(method.change.value)}"
    if isinstance(method, FrameLinearMethod):
        for index, term in enumerate(method.terms):
            yield f"term[{index}] coefficient={_coefficient_text(term.coefficient)}"
            yield from _method_lines(term.method)
    else:
        if isinstance(method, FrameRatioMethod):
            yield f"numerator left/right={_cell(method.numerator_left.value)}/{_cell(method.numerator_right.value)}; denominator left/right={_cell(method.denominator_left.value)}/{_cell(method.denominator_right.value)}"
        for support in method.support:
            yield repr(support)


@dataclass(frozen=True, slots=True, repr=False)
class FrameAdditiveMethod(_FactRecord):
    """Explain additive totals. Args: kind, left, right, change, support.

    Returns a frozen inert rule; e.g. ``FrameAdditiveMethod('additive', l, r, d, (s,))``.
    Source and arithmetic qualification belongs to the successful producer.
    """

    kind: Literal["additive"]
    left: FrameMethodValue
    right: FrameMethodValue
    change: FrameMethodValue
    support: tuple[FrameMethodSupport, ...]

    def __post_init__(self) -> None:
        if self.kind != "additive" or type(self.kind) is not str:
            _fail("kind='additive'", repr(self.kind))
        _totals(self.left, self.right, self.change)
        _supports(self.support)

    def _summary(self) -> str:
        return f"kind=additive, column={_short(self.left.column.name)!r}, roots={len(self.support)}"

    def _lines(self) -> Iterator[str]:
        yield from _method_lines(self)


@dataclass(frozen=True, slots=True, repr=False)
class FrameRatioMethod(_FactRecord):
    """Explain parent ratio components. Args: kind, totals, four components, support.

    Returns a frozen rule; e.g. ``FrameRatioMethod('ratio_component', l, r, d, nl, nr, dl, dr, (s,))``.
    Denominators must be nonzero; component support never substitutes for execution proof.
    """

    kind: Literal["ratio_component"]
    left: FrameMethodValue
    right: FrameMethodValue
    change: FrameMethodValue
    numerator_left: FrameMethodValue
    numerator_right: FrameMethodValue
    denominator_left: FrameMethodValue
    denominator_right: FrameMethodValue
    support: tuple[FrameMethodSupport, ...]

    def __post_init__(self) -> None:
        if self.kind != "ratio_component" or type(self.kind) is not str:
            _fail("kind='ratio_component'", repr(self.kind))
        _totals(self.left, self.right, self.change)
        _totals(self.numerator_left, self.numerator_right, self.numerator_left)
        _totals(self.denominator_left, self.denominator_right, self.denominator_left)
        for numerator, denominator in (
            (self.numerator_left, self.denominator_left),
            (self.numerator_right, self.denominator_right),
        ):
            if denominator.value == 0:
                _fail("nonzero parent ratio denominators", denominator.column.name)
            if (
                _quotient_unit(numerator.column.unit, denominator.column.unit)
                != self.left.column.unit
            ):
                _fail("component units whose ratio matches the metric", self.left.column.name)
        _supports(self.support)

    def _summary(self) -> str:
        return f"kind=ratio_component, column={_short(self.left.column.name)!r}, roots={len(self.support)}"

    def _lines(self) -> Iterator[str]:
        yield from _method_lines(self)


@dataclass(frozen=True, slots=True, repr=False)
class FrameLinearTerm(_FactRecord):
    """Explain a consumed coefficient and subrule. Args: coefficient, method.

    Returns a frozen term; e.g. ``FrameLinearTerm(-1, rule)``. Coefficients are finite,
    exact int/float/Decimal scalars excluding bool; child order remains unchanged.
    """

    coefficient: int | float | Decimal
    method: FrameAdditiveMethod | FrameRatioMethod | FrameLinearMethod

    def __post_init__(self) -> None:
        if (
            type(self.coefficient) not in (int, float, Decimal)
            or (isinstance(self.coefficient, float) and not isfinite(self.coefficient))
            or (isinstance(self.coefficient, Decimal) and not self.coefficient.is_finite())
        ):
            _fail("a finite non-bool original numeric coefficient", type(self.coefficient).__name__)
        _method(self.method)

    def _summary(self) -> str:
        return f"kind=linear_term, child={self.method.kind}, column={_short(self.method.left.column.name)!r}"

    def _lines(self) -> Iterator[str]:
        yield f"coefficient={_coefficient_text(self.coefficient)}"
        yield from _method_lines(self.method)


@dataclass(frozen=True, slots=True, repr=False)
class FrameLinearMethod(_FactRecord):
    """Explain ordered linear terms. Args: kind, left, right, change, terms.

    Returns a frozen rule; e.g. ``FrameLinearMethod('linear', l, r, d, (term,))``.
    No offsets, expression rewriting or executable recipe is retained.
    """

    kind: Literal["linear"]
    left: FrameMethodValue
    right: FrameMethodValue
    change: FrameMethodValue
    terms: tuple[FrameLinearTerm, ...]

    def __post_init__(self) -> None:
        if self.kind != "linear" or type(self.kind) is not str:
            _fail("kind='linear'", repr(self.kind))
        _totals(self.left, self.right, self.change)
        if (
            type(self.terms) is not tuple
            or not self.terms
            or any(type(t) is not FrameLinearTerm for t in self.terms)
        ):
            _fail("nonempty ordered immutable linear terms", type(self.terms).__name__)
        if any(term.method.left.column.unit != self.left.column.unit for term in self.terms):
            _fail("term units matching the linear total", self.left.column.name)

    def _summary(self) -> str:
        return f"kind=linear, column={_short(self.left.column.name)!r}, terms={len(self.terms)}"

    def _lines(self) -> Iterator[str]:
        yield from _method_lines(self)


@dataclass(frozen=True, slots=True, repr=False)
class FrameMethodMetric(_FactRecord):
    """Explain one named total. Args: name, method, share_state.

    Returns a frozen metric; e.g. ``FrameMethodMetric('revenue', rule, 'defined')``.
    The zero-change flag uses the saved exact value, without display rounding.
    """

    name: str
    method: FrameAdditiveMethod | FrameRatioMethod | FrameLinearMethod
    share_state: Literal["defined", "zero_total_change"]

    def __post_init__(self) -> None:
        _text(self.name)
        _method(self.method)
        expected = "zero_total_change" if self.method.change.value == 0 else "defined"
        if type(self.share_state) is not str or self.share_state != expected:
            _fail("share_state matching exact zero/nonzero change", repr(self.share_state))

    def _summary(self) -> str:
        return f"kind=metric, name={_short(self.name)!r}, method={self.method.kind}, share_state={self.share_state}"

    def _lines(self) -> Iterator[str]:
        yield from _method_lines(self.method)


@dataclass(frozen=True, slots=True, repr=False)
class FrameMethodParent(_FactRecord):
    """Explain a complete parent key and ordered metrics. Args: key, metrics.

    Returns a frozen parent; e.g. ``FrameMethodParent((), (metric,))``.
    The owning facts record validates key columns and the original metric directory.
    """

    key: tuple[FrameMethodValue, ...]
    metrics: tuple[FrameMethodMetric, ...]

    def __post_init__(self) -> None:
        if type(self.key) is not tuple or any(type(v) is not FrameMethodValue for v in self.key):
            _fail("immutable complete typed parent keys", type(self.key).__name__)
        if (
            type(self.metrics) is not tuple
            or not self.metrics
            or any(type(m) is not FrameMethodMetric for m in self.metrics)
        ):
            _fail("a nonempty immutable ordered metric tuple", type(self.metrics).__name__)
        if len({m.name for m in self.metrics}) != len(self.metrics):
            _fail("unique parent metric names", "duplicate metric")
        roots: dict[str, FrameMethodSupport] = {}
        for metric in self.metrics:
            for support in _method_supports(metric.method):
                if support.root_id in roots and roots[support.root_id] != support:
                    _fail("consistent facts for each shared local sample root", support.root_id)
                roots[support.root_id] = support

    def __repr__(self) -> str:
        key = ", ".join(_cell(value.value) for value in self.key[:2])
        if len(self.key) > 2:
            key += ", ..."
        return f"FrameMethodParent(kind=parent, key=({_short(key, 90)}), metrics={len(self.metrics)}; .show())"

    def _summary(self) -> str:
        return f"kind=parent, key_columns={len(self.key)}, metrics={len(self.metrics)}"

    def _lines(self) -> Iterator[str]:
        yield "key: " + " | ".join(f"{_short(v.column.name)}={_cell(v.value)}" for v in self.key)
        for metric in self.metrics:
            yield repr(metric)
            yield from metric._lines()


def _arrow_table(schema: DataSchema, rows: tuple[tuple[_MethodScalar, ...], ...]) -> pd.DataFrame:
    result: dict[str, pd.Series] = {}
    for i, column in enumerate(schema.columns):
        dtype = column.dtype
        if isinstance(dtype, ScalarDataType):
            physical = {
                "boolean": pa.bool_(),
                "string": pa.string(),
                "int64": pa.int64(),
                "float64": pa.float64(),
                "date": pa.date32(),
            }[dtype.name]
        elif isinstance(dtype, DecimalDataType):
            physical = pa.decimal128(dtype.precision, dtype.scale)
        elif isinstance(dtype, TimestampDataType):
            physical = pa.timestamp(dtype.unit, dtype.timezone)
        else:
            physical = pa.duration(dtype.unit)
        values = tuple(row[i] for row in rows)
        array = pa.array(
            values,
            type=pa.int64()
            if isinstance(dtype, (TimestampDataType, DurationDataType))
            else physical,
        )
        result[column.slot_id] = pd.Series(pd.arrays.ArrowExtensionArray(array.cast(physical)))
    return pd.DataFrame(result, index=pd.RangeIndex(len(rows)))


def _coordinates(
    schema: DataSchema,
    coordinates: tuple[FrameMethodCoordinate, ...],
    parents: tuple[FrameMethodParent, ...],
    parent_axis_count: int,
) -> None:
    expected = dict(coordinate_attachment_schemas(schema))
    names = tuple(c.name for c in coordinates)
    if names != tuple(sorted(expected)):
        _fail("exact required coordinate snapshots in canonical name order", repr(names))
    if any(c.schema != expected[c.name] for c in coordinates):
        _fail("exact required coordinate snapshot schemas", repr(names))
    tables = {
        c.name: FrozenAttachment(c.name, c.schema, _arrow_table(c.schema, c.rows))
        for c in coordinates
    }

    def directory(
        axis: CalendarDataAxis | IntervalDataAxis,
    ) -> FrozenCalendarDirectory | FrozenIntervalDirectory:
        assert axis.frozen is not None
        table = tables[axis.frozen.directory_name]
        return (
            read_interval_directory(axis, table.schema, table.rows)
            if isinstance(axis, IntervalDataAxis)
            else read_calendar_directory(axis, table.schema, table.rows)
        )

    for index, axis in enumerate(schema.axes):
        if isinstance(axis, PairedDataAxis):
            table = tables[axis.mapping_name]
            validate_paired_mapping(
                axis, table.schema, table.rows, directory(axis.left), directory(axis.right)
            )
            domain = set(frozen_key_rows(table.rows, axis.columns))
        elif isinstance(axis, (CalendarDataAxis, IntervalDataAxis)):
            domain = {bucket.key for bucket in directory(axis).buckets}
        else:
            continue
        if index < parent_axis_count:
            start = sum(len(a.columns) for a in schema.axes[:index])
            values = tuple(
                tuple(v.value for v in parent.key[start : start + len(axis.columns)])
                for parent in parents
            )
            keys = frozen_key_rows(_arrow_table(DataSchema((axis,), ()), values), axis.columns)
            if any(key not in domain for key in keys):
                _fail("parent keys resolving in their original coordinate snapshot", axis.token)


def _key_order(parent: FrameMethodParent) -> tuple[tuple[bool, _MethodScalar], ...]:
    # Every key position has one fixed physical type. Nulls sort last, as Frame rows do.
    return tuple((v.value is None, v.value) for v in parent.key)


@dataclass(frozen=True, slots=True, repr=False)
class FrameMethodFacts(_FactRecord):
    """Own one original decomposition's complete explanatory facts.

    Read an item from ``fixed_frame.method_facts``. The returned frozen record
    contains complete original output schema, coordinates and ordered parents.
    Direct construction is not a supported public entrypoint.
    Constraints: no source authority, current-result certification or numerical reexecution.
    """

    kind: Literal["attribute"]
    origin_result_id: str
    output_schema: DataSchema = field(
        metadata={
            "meaning": "Read facts.output_schema for the complete original saved output layout. Read-only schema metadata; nested axes, comparisons and frozen descriptors remain internal."
        }
    )
    coordinates: tuple[FrameMethodCoordinate, ...]
    parent_axis_count: int
    parents: tuple[FrameMethodParent, ...]

    def __post_init__(self) -> None:
        if type(self.kind) is not str or self.kind != "attribute":
            _fail("kind='attribute'", repr(self.kind))
        _text(self.origin_result_id)
        if type(self.output_schema) is not DataSchema:
            _fail("the exact original output DataSchema", type(self.output_schema).__name__)
        if type(self.parent_axis_count) is not int or not 0 <= self.parent_axis_count <= len(
            self.output_schema.axes
        ):
            _fail(
                "parent_axis_count within the original axis directory", repr(self.parent_axis_count)
            )
        if type(self.parents) is not tuple or any(
            type(p) is not FrameMethodParent for p in self.parents
        ):
            _fail("immutable exact parent records", type(self.parents).__name__)
        if type(self.coordinates) is not tuple or any(
            type(c) is not FrameMethodCoordinate for c in self.coordinates
        ):
            _fail("immutable exact coordinate snapshots", type(self.coordinates).__name__)
        columns = tuple(
            c for axis in self.output_schema.axes[: self.parent_axis_count] for c in axis.columns
        )
        paths = self.output_schema.value_paths
        names = tuple(path[1] for path in paths if len(path) == 2 and path[0] == "left")
        if not names or len(set(names)) != len(names):
            _fail("an original ordered Attribute metric directory", repr(paths))
        expected_paths: list[tuple[str, ...]] = []
        for name in names:
            roles = ("left", "right", "absolute", "contribution", "contribution_share")
            expected_paths.extend((role, name) for role in roles)
            if ("numerator_left", name) in paths:
                expected_paths.extend(
                    (role, name)
                    for role in (
                        "numerator_left",
                        "numerator_right",
                        "denominator_left",
                        "denominator_right",
                    )
                )
        if paths != tuple(expected_paths):
            _fail("the complete ordered contribution output directory", repr(paths))
        directory = dict(zip(paths, self.output_schema.values, strict=True))
        if self.output_schema.presence_paths != (("left_present",), ("right_present",)):
            _fail("the original left_present/right_present directory", "invalid presence paths")
        for name in names:
            left, right, change, contribution, share = (
                directory[(role, name)]
                for role in ("left", "right", "absolute", "contribution", "contribution_share")
            )
            if not left.unit == right.unit == change.unit == contribution.unit:
                _fail("matching original metric, change and contribution units", name)
            if share.unit != (None if change.unit is None else "1"):
                _fail("a dimensionless contribution share unit", name)
            for column in (left, right, change, contribution, share):
                if not (
                    isinstance(column.dtype, (DecimalDataType, DurationDataType))
                    or column.dtype in (ScalarDataType("int64"), ScalarDataType("float64"))
                ):
                    _fail("numeric or Duration contribution columns", column.name)
            if isinstance(share.dtype, DurationDataType):
                _fail("a numeric contribution share type", share.name)
        for parent in self.parents:
            if tuple(v.column for v in parent.key) != columns:
                _fail(
                    "the complete original parent columns in axis order",
                    "parent key columns differ",
                )
            if tuple(metric.name for metric in parent.metrics) != names:
                _fail(
                    "all original metrics in declaration order", "parent metric directory differs"
                )
            for metric in parent.metrics:
                method = metric.method
                for role, value in (
                    ("left", method.left),
                    ("right", method.right),
                    ("absolute", method.change),
                ):
                    if value.column != directory[(role, metric.name)]:
                        _fail("parent totals carrying exact original output columns", metric.name)
                ratio = ("numerator_left", metric.name) in directory
                if ratio != isinstance(method, FrameRatioMethod):
                    _fail("ratio components exactly for a direct ratio method", metric.name)
                if isinstance(method, FrameRatioMethod):
                    for role, value in (
                        ("numerator_left", method.numerator_left),
                        ("numerator_right", method.numerator_right),
                        ("denominator_left", method.denominator_left),
                        ("denominator_right", method.denominator_right),
                    ):
                        if value.column != directory[(role, metric.name)]:
                            _fail("exact original ratio component columns", metric.name)
        if len({parent.key for parent in self.parents}) != len(self.parents):
            _fail("unique complete parent keys", "duplicate parent key")
        if tuple(sorted(self.parents, key=_key_order)) != self.parents:
            _fail("canonical complete parent key order", "unsorted parent keys")
        _coordinates(self.output_schema, self.coordinates, self.parents, self.parent_axis_count)

    def _summary(self) -> str:
        return f"kind=attribute, origin={_short(self.origin_result_id)!r}, parents={len(self.parents)}, coordinates={len(self.coordinates)}, parent_axes={self.parent_axis_count}"

    def _lines(self) -> Iterator[str]:
        for parent in self.parents:
            yield repr(parent)
            yield from parent._lines()
        for coordinate in self.coordinates:
            yield repr(coordinate)
            yield from coordinate._lines()
