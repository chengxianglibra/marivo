"""Frozen pandas operation programs selected by the Query compiler."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from marivo._definition.correspondence import ComparisonAxis
from marivo._definition.query import QueryOperation, ValueBinary
from marivo._definition.relations import PopulationDependency, QueryKeys
from marivo.analysis.binding.correspondence import (
    BoundCorrespondence,
    BroadcastMapping,
    ComparisonEndpoint,
    CorrespondenceRelation,
)
from marivo.analysis.core.data_types import DataColumn, DataType, DurationDataType, ScalarDataType

if TYPE_CHECKING:
    from marivo.analysis.compiler.observation import FormulaProgram
    from marivo.analysis.compiler.original import DrillSchema
    from marivo.analysis.core.data_schema import DataSchema
    from marivo.analysis.core.frame_expression import (
        FrameBinaryOperationKind,
        FrameUnaryOperationKind,
    )


@dataclass(frozen=True, slots=True)
class QueryParameter:
    definition: PopulationDependency
    query: QueryKeys
    placeholder: str
    columns: tuple[DataColumn, ...]


@dataclass(frozen=True, slots=True)
class SourceParameter:
    stage_id: str
    placeholder: str
    columns: tuple[DataColumn, ...]


@dataclass(frozen=True, slots=True)
class RelationInput:
    relation: CorrespondenceRelation
    query: QueryKeys | None = None


@dataclass(frozen=True, slots=True)
class QueryContext:
    relations: tuple[RelationInput, ...] = ()
    drills: tuple[tuple[str, tuple[DrillSchema, ...]], ...] = ()


EMPTY_QUERY_CONTEXT = QueryContext()


@dataclass(frozen=True, slots=True)
class BroadcastInput:
    definition: QueryKeys
    input_index: int
    reference: ComparisonEndpoint
    target: ComparisonEndpoint
    mapping: BroadcastMapping


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
    comparison: BoundCorrespondence | None = None
    relation_groups: tuple[tuple[str, ...], ...] = ()
    original: FormulaProgram | None = None
    references: tuple[BroadcastInput, ...] = ()
    drills: tuple[DrillSchema, ...] = ()
    duration_operations: tuple[DurationBinary, ...] = ()


@dataclass(frozen=True, slots=True)
class FrameProgram:
    """One data-only operation over current concrete directories."""

    operation: FrameUnaryOperationKind
    input_schema: DataSchema
    output_schema: DataSchema
    statistic_types: tuple[DataType, ...] = ()
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
