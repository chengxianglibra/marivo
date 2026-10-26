"""Closed identities, values, and policies shared by neutral definition graphs."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Final, Literal, NoReturn, TypeAlias

from marivo._temporal import Grain, InstantAnchor, PeriodCalendarSnapshotV2
from marivo.refs import Ref, SemanticKind, SemanticKindTag

if TYPE_CHECKING:
    from marivo._temporal_grid import CivilBounds, TimeGridOrigin

DefinitionErrorKind: TypeAlias = Literal[
    "invalid_value",
    "cycle",
    "ownership_conflict",
    "unresolved",
    "unknown_rule_version",
]


class DefinitionGraphError(ValueError):
    """Structured failure raised by the private definition layer.

    Semantic and analysis owners translate it into their public error families
    at their boundaries; ``path`` locates the failing node or value.
    """

    def __init__(
        self,
        message: str,
        *,
        kind: DefinitionErrorKind,
        path: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.kind: DefinitionErrorKind = kind
        self.path = path


def invalid(
    message: str,
    *,
    kind: DefinitionErrorKind = "invalid_value",
    path: tuple[str, ...] = (),
) -> NoReturn:
    raise DefinitionGraphError(message, kind=kind, path=path)


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
class ExactRef:
    """One semantic reference and the definition identity represented by a computation."""

    ref: Ref[SemanticKindTag]
    version: str

    def __post_init__(self) -> None:
        if type(self.version) is not str or not self.version:
            invalid(
                f"exact reference {self.ref.key} expected a non-empty definition version; "
                f"received {self.version!r}. Capture the version from the resolved definition."
            )


@dataclass(frozen=True, slots=True)
class Owner:
    """Existing Session ownership carried by a lazy Population or Query definition."""

    session_id: str
    owner_id: str


@dataclass(frozen=True, slots=True)
class SourceLocation:
    """Declaration site captured for disclosure; never part of computation identity."""

    path: str
    line: int


@dataclass(frozen=True, slots=True)
class AxisBinding:
    """One complete coordinate axis: Entity identity or one of its fields, with its path."""

    entity: ExactRef
    field: ExactRef | None
    path: tuple[ExactRef, ...]
    role: Literal["identity", "field"]
    at: InstantAnchor | None = None

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
    """Scalar field usage with its declared owner, directed path and exact anchor."""

    field: ExactRef
    entity: ExactRef
    path: tuple[ExactRef, ...] = ()
    at: InstantAnchor | None = None


@dataclass(frozen=True, slots=True)
class IdentityRead:
    """Complete declared primary-key coordinates through one scalar path."""

    entity: ExactRef
    path: tuple[ExactRef, ...] = ()
    at: InstantAnchor | None = None
    components: tuple[str, ...] = ()


def field_read_key(read: FieldRead | IdentityRead) -> str:
    """Identify a physical field usage including its path and attribute anchor."""
    if isinstance(read, IdentityRead):
        return repr(
            (
                "identity",
                read.entity.ref.path,
                tuple(step.ref.path for step in read.path),
                read.at.isoformat() if read.at else None,
            )
        )
    if not read.path and read.at is None:
        return read.field.ref.path
    return repr(
        (
            read.field.ref.path,
            tuple(step.ref.path for step in read.path),
            read.at.isoformat() if read.at is not None else None,
        )
    )


def coordinate_read_keys(read: FieldRead | IdentityRead) -> tuple[str, ...]:
    """Physical key names; identity components stay separate, ordered columns."""
    base = field_read_key(read)
    return (
        tuple(f"{base}:component:{i}" for i in range(len(read.components)))
        if isinstance(read, IdentityRead)
        else (base,)
    )


@dataclass(frozen=True, slots=True)
class IntervalCoordinate:
    """One frozen finite interval coordinate, distinct from calendar periods."""

    identity: str
    start: InstantAnchor
    end: InstantAnchor
    timezone: str


@dataclass(frozen=True, slots=True)
class CalendarCoordinate:
    """Frozen output calendar, including nominal and actual half-open buckets."""

    identity: str
    grain_spec: Grain
    timezone: str
    snapshot_digest: str | None
    cells: tuple[tuple[InstantAnchor, InstantAnchor, InstantAnchor, InstantAnchor], ...]
    snapshot: PeriodCalendarSnapshotV2 | None = None
    origin: TimeGridOrigin | None = None
    civil_bounds: tuple[CivilBounds, ...] = field(kw_only=True)

    def __post_init__(self) -> None:
        from marivo._temporal_grid import validate_civil_bounds

        if len(self.civil_bounds) != len(self.cells):
            invalid("calendar civil facts must cover the exact selected cells")
        for civil, bounds in zip(self.civil_bounds, self.cells, strict=True):
            validate_civil_bounds(civil, bounds)

    @property
    def grain(self) -> str:
        return self.grain_spec._to_token()


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
