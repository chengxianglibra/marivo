"""Shared public type aliases for analysis intents."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

SlicePredicateOp = Literal["==", "!=", "in", ">", ">=", "<", "<=", "between"]


class SlicePredicate(TypedDict):
    op: SlicePredicateOp
    value: Any


SliceScalar = str | int | float | bool | None
SliceValue = (
    SliceScalar | list[SliceScalar] | tuple[SliceScalar, ...] | set[SliceScalar] | SlicePredicate
)

__all__ = [
    "SlicePredicate",
    "SlicePredicateOp",
    "SliceScalar",
    "SliceValue",
]
