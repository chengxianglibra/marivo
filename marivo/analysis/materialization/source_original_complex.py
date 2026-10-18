"""Transport grouped leaves for qualified sampled and windowed original operations."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Literal

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.types as ir
import pandas as pd

from marivo._definition.expressions import (
    DeclaredMetricRef,
    EntityCount,
    MetricNode,
    PerReduce,
    PerWeightedMean,
    SourceAggregate,
    SourceWeightedMean,
)
from marivo._definition.graph import children
from marivo._definition.query import (
    CoordinateSlot,
    DrillValues,
    FilterValues,
    RollupValues,
    TopValues,
    ValueBinary,
    ValueExpression,
    ValueLiteral,
)
from marivo._definition.relations import PopulationProgram
from marivo.analysis.compiler.graph_lowering import LoweredQuery, LoweredRead
from marivo.analysis.compiler.graph_plan import LocalCompute, SourceRead
from marivo.analysis.compiler.native_formula import (
    NativeOriginalBatchFinish,
    NativeOriginalLeaf,
    NativeOriginalPhase,
    NativeOriginalPhaseFinish,
    NativeOriginalState,
)
from marivo.analysis.compiler.observation import FormulaProgram, PopulationInput, ReductionInput
from marivo.analysis.compiler.optimization import RuleDecision, required_slots
from marivo.analysis.core.data_types import DataColumn
from marivo.semantic._numeric_lowering import lower_row

if TYPE_CHECKING:
    from marivo.analysis.materialization.graph_source_execution import PreparedSources


def _append_batch(
    batches: list[ir.Table],
    types: dict[dt.DataType, int],
    expression: ir.Table,
    states: tuple[NativeOriginalState, ...] = (),
    keys: tuple[DataColumn, ...] = (),
) -> NativeOriginalLeaf:
    positions: dict[dt.DataType, int] = {}
    columns: list[tuple[str, str]] = []
    for name in expression.columns:
        dtype = expression[name].type().copy(nullable=True)
        group = types.setdefault(dtype, len(types))
        position = positions.get(dtype, 0)
        positions[dtype] = position + 1
        columns.append((f"__batch.{group}.{position}", name))
    names = tuple(columns)
    batches.append(expression.select(**{name: expression[original] for name, original in names}))
    return NativeOriginalLeaf(len(batches) - 1, names, states, keys)


def _fixed_coordinates(
    expression: ValueExpression, groups: tuple[tuple[str, ...], ...]
) -> set[str]:
    if not isinstance(expression, ValueBinary):
        return set()
    if expression.operation == "and":
        return _fixed_coordinates(expression.left, groups) | _fixed_coordinates(
            expression.right, groups
        )
    if expression.operation != "eq":
        return set()
    for coordinate, literal in (
        (expression.left, expression.right),
        (expression.right, expression.left),
    ):
        if isinstance(coordinate, CoordinateSlot) and isinstance(literal, ValueLiteral):
            return set(groups[coordinate.index]) if len(groups[coordinate.index]) == 1 else set()
    return set()


def _single_parent_selection(
    selections: tuple[LocalCompute, ...], kept: tuple[str, ...], parent_keys: tuple[str, ...]
) -> bool:
    """Prove each target coordinate consumes at most one selected fine parent."""
    fixed: set[str] = set()
    for stage in selections:
        assert stage.result is not None
        operation = stage.result.operation
        if isinstance(operation, TopValues):
            partitions = {
                key for index in operation.partitions for key in stage.result.key_groups[index]
            }
            if operation.n <= 1 and partitions <= set(kept):
                return True
        elif (
            isinstance(operation, FilterValues)
            and not operation.domains
            and isinstance(operation.predicate, ValueExpression)
        ):
            fixed |= _fixed_coordinates(operation.predicate, stage.result.key_groups)
    return bool(selections) and set(parent_keys) - set(kept) <= fixed


def _used_sample_populations(program: FormulaProgram) -> tuple[PopulationInput, ...]:
    """Keep domains consumed by the formula; their source dependencies stay in SQL."""
    required: list[PopulationProgram] = []
    visited: set[int] = set()
    declarations = dict(program.declared)

    def visit(node: MetricNode) -> None:
        if id(node) in visited:
            return
        visited.add(id(node))
        if isinstance(node, DeclaredMetricRef):
            visit(declarations[node.ref])
            return
        if isinstance(node, (PerReduce, PerWeightedMean)) and isinstance(
            node.samples, PopulationProgram
        ):
            required.append(node.samples)
        for edge in children(node):
            if isinstance(edge.child, MetricNode):
                visit(edge.child)

    for _, node in program.outputs:
        visit(node)
    return tuple(
        population for population in program.populations if population.definition in required
    )


def _lower_complex_original(
    prepared: PreparedSources,
    lowered: LoweredQuery,
    *,
    phase: Literal["all", "parent", "target"] = "all",
    selected_domain: ir.Table | None = None,
) -> LoweredQuery | None:
    """Keep contributions at their source and finish formulas at their original deadline.

    Spatial rollups, finite parent selections and drills transport complete
    grouped sample leaves. Captured sample domains and window occurrences share
    the same statement. The existing host kernels finish and check each phase.
    """
    from marivo.analysis.materialization.source_original import _NativeOriginal, _Relation

    native = _NativeOriginal(prepared, lowered)
    stages = native.stages
    final = stages[lowered.plan.publish]
    if (
        not isinstance(final, LocalCompute)
        or final.result is None
        or not isinstance(final.result.operation, (RollupValues, DrillValues))
        or (
            isinstance(final.result.operation, RollupValues)
            and final.result.operation.calendar_maps
        )
        or (isinstance(final.result.operation, RollupValues) and len(final.inputs) != 1)
    ):
        return None
    parent = stages[final.inputs[0]]
    selected = []
    while (
        isinstance(parent, LocalCompute)
        and parent.result is not None
        and isinstance(parent.result.operation, (FilterValues, TopValues))
    ):
        if len(parent.inputs) != 1 or parent.fused_steps:
            return None
        selected.append(parent)
        parent = stages[parent.inputs[0]]
    selections = tuple(reversed(selected))
    intermediate_stages = []
    while (
        isinstance(parent, LocalCompute)
        and parent.result is not None
        and isinstance(parent.result.operation, RollupValues)
    ):
        if selections or parent.result.operation.calendar_maps or len(parent.inputs) != 1:
            return None
        intermediate_stages.append(parent)
        parent = stages[parent.inputs[0]]
    if not isinstance(parent, LocalCompute) or parent.program is None or parent.fused_steps:
        return None
    program = parent.program
    if program.output_population is not None or program.members is not None or not program.values:
        return None
    reads: list[SourceRead] = []
    for binding in program.values:
        stage = stages.get(binding.stage_id)
        if (
            not isinstance(stage, SourceRead)
            or stage.reduction is None
            or stage.component_layout is None
            or stage.common_reads
            or stage.inputs
            or stage.parameters
            or not stage.binding_id
            or stage.reduction.duration is not None
        ):
            return None
        node = stage.reduction.node
        if not isinstance(node, (SourceAggregate, SourceWeightedMean, EntityCount)):
            return None
        if isinstance(node, SourceAggregate) and node.reducer not in (
            "sum",
            "count",
            "count_distinct",
            "min",
            "max",
            "mean",
            "median",
            "quantile",
        ):
            return None
        table = prepared.relations.get(stage.stage_id)
        if table is None:
            return None
        fields = {path: table[name] for path, name in stage.field_columns}
        operands = (
            (node.value, node.weight)
            if isinstance(node, SourceWeightedMean)
            else (node.operand,)
            if isinstance(node, SourceAggregate)
            else ()
        )
        if any(not lower_row(operand, fields).value.type().is_numeric() for operand in operands):
            return None
        reads.append(stage)
    if not any(
        read.reduction is not None
        and (read.reduction.sampled or read.reduction.calendars or read.reduction.calendar)
        for read in reads
    ):
        return None
    owner = prepared.sources.get(reads[0].source_id, prepared.source)
    if owner.provider.name not in ("duckdb", "trino", "clickhouse") or any(
        prepared.sources.get(read.source_id, prepared.source) is not owner for read in reads
    ):
        return None
    if owner.provider.name != "duckdb" and (
        (selections and phase == "all")
        or intermediate_stages
        or program.populations
        or isinstance(final.result.operation, DrillValues)
    ):
        return None
    if set(parent.inputs) != {read.stage_id for read in reads} | {
        population.stage_id for population in program.populations
    }:
        return None
    program = replace(program, populations=_used_sample_populations(program))
    drilling = isinstance(final.result.operation, DrillValues)
    if phase != "all" and (
        not selections or intermediate_stages or drilling or program.populations
    ):
        return None
    if phase == "target" and (
        selected_domain is None or tuple(selected_domain.columns) != parent.keys
    ):
        return None
    if intermediate_stages and drilling:
        return None
    phase_stages = (parent, *reversed(intermediate_stages), final)
    mappings: list[dict[str, str]] = [{key: key for key in parent.keys}]
    domains: list[tuple[tuple[str, str], ...]] = []
    for phase_stage in phase_stages[1:]:
        assert phase_stage.result is not None
        operation = phase_stage.result.operation
        assert isinstance(operation, (RollupValues, DrillValues))
        kept = (
            parent.keys
            if isinstance(operation, DrillValues)
            else tuple(
                key
                for position in operation.positions
                for key in phase_stage.result.key_groups[position]
            )
        )
        previous = mappings[-1]
        inverse = {value: key for key, value in previous.items()}
        mapping = {
            inverse[key]: slot
            for key, slot in zip(kept, phase_stage.keys[: len(kept)], strict=True)
        }
        if program.calendar is not None and program.calendar_key not in mapping:
            return None
        mappings.append(mapping)
        domains.append(tuple((previous[key], value) for key, value in mapping.items()))
    output_mapping = mappings[-1]
    if (
        phase == "all"
        and selections
        and not _single_parent_selection(selections, tuple(output_mapping), parent.keys)
    ):
        return None
    added_keys = final.keys[len(parent.keys) :] if drilling else ()
    selection_mapping = (
        {
            key: f"key.{len(final.keys) + max(len(read.keys) for read in reads) + index}"
            for index, key in enumerate(parent.keys)
        }
        if selections and phase == "all"
        else {}
    )
    fibers = []
    for read in reads:
        domain = selected_domain if phase == "target" else None
        selected_axes = None
        if domain is not None:
            assert read.reduction is not None
            reduction = read.reduction
            selected_axes = tuple(axis for axis in reduction.axes if axis[0] in parent.keys)
            missing = set(parent.keys) - {key for key, _ in selected_axes}
            if missing:
                if (
                    missing != {program.calendar_key}
                    or reduction.window_key is None
                    or reduction.calendar is None
                ):
                    return None
                domain = domain.filter(domain[program.calendar_key] == reduction.window_key)
        fiber = native.prepare_fiber((read,), domain, sampled=True, selected_axes=selected_axes)
        if isinstance(fiber, str):
            return None
        fibers.append(fiber)
    batches: list[ir.Table] = []
    phases: list[NativeOriginalPhase] = []
    transport_types: dict[dt.DataType, int] = {}
    population_batches: list[NativeOriginalLeaf] = []
    population_reads: list[SourceRead] = []
    population_reductions: list[ReductionInput] = []
    if program.populations:
        from marivo.analysis.materialization.query_key_fusion import native_query_relation

        for population in program.populations:
            domain = native_query_relation(
                prepared, lowered, population.stage_id, owner, terminal=True, shared=True
            )
            if isinstance(domain, str):
                return None
            if domain.checks:
                return None
            from marivo.analysis.materialization.graph_source_execution import (
                qualify_original_source,
            )

            if (
                qualify_original_source(
                    prepared, tuple(source.stage for source in domain.sources), domain.expression
                )
                is not None
            ):
                return None
            population_reductions.extend(domain.reductions)
            for source in domain.sources:
                native.summary(source.expression, source.numeric_checks)
                population_reads.append(source.stage)
            stage = stages[population.stage_id]
            population_batches.append(
                _append_batch(
                    batches,
                    transport_types,
                    domain.expression.select(*population.keys).distinct(),
                    keys=tuple(
                        column for column in stage.columns if column.slot_id in population.keys
                    ),
                )
            )
    for phase_index, phase_stage in enumerate(phase_stages):
        if (phase == "parent" and phase_index != 0) or (
            phase == "target" and phase_index != len(phase_stages) - 1
        ):
            continue
        target = phase_index != 0
        output_mapping = mappings[phase_index]
        bindings = []
        leaves = []
        for binding, read, fiber in zip(program.values, reads, fibers, strict=True):
            assert read.reduction is not None
            reduction = read.reduction
            added_axes: tuple[tuple[str, str], ...] = ()
            if target and drilling:
                relation = _Relation(
                    fiber.raw, fiber.raw, fiber.axes, program, (read,), owner, (fiber,)
                )
                drilled = native.drill_fiber(final, relation, fiber, retain_checks=True)
                if isinstance(drilled, str):
                    return None
                added_axes = tuple(
                    zip(
                        added_keys,
                        (path for _, path in drilled.axes[len(fiber.axes) :]),
                        strict=True,
                    )
                )
                fiber = drilled
                read = drilled.reads[0]
            mapping = (
                {
                    **output_mapping,
                    **{
                        key: name
                        for key, name in selection_mapping.items()
                        if key not in output_mapping
                    },
                    **{
                        key: f"key.{len(phase_stage.keys) + index}"
                        for index, key in enumerate(
                            key for key in read.keys if key not in parent.keys
                        )
                    },
                }
                if target
                else {key: key for key in read.keys}
            )
            calendars = reduction.calendars or (
                ((program.calendar_key, reduction.calendar),)
                if reduction.calendar is not None
                else ()
            )
            if any(key not in mapping for key, _ in calendars):
                return None
            axes = (
                *tuple((mapping[key], path) for key, path in reduction.axes if key in mapping),
                *added_axes,
            )
            raw = fiber.raw
            if target:
                relation = _Relation(raw, raw, fiber.axes, program, (read,), owner, (fiber,))
                dedup_axes = axes
                if reduction.time_column is not None:
                    dedup_axes = (*axes, ("__calendar_time", reduction.time_column))
                raw = native.deduplicate(relation, dedup_axes, fiber)
            reduction = replace(
                reduction,
                axes=axes,
                calendars=tuple((mapping[key], calendar) for key, calendar in calendars),
            )
            read = replace(read, reduction=reduction)
            completed = native.complete_leaf(
                read,
                raw,
                axes,
                terminal=target,
                state_offset=len(batches),
                retain_checks=True,
                row_checks=fiber.checks,
            )
            if isinstance(completed, str):
                return None
            expression, states, checks = completed
            if target and selection_mapping:
                expression = expression.mutate(
                    **{name: expression[mapping[key]] for key, name in selection_mapping.items()}
                )
            keys = tuple(
                mapping[key]
                for key in binding.keys
                if key in mapping
                and (not target or key not in parent.keys or key in output_mapping)
            )
            bindings.append(
                replace(
                    binding,
                    keys=(*keys, *added_keys) if target else keys,
                    checks=tuple((name, check.expected, check.received) for name, check in checks),
                    key_groups=(
                        *tuple(
                            tuple(mapping[key] for key in group)
                            for group in binding.key_groups
                            if all(
                                key in mapping
                                and (not target or key not in parent.keys or key in output_mapping)
                                for key in group
                            )
                        ),
                        *(((key,) for key in added_keys) if target else ()),
                    ),
                    coverage=tuple(
                        replace(
                            coverage,
                            key=mapping.get(coverage.key, coverage.key),
                            output_slot=mapping.get(coverage.output_slot, coverage.output_slot),
                        )
                        for coverage in binding.coverage
                    ),
                )
            )
            # Reuse typed column positions across tagged leaves. Allocating a
            # distinct sparse column for every leaf would make endpoint UNION
            # width, and therefore generated SQL size, grow quadratically.
            leaves.append(
                _append_batch(
                    batches,
                    transport_types,
                    expression,
                    states,
                    (
                        *tuple(
                            replace(column, slot_id=mapping[column.slot_id])
                            for column in read.columns
                            if column.slot_id in mapping
                        ),
                        *(
                            column
                            for column in final.columns
                            if target and column.slot_id in added_keys
                        ),
                        *(
                            replace(column, slot_id=selection_mapping[column.slot_id])
                            for column in parent.columns
                            if target
                            and column.slot_id in selection_mapping
                            and column.slot_id in output_mapping
                        ),
                    ),
                )
            )
        keys = phase_stage.keys
        columns = phase_stage.columns
        phases.append(
            NativeOriginalPhase(
                replace(
                    program,
                    values=tuple(bindings),
                    calendar_key=output_mapping.get(program.calendar_key, program.calendar_key)
                    if target
                    else program.calendar_key,
                ),
                keys,
                tuple(column for column in columns if column.slot_id not in keys),
                tuple(leaves),
                tuple(population_batches),
            )
        )
    domain_leaf = None
    if phase == "target" and final.keys:
        assert selected_domain is not None
        domain_leaf = _append_batch(
            batches,
            transport_types,
            selected_domain.select(
                **{target: selected_domain[source] for source, target in domains[-1]}
            ).distinct(),
            keys=tuple(column for column in final.columns if column.slot_id in final.keys),
        )
    schema = {
        name: table[name].type().copy(nullable=True) for table in batches for name in table.columns
    }
    padded = tuple(
        table.select(
            __original_batch=ibis.literal(index),
            **{
                name: table[name].cast(dtype)
                if name in table.columns
                else ibis.null().cast(dtype.copy(nullable=True))
                for name, dtype in schema.items()
            },
        )
        for index, table in enumerate(batches)
    )
    expression = ibis.union(*padded, distinct=False).mutate(__numeric_present=ibis.literal(True))
    for summary in native.summaries:
        expression = summary.left_join(expression, ibis.literal(True)).select(
            *summary.columns, *expression.columns
        )
    first = reads[0]
    output_stage = parent if phase == "parent" else final
    read = SourceRead(
        output_stage.stage_id,
        first.source_id,
        first.occurrence,
        output_stage.columns,
        output_stage.keys,
        (),
        (),
        first.predicates,
        first.period,
        native_reductions=(
            *tuple(read.reduction for read in reads if read.reduction is not None),
            *population_reductions,
        ),
        native_original_finish=(
            NativeOriginalBatchFinish(
                phases[0],
                phases[-1],
                domains[-1],
                selections,
                tuple(selection_mapping.items()),
                drilling,
                tuple(phases[1:-1]),
                tuple(domains[:-1]),
            )
            if phase == "all"
            else NativeOriginalPhaseFinish(phases[0], domain_leaf)
        ),
        binding_id=first.binding_id,
        native_reads=(*reads, *population_reads),
    )
    plan = replace(
        lowered.plan,
        stages=(read,),
        publish=read.stage_id,
        consumers=((read.stage_id, ()),),
        original=None,
        required_slots=(),
        fusion_groups=(),
        optimization_decisions=(
            *lowered.plan.optimization_decisions,
            RuleDecision(
                "source_original",
                "adopted",
                tuple(stages),
                f"One {owner.provider.name} statement transports grouped sample leaves and captured domains; existing host formulas preserve every phase and selection deadline.",
            ),
        ),
    )
    plan = replace(plan, required_slots=required_slots(plan))
    return LoweredQuery(plan, (LoweredRead(read, expression, tuple(native.checks)),))


def fuse_complex_original(prepared: PreparedSources, lowered: LoweredQuery) -> LoweredQuery | None:
    """Transport both spatial phases when one source batch can finish them."""
    return _lower_complex_original(prepared, lowered)


def build_dependent_complex_parent(
    prepared: PreparedSources, lowered: LoweredQuery, parent: LocalCompute
) -> LoweredRead | str:
    """Complete all sampled parent checks before a host selection runs."""
    result = _lower_complex_original(prepared, lowered, phase="parent")
    if result is None or result.reads[0].stage.stage_id != parent.stage_id:
        return "dependent sample parent requires a plain same-owner spatial rollup"
    return result.reads[0]


def build_dependent_complex_target(
    prepared: PreparedSources,
    lowered: LoweredQuery,
    target: LocalCompute,
    selected: ir.Table,
) -> LoweredRead | str:
    """Regroup selected original identities before finishing target samples."""
    result = _lower_complex_original(prepared, lowered, phase="target", selected_domain=selected)
    if result is None or result.reads[0].stage.stage_id != target.stage_id:
        return "dependent sample target requires fixed complete parent keys and preserved window endpoints"
    return result.reads[0]


def _leaf_frame(leaf: NativeOriginalLeaf, frame: pd.DataFrame) -> pd.DataFrame:
    return (
        frame.loc[frame["__original_batch"] == leaf.tag, [name for name, _ in leaf.columns]]
        .rename(columns=dict(leaf.columns))
        .reset_index(drop=True)
    )


def _finish_phase(
    phase: NativeOriginalPhase,
    inputs: tuple[pd.DataFrame, ...],
    domain: pd.DataFrame | None,
) -> pd.DataFrame:
    from marivo.analysis.methods.local import _combine_program
    from marivo.semantic._numeric_lowering import numeric_failure

    result, checks = _combine_program(phase.program, inputs, phase.keys, phase.outputs, domain)
    for name, expected, received in checks:
        if result[name].astype("boolean").fillna(False).any():
            numeric_failure(expected, received)
    return result.loc[:, [*phase.keys, *(column.slot_id for column in phase.outputs)]]


def finish_original_phase(finish: NativeOriginalPhaseFinish, frame: pd.DataFrame) -> pd.DataFrame:
    """Use the owning sample formula for one compact source phase."""
    from marivo.analysis.materialization.graph_exchange import source_columns
    from marivo.analysis.materialization.source_original import finish_native_states

    inputs = []
    for leaf in (*finish.phase.leaves, *finish.phase.populations):
        data = _leaf_frame(leaf, frame)
        source_columns(data, leaf.keys, tuple(column.slot_id for column in leaf.keys))
        inputs.append(finish_native_states(leaf.states, data))
    domain = None
    if finish.domain is not None:
        domain = _leaf_frame(finish.domain, frame)
        source_columns(domain, finish.domain.keys, finish.phase.keys)
    return _finish_phase(finish.phase, tuple(inputs), domain)


def finish_original_batch(finish: NativeOriginalBatchFinish, frame: pd.DataFrame) -> pd.DataFrame:
    """Finish fine checks before the target formula over compact source leaves."""
    from marivo.analysis.materialization.graph_exchange import normalize_stage_frame, source_columns
    from marivo.analysis.materialization.source_original import finish_native_states
    from marivo.analysis.methods.query_values import evaluate_result

    parent: pd.DataFrame | None = None
    for phase_index, phase in enumerate((finish.parent, *finish.intermediates, finish.target)):
        inputs = []
        for leaf in (*phase.leaves, *phase.populations):
            data = _leaf_frame(leaf, frame)
            if parent is not None and finish.selections and leaf in phase.leaves:
                carrier_names = {name for _, name in finish.selection_keys}
                carriers = tuple(column for column in leaf.keys if column.slot_id in carrier_names)
                carrier_columns = source_columns(data, carriers, tuple(carrier_names))
                normalize_stage_frame(
                    data.loc[:, [column.slot_id for column in carriers]], carrier_columns, ()
                )
                selected = parent.loc[:, [key for key, _ in finish.selection_keys]].rename(
                    columns=dict(finish.selection_keys)
                )
                data = data.merge(
                    selected,
                    on=[name for _, name in finish.selection_keys],
                    how="inner",
                    validate="many_to_one",
                )
            source_columns(data, leaf.keys, tuple(column.slot_id for column in leaf.keys))
            inputs.append(finish_native_states(leaf.states, data))
        domain = None
        if parent is not None and finish.target_domain_from_leaves:
            domain = pd.concat(
                [data.loc[:, list(phase.keys)] for data in inputs[: len(phase.leaves)]],
                ignore_index=True,
            ).drop_duplicates()
        elif parent is not None:
            mapping = (*finish.intermediate_domains, finish.domain_mapping)[phase_index - 1]
            domain = (
                parent.loc[:, [key for key, _ in mapping]]
                .rename(columns=dict(mapping))
                .drop_duplicates()
                if phase.keys
                else pd.DataFrame(index=pd.RangeIndex(1))
            )
        parent = _finish_phase(phase, tuple(inputs), domain)
        if phase is finish.parent:
            for selection in finish.selections:
                parent = evaluate_result(selection, (parent,))
    assert parent is not None
    return parent
