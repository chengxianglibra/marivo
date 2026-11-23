"""Exact immutable witnesses of failed attribution premises."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Literal, TypeAlias

if TYPE_CHECKING:
    from marivo._definition.observation_time import ContributionClockBinding, ObservationPeriod
    from marivo._definition.types import CalendarCoordinate
    from marivo.analysis.model.data_types import DataColumn
    from marivo.analysis.model.frozen_keys import FrozenKey
    from marivo.analysis.model.method_values import FrameMethodValue


@dataclass(frozen=True, slots=True)
class AttributeUseContext:
    endpoint: Literal["left", "right"]
    metric: str
    occurrence: tuple[int, ...]
    logical_occurrence: tuple[int, ...]
    root: str


@dataclass(frozen=True, slots=True)
class AttributeParentFiber:
    parent: FrozenKey
    parent_columns: tuple[DataColumn, ...]


@dataclass(frozen=True, slots=True)
class AttributeFineFiber:
    parent: FrozenKey
    parent_columns: tuple[DataColumn, ...]
    fine: FrozenKey
    fine_columns: tuple[DataColumn, ...]


@dataclass(frozen=True, slots=True)
class AttributeWindowFiber:
    fiber: AttributeParentFiber | AttributeFineFiber
    window_key: int | datetime
    period: ObservationPeriod
    time: ContributionClockBinding
    calendar: CalendarCoordinate


@dataclass(frozen=True, slots=True)
class AttributeCoverageWitness:
    identity: FrozenKey


@dataclass(frozen=True, slots=True)
class AttributePartitionWitness:
    kind: Literal["root", "distinct"]
    identity: FrozenKey
    groups: tuple[FrozenKey, ...]


@dataclass(frozen=True, slots=True)
class AttributeAggregateWitness:
    scope: Literal["parent", "fine"]
    key: FrozenKey
    columns: tuple[DataColumn, ...]


@dataclass(frozen=True, slots=True)
class AttributeCheckWitness:
    check: str
    source: str
    coordinates: tuple[FrozenKey, ...]
    columns: tuple[DataColumn, ...]


@dataclass(frozen=True, slots=True)
class AttributeNumericWitness:
    path: tuple[int, ...]
    operation: Literal["original", "add", "sub", "mul", "div", "denominator", "contribution"]
    values: tuple[FrameMethodValue, ...]


@dataclass(frozen=True, slots=True)
class AttributeClosureWitness:
    target: tuple[str, ...]
    kind: Literal["level", "absolute", "relative"]
    total: int | float | Decimal
    contribution_sum: int | float | Decimal
    residual: int | float | Decimal
    tolerance: int | float | Decimal
    policy: Literal["exact_integer", "duration_ticks", "decimal_rounding", "float64"]
    total_column: DataColumn


AttributeWitness: TypeAlias = (
    AttributeCoverageWitness
    | AttributePartitionWitness
    | AttributeAggregateWitness
    | AttributeCheckWitness
    | AttributeNumericWitness
    | AttributeClosureWitness
)


@dataclass(frozen=True, slots=True)
class AttributeFailureContext:
    uses: tuple[AttributeUseContext, ...]
    fiber: AttributeParentFiber | AttributeFineFiber | AttributeWindowFiber
    witness: AttributeWitness


@dataclass(frozen=True, slots=True)
class AttributeWindowInput:
    """Captured fixed source fiber; actual coverage remains an execution check."""

    window_key: int | datetime
    calendar_slot: str
    period: ObservationPeriod
    time: ContributionClockBinding
    calendar: CalendarCoordinate
