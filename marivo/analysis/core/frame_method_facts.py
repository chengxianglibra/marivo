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
    CalendarComparisonBinding,
    CalendarDataAxis,
    DataSchema,
    IntervalComparisonBinding,
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
    read_comparison_mapping,
    read_interval_directory,
    validate_paired_mapping,
)
from marivo.analysis.core.model import reject
from marivo.semantic.unit_algebra import ratio_unit

_MethodScalar: TypeAlias = bool | int | float | str | date | Decimal | None
_Method: TypeAlias = (
    "FrameAdditiveMethod | FrameRatioMethod | FrameLinearMethod | FrameDivisionMethod"
)


def _fail(
    expected: str,
    received: str,
    location: str = "frame.method_facts",
    *,
    canonical_id: str = "analysis_frame.method_facts",
) -> NoReturn:
    reject(
        expected,
        received,
        "Retain the exact closed facts from the completed Attribute result.",
        location,
        canonical_id=canonical_id,
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
        return f"kind={type(self).__name__}"

    def _lines(self) -> Iterator[str]:
        assert is_dataclass(self)
        for item in fields(self):
            value = getattr(self, item.name)
            if isinstance(value, _FactRecord):
                yield f"{item.name}: {value!r}"
                yield from value._lines()
            elif isinstance(value, tuple):
                for index, child in enumerate(value):
                    yield f"{item.name}[{index}]={_short(repr(child), 160)}"
                    if isinstance(child, _FactRecord):
                        yield from child._lines()
            else:
                shown = (
                    _coefficient_text(value)
                    if type(value) in (int, float, Decimal)
                    else repr(value)
                )
                yield f"{item.name}={_short(shown, 200)}"

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
            _fail(
                "a nonnegative integer preview count",
                repr(n),
                "frame.method_facts.show",
                canonical_id="frame_method_facts.show",
            )
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
    if type(value) not in (
        FrameAdditiveMethod,
        FrameRatioMethod,
        FrameLinearMethod,
        FrameDivisionMethod,
    ):
        _fail("a closed additive, ratio_component or linear method", type(value).__name__)


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
        if isinstance(method, FrameDivisionMethod):
            yield repr(method.divisor)
            yield from method_values(method.method)
        if isinstance(method, FrameLinearMethod):
            for term in method.terms:
                yield repr(term.coefficient)
                yield from method_values(term.method)

    def target_values(method: _Method | _LevelMethod) -> Iterator[str]:
        if isinstance(
            method,
            (
                FrameAdditiveMethod,
                FrameRatioMethod,
                FrameLinearMethod,
                FrameDivisionMethod,
            ),
        ):
            yield from method_values(method)
        else:
            yield repr(method.value.value)
            if isinstance(method, FrameLevelRatioMethod):
                yield repr(method.numerator.value)
                yield repr(method.denominator.value)
            elif isinstance(method, FrameLevelDivisionMethod):
                yield repr(method.divisor)
                yield from target_values(method.method)
            elif isinstance(method, FrameLevelLinearMethod):
                for term in method.terms:
                    yield repr(term.coefficient)
                    yield from target_values(term.method)

    def scalar_values(facts: FrameMethodFacts) -> Iterator[str]:
        for parent in facts.parents:
            for key in parent.key:
                yield repr(key.value)
            for metric in parent.metrics:
                yield repr(metric.observed_total.value)
                yield repr(metric.total.value)
                yield from target_values(metric.method)
        for coordinate in facts.coordinates:
            for row in coordinate.rows:
                for value in row:
                    yield repr(value)

    return left == right and tuple(scalar_values(left)) == tuple(scalar_values(right))


def _method_lines(method: _Method) -> Iterator[str]:
    yield f"{method.kind}: left={_cell(method.left.value)}, right={_cell(method.right.value)}, change={_cell(method.change.value)}"
    if isinstance(method, FrameDivisionMethod):
        yield f"{method.operation} divisor={_coefficient_text(method.divisor)}"
        yield from _method_lines(method.method)
    elif isinstance(method, FrameLinearMethod):
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
    Positive parent denominators and checked components use the versioned symmetric
    within/mix rule and zero-mass entry/exit boundary. Support retains each original
    effective NULL mask; the record grants no source execution authority.
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
    rule_version: Literal["symmetric_rate_mix/v1"] = "symmetric_rate_mix/v1"
    component_policy: Literal["attribute_components/v1"] = "attribute_components/v1"
    boundary_policy: Literal["zero_mass_entry_exit/v1"] = "zero_mass_entry_exit/v1"

    def __post_init__(self) -> None:
        if self.kind != "ratio_component" or type(self.kind) is not str:
            _fail("kind='ratio_component'", repr(self.kind))
        if (self.rule_version, self.component_policy, self.boundary_policy) != (
            "symmetric_rate_mix/v1",
            "attribute_components/v1",
            "zero_mass_entry_exit/v1",
        ):
            _fail("closed versioned rate/mix policies", self.left.column.name)
        _totals(self.left, self.right, self.change)
        _totals(self.numerator_left, self.numerator_right, self.numerator_left)
        _totals(self.denominator_left, self.denominator_right, self.denominator_left)
        for numerator, denominator in (
            (self.numerator_left, self.denominator_left),
            (self.numerator_right, self.denominator_right),
        ):
            if not isinstance(denominator.value, (int, float, Decimal)) or denominator.value <= 0:
                _fail("positive parent ratio denominators", denominator.column.name)
            if (
                _quotient_unit(numerator.column.unit, denominator.column.unit)
                != self.left.column.unit
            ):
                _fail("component units whose ratio matches the metric", self.left.column.name)
        _supports(self.support)

    def _summary(self) -> str:
        return f"kind=ratio_component, column={_short(self.left.column.name)!r}, roots={len(self.support)}"

    def _lines(self) -> Iterator[str]:
        yield f"rule={self.rule_version}; components={self.component_policy}; boundary={self.boundary_policy}"
        yield from _method_lines(self)


def _divisor(value: int | float | Decimal, operation: str) -> None:
    if (
        type(value) not in (int, float, Decimal)
        or (isinstance(value, float) and not isfinite(value))
        or (isinstance(value, Decimal) and not value.is_finite())
        or value == 0
        or (operation == "rescale" and value < 0)
    ):
        _fail("a finite nonzero typed divisor, positive for rescale", repr(value))
    if operation not in ("divide", "rescale"):
        _fail("divide or rescale", operation)


@dataclass(frozen=True, slots=True, repr=False)
class FrameDivisionMethod(_FactRecord):
    """Explain captured division. Args: kind, totals, divisor, operation, method.

    Returns an inert ordered rule retaining the original typed divisor.
    Example: read ``metric.method.divisor`` when kind is ``division``.
    Constraints: divide retains child units; rescale declares the result unit.
    """

    kind: Literal["division"]
    left: FrameMethodValue
    right: FrameMethodValue
    change: FrameMethodValue
    divisor: int | float | Decimal
    operation: Literal["divide", "rescale"]
    method: FrameAdditiveMethod | FrameRatioMethod | FrameLinearMethod | FrameDivisionMethod

    def __post_init__(self) -> None:
        if self.kind != "division":
            _fail("kind='division'", repr(self.kind))
        _totals(self.left, self.right, self.change)
        _divisor(self.divisor, self.operation)
        _method(self.method)
        if self.operation == "divide" and self.method.left.column.unit != self.left.column.unit:
            _fail("division preserving the child unit", self.left.column.name)
        if self.operation == "rescale" and self.left.column.unit is None:
            _fail("an explicit rescale result unit", self.left.column.name)

    def _summary(self) -> str:
        return (
            f"kind=division, column={_short(self.left.column.name)!r}, operation={self.operation}"
        )

    def _lines(self) -> Iterator[str]:
        yield from _method_lines(self)


@dataclass(frozen=True, slots=True, repr=False)
class FrameLinearTerm(_FactRecord):
    """Explain a consumed coefficient and subrule. Args: coefficient, method.

    Returns a frozen term; e.g. ``FrameLinearTerm(-1, rule)``. Coefficients are finite,
    exact int/float/Decimal scalars excluding bool; child order remains unchanged.
    """

    coefficient: int | float | Decimal
    method: FrameAdditiveMethod | FrameRatioMethod | FrameLinearMethod | FrameDivisionMethod

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
class FrameLevelSupport(_FactRecord):
    """Captured support for one endpoint, with no synthetic baseline."""

    root_id: str
    policy: Literal[
        "rows", "non_null_value", "co_valid_value_weight", "identity", "preserved_sample"
    ]
    input_count: int
    effective_count: int

    def __post_init__(self) -> None:
        _text(self.root_id)
        if self.policy not in (
            "rows",
            "non_null_value",
            "co_valid_value_weight",
            "identity",
            "preserved_sample",
        ):
            _fail("a closed sample policy", repr(self.policy))
        if (
            type(self.input_count) is not int
            or type(self.effective_count) is not int
            or not 0 <= self.effective_count <= self.input_count
        ):
            _fail("exact nonnegative endpoint support counts", repr(self.input_count))

    def _summary(self) -> str:
        return f"kind=level_support, root={_short(self.root_id)!r}, policy={self.policy}"


def _level_support(support: tuple[FrameLevelSupport, ...]) -> None:
    if (
        type(support) is not tuple
        or not support
        or any(type(item) is not FrameLevelSupport for item in support)
        or len({item.root_id for item in support}) != len(support)
    ):
        _fail("immutable unique one-endpoint supports", repr(support))


@dataclass(frozen=True, slots=True, repr=False)
class FrameLevelAdditiveMethod(_FactRecord):
    """A one-endpoint additive rule and its original total."""

    kind: Literal["level_additive"]
    value: FrameMethodValue
    support: tuple[FrameLevelSupport, ...]

    def __post_init__(self) -> None:
        if self.kind != "level_additive":
            _fail("kind='level_additive'", repr(self.kind))
        _total(self.value)
        _level_support(self.support)

    def _summary(self) -> str:
        return f"kind=level_additive, column={_short(self.value.column.name)!r}, roots={len(self.support)}"


@dataclass(frozen=True, slots=True, repr=False)
class FrameLevelRatioMethod(_FactRecord):
    """A one-endpoint ratio with independently captured components."""

    kind: Literal["level_ratio_component"]
    value: FrameMethodValue
    numerator: FrameMethodValue
    denominator: FrameMethodValue
    support: tuple[FrameLevelSupport, ...]

    def __post_init__(self) -> None:
        if self.kind != "level_ratio_component":
            _fail("kind='level_ratio_component'", repr(self.kind))
        for value in (self.value, self.numerator, self.denominator):
            _total(value)
        if (
            self.denominator.value == 0
            or _quotient_unit(self.numerator.column.unit, self.denominator.column.unit)
            != self.value.column.unit
        ):
            _fail("nonzero denominator and matching ratio units", self.value.column.name)
        _level_support(self.support)

    def _summary(self) -> str:
        return f"kind=level_ratio_component, column={_short(self.value.column.name)!r}, roots={len(self.support)}"


_LevelMethod: TypeAlias = "FrameLevelAdditiveMethod | FrameLevelRatioMethod | FrameLevelLinearMethod | FrameLevelDivisionMethod"


def _level_method(method: _LevelMethod) -> None:
    if type(method) not in (
        FrameLevelAdditiveMethod,
        FrameLevelRatioMethod,
        FrameLevelLinearMethod,
        FrameLevelDivisionMethod,
    ):
        _fail("a closed one-endpoint rule", type(method).__name__)


@dataclass(frozen=True, slots=True, repr=False)
class FrameLevelDivisionMethod(_FactRecord):
    """Explain one-endpoint division with its original divisor and child method.

    Args: kind, value, divisor, operation, method. Returns immutable inert facts.
    Example: read ``metric.method.divisor`` when kind is ``level_division``.
    Constraints: divide preserves units; rescale retains its declared result unit.
    """

    kind: Literal["level_division"]
    value: FrameMethodValue
    divisor: int | float | Decimal
    operation: Literal["divide", "rescale"]
    method: (
        FrameLevelAdditiveMethod
        | FrameLevelRatioMethod
        | FrameLevelLinearMethod
        | FrameLevelDivisionMethod
    )

    def __post_init__(self) -> None:
        if self.kind != "level_division":
            _fail("kind='level_division'", repr(self.kind))
        _total(self.value)
        _divisor(self.divisor, self.operation)
        _level_method(self.method)
        if self.operation == "divide" and self.method.value.column.unit != self.value.column.unit:
            _fail("division preserving the child unit", self.value.column.name)
        if self.operation == "rescale" and self.value.column.unit is None:
            _fail("an explicit rescale result unit", self.value.column.name)

    def _summary(self) -> str:
        return f"kind=level_division, column={_short(self.value.column.name)!r}, operation={self.operation}"


@dataclass(frozen=True, slots=True, repr=False)
class FrameLevelLinearTerm(_FactRecord):
    """An original ordered coefficient and one-endpoint child rule."""

    coefficient: int | float | Decimal
    method: (
        FrameLevelAdditiveMethod
        | FrameLevelRatioMethod
        | FrameLevelLinearMethod
        | FrameLevelDivisionMethod
    )

    def __post_init__(self) -> None:
        if (
            type(self.coefficient) not in (int, float, Decimal)
            or (isinstance(self.coefficient, float) and not isfinite(self.coefficient))
            or (isinstance(self.coefficient, Decimal) and not self.coefficient.is_finite())
        ):
            _fail("a finite original coefficient", repr(self.coefficient))
        _level_method(self.method)

    def _summary(self) -> str:
        return f"kind=level_linear_term, child={self.method.kind}, column={_short(self.method.value.column.name)!r}"


@dataclass(frozen=True, slots=True, repr=False)
class FrameLevelLinearMethod(_FactRecord):
    """A one-endpoint fixed linear rule retaining child order."""

    kind: Literal["level_linear"]
    value: FrameMethodValue
    terms: tuple[FrameLevelLinearTerm, ...]

    def __post_init__(self) -> None:
        if self.kind != "level_linear":
            _fail("kind='level_linear'", repr(self.kind))
        _total(self.value)
        if (
            type(self.terms) is not tuple
            or not self.terms
            or any(type(term) is not FrameLevelLinearTerm for term in self.terms)
        ):
            _fail("nonempty exact ordered level terms", repr(self.terms))
        if any(term.method.value.column.unit != self.value.column.unit for term in self.terms):
            _fail("linear child units matching parent", self.value.column.name)

    def _summary(self) -> str:
        return (
            f"kind=level_linear, column={_short(self.value.column.name)!r}, terms={len(self.terms)}"
        )


@dataclass(frozen=True, slots=True, repr=False)
class FrameMethodOutput(_FactRecord):
    """Associate one exact original output path with its report role."""

    role: Literal[
        "contribution",
        "total",
        "observed_total",
        "share",
        "value",
        "current",
        "baseline",
        "absolute",
        "numerator",
        "denominator",
        "numerator_current",
        "numerator_baseline",
        "denominator_current",
        "denominator_baseline",
        "valid_count",
        "valid_count_current",
        "valid_count_baseline",
        "present",
        "current_present",
        "baseline_present",
        "effect_within",
        "effect_mix",
        "effect_entry_exit",
        "effect_additive",
    ]
    path: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.role not in (
            "contribution",
            "total",
            "observed_total",
            "share",
            "value",
            "current",
            "baseline",
            "absolute",
            "numerator",
            "denominator",
            "numerator_current",
            "numerator_baseline",
            "denominator_current",
            "denominator_baseline",
            "valid_count",
            "valid_count_current",
            "valid_count_baseline",
            "present",
            "current_present",
            "baseline_present",
            "effect_within",
            "effect_mix",
            "effect_entry_exit",
            "effect_additive",
        ):
            _fail("a closed report output role", repr(self.role))
        if (
            type(self.path) is not tuple
            or not self.path
            or any(type(part) is not str or not part for part in self.path)
        ):
            _fail("a complete nonempty output path", repr(self.path))

    def _summary(self) -> str:
        return f"kind=output, role={self.role}, path={_short(repr(self.path), 60)}"


@dataclass(frozen=True, slots=True, repr=False)
class FrameMethodTarget(_FactRecord):
    """Original target and output directory, retained even for an empty parent set."""

    target: tuple[str, ...]
    kind: Literal["level", "absolute", "relative"]
    outputs: tuple[FrameMethodOutput, ...]

    def __post_init__(self) -> None:
        if (
            type(self.target) is not tuple
            or not self.target
            or any(type(part) is not str or not part for part in self.target)
        ):
            _fail("a complete original target path", repr(self.target))
        if self.kind not in ("level", "absolute", "relative"):
            _fail("a closed target kind", repr(self.kind))
        if type(self.outputs) is not tuple or any(
            type(item) is not FrameMethodOutput for item in self.outputs
        ):
            _fail("immutable closed output mappings", repr(self.target))
        roles = tuple(item.role for item in self.outputs)
        if (
            roles[:4] != ("contribution", "total", "observed_total", "share")
            or len(set(roles)) != len(roles)
            or len({item.path for item in self.outputs}) != len(self.outputs)
        ):
            _fail("four ordered main roles and unique diagnostics", repr(self.target))

    def _summary(self) -> str:
        return (
            f"kind={self.kind}, target={_short(repr(self.target), 60)}, outputs={len(self.outputs)}"
        )


@dataclass(frozen=True, slots=True, repr=False)
class FrameAttributeMetric(_FactRecord):
    """Complete source-free target facts, including observed and reread totals."""

    target: tuple[str, ...]
    kind: Literal["level", "absolute", "relative"]
    method: (
        FrameLevelAdditiveMethod
        | FrameLevelRatioMethod
        | FrameLevelLinearMethod
        | FrameLevelDivisionMethod
        | FrameAdditiveMethod
        | FrameRatioMethod
        | FrameLinearMethod
        | FrameDivisionMethod
    )
    observed_total: FrameMethodValue
    total: FrameMethodValue
    outputs: tuple[FrameMethodOutput, ...]
    share_state: Literal["defined", "zero_total"]

    @property
    def name(self) -> str:
        return ".".join(self.target)

    def __post_init__(self) -> None:
        if (
            type(self.target) is not tuple
            or not self.target
            or any(type(part) is not str or not part for part in self.target)
        ):
            _fail("the complete original target path", repr(self.target))
        if self.kind == "level":
            if not isinstance(
                self.method,
                (
                    FrameLevelAdditiveMethod,
                    FrameLevelRatioMethod,
                    FrameLevelLinearMethod,
                    FrameLevelDivisionMethod,
                ),
            ):
                _fail("a one-endpoint level rule", self.name)
            _level_method(self.method)
        elif self.kind in ("absolute", "relative"):
            if not isinstance(
                self.method,
                (
                    FrameAdditiveMethod,
                    FrameRatioMethod,
                    FrameLinearMethod,
                    FrameDivisionMethod,
                ),
            ):
                _fail("a two-endpoint change rule", self.name)
            _method(self.method)
        else:
            _fail("level, absolute or relative target", repr(self.kind))
        support_by_root: dict[tuple[str, str], tuple[str, int, int]] = {}

        def supports(method: _Method | _LevelMethod) -> Iterator[tuple[str, str, str, int, int]]:
            if isinstance(method, (FrameDivisionMethod, FrameLevelDivisionMethod)):
                yield from supports(method.method)
            elif isinstance(method, (FrameLinearMethod, FrameLevelLinearMethod)):
                for term in method.terms:
                    yield from supports(term.method)
            elif isinstance(method, (FrameLevelAdditiveMethod, FrameLevelRatioMethod)):
                for item in method.support:
                    yield item.root_id, "level", item.policy, item.input_count, item.effective_count
            else:
                for paired_support in method.support:
                    yield (
                        paired_support.root_id,
                        "current",
                        paired_support.policy,
                        paired_support.left_input_count,
                        paired_support.left_effective_count,
                    )
                    yield (
                        paired_support.root_id,
                        "baseline",
                        paired_support.policy,
                        paired_support.right_input_count,
                        paired_support.right_effective_count,
                    )

        for root, endpoint, policy, input_count, effective_count in supports(self.method):
            key = (root, endpoint)
            value = (policy, input_count, effective_count)
            if key in support_by_root and support_by_root[key] != value:
                _fail("consistent support for every shared root and endpoint", root)
            support_by_root[key] = value
        _total(self.total)
        if (
            type(self.observed_total) is not FrameMethodValue
            or self.observed_total.column.dtype != self.total.column.dtype
            or self.observed_total.column.unit != self.total.column.unit
        ):
            _fail(
                "observed total with the exact report carrier and unit",
                f"{self.name}: observed={self.observed_total.column.dtype!r}/{self.observed_total.column.unit!r}, total={self.total.column.dtype!r}/{self.total.column.unit!r}",
            )
        if type(self.outputs) is not tuple or any(
            type(item) is not FrameMethodOutput for item in self.outputs
        ):
            _fail("immutable closed output mappings", self.name)
        roles = tuple(item.role for item in self.outputs)
        if (
            roles[:4] != ("contribution", "total", "observed_total", "share")
            or len(set(roles)) != len(roles)
            or len({item.path for item in self.outputs}) != len(self.outputs)
        ):
            _fail("four ordered main outputs and unique diagnostic mappings", self.name)

        def effect_roles(method: _Method | _LevelMethod) -> set[str]:
            if isinstance(method, FrameRatioMethod):
                return {"effect_within", "effect_mix", "effect_entry_exit"}
            if isinstance(method, FrameDivisionMethod):
                return effect_roles(method.method)
            if isinstance(method, FrameLinearMethod):
                children = tuple(effect_roles(term.method) for term in method.terms)
                combined = set().union(*children)
                if combined and any(not child for child in children):
                    combined.add("effect_additive")
                return combined
            return set()

        expected_effects = effect_roles(self.method)
        actual_effects = {role for role in roles if role.startswith("effect_")}
        if expected_effects != actual_effects:
            _fail("exact effect mappings selected by the closed rule", self.name)
        if self.share_state != ("zero_total" if self.total.value == 0 else "defined"):
            _fail("share state matching the exact reread total", self.name)

    def _summary(self) -> str:
        return f"kind={self.kind}, target={_short(self.name)!r}, share_state={self.share_state}"

    def _lines(self) -> Iterator[str]:
        yield f"observed_total={_cell(self.observed_total.value)}, total={_cell(self.total.value)}"
        yield repr(self.method)
        yield from self.method._lines()


@dataclass(frozen=True, slots=True, repr=False)
class FrameMethodParent(_FactRecord):
    """Explain a complete parent key and ordered metrics. Args: key, metrics.

    Returns a frozen parent; e.g. ``FrameMethodParent((), (metric,))``.
    The owning facts record validates key columns and the original metric directory.
    """

    key: tuple[FrameMethodValue, ...]
    metrics: tuple[FrameAttributeMetric, ...]

    def __post_init__(self) -> None:
        if type(self.key) is not tuple or any(type(v) is not FrameMethodValue for v in self.key):
            _fail("immutable complete typed parent keys", type(self.key).__name__)
        if (
            type(self.metrics) is not tuple
            or not self.metrics
            or any(type(m) is not FrameAttributeMetric for m in self.metrics)
        ):
            _fail("a nonempty immutable ordered metric tuple", type(self.metrics).__name__)
        if len({m.name for m in self.metrics}) != len(self.metrics):
            _fail("unique parent metric names", "duplicate metric")

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
    parent_schema: DataSchema,
) -> None:
    expected = dict(coordinate_attachment_schemas(schema))
    for name, attachment_schema in coordinate_attachment_schemas(parent_schema):
        if name in expected and expected[name] != attachment_schema:
            _fail("consistent original parent/output coordinate schemas", name)
        expected[name] = attachment_schema
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

    for comparison in parent_schema.comparisons:
        binding = comparison.binding
        if isinstance(binding, (CalendarComparisonBinding, IntervalComparisonBinding)):
            current = next(
                axis for axis in parent_schema.axes if axis.token == binding.current_axis_token
            )
            assert isinstance(current, (CalendarDataAxis, IntervalDataAxis))
            table = tables[binding.mapping_name]
            read_comparison_mapping(
                binding, table.schema, table.rows, directory(current), directory(binding.baseline)
            )


