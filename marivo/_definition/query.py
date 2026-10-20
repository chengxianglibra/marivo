"""Closed result expressions and operations, independent of execution engines."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, TypeAlias

from marivo._definition.correspondence import ComparisonAxis
from marivo._definition.types import (
    CoordinateValue,
    LiteralValue,
    Owner,
)

if TYPE_CHECKING:
    from marivo._definition.relations import PopulationDependency


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
    zero_denominator: Literal["null", "error"] = "null"


ValueExpression: TypeAlias = (
    ValueSlot | CoordinateSlot | PresenceSlot | ValueLiteral | ValueUnary | ValueBinary
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
class ReduceValues:
    values: tuple[tuple[str, Statistic], ...]
    positions: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class LiteralValues:
    columns: tuple[str, ...]
    rows: tuple[tuple[tuple[CoordinateValue, ...], tuple[LiteralValue, ...]], ...]


@dataclass(frozen=True, slots=True)
class PopulationValues:
    definition: PopulationDependency


@dataclass(frozen=True, slots=True)
class KeyImage:
    """Project a checked producer's complete keys onto a prepared input layout."""


@dataclass(frozen=True, slots=True)
class ReadFields:
    positions: tuple[int, ...]


QueryOperation: TypeAlias = (
    SelectValues
    | FilterValues
    | TopValues
    | ReduceValues
    | LiteralValues
    | PopulationValues
    | KeyImage
    | ReadFields
)


@dataclass(frozen=True, slots=True)
class ResultQuery:
    """A result operation over exact, captured inputs from this invocation."""

    operation: LiteralValues
    by: tuple[ComparisonAxis, ...]
    owner: Owner
