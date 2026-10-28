"""Concrete data column types shared by compiler slots and terminal output schemas.

These records name physical value types only. They hold no semantic Ref,
receipt, engine handle or table, so the pure compiler can fix stage slots
without depending on storage.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from marivo.analysis.core.model import reject


def require_data_text(value: str, location: str) -> None:
    """Validate single-line data metadata with its owning error location."""
    if type(value) is not str or not value or any(c in value for c in "\n\r\t"):
        reject(
            "nonempty single-line data text", repr(value), "Use exact output metadata.", location
        )


@dataclass(frozen=True, slots=True, repr=False)
class ScalarDataType:
    """Exact scalar type, acquired through a completed Frame schema field."""

    name: Literal["boolean", "string", "int64", "float64", "date"]

    def __post_init__(self) -> None:
        if self.name not in ("boolean", "string", "int64", "float64", "date"):
            reject(
                "a supported scalar output type",
                str(self.name),
                "Keep the exact output type.",
                "data.dtype",
            )

    def __repr__(self) -> str:
        return f"<ScalarDataType name={self.name}; use .show()>"

    def show(self) -> None:
        """Print the captured type. Args: None. Returns: None.

        Example: ``frame.schema.values['revenue'].dtype.show()``.
        Constraints: Metadata only; no conversion or data lookup.
        """
        print(repr(self))


@dataclass(frozen=True, slots=True, repr=False)
class DecimalDataType:
    """Exact Decimal precision and scale, acquired through a Frame schema field."""

    precision: int
    scale: int

    def __post_init__(self) -> None:
        if (
            type(self.precision) is not int
            or type(self.scale) is not int
            or not 0 <= self.scale <= self.precision <= 38
            or self.precision == 0
        ):
            reject(
                "Decimal precision 1..38 and scale 0..precision",
                repr(self),
                "Keep the exact output precision.",
                "data.dtype",
            )

    def __repr__(self) -> str:
        return f"<DecimalDataType precision={self.precision} scale={self.scale}; use .show()>"

    def show(self) -> None:
        """Print precision and scale. Args: None. Returns: None.

        Example: ``frame.schema.values['revenue'].dtype.show()``.
        Constraints: Describes the exact physical carrier without converting it.
        """
        print(repr(self))


@dataclass(frozen=True, slots=True, repr=False)
class TimestampDataType:
    """Exact timestamp unit and optional timezone, acquired through a schema field."""

    unit: Literal["s", "ms", "us", "ns"]
    timezone: str | None

    def __post_init__(self) -> None:
        _time_unit(self.unit)
        if self.timezone is not None:
            require_data_text(self.timezone, "data.timezone")

    def __repr__(self) -> str:
        zone = None if self.timezone is None else self.timezone[:80]
        return f"<TimestampDataType unit={self.unit} timezone={zone!r}; use .show()>"

    def show(self) -> None:
        """Print timestamp facts. Args: None. Returns: None.

        Example: ``frame.schema.axes[0].keys[0].dtype.show()``.
        Constraints: timezone=None retains naive time identity; no conversion occurs.
        """
        print(repr(self))


@dataclass(frozen=True, slots=True, repr=False)
class DurationDataType:
    """Exact duration unit, acquired through a Frame schema field."""

    unit: Literal["s", "ms", "us", "ns"]

    def __post_init__(self) -> None:
        _time_unit(self.unit)

    def __repr__(self) -> str:
        return f"<DurationDataType unit={self.unit}; use .show()>"

    def show(self) -> None:
        """Print the duration unit. Args: None. Returns: None.

        Example: ``frame.schema.values['elapsed'].dtype.show()``.
        Constraints: Captured precision only; no lossy datetime conversion.
        """
        print(repr(self))


def _time_unit(unit: str) -> None:
    if unit not in ("s", "ms", "us", "ns"):
        reject("an exact s/ms/us/ns time unit", unit, "Preserve time precision.", "data.dtype")


DataType: TypeAlias = ScalarDataType | DecimalDataType | TimestampDataType | DurationDataType


@dataclass(frozen=True, slots=True)
class DataColumn:
    slot_id: str
    name: str
    dtype: DataType
    nullable: bool
    unit: str | None = None

    def __post_init__(self) -> None:
        require_data_text(self.slot_id, "data.slot_id")
        require_data_text(self.name, "data.column_name")
        if (
            type(self.dtype)
            not in (ScalarDataType, DecimalDataType, TimestampDataType, DurationDataType)
            or type(self.nullable) is not bool
        ):
            reject(
                "a concrete data type and boolean nullability",
                repr(self),
                "Freeze the final output layout.",
                "data.column",
            )
        if self.unit is not None:
            require_data_text(self.unit, "data.unit")
