"""Current Frame directories and explicitly unresolved source schema types."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, TypeAlias
from unicodedata import category

from marivo.analysis.core.artifact_data import (
    CalendarDataAxis,
    DataAxis,
    DataColumn,
    DataSchema,
    PairedDataAxis,
)
from marivo.analysis.core.data_types import DurationDataType, ScalarDataType, TimestampDataType
from marivo.analysis.core.model import reject

ValueKind: TypeAlias = Literal[
    "numeric", "boolean", "string", "date", "timestamp", "duration", "unresolved"
]
AxisRole: TypeAlias = Literal["field", "identity", "calendar", "paired"]
ValuePath: TypeAlias = tuple[str, ...]


def _segment(name: str, location: str) -> None:
    if (
        type(name) is not str
        or not name
        or "." in name
        or "\\" in name
        or any(category(character) == "Cc" for character in name)
    ):
        reject(
            "a nonempty name segment without dots, backslashes or control characters",
            repr(name),
            "Rename the value with one nonempty name segment.",
            location,
        )


def value_name(name: str, *, location: str) -> ValuePath:
    """Validate one user declaration; generated paths use validate_paths."""
    _segment(name, location)
    if name in ("left_present", "right_present"):
        reject(
            "a value name outside the reserved presence names",
            repr(name),
            "Rename the value; left_present and right_present belong to presence.",
            location,
        )
    return (name,)


def validate_paths(paths: tuple[ValuePath, ...], *, location: str) -> None:
    if type(paths) is not tuple:
        reject("immutable ordered name paths", repr(paths), "Freeze the name paths.", location)
    for path in paths:
        if type(path) is not tuple or not path:
            reject("a nonempty immutable path", repr(path), "Preserve each name segment.", location)
        for segment in path:
            _segment(segment, location)
    if len(set(paths)) != len(paths):
        reject("distinct name paths", repr(paths), "Remove the repeated name path.", location)


def axis_role(axis: DataAxis | CalendarDataAxis | PairedDataAxis) -> AxisRole:
    if isinstance(axis, PairedDataAxis):
        return "paired"
    if isinstance(axis, CalendarDataAxis):
        return "calendar"
    if axis.kind == "member":
        return "identity"
    if axis.kind == "correspondence":
        return "paired"
    return "field"


@dataclass(frozen=True, slots=True)
class PendingDataColumn:
    """A captured slot whose physical type is not prepared yet."""

    slot_id: str
    name: str
    kind: ValueKind
    nullable: bool | None
    unit: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.slot_id) is not str
            or not self.slot_id
            or type(self.name) is not str
            or not self.name
            or self.kind
            not in ("numeric", "boolean", "string", "date", "timestamp", "duration", "unresolved")
            or (self.nullable is not None and type(self.nullable) is not bool)
            or (self.unit is not None and (type(self.unit) is not str or not self.unit))
        ):
            reject(
                "a typed pending slot with explicit unresolved physical type",
                repr(self),
                "Capture a slot constraint; prepare a concrete DataColumn before materializing.",
                "frame.pending.column",
            )


@dataclass(frozen=True, slots=True)
class PendingDataAxis:
    """Complete captured key slots without semantic definitions or physical types."""

    token: str
    name: str
    role: AxisRole
    columns: tuple[PendingDataColumn, ...]
    at: datetime | None = None

    def __post_init__(self) -> None:
        if (
            type(self.token) is not str
            or not self.token
            or type(self.name) is not str
            or not self.name
            or self.role not in ("field", "identity", "calendar", "paired")
            or type(self.columns) is not tuple
            or not self.columns
            or any(type(column) is not PendingDataColumn for column in self.columns)
            or (
                self.at is not None
                and (type(self.at) is not datetime or self.at.tzinfo is not timezone.utc)
            )
        ):
            reject(
                "a complete named pending axis",
                repr(self),
                "Capture every component of the current axis key.",
                "frame.pending.axis",
            )
        if self.role == "identity" and any(column.nullable is True for column in self.columns):
            reject(
                "nonnullable complete identity components",
                repr(self.columns),
                "Keep every component of the complete identity nonnullable.",
                "frame.pending.axis",
            )


@dataclass(frozen=True, slots=True)
class PendingDataSchema:
    """A source-independent structure that cannot stand for completed data."""

    axes: tuple[PendingDataAxis, ...]
    values: tuple[PendingDataColumn, ...]
    value_paths: tuple[ValuePath, ...]
    presence: tuple[PendingDataColumn, ...] = ()
    presence_paths: tuple[ValuePath, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.axes) is not tuple
            or any(type(axis) is not PendingDataAxis for axis in self.axes)
            or any(
                type(columns) is not tuple
                or any(type(column) is not PendingDataColumn for column in columns)
                for columns in (self.values, self.presence)
            )
        ):
            reject(
                "immutable pending columns and axes",
                type(self).__name__,
                "Capture the current structure without semantic objects.",
                "frame.pending.schema",
            )
        _directories(self.values, self.value_paths, self.presence, self.presence_paths)
        if any(
            column.kind != "boolean" or column.nullable is not False for column in self.presence
        ):
            reject(
                "nonnullable boolean presence constraints",
                repr(self.presence),
                "Keep presence independent from nullable values.",
                "frame.pending.presence",
            )
        _identities(tuple(axis.token for axis in self.axes), tuple(c.slot_id for c in self.columns))

    @property
    def columns(self) -> tuple[PendingDataColumn, ...]:
        return (*tuple(c for axis in self.axes for c in axis.columns), *self.values, *self.presence)

    def complete(self, schema: DataSchema) -> DataSchema:
        """Validate concrete prepared data against the captured slot constraints."""
        require_complete_structure(schema)
        if (
            schema.value_paths != self.value_paths
            or schema.presence_paths != self.presence_paths
            or tuple(
                (a.token, a.name, a.role, a.at, tuple(c.slot_id for c in a.columns))
                for a in self.axes
            )
            != tuple(
                (a.token, a.name, axis_role(a), a.at, tuple(c.slot_id for c in a.columns))
                for a in schema.axes
            )
            or len(self.columns) != len(schema.columns)
        ):
            reject(
                "the captured complete axes, slots and name directories",
                repr(schema),
                "Preserve the captured current structure during physical preparation.",
                "frame.pending.complete",
            )
        for pending, complete in zip(self.columns, schema.columns, strict=True):
            if (
                (pending.slot_id, pending.name, pending.unit)
                != (complete.slot_id, complete.name, complete.unit)
                or (pending.kind != "unresolved" and pending.kind != column_kind(complete))
                or (pending.nullable is not None and pending.nullable != complete.nullable)
            ):
                reject(
                    "a concrete column satisfying its captured type, unit and nullability",
                    repr(complete),
                    "Prepare a lossless physical column for the captured slot.",
                    "frame.pending.complete",
                )
        return schema


def column_kind(column: DataColumn | PendingDataColumn) -> ValueKind:
    if isinstance(column, PendingDataColumn):
        return column.kind
    dtype = column.dtype
    if isinstance(dtype, ScalarDataType):
        if dtype.name in ("int64", "float64"):
            return "numeric"
        if dtype.name == "boolean":
            return "boolean"
        if dtype.name == "string":
            return "string"
        return "date"
    if isinstance(dtype, TimestampDataType):
        return "timestamp"
    if isinstance(dtype, DurationDataType):
        return "duration"
    return "numeric"


def _identities(tokens: tuple[str, ...], slots: tuple[str, ...]) -> None:
    if len(set(tokens)) != len(tokens) or len(set(slots)) != len(slots):
        reject(
            "distinct axis tokens and complete column slots",
            repr((tokens, slots)),
            "Assign one identity to each axis and column.",
            "frame.structure",
        )


def _directories(
    values: tuple[DataColumn, ...] | tuple[PendingDataColumn, ...],
    value_paths: tuple[ValuePath, ...],
    presence: tuple[DataColumn, ...] | tuple[PendingDataColumn, ...],
    presence_paths: tuple[ValuePath, ...],
) -> None:
    for columns, paths, role in (
        (values, value_paths, "values"),
        (presence, presence_paths, "presence"),
    ):
        validate_paths(paths, location=f"frame.{role}")
        if len(paths) != len(columns) or tuple(".".join(path) for path in paths) != tuple(
            column.name for column in columns
        ):
            reject(
                "one explicit exact ordered path for each current column",
                repr(paths),
                "Supply the current value and presence directories.",
                f"frame.{role}",
            )
    if set(value_paths) & set(presence_paths):
        reject(
            "disjoint current value and presence paths",
            repr((value_paths, presence_paths)),
            "Assign each name to one directory.",
            "frame.structure",
        )


def require_complete_structure(schema: DataSchema) -> None:
    """Reject legacy omitted directories at the private new-Frame boundary."""
    if type(schema) is not DataSchema:
        reject(
            "a concrete DataSchema",
            type(schema).__name__,
            "Finish physical type preparation before binding complete Frame data.",
            "frame.structure",
        )
    _directories(schema.values, schema.value_paths, schema.presence, schema.presence_paths)
    if any(not axis.name for axis in schema.axes):
        reject(
            "an explicit display name for every current axis",
            repr(tuple(axis.name for axis in schema.axes)),
            "Freeze each current axis name.",
            "frame.structure",
        )
    for axis in schema.axes:
        calendars = (
            (axis.left, axis.right)
            if isinstance(axis, PairedDataAxis)
            else ((axis,) if isinstance(axis, CalendarDataAxis) else ())
        )
        if type(axis) is DataAxis and axis.kind == "correspondence":
            reject(
                "a closed paired descriptor with two calendar endpoints",
                axis.token,
                "Retain the explicit paired-coordinate description.",
                "frame.structure",
            )
        if (
            type(axis) is DataAxis
            and axis.kind == "member"
            and any(column.nullable for column in axis.columns)
        ):
            reject(
                "nonnullable complete identity components",
                repr(axis.columns),
                "Keep every component of the complete identity nonnullable.",
                "frame.structure",
            )
        if any(calendar.frozen is None for calendar in calendars):
            reject(
                "a frozen calendar descriptor and finite directory reference",
                axis.token,
                "Freeze the calendar interpretation before materializing.",
                "frame.structure",
            )
    coordinate_attachments(schema)


def coordinate_attachments(schema: DataSchema) -> tuple[str, ...]:
    """Validate the reachable local directory and return its owned attachments.

    Exact calendar descriptors can be shared. Conflicting local tokens or
    attachment owners cannot be resolved by traversal order or name deduplication.
    This validates metadata only; finite attachment rows belong to consumption.
    """
    descriptors: dict[str, DataAxis] = {}
    attachments: dict[str, tuple[Literal["calendar", "paired"], DataAxis]] = {}

    def attachment(name: str, role: Literal["calendar", "paired"], axis: DataAxis) -> None:
        owner = attachments.get(name)
        if owner is not None and owner != (role, axis):
            reject(
                "one role and exact descriptor owner for each coordinate attachment name",
                repr(name),
                "Assign distinct names to calendar directories and paired mappings.",
                "frame.attachments",
            )
        attachments[name] = (role, axis)

    def visit(axis: DataAxis) -> None:
        previous = descriptors.get(axis.token)
        if previous is not None:
            if (
                type(previous) is CalendarDataAxis
                and type(axis) is CalendarDataAxis
                and previous == axis
            ):
                return
            reject(
                "one exact descriptor for each reachable local coordinate token",
                axis.token,
                "Use distinct tokens, or reuse the exact same calendar descriptor.",
                "frame.structure",
            )
        descriptors[axis.token] = axis
        if isinstance(axis, CalendarDataAxis):
            if axis.frozen is not None:
                attachment(axis.frozen.directory_name, "calendar", axis)
        elif isinstance(axis, PairedDataAxis):
            visit(axis.left)
            visit(axis.right)
            attachment(axis.mapping_name, "paired", axis)

    for axis in schema.axes:
        visit(axis)
    return tuple(attachments)
