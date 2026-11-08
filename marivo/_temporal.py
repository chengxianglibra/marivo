"""Dependency-neutral exact time ranges and natural calendar values."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import core_schema

from marivo._grouping import Grouping
from marivo._timezones import normalize_timezone
from marivo.render import _DEFAULT_MAX_OUTPUT_BYTES

if TYPE_CHECKING:
    from marivo._definition.correspondence import CalendarCorrespondence
    from marivo._definition.types import CalendarCoordinate
    from marivo._temporal_grid import BoundTimeGrid

_GRAIN_UNITS = frozenset({"second", "minute", "hour", "day", "week", "month", "quarter", "year"})
_TIME_SCOPE_INTERNAL = ContextVar("marivo_time_scope_internal", default=False)


class InstantAnchor(datetime):
    """An immutable exact instant returned by Session.instant or scope boundaries.

    This datetime subtype preserves compatibility with existing temporal consumers.
    Construct through Session.instant; analysis constructors always return UTC.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return f"<InstantAnchor {self.isoformat()}; use .show()>"

    def show(self) -> None:
        """Display the exact instant.

        Args: None.
        Returns: None.
        Example: ``session.instant('2026-01-01').show()``.
        Constraints: Does not consult a clock, Session, or source.
        """
        print(self.isoformat())


def anchor_from_datetime(value: datetime) -> InstantAnchor:
    """Preserve an aware datetime as an exact UTC anchor without float conversion."""
    if value.utcoffset() is None:
        raise ValueError("an exact instant requires an aware datetime")
    utc = value.astimezone(timezone.utc)
    return InstantAnchor(
        utc.year,
        utc.month,
        utc.day,
        utc.hour,
        utc.minute,
        utc.second,
        utc.microsecond,
        tzinfo=timezone.utc,
    )


@contextmanager
def _trusted_time_scope_validation() -> Iterator[None]:
    """Allow trusted persistence readers to rehydrate an already sealed scope."""

    token = _TIME_SCOPE_INTERNAL.set(True)
    try:
        yield
    finally:
        _TIME_SCOPE_INTERNAL.reset(token)


def _require_timezone(value: object) -> str:
    if type(value) is not str:
        raise ValueError("boundary_timezone must be an IANA or fixed-offset timezone")
    try:
        return normalize_timezone(value).name
    except (ValueError, KeyError) as error:
        raise ValueError(f"invalid boundary_timezone {value!r}") from error


@dataclass(frozen=True, slots=True, init=False)
class Grain:
    """One immutable natural calendar grain, acquired through mv.grain(...)."""

    unit: str
    count: int

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError("Grain values are returned by marivo.analysis.grain(...)")

    @classmethod
    def __get_pydantic_core_schema__(cls, _source: Any, _handler: Any) -> Any:
        def validate(value: Any, info: Any) -> Grain:
            if isinstance(value, Grain):
                return value
            if not isinstance(value, Mapping):
                raise ValueError("grain values must be returned Grain instances")
            if info.field_name is None:
                raise TypeError("grain values must be returned Grain instances")
            if (
                set(value) != {"schema", "kind", "unit", "count"}
                or value.get("schema") != "grain/v2"
                or value.get("kind") != "builtin"
            ):
                raise ValueError("unsupported Grain payload; obtain a natural Grain")
            return builtin_grain(value["unit"], count=value["count"])

        def serialize(value: Grain) -> dict[str, object]:
            return {
                "schema": "grain/v2",
                "kind": "builtin",
                "unit": value.unit,
                "count": value.count,
            }

        return core_schema.with_info_plain_validator_function(
            validate, serialization=core_schema.plain_serializer_function_ser_schema(serialize)
        )

    @property
    def _is_subday(self) -> bool:
        return self.unit in {"second", "minute", "hour"}

    def _width_seconds(self) -> int:
        widths = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800}
        if self.unit not in widths:
            raise ValueError(
                f"Grain._width_seconds() is undefined for calendar-variable grain {self!r}"
            )
        return self.count * widths[self.unit]

    def _to_token(self) -> str:
        return self.unit if self.count == 1 else f"{self.count}{self.unit}"

    def __repr__(self) -> str:
        return f"Grain({self._to_token()!r})"


def builtin_grain(unit: str, *, count: int = 1) -> Grain:
    """Create the natural calendar value behind mv.grain."""
    if unit not in _GRAIN_UNITS or type(count) is not int or count < 1:
        raise ValueError("Grain requires a supported unit and count >= 1")
    if unit not in {"second", "minute", "hour"} and count != 1:
        raise ValueError(f"calendar grain {unit!r} only supports count == 1")
    if unit in _SUBDAY_UNIT_SECONDS:
        validate_subday_count(unit, count)
    value = object.__new__(Grain)
    object.__setattr__(value, "unit", unit)
    object.__setattr__(value, "count", count)
    return value


