"""Private planning and typed carriers for exact quantile frequency capture."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

from marivo._definition.expressions import SourceAggregate
from marivo.analysis.compiler.graph_plan import (
    AttributeFrequencyRead,
    LocalCompute,
    QueryPlan,
    ReductionRead,
    SourceRead,
    require_reduction,
)
from marivo.analysis.compiler.observation import FormulaProgram
from marivo.analysis.core.data_types import DataColumn, DataType
from marivo.analysis.errors import AnalysisStageUnavailableError

if TYPE_CHECKING:
    from collections.abc import Mapping

    import ibis.expr.types as ir

    from marivo.analysis.methods.attribute_quantiles import FrequencyState


@dataclass(frozen=True, slots=True)
class AttributeStateCapture:
    """Actual statement result size; decoded bytes are not network or scan bytes."""

    stage_id: str
    provider: str
    state_route: Literal[
        "sufficient_statistics", "typed_frequencies", "typed_membership", "key_domain"
    ]
    value_slots: tuple[str, ...]
    state_entries: int
    state_bytes: int
    returned_rows: int
    decoded_arrow_bytes: int
    input_types: tuple[tuple[str, tuple[DataType, ...]], ...]
    output_types: tuple[tuple[str, DataType], ...]


@dataclass(frozen=True, slots=True)
class AttributeFrequencyCapture:
    """Fixed exact endpoint distributions; never persisted with method facts."""

    stage_id: str
    occurrence: tuple[int, ...]
    keys: tuple[str, ...]
    key_columns: tuple[DataColumn, ...]
    groups: tuple[tuple[tuple[object, ...], FrequencyState], ...]
    output_type: DataType
    q: float | None
    value_slot: str
    kind: Literal["quantile", "distinct"]
    state_entries: int
    returned_rows: int
    decoded_arrow_bytes: int
    finisher: Literal["rank_bounds", "duckdb_native", "postgres_native"]


def frequency_read(stage: SourceRead) -> SourceRead:
    """Request exact frequencies using the existing prepared observation input."""
    reduction = require_reduction(stage)
    node = reduction.node
    if (
        not isinstance(stage.program, (ReductionRead, AttributeFrequencyRead))
        or not isinstance(node, SourceAggregate)
        or node.reducer not in ("median", "quantile", "count_distinct")
        or reduction.duration is not None
        or reduction.sampled
        or reduction.mean_fold is not None
    ):
        raise AnalysisStageUnavailableError(
            stage="attribute_state_binding",
            expected="an independent exact numeric source reduction with a typed frequency route",
            received=f"{type(stage.program).__name__}: {type(node).__name__}",
            repair="Use a qualified exact numeric quantile or distinct reduction. Internal sample reductions and native approximate quantiles need their own qualified state route; no business row was read.",
            target="analysis_frame.attribute",
        )
    return replace(stage, program=AttributeFrequencyRead(reduction), attribute_state=True)


def share_frequency_reads(plan: QueryPlan) -> QueryPlan:
    """Share an identical prepared distribution, retaining every scalar finisher."""
    groups: list[list[SourceRead]] = []

    def identity(stage: SourceRead) -> tuple[object, ...]:
        reduction = require_reduction(stage)
        node = reduction.node
        assert isinstance(node, SourceAggregate)
        return (
            stage.binding_id,
            stage.source_id,
            stage.keys,
            tuple(c for c in stage.columns if c.slot_id in stage.keys),
            stage.field_columns,
            stage.predicates,
            stage.period,
            stage.inputs,
            stage.parameters,
            stage.preparation_checks,
            node.operand,
            node.time,
            node.nulls,
            node.rule_version,
            "distinct" if node.reducer == "count_distinct" else "quantile",
            reduction.axes,
            reduction.calendar,
            reduction.time_column,
            reduction.duration,
            reduction.window_key,
            reduction.preserve_sample_presence,
            reduction.calendars,
            reduction.sampled,
            reduction.coverage,
            reduction.provider,
            reduction.mean_fold,
        )

    for stage in plan.stages:
        if (
            not isinstance(stage, SourceRead)
            or not isinstance(stage.program, AttributeFrequencyRead)
            or not stage.binding_id
        ):
            continue
        group = next((items for items in groups if identity(items[0]) == identity(stage)), None)
        if group is None:
            groups.append([stage])
        else:
            group.append(stage)
    shared = tuple(tuple(group) for group in groups if len(group) > 1)
    if not shared:
        return plan
    aliases = {item.stage_id: group[0].stage_id for group in shared for item in group}
    heads = {group[0].stage_id: group for group in shared}

    def formula(program: FormulaProgram) -> FormulaProgram:
        return replace(
            program,
            values=tuple(
                replace(value, stage_id=aliases.get(value.stage_id, value.stage_id))
                for value in program.values
            ),
        )

    stages = []
    for stage in plan.stages:
        if isinstance(stage, SourceRead):
            if stage.stage_id in aliases and stage.stage_id not in heads:
                continue
            if stage.stage_id in heads:
                members = heads[stage.stage_id]
                columns = {
                    column.slot_id: column for member in members for column in member.columns
                }
                assert isinstance(stage.program, AttributeFrequencyRead)
                stage = replace(
                    stage,
                    columns=tuple(columns.values()),
                    program=replace(stage.program, members=members),
                )
        inputs = tuple(aliases.get(item, item) for item in stage.inputs)
        if isinstance(stage, LocalCompute) and isinstance(stage.program, FormulaProgram):
            stage = replace(stage, inputs=inputs, program=formula(stage.program))
        else:
            stage = replace(stage, inputs=inputs)
        stages.append(stage)
    consumers: dict[str, list[str]] = {stage.stage_id: [] for stage in stages}
    for stage in stages:
        for source in stage.inputs:
            consumers[source].append(stage.stage_id)
    return replace(
        plan,
        stages=tuple(stages),
        consumers=tuple((stage.stage_id, tuple(consumers[stage.stage_id])) for stage in stages),
        publish=aliases.get(plan.publish, plan.publish),
        original=formula(plan.original) if plan.original is not None else None,
    )


def frequency_finishers(
    stage: SourceRead, fields: Mapping[str, ir.Value]
) -> tuple[tuple[str, Literal["rank_bounds", "duckdb_native", "postgres_native"]], ...]:
    """Retain each original reducer's already-qualified numeric finish recipe."""
    from marivo.analysis.compiler.graph_plan import read_members
    from marivo.analysis.compiler.quantiles import quantile_bounds
    from marivo.semantic._numeric_lowering import lower_row

    result = []
    for member in read_members(stage) or (stage,):
        reduction = require_reduction(member)
        node = reduction.node
        assert isinstance(node, SourceAggregate)
        finisher: Literal["rank_bounds", "duckdb_native", "postgres_native"] = "rank_bounds"
        if node.reducer != "count_distinct" and not quantile_bounds(reduction, fields):
            dtype = lower_row(node.operand, fields).value.type()
            if dtype.is_decimal():
                raise AnalysisStageUnavailableError(
                    stage="attribute_state_binding",
                    expected="the original qualified Decimal bound finisher",
                    received="a native Decimal quantile value requiring an unqualified state finisher",
                    repair="Inspect the original exact quantile route; this native Decimal finishing variant has no qualified replacement-state consumer. No business row was read.",
                    target="analysis_frame.attribute",
                )
            assert reduction.provider in ("duckdb", "postgres")
            q = 0.5 if node.reducer == "median" else node.q
            if reduction.provider == "duckdb" and q not in (0.0, 1.0):
                raise AnalysisStageUnavailableError(
                    stage="attribute_state_binding",
                    expected="a qualified exact quantile state finisher preserving the original native operation order",
                    received=f"duckdb: native interpolating quantile q={q!r} has no qualified fixed-state finisher",
                    repair="Inspect the original observations or use a business-appropriate Decimal source representation only if that matches the intended metric. Native DuckDB interpolation remains available to ordinary observe/compare; no business row was read and attribution does not substitute an algorithm.",
                    target="analysis_frame.attribute",
                )
            finisher = "duckdb_native" if reduction.provider == "duckdb" else "postgres_native"
        result.append((reduction.value_slot, finisher))
    return tuple(result)
