"""Move demand on complete ordinary keys into a qualified direct reduction."""

from __future__ import annotations

import operator
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final, Literal, TypeAlias

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo._definition.correspondence import ComparisonAxis
from marivo._definition.expressions import CompareOp, MetricWindow, SourceAggregate
from marivo._definition.query import (
    CoordinateSlot,
    FilterValues,
    ObserveQuery,
    ValueBinary,
    ValueExpression,
    ValueLiteral,
    ValueUnary,
)
from marivo._definition.relations import (
    LiteralPopulation,
    PopulationClassify,
    PopulationProgram,
    PopulationProject,
    SourceIdentities,
)
from marivo._definition.rows import RowField
from marivo._definition.types import AxisBinding, CalendarCoordinate, CoordinateValue, LiteralValue
from marivo.analysis.binding.programs import FormulaProgram
from marivo.analysis.compiler.lowered_query import LoweredQuery, LoweredRead
from marivo.analysis.compiler.query_stages import (
    LocalCompute,
    ProjectionRead,
    ReductionRead,
    RuleDecision,
    SourceReadStage,
)
from marivo.analysis.compiler.source_predicates import string_bytes
from marivo.analysis.core.frame_operations import FixFrameValues, FrameOperation
from marivo.analysis.materialization.ordinary_key_admission import (
    stored_group_input,
    stored_key_field,
    stored_population_read,
    supported_reduction,
)
from marivo.analysis.materialization.query_coverage_fusion import rebind_covered_business
from marivo.analysis.materialization.window_key_admission import qualified_calendar_group_slot
from marivo.analysis.model.data_types import DataColumn
from marivo.analysis.model.local_laws import selection_predicate_columns
from marivo.datasource.spi import TableSourceIR

if TYPE_CHECKING:
    from marivo.analysis.binding.graph import BoundGraph
    from marivo.analysis.materialization.frame_inputs import CapturedFrameInputs
    from marivo.analysis.materialization.prepared_sources import PreparedSources
    from marivo.analysis.model.optimization_policy import OptimizationPolicy
    from marivo.datasource.spi import BoundSource, SourceSession

_Comparison: TypeAlias = Callable[[ir.Value, ir.Value], ir.BooleanValue]
_Operation: TypeAlias = CompareOp | Literal["is_null", "not_null", "fix"]
_COMPARISONS: dict[str, _Comparison] = {
    "eq": operator.eq,
    "ne": operator.ne,
    "lt": operator.lt,
    "le": operator.le,
    "gt": operator.gt,
    "ge": operator.ge,
}
_REVERSE: dict[str, CompareOp] = {
    "eq": "eq",
    "ne": "ne",
    "lt": "gt",
    "le": "ge",
    "gt": "lt",
    "ge": "le",
}
_KEY_RULE: Final = "L7_key_pushdown"


@dataclass(frozen=True, slots=True)
class _KeyTest:
    slot: str
    operation: _Operation
    value: LiteralValue = None


def _scalar_slot(expression: ValueExpression, groups: tuple[tuple[str, ...], ...]) -> str | None:
    if isinstance(expression, CoordinateSlot) and 0 <= expression.index < len(groups):
        group = groups[expression.index]
        if len(group) == 1:
            return group[0]
    return None


def _where_keys(
    expression: ValueExpression, groups: tuple[tuple[str, ...], ...]
) -> tuple[_KeyTest, ...]:
    if isinstance(expression, ValueUnary):
        reversed_null = expression.operation == "not"
        test = expression.operand if reversed_null else expression
        if isinstance(test, ValueUnary) and test.operation == "is_null":
            slot = _scalar_slot(test.operand, groups)
            if slot is not None:
                return (_KeyTest(slot, "not_null" if reversed_null else "is_null"),)
    if isinstance(expression, ValueBinary):
        if expression.operation == "and":
            return (*_where_keys(expression.left, groups), *_where_keys(expression.right, groups))
        if expression.operation in ("eq", "ne", "lt", "le", "gt", "ge"):
            slot = _scalar_slot(expression.left, groups)
            if slot is not None and isinstance(expression.right, ValueLiteral):
                return (_KeyTest(slot, expression.operation, expression.right.value),)
            slot = _scalar_slot(expression.right, groups)
            if slot is not None and isinstance(expression.left, ValueLiteral):
                return (_KeyTest(slot, _REVERSE[expression.operation], expression.left.value),)
    return ()


