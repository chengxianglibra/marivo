"""Qualify single-owner field selection as a native Query-key subquery."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo._definition.expressions import (
    MemberCount,
    PerReduce,
    PerWeightedMean,
)
from marivo._definition.graph import walk
from marivo._definition.query import (
    PopulationValues,
)
from marivo._definition.relations import (
    PopulationDependency,
    condition_populations,
)
from marivo.analysis.binding.programs import FormulaProgram, PopulationInput
from marivo.analysis.compiler.lowered_query import LoweredQuery, LoweredRead
from marivo.analysis.compiler.optimization import required_slots
from marivo.analysis.compiler.query_program import ResultProgram, SourceParameter
from marivo.analysis.compiler.query_stages import (
    FrameKeysInput,
    LocalCompute,
    NativeRead,
    QueryPlan,
    QueryStage,
    RuleDecision,
    SourceReadStage,
    local_steps,
    read_components,
    read_members,
)
from marivo.analysis.compiler.source_predicates import (
    source_parameter_comparisons,
)
from marivo.analysis.materialization.native_population_relations import _native_member_counts
from marivo.analysis.materialization.native_query_checks import (
    NativeQueryRelation,
    _attach_relation_checks,
)
from marivo.analysis.materialization.native_query_relation import (
    _physical_owner,
    _read_scopes,
    native_query_relation,
)
from marivo.analysis.materialization.query_coverage_fusion import _fold_output_coverage
from marivo.analysis.materialization.query_key_values import _distinct
from marivo.analysis.materialization.query_parameters import _replace_parameter, inline_key_relation
from marivo.analysis.materialization.query_plan_transport import (
    _consumers,
    _host_domain_selections,
    _logical_reads,
    _prune_transport,
    _stage_dependencies,
)
from marivo.analysis.materialization.query_shared_keys import (
    fuse_shared_query_keys,
    qualified_parameter_consumer,
)
from marivo.analysis.materialization.source_query_policy import (
    qualified_query_provider,
)

if TYPE_CHECKING:
    from marivo.analysis.materialization.prepared_sources import PreparedSources
    from marivo.analysis.model.optimization_policy import OptimizationPolicy
    from marivo.datasource.spi import SourceSession


def _fuse_parameter_blocks(prepared: PreparedSources, lowered: LoweredQuery) -> LoweredQuery:
    """Replace mutually shared parameter producers atomically in one consumer."""
    for original in tuple(lowered.reads):
        if (
            len(lowered.plan.source_read_stage(original.stage_id).parameters) < 2
            or read_components(lowered.plan.source_read_stage(original.stage_id)) is not None
        ):
            continue
        owner = prepared.sources.get(
            lowered.plan.source_read_stage(original.stage_id).source_id, prepared.source
        )
        if not qualified_parameter_consumer(
            prepared, lowered.plan.source_read_stage(original.stage_id), original, owner
        ):
            continue
        candidate = lowered.plan
        pruned: set[str] = set()
        parameters = lowered.plan.source_read_stage(original.stage_id).parameters
        for parameter in parameters:
            candidate, transport = _prune_transport(candidate, parameter.stage_id)
            pruned.update(transport)
        by_id = {stage.stage_id: stage for stage in candidate.stages}
        closure = {original.stage_id} | _stage_dependencies(
            by_id, tuple(parameter.stage_id for parameter in parameters)
        )
        natives = tuple(
            native_query_relation(
                prepared,
                replace(lowered, plan=candidate),
                parameter.stage_id,
                owner,
                shared=True,
                allowed_consumers=frozenset(closure),
            )
            for parameter in parameters
        )
        if any(isinstance(native, str) for native in natives):
            continue
        native_inputs = tuple(native for native in natives if not isinstance(native, str))
        if any(
            not set(candidate.consumers_of(identifier)) <= closure
            for native in native_inputs
            for identifier in native.stages
        ):
            continue
        expression = original.expression
        compatible = True
        for parameter, native in zip(parameters, native_inputs, strict=True):
            producer = by_id[parameter.stage_id]
            if len(producer.keys) != len(parameter.columns):
                compatible = False
                break
            keys = _distinct(
                native.expression.select(
                    **dict(
                        zip(
                            (column.slot_id for column in parameter.columns),
                            (native.expression[key] for key in producer.keys),
                            strict=True,
                        )
                    )
                ),
                owner.provider.name,
            )
            expression = _replace_parameter(
                source_parameter_comparisons(
                    expression, parameter.placeholder, owner.provider.name
                ),
                parameter.placeholder,
                keys,
            )
        if not compatible:
            continue
        native = NativeQueryRelation(
            expression,
            frozenset().union(*(value.stages for value in native_inputs)),
            tuple(
                {read.stage_id: read for value in native_inputs for read in value.sources}.values()
            ),
            tuple(check for value in native_inputs for check in value.checks),
        )
        expression, preparation, checks = _attach_relation_checks(
            expression,
            native,
            lowered.plan,
            lowered.plan.source_read_stage(original.stage_id).preparation_checks,
            original.numeric_checks,
            prepared=prepared,
        )
        producers = {parameter.stage_id for parameter in parameters}
        stage = replace(
            lowered.plan.source_read_stage(original.stage_id),
            inputs=tuple(
                value
                for value in lowered.plan.source_read_stage(original.stage_id).inputs
                if value not in producers
            ),
            parameters=(),
            preparation_checks=preparation,
            native_reductions=(
                *lowered.plan.source_read_stage(original.stage_id).native_reductions,
                *native.reductions(lowered.plan),
            ),
            native_reads=_logical_reads(
                (
                    lowered.plan.source_read_stage(original.stage_id),
                    *(lowered.plan.source_read_stage(read.stage_id) for read in native.sources),
                )
            ),
        )
        removed = native.stages | frozenset(pruned)
        stages = tuple(
            stage if item.stage_id == stage.stage_id else item
            for item in candidate.stages
            if item.stage_id not in removed
        )
        plan = replace(
            candidate,
            stages=stages,
            consumers=_consumers(stages),
            fusion_groups=tuple(
                group for group in candidate.fusion_groups if group.output not in removed
            ),
            optimization_decisions=(
                *candidate.optimization_decisions,
                RuleDecision(
                    "source_parameter_block",
                    "adopted",
                    tuple(sorted(removed)),
                    "All same-owner parameter producers share one source statement; complete key tuples and parent checks stay inside the consumer.",
                ),
            ),
        )
        plan = replace(plan, required_slots=required_slots(plan))
        lowered = LoweredQuery(
            plan,
            tuple(
                LoweredRead(stage.stage_id, expression, checks)
                if read.stage_id == stage.stage_id
                else read
                for read in lowered.reads
                if read.stage_id not in removed
            ),
        )
    return lowered


def _fold_population_checks(prepared: PreparedSources, lowered: LoweredQuery) -> LoweredQuery:
    """Retain obligated checks without transporting unused Population keys."""
    for candidate in tuple(lowered.plan.stages):
        if (
            not isinstance(candidate, LocalCompute)
            or not isinstance(candidate.program, FormulaProgram)
            or local_steps(candidate)
        ):
            continue
        program = candidate.program
        by_id = {stage.stage_id: stage for stage in lowered.plan.stages}
        if candidate.stage_id not in by_id:
            continue
        selection = _population_check_carrier(program)
        if selection is None:
            continue
        carrier_id, kept, redundant = selection
        carrier_stage = by_id.get(carrier_id)
        if (
            not isinstance(carrier_stage, SourceReadStage)
            or read_components(carrier_stage) is not None
        ):
            continue
        carrier = lowered.read(carrier_id)
        owner = prepared.sources.get(
            lowered.plan.source_read_stage(carrier.stage_id).source_id, prepared.source
        )
        if not qualified_query_provider(owner):
            continue
        domains: tuple[PopulationDependency, ...]
        if program.output_population is not None:
            domains = (program.output_population,)
        else:
            graph = prepared.graph
            observation = None if graph is None else graph.signature.observation
            if observation is None:
                continue
            occurrences = {
                carrier_stage.occurrence,
                *(member.occurrence for member in read_members(carrier_stage)),
            }
            domains = tuple(
                domain
                for contribution in observation.contributions
                if contribution.occurrence in occurrences
                for condition in (*contribution.intrinsic_conditions, contribution.common_condition)
                if condition is not None
                for domain in condition_populations(condition)
            )
        consumed = walk(domains, resolve=dict(program.declared).get).order
        if any(binding.definition not in consumed for binding in redundant):
            continue
        removed = {binding.stage_id for binding in redundant}
        if any(
            lowered.plan.consumers_of(identifier) != (candidate.stage_id,) for identifier in removed
        ):
            continue
        sources = _compatible_population_checks(prepared, lowered, by_id, removed, owner)
        if sources is None:
            continue
        parameters = _merged_check_parameters(lowered.plan, carrier_id, sources)
        if parameters is None:
            continue
        inputs = tuple(dict.fromkeys(parameter.stage_id for parameter in parameters))
        order = {identifier: index for index, identifier in enumerate(by_id)}
        if any(order[identifier] >= order[carrier_id] for identifier in inputs):
            continue
        if any(source.numeric_checks for source in sources):
            continue
        checks = (
            *carrier.population_checks,
            *(check for source in sources for check in source.population_checks),
        )
        stage = replace(
            lowered.plan.source_read_stage(carrier.stage_id),
            inputs=inputs,
            parameters=tuple(parameters),
            native_reads=_logical_reads(
                (
                    lowered.plan.source_read_stage(carrier.stage_id),
                    *(lowered.plan.source_read_stage(source.stage_id) for source in sources),
                )
            ),
        )
        updated = replace(program, populations=kept, counts=())
        compute = replace(
            candidate,
            inputs=tuple(
                identifier for identifier in candidate.inputs if identifier not in removed
            ),
            program=updated,
        )
        rewritten = {**by_id, carrier_id: stage, candidate.stage_id: compute}
        retained = _stage_dependencies(rewritten, (lowered.plan.publish,))
        stages = tuple(
            rewritten[item.stage_id] for item in lowered.plan.stages if item.stage_id in retained
        )
        plan = replace(
            lowered.plan,
            stages=stages,
            consumers=_consumers(stages),
            original=updated if lowered.plan.original == program else lowered.plan.original,
            fusion_groups=tuple(
                group for group in lowered.plan.fusion_groups if group.output in retained
            ),
            optimization_decisions=(
                *lowered.plan.optimization_decisions,
                RuleDecision(
                    "source_population_check_fusion",
                    "adopted",
                    (carrier_id, *sorted(removed)),
                    "Unused Population keys remain source check summaries; only the consumed output domain or reduced values are transported.",
                ),
            ),
        )
        plan = replace(plan, required_slots=required_slots(plan))
        lowered = LoweredQuery(
            plan,
            tuple(
                replace(request, population_checks=checks)
                if request.stage_id == carrier_id
                else request
                for request in lowered.reads
                if request.stage_id in retained
            ),
        )
    return lowered


def _population_check_carrier(
    program: FormulaProgram,
) -> tuple[str, tuple[PopulationInput, ...], tuple[PopulationInput, ...]] | None:
    if program.calendar is not None or not program.populations:
        return None
    if any(
        isinstance(node, (MemberCount, PerReduce, PerWeightedMean))
        for node in walk(
            (node for _, node in program.outputs), resolve=dict(program.declared).get
        ).order
    ):
        return None
    if (
        program.counts
        or program.parent_scope is not None
        or program.calendar_samples
        or program.window_endpoints
    ):
        return None
    kept = tuple(
        binding
        for binding in program.populations
        if binding.definition == program.output_population
    )
    redundant = tuple(binding for binding in program.populations if binding not in kept)
    if not redundant:
        return None
    if program.output_population is not None:
        if program.values or program.outputs or len(kept) != 1:
            return None
        carrier_id = kept[0].stage_id
    elif len(program.values) == 1:
        carrier_id = program.values[0].stage_id
    else:
        return None
    return carrier_id, kept, redundant


def _compatible_population_checks(
    prepared: PreparedSources,
    lowered: LoweredQuery,
    by_id: dict[str, QueryStage],
    removed: set[str],
    owner: SourceSession,
) -> list[LoweredRead] | None:
    sources: list[LoweredRead] = []
    for identifier in removed:
        stage = by_id[identifier]
        if isinstance(stage, SourceReadStage):
            request = lowered.read(identifier)
            scopes = _read_scopes(request)
            placeholders = {parameter.placeholder for parameter in stage.parameters}
            allowed = frozenset(
                node
                for scope in scopes
                for node in scope.op().find(ops.UnboundTable)
                if node.name in placeholders
            )
            if prepared.sources.get(stage.source_id, prepared.source) is not owner or any(
                not _physical_owner(scope, owner, prepared, allowed_parameters=allowed)
                for scope in scopes
            ):
                return None
            sources.append(request)
        elif (
            not isinstance(stage, LocalCompute)
            or not isinstance(stage.program, ResultProgram)
            or not isinstance(stage.program.operation, PopulationValues)
        ):
            return None

    # Preserve source-check order independently of the removed identifier set.
    order = {identifier: index for index, identifier in enumerate(by_id)}
    return sorted(sources, key=lambda request: order[request.stage_id])


def _merged_check_parameters(
    plan: QueryPlan,
    carrier_id: str,
    sources: list[LoweredRead],
) -> tuple[SourceParameter, ...] | None:
    parameters = {
        parameter.placeholder: parameter
        for parameter in plan.source_read_stage(carrier_id).parameters
    }
    for request in sources:
        if set(plan.source_read_stage(request.stage_id).inputs) != {
            parameter.stage_id for parameter in plan.source_read_stage(request.stage_id).parameters
        }:
            return None
        for parameter in plan.source_read_stage(request.stage_id).parameters:
            existing = parameters.get(parameter.placeholder)
            if existing is not None and existing != parameter:
                return None
            parameters[parameter.placeholder] = parameter

    return tuple(parameters.values())


def _fold_member_count_reads(prepared: PreparedSources, lowered: LoweredQuery) -> LoweredQuery:
    """Keep count completion native even when companion Metrics finish locally."""
    for candidate in tuple(lowered.plan.stages):
        if (
            not isinstance(candidate, LocalCompute)
            or not isinstance(candidate.program, FormulaProgram)
            or not candidate.program.counts
            or local_steps(candidate)
        ):
            continue
        program = candidate.program
        natives: dict[str, NativeQueryRelation] = {}
        relations: dict[str, ir.Table] = {}
        providers: dict[str, str] = {}
        allowed = frozenset(stage.stage_id for stage in lowered.plan.stages)
        for count in program.counts:
            qualified = _member_count_relation(
                prepared, lowered, program, count.population, allowed
            )
            if qualified is None:
                break
            identifier, native, provider = qualified
            natives[identifier] = native
            relations[identifier] = native.expression
            providers[identifier] = provider
        else:
            folded = _native_member_counts(
                program,
                relations,
                candidate.keys,
                providers,
                {
                    stage.stage_id: stage.unique_keys
                    for stage in lowered.plan.stages
                    if isinstance(stage, SourceReadStage)
                },
            )
            if isinstance(folded, str):
                continue
            updated, relations = folded
            added: list[SourceReadStage] = []
            reads: list[LoweredRead] = []
            for binding in updated.values[len(program.values) :]:
                original_id = binding.stage_id.rsplit(":__member_count_", 1)[0]
                native = natives[original_id]
                expression, preparation, checks = _attach_relation_checks(
                    relations[binding.stage_id],
                    replace(
                        native,
                        sources=tuple(
                            replace(read, population_checks=()) for read in native.sources
                        ),
                    ),
                    lowered.plan,
                    prepared=prepared,
                )
                obligations = tuple(
                    check for read in native.sources for check in read.population_checks
                )
                added.append(
                    SourceReadStage(
                        binding.stage_id,
                        lowered.plan.source_read_stage(native.sources[0].stage_id).source_id,
                        (),
                        (
                            *tuple(
                                column
                                for column in candidate.columns
                                if column.slot_id in candidate.keys
                            ),
                            binding.column,
                        ),
                        candidate.keys,
                        NativeRead(()),
                        (),
                        (),
                        None,
                        preparation_checks=preparation,
                        native_reads=_logical_reads(
                            tuple(
                                lowered.plan.source_read_stage(read.stage_id)
                                for read in native.sources
                            )
                        ),
                    )
                )
                reads.append(LoweredRead(binding.stage_id, expression, checks, obligations))
            compute = replace(
                candidate,
                program=updated,
                inputs=tuple(binding.stage_id for binding in updated.values),
            )
            rewritten: dict[str, QueryStage] = {
                stage.stage_id: stage for stage in (*lowered.plan.stages, *added)
            }
            rewritten[candidate.stage_id] = compute
            retained: set[str] = set()

            def keep(
                identifier: str,
                retained: set[str] = retained,
                rewritten: dict[str, QueryStage] = rewritten,
            ) -> None:
                if identifier not in retained:
                    retained.add(identifier)
                    for dependency in rewritten[identifier].inputs:
                        keep(dependency)

            keep(lowered.plan.publish)
            scheduled: list[QueryStage] = []
            for item in lowered.plan.stages:
                if item.stage_id == candidate.stage_id:
                    scheduled.extend(added)
                    scheduled.append(compute)
                elif item.stage_id in retained:
                    scheduled.append(item)
            stages = tuple(scheduled)
            plan = replace(
                lowered.plan,
                stages=stages,
                consumers=_consumers(stages),
                optimization_decisions=(
                    *lowered.plan.optimization_decisions,
                    RuleDecision(
                        "source_member_count",
                        "adopted",
                        tuple(stage.stage_id for stage in added),
                        "Distinct complete Population tuples are counted at source; companion Metrics retain their own numeric finish.",
                    ),
                ),
            )
            plan = replace(plan, required_slots=required_slots(plan))
            lowered = LoweredQuery(
                plan, tuple(read for read in (*lowered.reads, *reads) if read.stage_id in retained)
            )
    return lowered


def _member_count_relation(
    prepared: PreparedSources,
    lowered: LoweredQuery,
    program: FormulaProgram,
    population: PopulationDependency,
    allowed: frozenset[str],
) -> tuple[str, NativeQueryRelation, str] | None:
    population_binding = next(
        (item for item in program.populations if item.definition == population), None
    )
    if population_binding is None:
        return None
    stage = next(
        item for item in lowered.plan.stages if item.stage_id == population_binding.stage_id
    )
    if not isinstance(stage, SourceReadStage):
        return None
    owner = prepared.sources.get(stage.source_id, prepared.source)
    if not qualified_query_provider(owner):
        return None
    native = native_query_relation(
        prepared,
        lowered,
        population_binding.stage_id,
        owner,
        shared=True,
        allowed_consumers=allowed,
    )
    if isinstance(native, str) or native.parameters or not native.sources:
        return None
    closure = walk((population,), resolve=lambda _: None).order
    inherited = tuple(
        lowered.read(item.stage_id)
        for item in lowered.plan.stages
        if isinstance(item, SourceReadStage)
        and not item.parameters
        and prepared.sources.get(item.source_id, prepared.source) is owner
        and any(
            binding.stage_id == item.stage_id and binding.definition in closure
            for binding in program.populations
        )
        and (
            lowered.read(item.stage_id).numeric_checks
            or lowered.read(item.stage_id).population_checks
        )
    )
    native = replace(
        native,
        sources=tuple({item.stage_id: item for item in (*native.sources, *inherited)}.values()),
    )
    return population_binding.stage_id, native, owner.provider.name


def fuse_query_keys(
    prepared: PreparedSources,
    lowered: LoweredQuery,
    *,
    policy: OptimizationPolicy | None = None,
) -> LoweredQuery:
    """Inline qualified source-key uses while retaining independently consumed producers."""
    if policy is not None and policy != lowered.plan.optimization_policy:
        lowered = replace(lowered, plan=replace(lowered.plan, optimization_policy=policy))
    policy = lowered.plan.optimization_policy
    if any(isinstance(stage, FrameKeysInput) for stage in lowered.plan.stages):
        # An explicit bridge still completes before its source consumer. Its
        # existing parameter can carry check summaries in that same read; this
        # neither uploads additional keys nor rereads the fixed Frame.
        if policy.allows("source_population_check_fusion"):
            lowered = _fold_population_checks(prepared, lowered)
        if not policy.allows("source_key_fusion"):
            return lowered
        return replace(
            lowered,
            plan=replace(
                lowered.plan,
                optimization_decisions=(
                    *lowered.plan.optimization_decisions,
                    RuleDecision(
                        "source_key_fusion",
                        "refused",
                        (),
                        "Explicit Frame selection completes before dependent source reads.",
                    ),
                ),
            ),
        )
    if policy.allows("source_member_count"):
        lowered = _fold_member_count_reads(prepared, lowered)
    if policy.allows("source_population_check_fusion"):
        lowered = _fold_population_checks(prepared, lowered)
    if policy.allows("source_parameter_block"):
        lowered = _fuse_parameter_blocks(prepared, lowered)
    if policy.allows("source_shared_key_fusion"):
        lowered = fuse_shared_query_keys(prepared, lowered)
    if policy.allows("source_key_fusion"):
        lowered = _fuse_inline_query_keys(prepared, lowered)
    if policy.allows("source_output_population_coverage"):
        lowered = _fold_output_coverage(prepared, lowered)
    return lowered


def _fuse_inline_query_keys(prepared: PreparedSources, lowered: LoweredQuery) -> LoweredQuery:
    decisions = list(lowered.plan.optimization_decisions)
    host_selections = _host_domain_selections(lowered.plan)
    for original in lowered.reads:
        for parameter in lowered.plan.source_read_stage(original.stage_id).parameters:
            if original.stage_id not in {read.stage_id for read in lowered.reads}:
                break
            current = lowered.read(original.stage_id)
            owner = prepared.sources.get(
                lowered.plan.source_read_stage(current.stage_id).source_id, prepared.source
            )
            pair = (parameter.stage_id, current.stage_id)
            if (
                sum(
                    value.stage_id == parameter.stage_id
                    for value in lowered.plan.source_read_stage(current.stage_id).parameters
                )
                != 1
            ):
                decisions.append(
                    RuleDecision(
                        "source_key_fusion",
                        "refused",
                        pair,
                        "producer supplies multiple parameter uses in the same consumer",
                    )
                )
                continue
            candidate, pruned = _prune_transport(lowered.plan, parameter.stage_id)
            native: NativeQueryRelation | str = (
                "selected provider has no qualified source-key fusion route"
            )
            if qualified_query_provider(owner):
                native = native_query_relation(
                    prepared,
                    replace(lowered, plan=candidate),
                    parameter.stage_id,
                    owner,
                    shared=True,
                    allowed_consumers=frozenset(stage.stage_id for stage in candidate.stages),
                )
            if isinstance(native, str):
                decisions.append(RuleDecision("source_key_fusion", "refused", pair, native))
                continue
            if native.stages & host_selections:
                decisions.append(
                    RuleDecision(
                        "source_key_fusion",
                        "refused",
                        pair,
                        "The host Population domain consumes completed parent where/Top keys; this source edge retains those fixed complete keys.",
                    )
                )
                continue
            producer = next(
                stage for stage in candidate.stages if stage.stage_id == parameter.stage_id
            )
            if len(producer.keys) != len(parameter.columns):
                decisions.append(
                    RuleDecision(
                        "source_key_fusion",
                        "refused",
                        pair,
                        "complete key width differs from the planned parameter",
                    )
                )
                continue

            expression = inline_key_relation(
                current.expression, parameter, native.expression, producer.keys, owner.provider.name
            )
            lowered = _install_key_relation(
                prepared,
                lowered,
                current,
                parameter,
                candidate,
                native,
                expression,
                pruned,
                decisions,
            )
    return replace(lowered, plan=replace(lowered.plan, optimization_decisions=tuple(decisions)))


def _install_key_relation(
    prepared: PreparedSources,
    lowered: LoweredQuery,
    current: LoweredRead,
    parameter: SourceParameter,
    candidate: QueryPlan,
    native: NativeQueryRelation,
    expression: ir.Table,
    pruned: tuple[str, ...],
    decisions: list[RuleDecision],
) -> LoweredQuery:
    consumer_stage = lowered.plan.source_read_stage(current.stage_id)
    expression, preparation, checks = _attach_relation_checks(
        expression,
        native,
        lowered.plan,
        consumer_stage.preparation_checks,
        current.numeric_checks,
        prepared=prepared,
    )
    parameters = tuple(value for value in consumer_stage.parameters if value != parameter)
    stage = replace(
        consumer_stage,
        inputs=tuple(value for value in consumer_stage.inputs if value != parameter.stage_id),
        parameters=parameters,
        preparation_checks=tuple(preparation),
        native_reductions=(
            *consumer_stage.native_reductions,
            *native.reductions(lowered.plan),
        ),
        native_reads=_logical_reads(
            (
                consumer_stage,
                *(lowered.plan.source_read_stage(read.stage_id) for read in native.sources),
            )
        ),
    )
    rewritten = {
        item.stage_id: stage if item.stage_id == stage.stage_id else item
        for item in candidate.stages
    }
    retained = _stage_dependencies(rewritten, (candidate.publish,))
    removed = frozenset(rewritten) - retained
    stages = tuple(
        rewritten[item.stage_id] for item in candidate.stages if item.stage_id in retained
    )
    decisions.append(
        RuleDecision(
            "source_key_fusion",
            "adopted",
            (*sorted(removed), stage.stage_id),
            "Same SourceSession field selection stays in the null-safe membership subquery; full-scope identity/classification checks are retained.",
        )
    )
    if pruned:
        decisions.append(
            RuleDecision(
                "dead_population_transport",
                "adopted",
                pruned,
                "Published formula consumes no output/member/sample domain and retains no downstream original state.",
            )
        )
    plan = replace(
        candidate,
        stages=stages,
        consumers=_consumers(stages),
        fusion_groups=tuple(
            group for group in candidate.fusion_groups if group.output not in removed
        ),
    )
    plan = replace(plan, required_slots=required_slots(plan))
    read = replace(
        current,
        stage_id=stage.stage_id,
        expression=expression,
        numeric_checks=tuple(checks),
    )
    return LoweredQuery(
        plan,
        tuple(
            read if item.stage_id == stage.stage_id else item
            for item in lowered.reads
            if item.stage_id not in removed
        ),
    )
