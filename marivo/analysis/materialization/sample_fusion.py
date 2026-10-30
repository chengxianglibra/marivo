"""Replace qualified sample subtrees with bounded same-owner native reads."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir
import pandas as pd

from marivo._definition.expressions import (
    ContributionFilter,
    DeclaredMetricRef,
    MemberCount,
    MetricExpression,
    MetricNode,
    MetricWindow,
    PerReduce,
    PerWeightedMean,
)
from marivo._definition.graph import children
from marivo._definition.relations import LiteralPopulation, PopulationDependency, sample_population
from marivo._definition.types import AxisBinding
from marivo._temporal import Grain
from marivo.analysis.compiler.graph_lowering import LoweredQuery, LoweredRead
from marivo.analysis.compiler.graph_plan import (
    LocalCompute,
    NativeRead,
    SourceRead,
    read_components,
    read_reduction,
)
from marivo.analysis.compiler.observation import FormulaInput, FormulaProgram
from marivo.analysis.compiler.optimization import RuleDecision, required_slots
from marivo.analysis.compiler.query_program import SourceParameter
from marivo.analysis.compiler.sample_reductions import (
    SampleLowerer,
    SampleReductionUnavailableError,
)
from marivo.analysis.core.data_types import DataColumn, ScalarDataType
from marivo.analysis.core.value_types import data_type, ibis_type
from marivo.analysis.materialization.graph_exchange import StageOutput
from marivo.analysis.materialization.graph_source_execution import PreparedSources
from marivo.analysis.materialization.query_parameters import instantiate
from marivo.datasource.adapters import SourceSession
from marivo.semantic._numeric_lowering import NumericCheck


def fuse_sample_reductions(prepared: PreparedSources, lowered: LoweredQuery) -> LoweredQuery:
    """Keep local completion and independent consumers while compacting source samples."""
    from marivo.analysis.materialization.query_key_fusion import _consumers, native_query_relation

    sources = {
        stage.stage_id: stage for stage in lowered.plan.stages if isinstance(stage, SourceRead)
    }
    stages = list(lowered.plan.stages)
    reads = list(lowered.reads)
    decisions = list(lowered.plan.optimization_decisions)
    adopted = False
    for original in lowered.plan.stages:
        if (
            not isinstance(original, LocalCompute)
            or not isinstance(original.program, FormulaProgram)
            or original.attribute is not None
        ):
            continue
        program = replace(
            original.program,
            values=tuple(
                replace(value, stage_id=identifier)
                for value, identifier in zip(
                    original.program.values,
                    original.inputs[: len(original.program.values)],
                    strict=True,
                )
            ),
        )
        values = list(program.values)
        replacements: list[SourceRead] = []
        removed: set[str] = set()
        lowerers: dict[SourceSession, SampleLowerer] = {}
        active_parameters: dict[SourceSession, dict[str, SourceParameter]] = {}

        def visit(
            node: MetricNode,
            path: tuple[int, ...],
            keys: tuple[str, ...],
            program: FormulaProgram = program,
            original: LocalCompute = original,
            replacements: list[SourceRead] = replacements,
            removed: set[str] = removed,
            reads: list[LoweredRead] = reads,
            lowerers: dict[SourceSession, SampleLowerer] = lowerers,
            active_parameters: dict[SourceSession, dict[str, SourceParameter]] = active_parameters,
        ) -> None:
            nonlocal values
            if isinstance(node, DeclaredMetricRef):
                visit(dict(program.declared)[node.ref], (*path, 0), keys)
                return
            if isinstance(node, (ContributionFilter, MetricWindow)):
                visit(node.operand, (*path, 0), keys)
                return
            if isinstance(node, MetricExpression):
                for index, child in enumerate(node.operands):
                    visit(child, (*path, index), keys)
                return
            if not isinstance(node, (PerReduce, PerWeightedMean)):
                return
            selected = tuple(
                value for value in program.values if value.occurrence[: len(path)] == path
            )
            if not selected:
                return
            if any(value.stage_id not in sources for value in selected):
                decisions.append(
                    RuleDecision(
                        "source_sample_reduction",
                        "refused",
                        tuple(value.stage_id for value in selected),
                        "Typed empty or fixed local inputs retain their execution boundary.",
                    )
                )
                return
            source_stages = tuple(sources[value.stage_id] for value in selected)
            owner = prepared.sources.get(source_stages[0].source_id, prepared.source)
            try:
                if owner.provider.name not in (
                    "duckdb",
                    "postgres",
                    "mysql",
                    "trino",
                    "clickhouse",
                ):
                    raise SampleReductionUnavailableError(
                        "provider has no qualified native sample route"
                    )
                if any(
                    prepared.sources.get(stage.source_id, prepared.source) is not owner
                    for stage in source_stages
                ):
                    raise SampleReductionUnavailableError(
                        "sample formula crosses datasource execution owners"
                    )
                if any(
                    read_components(stage) is not None
                    or stage.attribute_state
                    or stage.attribute_native
                    for stage in source_stages
                ):
                    raise SampleReductionUnavailableError(
                        "original-contribution consumers retain their complete samples"
                    )
                relations = {
                    read.stage_id: read.expression
                    for read in lowered.reads
                    if prepared.sources.get(
                        lowered.plan.source_read(read.stage_id).source_id, prepared.source
                    )
                    is owner
                }
                dependencies = {
                    parameter.stage_id: parameter
                    for stage in source_stages
                    for parameter in stage.parameters
                }
                active_parameters[owner] = dependencies
                columns = {
                    column.slot_id: column
                    for value in program.values
                    if value.stage_id in sources
                    for column in sources[value.stage_id].columns
                }

                def literal(
                    names: tuple[str, ...],
                    rows: tuple[tuple[object, ...], ...],
                    *,
                    grid: bool = False,
                ) -> ir.Table:
                    if grid:
                        from marivo.datasource._calendar_relations import calendar_data

                        schema = ibis.schema(
                            {
                                name: ibis_type(columns[name].dtype)
                                if name in columns
                                else (
                                    ibis.dtype("timestamp('UTC', 6)")
                                    if any(isinstance(row[index], datetime) for row in rows)
                                    else ibis.dtype("int64")
                                )
                                for index, name in enumerate(names)
                            }
                        )
                        return calendar_data(schema, rows)
                    fields = tuple(columns[name] for name in names)
                    table = ibis.table(
                        {column.slot_id: ibis_type(column.dtype) for column in fields},
                        name="__sample_literal",
                    )
                    frame = pd.DataFrame.from_records(rows, columns=list(names))
                    return instantiate(
                        table,
                        "__sample_literal",
                        fields,
                        StageOutput.from_frame(frame, fields, names),
                        provider=owner.provider.name,
                        source=owner,
                    )

                def population(
                    definition: PopulationDependency, names: tuple[str, ...]
                ) -> ir.Table:
                    if isinstance(definition, LiteralPopulation):
                        rows = tuple(
                            tuple(
                                component
                                for axis, value in zip(definition.axes, row, strict=True)
                                for component in (
                                    value
                                    if isinstance(axis, AxisBinding)
                                    and axis.role == "identity"
                                    and isinstance(value, tuple)
                                    else (value,)
                                )
                            )
                            for row in definition.keys
                        )
                        return literal(names, rows)
                    binding = next(
                        (item for item in program.populations if item.definition == definition),
                        None,
                    )
                    if binding is None:
                        raise SampleReductionUnavailableError(
                            "sample Population has no prepared native relation"
                        )
                    native = native_query_relation(
                        prepared,
                        lowered,
                        binding.stage_id,
                        owner,
                        shared=True,
                        allowed_consumers=frozenset({original.stage_id}),
                    )
                    if (
                        isinstance(native, str)
                        or native.checks
                        or any(read.numeric_checks for read in native.sources)
                    ):
                        raise SampleReductionUnavailableError(
                            "sample Population retains independent checks or a local boundary"
                        )
                    active_parameters[owner].update(
                        (parameter.stage_id, parameter) for parameter in native.parameters
                    )
                    return native.expression.select(
                        **{
                            name: native.expression[source]
                            for name, source in zip(names, binding.keys, strict=True)
                        }
                    )

                lowerer = lowerers.get(owner)
                if lowerer is None:
                    lowerer = SampleLowerer(
                        program,
                        relations,
                        original.keys,
                        owner.provider.name,
                        population,
                        lambda names, rows: literal(names, rows, grid=True),
                        {
                            value.stage_id: lowered.plan.source_read(value.stage_id).binding_id
                            or value.stage_id
                            for value in program.values
                            if value.stage_id in sources
                        },
                        {identifier: source.unique_keys for identifier, source in sources.items()},
                    )
                    lowerers[owner] = lowerer
                result = lowerer.lower(node, path, keys)
                identifier = "source:sample:" + original.stage_id + ":" + ".".join(map(str, path))
                slot = "sample:" + ".".join(map(str, path))
                expression = result.table.rename({slot: result.value})
                checks = tuple(
                    (name, NumericCheck(expression[name], expected, received))
                    for name, expected, received in result.checks
                )
                from marivo.analysis.materialization.query_key_fusion import (
                    NativeQueryRelation,
                    _attach_relation_checks,
                )

                preparation_sources = tuple(
                    replace(
                        lowered.read(stage.stage_id),
                        numeric_checks=tuple(
                            (name, check)
                            for name, check in lowered.read(stage.stage_id).numeric_checks
                            if name in {name for name, _, _ in stage.preparation_checks}
                        ),
                    )
                    for stage in source_stages
                    if stage.preparation_checks
                )
                expression, preparation, checked = _attach_relation_checks(
                    expression,
                    NativeQueryRelation(expression, frozenset(), preparation_sources),
                    lowered.plan,
                    numeric=checks,
                    prepared=prepared,
                )
                checks = checked
                output_columns = (
                    *(columns[key] for key in keys),
                    DataColumn(slot, slot, data_type(expression[slot].type()), True),
                    *(
                        DataColumn(name, name, ScalarDataType("boolean"), False)
                        for name, _, _ in result.checks
                    ),
                )
                source = SourceRead(
                    identifier,
                    source_stages[0].source_id,
                    path,
                    output_columns,
                    keys,
                    NativeRead(
                        tuple(
                            reduction
                            for stage in source_stages
                            if (reduction := read_reduction(stage)) is not None
                        )
                    ),
                    (),
                    (),
                    None,
                    preparation_checks=preparation,
                    inputs=tuple(dependencies),
                    parameters=tuple(dependencies.values()),
                    native_reads=tuple(dict.fromkeys(source_stages)),
                    unique_keys=result.unique_keys.project(
                        tuple(column.slot_id for column in output_columns), {result.value: slot}
                    ),
                )
                replacements.append(source)
                reads.append(LoweredRead(identifier, expression, checks))
                values = [value for value in values if value not in selected]
                values.append(
                    FormulaInput(
                        path,
                        identifier,
                        output_columns[len(keys)],
                        result.zero_when_absent,
                        result.checks,
                        keys,
                        tuple(
                            group
                            for group in selected[0].key_groups
                            if all(key in keys for key in group)
                        ),
                    )
                )
                removed.update(value.stage_id for value in selected)
                decisions.append(
                    RuleDecision(
                        "source_sample_reduction",
                        "adopted",
                        tuple(value.stage_id for value in selected),
                        "Completed sample Metric, exact domain and qualified reducer execute on one source owner.",
                    )
                )
                return
            except SampleReductionUnavailableError as error:
                decisions.append(
                    RuleDecision(
                        "source_sample_reduction",
                        "refused",
                        tuple(value.stage_id for value in selected),
                        error.received or "sample relation is unavailable",
                    )
                )
            remaining = tuple(
                group for group in selected[0].key_groups if not all(key in keys for key in group)
            )
            count = (
                len(node.samples)
                if isinstance(node.samples, tuple)
                else len(node.samples.axes)
                if not isinstance(node.samples, Grain)
                else 1
            )
            child_keys = (*keys, *(key for group in remaining[:count] for key in group))
            operands = (node.operand,) if isinstance(node, PerReduce) else (node.value, node.weight)
            for index, child in enumerate(operands):
                visit(child, (*path, index), child_keys)

        for index, (_, node) in enumerate(program.outputs):
            visit(node, (index,), original.keys)
        if not replacements:
            continue
        adopted = True
        replacements, reads, values = _share_folds(replacements, reads, values, prepared)
        # Keep sources with other consumers, or with unoptimized sibling occurrences.
        used = {value.stage_id for value in values}
        removed = {
            identifier
            for identifier in removed
            if identifier not in used
            and set(lowered.plan.consumers_of(identifier)) <= {original.stage_id}
        }
        required_domains = _remaining_domains(program, tuple(values))
        populations = tuple(
            item for item in program.populations if item.definition in required_domains
        )
        updated = replace(
            original,
            inputs=tuple(value.stage_id for value in values)
            + tuple(item.stage_id for item in populations),
            program=replace(program, values=tuple(values), populations=populations),
        )
        position = next(
            index for index, stage in enumerate(stages) if stage.stage_id == original.stage_id
        )
        stages[position : position + 1] = [*replacements, updated]
        stages = [stage for stage in stages if stage.stage_id not in removed]
        reads = [read for read in reads if read.stage_id not in removed]
    if not adopted:
        return lowered
    retained: set[str] = set()
    by_id = {stage.stage_id: stage for stage in stages}

    def retain(identifier: str) -> None:
        if identifier in retained:
            return
        retained.add(identifier)
        for dependency in by_id[identifier].inputs:
            retain(dependency)

    retain(lowered.plan.publish)
    stages = [stage for stage in stages if stage.stage_id in retained]
    reads = [read for read in reads if read.stage_id in retained]
    plan = replace(
        lowered.plan,
        stages=tuple(stages),
        consumers=_consumers(tuple(stages)),
        optimization_decisions=tuple(decisions),
    )
    return LoweredQuery(replace(plan, required_slots=required_slots(plan)), tuple(reads))


def _share_folds(
    stages: list[SourceRead],
    reads: list[LoweredRead],
    values: list[FormulaInput],
    prepared: PreparedSources,
) -> tuple[list[SourceRead], list[LoweredRead], list[FormulaInput]]:
    """Share an actual aggregate, rather than repeated references to a CTE."""
    payloads = {read.stage_id: read for read in reads}
    groups: dict[
        tuple[SourceSession, ops.Relation, tuple[str, ...], tuple[SourceParameter, ...]],
        list[SourceRead],
    ] = {}
    aggregates: dict[str, tuple[ops.Aggregate, dict[str, str]]] = {}
    for stage in stages:
        projected = _aggregate_projection(payloads[stage.stage_id].expression.op())
        if projected is None:
            continue
        aggregate, _ = projected
        aggregates[stage.stage_id] = projected
        groups.setdefault(
            (
                prepared.sources.get(stage.source_id, prepared.source),
                aggregate.parent,
                stage.keys,
                stage.parameters,
            ),
            [],
        ).append(stage)
    for members in groups.values():
        if len(members) < 2:
            continue
        metrics: dict[str, ops.Value] = {}
        projections: dict[str, ops.Value] = {}
        first = members[0]
        reference: ops.Aggregate | None = None
        for stage in members:
            reference, _ = aggregates[stage.stage_id]
            metrics.update(reference.metrics)
        assert reference is not None
        quantiles: dict[tuple[ops.Value, ops.Value | None], list[ops.Quantile]] = {}
        for metric in metrics.values():
            for quantile in metric.find(ops.Quantile):
                quantiles.setdefault((quantile.arg, quantile.where), []).append(quantile)
        for (argument, where), candidates in quantiles.items():
            candidates = list(dict.fromkeys(candidates))
            if len(candidates) < 2:
                continue
            probabilities = [candidate.quantile.to_expr() for candidate in candidates]
            multiple = ops.MultiQuantile(argument, ibis.array(probabilities).op(), where).to_expr()
            replacement = {
                candidate: multiple[index].op() for index, candidate in enumerate(candidates)
            }
            metrics = {name: metric.replace(replacement) for name, metric in metrics.items()}
        aggregate = ops.Aggregate(reference.parent, reference.groups, metrics)
        for stage in members:
            _, aliases = aggregates[stage.stage_id]
            projections.update(
                (name, ops.Field(aggregate, source)) for name, source in aliases.items()
            )
        combined = aggregate.to_expr().select(
            **{name: value.to_expr() for name, value in projections.items()}
        )
        checks = tuple(
            check for stage in members for check in payloads[stage.stage_id].numeric_checks
        )
        all_columns = tuple(
            {column.slot_id: column for stage in members for column in stage.columns}.values()
        )
        shared = replace(
            first,
            columns=all_columns,
            native_reads=tuple(
                dict.fromkeys(read for stage in members for read in stage.native_reads)
            ),
            program=NativeRead(
                tuple(
                    reduction
                    for stage in members
                    if isinstance(stage.program, NativeRead)
                    for reduction in stage.program.reductions
                )
            ),
        )
        identifiers = {stage.stage_id for stage in members}
        stages = [
            shared if stage.stage_id == first.stage_id else stage
            for stage in stages
            if stage.stage_id == first.stage_id or stage.stage_id not in identifiers
        ]
        reads = [read for read in reads if read.stage_id not in identifiers]
        reads.append(
            LoweredRead(
                first.stage_id,
                combined,
                tuple(
                    (name, NumericCheck(combined[name], check.expected, check.received))
                    for name, check in checks
                ),
            )
        )
        values = [
            replace(value, stage_id=first.stage_id) if value.stage_id in identifiers else value
            for value in values
        ]
    return stages, reads, values


def _remaining_domains(
    program: FormulaProgram, values: tuple[FormulaInput, ...]
) -> set[PopulationDependency]:
    required = {
        item for item in (program.output_population, program.parent_scope) if item is not None
    }
    required.update(count.population for count in program.counts)
    complete = {value.occurrence for value in values}
    declared = dict(program.declared)

    def visit(node: MetricNode, path: tuple[int, ...]) -> None:
        if path in complete:
            return
        if isinstance(node, DeclaredMetricRef):
            visit(declared[node.ref], (*path, 0))
            return
        if isinstance(node, MemberCount):
            required.add(node.population)
        if isinstance(node, (PerReduce, PerWeightedMean)):
            definition = sample_population(node.samples)
            if definition is not None:
                required.add(definition)
        for edge in children(node):
            if isinstance(edge.child, MetricNode):
                visit(edge.child, (*path, edge.index))

    for index, (_, node) in enumerate(program.outputs):
        visit(node, (index,))
    return required


def _aggregate_projection(relation: ops.Relation) -> tuple[ops.Aggregate, dict[str, str]] | None:
    """Unwrap only exact field projections; retain every other execution boundary."""
    if isinstance(relation, ops.Aggregate):
        return relation, {name: name for name in relation.schema}
    if not isinstance(relation, ops.Project):
        return None
    parent = _aggregate_projection(relation.parent)
    if parent is None:
        return None
    aggregate, names = parent
    aliases: dict[str, str] = {}
    for name, value in relation.values.items():
        if not isinstance(value, ops.Field) or value.rel != relation.parent:
            return None
        aliases[name] = names[value.name]
    return aggregate, aliases