def _key_tests(
    operation: FilterValues | FixFrameValues,
    groups: tuple[tuple[str, ...], ...],
    columns: tuple[DataColumn, ...],
    axes: tuple[ComparisonAxis, ...],
) -> tuple[_KeyTest, ...]:
    if len(axes) != len(groups):
        return ()
    if isinstance(operation, FixFrameValues):
        if not 0 <= operation.position < len(groups):
            return ()
        if not isinstance(axes[operation.position], AxisBinding):
            return ()
        support = groups[operation.position]
        if len(support) != len(operation.key):
            return ()
        return tuple(
            _KeyTest(slot, "fix", value) for slot, value in zip(support, operation.key, strict=True)
        )
    keys = frozenset(slot for group in groups for slot in group)
    values = tuple(column for column in columns if column.slot_id not in keys)
    if selection_predicate_columns(operation.predicate, values, columns, groups) is None:
        return ()
    ordinary = frozenset(
        slot
        for axis, group in zip(axes, groups, strict=True)
        if isinstance(axis, AxisBinding)
        for slot in group
    )
    return tuple(test for test in _where_keys(operation.predicate, groups) if test.slot in ordinary)


def _string_key_is_exact(owner: SourceSession, bound: BoundSource, field: ops.Field) -> bool:
    dialect = owner.provider.dialect
    try:
        if dialect.original_input is not None:
            # The qualified input already establishes native string equality.
            return True
        if dialect.original_string_column is not None:
            return owner.string_facts.original_key_string(bound, field.name)
        if dialect.original_strings is not None:
            return owner.string_facts.original_string_issue(bound, field.name) is None
    except Exception:
        # Collation qualification is optional metadata. Unknown facts leave the
        # original grouped read in place and its business failures stay visible.
        return False
    return False


def _physical_input_is_exact(owner: SourceSession, bound: BoundSource, leaf: ops.Node) -> bool:
    try:
        return owner.stored_projection.proves(
            bound
        ) and owner.string_facts.original_input_qualified(bound, leaf, owner.table_join_issue)
    except Exception:
        # Unavailable optional metadata keeps the original application scope.
        return False


def _sum_group_is_singleton(
    aggregate: ops.Aggregate, prepared: PreparedSources, stage: SourceReadStage
) -> bool:
    schema = next(
        (
            item
            for item in prepared.schemas
            if item.source_id == stage.source_id and item.occurrence == stage.occurrence
        ),
        None,
    )
    if schema is None or not schema.original_identity:
        return False
    grouped = {
        field.name
        for value in aggregate.groups.values()
        if (field := stored_key_field(value)) is not None and not field.dtype.nullable
    }
    return all(item.column in grouped for item in schema.original_identity)


def _group_issue(
    aggregate: ops.Aggregate,
    prepared: PreparedSources,
    stage: SourceReadStage,
    required_nonnull: frozenset[str],
    calendar_group_slot: str | None,
) -> str | None:
    physical = aggregate.parent.find(ops.DatabaseTable)
    if len(physical) != 1 or (calendar_group_slot is None and not stored_group_input(aggregate)):
        return "the grouping input is not one direct physical table relation"
    owner = prepared.sources.get(stage.source_id, prepared.source)
    leaf = physical[0]
    bound = next(
        (
            item
            for path, item in prepared.bound.items()
            if isinstance(item.source, TableSourceIR)
            and prepared.sources.get(path, prepared.source) is owner
            and item.relation.op() == leaf
        ),
        None,
    )
    if (
        bound is None
        or not owner.uses_native_provider()
        or not _physical_input_is_exact(owner, bound, leaf)
    ):
        return "the grouped input requires a qualified native stored table and grouping collation"
    for slot, value in aggregate.groups.items():
        if slot == calendar_group_slot:
            continue
        field = stored_key_field(value)
        if (
            field is None
            or field.rel != leaf
            or not (field.dtype.is_signed_integer() or field.dtype.is_string())
        ):
            return "only direct ordinary signed integer or string keys are qualified"
        if slot in required_nonnull and value.dtype.nullable:
            return "required nonnullable key checks stay on the complete source domain"
        if field.dtype.is_string() and not _string_key_is_exact(owner, bound, field):
            return "string keys require exact returned-byte grouping collation"
    if any(isinstance(reduction, ops.Sum) for reduction in aggregate.find(ops.Reduction)) and not (
        _sum_group_is_singleton(aggregate, prepared, stage)
    ):
        return "a SUM over multiple source rows can fail in an excluded key group"
    if any(
        not isinstance(reduction, ops.CountStar)
        and (
            not isinstance(reduction.args[0], ops.Value)
            or (operand := stored_key_field(reduction.args[0])) is None
            or not owner.stored_projection.preserves_reduction_carrier(bound, operand)
        )
        for reduction in aggregate.find(ops.Reduction)
    ):
        return "the aggregate arguments require directly stored scalar values with qualified native carriers"
    return None


