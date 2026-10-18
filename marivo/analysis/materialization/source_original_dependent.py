"""Source recomputation over complete keys selected from an earlier parent read."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256

import ibis

from marivo._definition.query import (
    DrillValues,
    FilterValues,
    RollupValues,
    SelectValues,
    TopValues,
    ValueSlot,
)
from marivo.analysis.compiler.graph_lowering import LoweredQuery
from marivo.analysis.compiler.graph_plan import LocalCompute, QueryStage, SourceRead
from marivo.analysis.compiler.observation import ibis_type
from marivo.analysis.compiler.optimization import RuleDecision, required_slots
from marivo.analysis.compiler.query_program import SourceParameter
from marivo.analysis.materialization.graph_source_execution import PreparedSources
from marivo.datasource.ir import TableSourceIR


def fuse_dependent_original(prepared: PreparedSources, lowered: LoweredQuery) -> LoweredQuery:
    """Keep host parent selection and push only its fixed-key target recomputation."""
    original_lowered = lowered
    by_id = {stage.stage_id: stage for stage in lowered.plan.stages}
    target = by_id[lowered.plan.publish]
    if (
        not isinstance(target, LocalCompute)
        or target.result is None
        or not isinstance(target.result.operation, (RollupValues, DrillValues))
        or not target.inputs
        or lowered.plan.checks
    ):
        return original_lowered
    selected = by_id[target.inputs[0]]
    if not selected.keys:
        return original_lowered
    root = selected
    has_selection = False
    first_selector: LocalCompute | None = None
    while isinstance(root, LocalCompute) and root.result is not None:
        if root.fused_steps or len(root.inputs) != 1:
            return original_lowered
        operation = root.result.operation
        if isinstance(operation, (FilterValues, TopValues)):
            has_selection = True
        elif isinstance(operation, SelectValues):
            if any(
                not isinstance(value, ValueSlot) or value.index != index
                for index, (_, value) in enumerate(operation.values)
            ):
                return original_lowered
        else:
            return original_lowered
        first_selector = root
        root = by_id[root.inputs[0]]
    if isinstance(root, SourceRead) and first_selector is not None:
        result = first_selector.result
        original = result.original if result is not None else None
        if original is None or result is None:
            return original_lowered
        synthetic = LocalCompute(
            first_selector.stage_id + ":checked-parent",
            tuple(dict.fromkeys(binding.stage_id for binding in original.values)),
            "query.combine@v1",
            result.columns,
            first_selector.keys,
            program=original,
        )
        updated_selector = replace(first_selector, inputs=(synthetic.stage_id,))
        expanded: list[QueryStage] = []
        for stage in lowered.plan.stages:
            if stage.stage_id == first_selector.stage_id:
                expanded.extend((synthetic, updated_selector))
            else:
                expanded.append(stage)
        lowered = replace(lowered, plan=replace(lowered.plan, stages=tuple(expanded)))
        by_id = {stage.stage_id: stage for stage in lowered.plan.stages}
        selected = by_id[selected.stage_id]
        root = synthetic
    if not has_selection or not isinstance(root, LocalCompute) or root.program is None:
        return original_lowered
    source_ids = {binding.stage_id for binding in root.program.values}
    sources = tuple(
        read
        for read in lowered.reads
        if read.stage.stage_id in source_ids
        or any(member.stage_id in source_ids for member in read.stage.common_reads)
    )
    if not sources:
        return original_lowered
    owner = prepared.sources.get(sources[0].stage.source_id, prepared.source)
    if owner.provider.name not in ("duckdb", "postgres", "mysql", "trino", "clickhouse") or any(
        prepared.sources.get(read.stage.source_id, prepared.source) is not owner for read in sources
    ):
        return original_lowered
    if owner.provider.name == "duckdb" and all(
        isinstance(bound.source, TableSourceIR)
        for path, bound in prepared.bound.items()
        if prepared.sources.get(path, prepared.source) is owner
    ):
        if not any(
            request.stage.reduction is not None
            and (
                request.stage.reduction.sampled
                or request.stage.reduction.calendar is not None
                or request.stage.reduction.calendars
            )
            for request in sources
        ):
            return original_lowered
        from marivo.analysis.materialization.source_original_complex import fuse_complex_original

        compact = fuse_complex_original(prepared, lowered)
        if compact is not None:
            return compact
    from marivo.analysis.materialization.source_original import (
        build_dependent_original_parent,
        build_dependent_original_target,
    )

    parent = build_dependent_original_parent(prepared, lowered, root)
    if isinstance(parent, str):
        return original_lowered
    columns = tuple(
        next(column for column in selected.columns if column.slot_id == key)
        for key in selected.keys
    )
    name = "__marivo_original_selected_" + sha256(target.stage_id.encode()).hexdigest()[:24]
    keys = ibis.table({column.slot_id: ibis_type(column.dtype) for column in columns}, name=name)
    recomputed = build_dependent_original_target(prepared, lowered, target, keys)
    if isinstance(recomputed, str) or recomputed.stage.inputs or recomputed.stage.parameters:
        return original_lowered
    read = replace(
        recomputed.stage,
        inputs=(selected.stage_id,),
        parameters=(SourceParameter(selected.stage_id, name, columns),),
    )
    rewritten = {
        **by_id,
        root.stage_id: parent.stage,
        target.stage_id: read,
    }
    retained: set[str] = set()

    def keep(identifier: str) -> None:
        if identifier not in retained:
            retained.add(identifier)
            for dependency in rewritten[identifier].inputs:
                keep(dependency)

    keep(lowered.plan.publish)
    stages = tuple(
        rewritten[item.stage_id] for item in lowered.plan.stages if item.stage_id in retained
    )
    plan = replace(
        lowered.plan,
        stages=stages,
        consumers=tuple(
            (
                stage.stage_id,
                tuple(item.stage_id for item in stages if stage.stage_id in item.inputs),
            )
            for stage in stages
        ),
        original=None,
        fusion_groups=(),
        optimization_decisions=(
            *lowered.plan.optimization_decisions,
            RuleDecision(
                "source_original_dependent",
                "adopted",
                (root.stage_id, selected.stage_id, target.stage_id),
                "The checked parent is selected once on the host; its complete keys parameterize a later source recomputation over current contributions.",
            ),
        ),
    )
    plan = replace(plan, required_slots=required_slots(plan))
    requests = {
        **{request.stage.stage_id: request for request in lowered.reads},
        root.stage_id: parent,
        target.stage_id: replace(recomputed, stage=read),
    }
    return LoweredQuery(
        plan, tuple(requests[stage.stage_id] for stage in stages if isinstance(stage, SourceRead))
    )