def grain_nests(inner: Grain, outer: Grain) -> bool:
    """Return the supported static natural calendar containment relation."""
    widths = {"second": 1, "minute": 60, "hour": 3600}
    if inner.unit in widths:
        return (
            outer.unit not in widths
            or (widths[outer.unit] * outer.count) % (widths[inner.unit] * inner.count) == 0
        )
    return inner == outer or (inner.unit, outer.unit) in {
        ("day", "week"),
        ("day", "month"),
        ("day", "quarter"),
        ("day", "year"),
        ("month", "quarter"),
        ("month", "year"),
        ("quarter", "year"),
    }


_CIVIL_DAY_SECONDS = 86_400
_SUBDAY_UNIT_SECONDS = {"second": 1, "minute": 60, "hour": 3_600}


def validate_subday_count(unit: str, count: int) -> int:
    """Validate the unique supported spelling of a civil subday width."""
    if unit not in _SUBDAY_UNIT_SECONDS or type(count) is not int or count < 1:
        raise ValueError(
            "subday width requires second, minute or hour and a positive integer count"
        )
    width = _SUBDAY_UNIT_SECONDS[unit] * count
    if width >= _CIVIL_DAY_SECONDS:
        raise ValueError(
            "subday width must be less than one nominal day; choose civil day "
            "for calendar partitioning or timedelta for elapsed duration"
        )
    if unit == "second" and count % 60 == 0:
        replacement = f"{count // 3600}hour" if count % 3600 == 0 else f"{count // 60}minute"
        raise ValueError(f"subday width has a duplicate spelling; use {replacement}")
    if unit == "minute" and count % 60 == 0:
        raise ValueError(f"subday width has a duplicate spelling; use {count // 60}hour")
    if _CIVIL_DAY_SECONDS % width:
        raise ValueError("subday width must divide one civil day; choose a supported subdivision")
    return width


def civil_midnight_width_seconds(grain: Grain) -> int | None:
    """Return the validated width for a natural civil subday grain."""
    return validate_subday_count(grain.unit, grain.count) if grain._is_subday else None


