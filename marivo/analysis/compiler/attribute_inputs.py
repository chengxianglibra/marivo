"""Closed source bundles for private Attribute input preparation."""

from __future__ import annotations

import operator
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

import ibis
from ibis.common.deferred import Attr, BinaryOperator, Call, Just, Resolver, UnaryOperator, Variable

from marivo._definition.deferred import RescaleResolver
from marivo._definition.expressions import (
    ContributionFilter,
    DeclaredMetricRef,
    EntityCount,
    MetricExpression,
    MetricNode,
    MetricWindow,
    SourceAggregate,
)
from marivo._definition.types import CalendarCoordinate
from marivo.analysis.binding.attribute_rules import (
    AdditiveSource,
    AttributeSourceRequest,
    RuleAdd,
    RuleNegate,
    RuleScale,
    RuleSubtract,
    component_requests,
)
from marivo.analysis.binding.attribute_units import component_unit
from marivo.analysis.binding.programs import FormulaProgram
from marivo.analysis.compiler.attribute_input_lowering import input_failure as input_failure
from marivo.analysis.compiler.attribute_input_lowering import (
    original_input_slot as original_input_slot,
)
from marivo.analysis.compiler.original import AggregatePayload
from marivo.analysis.compiler.query_program import SourceParameter
from marivo.analysis.compiler.query_stages import (
    AttributeLeafInput,
    AttributeNativeFormula,
    AttributeRead,
    AttributeRootLayout,
    LocalCompute,
    SourceReadStage,
    read_components,
    read_members,
    read_reduction,
    require_components,
    require_reduction,
)
from marivo.analysis.core.attribute import (
    AttributeInputProgram,
    AttributeMatrixColumn,
    AttributeMatrixSource,
    attribute_schema,
)
from marivo.analysis.core.value_types import data_type, ibis_type
from marivo.analysis.model.attribute_failures import AttributeUseContext, AttributeWindowInput
from marivo.analysis.model.data_schema import DataSchema
from marivo.analysis.model.data_types import DataColumn, DurationDataType, ScalarDataType
from marivo.analysis.model.programs import FormulaInput
from marivo.analysis.model.unique_keys import UniqueKeys
from marivo.semantic.spi import (
    NumericValue,
    lower_metric_formula,
)

if TYPE_CHECKING:
    from marivo.analysis.binding.attribute_rules import AttributeRule
    from marivo.analysis.compiler.query_stages import QueryPlan, QueryStage
    from marivo.analysis.core.frame_operations import (
        AttributeContributionValues,
        AttributeInputsValues,
    )


def _native_nodes(
    program: FormulaProgram,
) -> tuple[tuple[tuple[int, ...], tuple[int, ...], MetricNode], ...]:
    result: dict[tuple[int, ...], tuple[tuple[int, ...], MetricNode]] = {}
    declared = dict(program.declared)

    def visit(node: MetricNode, occurrence: tuple[int, ...], path: tuple[int, ...]) -> None:
        if isinstance(node, DeclaredMetricRef):
            visit(declared[node.ref], (*occurrence, 0), path)
        elif isinstance(node, (MetricWindow, ContributionFilter)):
            visit(node.operand, (*occurrence, 0), path)
        elif isinstance(node, MetricExpression):
            result.setdefault(path, (occurrence, node))

            def recipe(value: Resolver, child_path: tuple[int, ...]) -> None:
                if isinstance(value, Variable):
                    result.pop(child_path, None)
                    index = int(value.name.removeprefix("slot_"))
                    visit(node.operands[index], (*occurrence, index), child_path)
                    return
                result.setdefault(child_path, (occurrence, MetricExpression(value, node.operands)))

                if isinstance(value, RescaleResolver):
                    recipe(value.recipe, (*child_path, 0))
                elif isinstance(value, BinaryOperator):
                    if value.func is operator.mul and isinstance(value.left, Just):
                        recipe(value.right, (*child_path, 0))
                    elif value.func in (operator.mul, operator.truediv) and isinstance(
                        value.right, Just
                    ):
                        recipe(value.left, (*child_path, 0))
                    else:
                        recipe(value.left, (*child_path, 0))
                        recipe(value.right, (*child_path, 1))
                elif isinstance(value, UnaryOperator):
                    recipe(value.arg, (*child_path, 0))
                elif isinstance(value, Call) and isinstance(value.func, Attr):
                    recipe(value.func.obj, (*child_path, 0))
                    if value.args:
                        recipe(value.args[0], (*child_path, 1))

            recipe(node.recipe, path)

    for index, (_, node) in enumerate(program.outputs):
        visit(node, (index,), (index,))
    return tuple((path, occurrence, node) for path, (occurrence, node) in result.items())


