"""Frozen time scopes and finite calendar selections."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, GetCoreSchemaHandler, model_validator
from pydantic_core import CoreSchema, core_schema

from marivo._kernel.errors import invalid
from marivo._kernel.grouping import Grouping
from marivo._kernel.render import _DEFAULT_MAX_OUTPUT_BYTES, Card, result_repr
from marivo._kernel.temporal_grid import BoundTimeGrid, TimeShiftAbsent, bind_time_grid, shift_grid
from marivo._kernel.time_types import Grain as Grain
from marivo._kernel.time_types import InstantAnchor as InstantAnchor
from marivo._kernel.time_types import anchor_from_datetime as anchor_from_datetime
from marivo._kernel.time_types import builtin_grain as builtin_grain
from marivo._kernel.time_types import civil_midnight_width_seconds as civil_midnight_width_seconds
from marivo._kernel.time_types import grain_nests as grain_nests
from marivo._kernel.time_types import validate_subday_count as validate_subday_count
from marivo._kernel.timezones import normalize_timezone

_TIME_SCOPE_INTERNAL = ContextVar("marivo_time_scope_internal", default=False)


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


@dataclass(frozen=True, slots=True, repr=False)
class CalendarCorrespondence:
    """One closed calendar mapping rule, without endpoint axes or source state.

    Acquire with mv.ordinal() or mv.offset(years=...)/mv.offset(periods=...).
    Direct kind/by construction and subclass extension are unsupported.
    """

    kind: Literal["ordinal", "years", "periods"]
    by: int = 0

    def __post_init__(self) -> None:
        if self.kind not in ("ordinal", "years", "periods"):
            invalid("calendar correspondence requires ordinal, years or periods")
        if type(self.by) is not int or (self.kind == "ordinal" and self.by != 0):
            invalid("offset requires a non-boolean integer; ordinal has no offset")

    def __repr__(self) -> str:
        suffix = f" by={self.by}" if self.kind != "ordinal" else ""
        return f"<CalendarCorrespondence {self.kind}{suffix}; use .show()>"

    def show(self) -> str:
        """Return the finite rule; endpoint calendars bind at shift/align/compare."""
        return repr(self)


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

    def __init__(self, **data: object) -> None:
        if not _TIME_SCOPE_INTERNAL.get():
            raise TypeError("TimeScope values are returned by session.time_scope(...)")
        super().__init__(**data)

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source: object, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        schema = handler(source)

        def validate(
            value: object,
            validator: core_schema.ValidatorFunctionWrapHandler,
            info: core_schema.ValidationInfo,
        ) -> object:
            if _TIME_SCOPE_INTERNAL.get() or info.field_name is not None:
                with _trusted_time_scope_validation():
                    return validator(value)
            return validator(value)

        return core_schema.with_info_wrap_validator_function(validate, schema)

    @model_validator(mode="before")
    @classmethod
    def _normalize_bounds(cls, data: object) -> object:
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
        Report grain never replaces a Metric's contribution clock. A DATE clock
        cannot supply subday buckets; explicitly choose the source reduction's
        time= field for that business meaning, including an existing hour_prefix
        partition clock. Changing clocks can change membership and totals.
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


def _validate_time_scope_data(data: Mapping[str, object]) -> TimeScope:
    with _trusted_time_scope_validation():
        return TimeScope.model_validate(data)


def _time_scope_from_contract_data(data: Mapping[str, object]) -> TimeScope:
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

    def shift(self, rule: CalendarCorrespondence) -> CalendarAxis:
        """Construct an exact baseline calendar and freeze its direct correspondence.

        Args: rule: An axis-free offset(years=...) or offset(periods=...) rule.
        Returns: A new immutable CalendarAxis with exact selected target buckets.
        Example: ``baseline = periods.shift(mv.offset(years=-1))``.
        Constraints: Builtin unit-step week/month/year; full buckets or bucket-start
        prefixes only. Missing civil periods/cutoffs have no selected target.
        """
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
    return CalendarAxis(scope, bind_grid(scope, grain))


def bind_grid(during: TimeScope, grain: Grain) -> BoundTimeGrid:
    """Freeze a finite grid from a validated TimeScope and Grain."""
    if not isinstance(during, TimeScope) or not isinstance(grain, Grain):  # type: ignore[redundant-expr]  # Preserve validation for untyped inputs.
        raise ValueError(
            "Expected finite half-open time cells with exact timezone and calendar authority; "
            "received grid requires a TimeScope and Grain. "
            "Use an explicit non-empty range in the report timezone."
        )
    return bind_time_grid(during.start, during.end, during.boundary_timezone, grain)
