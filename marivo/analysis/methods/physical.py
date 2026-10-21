"""Physical numeric carriers used by the current Query kernels."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias, get_args

from marivo.analysis.methods.errors import reject

QUERY_COMBINE_KERNEL = "query.combine@v1"
ScalarName: TypeAlias = Literal["boolean", "string", "int64", "float64", "date", "timestamp"]


@dataclass(frozen=True, slots=True)
class ScalarType:
    name: ScalarName

    def __post_init__(self) -> None:
        if self.name not in get_args(ScalarName):
            reject(
                "an exact scalar type", str(self.name), "Declare a supported scalar or DecimalType."
            )


@dataclass(frozen=True, slots=True)
class DecimalType:
    precision: int
    scale: int

    def __post_init__(self) -> None:
        if (
            type(self.precision) is not int
            or type(self.scale) is not int
            or not 0 <= self.scale <= self.precision
            or not 1 <= self.precision <= 38
        ):
            reject(
                "positive Decimal precision and scale within precision",
                repr(self),
                "Bind exact decimal metadata.",
            )

    @property
    def name(self) -> str:
        return f"decimal({self.precision},{self.scale})"


@dataclass(frozen=True, slots=True)
class DurationType:
    """Fixed elapsed ticks, never a calendar interval or a floating value."""

    unit: Literal["s", "ms", "us", "ns"]

    def __post_init__(self) -> None:
        if self.unit not in ("s", "ms", "us", "ns"):
            reject(
                "fixed duration unit s/ms/us/ns",
                repr(self.unit),
                "Preserve the exact elapsed tick unit.",
            )

    @property
    def name(self) -> str:
        return f"interval('{self.unit}')"


ValueType: TypeAlias = ScalarType | DecimalType | DurationType