def _predicate(test: _KeyTest, key: ir.Value, provider: str) -> ir.BooleanValue | None:
    value = test.value
    if test.operation == "not_null":
        return key.notnull()
    if test.operation == "is_null" or (test.operation == "fix" and value is None):
        return key.isnull()
    if key.type().is_signed_integer() and type(value) is int and -(2**63) <= value < 2**63:
        literal = ibis.literal(value, type="int64")
    elif (
        key.type().is_string() and isinstance(value, str) and test.operation in ("eq", "ne", "fix")
    ):
        if "\x00" in value:
            return None
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            return None
        key = string_bytes(key, provider)
        literal = string_bytes(ibis.literal(value), provider)
    else:
        return None
    if test.operation == "fix":
        return key.identical_to(literal)
    return _COMPARISONS[test.operation](key, literal)


def _reduction_issue(stage: SourceReadStage, read: LoweredRead, *, window: bool) -> str | None:
    if stage.preparation_checks or read.numeric_checks or read.population_checks:
        return "required full-domain source checks stay on the original grouped read"
    if stage.inputs or stage.parameters or not isinstance(stage.program, ReductionRead):
        return "the output is not a direct independent native reduction"
    reduction = stage.program.reduction
    if reduction.sampled or (
        (reduction.calendars or reduction.calendar is not None or reduction.window_key is not None)
        and not window
    ):
        return "window or sampled contributions keep their original history and support"
    node = reduction.node
    if (
        not isinstance(node, SourceAggregate)
        or not isinstance(node.operand, RowField)
        or node.reducer not in ("sum", "min", "max", "count")
        or node.nulls != "ignore"
        or reduction.duration is not None
    ):
        return "only direct sum, min, max and count without required checks are qualified"
    return None


def _restrict_read(
    prepared: PreparedSources,
    stage: SourceReadStage,
    read: LoweredRead,
    tests: tuple[_KeyTest, ...],
    groups: tuple[tuple[str, ...], ...],
    axes: tuple[ComparisonAxis, ...],
    *,
    calendar_slot: str | None = None,
) -> tuple[LoweredRead | None, str]:
    relation = read.expression.op()
    supported = supported_reduction(relation, stage)
    if supported is None:
        return None, "the lowered read is not one exact complete-key native grouping"
    aggregate, crop_input = supported
    if not (
        stage.keys
        and tuple(aggregate.groups)
        == stage.keys
        == tuple(
            slot
            for group in groups
            for slot in group
            if slot != calendar_slot or slot in stage.keys
        )
        and set(read.expression.columns) == {column.slot_id for column in stage.columns}
    ):
        return None, "the lowered read is not one exact complete-key native grouping"
    calendar_group_slot = (
        qualified_calendar_group_slot(aggregate, stage) if calendar_slot in stage.keys else None
    )
    if calendar_slot in stage.keys and calendar_group_slot != calendar_slot:
        return (
            None,
            "the calendar grouping needs an exact stored DATE day or window endpoint",
        )
    issue = _reduction_issue(stage, read, window=calendar_group_slot is not None)
    if issue is not None:
        return None, issue
    if any(test.slot not in stage.keys or test.slot == calendar_slot for test in tests):
        return (
            None,
            "the full predicate needs qualified ordinary key terms before any rows can be cropped",
        )
    required_nonnull = frozenset(
        (
            *(column.slot_id for column in stage.columns if not column.nullable),
            *(
                slot
                for axis, group in zip(axes, groups, strict=True)
                if isinstance(axis, AxisBinding) and axis.role == "identity"
                for slot in group
            ),
        )
    )
    issue = _group_issue(aggregate, prepared, stage, required_nonnull, calendar_group_slot)
    if issue is not None:
        return None, issue
    provider = prepared.sources.get(stage.source_id, prepared.source).provider.name
    predicates: list[ir.BooleanValue] = []
    for test in tests:
        value = aggregate.groups[test.slot]
        if crop_input != aggregate.parent:
            assert isinstance(value, ops.Field)
            value = ops.Field(crop_input, value.name)
        predicate = _predicate(test, value.to_expr(), provider)
        if predicate is None:
            return None, "the key comparison requires an unqualified operator or literal carrier"
        predicates.append(predicate)
    cropped = crop_input.to_expr().filter(*predicates)
    restricted = relation.replace({crop_input: cropped.op()}).to_expr()
    assert isinstance(restricted, ir.Table)
    return replace(
        read, expression=restricted
    ), "ordinary key demand reaches the original native grouping input"


