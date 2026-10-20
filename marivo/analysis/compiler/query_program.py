"""Frozen pandas operation programs selected by the Query compiler."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeAlias

from marivo._definition.correspondence import ComparisonAxis
from marivo._definition.query import PairStatistic, QueryOperation, ReduceValues, ValueBinary
from marivo._definition.relations import PopulationDependency, PopulationProgram
from marivo.analysis.binding.correspondence import ComparisonEndpoint
from marivo.analysis.core.data_types import DataColumn, DataType, DurationDataType, ScalarDataType
from marivo.analysis.core.model import reject

if TYPE_CHECKING:
    from marivo.analysis.core.data_schema import DataSchema
    from marivo.analysis.core.frame_expression import (
        FrameBinaryOperationKind,
        FrameUnaryOperationKind,
    )


@dataclass(frozen=True, slots=True)
class FrameParameter:
    """An inert Frame-key parameter whose producer joins the outer Frame plan."""

    definition: PopulationProgram
    placeholder: str
    columns: tuple[DataColumn, ...]


PopulationParameterPlan: TypeAlias = FrameParameter


@dataclass(frozen=True, slots=True)
class SourceParameter:
    stage_id: str
    placeholder: str
    columns: tuple[DataColumn, ...]


@dataclass(frozen=True, slots=True)
class DurationBinary:
    """Captured operand carriers and selected output for one existing expression."""

    expression: ValueBinary
    left: DataType
    right: DataType
    output: DurationDataType | ScalarDataType


@dataclass(frozen=True, slots=True)
class ResultProgram:
    operation: QueryOperation
    columns: tuple[DataColumn, ...]
    keys: tuple[str, ...]
    axes: tuple[ComparisonAxis, ...]
    key_groups: tuple[tuple[str, ...], ...]
    output_groups: tuple[tuple[str, ...], ...]
    statistic_types: tuple[DataType, ...] = ()
    population_inputs: tuple[PopulationDependency, ...] = ()
    population_layouts: tuple[tuple[tuple[str, ...], ...], ...] = ()
    input_layout: ComparisonEndpoint | None = None
    output_layout: ComparisonEndpoint | None = None
    duration_operations: tuple[DurationBinary, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.operation, ReduceValues):
            for _, statistic in self.operation.values:
                if isinstance(statistic, PairStatistic):
                    reject(
                        "ordinary Frame execution for paired statistics",
                        "a paired statistic in a legacy ResultProgram",
                        "Construct paired statistics through AnalysisFrame.correlate or reduce.",
                        statistic.location,
                    )


@dataclass(frozen=True, slots=True)
class FrameProgram:
    """One data-only operation over current concrete directories."""

    operation: FrameUnaryOperationKind
    input_schema: DataSchema
    output_schema: DataSchema
    statistic_types: tuple[tuple[DataType, ...], ...] = ()
    duration_operations: tuple[DurationBinary, ...] = ()

    @property
    def keys(self) -> tuple[str, ...]:
        return self.input_schema.key_fields

    @property
    def key_groups(self) -> tuple[tuple[str, ...], ...]:
        return tuple(tuple(c.slot_id for c in axis.columns) for axis in self.input_schema.axes)


@dataclass(frozen=True, slots=True)
class FrameCombineProgram:
    """Two explicit ordinary inputs and their fully inferred current output."""

    operation: FrameBinaryOperationKind
    left_schema: DataSchema
    right_schema: DataSchema
    output_schema: DataSchema
