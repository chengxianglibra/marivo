"""Validate values entering typed APIs from authored scripts and decoded data."""

from __future__ import annotations

from collections.abc import Sequence
from types import UnionType
from typing import TypeGuard


def nonempty_text(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and bool(value)


def nonblank_text(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and bool(value.strip())


def trimmed_text(value: object) -> TypeGuard[str]:
    return nonempty_text(value) and value == value.strip()


def integer(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def input_sequence(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


def is_instance(value: object, expected: type | UnionType | tuple[type, ...]) -> bool:
    """Check the runtime shape of a value received through a typed boundary."""
    return isinstance(value, expected)


def has_exact_type(value: object, expected: type) -> bool:
    """Check a closed record type without admitting subclasses."""
    return type(value) is expected


def is_pair(value: object) -> bool:
    return isinstance(value, tuple) and len(value) == 2


def is_exact_pair(value: object) -> bool:
    return type(value) is tuple and len(value) == 2