@dataclass(slots=True)
class _AttributeBundler:
    requests: dict[tuple[int, ...], AttributeSourceRequest]
    metrics: tuple[str, ...]
    parent_keys: tuple[str, ...]
    scoped_columns: dict[str, tuple[tuple[str, str], ...]]
    parent_projection: str
    side: Literal["left", "right"]
    parent_checks: dict[str, tuple[tuple[str, str, str], ...]]
    formula: FormulaProgram | None
    population: SourceParameter | None
    native_bindings: dict[int, tuple[str, DataColumn]]
    aliases: dict[str, str]
    bundles: dict[str, SourceReadStage]

    def leaves(
        self, root: str, members: list[SourceReadStage], columns: dict[str, DataColumn]
    ) -> tuple[AttributeLeafInput, ...]:
        first = members[0]
        leaves: list[AttributeLeafInput] = []
        for member in members:
            request = self.requests[member.occurrence]
            if read_components(member) is None or read_reduction(member) is None:
                input_failure("missing_component_proof", f"{root}: no original input layout")
            if member.keys != first.keys:
                input_failure("unqualified_contribution_route", f"{root}: incompatible root scopes")
            reduction = read_reduction(member)
            component = read_components(member)
            assert reduction is not None and component is not None
            token = ".".join(map(str, member.occurrence))
            roles = tuple(
                (
                    role,
                    DataColumn(
                        f"attribute.component.{token}.{role}",
                        role,
                        ScalarDataType("int64")
                        if role in ("valid_value_count", "pair_count", "pair_negative_count")
                        else next(
                            c.dtype for c in member.columns if c.slot_id == reduction.value_slot
                        ),
                        True,
                        component_unit(reduction.node, role),
                    ),
                )
                for role in request.roles
            )
            support = DataColumn(
                f"attribute.component.{token}.input_count",
                "input count",
                ScalarDataType("int64"),
                False,
            )
            distinct = None
            if (
                isinstance(reduction.node, SourceAggregate)
                and reduction.node.reducer == "count_distinct"
            ):
                assert isinstance(component.payload, AggregatePayload)
                distinct = replace(component.payload.value, slot_id=f"attribute.distinct.{token}")
            effective = replace(
                support,
                slot_id=f"attribute.component.{token}.effective_count",
                name="effective count",
            )
            logical = request.binding.logical_occurrence or member.occurrence
            use = AttributeUseContext(
                self.side, self.metrics[logical[0]], member.occurrence, logical, root
            )

            exact_integer = isinstance(reduction.node, EntityCount) or (
                isinstance(reduction.node, SourceAggregate)
                and (
                    reduction.node.reducer in ("count", "count_distinct")
                    or (
                        reduction.node.reducer == "sum"
                        and isinstance(component.payload, AggregatePayload)
                        and component.payload.value.dtype == ScalarDataType("int64")
                    )
                )
            )
            leaves.append(
                AttributeLeafInput(
                    request,
                    member,
                    roles,
                    support,
                    effective,
                    use,
                    distinct,
                    tuple(
                        dict.fromkeys(
                            (
                                item.period,
                                require_reduction(item).window_key
                                if read_reduction(item) is not None
                                else None,
                            )
                            for item in members
                        )
                    ).index((member.period, reduction.window_key)),
                    exact_integer,
                )
            )
            columns[reduction.value_slot] = replace(
                columns[reduction.value_slot], unit=component_unit(reduction.node, "value")
            )
            columns[effective.slot_id] = replace(effective, unit="1")
            columns[support.slot_id] = replace(support, unit="1")
            columns.update((column.slot_id, column) for _, column in roles)
            self.aliases[member.stage_id] = first.stage_id
        return tuple(leaves)

    def native_formulas(
        self,
        members: list[SourceReadStage],
        leaves: tuple[AttributeLeafInput, ...],
        columns: dict[str, DataColumn],
    ) -> tuple[AttributeNativeFormula, ...]:
        first = members[0]
        native_formulas: list[AttributeNativeFormula] = []
        if (formula := self.formula) is not None:
            member_ids = {member.stage_id for member in members}
            for path, occurrence, node in _native_nodes(formula):
                bindings = tuple(
                    value
                    for value in formula.values
                    if value.occurrence[: len(occurrence)] == occurrence
                )
                if not bindings or any(
                    value.stage_id not in member_ids
                    or isinstance(value.column.dtype, DurationDataType)
                    for value in bindings
                ):
                    continue
                active = tuple(
                    leaf
                    for leaf in leaves
                    if leaf.read.stage_id in {value.stage_id for value in bindings}
                )
                if len({leaf.fiber for leaf in active}) != 1:
                    continue
                column = DataColumn(
                    "attribute.native." + ".".join(map(str, path)),
                    "original." + ".".join(map(str, path)),
                    data_type(
                        lower_metric_formula(
                            node,
                            occurrence,
                            {
                                value.occurrence: NumericValue(
                                    ibis.null().cast(ibis_type(value.column.dtype))
                                )
                                for value in formula.values
                            },
                            dict(formula.declared),
                        ).value.type()
                    ),
                    True,
                )
                native_formulas.append(
                    AttributeNativeFormula(
                        path, occurrence, node, formula, column, active[0].use, active[0].fiber
                    )
                )
                columns[column.slot_id] = column
                if len(path) == 1:
                    self.native_bindings[path[0]] = (first.stage_id, column)
        return tuple(native_formulas)

    def bundle(self, root: str, members: list[SourceReadStage]) -> None:
        first = members[0]
        columns = {
            column.slot_id: column
            for member in members
            for column in member.columns
            if column.slot_id
            in (
                *member.keys,
                require_reduction(member).value_slot if read_reduction(member) is not None else "",
            )
        }
        leaves = self.leaves(root, members, columns)
        native_formulas = self.native_formulas(members, leaves, columns)
        assert read_components(first) is not None
        layout = AttributeRootLayout(
            leaves,
            tuple(columns.values()),
            self.parent_keys,
            require_components(first).identity,
            self.scoped_columns[root],
            next(
                parameter
                for parameter in first.parameters
                if parameter.placeholder == "__marivo_population_" + self.parent_projection
            ),
            self.side,
            native_formulas=native_formulas,
            population=self.population,
        )
        # Every fiber groups independently. A later UNION ALL cannot inherit
        # per-fiber uniqueness without a separate disjointness proof.
        if len({leaf.fiber for leaf in leaves}) == 1:
            slots = tuple(c.slot_id for c in layout.columns)
            layout = replace(
                layout,
                parent_unique_keys=UniqueKeys(slots, (layout.parent_keys,)),
                fine_unique_keys=UniqueKeys(slots, (layout.keys,)),
            )
        self.bundles[first.stage_id] = replace(
            first,
            columns=layout.columns,
            field_columns=tuple(
                dict(pair for member in members for pair in member.field_columns).items()
            ),
            program=AttributeRead(layout, require_reduction(first), read_members(first)),
            unique_keys=UniqueKeys(),
            inputs=(
                *first.inputs,
                *((self.population.stage_id,) if self.population is not None else ()),
            ),
            parameters=(
                *first.parameters,
                *((self.population,) if self.population is not None else ()),
            ),
        )
        parent_layout = replace(
            layout,
            scope="parent",
            population=None,
            columns=tuple(
                c
                for c in layout.columns
                if c.slot_id not in first.keys or c.slot_id in self.parent_keys
            ),
        )
        self.bundles[attribute_parent_key(first.stage_id)] = replace(
            self.bundles[first.stage_id],
            stage_id=attribute_parent_key(first.stage_id),
            keys=self.parent_keys,
            columns=parent_layout.columns,
            preparation_checks=self.parent_checks[root],
            inputs=first.inputs,
            parameters=first.parameters,
            program=AttributeRead(parent_layout, require_reduction(first), read_members(first)),
        )


