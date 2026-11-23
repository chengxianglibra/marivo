"""Closed result expressions and operations, independent of execution engines."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from marivo._definition.correspondence import ComparisonAxis
from marivo._definition.expressions import MetricNode
from marivo._definition.population_types import PopulationDependency
from marivo._definition.types import (
    CoordinateValue,
    LiteralValue,
)
from marivo._definition.value_ir import CoordinateSlot as CoordinateSlot
from marivo._definition.value_ir import FilterValues as FilterValues
from marivo._definition.value_ir import PairStatistic as PairStatistic
from marivo._definition.value_ir import PresenceSlot as PresenceSlot
from marivo._definition.value_ir import ReduceValues as ReduceValues
from marivo._definition.value_ir import SelectValues as SelectValues
from marivo._definition.value_ir import Statistic as Statistic
from marivo._definition.value_ir import TopValues as TopValues
from marivo._definition.value_ir import ValueBinary as ValueBinary
from marivo._definition.value_ir import ValueExpression as ValueExpression
from marivo._definition.value_ir import ValueLiteral as ValueLiteral
from marivo._definition.value_ir import ValuePartition as ValuePartition
from marivo._definition.value_ir import ValueShare as ValueShare
from marivo._definition.value_ir import ValueSlot as ValueSlot
from marivo._definition.value_ir import ValueUnary as ValueUnary
from marivo._kernel.input_context import Owner


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


@dataclass(frozen=True, slots=True)
class ObserveQuery:
    """Lazy observation of labelled metrics over output axes."""

    metrics: tuple[tuple[str, MetricNode], ...]
    by: tuple[ComparisonAxis, ...]
    owner: Owner | None
    population: PopulationDependency | None = None
    count_populations: tuple[PopulationDependency, ...] = ()


@dataclass(frozen=True, slots=True)
class ReadQuery:
    """Explicit identity-domain attribute projection over a classified Population."""

    population: PopulationDependency
    positions: tuple[int, ...]
    by: tuple[ComparisonAxis, ...]
    owner: Owner


QueryNode: TypeAlias = ObserveQuery | ReadQuery | ResultQuery
