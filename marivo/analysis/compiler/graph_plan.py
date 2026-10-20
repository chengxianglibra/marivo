"""Pure Query admission and stage handoff, with no execution imports."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import TYPE_CHECKING, Literal, TypeAlias

from marivo._definition.correspondence import coordinate_position
from marivo._definition.expressions import PredicateExpression
from marivo._definition.relations import PopulationDependency
from marivo.analysis.compiler.query_program import (
    FrameCombineProgram,
    FrameParameter,
    FrameProgram,
    PopulationParameterPlan,
    ResultProgram,
    SourceParameter,
)
from marivo.analysis.core.data_schema import DataSchema
from marivo.analysis.core.data_types import DataColumn, DataType, DurationDataType, ScalarDataType
from marivo.analysis.core.graph import BoundGraph
from marivo.analysis.core.model import Obligation, reject
from marivo.analysis.methods.physical import QUERY_COMBINE_KERNEL

if TYPE_CHECKING:
    from marivo.analysis.compiler.native_query import NativeQueryFinish
    from marivo.analysis.compiler.observation import FormulaProgram, ReductionInput
    from marivo.analysis.compiler.optimization import (
        FusionGroup,
        InvocationMemo,
        RequiredSlots,
        RuleDecision,
    )
    from marivo.analysis.compiler.original import ComponentLayout


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
    """Half-open instant filter on one native timezone-aware time column."""

    column: str
    start: datetime | None
    end: datetime
    timezone: str = "UTC"


@dataclass(frozen=True, slots=True)
class SourceRead:
    """One governed source read; its slots are the only columns it returns."""

    stage_id: str
    source_id: str
    occurrence: tuple[int, ...]
    columns: tuple[DataColumn, ...]
    keys: tuple[str, ...]
    projections: tuple[tuple[str, str], ...]
    field_columns: tuple[tuple[str, str], ...]
    predicates: tuple[PredicateExpression, ...]
    period: PeriodFilter | None
    reduction: ReductionInput | None = None
    preparation_checks: tuple[tuple[str, str, str], ...] = ()
    presence: str | None = None
    inputs: tuple[str, ...] = ()
    parameters: tuple[SourceParameter, ...] = ()
    component_layout: ComponentLayout | None = None
    binding_id: str = ""
    common_reads: tuple[SourceRead, ...] = ()
    native_reductions: tuple[ReductionInput, ...] = ()
    native_reads: tuple[SourceRead, ...] = ()
    native_query_finish: NativeQueryFinish | None = None


@dataclass(frozen=True, slots=True)
class LocalCompute:
    """One registered pandas kernel over completed stage outputs."""

    stage_id: str
    inputs: tuple[str, ...]
    kernel: str
    columns: tuple[DataColumn, ...]
    keys: tuple[str, ...]
    program: FormulaProgram | None = None
    result: ResultProgram | None = None
    fused_steps: tuple[LocalCompute, ...] = ()


@dataclass(frozen=True, slots=True)
class FrameInput:
    """Exact fixed-data identity; its full validation contract cannot be pruned."""

    stage_id: str
    input_id: str
    schema: DataSchema
    inputs: tuple[str, ...] = ()

    @property
    def columns(self) -> tuple[DataColumn, ...]:
        return self.schema.columns

    @property
    def keys(self) -> tuple[str, ...]:
        return self.schema.key_fields


@dataclass(frozen=True, slots=True)
class FrameCompute:
    """A closed Frame program, independent from semantic Query kernels."""

    stage_id: str
    inputs: tuple[str, ...]
    program: FrameProgram | FrameCombineProgram

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
    def columns(self) -> tuple[DataColumn, ...]:
        return self.parameter.columns

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(column.slot_id for column in self.columns)


QueryStage: TypeAlias = SourceRead | LocalCompute | FrameInput | FrameCompute | FrameKeysInput


@dataclass(frozen=True, slots=True)
class SourceCheckRoute:
    """An implemented check bound to one exact contribution occurrence."""

    obligation: Obligation
    occurrence: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class PlannedCheck:
    """A check with its fixed stage and consume/publish deadline."""

    obligation: Obligation
    stage_id: str

    @property
    def check_id(self) -> str:
        return self.obligation.check_id

    @property
    def deadline(self) -> Literal["consume", "publish"]:
        return self.obligation.before


@dataclass(frozen=True, slots=True)
class QueryPlan:
    """Topologically ordered stages, consumers, checks and the published stage."""

    stages: tuple[QueryStage, ...]
    checks: tuple[PlannedCheck, ...]
    consumers: tuple[tuple[str, tuple[str, ...]], ...]
    publish: str
    output_groups: tuple[tuple[str, ...], ...] = ()
    original: FormulaProgram | None = None
    required_slots: tuple[RequiredSlots, ...] = ()
    fusion_groups: tuple[FusionGroup, ...] = ()
    optimization_decisions: tuple[RuleDecision, ...] = ()

    def consumers_of(self, stage_id: str) -> tuple[str, ...]:
        return dict(self.consumers).get(stage_id, ())


def _stage_id(occurrence: tuple[int, ...]) -> str:
    return "read:" + ".".join(str(index) for index in occurrence)


def _plan_source_reads(
    graph: BoundGraph,
    schemas: tuple[SourceSchema, ...],
    *,
    checks: tuple[SourceCheckRoute, ...] = (),
) -> QueryPlan:
    """Fix stages, slots, consumers and check deadlines without any I/O.

    Each contribution occurrence becomes one SourceRead. A single contribution
    publishes its read directly; several are combined by one LocalCompute whose
    output slots the registered kernel declares. Implemented source checks must
    identify their exact occurrence; a check ID alone does not establish scope.
    """
    from marivo.analysis.errors import AnalysisStageUnavailableError

    observation = graph.signature.observation
    contributions = () if observation is None else observation.contributions
    if not contributions:
        raise AnalysisStageUnavailableError(
            stage="query_execution",
            expected="at least one source contribution for an AnalysisFrame compute plan",
            received="source-free Metric outputs",
            repair="Bind a source-backed Metric with session.observe(...) before computing the AnalysisFrame.",
            target="analysis_frame.compute",
        )
    by_source = {schema.source_id: schema for schema in schemas}
    from marivo._definition.relations import condition_populations
    from marivo._definition.types import FieldRead, coordinate_read_keys, field_read_key
    from marivo.analysis.compiler.predicate import population_key

    reads: list[SourceRead] = []
    for contribution in contributions:
        source_id = contribution.root.ref.path
        schema = next(
            (
                s
                for s in schemas
                if s.source_id == source_id and s.occurrence == contribution.occurrence
            ),
            by_source.get(source_id),
        )
        if schema is None:
            reject(
                f"prepared schema facts for {source_id}",
                repr(tuple(by_source)),
                "Prepare every contribution root before planning.",
                "analysis.query_plan.source_schema",
            )
        stage_id = _stage_id(contribution.occurrence)
        paths = list(
            dict.fromkeys(
                (
                    *(field_read_key(read) for read in contribution.reads),
                    *(
                        population_key(domain)
                        for condition in (
                            *contribution.intrinsic_conditions,
                            *(
                                (contribution.common_condition,)
                                if contribution.common_condition is not None
                                else ()
                            ),
                        )
                        for domain in condition_populations(condition)
                    ),
                    *(
                        key
                        for read in contribution.coordinates
                        for key in coordinate_read_keys(read)
                    ),
                )
            )
        )
        fields = tuple(schema.field(path) for path in paths)
        key_slots = tuple(f"{stage_id}/k{index}" for index in range(len(schema.keys)))
        columns = (
            *(
                DataColumn(slot, key.column, key.dtype, False)
                for slot, key in zip(key_slots, schema.keys, strict=True)
            ),
            *(
                DataColumn(f"{stage_id}/f{index}", item.field, item.dtype, item.nullable)
                for index, item in enumerate(fields)
            ),
        )
        projections = tuple(
            zip(
                (column.slot_id for column in columns),
                (*(key.column for key in schema.keys), *(item.column for item in fields)),
                strict=True,
            )
        )
        period = None
        if contribution.period is not None and contribution.time is not None:
            period = PeriodFilter(
                schema.field(
                    field_read_key(
                        FieldRead(
                            contribution.time.field,
                            contribution.root,
                            contribution.time.path,
                            contribution.time.at,
                        )
                    )
                ).column,
                contribution.period.start,
                contribution.period.end,
                contribution.period.timezone,
            )
        reads.append(
            SourceRead(
                stage_id,
                source_id,
                contribution.occurrence,
                columns,
                key_slots,
                projections,
                tuple((item.field, item.column) for item in fields),
                (
                    *contribution.intrinsic_conditions,
                    *(
                        (contribution.common_condition,)
                        if contribution.common_condition is not None
                        else ()
                    ),
                ),
                period,
                preparation_checks=schema.preparation_checks,
                binding_id=schema.binding_id,
            )
        )
    stages: tuple[QueryStage, ...] = tuple(reads)
    consumers: tuple[tuple[str, tuple[str, ...]], ...] = ()
    publish = reads[0].stage_id
    if len(reads) > 1:
        combine = LocalCompute(
            "local:combine", tuple(read.stage_id for read in reads), QUERY_COMBINE_KERNEL, (), ()
        )
        stages = (*reads, combine)
        consumers = tuple((read.stage_id, (combine.stage_id,)) for read in reads)
        publish = combine.stage_id
    planned: list[PlannedCheck] = []
    by_occurrence = {read.occurrence: read for read in reads}
    for route in checks:
        if (
            route.obligation not in graph.signature.obligations
            or route.occurrence not in by_occurrence
        ):
            reject(
                "a source-check obligation and its exact source contribution occurrence",
                repr(route),
                "Bind the implemented check to its exact source contribution before planning.",
                "analysis.query_plan.check",
            )
    for obligation in graph.signature.obligations:
        routes = tuple(route for route in checks if route.obligation == obligation)
        if not routes:
            raise AnalysisStageUnavailableError(
                stage="query_execution",
                expected=f"an implemented {obligation.check_id} check bound to its source occurrence",
                received=f"{obligation.fact.kind} premise without a current source-check route",
                repair="This check route is not delivered yet; no business row was read.",
                target="analysis_frame.compute",
            )
        planned.extend(
            PlannedCheck(obligation, by_occurrence[route.occurrence].stage_id) for route in routes
        )
    return QueryPlan(stages, tuple(planned), consumers, publish)


def plan_query(
    graph: BoundGraph,
    schemas: tuple[SourceSchema, ...],
    *,
    checks: tuple[SourceCheckRoute, ...] = (),
    require_original: bool = False,
    memo: InvocationMemo | None = None,
    prefix: tuple[int, ...] = (),
) -> QueryPlan:
    """Plan dependency producers before every parameterized source consumer."""
    if memo is not None:
        from marivo.analysis.compiler.optimization import compilation_key

        return memo.plan(
            compilation_key(
                graph,
                schemas,
                required=("prefix:" + repr(prefix),),
                require_original=require_original,
                checks=checks,
            ),
            lambda: _plan_bound_query(
                graph,
                schemas,
                checks=checks,
                require_original=require_original,
                memo=memo,
                prefix=prefix,
            ),
        )
    return _plan_bound_query(
        graph,
        schemas,
        checks=checks,
        require_original=require_original,
        memo=None,
        prefix=prefix,
    )


def _plan_bound_query(
    graph: BoundGraph,
    schemas: tuple[SourceSchema, ...],
    *,
    checks: tuple[SourceCheckRoute, ...],
    require_original: bool,
    memo: InvocationMemo | None,
    prefix: tuple[int, ...],
) -> QueryPlan:
    from marivo._definition.query import ResultQuery
    from marivo._definition.relations import ReadQuery

    if isinstance(graph.root, ResultQuery):
        from marivo.analysis.compiler.queries import plan_literal

        return plan_literal(graph, graph.root.operation, schemas)
    if isinstance(graph.root, ReadQuery):
        from marivo.analysis.compiler.queries import plan_read

        return plan_read(graph, schemas, memo=memo)

    from marivo.analysis.compiler.parameters import plan_parameters

    planned = plan_parameters(graph, schemas, checks, memo=memo)
    if require_original:
        from marivo.analysis.compiler.optimization import retain_components

        return retain_components(planned, schemas)
    return planned


def _plan_query(
    graph: BoundGraph,
    schemas: tuple[SourceSchema, ...],
    *,
    checks: tuple[SourceCheckRoute, ...] = (),
) -> QueryPlan:
    """Fix ordinary numeric source and local routes before any business read."""
    import ibis

    from marivo._definition.relations import ObserveQuery

    assert isinstance(graph.root, ObserveQuery)

    from marivo._definition.expressions import (
        ContributionFilter,
        DeclaredMetricRef,
        EntityCount,
        SourceAggregate,
    )
    from marivo._definition.graph import computation_fingerprint, walk
    from marivo._definition.query import PopulationValues
    from marivo._definition.relations import LiteralPopulation, PopulationProduct, PopulationProgram
    from marivo._definition.types import (
        CalendarCoordinate,
        IntervalCoordinate,
        coordinate_read_keys,
    )
    from marivo.analysis.compiler.observation import (
        FormulaInput,
        FormulaProgram,
        PopulationInput,
        data_type,
        duration_type,
        formula_columns,
        ibis_type,
        lower_observation,
        reduction_input,
    )

    def time_name(axis: CalendarCoordinate | IntervalCoordinate) -> str:
        return f"calendar:{axis.grain}" if isinstance(axis, CalendarCoordinate) else "interval"

    observation = graph.signature.observation
    contributions = () if observation is None else observation.contributions
    base = (
        _plan_source_reads(graph, schemas, checks=checks)
        if contributions
        else QueryPlan((), (), (), "")
    )
    calendar = next(
        (
            axis
            for axis in graph.signature.axes
            if isinstance(axis, (CalendarCoordinate, IntervalCoordinate))
        ),
        None,
    )
    reads: list[SourceRead] = []
    values: list[FormulaInput] = []
    keys: tuple[DataColumn, ...] = (
        (
            DataColumn(
                "key.0",
                f"calendar:{calendar.grain}"
                if isinstance(calendar, CalendarCoordinate)
                else "interval",
                ScalarDataType("int64"),
                False,
            ),
        )
        if calendar is not None
        else ()
    )
    calendar_key = "key.0"
    source_stages = tuple(stage for stage in base.stages if isinstance(stage, SourceRead))
    for stage, contribution in zip(source_stages, contributions, strict=True):
        reduction = reduction_input(contribution, f"{stage.stage_id}/value", calendar)
        schema = next(
            item
            for item in schemas
            if item.source_id == stage.source_id
            and (item.occurrence is None or item.occurrence == stage.occurrence)
        )
        reduction = replace(
            reduction,
            duration=duration_type(
                contribution.definition, {item.field: item.dtype for item in schema.fields}
            ),
        )
        table = ibis.table({item.column: ibis_type(item.dtype) for item in schema.fields})
        expression, numeric_checks = lower_observation(
            reduction, table, dict(stage.field_columns), stage.predicates
        )
        calendar_slots = dict(reduction.calendars)
        if calendar is not None:
            calendar_key = next(slot for slot, axis in reduction.calendars if axis == calendar)
        stage_keys = tuple(
            DataColumn(
                slot,
                (time_name(calendar_slots[slot]))
                if slot in calendar_slots
                else next(path for key, path in reduction.axes if key == slot),
                next(
                    (
                        field.dtype
                        for field in schema.fields
                        if field.field
                        == next((path for key, path in reduction.axes if key == slot), "")
                        and isinstance(field.dtype, DurationDataType)
                    ),
                    data_type(expression[slot].type()),
                ),
                True,
            )
            for slot in expression.columns
            if slot.startswith("key.")
        )
        output_width = int(calendar is not None) + sum(
            len(coordinate_read_keys(read))
            for read in contribution.coordinates[: contribution.output_axis_count]
        )
        output_keys = stage_keys[:output_width]
        if reads and tuple(c.dtype for c in keys) != tuple(c.dtype for c in output_keys):
            reject(
                "identical physical coordinate types across contributions",
                repr(stage_keys),
                "Choose fields with a common lossless coordinate type.",
                "analysis.query_plan",
            )
        keys = output_keys
        column = DataColumn(
            reduction.value_slot,
            reduction.value_slot,
            reduction.duration or data_type(expression[reduction.value_slot].type()),
            True,
        )
        reads.append(
            replace(
                stage,
                columns=(
                    *stage_keys,
                    column,
                    *(
                        DataColumn(name, name, ScalarDataType("boolean"), False)
                        for name, _ in numeric_checks
                    ),
                    *(
                        (
                            DataColumn(
                                "__numeric_present",
                                "__numeric_present",
                                ScalarDataType("boolean"),
                                False,
                            ),
                        )
                        if "__numeric_present" in expression.columns
                        else ()
                    ),
                    *(
                        (
                            DataColumn(
                                "__sample_present",
                                "__sample_present",
                                ScalarDataType("boolean"),
                                False,
                            ),
                        )
                        if "__sample_present" in expression.columns
                        else ()
                    ),
                ),
                keys=tuple(c.slot_id for c in stage_keys),
                reduction=reduction,
            )
        )
        node = contribution.definition
        widths_list: list[int] = []
        physical_index = 0
        coordinate_iterator = iter(contribution.coordinates)
        for _ in range(len(contribution.coordinates) + len(reduction.calendars)):
            width = (
                1
                if f"key.{physical_index}" in calendar_slots
                else len(coordinate_read_keys(next(coordinate_iterator)))
            )
            widths_list.append(width)
            physical_index += width
        widths = tuple(widths_list)
        key_groups: list[tuple[str, ...]] = []
        key_offset = 0
        for width in widths:
            key_groups.append(tuple(f"key.{i}" for i in range(key_offset, key_offset + width)))
            key_offset += width
        values.append(
            FormulaInput(
                contribution.logical_occurrence or contribution.occurrence,
                stage.stage_id,
                column,
                isinstance(node, EntityCount)
                or (
                    isinstance(node, SourceAggregate)
                    and (
                        node.reducer in ("count", "count_distinct", "approx_nunique")
                        or (node.reducer == "sum" and node.empty == "zero")
                    )
                ),
                tuple((name, check.expected, check.received) for name, check in numeric_checks),
                keys=tuple(c.slot_id for c in stage_keys),
                coverage=contribution.coverage,
                key_groups=tuple(key_groups),
            )
        )
    population_inputs: list[PopulationInput] = []
    population_stages: list[SourceRead | LocalCompute] = []
    output_population = observation.coordinates.population if observation is not None else None
    closure = walk(
        (
            graph.root,
            *((output_population,) if output_population is not None else ()),
            *(
                (observation.condition,)
                if observation is not None and observation.condition is not None
                else ()
            ),
        ),
        resolve=dict(graph.signature.declared_metrics).get,
    )
    for population in closure.order:
        if not isinstance(population, (PopulationProgram, LiteralPopulation)):
            continue
        identifier = "population:" + computation_fingerprint(population)
        population_schema = next((item for item in schemas if item.source_id == identifier), None)
        if population_schema is None:
            continue  # Entirely literal domain programs are evaluated without source reads.
        domain_columns = tuple(
            DataColumn(f"population.{i}", item.field, item.dtype, item.nullable)
            for i, item in enumerate(population_schema.keys)
        )
        if not graph.root.metrics and population == output_population:
            keys = tuple(
                replace(column, slot_id=f"key.{i}") for i, column in enumerate(domain_columns)
            )
        from marivo._definition.relations import population_keys

        if population_keys(population) is not None:
            continue  # Keep schema-bound key types, execute finite keys locally.
        slots = tuple(column.slot_id for column in domain_columns)
        column_slots = {
            key.column: column.slot_id
            for key, column in zip(population_schema.keys, domain_columns, strict=True)
        }
        groups = tuple(
            tuple(column_slots[column] for column in group)
            for group in population_schema.key_groups
        )
        if isinstance(population, PopulationProgram) and isinstance(
            population.root, PopulationProduct
        ):
            # Reuse checked factor keys, including factors consumed directly by the formula.
            factors = tuple(
                next(item for item in population_inputs if item.definition == factor)
                for factor in population.root.factors
                if population_keys(factor) is None
            )
            population_stages.append(
                LocalCompute(
                    identifier,
                    tuple(factor.stage_id for factor in factors),
                    "query.result@v1",
                    domain_columns,
                    slots,
                    result=ResultProgram(
                        PopulationValues(population),
                        (),
                        (),
                        (),
                        (),
                        groups,
                        population_inputs=tuple(factor.definition for factor in factors),
                        population_layouts=tuple(factor.key_groups for factor in factors),
                    ),
                )
            )
        else:
            population_stages.append(
                SourceRead(
                    identifier,
                    identifier,
                    (),
                    domain_columns,
                    slots,
                    tuple(
                        (column.slot_id, key.column)
                        for column, key in zip(domain_columns, population_schema.keys, strict=True)
                    ),
                    (),
                    (),
                    None,
                    preparation_checks=population_schema.preparation_checks,
                    presence=population_schema.presence,
                )
            )
        population_inputs.append(
            PopulationInput(
                population,
                identifier,
                slots,
                key_groups=groups,
            )
        )
    program = FormulaProgram(
        graph.root.metrics,
        graph.signature.declared_metrics,
        tuple(values),
        calendar,
        tuple(population_inputs),
        output_population=observation.coordinates.population if observation is not None else None,
        calendar_key=calendar_key,
        axes=graph.signature.axes,
    )
    count_inputs = []
    for definition, member_population in (
        () if observation is None else observation.count_populations
    ):
        from marivo.analysis.compiler.observation import MemberInput

        member_schema = next(
            item
            for item in schemas
            if item.source_id == "population:" + computation_fingerprint(member_population)
        )
        member_slots = tuple(f"member.{index}" for index in range(len(member_schema.keys)))
        member_groups = tuple(
            tuple(
                member_slots[tuple(key.column for key in member_schema.keys).index(name)]
                for name in group
            )
            for group in member_schema.key_groups
        )
        output_columns: list[DataColumn] = []
        for axis in graph.signature.axes:
            if isinstance(axis, (CalendarCoordinate, IntervalCoordinate)):
                calendar_key = f"key.{len(output_columns)}"
                output_columns.append(
                    DataColumn(
                        f"key.{len(output_columns)}",
                        f"calendar:{axis.grain}"
                        if isinstance(axis, CalendarCoordinate)
                        else "interval",
                        ScalarDataType("int64"),
                        False,
                    )
                )
                continue
            group = member_groups[coordinate_position(member_population.axes, axis)]
            for name in group:
                slot = f"key.{len(output_columns)}"
                item = member_schema.keys[member_slots.index(name)]
                output_columns.append(DataColumn(slot, item.field, item.dtype, item.nullable))
        if not reads:
            keys = tuple(output_columns)
        count_inputs.append(
            MemberInput(
                definition,
                member_population,
                member_slots,
                tuple(zip(member_population.axes, member_groups, strict=True)),
            )
        )
    program = replace(program, counts=tuple(count_inputs), calendar_key=calendar_key)
    columns = tuple(
        replace(column, unit=quantity.unit)
        for column, quantity in zip(
            formula_columns(program), graph.signature.quantities, strict=True
        )
    )
    # A single bare reduction already is the requested final table. No empty
    # local stage is inserted into a pure source route.
    direct = graph.root.metrics[0][1] if len(graph.root.metrics) == 1 else None
    declarations = dict(program.declared)
    while isinstance(direct, (DeclaredMetricRef, ContributionFilter)):
        direct = (
            declarations[direct.ref] if isinstance(direct, DeclaredMetricRef) else direct.operand
        )
    if (
        len(reads) == 1
        and not population_stages
        and program.output_population is None
        and direct == contributions[0].definition
        and calendar is None
    ):
        stage = reads[0]
        assert stage.reduction is not None
        stage = replace(
            stage,
            columns=(*keys, columns[0]),
            reduction=replace(stage.reduction, value_slot=columns[0].slot_id),
        )
        return replace(
            base,
            stages=(stage,),
            consumers=(),
            publish=stage.stage_id,
            original=replace(program, values=(replace(program.values[0], column=columns[0]),)),
        )
    combine = LocalCompute(
        "local:combine:" + graph.fingerprint,
        tuple(stage.stage_id for stage in (*reads, *population_stages)),
        QUERY_COMBINE_KERNEL,
        (*keys, *columns),
        tuple(c.slot_id for c in keys),
        program,
    )
    return QueryPlan(
        (*reads, *population_stages, combine),
        base.checks,
        tuple(
            (
                stage.stage_id,
                tuple(
                    consumer.stage_id
                    for consumer in (*population_stages, combine)
                    if stage.stage_id in consumer.inputs
                ),
            )
            for stage in (*reads, *population_stages)
        ),
        combine.stage_id,
        original=program,
    )
