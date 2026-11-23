"""Closed identities, values, and policies shared by neutral definition graphs."""

from __future__ import annotations

import math
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Final, Literal, TypeAlias, overload

from marivo._kernel.errors import DefinitionGraphError as DefinitionGraphError
from marivo._kernel.errors import invalid as invalid
from marivo._kernel.input_context import InputContext as InputContext
from marivo._kernel.input_context import Owner as Owner
from marivo._kernel.input_context import merge_input_contexts as merge_input_contexts
from marivo._kernel.paths import PathParameter as PathParameter
from marivo._kernel.paths import RelationshipParameter as RelationshipParameter
from marivo._kernel.paths import RelationshipStepParameter as RelationshipStepParameter
from marivo._kernel.refs import (
    ExactRef as ExactRef,
)
from marivo._kernel.refs import (
    SemanticKind,
)
from marivo._kernel.time_types import Grain, InstantAnchor, grain_token

if TYPE_CHECKING:
    from marivo._kernel.temporal_grid import CivilBounds, TimeShiftEntry

RULE_VERSION: Final = 2
NUMERIC_RULE_VERSION: Final = 3

NullPolicy: TypeAlias = Literal["ignore", "error"]

FiniteNumber: TypeAlias = int | float | Decimal
LiteralValue: TypeAlias = str | int | float | Decimal | bool | date | datetime | timedelta | None
# One literal coordinate: a field value (or None), or a complete primary-key tuple on an
# identity axis.
CoordinateValue: TypeAlias = LiteralValue | tuple[LiteralValue, ...]


def check_finite_number(value: FiniteNumber, *, role: str) -> FiniteNumber:
    """Return ``value`` when it is a finite non-boolean number."""
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        invalid(
            f"{role} expected a finite int, float, or Decimal; received "
            f"{type(value).__name__}. Pass a numeric constant."
        )
    if (isinstance(value, float) and not math.isfinite(value)) or (
        isinstance(value, Decimal) and not value.is_finite()
    ):
        invalid(f"{role} expected a finite number; received {value!r}. Pass a finite constant.")
    return value


def check_literal(value: LiteralValue, *, role: str) -> LiteralValue:
    """Return ``value`` when it is an immutable, encodable literal key or constant."""
    if value is None or isinstance(value, (str, bool, date, timedelta)):
        return value
    if isinstance(value, (int, float, Decimal)):
        check_finite_number(value, role=role)
        return value
    invalid(
        f"{role} expected str, int, float, Decimal, bool, date, datetime, or None; "
        f"received {type(value).__name__}. Pass an immutable literal value."
    )


def _decimal_text(value: Decimal) -> str:
    """Exact coefficient/exponent text that never consults the active decimal context."""
    sign, digits, exponent = value.as_tuple()
    if not isinstance(exponent, int):
        invalid(f"decimal literal expected a finite value; received {value!r}.")
    coefficient = "".join(str(digit) for digit in digits)
    stripped = coefficient.rstrip("0")
    if not stripped:
        return "0"
    exponent += len(coefficient) - len(stripped)
    return f"{'-' if sign else ''}{stripped}E{exponent}"


def literal_token(value: LiteralValue) -> tuple[str, str]:
    """Return a type-tagged canonical token so ``True``, ``1``, and ``1.0`` stay distinct."""
    if value is None:
        return ("none", "")
    if isinstance(value, bool):
        return ("bool", "true" if value else "false")
    if isinstance(value, int):
        return ("int", hex(value))
    if isinstance(value, float):
        return ("float", repr(value))
    if isinstance(value, Decimal):
        return ("decimal", _decimal_text(value))
    if isinstance(value, datetime):
        return ("datetime", value.isoformat())
    if isinstance(value, date):
        return ("date", value.isoformat())
    if isinstance(value, timedelta):
        return (
            "duration",
            str((value.days * 86400 + value.seconds) * 1_000_000 + value.microseconds),
        )
    return ("str", value)


@dataclass(frozen=True, slots=True)
class SourceLocation:
    """Declaration site captured for disclosure; never part of computation identity."""

    path: str
    line: int


@dataclass(frozen=True, slots=True)
class RowAttributeTime:
    """Closed control field on the contribution root, never an executable recipe."""

    field: ExactRef
    root: ExactRef

    def __post_init__(self) -> None:
        if self.field.ref.kind is not SemanticKind.TIME_DIMENSION:
            invalid("Row attribute time requires a named TimeDimension.")
        if self.root.ref.kind is not SemanticKind.ENTITY:
            invalid("Row attribute time requires its declared Entity root.")


AttributeTime: TypeAlias = InstantAnchor | RowAttributeTime