def _restrict_group(
    lowered: LoweredQuery,
    prepared: PreparedSources,
    tests: tuple[_KeyTest, ...],
    axes: tuple[AxisBinding, ...],
) -> tuple[LoweredQuery | None, str]:
    plan = lowered.plan
    if len(plan.stages) != 1:
        return None, "multiple branches require synchronized selection"
    stage = plan.stages[0]
    if not isinstance(stage, SourceReadStage) or stage.stage_id != plan.publish:
        return None, "the source grouping is not the only published stage"
    result, reason = _restrict_read(
        prepared, stage, lowered.read(stage.stage_id), tests, plan.output_groups, axes
    )
    return (None if result is None else replace(lowered, reads=(result,))), reason


def _direct_calendar_formula(program: FormulaProgram) -> bool:
    return (
        isinstance(program.calendar, CalendarCoordinate)
        and not program.calendar.actual_only
        and program.output_population is None
        and not program.populations
        and not program.counts
        and not program.calendar_samples
        and not program.sample_matches
        and all(
            isinstance(node.operand if isinstance(node, MetricWindow) else node, SourceAggregate)
            for _, node in program.outputs
        )
    )


def _restrict_calendar_groups(
    lowered: LoweredQuery,
    prepared: PreparedSources,
    tests: tuple[_KeyTest, ...],
    axes: tuple[ComparisonAxis, ...],
) -> tuple[LoweredQuery | None, str]:
    plan = lowered.plan
    groups = plan.output_groups
    slots = tuple(
        group[0]
        for axis, group in zip(axes, groups, strict=True)
        if isinstance(axis, CalendarCoordinate) and len(group) == 1
    )
    if len(slots) != 1 or any(
        not isinstance(axis, (AxisBinding, CalendarCoordinate)) for axis in axes
    ):
        return None, "only one explicit calendar can share ordinary output restriction"
    (calendar_slot,) = slots
    if any(test.slot == calendar_slot for test in tests):
        return (
            None,
            "calendar and window endpoint predicates retain the original contribution history",
        )

    *sources, publish = plan.stages
    if not (
        isinstance(publish, LocalCompute)
        and isinstance(publish.program, FormulaProgram)
        and publish.stage_id == plan.publish
        and _direct_calendar_formula(publish.program)
        and set(publish.inputs) == {stage.stage_id for stage in sources}
    ):
        return None, "calendar output requires direct native calendar and independent reductions"
    ordinary = tuple(slot for group in groups for slot in group if slot != calendar_slot)
    complete = tuple(slot for group in groups for slot in group)
    replacements: list[LoweredRead] = []
    for stage in sources:
        if not isinstance(stage, SourceReadStage) or stage.keys not in (ordinary, complete):
            return (
                None,
                "all window and independent source reads must expose the same ordinary keys",
            )
        result, reason = _restrict_read(
            prepared,
            stage,
            lowered.read(stage.stage_id),
            tests,
            groups,
            axes,
            calendar_slot=calendar_slot,
        )
        if result is None:
            return None, reason
        replacements.append(result)
    return (
        replace(lowered, reads=tuple(replacements)),
        "restrict every calendar and independent read on the same ordinary keys while preserving complete contribution time ranges",
    )


