"""Records shared by Query planning, lowering and stage execution."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import TYPE_CHECKING, Literal, TypeAlias

from marivo._definition.expressions import PredicateExpression
from marivo._definition.observations import ContributionBinding
from marivo._definition.relations import PopulationDependency
from marivo.analysis.binding.programs import FormulaProgram, MemberInput, PopulationInput
from marivo.analysis.compiler.query_program import (
    FrameCombineProgram,
    FrameCompareProgram,
    FrameParameter,
    FrameProgram,
    PopulationParameterPlan,
    ResultProgram,
    SourceParameter,
)
from marivo.analysis.core.attribute import (
    AttributeContributionProgram,
    AttributeFormulaBinding,
    AttributeInputProgram,
    AttributeProjectProgram,
    AttributeSourceParentBinding,
)
from marivo.analysis.core.attribute_game import AttributeGameProgram
from marivo.analysis.model.data_schema import DataSchema
from marivo.analysis.model.data_types import DataColumn, DataType
from marivo.analysis.model.errors import reject
from marivo.analysis.model.frame_attribute_merge import AttributeMergeProgram
from marivo.analysis.model.kernels import QUERY_COMBINE_KERNEL
from marivo.analysis.model.time_authority import SourceTimeAuthority
from marivo.analysis.model.unique_keys import UniqueKeys
from marivo.datasource.spi import DEFAULT_BACKEND_TYPE

if TYPE_CHECKING:
    from marivo._definition.expressions import MetricNode
    from marivo.analysis.binding.attribute_rules import (
        AttributeComponentRole,
        AttributeSourceRequest,
    )
    from marivo.analysis.binding.graph import BoundGraph
    from marivo.analysis.compiler.native_query import NativeQueryFinish
    from marivo.analysis.compiler.original import ComponentLayout
    from marivo.analysis.compiler.reduction_input import ReductionInput
    from marivo.analysis.core.attribute import AttributeParentContext
    from marivo.analysis.model.attribute_failures import AttributeUseContext
    from marivo.semantic.spi import CompiledSemanticState


@dataclass(frozen=True, slots=True)
class AttributeLeafInput:
    request: AttributeSourceRequest
    read: SourceReadStage
    roles: tuple[tuple[AttributeComponentRole, DataColumn], ...]
    support: DataColumn
    effective: DataColumn
    use: AttributeUseContext
    distinct: DataColumn | None = None
    fiber: int = 0
    exact_integer: bool = False


@dataclass(frozen=True, slots=True)
class AttributeCheckLayout:
    name: str
    scope: Literal["parent", "fine", "preparation"]
    leaves: tuple[AttributeLeafInput, ...]
    columns: tuple[DataColumn, ...]


@dataclass(frozen=True, slots=True)
class AttributeNativeFormula:
    """One original recipe subtree over this root's completed reductions."""

    path: tuple[int, ...]
    occurrence: tuple[int, ...]
    node: MetricNode
    program: FormulaProgram
    column: DataColumn
    use: AttributeUseContext
    fiber: int


@dataclass(frozen=True, slots=True)
class AttributeRootLayout:
    """One independent endpoint/root role's original values and components."""

    leaves: tuple[AttributeLeafInput, ...]
    columns: tuple[DataColumn, ...]
    parent_keys: tuple[str, ...]
    identity: tuple[tuple[DataColumn, str], ...]
    scoped_columns: tuple[tuple[str, str], ...]
    parent_parameter: SourceParameter
    side: Literal["left", "right"]
    scope: Literal["parent", "fine"] = "fine"
    assigned: str = "__attribute_assigned"
    partition: bool = False
    population: SourceParameter | None = None
    kind: str = "__attribute_kind"
    fiber: str = "__attribute_fiber"
    checks: tuple[AttributeCheckLayout, ...] = ()
    parent_context: AttributeParentContext | None = None
    native_formulas: tuple[AttributeNativeFormula, ...] = ()
    parent_unique_keys: UniqueKeys = field(default_factory=UniqueKeys)
    fine_unique_keys: UniqueKeys = field(default_factory=UniqueKeys)

    @property
    def keys(self) -> tuple[str, ...]:
        return self.parent_keys if self.scope == "parent" else self.leaves[0].read.keys


@dataclass(frozen=True, slots=True)
class RequiredSlots:
    """Transport needed after this stage has completed all its owning checks."""

    stage_id: str
    columns: tuple[str, ...]
    components: tuple[str, ...] = ()
    retain_populations: bool = False


