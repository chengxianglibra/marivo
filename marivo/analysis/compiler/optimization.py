"""Bounded Query demand, registered local laws and invocation compilation reuse."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Literal, TypeAlias

import ibis
import ibis.expr.types as ir

from marivo._definition.expressions import EntityCount, SourceAggregate
from marivo._definition.query import (
    CoordinateSlot,
    FilterValues,
    KeyImage,
    LiteralValues,
    PresenceSlot,
    ReadFields,
    ReduceValues,
    SelectValues,
    TopValues,
    ValueBinary,
    ValueExpression,
    ValueLiteral,
    ValueSlot,
    ValueUnary,
)
from marivo._definition.types import RULE_VERSION, Owner
from marivo.analysis.compiler.graph_plan import (
    FrameCompute,
    FrameInput,
    FrameKeysInput,
    LocalCompute,
    QueryPlan,
    QueryStage,
    SourceCheckRoute,
    SourceRead,
    SourceSchema,
)
from marivo.analysis.compiler.observation import FormulaProgram, common_slot, ibis_type
from marivo.analysis.compiler.original import component_layout
from marivo.analysis.compiler.query_program import ResultProgram
from marivo.analysis.core.data_types import DataColumn, DecimalDataType, ScalarDataType
from marivo.analysis.core.graph import BoundGraph
from marivo.analysis.core.local_laws import (
    query_selection_fusion_issue,
)
from marivo.analysis.core.model import ResultQuantity
from marivo.analysis.methods.physical import QUERY_COMBINE_KERNEL

if TYPE_CHECKING:
    from marivo.analysis.compiler.graph_lowering import LoweredQuery


@dataclass(frozen=True, slots=True)
class RequiredSlots:
    """Transport needed after this stage has completed all its owning checks."""

    stage_id: str
    columns: tuple[str, ...]
    components: tuple[str, ...] = ()
    checks: tuple[str, ...] = ()
    retain_populations: bool = False


@dataclass(frozen=True, slots=True)
class FusionGroup:
    law: Literal["L1", "L8", "source_block"]
    members: tuple[str, ...]
    output: str
    checks: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class RuleDecision:
    rule: str
    status: Literal["adopted", "refused"]
    members: tuple[str, ...]
    reason: str


def required_for(plan: QueryPlan, stage_id: str) -> RequiredSlots:
    """Return the compiler's exact post-check transport contract."""
    for required in plan.required_slots:
        if required.stage_id == stage_id:
            return required
    stage = next(stage for stage in plan.stages if stage.stage_id == stage_id)
    return RequiredSlots(stage_id, tuple(column.slot_id for column in stage.columns))


def _support(stage: QueryStage) -> set[str]:
    required = set(stage.keys)
    if isinstance(stage, (FrameInput, FrameCompute, FrameKeysInput)):
        return {column.slot_id for column in stage.columns}
    if isinstance(stage, SourceRead):
        required.update(
            column.slot_id
            for column in stage.columns
            if column.slot_id.startswith(
                ("numeric_check.", "predicate_check.", "__numeric_present", "__sample_present")
            )
        )
    elif stage.result is not None and stage.result.output_layout is not None:
        layout = stage.result.output_layout
        values = {column.slot_id for column in layout.values}
        required.update(column.slot_id for column in layout.columns if column.slot_id not in values)
    return required


def _expressions(expression: ValueExpression, program: ResultProgram) -> set[str]:
    if isinstance(expression, ValueSlot):
        values = (
            program.input_layout.values
            if program.input_layout is not None
            else tuple(column for column in program.columns if column.slot_id not in program.keys)
        )
        return {values[expression.index].slot_id}
    if isinstance(expression, CoordinateSlot):
        return set(program.key_groups[expression.index])
    if isinstance(expression, PresenceSlot):
        return set()
    if isinstance(expression, ValueUnary):
        return _expressions(expression.operand, program)
    if isinstance(expression, ValueBinary):
        return _expressions(expression.left, program) | _expressions(expression.right, program)
    return set()