def _key_order(parent: FrameMethodParent) -> tuple[tuple[bool, _MethodScalar], ...]:
    # Every key position has one fixed physical type. Nulls sort last, as Frame rows do.
    return tuple((v.value is None, v.value) for v in parent.key)


@dataclass(frozen=True, slots=True, repr=False)
class FrameMethodFacts(_FactRecord):
    """Own one original decomposition's complete explanatory facts.

    Read an item from ``fixed_frame.method_facts``. The returned frozen record
    contains complete original output schema, coordinates and ordered parents.
    Direct construction is not a supported public entrypoint.
    Float64 values remain finite captured facts without arithmetic recomputation.
    Structure, types, units, storage integrity and exact non-floating arithmetic remain checked.
    Constraints: no source authority or current-result certification.
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
    parent_schema: DataSchema = field(
        metadata={
            "meaning": "Read facts.parent_schema for the original receiver layout and frozen endpoint correspondence. This descriptive schema retains no original value table, source declaration or execution authority."
        }
    )
    targets: tuple[FrameMethodTarget, ...]

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
        if (
            type(self.parent_schema) is not DataSchema
            or self.parent_schema.axes != self.output_schema.axes[: self.parent_axis_count]
        ):
            _fail(
                "the exact original parent schema with complete parent axes",
                type(self.parent_schema).__name__,
            )
        if (
            type(self.targets) is not tuple
            or not self.targets
            or any(type(target) is not FrameMethodTarget for target in self.targets)
        ):
            _fail(
                "a nonempty immutable closed original target directory", type(self.targets).__name__
            )
        self._attribute_targets(columns)

    def _attribute_targets(self, columns: tuple[DataColumn, ...]) -> None:
        directory = dict(
            zip(self.output_schema.value_paths, self.output_schema.values, strict=True)
        )
        presence = set(self.output_schema.presence_paths)
        if len({target.target for target in self.targets}) != len(self.targets):
            _fail("unique complete target paths", "duplicate target")
        targets = tuple(target.target for target in self.targets)
        mapping = tuple(target.outputs for target in self.targets)
        mapped_values: list[tuple[str, ...]] = []
        mapped_presence: set[tuple[str, ...]] = set()
        for target in self.targets:
            roles = {output.role for output in target.outputs}
            required = (
                {"value", "present"}
                if target.kind == "level"
                else {"current", "baseline", "absolute", "current_present", "baseline_present"}
            )
            if not required <= roles:
                _fail("complete diagnostics for the target endpoint shape", repr(target.target))
            typed = {}
            for output in target.outputs:
                if output.role in ("present", "current_present", "baseline_present"):
                    if output.path not in presence:
                        _fail("presence roles mapped only to presence columns", repr(output.path))
                    mapped_presence.add(output.path)
                else:
                    column = directory.get(output.path)
                    if column is None:
                        _fail(
                            "numeric report roles mapped only to value columns", repr(output.path)
                        )
                    if not (
                        isinstance(column.dtype, (DecimalDataType, DurationDataType))
                        or column.dtype in (ScalarDataType("int64"), ScalarDataType("float64"))
                    ):
                        _fail("numeric or Duration report values", column.name)
                    if output.role.startswith("valid_count") and (
                        column.dtype != ScalarDataType("int64") or column.unit not in (None, "1")
                    ):
                        _fail("integer dimensionless valid sample counts", column.name)
                    typed[output.role] = column
                    mapped_values.append(output.path)
            total = typed["total"]
            for main_role in (
                "contribution",
                "observed_total",
                *(effect_role for effect_role in typed if effect_role.startswith("effect_")),
            ):
                if typed[main_role].dtype != total.dtype or typed[main_role].unit != total.unit:
                    _fail(
                        "main totals and contributions sharing the exact carrier and unit",
                        repr(target.target),
                    )
            share = typed["share"]
            if isinstance(share.dtype, DurationDataType) or share.unit != (
                None if total.unit is None else "1"
            ):
                _fail("numeric dimensionless share output", share.name)
        if tuple(mapped_values) != self.output_schema.value_paths or mapped_presence != presence:
            _fail(
                "complete ordered value and presence output mapping", "missing or extra output path"
            )
        for parent in self.parents:
            if tuple(value.column for value in parent.key) != columns:
                _fail("complete original parent key columns", "parent key columns differ")
            metrics = parent.metrics
            current = tuple(metric.target for metric in metrics)
            paths = tuple(metric.outputs for metric in metrics)
            if (
                targets != current
                or mapping != paths
                or tuple(metric.kind for metric in metrics)
                != tuple(target.kind for target in self.targets)
            ):
                _fail(
                    "identical ordered target/output directory for all parents",
                    "inconsistent targets",
                )
            for metric in metrics:
                by_role: dict[str, tuple[str, ...]] = {
                    output.role: output.path for output in metric.outputs
                }
                rule = metric.method
                if isinstance(
                    rule,
                    (
                        FrameLevelAdditiveMethod,
                        FrameLevelRatioMethod,
                        FrameLevelLinearMethod,
                        FrameLevelDivisionMethod,
                    ),
                ):
                    expected_columns = {"value": rule.value.column}
                    required_presence: tuple[str, ...] = ("present",)
                    if isinstance(rule, FrameLevelRatioMethod):
                        expected_columns.update(
                            numerator=rule.numerator.column, denominator=rule.denominator.column
                        )
                else:
                    expected_columns = {
                        "current": rule.left.column,
                        "baseline": rule.right.column,
                        "absolute": rule.change.column,
                    }
                    required_presence = ("current_present", "baseline_present")
                    if isinstance(rule, FrameRatioMethod):
                        expected_columns.update(
                            numerator_current=rule.numerator_left.column,
                            numerator_baseline=rule.numerator_right.column,
                            denominator_current=rule.denominator_left.column,
                            denominator_baseline=rule.denominator_right.column,
                        )
                for presence_role in required_presence:
                    if presence_role not in by_role:
                        _fail("all endpoint presence diagnostics", metric.name)
                for diagnostic_role, column in expected_columns.items():
                    diagnostic = directory.get(by_role.get(diagnostic_role, ()))
                    if (
                        diagnostic is None
                        or diagnostic.dtype != column.dtype
                        or diagnostic.unit != column.unit
                        or diagnostic.nullable != column.nullable
                    ):
                        _fail(
                            "diagnostic columns matching exact captured rule carriers and units",
                            diagnostic_role,
                        )
                for output in metric.outputs:
                    if output.path not in directory and output.path not in presence:
                        _fail("output mappings resolving in the saved schema", repr(output.path))
                for total_role, value in (
                    ("total", metric.total),
                    ("observed_total", metric.observed_total),
                ):
                    path = next(
                        output.path for output in metric.outputs if output.role == total_role
                    )
                    if directory.get(path) != value.column:
                        _fail("exact typed report columns for parent totals", repr(path))
        if (
            len({parent.key for parent in self.parents}) != len(self.parents)
            or tuple(sorted(self.parents, key=_key_order)) != self.parents
        ):
            _fail("unique canonically ordered complete parent keys", "invalid parent ordering")
        _coordinates(
            self.output_schema,
            self.coordinates,
            self.parents,
            self.parent_axis_count,
            self.parent_schema,
        )

    def _summary(self) -> str:
        return f"kind=attribute, origin={_short(self.origin_result_id)!r}, parents={len(self.parents)}, coordinates={len(self.coordinates)}, parent_axes={self.parent_axis_count}"

    def _lines(self) -> Iterator[str]:
        for parent in self.parents:
            yield repr(parent)
            yield from parent._lines()
        for coordinate in self.coordinates:
            yield repr(coordinate)
            yield from coordinate._lines()
