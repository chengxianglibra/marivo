"""Admission of same-source reductions over a compatible contribution domain."""

from __future__ import annotations

from dataclasses import replace

from marivo._definition.expressions import (
    EntityCount,
    MetricWindow,
    PerReduce,
    PerWeightedMean,
    RowField,
    SourceAggregate,
    SourceWeightedMean,
)
from marivo._definition.graph import walk
from marivo._definition.relations import PopulationContains
from marivo._definition.types import AxisBinding
from marivo._kernel.refs import SemanticKind
from marivo._kernel.validation import is_instance
from marivo.analysis.binding.programs import FormulaProgram
from marivo.analysis.compiler.query_stages import (
    AttributeRead,
    ComponentRead,
    LocalCompute,
    QueryPlan,
    QueryStage,
    ReductionRead,
    SourceReadStage,
    read_components,
    read_reduction,
    require_components,
    require_reduction,
)
from marivo.analysis.model.data_types import DataColumn, DecimalDataType, ScalarDataType
from marivo.datasource.spi import native_aggregate, query_dialect


def source_merge_issue(stage: SourceReadStage, *, ordinary_samples: bool = False) -> str | None:
    """Explain the first physical boundary that prevents a common aggregate."""
    reduction = require_reduction(stage)
    value = next(column for column in stage.columns if column.slot_id == reduction.value_slot)
    reasons = (
        (
            not isinstance(stage.program, (ReductionRead, ComponentRead, AttributeRead)),
            "Specialized source payloads retain their own execution program.",
        ),
        (not stage.binding_id, "No qualified shared source binding."),
        (
            bool(stage.inputs or stage.parameters),
            "Dependent source parameters retain their execution boundary.",
        ),
        (
            bool(stage.preparation_checks),
            "Source-wide preparation checks retain their execution boundary.",
        ),
        (
            reduction.window_key is not None,
            "Window contributions retain their own period and grid.",
        ),
        (
            bool(
                (reduction.sampled and not ordinary_samples) or reduction.preserve_sample_presence
            ),
            "Sample presence retains its own domain.",
        ),
        (
            reduction.sampled
            and (
                not isinstance(stage.program, ReductionRead)
                or bool(
                    reduction.calendar or reduction.calendars or reduction.sample_calendar_reads
                )
                or any(path.startswith("__sample_assigned_") for path, _ in stage.field_columns)
            ),
            "Sample calendars, allocation and original components retain their execution boundary.",
        ),
        (reduction.duration is not None, "Duration reductions retain their exact tick completion."),
        (
            not (
                value.dtype in (ScalarDataType("int64"), ScalarDataType("float64"))
                or isinstance(value.dtype, DecimalDataType)
            ),
            "Result carrier has no common-aggregate qualification.",
        ),
    )
    return next((reason for refused, reason in reasons if refused), None)


def _ordinary_sample_formula(stage: QueryStage) -> bool:
    """Admit only represented field samples without enclosing domain completion."""
    if not isinstance(stage, LocalCompute) or not isinstance(stage.program, FormulaProgram):
        return False
    program = stage.program
    if (
        program.calendar is not None
        or program.populations
        or program.output_population is not None
        or program.parent_scope is not None
        or program.counts
    ):
        return False
    closure = walk((node for _, node in program.outputs), resolve=dict(program.declared).get)
    for node in closure.order:
        if isinstance(node, MetricWindow):
            return False
        if isinstance(node, (PerReduce, PerWeightedMean)) and not (
            isinstance(node.samples, tuple)
            and all(
                is_instance(axis, AxisBinding)
                and axis.field is not None
                and axis.field.ref.kind is SemanticKind.DIMENSION
                for axis in node.samples
            )
        ):
            return False
    return True


def _conditional_member(stage: SourceReadStage) -> bool:
    reduction = require_reduction(stage)
    node = reduction.node
    if (
        not isinstance(stage.program, ReductionRead)
        or not stage.conditional_binding_id
        or stage.attribute_state
        or stage.attribute_native
        or reduction.sampled
        or any(
            isinstance(item, PopulationContains)
            for predicate in stage.predicates
            for item in predicate.inputs
        )
        or query_dialect(reduction.provider).conditional_aggregate_style == "none"
    ):
        return False
    if isinstance(node, EntityCount):
        return True
    if isinstance(node, SourceWeightedMean):
        return isinstance(node.value, RowField) and isinstance(node.weight, RowField)
    if not isinstance(node.operand, RowField):
        return False
    return node.reducer in {"count", "sum", "mean", "min", "max"} or (
        node.reducer in {"count_distinct", "median", "quantile"}
        and native_aggregate(reduction.provider, node.reducer) is not None
    )


