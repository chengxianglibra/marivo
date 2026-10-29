"""Immutable logical Frame structure and the shared flat export layout."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, TypeAlias

from marivo.analysis.core.data_schema import (
    CalendarDataAxis,
    DataAxis,
    DataSchema,
    IntervalDataAxis,
    PairedDataAxis,
)
from marivo.analysis.core.data_types import DataColumn, DataType, require_data_text
from marivo.analysis.core.frame_structure import (
    AxisRole,
    PendingDataAxis,
    PendingDataColumn,
    PendingDataSchema,
    ValueKind,
    axis_role,
)
from marivo.analysis.core.model import reject


@dataclass(frozen=True, slots=True, repr=False)
class UnresolvedDataType:
    """Captured value kind whose physical type is unresolved; acquired from pending schema."""

    kind: ValueKind

    def __post_init__(self) -> None:
        if self.kind not in (
            "numeric",
            "boolean",
            "string",
            "date",
            "timestamp",
            "duration",
            "unresolved",
        ):
            _invalid("an explicit pending value kind")

    def __repr__(self) -> str:
        return f"<UnresolvedDataType kind={self.kind}; use .show()>"

    def show(self) -> None:
        """Print the known kind. Args: None. Returns: None.

        Example: ``frame.schema.values['revenue'].dtype.show()``.
        Constraints: Does not execute or infer a type from data.
        """
        print(repr(self))


FrameDataType: TypeAlias = DataType | UnresolvedDataType


def _invalid(received: str) -> None:
    reject(
        "a captured immutable Frame structure",
        received,
        "Read schema from the exact Frame whose fields you need.",
        "frame.schema",
        canonical_id="analysis_frame.schema",
    )


@dataclass(frozen=True, slots=True, repr=False)
class FrameField:
    """One exact-input slot, acquired from a FrameSchema directory or complete axis key."""

    slot_id: str
    name: str
    dtype: FrameDataType
    nullable: bool | None
    unit: str | None

    def __post_init__(self) -> None:
        require_data_text(self.slot_id, "frame.schema.slot_id")
        require_data_text(self.name, "frame.schema.name")
        if self.nullable is not None and type(self.nullable) is not bool:
            _invalid("nullability must be a boolean or explicitly unresolved")
        if self.nullable is None and not isinstance(self.dtype, UnresolvedDataType):
            _invalid("only unresolved pending fields may have unknown nullability")
        if self.unit is not None:
            require_data_text(self.unit, "frame.schema.unit")

    def __repr__(self) -> str:
        return f"<FrameField slot={self.slot_id[:64]!r} name={self.name[:64]!r}; use .show()>"

    def show(self) -> None:
        """Print captured slot facts. Args: None. Returns: None.

        Example: ``frame.schema.values['revenue'].show()``.
        Constraints: Names and slots belong only to this exact input.
        """
        print(repr(self))
        unit = None if self.unit is None else self.unit[:160]
        print(f"dtype={self.dtype!r} nullable={self.nullable} unit={unit!r}")


@dataclass(frozen=True, slots=True, repr=False)
class FrameAxisSchema:
    """A complete logical axis, acquired from FrameSchema.axes; no operation authority."""

    token: str
    name: str
    role: AxisRole
    keys: tuple[FrameField, ...]

    def __post_init__(self) -> None:
        require_data_text(self.token, "frame.schema.axis.token")
        require_data_text(self.name, "frame.schema.axis.name")
        if self.role not in ("field", "identity", "calendar", "interval", "paired"):
            _invalid("an axis role from the closed logical role set")
        if type(self.keys) is not tuple or not self.keys:
            _invalid("the complete immutable axis key")

    def __repr__(self) -> str:
        return f"<FrameAxisSchema token={self.token[:64]!r} role={self.role} keys={len(self.keys)}; use .show()>"

    def show(self) -> None:
        """Print bounded complete-key facts. Args: None. Returns: None.

        Example: ``frame.schema.axes[0].show()``.
        Constraints: At most 20 key fields; read keys for the complete tuple.
        """
        print(repr(self))
        for key in self.keys[:20]:
            print(repr(key))
        if len(self.keys) > 20:
            print(f"{len(self.keys) - 20} keys omitted; read .keys")


@dataclass(frozen=True, slots=True, repr=False)
class FieldExportColumn:
    """A flat value or presence export column, acquired from schema or page layout."""

    name: str
    slot_id: str
    kind: Literal["field"] = field(default="field", init=False)

    def __repr__(self) -> str:
        return (
            f"<FieldExportColumn name={self.name[:64]!r} slot={self.slot_id[:64]!r}; use .show()>"
        )

    def show(self) -> None:
        """Print field export identity. Args: None. Returns: None.

        Example: ``frame.schema.layout[-1].show()``.
        Constraints: Resolve slot_id only within the matching schema input.
        """
        print(repr(self))


@dataclass(frozen=True, slots=True, repr=False)
class CoordinateExportColumn:
    """A flat readable coordinate column, acquired from schema or page layout."""

    name: str
    axis_token: str
    endpoint: Literal["self", "left", "right"]
    part: Literal[
        "key",
        "label",
        "start",
        "end",
        "nominal_start",
        "nominal_end",
    ]
    kind: Literal["coordinate"] = field(default="coordinate", init=False)

    def __repr__(self) -> str:
        return f"<CoordinateExportColumn name={self.name[:32]!r} axis={self.axis_token[:32]!r} endpoint={self.endpoint} part={self.part}; use .show()>"

    def show(self) -> None:
        """Print coordinate export identity. Args: None. Returns: None.

        Example: ``frame.schema.layout[0].show()``.
        Constraints: Display labels are separate from the complete logical key.
        """
        print(repr(self))


FrameExportColumn: TypeAlias = FieldExportColumn | CoordinateExportColumn
LogicalSchema: TypeAlias = DataSchema | PendingDataSchema


def selected_columns(
    schema: LogicalSchema, columns: tuple[str, ...] | None
) -> tuple[DataColumn | PendingDataColumn, ...]:
    """Validate complete field names before any payload read, retaining request order."""
    available = (*schema.values, *schema.presence)
    if columns is None:
        return available
    names = tuple(column.name for column in available)
    if (
        type(columns) is not tuple
        or any(type(name) is not str for name in columns)
        or len(set(columns)) != len(columns)
        or any(name not in names for name in columns)
    ):
        reject(
            "distinct complete value or presence names",
            repr(columns),
            f"Choose columns from {names!r}; use columns=() for coordinates only.",
            "frame.read_page.columns",
            canonical_id="analysis_frame.read_page",
        )
    by_name = {column.name: column for column in available}
    return tuple(by_name[name] for name in columns)


def axis_export_layout(
    axis: DataAxis | PendingDataAxis,
    name: str,
    *,
    token: str | None = None,
    endpoint: Literal["self", "left", "right"] = "self",
    time_details: bool = False,
) -> tuple[CoordinateExportColumn, ...]:
    token = axis.token if token is None else token
    if isinstance(axis, PairedDataAxis):
        return (
            *axis_export_layout(
                axis.left, f"{name}.left", token=token, endpoint="left", time_details=time_details
            ),
            *axis_export_layout(
                axis.right,
                f"{name}.right",
                token=token,
                endpoint="right",
                time_details=time_details,
            ),
        )
    if isinstance(axis, (CalendarDataAxis, IntervalDataAxis)):
        bounds = (
            CoordinateExportColumn(f"{name}.start", token, endpoint, "start"),
            CoordinateExportColumn(f"{name}.end", token, endpoint, "end"),
        )
        if isinstance(axis, CalendarDataAxis) and time_details:
            return (
                *bounds,
                CoordinateExportColumn(f"{name}.label", token, endpoint, "label"),
                CoordinateExportColumn(f"{name}.nominal_start", token, endpoint, "nominal_start"),
                CoordinateExportColumn(f"{name}.nominal_end", token, endpoint, "nominal_end"),
            )
        return bounds
    return (CoordinateExportColumn(name, token, endpoint, "key"),)


def export_layout(
    schema: LogicalSchema,
    axis_names: tuple[str, ...] | None = None,
    columns: tuple[str, ...] | None = None,
    *,
    validate_names: bool = False,
    time_details: bool = False,
    member: Literal["to_pandas", "read_page"] = "to_pandas",
) -> tuple[FrameExportColumn, ...]:
    """Derive the one export layout; logical schema permits default name collisions."""
    fields = selected_columns(schema, columns)
    aliases = (
        tuple(axis.name.rsplit(".", 1)[-1] for axis in schema.axes)
        if axis_names is None
        else axis_names
    )
    layout: tuple[FrameExportColumn, ...] = (
        *(
            column
            for axis, alias in zip(schema.axes, aliases, strict=True)
            for column in axis_export_layout(axis, alias, time_details=time_details)
        ),
        *(FieldExportColumn(column.name, column.slot_id) for column in fields),
    )
    duplicates = tuple(name for name, count in Counter(c.name for c in layout).items() if count > 1)
    if validate_names and duplicates:
        reject(
            "unique flat export column names",
            repr(duplicates),
            "Pass axis_names={'full.axis.name': 'unique_name'} or use an axis token; "
            "rename conflicting values with select().",
            f"frame.{member}",
            canonical_id=f"analysis_frame.{member}",
        )
    return layout


@dataclass(frozen=True, slots=True, repr=False)
class FrameSchema:
    """Captured logical structure, acquired from AnalysisFrame.schema without payload I/O."""

    input_id: str
    _values: tuple[FrameField, ...]
    _presence: tuple[FrameField, ...]
    axes: tuple[FrameAxisSchema, ...]
    layout: tuple[FrameExportColumn, ...]

    def __post_init__(self) -> None:
        require_data_text(self.input_id, "frame.schema.input_id")
        fields = (*self._values, *self._presence, *(key for axis in self.axes for key in axis.keys))
        if len({item.slot_id for item in fields}) != len(fields):
            _invalid("unique slots within this exact Frame input")

    @property
    def values(self) -> Mapping[str, FrameField]:
        """Return complete value names. Args: None. Returns: Read-only field mapping.

        Example: ``frame.schema.values['revenue']``. Constraints: Metadata only.
        """
        return MappingProxyType({item.name: item for item in self._values})

    @property
    def presence(self) -> Mapping[str, FrameField]:
        """Return complete presence names. Args: None. Returns: Read-only field mapping.

        Example: ``frame.schema.presence``. Constraints: Separate from value names.
        """
        return MappingProxyType({item.name: item for item in self._presence})

    @property
    def slots(self) -> Mapping[str, FrameField]:
        """Return every value, presence and axis key by exact slot identity.

        Args: None. Returns: Read-only field mapping.
        Example: ``frame.schema.slots[coordinate.slot_id]``.
        Constraints: slot_id is not a cross-result business identity.
        """
        return MappingProxyType(
            {
                item.slot_id: item
                for item in (
                    *self._values,
                    *self._presence,
                    *(key for axis in self.axes for key in axis.keys),
                )
            }
        )

    def __repr__(self) -> str:
        return f"<FrameSchema input={self.input_id[:64]!r} axes={len(self.axes)} values={len(self._values)} presence={len(self._presence)}; use .show()>"

    def show(self) -> None:
        """Print bounded captured structure. Args: None. Returns: None.

        Example: ``frame.schema.show()``.
        Constraints: At most 20 axes and 20 fields; typed directories remain complete.
        """
        print(repr(self))
        for axis in self.axes[:20]:
            print(repr(axis))
        for item in (*self._values, *self._presence)[:20]:
            print(repr(item))
        omitted = max(len(self.axes) - 20, 0) + max(len(self._values) + len(self._presence) - 20, 0)
        if omitted:
            print(f"{omitted} entries omitted; read typed directories")


def _capture_field(column: DataColumn | PendingDataColumn) -> FrameField:
    dtype = (
        UnresolvedDataType(column.kind) if isinstance(column, PendingDataColumn) else column.dtype
    )
    return FrameField(column.slot_id, column.name, dtype, column.nullable, column.unit)


def capture_schema(input_id: str, schema: LogicalSchema) -> FrameSchema:
    """Copy logical facts only; the result owns no source, Store or internal schema."""
    return FrameSchema(
        input_id,
        tuple(_capture_field(column) for column in schema.values),
        tuple(_capture_field(column) for column in schema.presence),
        tuple(
            FrameAxisSchema(
                axis.token,
                axis.name,
                axis_role(axis),
                tuple(_capture_field(column) for column in axis.columns),
            )
            for axis in schema.axes
        ),
        export_layout(schema),
    )
