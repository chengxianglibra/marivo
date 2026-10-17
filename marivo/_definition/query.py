"""Closed result expressions and operations, independent of execution engines."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, TypeAlias

from marivo._definition.correspondence import ComparisonAxis, Correspondence
from marivo._definition.types import (
    CoordinateValue,
    FieldRead,
    LiteralValue,
    Owner,
)

if TYPE_CHECKING:
    from marivo._definition.relations import PopulationCondition, PopulationDependency, QueryKeys


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
class ValueBroadcast:
    source: QueryKeys


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
    ValueSlot
    | CoordinateSlot
    | PresenceSlot
    | ValueLiteral
    | ValueUnary
    | ValueBinary
    | ValueBroadcast
)


@dataclass(frozen=True, slots=True)
class SelectValues:
    values: tuple[tuple[str, ValueExpression], ...]


@dataclass(frozen=True, slots=True)
class FilterValues:
    predicate: ValueExpression | PopulationCondition
    domains: tuple[PopulationDependency, ...] = ()


@dataclass(frozen=True, slots=True)
class TopValues:
    n: int
    score: ValueExpression
    descending: bool
    partitions: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class Statistic:
    method: Literal["sum", "mean", "row_count"]
    value: ValueExpression | None = None


@dataclass(frozen=True, slots=True)
class ReduceValues:
    values: tuple[tuple[str, Statistic], ...]
    positions: tuple[int, ...]
    calendar_maps: tuple[tuple[int, tuple[int, ...]], ...] = ()


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


@dataclass(frozen=True, slots=True)
class ProjectKeys:
    positions: tuple[int, ...]
    paths: tuple[tuple[str, ...] | None, ...]


@dataclass(frozen=True, slots=True)
class CombineValues:
    operation: Literal["align", "compare"]
    match: Correspondence | None
    how: Literal["exact", "outer"] = "exact"
    time_axis: Literal["paired", "current"] = "paired"
    zero_denominator: Literal["null", "error"] = "null"


@dataclass(frozen=True, slots=True)
class RollupValues:
    positions: tuple[int, ...]
    calendar_maps: tuple[tuple[int, tuple[int, ...]], ...] = ()


@dataclass(frozen=True, slots=True)
class ShareValues:
    value_index: int
    partitions: tuple[int, ...]
    zero_denominator: Literal["null", "error"] = "null"
    calendar_maps: tuple[tuple[int, tuple[int, ...]], ...] = ()


@dataclass(frozen=True, slots=True)
class DrillValues:
    added: tuple[FieldRead, ...]


@dataclass(frozen=True, slots=True)
class ComponentKeys:
    value_slot: str


QueryOperation: TypeAlias = (
    SelectValues
    | FilterValues
    | TopValues
    | ReduceValues
    | LiteralValues
    | PopulationValues
    | KeyImage
    | ReadFields
    | CombineValues
    | RollupValues
    | ShareValues
    | ProjectKeys
    | DrillValues
    | ComponentKeys
)


@dataclass(frozen=True, slots=True)
class ResultQuery:
    """A result operation over exact, captured inputs from this invocation."""

    inputs: tuple[QueryKeys, ...]
    operation: QueryOperation
    by: tuple[ComparisonAxis, ...]
    owner: Owner


def operation_dependencies(operation: QueryOperation) -> tuple[QueryKeys, ...]:
    """Return explicit reference inputs in finite expression order."""
    expressions = (
        tuple(value for _, value in operation.values)
        if isinstance(operation, SelectValues)
        else tuple(
            statistic.value for _, statistic in operation.values if statistic.value is not None
        )
        if isinstance(operation, ReduceValues)
        else (operation.score,)
        if isinstance(operation, TopValues)
        else (operation.predicate,)
        if isinstance(operation, FilterValues)
        and isinstance(
            operation.predicate,
            (
                ValueSlot,
                CoordinateSlot,
                PresenceSlot,
                ValueLiteral,
                ValueUnary,
                ValueBinary,
                ValueBroadcast,
            ),
        )
        else ()
    )

    def visit(value: ValueExpression) -> tuple[QueryKeys, ...]:
        if isinstance(value, ValueBroadcast):
            return (value.source,)
        if isinstance(value, ValueUnary):
            return visit(value.operand)
        if isinstance(value, ValueBinary):
            return (*visit(value.left), *visit(value.right))
        return ()

    return tuple(
        dict.fromkeys(source for expression in expressions for source in visit(expression))
    )