class TimeScopeContractV1(BaseModel):
    """Versioned exact scope identity used by captured computations."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, populate_by_name=True, serialize_by_alias=True
    )
    schema_: Literal["time-scope/v1"] = Field(default="time-scope/v1", alias="schema")
    kind: Literal["absolute"] = "absolute"
    start: datetime
    end: datetime
    boundary_timezone: str

    @model_validator(mode="after")
    def _validate_bounds(self) -> TimeScopeContractV1:
        if self.start.utcoffset() is None or self.end.utcoffset() is None or self.start >= self.end:
            raise ValueError("time-scope bounds must be aware and non-empty")
        _require_timezone(self.boundary_timezone)
        return self

    def render(self, *, max_output_bytes: int | None = _DEFAULT_MAX_OUTPUT_BYTES) -> str:
        """Render captured scope facts without source reads."""
        from marivo.render import Card

        return (
            Card(
                identity=f"TimeScopeContractV1 start={self.start.isoformat()} end={self.end.isoformat()}",
                available=(".start", ".end", ".model_dump()", ".show()"),
            )
            .field("bounds", f"[{self.start.isoformat()}, {self.end.isoformat()})")
            .field("boundary_timezone", self.boundary_timezone)
            .render(max_output_bytes=max_output_bytes)
        )

    def show(self, *, max_output_bytes: int | None = _DEFAULT_MAX_OUTPUT_BYTES) -> None:
        """Display captured scope facts without source reads."""
        print(self.render(max_output_bytes=max_output_bytes))

    def __repr__(self) -> str:
        from marivo.render import result_repr

        return result_repr(
            f"TimeScopeContractV1 start={self.start.isoformat()} end={self.end.isoformat()}"
        )

    def __str__(self) -> str:
        return self.render()


class TimeScope(BaseModel):
    """Immutable half-open range with exact anchors and a frozen report timezone.

    Acquire with session.time_scope(...). start is inclusive; end is exclusive.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)
    start: InstantAnchor
    end: InstantAnchor
    boundary_timezone: str

    def __init__(self, **data: Any) -> None:
        if not _TIME_SCOPE_INTERNAL.get():
            raise TypeError("TimeScope values are returned by session.time_scope(...)")
        super().__init__(**data)

    @classmethod
    def __get_pydantic_core_schema__(cls, source: Any, handler: Any) -> Any:
        schema = handler(source)

        def validate(value: Any, validator: Any, info: Any) -> Any:
            if _TIME_SCOPE_INTERNAL.get() or info.field_name is not None:
                with _trusted_time_scope_validation():
                    return validator(value)
            return validator(value)

        return core_schema.with_info_wrap_validator_function(validate, schema)

    @model_validator(mode="before")
    @classmethod
    def _normalize_bounds(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        payload = dict(data)
        if "boundary_timezone" not in payload:
            raise ValueError("TimeScope requires a frozen boundary_timezone")
        for name in ("start", "end"):
            bound = payload.get(name)
            if isinstance(bound, str):
                bound = datetime.fromisoformat(bound.replace("Z", "+00:00"))
            if isinstance(bound, datetime):
                payload[name] = anchor_from_datetime(bound)
        if "boundary_timezone" in payload:
            payload["boundary_timezone"] = _require_timezone(payload["boundary_timezone"])
        return payload

    @model_validator(mode="after")
    def _validate_bounds(self) -> TimeScope:
        if self.start >= self.end:
            raise ValueError("TimeScope requires start < end")
        return self

    def by(self, grain: Grain) -> CalendarAxis:
        """Capture finite calendar selection intent.

        Args: grain: A natural calendar grain from mv.grain(...).
        Returns: An immutable CalendarAxis; evaluation resolves native keys.
        Example: ``scope.by(mv.grain('day'))``.
        Constraints: Uses this scope's frozen report timezone without source reads.
        Defines candidate positions, not result rows; ordinary observe does not fill
        missing buckets. Use an explicit output Population for complete time output.
        """
        return calendar_axis(self, grain)

    def _identity(self) -> tuple[InstantAnchor, InstantAnchor, str]:
        return self.start, self.end, self.boundary_timezone

    def __eq__(self, other: object) -> bool:
        return isinstance(other, TimeScope) and self._identity() == other._identity()

    def __hash__(self) -> int:
        return hash(self._identity())

    def __repr__(self) -> str:
        return f"TimeScope([{self.start.isoformat()}, {self.end.isoformat()}), timezone={self.boundary_timezone}; use .show())"

    def render(self, *, max_output_bytes: int | None = _DEFAULT_MAX_OUTPUT_BYTES) -> str:
        """Return a bounded exact scope summary; use scope.render(). No source reads."""
        text = repr(self)
        if max_output_bytes is not None and len(text.encode()) > max_output_bytes:
            raise ValueError("max_output_bytes is too small; pass None for full output")
        return text

    def show(self, *, max_output_bytes: int | None = _DEFAULT_MAX_OUTPUT_BYTES) -> None:
        """Print the exact scope summary; use scope.show(). Returns None; no source reads."""
        print(self.render(max_output_bytes=max_output_bytes))

    def _contract(self) -> TimeScopeContractV1:
        return TimeScopeContractV1(
            start=self.start, end=self.end, boundary_timezone=self.boundary_timezone
        )

    def _model_dump(
        self, *, mode: Literal["python", "json"] = "python", exclude_none: bool = True
    ) -> dict[str, object]:
        return super().model_dump(mode=mode, exclude_none=exclude_none)


def session_time_scope(
    start: InstantAnchor, end: InstantAnchor, boundary_timezone: str
) -> TimeScope:
    """Capture exact Session bounds without exposing a raw scope constructor."""
    with _trusted_time_scope_validation():
        return TimeScope(start=start, end=end, boundary_timezone=boundary_timezone)


def _validate_time_scope_data(data: Mapping[str, Any]) -> TimeScope:
    with _trusted_time_scope_validation():
        return TimeScope.model_validate(data)


def _time_scope_from_contract_data(data: Mapping[str, Any]) -> TimeScope:
    contract = TimeScopeContractV1.model_validate(data)
    return session_time_scope(
        anchor_from_datetime(contract.start),
        anchor_from_datetime(contract.end),
        contract.boundary_timezone,
    )


@dataclass(frozen=True, slots=True, repr=False)
class CalendarAxis(Grouping):
    """Exact finite calendar selection with captured civil boundary authority.

    Ordinary observe returns actual contribution keys; only explicit by=Population
    retains the complete output domain. Frozen time directories keep selected positions.
    Acquire with scope.by(grain) or axis.shift(rule); raw scope/grid construction
    is not a supported public entrypoint.
    """

    scope: TimeScope | None = dataclass_field(
        metadata={
            "meaning": "Read the already captured TimeScope: scope.by(grain).scope is the original scope; a shifted axis may have None. Reading never rebuilds a range or fills a grid envelope."
        }
    )
    grid: BoundTimeGrid = dataclass_field(
        metadata={
            "meaning": "Read the frozen finite buckets and natural calendar interpretation from calendar_axis.grid. Read-only metadata; no source reads or new construction authority. Nested bucket internals have no additional public entrypoints."
        }
    )

    @property
    def kind(self) -> Literal["calendar"]:
        """Return the calendar grouping kind without source access."""
        return "calendar"

    @property
    def _binding(self) -> CalendarCoordinate:
        from marivo._definition.types import CalendarCoordinate, CalendarOrigin, _merge_time_ranges

        grid = self.grid
        return CalendarCoordinate(
            grid.identity,
            grid.grain_spec,
            grid.boundary_timezone,
            tuple(
                (
                    anchor_from_datetime(c.original_start),
                    anchor_from_datetime(c.original_end),
                    anchor_from_datetime(c.start),
                    anchor_from_datetime(c.end),
                )
                for c in grid.cells
            ),
            CalendarOrigin(
                CalendarAxis(None, grid.origin.grid)._binding,
                grid.origin.kind,
                grid.origin.by,
                grid.origin.entries,
            )
            if grid.origin is not None
            else None,
            civil_bounds=grid.civil_bounds,
            ranges=_merge_time_ranges(
                tuple(
                    (anchor_from_datetime(cell.start), anchor_from_datetime(cell.end))
                    for cell in grid.cells
                )
            ),
        )

    def shift(self, rule: CalendarCorrespondence) -> CalendarAxis:
        """Construct an exact baseline calendar and freeze its direct correspondence.

        Args: rule: An axis-free offset(years=...) or offset(periods=...) rule.
        Returns: A new immutable CalendarAxis with exact selected target buckets.
        Example: ``baseline = periods.shift(mv.offset(years=-1))``.
        Constraints: Builtin unit-step week/month/year; full buckets or bucket-start
        prefixes only. Missing civil periods/cutoffs have no selected target.
        """
        from marivo._definition.correspondence import CalendarCorrespondence
        from marivo._temporal_grid import shift_grid

        if type(rule) is not CalendarCorrespondence or rule.kind == "ordinal":
            raise ValueError(
                f"Expected offset(years=...) or offset(periods=...); received {rule!r}. "
                "Construct an offset rule; ordinal pairs explicit complete inputs."
            )
        try:
            return CalendarAxis(None, shift_grid(self.grid, rule.kind, rule.by))
        except ValueError as error:
            raise ValueError(
                "Expected a unit week/month/year full bucket or bucket-start prefix "
                f"with unique target instants; received {error}. "
                "Use a supported calendar domain and explicit intervals for ambiguous civil times."
            ) from error

    def __repr__(self) -> str:
        return f"<CalendarAxis id={self.grid.identity[:16]} buckets={len(self.grid.cells)}; use .show()>"

    def show(self) -> None:
        """Display a bounded calendar summary.

        Args: None.
        Returns: None.
        Example: ``scope.by(mv.grain('day')).show()``.
        Constraints: Uses frozen metadata only; never reads a source.
        """
        print(
            f"CalendarAxis {self.grid.grain_token}: {len(self.grid.cells)} buckets; timezone={self.grid.boundary_timezone}"
        )
        if self.grid.cells:
            print(
                f"Selected bounds: first={self.grid.cells[0].start.isoformat()}; "
                f"last_end={self.grid.cells[-1].end.isoformat()}; exact selected buckets only"
            )
        if self.grid.origin is not None:
            from marivo._temporal_grid import TimeShiftAbsent

            origin = self.grid.origin
            missing = tuple(entry for entry in origin.entries if isinstance(entry, TimeShiftAbsent))
            print(
                f"Direct offset {origin.kind}={origin.by}: origin_buckets={len(origin.grid.cells)}; "
                f"matched={len(origin.entries) - len(missing)}; "
                f"period_absent={sum(entry.reason == 'period_absent' for entry in missing)}; "
                f"cutoff_absent={sum(entry.reason == 'cutoff_absent' for entry in missing)}"
            )


def calendar_axis(scope: TimeScope, grain: Grain) -> CalendarAxis:
    """Bind a finite natural calendar in the scope's frozen report timezone."""
    if not isinstance(grain, Grain):
        raise ValueError("Expected a Grain; use mv.grain(unit).")
    from marivo._temporal_grid import bind_grid

    return CalendarAxis(scope, bind_grid(scope, grain))