def _literal_matches(test: _KeyTest, value: CoordinateValue) -> bool | None:
    if test.operation == "not_null":
        return value is not None
    if test.operation == "is_null" or (test.operation == "fix" and test.value is None):
        return value is None
    if value is None:
        return False
    needle = test.value
    if isinstance(value, str) and isinstance(needle, str):
        if test.operation in ("eq", "fix"):
            return value == needle
        if test.operation == "ne":
            return value != needle
        return None
    if type(value) is int and type(needle) is int:
        if test.operation in ("eq", "fix"):
            return value == needle
        if test.operation == "ne":
            return value != needle
        if test.operation == "lt":
            return value < needle
        if test.operation == "le":
            return value <= needle
        if test.operation == "gt":
            return value > needle
        if test.operation == "ge":
            return value >= needle
    return None


def _crop_literal_output(
    domain: LiteralPopulation,
    groups: tuple[tuple[str, ...], ...],
    tests: tuple[_KeyTest, ...],
) -> LiteralPopulation | None:
    if any(len(group) != 1 for group in groups) or len(groups) != len(domain.axes):
        return None
    positions = {group[0]: index for index, group in enumerate(groups)}
    retained: list[tuple[CoordinateValue, ...]] = []
    for row in domain.keys:
        matches = tuple(_literal_matches(test, row[positions[test.slot]]) for test in tests)
        if None in matches:
            return None
        if all(matches):
            retained.append(row)
    return replace(domain, keys=tuple(retained))


def _crop_source_output(
    lowered: LoweredQuery,
    prepared: PreparedSources,
    publish: LocalCompute,
    program: FormulaProgram,
    business: SourceReadStage,
    original: LoweredRead,
    tests: tuple[_KeyTest, ...],
    domain: PopulationProgram,
) -> LoweredRead | None:
    if not isinstance(domain.root, (SourceIdentities, PopulationClassify, PopulationProject)):
        return None
    if isinstance(domain.root, SourceIdentities) and domain.root.at is not None:
        return None
    binding = next((item for item in program.populations if item.definition == domain), None)
    if binding is None or binding.stage_id not in publish.inputs:
        return None
    stage = lowered.plan.source_read_stage(binding.stage_id)
    owner = prepared.sources.get(business.source_id, prepared.source)
    if not (
        prepared.sources.get(stage.source_id, prepared.source) is owner
        and isinstance(stage.program, ProjectionRead)
        and stage.keys == binding.keys
        and len(stage.keys) == len(publish.keys)
        and not stage.inputs
        and not stage.parameters
        and not stage.preparation_checks
    ):
        return None
    read = lowered.read(binding.stage_id)
    physical = original.expression.op().find(ops.DatabaseTable)
    if (
        len(physical) != 1
        or read.numeric_checks
        or read.output_coverage is not None
        or not stored_population_read(read.expression, physical[0])
    ):
        return None
    slots = dict(zip(publish.keys, binding.keys, strict=True))
    expression = read.expression
    required_nonnull = frozenset(column.slot_id for column in stage.columns if not column.nullable)
    aggregate = supported_reduction(original.expression.op(), business)
    assert aggregate is not None
    for key, slot in slots.items():
        field = stored_key_field(expression[slot].op())
        if (
            field is None
            or field != stored_key_field(aggregate[0].groups[key])
            or (slot in required_nonnull and field.dtype.nullable)
        ):
            return None
    predicates: list[ir.BooleanValue] = []
    for test in tests:
        predicate = _predicate(test, expression[slots[test.slot]], owner.provider.name)
        if predicate is None:
            return None
        predicates.append(predicate)
    # PopulationCheck relations are independently retained on their full domain.
    return replace(read, expression=expression.filter(*predicates))


