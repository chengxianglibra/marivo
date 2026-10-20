"""Pure closed data schemas, independent of receipts and publication."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from marivo.analysis.core.data_types import DataColumn as DataColumn
from marivo.analysis.core.data_types import ScalarDataType
from marivo.analysis.core.model import reject


def _text(value: str, location: str) -> None:
    if type(value) is not str or not value or any(c in value for c in "\n\r\t"):
        reject(
            "nonempty single-line data text", repr(value), "Use exact output metadata.", location
        )


@dataclass(frozen=True, slots=True)
class DataAxis:
    """A data token and complete key columns, never an Entity or Field Ref."""

    token: str
    columns: tuple[DataColumn, ...]
    kind: Literal["field", "member", "correspondence"] = field(default="field", kw_only=True)
    name: str = field(default="", kw_only=True)
    at: datetime | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        _text(self.token, "data.axis")
        if self.name:
            _text(self.name, "data.axis.name")
        if self.at is not None and (
            type(self.at) is not datetime or self.at.tzinfo is not timezone.utc
        ):
            reject(
                "an inert UTC datetime anchor or no anchor",
                repr(self.at),
                "Freeze the captured instant as a plain UTC datetime.",
                "data.axis.at",
            )
        if (
            type(self.columns) is not tuple
            or not self.columns
            or any(type(c) is not DataColumn for c in self.columns)
            or self.kind not in ("field", "member", "correspondence")
        ):
            reject(
                "complete immutable axis columns",
                repr(self.columns),
                "Preserve the complete output key.",
                "data.axis",
            )


@dataclass(frozen=True, slots=True)
class BuiltinCalendarData:
    """Captured nominal builtin grain; never an executable calendar rule."""

    unit: Literal["second", "minute", "hour", "day", "week", "month", "quarter", "year"]
    step: int

    def __post_init__(self) -> None:
        if (
            self.unit not in ("second", "minute", "hour", "day", "week", "month", "quarter", "year")
            or type(self.step) is not int
            or self.step < 1
        ):
            reject(
                "a captured builtin grain", repr(self), "Freeze the nominal grain.", "data.calendar"
            )


@dataclass(frozen=True, slots=True)
class NamedCalendarData:
    """An inert identifier and digest explaining a saved named calendar."""

    identity: str
    snapshot_digest: str
    level: str

    def __post_init__(self) -> None:
        _text(self.identity, "data.calendar.identity")
        _text(self.snapshot_digest, "data.calendar.snapshot")
        _text(self.level, "data.calendar.level")


@dataclass(frozen=True, slots=True)
class FrozenCalendarData:
    """Small calendar descriptor referencing the required finite directory."""

    definition: BuiltinCalendarData | NamedCalendarData
    directory_name: str
    rule_version: Literal["calendar.v1"] = "calendar.v1"

    def __post_init__(self) -> None:
        if (
            type(self.definition) not in (BuiltinCalendarData, NamedCalendarData)
            or self.rule_version != "calendar.v1"
        ):
            reject(
                "a closed versioned frozen calendar descriptor",
                repr(self),
                "Freeze the builtin or named calendar and its finite directory.",
                "data.calendar",
            )
        _text(self.directory_name, "data.calendar.directory")


@dataclass(frozen=True, slots=True)
class CalendarDataAxis(DataAxis):
    """Saved calendar coordinates retain their timezone without calendar logic."""

    timezone: str
    frozen: FrozenCalendarData | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        DataAxis.__post_init__(self)
        _text(self.timezone, "data.calendar.timezone")
        if self.frozen is not None and type(self.frozen) is not FrozenCalendarData:
            reject(
                "a frozen calendar descriptor",
                repr(self.frozen),
                "Freeze the calendar.",
                "data.calendar",
            )


@dataclass(frozen=True, slots=True)
class PairedDataAxis(DataAxis):
    """A single-level paired key with explicit calendar endpoint descriptors."""

    left: CalendarDataAxis
    right: CalendarDataAxis
    mapping_name: str

    def __post_init__(self) -> None:
        DataAxis.__post_init__(self)
        if type(self.left) is not CalendarDataAxis or type(self.right) is not CalendarDataAxis:
            reject(
                "two nonpaired calendar endpoint descriptors",
                repr((self.left, self.right)),
                "Retain the two complete calendar endpoints.",
                "data.paired",
            )
        _text(self.mapping_name, "data.paired.mapping")
        if len({self.token, self.left.token, self.right.token}) != 3:
            reject(
                "distinct paired and endpoint tokens",
                repr((self.token, self.left.token, self.right.token)),
                "Assign a distinct local token to each coordinate descriptor.",
                "data.paired",
            )


@dataclass(frozen=True, slots=True)
class DataSchema:
    axes: tuple[DataAxis | CalendarDataAxis | PairedDataAxis, ...]
    values: tuple[DataColumn, ...]
    presence: tuple[DataColumn, ...] = ()
    projection_columns: tuple[DataColumn, ...] = ()
    value_paths: tuple[tuple[str, ...], ...] = ()
    presence_paths: tuple[tuple[str, ...], ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.axes) is not tuple
            or any(type(a) not in (DataAxis, CalendarDataAxis, PairedDataAxis) for a in self.axes)
            or any(
                type(columns) is not tuple or any(type(c) is not DataColumn for c in columns)
                for columns in (self.values, self.presence, self.projection_columns)
            )
        ):
            reject(
                "immutable typed output columns and axes",
                type(self).__name__,
                "Freeze the output schema.",
                "data.schema",
            )
        tokens = tuple(axis.token for axis in self.axes)
        slots = tuple(column.slot_id for column in self.columns)
        names = tuple(column.name for column in self.values)
        if (
            len(set(tokens)) != len(tokens)
            or len(set(slots)) != len(slots)
            or len(set(names)) != len(names)
        ):
            reject(
                "distinct axis tokens, slots and value names",
                repr((tokens, slots, names)),
                "Assign one identity to each output column.",
                "data.schema",
            )
        if any(
            column.dtype != ScalarDataType("boolean") or column.nullable for column in self.presence
        ):
            reject(
                "nonnullable boolean presence columns",
                repr(self.presence),
                "Store coordinate presence separately from nullable values.",
                "data.presence",
            )
        from marivo.analysis.core.frame_structure import validate_paths

        for paths, columns, role in (
            (self.value_paths, self.values, "values"),
            (self.presence_paths, self.presence, "presence"),
        ):
            validate_paths(paths, location=f"data.{role}")
            if len(paths) != len(columns) or tuple(".".join(path) for path in paths) != tuple(
                c.name for c in columns
            ):
                reject(
                    "one exact ordered path for each named column",
                    repr(paths),
                    "Preserve the current value and presence directories.",
                    f"data.{role}",
                )
        if set(self.value_paths) & set(self.presence_paths):
            reject(
                "disjoint value and presence paths",
                repr(self.value_paths),
                "Assign each path to one current directory.",
                "data.schema",
            )

    @property
    def columns(self) -> tuple[DataColumn, ...]:
        return (
            *tuple(c for axis in self.axes for c in axis.columns),
            *self.values,
            *self.presence,
            *self.projection_columns,
        )

    @property
    def key_fields(self) -> tuple[str, ...]:
        return tuple(c.slot_id for axis in self.axes for c in axis.columns)