def bundle_attribute_plan(
    plan: QueryPlan,
    rules: tuple[tuple[str, AttributeRule], ...],
    parent_positions: tuple[int, ...],
    scoped_columns: dict[str, tuple[tuple[str, str], ...]],
    parent_projection: str,
    side: Literal["left", "right"],
    parent_checks: dict[str, tuple[tuple[str, str, str], ...]],
) -> QueryPlan:
    """Replace each root's ordered occurrences by one governed physical read."""

    parent_keys = tuple(
        key for position in parent_positions for key in plan.output_groups[position]
    )
    requests = tuple(item for _, rule in rules for item in component_requests(rule))
    metrics = tuple(name for name, _ in rules)
    by_occurrence = {request.binding.occurrence: request for request in requests}
    roots: dict[str, list[SourceReadStage]] = {}
    for stage in plan.stages:
        if isinstance(stage, SourceReadStage) and stage.occurrence in by_occurrence:
            roots.setdefault(stage.source_id, []).append(stage)
    for request in requests:
        if request.binding.window_key is not None:
            proof = request.window
            if proof is None:
                input_failure(
                    "missing_component_proof",
                    "a window source lacks its captured finite binding proof",
                )
            actual = tuple(
                item.binding
                for item in requests
                if (item.binding.logical_occurrence or item.binding.occurrence)
                == (request.binding.logical_occurrence or request.binding.occurrence)
            )
            if actual != proof.bindings:
                input_failure(
                    "missing_component_proof",
                    "expanded source uses differ from the captured finite window proof",
                )
    formula = next(
        (
            stage.program
            for stage in reversed(plan.stages)
            if isinstance(stage, LocalCompute) and isinstance(stage.program, FormulaProgram)
        ),
        None,
    )
    population_parameter = None
    if formula is not None and formula.output_population is not None:
        population_input = next(
            item for item in formula.populations if item.definition == formula.output_population
        )
        population_stage = next(
            stage for stage in plan.stages if stage.stage_id == population_input.stage_id
        )
        population_parameter = SourceParameter(
            population_stage.stage_id,
            "__attribute_output_population_" + population_stage.stage_id.replace(":", "_"),
            population_stage.columns,
        )
    native_bindings: dict[int, tuple[str, DataColumn]] = {}
    native_keys: tuple[str, ...] = ()
    aliases: dict[str, str] = {}
    bundles: dict[str, SourceReadStage] = {}
    bundler = _AttributeBundler(
        by_occurrence,
        metrics,
        parent_keys,
        scoped_columns,
        parent_projection,
        side,
        parent_checks,
        formula,
        population_parameter,
        native_bindings,
        aliases,
        bundles,
    )
    for root, members in roots.items():
        native_keys = members[0].keys
        bundler.bundle(root, members)
    stages: list[QueryStage] = []
    for stage in plan.stages:
        if isinstance(stage, SourceReadStage) and stage.stage_id in aliases:
            if stage.stage_id in bundles:
                stages.append(bundles[attribute_parent_key(stage.stage_id)])
                stages.append(bundles[stage.stage_id])
            continue
        if isinstance(stage, LocalCompute) and isinstance(stage.program, FormulaProgram):
            original_values = stage.program.values
            selected_native = native_bindings if stage.program is formula else {}
            values = [
                replace(value, stage_id=aliases.get(value.stage_id, value.stage_id), checks=())
                for value in original_values
                if value.occurrence[0] not in selected_native
            ]
            for index, (stage_id, column) in selected_native.items():
                values.append(FormulaInput((index,), stage_id, column, False, keys=native_keys))
            program = replace(
                stage.program,
                values=tuple(values),
                population_checked=population_parameter is not None,
            )
            stage = replace(
                stage,
                inputs=(
                    *tuple(value.stage_id for value in values),
                    *tuple(aliases.get(i, i) for i in stage.inputs[len(original_values) :]),
                ),
                program=program,
            )
        stages.append(stage)
    if population_parameter is not None:
        # Population keys are an existing captured dependency, uploaded once as
        # a typed relation before the fine read that checks their coverage.
        by_id = {stage.stage_id: stage for stage in stages}
        ordered: dict[str, QueryStage] = {}

        def include(stage: QueryStage) -> None:
            if stage.stage_id in ordered:
                return
            for dependency in stage.inputs:
                if dependency in by_id:
                    include(by_id[dependency])
            ordered[stage.stage_id] = stage

        for stage in stages:
            include(stage)
        stages = list(ordered.values())
    consumers = tuple(
        (
            stage.stage_id,
            tuple(
                s.stage_id
                for s in stages
                for dependency in s.inputs
                if dependency == stage.stage_id
            ),
        )
        for stage in stages
    )
    return replace(
        plan,
        stages=tuple(stages),
        consumers=consumers,
        publish=aliases.get(plan.publish, plan.publish),
        required_slots=(),
    )