def attribute_time_key(value: AttributeTime | None) -> str:
    """Stable selection identity including the complete control-field binding."""
    if isinstance(value, RowAttributeTime):
        return repr(("row", value.field, value.root))
    return "" if value is None else value.isoformat()


@overload
def fixed_attribute_time(value: AttributeTime) -> InstantAnchor: ...


@overload
def fixed_attribute_time(value: None) -> None: ...


def fixed_attribute_time(value: AttributeTime | None) -> InstantAnchor | None:
    """Require a fixed anchor at consumers outside per-row observe classification."""
    if isinstance(value, RowAttributeTime):
        invalid(
            f"{value.field.ref.key} selects per-row attribute time, available only in observe by/where. "
            "Use session.instant(...) for this operation."
        )
    return value


@dataclass(frozen=True, slots=True)
class AxisBinding:
    """One complete coordinate axis: Entity identity or one of its fields, with its path."""

    entity: ExactRef
    field: ExactRef | None
    path: tuple[ExactRef, ...]
    role: Literal["identity", "field"]
    at: AttributeTime | None = None
    source_at: InstantAnchor | None = dataclass_field(default=None, kw_only=True)
    step_ats: tuple[InstantAnchor | None, ...] = dataclass_field(default=(), kw_only=True)
    path_root: ExactRef | None = dataclass_field(default=None, kw_only=True)
    path_facts: tuple[RelationshipParameter, ...] = dataclass_field(default=(), kw_only=True)
    versioned: bool = dataclass_field(default=False, kw_only=True)
    unit: str | None = dataclass_field(default=None, kw_only=True)

    def as_read(self) -> FieldRead | EntityIdentityRead:
        """Carry the complete explicit path binding into source preparation."""
        if self.field is None:
            return EntityIdentityRead(
                self.entity,
                self.path,
                fixed_attribute_time(self.at),
                source_at=self.source_at,
                step_ats=self.step_ats,
                path_root=self.path_root,
                path_facts=self.path_facts,
            )
        return FieldRead(
            self.field,
            self.entity,
            self.path,
            self.at,
            source_at=self.source_at,
            step_ats=self.step_ats,
            path_root=self.path_root,
            path_facts=self.path_facts,
            versioned=self.versioned,
            unit=self.unit,
        )

    def __post_init__(self) -> None:
        if self.entity.ref.kind is not SemanticKind.ENTITY:
            invalid(
                f"axis binding expected an entity reference; received {self.entity.ref.key}. "
                "Bind the axis to its owning entity."
            )
        if (self.role == "identity") != (self.field is None):
            invalid(
                f"axis binding role {self.role!r} on {self.entity.ref.key} requires "
                f"{'no field' if self.role == 'identity' else 'a field'}; received "
                f"{'none' if self.field is None else self.field.ref.key}. "
                "Use role='identity' without a field or role='field' with one."
            )


@dataclass(frozen=True, slots=True)
class FieldRead:
    """Scalar field usage with its declared owner, directed path and attribute time."""

    field: ExactRef
    entity: ExactRef
    path: tuple[ExactRef, ...] = ()
    at: AttributeTime | None = None
    source_at: InstantAnchor | None = dataclass_field(default=None, kw_only=True)
    step_ats: tuple[InstantAnchor | None, ...] = dataclass_field(default=(), kw_only=True)
    path_root: ExactRef | None = dataclass_field(default=None, kw_only=True)
    path_facts: tuple[RelationshipParameter, ...] = dataclass_field(default=(), kw_only=True)
    versioned: bool = dataclass_field(default=False, kw_only=True)
    unit: str | None = dataclass_field(default=None, kw_only=True)


@dataclass(frozen=True, slots=True)
class TimeInstantFieldRead(FieldRead):
    """A time-comparison operand interpreted as its declared source instant."""


@dataclass(frozen=True, slots=True)
class EntityIdentityRead:
    """Complete declared primary-key coordinates through one scalar path."""

    entity: ExactRef
    path: tuple[ExactRef, ...] = ()
    at: InstantAnchor | None = None
    components: tuple[str, ...] = ()
    source_at: InstantAnchor | None = dataclass_field(default=None, kw_only=True)
    step_ats: tuple[InstantAnchor | None, ...] = dataclass_field(default=(), kw_only=True)
    path_root: ExactRef | None = dataclass_field(default=None, kw_only=True)
    path_facts: tuple[RelationshipParameter, ...] = dataclass_field(default=(), kw_only=True)