@dataclass(frozen=True, slots=True)
class FusionGroup:
    law: Literal["L1", "source_block"]
    members: tuple[str, ...]
    output: str


@dataclass(frozen=True, slots=True)
class RuleDecision:
    rule: str
    status: Literal["adopted", "refused"]
    members: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class SourceField:
    """One physical column admitted at execute preparation, without its values."""

    field: str
    column: str
    dtype: DataType
    nullable: bool


@dataclass(frozen=True, slots=True)
class SourceSchema:
    """Schema-only facts for one contribution root Entity output."""

    source_id: str
    keys: tuple[SourceField, ...]
    fields: tuple[SourceField, ...]
    occurrence: tuple[int, ...] | None = None
    preparation_checks: tuple[tuple[str, str, str], ...] = ()
    key_groups: tuple[tuple[str, ...], ...] = ()
    presence: str | None = None
    scope_id: str = ""
    parameters: tuple[PopulationParameterPlan, ...] = ()
    inline_population: PopulationDependency | None = None
    original_identity: tuple[SourceField, ...] = ()
    binding_id: str = ""
    time_authorities: tuple[SourceTimeAuthority, ...] = ()
    provider: str = DEFAULT_BACKEND_TYPE
    unique_keys: UniqueKeys = field(default_factory=UniqueKeys)

    def field(self, path: str) -> SourceField:
        for item in self.fields:
            if item.field == path:
                return item
        reject(
            f"an admitted source column for {path}",
            f"columns of {self.source_id}: {tuple(f.field for f in self.fields)!r}",
            "Prepare every field read by this contribution before planning.",
            "analysis.query_plan.source_schema",
        )


@dataclass(frozen=True, slots=True)
class PeriodFilter:
    """Captured source period and whether its exact predicate is prepared."""

    column: str
    start: datetime | None
    end: datetime
    timezone: str = "UTC"
    predicate_prepared: bool = False


@dataclass(frozen=True, slots=True)
class ProjectionRead:
    projections: tuple[tuple[str, str], ...]
    presence: str | None = None


@dataclass(frozen=True, slots=True)
class ReductionRead:
    reduction: ReductionInput


@dataclass(frozen=True, slots=True)
class AttributeFrequencyRead:
    """Exact typed distribution state over one governed source occurrence."""

    reduction: ReductionInput
    input_type: DataType | None = None
    members: tuple[SourceReadStage, ...] = ()
    finishers: tuple[
        tuple[str, Literal["rank_bounds", "duckdb_native", "postgres_native"]], ...
    ] = ()


@dataclass(frozen=True, slots=True)
class ComponentRead:
    layout: ComponentLayout


@dataclass(frozen=True, slots=True)
class CommonRead:
    members: tuple[SourceReadStage, ...]


@dataclass(frozen=True, slots=True)
class AttributeRead:
    layout: AttributeRootLayout
    representative: ReductionInput
    members: tuple[SourceReadStage, ...] = ()


@dataclass(frozen=True, slots=True)
class NativeRead:
    reductions: tuple[ReductionInput, ...]
    finish: NativeQueryFinish | None = None


@dataclass(frozen=True, slots=True)
class NativeEndpoint:
    """Original captured reducer templates and their prepared source input."""

    input_id: str
    reads: tuple[tuple[tuple[int, ...], SourceReadStage], ...]


@dataclass(frozen=True, slots=True)
class AttributeCoalitionRead:
    """One logical native game; every issued statement has its own read identity."""

    game: AttributeGameProgram
    endpoints: tuple[NativeEndpoint, NativeEndpoint]


@dataclass(frozen=True, slots=True)
class ActualWindowRead:
    """Finish a windowed Grain-only observation over an actual complete key image."""

    graph: BoundGraph
    state: CompiledSemanticState
    timezone: str
    frame_schemas: tuple[tuple[str, DataSchema], ...]
    key_inputs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SampleWindowRead:
    """Evaluate windows over discovered calendar sample domains."""

    graph: BoundGraph
    state: CompiledSemanticState
    timezone: str
    frame_schemas: tuple[tuple[str, DataSchema], ...]
    key_inputs: tuple[str, ...]
    candidates: tuple[ContributionBinding, ...]
    populations: tuple[PopulationInput, ...]
    counts: tuple[MemberInput, ...]
    reuse_population_outputs: bool = False


@dataclass(frozen=True, slots=True)
class CheckedOutput:
    """Publish a result already completed by an earlier invocation phase."""