def attribute_parent_key(stage_id: str) -> str:
    return "attribute-parent:" + stage_id


def attribute_preparation_key(stage_id: str) -> str:
    """Name the exact prepared check relation paired with one Attribute read."""
    return "attribute-preparation:" + stage_id


def attribute_input_program(
    operation: AttributeInputsValues,
    parent: DataSchema,
    left: DataSchema,
    right: DataSchema,
    left_layouts: tuple[AttributeRootLayout, ...],
    right_layouts: tuple[AttributeRootLayout, ...],
) -> AttributeInputProgram:
    """Fix the actual sufficient component matrix, without contribution arithmetic."""

    base = attribute_schema(left, right, parent, operation.mapping)

    matrix: list[AttributeMatrixColumn] = []
    paths: list[tuple[str, ...]] = []
    sides: tuple[tuple[Literal["left", "right"], tuple[AttributeRootLayout, ...]], ...] = (
        ("left", left_layouts),
        ("right", right_layouts),
    )
    for side, layouts in sides:
        for layout in layouts:
            grouped: dict[
                tuple[tuple[int, ...], str], list[tuple[AttributeMatrixSource, DataColumn]]
            ] = {}
            for leaf in layout.leaves:
                reduction = read_reduction(leaf.read)
                assert reduction is not None
                window = None
                if reduction.window_key is not None:
                    binding = leaf.request.binding
                    assert binding.period is not None and binding.time is not None
                    slot, calendar = next(
                        (slot, calendar)
                        for slot, calendar in reduction.calendars
                        if calendar == reduction.calendar
                    )

                    assert isinstance(calendar, CalendarCoordinate)
                    window = AttributeWindowInput(
                        reduction.window_key, slot, binding.period, binding.time, calendar
                    )
                original = next(
                    column for column in layout.columns if column.slot_id == reduction.value_slot
                )
                for role, column in (
                    ("value", original),
                    ("input_count", leaf.support),
                    ("effective_count", leaf.effective),
                    *leaf.roles,
                ):
                    occurrence = (
                        leaf.use.occurrence if role == "value" else leaf.use.logical_occurrence
                    )
                    grouped.setdefault((occurrence, role), []).append(
                        (AttributeMatrixSource(column.slot_id, leaf.use, window), column)
                    )
            for native in layout.native_formulas:
                leaf = next(leaf for leaf in layout.leaves if leaf.use == native.use)
                window = None
                binding = leaf.request.binding
                if binding.window_key is not None:
                    assert (
                        binding.period is not None
                        and binding.time is not None
                        and read_reduction(leaf.read) is not None
                    )
                    slot, window_calendar = next(iter(require_reduction(leaf.read).calendars))

                    assert isinstance(window_calendar, CalendarCoordinate)
                    window = AttributeWindowInput(
                        binding.window_key, slot, binding.period, binding.time, window_calendar
                    )
                grouped[(native.path, "native_value")] = [
                    (
                        AttributeMatrixSource(native.column.slot_id, native.use, window),
                        native.column,
                    )
                ]
            for (occurrence, role), entries in grouped.items():
                for scope in (layout.scope,):
                    column = entries[0][1]
                    if any(
                        (other.dtype, other.unit) != (column.dtype, column.unit)
                        for _, other in entries
                    ):
                        input_failure(
                            "missing_component_proof", "expanded component types or units differ"
                        )
                    token = "_".join(map(str, occurrence))
                    target = replace(
                        column,
                        slot_id=f"attribute.input.{len(matrix)}",
                        name=".".join((side, scope, token, role)),
                        nullable=True,
                    )
                    paths.append((side, scope, token, role))
                    matrix.append(
                        AttributeMatrixColumn(
                            side,
                            scope,
                            occurrence,
                            role,
                            target,
                            tuple(entry for entry, _ in entries),
                        )
                    )
    schema = DataSchema(
        base.axes,
        tuple(item.target for item in matrix),
        base.presence,
        value_paths=tuple(paths),
        presence_paths=base.presence_paths,
    )
    return AttributeInputProgram(operation, left, right, parent, tuple(matrix), schema)


