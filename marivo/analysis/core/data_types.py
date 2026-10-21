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


@dataclass(frozen=True, slots=True)
class ScalarDataType:
    name: Literal["boolean", "string", "int64", "float64", "date"]

    def __post_init__(self) -> None:
        if self.name not in ("boolean", "string", "int64", "float64", "date"):
            reject(
                "a supported scalar output type",
                str(self.name),
                "Keep the exact output type.",
                "data.dtype",
            )


@dataclass(frozen=True, slots=True)
class DecimalDataType:
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


@dataclass(frozen=True, slots=True)
class TimestampDataType:
    unit: Literal["s", "ms", "us", "ns"]
    timezone: str | None

    def __post_init__(self) -> None:
        _time_unit(self.unit)
        if self.timezone is not None:
            require_data_text(self.timezone, "data.timezone")


@dataclass(frozen=True, slots=True)
class DurationDataType:
    unit: Literal["s", "ms", "us", "ns"]

    def __post_init__(self) -> None:
        _time_unit(self.unit)


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