def _input_columns(stage: LocalCompute, wanted: set[str]) -> set[str]:
    program = stage.result
    if program is None:
        return set()
    operation = program.operation
    if isinstance(operation, SelectValues):
        # All expressions finish here, including strict expressions whose
        # completed output a later select discards. Native Deferred branching
        # remains inside its existing demand-aware numeric kernel.
        result = set().union(
            *(_expressions(expression, program) for _, expression in operation.values)
        )
        if program.input_layout is not None:
            values = {column.slot_id for column in program.input_layout.values}
            result.update(
                column.slot_id
                for column in program.input_layout.columns
                if column.slot_id not in values
            )
        return result
    if isinstance(operation, FilterValues):
        if isinstance(
            operation.predicate,
            (
                ValueSlot,
                CoordinateSlot,
                PresenceSlot,
                ValueLiteral,
                ValueUnary,
                ValueBinary,
            ),
        ):
            return wanted | _expressions(operation.predicate, program)
        return wanted | set(program.keys)
    if isinstance(operation, TopValues):
        return wanted | _expressions(operation.score, program)
    if isinstance(operation, ReduceValues):
        return set().union(
            *(
                _expressions(statistic.value, program)
                for _, statistic in operation.values
                if statistic.value is not None
            )
        ) | {slot for index in operation.positions for slot in program.key_groups[index]}
    if isinstance(operation, KeyImage):
        return set(program.keys)
    if isinstance(operation, ReadFields):
        return set(program.key_groups[0]) | {
            slot for index in operation.positions for slot in program.key_groups[index]
        }
    return {column.slot_id for column in program.columns}


def required_slots(plan: QueryPlan) -> tuple[RequiredSlots, ...]:
    """Merge every consumer's finite demand in one backward topological pass."""
    stages = {stage.stage_id: stage for stage in plan.stages}
    wanted = {stage.stage_id: set[str]() for stage in plan.stages}
    components = {stage.stage_id: set[str]() for stage in plan.stages}
    populations = dict.fromkeys(stages, False)
    wanted[plan.publish].update(column.slot_id for column in stages[plan.publish].columns)
    completed: dict[str, RequiredSlots] = {}
    stage_checks: dict[str, set[str]] = {}
    for check in plan.checks:
        stage_checks.setdefault(check.stage_id, set()).add(check.check_id)
    for stage in reversed(plan.stages):
        columns = wanted[stage.stage_id] | _support(stage)
        checks = stage_checks.get(stage.stage_id, set()).copy()
        if isinstance(stage, SourceRead):
            checks.update(name for name, _, _ in stage.preparation_checks)
            checks.update(
                name for name in columns if name.startswith(("numeric_check.", "predicate_check."))
            )
        completed[stage.stage_id] = RequiredSlots(
            stage.stage_id,
            tuple(column.slot_id for column in stage.columns if column.slot_id in columns),
            tuple(sorted(components[stage.stage_id])),
            tuple(sorted(checks)),
            populations[stage.stage_id],
        )
        if not stage.inputs:
            continue
        if isinstance(stage, SourceRead):
            for source in stage.inputs:
                wanted[source].update(stages[source].keys)
            continue
        if isinstance(stage, (FrameInput, FrameCompute, FrameKeysInput)):
            for source in stage.inputs:
                wanted[source].update(column.slot_id for column in stages[source].columns)
            continue
        needed = set(components[stage.stage_id])
        if stage.program is not None:
            # The formula completes before downstream projection. Its source
            # check flags remain data for native branch demand, never eagerly
            # raised merely because the full static graph contains a branch.
            for source in stage.inputs:
                wanted[source].update(column.slot_id for column in stages[source].columns)
                components[source].update(needed)
                populations[source] = populations[source] or bool(needed)
            continue
        first, *others = stage.inputs
        wanted[first].update(_input_columns(stage, columns))
        components[first].update(needed)
        populations[first] = populations[first] or populations[stage.stage_id] or bool(needed)
        for source in others:
            wanted[source].update(column.slot_id for column in stages[source].columns)
    return tuple(completed[stage.stage_id] for stage in plan.stages)