def exact_integer_closure(
    operation: AttributeContributionValues,
    layouts: tuple[AttributeRootLayout, ...],
) -> bool:
    """Prove the complete requested target uses only lossless integer rules."""

    if operation.target is None or operation.target.mode == "relative":
        return False

    def exact(rule: AttributeRule, side: str) -> bool:
        node = rule.operation
        if isinstance(node, AdditiveSource):
            if operation.source_sides is not None:
                side = (
                    "left" if operation.source_sides[0 if side == "left" else 1] == 0 else "right"
                )
            logical = node.source.binding.logical_occurrence or node.source.binding.occurrence
            leaves = tuple(
                leaf
                for layout in layouts
                if layout.side == side
                for leaf in layout.leaves
                if leaf.use.logical_occurrence == logical
            )
            return bool(leaves) and all(leaf.exact_integer for leaf in leaves)
        if isinstance(node, (RuleAdd, RuleSubtract)):
            return exact(node.left, side) and exact(node.right, side)
        if isinstance(node, RuleNegate):
            return exact(node.operand, side)
        if isinstance(node, RuleScale):
            return type(node.scalar.value) is int and exact(node.operand, side)
        return False

    selected = operation.rules[:1] if operation.target.mode == "level" else operation.rules
    return all(
        exact(rule, "left" if index == 0 else "right")
        for index, rules in enumerate(selected)
        for _, rule in rules
    )
