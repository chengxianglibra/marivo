"""Logical Query admission and stage handoff."""

from __future__ import annotations

from dataclasses import replace

from marivo._definition.correspondence import ComparisonAxis, coordinate_position
from marivo._definition.expressions import (
    ContributionFilter,
    DeclaredMetricRef,
)
from marivo._definition.graph import computation_fingerprint, walk
from marivo._definition.observations import ContributionBinding, ObservationBinding
from marivo._definition.query import (
    FilterValues,
    KeyImage,
    PopulationValues,
    ReadFields,
    ReduceValues,
    SelectValues,
    TopValues,
    ValueSlot,
)
from marivo._definition.relations import (
    LiteralPopulation,
    ObserveQuery,
    PopulationDependency,
    PopulationProduct,
    PopulationProgram,
    PopulationSet,
    condition_populations,
    finite_population_domain,
)
from marivo._definition.types import (
    CalendarCoordinate,
    FieldRead,
    IntervalCoordinate,
    coordinate_read_keys,
    field_read_key,
)
from marivo.analysis.binding.calendar_keys import time_key_type, window_endpoint_keys
from marivo.analysis.binding.graph import BoundGraph
from marivo.analysis.binding.programs import FormulaProgram, MemberInput, PopulationInput
from marivo.analysis.compiler.contribution_plan import plan_contributions
from marivo.analysis.compiler.observation import (
    formula_columns,
)
from marivo.analysis.compiler.predicate import population_key
from marivo.analysis.compiler.queries import key_groups as output_key_groups
from marivo.analysis.compiler.query_program import (
    FrameCombineProgram,
    FrameCompareProgram,
    FrameProgram,
    ResultProgram,
)
from marivo.analysis.compiler.query_stages import (
    FrameCompute,
    LocalCompute,
    PeriodFilter,
    ProjectionRead,
    QueryPlan,
    QueryStage,
    ReductionRead,
    SourceReadStage,
    SourceSchema,
    read_reduction,
    require_reduction,
)
from marivo.analysis.core.frame_operations import FixFrameValues
from marivo.analysis.errors import AnalysisStageUnavailableError
from marivo.analysis.model.data_types import DataColumn
from marivo.analysis.model.errors import reject
from marivo.analysis.model.unique_keys import UniqueKeys


def _stage_id(occurrence: tuple[int, ...]) -> str:
    return "read:" + ".".join(str(index) for index in occurrence)


def _contribution_period(
    contribution: ContributionBinding, schema: SourceSchema
) -> PeriodFilter | None:
    time, period = contribution.time, contribution.period
    if time is None or period is None:
        return None
    clock = FieldRead(
        time.field,
        contribution.root,
        time.path,
        time.at,
        source_at=time.source_at,
        step_ats=time.step_ats,
    )
    return PeriodFilter(
        schema.field(field_read_key(clock)).column,
        period.start,
        period.end,
        period.timezone,
        predicate_prepared=any(item.field == "__time_period" for item in schema.fields),
    )


