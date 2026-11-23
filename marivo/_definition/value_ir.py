"""Immutable expressions over complete data slots, independent of source queries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from marivo._definition.types import LiteralValue


@dataclass(frozen=True, slots=True)
class ValueSlot:
    index: int


@dataclass(frozen=True, slots=True)
class CoordinateSlot:
    index: int


@dataclass(frozen=True, slots=True)
class PresenceSlot:
    path: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ValueLiteral:
    value: LiteralValue


@dataclass(frozen=True, slots=True)
class ValueUnary:
    operation: Literal["neg", "not", "is_null", "abs"]
    operand: ValueExpression


@dataclass(frozen=True, slots=True)
class ValueBinary:
    operation: Literal["add", "sub", "mul", "div", "eq", "ne", "lt", "le", "gt", "ge", "and", "or"]
    left: ValueExpression
    right: ValueExpression


@dataclass(frozen=True, slots=True)
class ValuePartition:
    """A current-input statistic broadcast over complete partition keys."""

    statistic: Statistic | PairStatistic
    positions: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ValueShare:
    """Explicit logical normalization intent; physical preparation uses sum and divide."""

    value: ValueExpression
    positions: tuple[int, ...]


ValueExpression: TypeAlias = (
    ValueSlot
    | CoordinateSlot
    | PresenceSlot
    | ValueLiteral
    | ValueUnary
    | ValueBinary
    | ValuePartition
    | ValueShare
)


@dataclass(frozen=True, slots=True)
class SelectValues:
    values: tuple[tuple[str, ValueExpression], ...]


@dataclass(frozen=True, slots=True)
class FilterValues:
    predicate: ValueExpression


@dataclass(frozen=True, slots=True)
class TopValues:
    n: int
    score: ValueExpression
    descending: bool


@dataclass(frozen=True, slots=True)
class Statistic:
    method: Literal["sum", "mean", "row_count"]
    value: ValueExpression | None = None


@dataclass(frozen=True, slots=True)
class PairStatistic:
    method: Literal["pearson", "spearman", "pair_count"]
    left: ValueExpression
    right: ValueExpression
    location: str


@dataclass(frozen=True, slots=True)
class ReduceValues:
    values: tuple[tuple[str, Statistic | PairStatistic], ...]
    positions: tuple[int, ...]