def _crop_covered_observation(
    lowered: LoweredQuery,
    prepared: PreparedSources,
    tests: tuple[_KeyTest, ...],
    domain: LiteralPopulation | PopulationProgram,
) -> tuple[LoweredQuery | None, str]:
    plan = lowered.plan
    if len(plan.stages) < 2:
        return None, "the output domain requires a checked source-grouping read"
    stage, *_, publish = plan.stages
    if not (
        isinstance(stage, SourceReadStage)
        and isinstance(publish, LocalCompute)
        and isinstance(publish.program, FormulaProgram)
        and publish.stage_id == plan.publish
        and publish.inputs[:1] == (stage.stage_id,)
    ):
        return None, "the output domain requires a single checked source-grouping read"
    program = publish.program
    read = lowered.read(stage.stage_id)
    coverage = read.output_coverage
    if not (
        program.population_checked
        and program.output_population == domain
        and len(program.values) == len(program.outputs) == 1
        and isinstance(program.outputs[0][1], SourceAggregate)
        and not program.counts
        and program.calendar is None
        and coverage is not None
        and coverage.keys == stage.keys == publish.keys
    ):
        return None, "the complete output Population coverage receipt must exist before cropping"
    qualified_stage = replace(
        stage,
        preparation_checks=tuple(
            check for check in stage.preparation_checks if check[0] != coverage.alias
        ),
    )
    limited, reason = _restrict_read(
        prepared, qualified_stage, coverage.business, tests, plan.output_groups, domain.axes
    )
    if limited is None:
        return None, reason
    rebound = rebind_covered_business(read, limited)
    if isinstance(domain, PopulationProgram):
        selected = _crop_source_output(
            lowered, prepared, publish, program, stage, coverage.business, tests, domain
        )
        if selected is None:
            return None, "the checked output Population needs a qualified same-source projection"
        return (
            replace(
                lowered,
                reads=tuple(
                    rebound
                    if item.stage_id == rebound.stage_id
                    else selected
                    if item.stage_id == selected.stage_id
                    else item
                    for item in lowered.reads
                ),
            ),
            "key selection crops the checked source Population and business grouping on the same axes",
        )
    if len(plan.stages) != 2:
        return None, "the checked literal output requires a single business grouping"
    cropped = _crop_literal_output(domain, plan.output_groups, tests)
    if cropped is None:
        return None, "the literal output Population requires exact scalar key comparison"
    completed = replace(program, output_population=cropped)
    return (
        LoweredQuery(
            replace(
                plan,
                stages=(stage, replace(publish, program=completed)),
                original=completed if plan.original == program else plan.original,
            ),
            (rebound,),
        ),
        "key selection crops the checked literal output and business grouping on the same axes",
    )


def push_source_key_predicate(
    captured: CapturedFrameInputs,
    input_id: str,
    graph: BoundGraph,
    lowered: LoweredQuery,
    prepared: PreparedSources,
    protected_inputs: frozenset[int],
    policy: OptimizationPolicy,
) -> LoweredQuery:
    """Apply L7 while retaining the actual local where or fix operation."""
    if not policy.allows(_KEY_RULE):
        return lowered
    consumers = tuple(
        captured.expressions[identifier]
        for identifier, dependencies in captured.dependencies.items()
        for dependency in dependencies
        if dependency == input_id
    )
    keys = tuple(
        node.operation
        for node in consumers
        if isinstance(node, FrameOperation)
        and isinstance(node.operation, (FilterValues, FixFrameValues))
    )
    if not keys:
        return lowered
    observation = graph.root
    replacement = None
    plan = lowered.plan
    published = next(stage for stage in plan.stages if stage.stage_id == plan.publish)
    if (
        len(consumers) != 1
        or id(captured.expressions[input_id]) in protected_inputs
        or input_id in captured.games
        or input_id in captured.requested
    ):
        reason = "the full source Frame has another consumer or a required checkpoint"
    elif not isinstance(observation, ObserveQuery):
        reason = "the input is not a pending source observation"
    elif observation.count_populations:
        reason = "Population counts require their independent complete support"
    elif not (tests := _key_tests(keys[0], plan.output_groups, published.columns, observation.by)):
        reason = "the predicate needs qualified ordinary key terms and total residual comparisons"
    elif observation.population is not None:
        replacement, reason = _crop_covered_observation(
            lowered, prepared, tests, observation.population
        )
    elif len(
        ordinary := tuple(axis for axis in observation.by if isinstance(axis, AxisBinding))
    ) != len(observation.by):
        replacement, reason = _restrict_calendar_groups(lowered, prepared, tests, observation.by)
    else:
        replacement, reason = _restrict_group(lowered, prepared, tests, ordinary)
    chosen = lowered if replacement is None else replacement
    plan = chosen.plan
    decision = RuleDecision(
        _KEY_RULE, "adopted" if replacement is not None else "refused", (plan.publish,), reason
    )
    return replace(
        chosen,
        plan=replace(plan, optimization_decisions=(*plan.optimization_decisions, decision)),
    )