def _consumers(stages: tuple[QueryStage, ...]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    consumers: dict[str, list[str]] = {stage.stage_id: [] for stage in stages}
    for stage in stages:
        for source in stage.inputs:
            consumers[source].append(stage.stage_id)
    return tuple((stage.stage_id, tuple(consumers[stage.stage_id])) for stage in stages)


def _fuse(plan: QueryPlan) -> QueryPlan:
    stages = {stage.stage_id: stage for stage in plan.stages}
    removed: set[str] = set()
    groups: dict[str, FusionGroup] = {}
    decisions: list[RuleDecision] = []
    consumers = {stage_id: set(values) for stage_id, values in _consumers(plan.stages)}
    source_roots: dict[str, SourceRead] = {}
    check_stages = {check.stage_id for check in plan.checks}
    for original in plan.stages:
        stage = stages[original.stage_id]
        if isinstance(stage, SourceRead):
            if stage.reduction is not None and stage.source_id in source_roots:
                prior = source_roots[stage.source_id]
                decisions.append(
                    RuleDecision(
                        "source_merge",
                        "refused",
                        (prior.stage_id, stage.stage_id),
                        "Ordered contribution occurrences, policies and source numeric checks are retained; no registered equivalence proves a common aggregate.",
                    )
                )
            source_roots[stage.source_id] = stage
            continue
        if not isinstance(stage, LocalCompute) or stage.result is None or len(stage.inputs) != 1:
            continue
        inner = stages[stage.inputs[0]]
        if not isinstance(inner, LocalCompute) or inner.result is None:
            continue
        first, second = inner.result.operation, stage.result.operation
        pair = (inner.stage_id, stage.stage_id)
        if isinstance(second, FilterValues) and isinstance(first, FilterValues):
            values = (
                inner.result.input_layout.values
                if inner.result.input_layout is not None
                else tuple(
                    column
                    for column in inner.result.columns
                    if column.slot_id not in inner.result.keys
                )
            )
            same = (
                inner.columns == stage.columns
                and inner.keys == stage.keys
                and inner.result.key_groups == stage.result.key_groups
                and inner.result.columns == stage.result.columns
            )
            issue = query_selection_fusion_issue(
                first,
                second,
                values,
                inner.result.columns,
                inner.result.key_groups,
                single_consumer=consumers[inner.stage_id] == {stage.stage_id}
                and inner.stage_id != plan.publish,
                same_layout=same,
                pending_checks=bool(check_stages & set(pair)),
                original_boundary=False,
            )
            if issue is None:
                assert isinstance(first.predicate, ValueExpression)
                assert isinstance(second.predicate, ValueExpression)
                operation = FilterValues(ValueBinary("and", first.predicate, second.predicate))
                stages[stage.stage_id] = replace(
                    stage, inputs=inner.inputs, result=replace(stage.result, operation=operation)
                )
                removed.add(inner.stage_id)
                for source in inner.inputs:
                    consumers[source].discard(inner.stage_id)
                    consumers[source].add(stage.stage_id)
                consumers[inner.stage_id].clear()
                members = (
                    (*groups.pop(inner.stage_id).members, stage.stage_id)
                    if inner.stage_id in groups
                    else pair
                )
                groups[stage.stage_id] = FusionGroup("L1", members, stage.stage_id)
                decisions.append(
                    RuleDecision(
                        "L1",
                        "adopted",
                        members,
                        "Same-input closed int64 comparisons retain the complete key, nullable True-only mask and check positions.",
                    )
                )
            else:
                decisions.append(RuleDecision("L1", "refused", pair, ": ".join(issue)))
        elif isinstance(first, ReduceValues) and isinstance(second, ReduceValues):
            decisions.append(
                RuleDecision(
                    "L8",
                    "refused",
                    pair,
                    "Complete intermediate masks, numeric bounds and original finish checks have no fixed-state proof permitting elimination.",
                )
            )
    retained = tuple(
        stages[stage.stage_id] for stage in plan.stages if stage.stage_id not in removed
    )
    return replace(
        plan,
        stages=retained,
        consumers=_consumers(retained),
        fusion_groups=tuple(groups.values()),
        optimization_decisions=tuple(decisions),
    )


def _common_sources(plan: QueryPlan) -> QueryPlan:
    """Share one actual bound aggregate input while retaining each occurrence."""
    groups: dict[tuple[str, str, str, str, tuple[str, ...], bool], list[SourceRead]] = {}
    consumers = dict(plan.consumers)
    check_stages = {check.stage_id for check in plan.checks}
    for stage in plan.stages:
        if not isinstance(stage, SourceRead) or stage.reduction is None:
            continue
        reduction = stage.reduction
        node = reduction.node
        value = next(column for column in stage.columns if column.slot_id == reduction.value_slot)
        eligible = (
            bool(stage.binding_id)
            and not stage.inputs
            and not stage.parameters
            and not stage.preparation_checks
            and stage.stage_id not in check_stages
            and not reduction.calendar
            and not reduction.calendars
            and reduction.window_key is None
            and not reduction.preserve_sample_presence
            and not reduction.sampled
            and reduction.duration is None
            and (
                isinstance(node, EntityCount)
                or (isinstance(node, SourceAggregate) and node.reducer in ("sum", "count"))
            )
            and (value.dtype == ScalarDataType("int64") or isinstance(value.dtype, DecimalDataType))
            and len(consumers.get(stage.stage_id, ())) == 1
        )
        if not eligible:
            continue
        policy = (
            (node.nulls, node.empty) if isinstance(node, SourceAggregate) else ("ignore", "zero")
        )
        identity = (
            stage.binding_id,
            stage.source_id,
            *policy,
            consumers[stage.stage_id],
            stage.component_layout is not None,
        )
        group = groups.get(identity)
        if group is None:
            groups[identity] = [stage]
            continue
        prior = group[0]
        assert prior.reduction is not None
        if (
            prior.period == stage.period
            and prior.predicates == stage.predicates
            and prior.field_columns == stage.field_columns
            and prior.keys == stage.keys
            and prior.reduction.axes == reduction.axes
            and tuple(column for column in prior.columns if column.slot_id in prior.keys)
            == tuple(column for column in stage.columns if column.slot_id in stage.keys)
            and (
                (prior.component_layout is None and stage.component_layout is None)
                or (
                    prior.component_layout is not None
                    and stage.component_layout is not None
                    and prior.component_layout.identity == stage.component_layout.identity
                )
            )
        ):
            group.append(stage)
    selected = tuple(tuple(group) for group in groups.values() if len(group) > 1)
    if not selected:
        return plan
    aliases = {read.stage_id: group[0].stage_id for group in selected for read in group}
    indexes = {read.stage_id: index for group in selected for index, read in enumerate(group)}

    def program(value: FormulaProgram | None) -> FormulaProgram | None:
        if value is None:
            return None
        return replace(
            value,
            values=tuple(
                replace(
                    binding,
                    checks=tuple(
                        (common_slot(indexes[binding.stage_id], name), expected, received)
                        for name, expected, received in binding.checks
                    ),
                )
                if binding.stage_id in indexes
                else binding
                for binding in value.values
            ),
        )

    heads = {group[0].stage_id: group for group in selected}
    stages: list[QueryStage] = []
    for stage in plan.stages:
        if isinstance(stage, SourceRead):
            if stage.stage_id in aliases and stage.stage_id not in heads:
                continue
            if stage.stage_id in heads:
                block = heads[stage.stage_id]
                columns = tuple(
                    column for column in stage.columns if column.slot_id in stage.keys
                ) + tuple(
                    replace(
                        column,
                        slot_id=common_slot(index, column.slot_id)
                        if column.slot_id != read.reduction.value_slot
                        else column.slot_id,
                    )
                    for index, read in enumerate(block)
                    if read.reduction is not None
                    for column in read.columns
                    if column.slot_id not in read.keys
                )
                stage = replace(stage, columns=columns, common_reads=block, component_layout=None)
        elif isinstance(stage, LocalCompute):
            stage = replace(
                stage,
                inputs=tuple(aliases.get(item, item) for item in stage.inputs),
                program=program(stage.program),
            )
        stages.append(stage)
    decisions = tuple(
        RuleDecision(
            "source_merge",
            "adopted",
            tuple(read.stage_id for read in group),
            "One explicit bound input and grouped aggregate; ordered value/check/component occurrences remain independent.",
        )
        for group in selected
    )
    result = replace(
        plan,
        stages=tuple(stages),
        consumers=_consumers(tuple(stages)),
        original=program(plan.original),
        fusion_groups=(
            *plan.fusion_groups,
            *(
                FusionGroup(
                    "source_block", tuple(read.stage_id for read in group), group[0].stage_id
                )
                for group in selected
            ),
        ),
        optimization_decisions=tuple(
            decision
            for decision in plan.optimization_decisions
            if not (
                decision.rule == "source_merge"
                and all(member in aliases for member in decision.members)
                and len({aliases[member] for member in decision.members}) == 1
            )
        )
        + decisions,
    )
    return replace(result, required_slots=required_slots(result))


def optimize_query(plan: QueryPlan) -> QueryPlan:
    """Apply one bounded demand/law pass; preserve the original plan as a control."""
    if any(isinstance(stage, (FrameInput, FrameCompute, FrameKeysInput)) for stage in plan.stages):
        # Source children are optimized before composition. Fixed data is a hard
        # boundary; complete validation and strict expressions survive demand.
        return replace(plan, required_slots=required_slots(plan))
    planned = _fuse(replace(plan, required_slots=required_slots(plan)))
    required = required_slots(planned)
    by_stage = {item.stage_id: item for item in required}
    stages: list[QueryStage] = []
    for stage in planned.stages:
        demand = by_stage[stage.stage_id]
        columns = tuple(column for column in stage.columns if column.slot_id in demand.columns)
        if isinstance(stage, SourceRead):
            layout = stage.component_layout
            if layout is not None and layout.reduction.value_slot not in demand.components:
                stage = replace(stage, component_layout=None)
            if stage.reduction is None:
                stage = replace(
                    stage,
                    columns=columns,
                    projections=tuple(
                        (slot, source)
                        for slot, source in stage.projections
                        if slot in demand.columns
                    ),
                )
        elif isinstance(stage, LocalCompute) and stage.result is not None:
            operation = stage.result.operation
            if isinstance(operation, LiteralValues):
                values = tuple(
                    column for column in stage.columns if column.slot_id not in stage.keys
                )
                positions = tuple(
                    index for index, column in enumerate(values) if column.slot_id in demand.columns
                )
                operation = replace(
                    operation,
                    columns=tuple(operation.columns[index] for index in positions),
                    rows=tuple(
                        (keys, tuple(values[index] for index in positions))
                        for keys, values in operation.rows
                    ),
                )
                stage = replace(
                    stage, columns=columns, result=replace(stage.result, operation=operation)
                )
            elif isinstance(operation, (FilterValues, TopValues)):
                stage = replace(stage, columns=columns)
        stages.append(stage)
    return _common_sources(replace(planned, stages=tuple(stages), required_slots=required))


@dataclass(frozen=True, slots=True)
class CompilationKey:
    """Exact immutable invocation references, never business values or results."""

    node_id: str
    owner: Owner
    schemas: tuple[SourceSchema, ...]
    definition: BoundGraph = field(compare=False, repr=False)
    checks: tuple[SourceCheckRoute, ...] = ()
    required: tuple[str, ...] = ()
    require_original: bool = False
    policy_version: int = RULE_VERSION
    implementations: tuple[str, ...] = (
        QUERY_COMBINE_KERNEL,
        "query.result@v1",
        "query.optimization@v2",
    )
    relations: tuple[tuple[str, int], ...] = ()
    relation_bindings: tuple[tuple[str, ir.Table], ...] = field(
        default=(), compare=False, repr=False
    )


def compilation_key(
    graph: BoundGraph,
    schemas: tuple[SourceSchema, ...],
    *,
    required: tuple[str, ...] = (),
    require_original: bool = False,
    relations: Mapping[str, ir.Table] | None = None,
    checks: tuple[SourceCheckRoute, ...] = (),
) -> CompilationKey:
    signature = graph.signature
    # These immutable source records are invocation-owned. Holding
    # the graph in the memo key keeps their identities alive for this execute;
    # no whole-subgraph serialization or recursive hash is needed per node.
    node_id = repr(
        (
            id(graph.root),
            id(signature.axes),
            id(signature.observation),
            id(signature.declared_metrics),
            id(signature.evidence),
            id(signature.obligations),
            tuple(
                (
                    quantity.name,
                    quantity.unit,
                    quantity.kind if isinstance(quantity, ResultQuantity) else "numeric",
                )
                for quantity in signature.quantities
            ),
        )
    )
    bindings = tuple(sorted(relations.items())) if relations is not None else ()
    return CompilationKey(
        node_id,
        graph.signature.owner,
        schemas,
        graph,
        checks,
        required,
        require_original,
        relations=tuple((name, id(table.op())) for name, table in bindings),
        relation_bindings=bindings,
    )


MemoSlot: TypeAlias = tuple[
    str,
    tuple[str, ...],
    bool,
    int,
    tuple[str, ...],
    tuple[tuple[str, int], ...],
    tuple[SourceCheckRoute, ...],
]


def _memo_slot(key: CompilationKey) -> MemoSlot:
    # Owner and bound schema are invocation-wide invalidation guards.
    # A small hash key holds only structural IDs/versions, without recursively
    # hashing or serializing the captured definition graph at every node.
    return (
        key.node_id,
        key.required,
        key.require_original,
        key.policy_version,
        key.implementations,
        key.relations,
        key.checks,
    )


class InvocationMemo:
    """One execute call's typed compiler artifacts; results never enter this memo."""

    def __init__(self) -> None:
        self._binding: tuple[Owner, tuple[SourceSchema, ...]] | None = None
        self._plans: dict[MemoSlot, QueryPlan] = {}
        self._lowered: dict[MemoSlot, LoweredQuery] = {}
        self._captures: dict[MemoSlot, BoundGraph] = {}
        self._keys: dict[MemoSlot, CompilationKey] = {}
        self.hits = 0
        self.misses = 0

    def _bind(self, key: CompilationKey) -> None:
        binding = (key.owner, key.schemas)
        if binding != self._binding:
            self._plans.clear()
            self._lowered.clear()
            self._captures.clear()
            self._keys.clear()
            self._binding = binding

    def plan(self, key: CompilationKey, build: Callable[[], QueryPlan]) -> QueryPlan:
        self._bind(key)
        slot = _memo_slot(key)
        value = self._plans.get(slot)
        if value is not None:
            self.hits += 1
            return value
        self.misses += 1
        value = build()
        self._bind(key)
        self._keys[slot] = key
        self._plans[slot] = value
        return value

    def lower(self, key: CompilationKey, build: Callable[[], LoweredQuery]) -> LoweredQuery:
        self._bind(key)
        slot = _memo_slot(key)
        value = self._lowered.get(slot)
        if value is not None:
            self.hits += 1
            return value
        self.misses += 1
        value = build()
        self._bind(key)
        self._keys[slot] = key
        self._lowered[slot] = value
        return value

    def capture(self, key: CompilationKey, build: Callable[[], BoundGraph]) -> BoundGraph:
        self._bind(key)
        slot = _memo_slot(key)
        value = self._captures.get(slot)
        if value is not None:
            self.hits += 1
            return value
        self.misses += 1
        value = build()
        self._bind(key)
        self._keys[slot] = key
        self._captures[slot] = value
        return value


def retain_components(plan: QueryPlan, schemas: tuple[SourceSchema, ...]) -> QueryPlan:
    """Add sufficient source inputs only when an original consumer demands them."""
    stages = []
    for stage in plan.stages:
        if (
            not isinstance(stage, SourceRead)
            or stage.reduction is None
            or stage.component_layout is not None
        ):
            stages.append(stage)
            continue
        schema = next(
            item
            for item in schemas
            if item.source_id == stage.source_id and item.occurrence == stage.occurrence
        )
        physical = {
            column.column: ibis_type(column.dtype)
            for column in (*schema.keys, *schema.fields, *schema.original_identity)
        }
        table = ibis.table(physical, name="__original_schema")
        identity = tuple(
            (DataColumn(f"__original_id.{index}", column.field, column.dtype, False), column.column)
            for index, column in enumerate(schema.original_identity or schema.keys)
        )
        layout = component_layout(
            stage.reduction, stage.columns, identity, table, dict(stage.field_columns)
        )
        stages.append(replace(stage, component_layout=layout))
    return replace(plan, stages=tuple(stages))
