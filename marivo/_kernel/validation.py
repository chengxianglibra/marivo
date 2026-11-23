"""Validate values entering typed APIs from authored scripts and decoded data."""

from __future__ import annotations

from collections.abc import Sequence
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
