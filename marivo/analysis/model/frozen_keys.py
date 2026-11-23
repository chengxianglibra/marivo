"""Exact frozen coordinate literals used by data facts and diagnostics."""

from datetime import date
from decimal import Decimal
from typing import Protocol, TypeAlias

from marivo.analysis.model.data_types import DataType

FrozenScalar: TypeAlias = bool | str | int | float | date | Decimal
FrozenKey: TypeAlias = tuple[tuple[DataType, FrozenScalar | None], ...]


class NativeTemporalKey(Protocol):
    """Exact Arrow scalar retained in a current fixed coordinate."""

    def as_py(self) -> object: ...


FrameKeyScalar: TypeAlias = bool | str | int | float | Decimal | NativeTemporalKey | None
_native_key_types: tuple[type[object], ...] = ()


def register_native_key_types(types: tuple[type[object], ...]) -> None:
    """Receive the concrete temporal carrier types from the Arrow boundary."""
    global _native_key_types
    _native_key_types = types


def is_frame_key_scalar(value: FrameKeyScalar) -> bool:
    return value is None or type(value) in (bool, str, int, float, Decimal, *_native_key_types)