SourceProgram: TypeAlias = (
    ProjectionRead
    | ReductionRead
    | AttributeFrequencyRead
    | ComponentRead
    | CommonRead
    | AttributeRead
    | NativeRead
    | AttributeCoalitionRead
    | ActualWindowRead
    | SampleWindowRead
    | CheckedOutput
)


@dataclass(frozen=True, slots=True)
class SourceReadStage:
    """One planned source-read stage with a closed execution program."""

    stage_id: str
    source_id: str
    occurrence: tuple[int, ...]
    columns: tuple[DataColumn, ...]
    keys: tuple[str, ...]
    program: SourceProgram
    field_columns: tuple[tuple[str, str], ...]
    predicates: tuple[PredicateExpression, ...]
    period: PeriodFilter | None
    preparation_checks: tuple[tuple[str, str, str], ...] = ()
    inputs: tuple[str, ...] = ()
    parameters: tuple[SourceParameter, ...] = ()
    attribute_parent: AttributeSourceParentBinding | None = None
    binding_id: str = ""
    native_reductions: tuple[ReductionInput, ...] = ()
    native_reads: tuple[SourceReadStage, ...] = ()
    attribute_state: bool = False
    attribute_input_types: tuple[tuple[str, tuple[DataType, ...]], ...] = ()
    attribute_native: bool = False
    native_relation_id: str = ""
    unique_keys: UniqueKeys = field(default_factory=UniqueKeys)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "unique_keys", self.unique_keys.project(tuple(c.slot_id for c in self.columns))
        )


def read_reduction(stage: SourceReadStage) -> ReductionInput | None:
    program = stage.program
    if isinstance(program, (ReductionRead, AttributeFrequencyRead)):
        return program.reduction
    if isinstance(program, ComponentRead):
        return program.layout.reduction
    if isinstance(program, AttributeRead):
        return program.representative
    if isinstance(program, CommonRead):
        return read_reduction(program.members[0])
    return None


def require_reduction(stage: SourceReadStage) -> ReductionInput:
    reduction = read_reduction(stage)
    assert reduction is not None
    return reduction


def read_components(stage: SourceReadStage) -> ComponentLayout | None:
    return stage.program.layout if isinstance(stage.program, ComponentRead) else None


def read_attribute(stage: SourceReadStage) -> AttributeRootLayout | None:
    return stage.program.layout if isinstance(stage.program, AttributeRead) else None


def read_members(stage: SourceReadStage) -> tuple[SourceReadStage, ...]:
    return (
        stage.program.members
        if isinstance(stage.program, (CommonRead, AttributeRead, AttributeFrequencyRead))
        else ()
    )


def common_program(
    stage: SourceReadStage, members: tuple[SourceReadStage, ...]
) -> CommonRead | AttributeRead:
    if isinstance(stage.program, AttributeRead):
        return replace(stage.program, members=members)
    return CommonRead(members)


def read_finish(stage: SourceReadStage) -> NativeQueryFinish | None:
    return stage.program.finish if isinstance(stage.program, NativeRead) else None


def require_components(stage: SourceReadStage) -> ComponentLayout:
    layout = read_components(stage)
    assert layout is not None
    return layout


def require_attribute(stage: SourceReadStage) -> AttributeRootLayout:
    layout = read_attribute(stage)
    assert layout is not None
    return layout


def require_finish(stage: SourceReadStage) -> NativeQueryFinish:
    finish = read_finish(stage)
    assert finish is not None
    return finish


def read_native_reductions(stage: SourceReadStage) -> tuple[ReductionInput, ...]:
    native = stage.program.reductions if isinstance(stage.program, NativeRead) else ()
    return (*native, *stage.native_reductions)


def read_projections(stage: SourceReadStage) -> tuple[tuple[str, str], ...]:
    return stage.program.projections if isinstance(stage.program, ProjectionRead) else ()


def read_presence(stage: SourceReadStage) -> str | None:
    return stage.program.presence if isinstance(stage.program, ProjectionRead) else None


@dataclass(frozen=True, slots=True)
class FusedLocalProgram:
    steps: tuple[LocalCompute, ...]


