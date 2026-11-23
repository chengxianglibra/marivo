"""Replace qualified sample subtrees with bounded same-owner native reads."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

import ibis
import ibis.expr.operations as ops

from marivo._definition.expressions import (
    DeclaredMetricRef,
    MemberCount,
    MetricNode,
    PerReduce,
    PerWeightedMean,
)
from marivo._definition.graph import children, walk
from marivo._definition.query import PopulationValues
from marivo._definition.relations import (
    PopulationDependency,
    SourceIdentities,
    condition_populations,
    sample_population,
)
from marivo.analysis.binding.programs import FormulaProgram
from marivo.analysis.compiler.lowered_query import LoweredQuery, LoweredRead
from marivo.analysis.compiler.optimization import required_slots
from marivo.analysis.compiler.query_program import ResultProgram, SourceParameter
from marivo.analysis.compiler.query_stages import (
    LocalCompute,
    NativeRead,
    QueryStage,
    RuleDecision,
    SourceReadStage,
    read_members,
)
from marivo.analysis.materialization.native_query_checks import (
    _population_check_summary,
)
from marivo.analysis.materialization.prepared_sources import PreparedSources
from marivo.analysis.materialization.query_plan_transport import _consumers
from marivo.analysis.materialization.sample_candidates import SampleCandidates
from marivo.analysis.model.data_types import DataColumn, DataType
from marivo.analysis.model.programs import FormulaInput
from marivo.datasource.spi import SourceSession
from marivo.semantic.spi import NumericCheck


def fuse_sample_reductions(prepared: PreparedSources, lowered: LoweredQuery) -> LoweredQuery:
    """Keep local completion and independent consumers while compacting source samples."""

    sources = {
        stage.stage_id: stage for stage in lowered.plan.stages if isinstance(stage, SourceReadStage)
    }
    stages = list(lowered.plan.stages)
    reads = list(lowered.reads)
    checks_by_id: dict[str, SourceReadStage] = {}
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
        candidates = SampleCandidates(
            prepared, lowered, original, program, sources, reads, decisions, list(program.values)
        )
        for index, (_, node) in enumerate(program.outputs):
            candidates.visit(node, (index,), original.keys)
        values = candidates.values
        replacements, removed = candidates.replacements, candidates.removed
        if not replacements:
            continue
        adopted = True
        if lowered.plan.optimization_policy.allows("source_sample_reduction"):
            replacements, reads, values = _share_folds(
                replacements, reads, values, prepared, candidates.cse_keys, decisions
            )
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
        check_stages = _membership_checks(prepared, lowered, program, required_domains)
        check_ids = tuple(stage.stage_id for stage, _ in check_stages)
        replacements = [
            replace(stage, inputs=(*stage.inputs, *check_ids)) for stage in replacements
        ]
        for check_stage, read in check_stages:
            if check_stage.stage_id not in checks_by_id:
                checks_by_id[check_stage.stage_id] = check_stage
                reads.append(read)
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
    return _publish_sample_plan(lowered, stages, reads, checks_by_id, decisions)


def _publish_sample_plan(
    lowered: LoweredQuery,
    stages: list[QueryStage],
    reads: list[LoweredRead],
    checks_by_id: dict[str, SourceReadStage],
    decisions: list[RuleDecision],
) -> LoweredQuery:
    by_id = {**{stage.stage_id: stage for stage in stages}, **checks_by_id}
    retained: set[str] = set()

    def retain(identifier: str) -> None:
        if identifier in retained:
            return
        retained.add(identifier)
        for dependency in by_id[identifier].inputs:
            retain(dependency)

    retain(lowered.plan.publish)
    scheduled: dict[str, QueryStage] = {}

    def schedule(identifier: str) -> None:
        if identifier in scheduled:
            return
        stage = by_id[identifier]
        for dependency in stage.inputs:
            schedule(dependency)
        scheduled[identifier] = stage

    # A plain sibling may become the output coverage carrier. Run its shared
    # validation before value reads without making the sibling depend on it.
    for stage in (*checks_by_id.values(), *stages):
        if stage.stage_id in retained:
            schedule(stage.stage_id)
    stages = list(scheduled.values())
    reads = [read for read in reads if read.stage_id in retained]
    plan = replace(
        lowered.plan,
        stages=tuple(stages),
        consumers=_consumers(tuple(stages)),
        optimization_decisions=tuple(decisions),
    )
    return LoweredQuery(replace(plan, required_slots=required_slots(plan)), tuple(reads))


def _membership_checks(
    prepared: PreparedSources,
    lowered: LoweredQuery,
    program: FormulaProgram,
    required_domains: set[PopulationDependency],
) -> tuple[tuple[SourceReadStage, LoweredRead], ...]:
    """Retain source checks for predicate Populations whose key transport was pruned."""
    by_id = {stage.stage_id: stage for stage in lowered.plan.stages}
    roots = {
        domain
        for value in program.values
        if isinstance(source := by_id[value.stage_id], SourceReadStage)
        for member in read_members(source) or (source,)
        for predicate in member.predicates
        for domain in condition_populations(predicate)
    }
    # A source identity selection consumes its predicate Population's keys.
    # Its own preparation checks do not carry that predicate's domain checks.
    roots.update(
        domain
        for node in walk(roots, resolve=lambda _: None).order
        if isinstance(node, SourceIdentities) and node.predicate is not None
        for domain in condition_populations(node.predicate)
    )
    needed: set[str] = set()

    def require(identifier: str) -> None:
        if identifier not in needed:
            needed.add(identifier)
            for dependency in by_id[identifier].inputs:
                require(dependency)

    for binding in program.populations:
        if binding.definition in required_domains:
            require(binding.stage_id)
    checked: set[str] = set()

    def collect(identifier: str) -> None:
        if identifier in needed or identifier in checked:
            return
        stage = by_id[identifier]
        if isinstance(stage, SourceReadStage) and lowered.read(identifier).population_checks:
            checked.add(identifier)
        elif (
            isinstance(stage, LocalCompute)
            and isinstance(stage.program, ResultProgram)
            and isinstance(stage.program.operation, PopulationValues)
        ):
            # Set/product stages can retain separate native owners for factors.
            for dependency in stage.inputs:
                collect(dependency)

    for binding in program.populations:
        if binding.definition in roots:
            collect(binding.stage_id)

    checks = []
    for source in lowered.plan.stages:
        if not isinstance(source, SourceReadStage) or source.stage_id not in checked:
            continue
        owner = prepared.sources.get(source.source_id, prepared.source)
        obligations = lowered.read(source.stage_id).population_checks
        summary, numeric = _population_check_summary(
            obligations, owner.provider.name, 0, policy=lowered.plan.optimization_policy
        )
        assert summary is not None
        # Emit only the diagnostic row when a check fails.
        expression = summary.filter(ibis.or_(*(summary[name] for name, _ in numeric)))
        identifier = "source:population-check:" + source.stage_id
        check_stage = replace(
            source,
            stage_id=identifier,
            program=NativeRead(()),
            columns=(),
            keys=(),
            preparation_checks=tuple(
                (name, check.expected, check.received) for name, check in numeric
            ),
        )
        checks.append((check_stage, LoweredRead(identifier, expression, numeric)))
    return tuple(checks)


def _shared_fold_metrics(
    members: list[SourceReadStage],
    aggregates: Mapping[str, tuple[ops.Aggregate, dict[str, str]]],
    keys: Mapping[str, str],
) -> tuple[dict[str, ops.Value], dict[str, str]]:
    metrics: dict[str, ops.Value] = {}
    aliases: dict[str, str] = {}
    representatives: dict[
        tuple[str, tuple[tuple[DataType, bool, str | None], ...], ops.Value], str
    ] = {}
    for stage in members:
        aggregate, _ = aggregates[stage.stage_id]
        signature = keys.get(stage.stage_id)
        layout = tuple(
            (column.dtype, column.nullable, column.unit)
            for column in stage.columns
            if column.slot_id not in stage.keys
        )
        for name, metric in aggregate.metrics.items():
            representative = (
                representatives.setdefault((signature, layout, metric), name)
                if signature is not None
                else name
            )
            aliases[name] = representative
            if representative == name:
                metrics[name] = metric
    return metrics, aliases


def _combine_exact_fold_quantiles(metrics: dict[str, ops.Value]) -> dict[str, ops.Value]:
    """Share different exact probabilities without changing the native quantile family."""
    quantiles: dict[tuple[ops.Value, ops.Value | None], list[ops.Quantile]] = {}
    for metric in metrics.values():
        for quantile in metric.find(ops.Quantile):
            if type(quantile) is ops.Quantile:
                # ApproxQuantile also matches Quantile; preserve its native algorithm.
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
    return metrics


def _share_folds(
    stages: list[SourceReadStage],
    reads: list[LoweredRead],
    values: list[FormulaInput],
    prepared: PreparedSources,
    cse_keys: Mapping[str, str],
    decisions: list[RuleDecision],
) -> tuple[list[SourceReadStage], list[LoweredRead], list[FormulaInput]]:
    """Share an actual aggregate, rather than repeated references to a CTE."""
    payloads = {read.stage_id: read for read in reads}
    groups: dict[
        tuple[
            SourceSession,
            ops.Relation,
            tuple[str, ...],
            tuple[SourceParameter, ...],
            tuple[tuple[str, ops.Value], ...],
            tuple[DataColumn, ...],
        ],
        list[SourceReadStage],
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
                tuple(aggregate.groups.items()),
                tuple(column for column in stage.columns if column.slot_id in stage.keys),
            ),
            [],
        ).append(stage)
    for members in groups.values():
        if len(members) < 2:
            continue
        projections: dict[str, ops.Value] = {}
        first = members[0]
        reference, _ = aggregates[first.stage_id]
        metrics, metric_aliases = _shared_fold_metrics(members, aggregates, cse_keys)
        removed_count = sum(name != alias for name, alias in metric_aliases.items())
        if removed_count:
            decisions.append(
                RuleDecision(
                    "common_read_cse",
                    "adopted",
                    tuple(member.stage_id for member in members),
                    f"Reused {removed_count} qualified sample aggregate projections.",
                )
            )
        metrics = _combine_exact_fold_quantiles(metrics)
        aggregate = ops.Aggregate(reference.parent, reference.groups, metrics)
        for stage in members:
            _, aliases = aggregates[stage.stage_id]
            projections.update(
                (name, ops.Field(aggregate, metric_aliases.get(source, source)))
                for name, source in aliases.items()
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