def _plan_source_reads(
    graph: BoundGraph,
    schemas: tuple[SourceSchema, ...],
) -> QueryPlan:
    """Fix source stages and output slots without any I/O.

    Each contribution occurrence becomes one SourceReadStage. A single contribution
    publishes its read directly; several are combined by the later Query
    planner with the exact slots its local program declares.
    """

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

    reads: list[SourceReadStage] = []
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
                    *(
                        item.field
                        for item in schema.fields
                        if item.field.startswith(("__sample_assigned_", "__calendar_"))
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
        reads.append(
            SourceReadStage(
                stage_id,
                source_id,
                contribution.occurrence,
                columns,
                key_slots,
                ProjectionRead(projections, None),
                tuple((item.field, item.column) for item in fields),
                (
                    *contribution.intrinsic_conditions,
                    *(
                        (contribution.common_condition,)
                        if contribution.common_condition is not None
                        else ()
                    ),
                ),
                _contribution_period(contribution, schema),
                preparation_checks=schema.preparation_checks,
                binding_id=schema.binding_id,
                conditional_binding_id=schema.conditional_binding_id,
                unique_keys=UniqueKeys(tuple(c.slot_id for c in columns), (key_slots,))
                if not contribution.memberships
                and (
                    not schema.original_identity
                    or tuple(k.column for k in schema.keys)
                    == tuple(k.column for k in schema.original_identity)
                )
                else UniqueKeys(),
            )
        )
    stages: tuple[QueryStage, ...] = tuple(reads)
    consumers: tuple[tuple[str, tuple[str, ...]], ...] = ()
    publish = reads[0].stage_id
    return QueryPlan(stages, consumers, publish)


def _plan_query(
    graph: BoundGraph,
    schemas: tuple[SourceSchema, ...],
    *,
    scope_id: str,
) -> QueryPlan:
    """Fix ordinary numeric source and local routes before any business read."""

    assert isinstance(graph.root, ObserveQuery)

    observation = graph.signature.observation
    contributions = () if observation is None else observation.contributions
    base = _plan_source_reads(graph, schemas) if contributions else QueryPlan((), (), "")
    calendar = next(
        (
            axis
            for axis in graph.signature.axes
            if isinstance(axis, (CalendarCoordinate, IntervalCoordinate))
        ),
        None,
    )
    source_stages = tuple(stage for stage in base.stages if isinstance(stage, SourceReadStage))
    output_population = observation.coordinates.population if observation is not None else None
    parent_scope = observation.coordinates.parent_scope if observation is not None else None
    reads, values, keys, calendar_key = plan_contributions(
        source_stages, contributions, schemas, calendar, output_population is not None
    )
    population_inputs, population_stages, keys = _plan_population_dependencies(
        graph, graph.root, schemas, observation, output_population, parent_scope, keys
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
        calendar_samples=observation.calendar_samples if observation is not None else (),
        window_endpoints=window_endpoint_keys(contributions),
        sample_matches=observation.sample_matches if observation is not None else (),
    )
    count_inputs, keys, calendar_key = _plan_member_counts(
        graph.signature.axes, schemas, observation, keys, calendar_key, has_source_reads=bool(reads)
    )
    program = _bind_formula_keys(graph, program, observation, keys, count_inputs, calendar_key)
    return _publish_observation(
        graph, base, reads, population_stages, contributions, calendar, keys, program, scope_id
    )


def _population_dependencies(
    population: PopulationDependency,
) -> tuple[PopulationDependency, ...] | None:
    node = population.root if isinstance(population, PopulationProgram) else None
    if isinstance(node, PopulationProduct):
        return node.factors
    if isinstance(node, PopulationSet) and all(
        not finite_population_domain(item) for item in (node.left, node.right)
    ):
        return node.left, node.right
    return None


def _plan_population_dependencies(
    graph: BoundGraph,
    root: ObserveQuery,
    schemas: tuple[SourceSchema, ...],
    observation: ObservationBinding | None,
    output_population: PopulationDependency | None,
    parent_scope: PopulationDependency | None,
    keys: tuple[DataColumn, ...],
) -> tuple[list[PopulationInput], list[SourceReadStage | LocalCompute], tuple[DataColumn, ...]]:
    population_inputs: list[PopulationInput] = []
    population_stages: list[SourceReadStage | LocalCompute] = []
    closure = walk(
        (
            root,
            *((output_population,) if output_population is not None else ()),
            *(
                (observation.condition,)
                if observation is not None and observation.condition is not None
                else ()
            ),
            *((parent_scope,) if parent_scope is not None else ()),
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
        if not root.metrics and population == output_population:
            keys = tuple(
                replace(column, slot_id=f"key.{i}") for i, column in enumerate(domain_columns)
            )

        if finite_population_domain(population):
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
        operands = _population_dependencies(population)
        if operands is not None:
            # Reuse the checked complete inputs in both local and fused execution.
            factors = tuple(
                next(item for item in population_inputs if item.definition == factor)
                for factor in operands
                if not finite_population_domain(factor)
            )
            population_stages.append(
                LocalCompute(
                    identifier,
                    tuple(factor.stage_id for factor in factors),
                    domain_columns,
                    slots,
                    ResultProgram(
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
                SourceReadStage(
                    identifier,
                    identifier,
                    (),
                    domain_columns,
                    slots,
                    ProjectionRead(
                        tuple(
                            (
                                (column.slot_id, key.column)
                                for column, key in zip(
                                    domain_columns, population_schema.keys, strict=True
                                )
                            )
                        ),
                        population_schema.presence,
                    ),
                    (),
                    (),
                    None,
                    preparation_checks=population_schema.preparation_checks,
                    unique_keys=population_schema.unique_keys.project(slots, column_slots),
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
    return population_inputs, population_stages, keys


def _plan_member_counts(
    axes: tuple[ComparisonAxis, ...],
    schemas: tuple[SourceSchema, ...],
    observation: ObservationBinding | None,
    keys: tuple[DataColumn, ...],
    calendar_key: str,
    *,
    has_source_reads: bool,
) -> tuple[tuple[MemberInput, ...], tuple[DataColumn, ...], str]:
    count_inputs: list[MemberInput] = []
    for definition, member_population in (
        () if observation is None else observation.count_populations
    ):
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
        for axis in axes:
            if isinstance(axis, (CalendarCoordinate, IntervalCoordinate)):
                calendar_key = f"key.{len(output_columns)}"
                output_columns.append(
                    DataColumn(
                        f"key.{len(output_columns)}",
                        f"calendar:{axis.grain}"
                        if isinstance(axis, CalendarCoordinate)
                        else "interval",
                        time_key_type(axis),
                        False,
                    )
                )
                continue
            group = member_groups[coordinate_position(member_population.axes, axis)]
            for name in group:
                slot = f"key.{len(output_columns)}"
                item = member_schema.keys[member_slots.index(name)]
                output_columns.append(DataColumn(slot, item.field, item.dtype, item.nullable))
        if not has_source_reads:
            keys = tuple(output_columns)
        count_inputs.append(
            MemberInput(
                definition,
                member_population,
                member_slots,
                tuple(zip(member_population.axes, member_groups, strict=True)),
            )
        )
    return tuple(count_inputs), keys, calendar_key


def _bind_formula_keys(
    graph: BoundGraph,
    program: FormulaProgram,
    observation: ObservationBinding | None,
    keys: tuple[DataColumn, ...],
    count_inputs: tuple[MemberInput, ...],
    calendar_key: str,
) -> FormulaProgram:
    parent_scope = observation.coordinates.parent_scope if observation is not None else None
    output_groups: tuple[tuple[str, ...], ...] = ()
    if parent_scope is not None or program.sample_matches:
        output_groups = output_key_groups(
            graph, keys, tuple(column.slot_id for column in keys), counts=count_inputs
        )
    if parent_scope is not None:
        program = replace(
            program,
            parent_scope=parent_scope,
            parent_slots=tuple(
                slot
                for axis in parent_scope.axes
                for slot in output_groups[graph.signature.axes.index(axis)]
            ),
        )
    program = replace(
        program, counts=count_inputs, calendar_key=calendar_key, key_groups=output_groups
    )
    return program


def _publish_observation(
    graph: BoundGraph,
    base: QueryPlan,
    reads: list[SourceReadStage],
    population_stages: list[SourceReadStage | LocalCompute],
    contributions: tuple[ContributionBinding, ...],
    calendar: CalendarCoordinate | IntervalCoordinate | None,
    keys: tuple[DataColumn, ...],
    program: FormulaProgram,
    scope_id: str,
) -> QueryPlan:
    assert isinstance(graph.root, ObserveQuery)
    columns = tuple(
        replace(column, unit=quantity.unit)
        for column, quantity in zip(
            formula_columns(program), graph.signature.quantities, strict=True
        )
    )
    # A single native reduction already produces the requested final table.
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
        and not contributions[0].sample_axes
        and calendar is None
    ):
        stage = reads[0]
        assert read_reduction(stage) is not None
        stage = replace(
            stage,
            columns=(*keys, columns[0]),
            program=ReductionRead(replace(require_reduction(stage), value_slot=columns[0].slot_id)),
        )
        return replace(
            base,
            stages=(stage,),
            consumers=(),
            publish=stage.stage_id,
            original=replace(program, values=(replace(program.values[0], column=columns[0]),)),
        )
    combine = LocalCompute(
        "local:combine:" + scope_id,
        tuple(stage.stage_id for stage in (*reads, *population_stages)),
        (*keys, *columns),
        tuple(c.slot_id for c in keys),
        program,
    )
    return QueryPlan(
        (*reads, *population_stages, combine),
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


def with_unique_keys(plan: QueryPlan) -> QueryPlan:
    """Bind controlled operation facts, using upstream checks only as postconditions."""

    stages: list[QueryStage] = []
    by_id: dict[str, QueryStage] = {}
    for stage in plan.stages:
        if isinstance(stage, (LocalCompute, FrameCompute)):
            slots = tuple(c.slot_id for c in stage.columns)
            facts = stage.unique_keys.project(slots)
            source = by_id.get(stage.inputs[0]) if stage.inputs else None
            # A consumer runs only after the input's normalization has succeeded.
            incoming = source.unique_keys if source is not None else UniqueKeys()
            if source is not None and source.keys:
                incoming = incoming.add(source.keys)
            program = stage.program
            if isinstance(program, (FrameCombineProgram, FrameCompareProgram)):
                # Their kernels complete required mapping/domain checks before
                # constructing one row per unioned complete output coordinate.
                facts = UniqueKeys(slots, (stage.keys,))
            elif isinstance(program, FormulaProgram):
                # Formula completion builds a distinct domain and aligns one value per full key.
                facts = UniqueKeys(slots, (stage.keys,))
            elif isinstance(program, (ResultProgram, FrameProgram)):
                operation = program.operation
                if isinstance(operation, (ReduceValues, PopulationValues, KeyImage, ReadFields)):
                    # These controlled producers group, project/distinct, or read single-valued attributes.
                    facts = UniqueKeys(slots, (stage.keys,))
                elif isinstance(operation, (FilterValues, TopValues)):
                    facts = incoming.project(slots)
                    if isinstance(operation, TopValues) and operation.n <= 1:
                        facts = facts.add(())
                elif isinstance(operation, SelectValues):
                    input_values = (
                        program.input_schema.values
                        if isinstance(program, FrameProgram)
                        else program.input_layout.values
                        if program.input_layout is not None
                        else tuple(c for c in program.columns if c.slot_id not in program.keys)
                    )
                    output_values = (
                        program.output_schema.values
                        if isinstance(program, FrameProgram)
                        else program.output_layout.values
                        if program.output_layout is not None
                        else tuple(c for c in stage.columns if c.slot_id not in stage.keys)
                    )
                    mapping = {
                        input_values[expression.index].slot_id: column.slot_id
                        for (_, expression), column in zip(
                            operation.values, output_values, strict=True
                        )
                        if isinstance(expression, ValueSlot)
                    }
                    value_slots = {column.slot_id for column in input_values}
                    retained = tuple(
                        slot
                        for slot in incoming.slots
                        if slot not in value_slots or slot in mapping
                    )
                    facts = incoming.project(retained).project(slots, mapping)
                elif isinstance(operation, FixFrameValues) and isinstance(program, FrameProgram):
                    fixed = tuple(
                        c.slot_id for c in program.input_schema.axes[operation.position].columns
                    )
                    facts = incoming.fixed(slots, fixed)
            stage = replace(stage, unique_keys=facts)
        stages.append(stage)
        by_id[stage.stage_id] = stage
    return replace(plan, stages=tuple(stages))