@dataclass(frozen=True, slots=True)
class LocalCompute:
    """One closed local program over completed stage outputs."""

    stage_id: str
    inputs: tuple[str, ...]
    columns: tuple[DataColumn, ...]
    keys: tuple[str, ...]
    program: FormulaProgram | ResultProgram | FusedLocalProgram
    attribute: AttributeFormulaBinding | None = None
    unique_keys: UniqueKeys = field(default_factory=UniqueKeys)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "unique_keys", self.unique_keys.project(tuple(c.slot_id for c in self.columns))
        )

    @property
    def kernel(self) -> str:
        return (
            "query.result@v1" if isinstance(self.program, ResultProgram) else QUERY_COMBINE_KERNEL
        )


def local_result(stage: LocalCompute) -> ResultProgram | None:
    return stage.program if isinstance(stage.program, ResultProgram) else None


def require_result(stage: LocalCompute) -> ResultProgram:
    program = local_result(stage)
    assert program is not None
    return program


def local_steps(stage: LocalCompute) -> tuple[LocalCompute, ...]:
    return stage.program.steps if isinstance(stage.program, FusedLocalProgram) else ()


@dataclass(frozen=True, slots=True)
class FrameInput:
    """Exact fixed-data identity; its full validation contract cannot be pruned."""

    stage_id: str
    input_id: str
    schema: DataSchema
    inputs: tuple[str, ...] = ()

    @property
    def unique_keys(self) -> UniqueKeys:
        return UniqueKeys(tuple(c.slot_id for c in self.columns), (self.keys,))

    @property
    def columns(self) -> tuple[DataColumn, ...]:
        return self.schema.columns

    @property
    def keys(self) -> tuple[str, ...]:
        return self.schema.key_fields


@dataclass(frozen=True, slots=True)
class FrameFilterFusion:
    """Columns needed by the second mask and original steps for carrier changes."""

    second_columns: tuple[str, ...]
    fallback: tuple[FrameCompute, FrameCompute]


@dataclass(frozen=True, slots=True)
class FrameCompute:
    """A closed Frame program, independent from semantic Query kernels."""

    stage_id: str
    inputs: tuple[str, ...]
    program: (
        FrameProgram
        | FrameCombineProgram
        | FrameCompareProgram
        | AttributeProjectProgram
        | AttributeInputProgram
        | AttributeContributionProgram
        | AttributeMergeProgram
        | AttributeGameProgram
    )

    unique_keys: UniqueKeys = field(default_factory=UniqueKeys)
    filter_fusion: FrameFilterFusion | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "unique_keys", self.unique_keys.project(tuple(c.slot_id for c in self.columns))
        )

    @property
    def columns(self) -> tuple[DataColumn, ...]:
        return self.program.output_schema.columns

    @property
    def keys(self) -> tuple[str, ...]:
        return self.program.output_schema.key_fields


@dataclass(frozen=True, slots=True)
class FrameKeysInput:
    """Compiler-only key relation, eliminated before the Frame schedule runs."""

    stage_id: str
    parameter: FrameParameter
    inputs: tuple[str, ...] = ()

    @property
    def unique_keys(self) -> UniqueKeys:
        return UniqueKeys(tuple(c.slot_id for c in self.columns), (self.keys,))

    @property
    def columns(self) -> tuple[DataColumn, ...]:
        return self.parameter.columns

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(column.slot_id for column in self.columns)


QueryStage: TypeAlias = SourceReadStage | LocalCompute | FrameInput | FrameCompute | FrameKeysInput


@dataclass(frozen=True, slots=True)
class QueryPlan:
    """Topologically ordered stages, consumers and the published stage."""

    stages: tuple[QueryStage, ...]
    consumers: tuple[tuple[str, tuple[str, ...]], ...]
    publish: str
    output_groups: tuple[tuple[str, ...], ...] = ()
    original: FormulaProgram | None = None
    required_slots: tuple[RequiredSlots, ...] = ()
    fusion_groups: tuple[FusionGroup, ...] = ()
    optimization_decisions: tuple[RuleDecision, ...] = ()

    def consumers_of(self, stage_id: str) -> tuple[str, ...]:
        return dict(self.consumers).get(stage_id, ())

    def source_read_stage(self, stage_id: str) -> SourceReadStage:
        for stage in self.stages:
            if stage.stage_id == stage_id and isinstance(stage, SourceReadStage):
                return stage
        reject(
            "a source stage in the current executable plan",
            stage_id,
            "Associate the lowered payload with its current source stage.",
            "analysis.query_plan",
        )


def checked_output(
    identifier: str, columns: tuple[DataColumn, ...], keys: tuple[str, ...]
) -> SourceReadStage:
    return SourceReadStage(identifier, identifier, (), columns, keys, CheckedOutput(), (), (), None)