def field_read_key(read: FieldRead | EntityIdentityRead) -> str:
    """Identify a physical field usage including its path and attribute anchor."""
    times = (
        attribute_time_key(read.source_at),
        tuple(attribute_time_key(anchor) for anchor in read.step_ats),
        attribute_time_key(read.at),
    )
    if isinstance(read, TimeInstantFieldRead):
        return repr(
            (
                "instant",
                read.field.ref.path,
                tuple(step.ref.path for step in read.path),
                times,
            )
        )
    if isinstance(read, EntityIdentityRead):
        return repr(
            (
                "identity",
                read.entity.ref.path,
                tuple(step.ref.path for step in read.path),
                times,
            )
        )
    if not read.path and read.at is None:
        return read.field.ref.path
    return repr(
        (
            read.field.ref.path,
            tuple(step.ref.path for step in read.path),
            times,
        )
    )


def coordinate_read_keys(read: FieldRead | EntityIdentityRead) -> tuple[str, ...]:
    """Physical key names; identity components stay separate, ordered columns."""
    base = field_read_key(read)
    return (
        tuple(f"{base}:component:{i}" for i in range(len(read.components)))
        if isinstance(read, EntityIdentityRead)
        else (base,)
    )


@dataclass(frozen=True, slots=True)
class IntervalCoordinate:
    """One frozen finite interval coordinate, distinct from calendar periods."""

    identity: str
    start: InstantAnchor
    end: InstantAnchor
    timezone: str
    intervals: tuple[tuple[InstantAnchor, InstantAnchor], ...] | None = dataclass_field(
        default=None, kw_only=True
    )
    report_key: int = dataclass_field(default=0, kw_only=True)

    @property
    def bounds(self) -> tuple[tuple[InstantAnchor, InstantAnchor], ...]:
        return self.intervals if self.intervals is not None else ((self.start, self.end),)


def _merge_time_ranges(
    ranges: tuple[tuple[InstantAnchor, InstantAnchor], ...],
) -> tuple[tuple[InstantAnchor, InstantAnchor], ...]:
    """Union explicit request ranges without interpreting bucket membership."""
    result: list[tuple[InstantAnchor, InstantAnchor]] = []
    for start, end in sorted(ranges):
        if start >= end:
            continue
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], end))
        else:
            result.append((start, end))
    return tuple(result)


@dataclass(frozen=True, slots=True)
class CalendarOrigin:
    """Captured immediate result correspondence, independent of a source directory."""

    coordinate: CalendarCoordinate
    kind: Literal["years", "periods"]
    by: int
    entries: tuple[TimeShiftEntry, ...]


@dataclass(frozen=True, slots=True)
class CalendarCoordinate:
    """Captured selection intent and evaluated native position facts."""

    identity: str
    grain_spec: Grain
    timezone: str
    cells: tuple[tuple[InstantAnchor, InstantAnchor, InstantAnchor, InstantAnchor], ...]
    origin: CalendarOrigin | None = None
    civil_bounds: tuple[CivilBounds, ...] = dataclass_field(kw_only=True)
    ranges: tuple[tuple[InstantAnchor, InstantAnchor], ...] = dataclass_field(
        default=(), kw_only=True
    )
    actual_only: bool = dataclass_field(default=False, kw_only=True)

    def __post_init__(self) -> None:
        if len(self.civil_bounds) != len(self.cells):
            invalid("calendar position facts must cover the exact selected keys")

    @property
    def grain(self) -> str:
        return grain_token(self.grain_spec)


@dataclass(frozen=True, slots=True)
class SumAll:
    """Sum is permitted across every axis."""


@dataclass(frozen=True, slots=True)
class SumAxes:
    """Sum is permitted only across the listed semantic axes."""

    axes: tuple[ExactRef, ...]


@dataclass(frozen=True, slots=True)
class SumAllExcept:
    """Sum is permitted across every axis except the listed fixed semantic axes."""

    axes: tuple[ExactRef, ...]

    def __post_init__(self) -> None:
        if not self.axes:
            invalid(
                "sum permission except expected at least one fixed axis; received none. "
                "Use SumAll when no axis stays fixed."
            )


@dataclass(frozen=True, slots=True)
class SumNone:
    """No sum permission; the value may still be reduced by non-additive reducers."""


SumPermission: TypeAlias = SumAll | SumAxes | SumAllExcept | SumNone


@dataclass(frozen=True, slots=True)
class Authorization:
    """Declaration that explicitly asserted a new unit or sum permission."""

    source: ExactRef


def field_axis(read: FieldRead) -> AxisBinding:
    return AxisBinding(
        read.entity,
        read.field,
        read.path,
        "field",
        read.at,
        source_at=read.source_at,
        step_ats=read.step_ats,
        path_root=read.path_root,
        path_facts=read.path_facts,
        versioned=read.versioned,
        unit=read.unit,
    )


def check_identity_anchor(value: InstantAnchor | None) -> None:
    if value is not None and (not isinstance(value, InstantAnchor) or value.utcoffset() is None):  # type: ignore[redundant-expr]  # Preserve validation for untyped inputs.
        invalid("an identity version selection requires an exact InstantAnchor")
