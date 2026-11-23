"""Frozen pandas operation programs selected by the Query compiler."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeAlias

from marivo._definition.correspondence import ComparisonAxis
from marivo._definition.query import FilterValues, QueryOperation, ValueBinary
from marivo._definition.relations import PopulationDependency, PopulationProgram
from marivo.analysis.binding.correspondence import ComparisonEndpoint
from marivo.analysis.model.data_types import DataColumn, DataType, DurationDataType, ScalarDataType

if TYPE_CHECKING:
    from marivo.analysis.core.frame_operations import (
        AlignFrameValues,
        CompareFrameValues,
        FrameUnaryOperationKind,
    )
    from marivo.analysis.model.data_schema import DataSchema


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
    """A local query operation over prepared inputs. Frame statistics use FrameProgram."""

    operation: QueryOperation
    columns: tuple[DataColumn, ...]
    keys: tuple[str, ...]
    axes: tuple[ComparisonAxis, ...]
    key_groups: tuple[tuple[str, ...], ...]
    output_groups: tuple[tuple[str, ...], ...]
    population_inputs: tuple[PopulationDependency, ...] = ()
    population_layouts: tuple[tuple[tuple[str, ...], ...], ...] = ()
    input_layout: ComparisonEndpoint | None = None
    output_layout: ComparisonEndpoint | None = None
    duration_operations: tuple[DurationBinary, ...] = ()
    selection_steps: tuple[FilterValues, ...] = ()

    @property
    def value_columns(self) -> tuple[DataColumn, ...]:
        if self.input_layout is not None:
            return self.input_layout.values
        return tuple(column for column in self.columns if column.slot_id not in self.keys)


@dataclass(frozen=True, slots=True)
class FrameProgram:
    """One data-only operation over current concrete directories."""

    operation: FrameUnaryOperationKind
    input_schema: DataSchema
    output_schema: DataSchema
    statistic_types: tuple[tuple[DataType, ...], ...] = ()
    duration_operations: tuple[DurationBinary, ...] = ()
    singleton_partitions: frozenset[tuple[int, ...]] = frozenset()

    @property
    def keys(self) -> tuple[str, ...]:
        return self.input_schema.key_fields

    @property
    def key_groups(self) -> tuple[tuple[str, ...], ...]:
        return tuple(tuple(c.slot_id for c in axis.columns) for axis in self.input_schema.axes)


@dataclass(frozen=True, slots=True)
class FrameCombineProgram:
    """Two explicit ordinary inputs and their fully inferred current output."""

    operation: AlignFrameValues
    left_schema: DataSchema
    right_schema: DataSchema
    output_schema: DataSchema


@dataclass(frozen=True, slots=True)
class FrameCompareProgram:
    """One current input followed by ordered named baseline inputs."""

    operation: CompareFrameValues
    input_schemas: tuple[DataSchema, ...]
    output_schema: DataSchema
