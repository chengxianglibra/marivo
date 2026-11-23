"""Immutable operations and input nodes of a Frame expression."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, TypeAlias

from marivo._definition.correspondence import CalendarCorrespondence
from marivo._definition.query import FilterValues, ReduceValues, SelectValues, TopValues
from marivo._definition.types import CalendarCoordinate, IntervalCoordinate, LiteralValue
from marivo.analysis.model.data_schema import DataComparison, DataSchema
from marivo.analysis.model.frame_attribute_merge import AttributeMergeValues
from marivo.analysis.model.frame_structure import PendingDataSchema
from marivo.analysis.model.lineage import Origins

if TYPE_CHECKING:
    from marivo.analysis.binding.attribute_game import AttributeGame
    from marivo.analysis.binding.attribute_rules import AttributeRule
    from marivo.analysis.binding.graph import BoundGraph


@dataclass(frozen=True, slots=True)
class FixFrameValues:
    position: int
    key: tuple[LiteralValue, ...]
    origins: Origins = ()


FrameUnaryOperationKind: TypeAlias = (
    SelectValues | FilterValues | TopValues | ReduceValues | FixFrameValues
)


@dataclass(frozen=True, slots=True)
class AttributeGameValues:
    mapping: AttributeFrameValues
    games: tuple[AttributeGame, AttributeGame]
    target: FrameAttributeTarget
    permutations: int
    seed: int


@dataclass(frozen=True, slots=True)
class AlignFrameValues:
    pairs: tuple[tuple[int, int], ...]
    match: CalendarCorrespondence | None = None


@dataclass(frozen=True, slots=True)
class NamedFrameComparison:
    alias: str
    pairs: tuple[tuple[int, int], ...]
    match: CalendarCorrespondence | None = None


@dataclass(frozen=True, slots=True)
class CompareFrameValues:
    baselines: tuple[NamedFrameComparison, ...]
    unnamed: bool = False


@dataclass(frozen=True, slots=True)
class AttributeFrameValues:
    """Endpoint parent positions refer to complete source axis groups."""

    left_parents: tuple[int, ...]
    right_parents: tuple[int, ...]
    how: Literal["exact", "outer"]
    left_projection: str
    right_projection: str


@dataclass(frozen=True, slots=True)
class AttributeInputsValues:
    """Private preparation matrix, never a successful contribution result."""

    mapping: AttributeFrameValues
    source_indices: tuple[int, int] | None = None


@dataclass(frozen=True, slots=True)
class FrameAttributeTarget:
    path: tuple[str, ...]
    mode: Literal["level", "absolute", "relative"]
    observed: int
    comparison: DataComparison | None = None


@dataclass(frozen=True, slots=True)
class AttributeContributionValues:
    """Explicit contribution consumer in the ordinary Frame expression."""

    mapping: AttributeFrameValues
    rules: tuple[tuple[tuple[str, AttributeRule], ...], tuple[tuple[str, AttributeRule], ...]]
    target: FrameAttributeTarget | None = None
    single_endpoint: bool = False
    source_rules: (
        tuple[tuple[tuple[str, AttributeRule], ...], tuple[tuple[str, AttributeRule], ...]] | None
    ) = None
    source_indices: tuple[int, int] | None = None
    source_sides: tuple[int, int] | None = None


@dataclass(frozen=True, slots=True)
class AttributeProjectValues:
    """One side of the selected frozen parent domain, with original keys retained."""

    side: Literal["left", "right"]
    ordinary: tuple[int, ...]
    calendar: int | None
    target_calendar: CalendarCoordinate | IntervalCoordinate | None = None
    how: Literal["exact", "outer"] = "exact"
    comparison_alias: str | None = None
    presence: tuple[str, ...] | None = None


FrameOperationKind: TypeAlias = (
    FrameUnaryOperationKind
    | AlignFrameValues
    | CompareFrameValues
    | AttributeInputsValues
    | AttributeContributionValues
    | AttributeProjectValues
    | AttributeMergeValues
    | AttributeGameValues
)


@dataclass(frozen=True, slots=True)
class SourceFrameInput:
    input_id: str
    graph: BoundGraph


@dataclass(frozen=True, slots=True)
class FixedFrameInput:
    input_id: str
    schema: DataSchema


@dataclass(frozen=True, slots=True)
class FrameOperation:
    inputs: tuple[FrameExpression, ...]
    operation: FrameOperationKind
    logical_schema: DataSchema | PendingDataSchema | None = None


FrameExpression: TypeAlias = SourceFrameInput | FixedFrameInput | FrameOperation