def _source_null_policy(stage: SourceReadStage) -> str:
    node = require_reduction(stage).node
    return node.nulls if isinstance(node, (SourceAggregate, SourceWeightedMean)) else "ignore"


def _common_domain(left: SourceReadStage, right: SourceReadStage) -> bool:
    first, second = require_reduction(left), require_reduction(right)
    left_fields = dict(left.field_columns)
    return (
        left.period == right.period
        and left.keys == right.keys
        and all(left_fields.get(path, column) == column for path, column in right.field_columns)
        and first.axes == second.axes
        and first.calendar == second.calendar
        and first.calendars == second.calendars
        and first.sample_calendar_reads == second.sample_calendar_reads
        and first.time_column == second.time_column
        and first.sampled == second.sampled
        and tuple(column for column in left.columns if column.slot_id in left.keys)
        == tuple(column for column in right.columns if column.slot_id in right.keys)
        and (
            (read_components(left) is None and read_components(right) is None)
            or (
                read_components(left) is not None
                and read_components(right) is not None
                and require_components(left).identity == require_components(right).identity
            )
        )
    )


def _source_groups(
    eligible: tuple[tuple[SourceReadStage, tuple[str, ...]], ...], *, conditional: bool
) -> tuple[tuple[SourceReadStage, ...], ...]:
    groups: dict[
        tuple[str, str, str | None, tuple[str, ...], type[object], bool, bool],
        list[SourceReadStage],
    ] = {}
    for stage, users in eligible:
        if conditional and not _conditional_member(stage):
            continue
        identity = (
            stage.conditional_binding_id if conditional else stage.binding_id,
            stage.source_id,
            None if conditional else _source_null_policy(stage),
            users,
            type(stage.program),
            stage.attribute_state,
            stage.attribute_native,
        )
        group = groups.get(identity)
        if group is None:
            groups[identity] = [stage]
            continue
        prior = group[0]
        if (
            all(_common_domain(member, stage) for member in group)
            if conditional
            else prior.predicates == stage.predicates and _common_domain(prior, stage)
        ):
            group.append(stage)
    return tuple(tuple(group) for group in groups.values() if len(group) > 1)


def _conditional_presence(stage: SourceReadStage) -> SourceReadStage:
    presence = DataColumn(
        "__numeric_present", "__numeric_present", ScalarDataType("boolean"), False
    )
    if presence.slot_id in {column.slot_id for column in stage.columns}:
        return stage
    return replace(stage, columns=(*stage.columns, presence))


def select_source_groups(
    plan: QueryPlan,
) -> tuple[tuple[tuple[SourceReadStage, ...], ...], frozenset[str]]:
    """Choose compatible reducers and identify separately gated occurrences."""
    consumers = dict(plan.consumers)
    ordinary_samples = {stage.stage_id for stage in plan.stages if _ordinary_sample_formula(stage)}
    eligible = tuple(
        (stage, users)
        for stage in plan.stages
        if isinstance(stage, SourceReadStage)
        and read_reduction(stage) is not None
        and len(users := consumers.get(stage.stage_id, ())) == 1
        and source_merge_issue(stage, ordinary_samples=users[0] in ordinary_samples) is None
    )
    conditional = (
        tuple(
            tuple(_conditional_presence(stage) for stage in group)
            for group in _source_groups(eligible, conditional=True)
            if any(
                stage.predicates != group[0].predicates
                or _source_null_policy(stage) != _source_null_policy(group[0])
                for stage in group[1:]
            )
        )
        if plan.optimization_policy.allows("conditional_source_merge")
        else ()
    )
    conditional_ids = frozenset(stage.stage_id for group in conditional for stage in group)
    ordinary = (
        _source_groups(
            tuple(
                (stage, users) for stage, users in eligible if stage.stage_id not in conditional_ids
            ),
            conditional=False,
        )
        if plan.optimization_policy.allows("source_merge")
        else ()
    )
    return (*ordinary, *conditional), conditional_ids
