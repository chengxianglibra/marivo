"""Fuse terminal field relations while retaining checked output order."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import ibis

from marivo._definition.graph import computation_fingerprint
from marivo._definition.query import (
    FilterValues,
    PopulationValues,
    SelectValues,
    TopValues,
)
from marivo._definition.relations import (
    PopulationProgram,
    PopulationSet,
)
from marivo.analysis.binding.programs import FormulaProgram
from marivo.analysis.compiler.lowered_query import LoweredQuery, LoweredRead
from marivo.analysis.compiler.native_query import NativeQueryFinish
from marivo.analysis.compiler.optimization import required_slots
from marivo.analysis.compiler.query_program import ResultProgram
from marivo.analysis.compiler.query_stages import (
    LocalCompute,
    NativeRead,
    RuleDecision,
    SourceReadStage,
)
from marivo.analysis.materialization.native_query_checks import (
    NativeQueryRelation,
    _attach_relation_checks,
)
from marivo.analysis.materialization.source_query_policy import (
    qualified_query_provider,
)

if TYPE_CHECKING:
    from marivo.analysis.materialization.prepared_sources import PreparedSources
    from marivo.datasource.spi import SourceSession


from marivo.analysis.materialization.native_query_relation import (
    native_query_relation,
)
from marivo.analysis.materialization.query_key_values import _distinct, _same_key
from marivo.analysis.materialization.query_plan_transport import (
    _consumers,
    _logical_reads,
)


def _terminal_population_stage(endpoint: LocalCompute, lowered: LoweredQuery) -> LocalCompute:
    """Find the complete Population forwarded by a field-only Frame formula."""
    program = endpoint.program
    if (
        not isinstance(program, FormulaProgram)
        or program.values
        or program.output_population is None
    ):
        return endpoint
    population = next(
        (item for item in program.populations if item.definition == program.output_population), None
    )
    if population is None:
        return endpoint
    return next(
        (
            stage
            for stage in lowered.plan.stages
            if isinstance(stage, LocalCompute) and stage.stage_id == population.stage_id
        ),
        endpoint,
    )


def _order_terminal_population(
    endpoint: LocalCompute,
    native: NativeQueryRelation,
    prepared: PreparedSources,
    lowered: LoweredQuery,
    owner: SourceSession,
) -> tuple[NativeQueryRelation, NativeQueryFinish | None] | None:
    population = _terminal_population_stage(endpoint, lowered)
    finish: NativeQueryFinish | None = None
    if isinstance(population.program, ResultProgram) and isinstance(
        population.program.operation, PopulationValues
    ):
        if any(
            isinstance(item, LocalCompute)
            and isinstance(item.program, ResultProgram)
            and isinstance(item.program.operation, (TopValues, PopulationValues))
            for item in lowered.plan.stages
            if item.stage_id in native.stages and item.stage_id != population.stage_id
        ):
            return None
        domain = population.program.operation.definition
        if (
            endpoint.keys
            and isinstance(domain, PopulationProgram)
            and isinstance(domain.root, PopulationSet)
            and domain.root.operation == "union"
        ):
            left_position = next(
                (
                    index
                    for index, atom in enumerate(population.program.population_inputs)
                    if computation_fingerprint(atom) == computation_fingerprint(domain.root.left)
                ),
                None,
            )
            if left_position is None:
                return None
            left = native_query_relation(
                prepared,
                lowered,
                population.inputs[left_position],
                owner,
                shared=True,
                allowed_consumers=native.stages,
            )
            if isinstance(left, str):
                return None
            source_keys = tuple(
                key
                for group in population.program.population_layouts[left_position]
                for key in group
            )
            # Each source leaf completes in key order. Retain the left block
            # before appending right-only keys in the same canonical order.
            key_relation = _distinct(
                left.expression.select(
                    **dict(
                        zip(
                            endpoint.keys,
                            (left.expression[key] for key in source_keys),
                            strict=True,
                        )
                    )
                ),
                owner.provider.name,
            ).mutate(__query_left_union=ibis.literal(True))
            expression = native.expression.left_join(
                key_relation,
                [
                    _same_key(native.expression[key], key_relation[key], owner.provider.name)
                    for key in endpoint.keys
                ],
            ).select(
                *native.expression.columns,
                __query_union_order=key_relation["__query_left_union"]
                .fill_null(False)
                .ifelse(0, 1),
            )
            native = replace(native, expression=expression)
            finish = NativeQueryFinish(
                endpoint.columns,
                ("__query_union_order", *endpoint.keys),
                population.program.output_layout if population is endpoint else None,
            )
    return native, finish


def fuse_query_terminal(prepared: PreparedSources, lowered: LoweredQuery) -> LoweredQuery:
    """Replace a qualified terminal field relation with one governed source read."""
    by_id = {stage.stage_id: stage for stage in lowered.plan.stages}
    final = by_id[lowered.plan.publish]
    if not isinstance(final, LocalCompute):
        return lowered
    # Source frames have canonical key order. Keep Top and its following local
    # presentation operations so display order is restored over the bounded rows.
    endpoint = final
    cursor = final
    while len(cursor.inputs) == 1 and isinstance(cursor.program, ResultProgram):
        if isinstance(cursor.program.operation, TopValues):
            endpoint = cursor
            break
        if not isinstance(cursor.program.operation, (FilterValues, SelectValues)):
            break
        parent = by_id[cursor.inputs[0]]
        if not isinstance(parent, LocalCompute):
            break
        cursor = parent
    for owner in dict.fromkeys(prepared.sources.values()):
        if not qualified_query_provider(owner):
            continue
        native = native_query_relation(prepared, lowered, endpoint.stage_id, owner, terminal=True)
        if isinstance(native, str) or not native.sources:
            continue
        ordered = _order_terminal_population(endpoint, native, prepared, lowered, owner)
        if ordered is None:
            continue
        native, finish = ordered
        return _install_terminal_read(prepared, lowered, endpoint, native, finish)
    return lowered


def _install_terminal_read(
    prepared: PreparedSources,
    lowered: LoweredQuery,
    endpoint: LocalCompute,
    native: NativeQueryRelation,
    finish: NativeQueryFinish | None,
) -> LoweredQuery:
    expression, preparation, checks = _attach_relation_checks(
        native.expression, native, lowered.plan, prepared=prepared
    )
    source_id = lowered.plan.source_read_stage(native.sources[0].stage_id).source_id
    keep_top = isinstance(endpoint.program, ResultProgram) and isinstance(
        endpoint.program.operation, TopValues
    )
    identifier = "source:terminal:" + endpoint.stage_id if keep_top else endpoint.stage_id
    stage = SourceReadStage(
        identifier,
        source_id,
        (),
        endpoint.columns,
        endpoint.keys,
        NativeRead(native.reductions(lowered.plan), finish),
        (),
        (),
        None,
        preparation_checks=preparation,
        unique_keys=endpoint.unique_keys,
        native_reads=_logical_reads(
            tuple(lowered.plan.source_read_stage(read.stage_id) for read in native.sources)
        ),
        inputs=tuple(dict.fromkeys(parameter.stage_id for parameter in native.parameters)),
        parameters=native.parameters,
    )
    replacement = replace(endpoint, inputs=(identifier,)) if keep_top else stage
    removed = native.stages - {endpoint.stage_id}
    stages = tuple(
        replacement if item.stage_id == endpoint.stage_id else item
        for item in lowered.plan.stages
        if item.stage_id not in removed
    )
    if keep_top:
        position = next(
            index for index, item in enumerate(stages) if item.stage_id == endpoint.stage_id
        )
        stages = (*stages[:position], stage, *stages[position:])
    decisions = (
        *lowered.plan.optimization_decisions,
        RuleDecision(
            "source_terminal_fusion",
            "adopted",
            tuple(sorted(native.stages)),
            "Qualified same-owner relation and full-scope checks execute at the source; Top display order is restored locally.",
        ),
    )
    plan = replace(
        lowered.plan,
        stages=stages,
        consumers=_consumers(stages),
        fusion_groups=tuple(
            group for group in lowered.plan.fusion_groups if group.output not in native.stages
        ),
        optimization_decisions=decisions,
    )
    plan = replace(plan, required_slots=required_slots(plan))
    return LoweredQuery(
        plan,
        (
            *(read for read in lowered.reads if read.stage_id not in native.stages),
            LoweredRead(stage.stage_id, expression, checks),
        ),
    )
