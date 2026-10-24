"""Closed source bundles for private Attribute input preparation."""

from __future__ import annotations

import operator
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal, NoReturn

import ibis
import ibis.expr.types as ir
from ibis.common.deferred import Attr, BinaryOperator, Call, Just, Resolver, UnaryOperator, Variable

from marivo._definition.expressions import (
    ContributionFilter,
    DeclaredMetricRef,
    EntityCount,
    MetricExpression,
    MetricNode,
    MetricWindow,
    SourceAggregate,
    SourceWeightedMean,
)
from marivo.analysis.binding.attribute_rules import AttributeComponentRole, AttributeSourceRequest
from marivo.analysis.compiler.graph_plan import (
    AttributeRead,
    read_components,
    read_members,
    read_reduction,
    require_components,
    require_reduction,
)
from marivo.analysis.compiler.observation import FormulaProgram
from marivo.analysis.core.attribute import (
    AttributeInputError,
    AttributeInputReason,
    AttributeUseContext,
)
from marivo.analysis.core.data_types import DataColumn, DurationDataType, ScalarDataType
from marivo.analysis.core.value_types import data_type, ibis_type
from marivo.semantic._numeric_lowering import (
    NumericCheck,
    NumericValue,
    lower_metric_formula,
    lower_row,
    lower_source_reduction,
    numeric_binary,
)

if TYPE_CHECKING:
    from marivo._definition.expressions import RowNode
    from marivo.analysis.binding.attribute_rules import AttributeRule
    from marivo.analysis.compiler.graph_plan import QueryPlan, QueryStage, SourceRead
    from marivo.analysis.compiler.query_program import SourceParameter
    from marivo.analysis.core.attribute import (
        AttributeFailureContext,
        AttributeInputProgram,
        AttributeParentContext,
    )
    from marivo.analysis.core.data_schema import DataSchema
    from marivo.analysis.core.frame_expression import (
        AttributeContributionValues,
        AttributeInputsValues,
    )


@dataclass(frozen=True, slots=True)
class AttributeLeafInput:
    request: AttributeSourceRequest
    read: SourceRead
    roles: tuple[tuple[AttributeComponentRole, DataColumn], ...]
    support: DataColumn
    effective: DataColumn
    use: AttributeUseContext
    distinct: DataColumn | None = None
    fiber: int = 0
    exact_integer: bool = False


@dataclass(frozen=True, slots=True)
class AttributeCheckLayout:
    name: str
    scope: Literal["parent", "fine", "preparation"]
    leaves: tuple[AttributeLeafInput, ...]
    columns: tuple[DataColumn, ...]


@dataclass(frozen=True, slots=True)
class AttributeNativeFormula:
    """One original recipe subtree over this root's completed reductions."""

    path: tuple[int, ...]
    occurrence: tuple[int, ...]
    node: MetricNode
    program: FormulaProgram
    column: DataColumn
    use: AttributeUseContext
    fiber: int


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
                if isinstance(value, BinaryOperator):
                    if value.func is operator.mul and isinstance(value.left, Just):
                        recipe(value.right, (*child_path, 0))
                    elif value.func is operator.mul and isinstance(value.right, Just):
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


@dataclass(frozen=True, slots=True)
class AttributeRootLayout:
    """One independent endpoint/root role's original values and components."""

    leaves: tuple[AttributeLeafInput, ...]
    columns: tuple[DataColumn, ...]
    parent_keys: tuple[str, ...]
    identity: tuple[tuple[DataColumn, str], ...]
    scoped_columns: tuple[tuple[str, str], ...]
    parent_parameter: SourceParameter
    side: Literal["left", "right"]
    scope: Literal["parent", "fine"] = "fine"
    assigned: str = "__attribute_assigned"
    partition: bool = False
    population: SourceParameter | None = None
    kind: str = "__attribute_kind"
    fiber: str = "__attribute_fiber"
    checks: tuple[AttributeCheckLayout, ...] = ()
    parent_context: AttributeParentContext | None = None
    native_formulas: tuple[AttributeNativeFormula, ...] = ()

    @property
    def keys(self) -> tuple[str, ...]:
        return self.parent_keys if self.scope == "parent" else self.leaves[0].read.keys


def input_failure(
    reason: AttributeInputReason, received: str, *, context: AttributeFailureContext | None = None
) -> NoReturn:
    raise AttributeInputError(reason=reason, received=received, context=context)


def _row_unit(node: RowNode) -> str | None:
    """Read captured row units through the existing semantic unit algebra."""
    from ibis.common.deferred import (
        Attr,
        BinaryOperator,
        Call,
        Just,
        UnaryOperator,
        Variable,
    )

    from marivo._definition.deferred import attribute_name
    from marivo._definition.expressions import MeasureAssertion, RowField
    from marivo.semantic.expressions import _arith_unit
    from marivo.semantic.unit_algebra import linear_unit

    if isinstance(node, RowField):
        return node.unit
    if isinstance(node, MeasureAssertion):
        return node.unit if node.unit is not None else _row_unit(node.operand)
    inputs = tuple(_row_unit(item) for item in node.inputs)

    def visit(recipe: Resolver) -> tuple[str | None, bool]:
        from marivo._definition.deferred import RescaleResolver

        if isinstance(recipe, RescaleResolver):
            return recipe.unit, False
        values: tuple[tuple[str | None, bool], ...]
        if isinstance(recipe, Variable):
            return inputs[int(recipe.name.removeprefix("slot_"))], False
        if isinstance(recipe, Just):
            return None, True
        if isinstance(recipe, UnaryOperator):
            return visit(recipe.arg)
        if isinstance(recipe, BinaryOperator):
            operation = recipe.func.__name__
            values = (visit(recipe.left), visit(recipe.right))
        elif isinstance(recipe, Call) and isinstance(recipe.func, Attr):
            operation = attribute_name(recipe.func)
            values = (visit(recipe.func.obj), *(visit(item) for item in recipe.args))
        else:
            input_failure("missing_component_proof", "uncaptured row unit recipe")
            raise AssertionError("unreachable")
        if operation in ("add", "sub", "mul", "truediv", "divide"):
            left, right = values[:2]
            operations: dict[str, Literal["add", "subtract", "multiply", "divide"]] = {
                "add": "add",
                "sub": "subtract",
                "mul": "multiply",
                "truediv": "divide",
                "divide": "divide",
            }
            method = operations[operation]
            return _arith_unit(
                method,
                left[0],
                right[0],
                constant="left" if left[1] else "right" if right[1] else None,
                stage="Row",
            ), False
        selected = values[1:] if operation == "ifelse" else values
        return linear_unit(tuple(unit for unit, scalar in selected if not scalar)), False

    return visit(node.recipe)[0]


def _component_unit(
    node: SourceAggregate | SourceWeightedMean | EntityCount, role: str
) -> str | None:
    from marivo.semantic.unit_algebra import multiply_unit_states, render_unit, unit_state

    if role in ("input_count", "effective_count", "valid_value_count", "pair_count") or isinstance(
        node, EntityCount
    ):
        return "1"
    if isinstance(node, SourceWeightedMean):
        value, weight = _row_unit(node.value), _row_unit(node.weight)
        if role == "pair_weight_sum":
            return weight
        if role == "pair_product_sum":
            return render_unit(multiply_unit_states(unit_state(value), unit_state(weight)))
        return value
    return "1" if node.reducer in ("count", "count_distinct") else _row_unit(node.operand)


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
    from marivo.analysis.binding.attribute_rules import component_requests
    from marivo.analysis.compiler.graph_plan import LocalCompute, SourceRead

    parent_keys = tuple(
        key for position in parent_positions for key in plan.output_groups[position]
    )
    requests = tuple(item for _, rule in rules for item in component_requests(rule))
    metrics = tuple(name for name, _ in rules)
    by_occurrence = {request.binding.occurrence: request for request in requests}
    roots: dict[str, list[SourceRead]] = {}
    for stage in plan.stages:
        if isinstance(stage, SourceRead) and stage.occurrence in by_occurrence:
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
        from marivo.analysis.compiler.query_program import SourceParameter

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
    aliases: dict[str, str] = {}
    bundles: dict[str, SourceRead] = {}
    for root, members in roots.items():
        first = members[0]
        leaves: list[AttributeLeafInput] = []
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
        for member in members:
            request = by_occurrence[member.occurrence]
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
                        if role in ("valid_value_count", "pair_count")
                        else next(
                            c.dtype for c in member.columns if c.slot_id == reduction.value_slot
                        ),
                        True,
                        _component_unit(reduction.node, role),
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
                from marivo.analysis.compiler.original import AggregatePayload

                assert isinstance(component.payload, AggregatePayload)
                distinct = replace(component.payload.value, slot_id=f"attribute.distinct.{token}")
            effective = replace(
                support,
                slot_id=f"attribute.component.{token}.effective_count",
                name="effective count",
            )
            logical = request.binding.logical_occurrence or member.occurrence
            use = AttributeUseContext(side, metrics[logical[0]], member.occurrence, logical, root)
            from marivo.analysis.compiler.original import AggregatePayload

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
                columns[reduction.value_slot], unit=_component_unit(reduction.node, "value")
            )
            columns[effective.slot_id] = replace(effective, unit="1")
            columns[support.slot_id] = replace(support, unit="1")
            columns.update((column.slot_id, column) for _, column in roles)
            aliases[member.stage_id] = first.stage_id
        native_formulas: list[AttributeNativeFormula] = []
        if formula is not None:
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
                    native_bindings[path[0]] = (first.stage_id, column)
        assert read_components(first) is not None
        layout = AttributeRootLayout(
            tuple(leaves),
            tuple(columns.values()),
            parent_keys,
            require_components(first).identity,
            scoped_columns[root],
            next(
                parameter
                for parameter in first.parameters
                if parameter.placeholder == "__marivo_population_" + parent_projection
            ),
            side,
            native_formulas=tuple(native_formulas),
            population=population_parameter,
        )
        bundles[first.stage_id] = replace(
            first,
            columns=layout.columns,
            field_columns=tuple(
                dict(pair for member in members for pair in member.field_columns).items()
            ),
            program=AttributeRead(layout, require_reduction(first), read_members(first)),
            inputs=(
                *first.inputs,
                *((population_parameter.stage_id,) if population_parameter is not None else ()),
            ),
            parameters=(
                *first.parameters,
                *((population_parameter,) if population_parameter is not None else ()),
            ),
        )
        parent_layout = replace(
            layout,
            scope="parent",
            population=None,
            columns=tuple(
                c for c in layout.columns if c.slot_id not in first.keys or c.slot_id in parent_keys
            ),
        )
        bundles[attribute_parent_key(first.stage_id)] = replace(
            bundles[first.stage_id],
            stage_id=attribute_parent_key(first.stage_id),
            keys=parent_keys,
            columns=parent_layout.columns,
            preparation_checks=parent_checks[root],
            inputs=first.inputs,
            parameters=first.parameters,
            program=AttributeRead(parent_layout, require_reduction(first), read_members(first)),
        )
    stages: list[QueryStage] = []
    for stage in plan.stages:
        if isinstance(stage, SourceRead) and stage.stage_id in aliases:
            if stage.stage_id in bundles:
                stages.append(bundles[attribute_parent_key(stage.stage_id)])
                stages.append(bundles[stage.stage_id])
            continue
        if isinstance(stage, LocalCompute) and isinstance(stage.program, FormulaProgram):
            from marivo.analysis.compiler.observation import FormulaInput

            original_values = stage.program.values
            selected_native = native_bindings if stage.program is formula else {}
            values = [
                replace(value, stage_id=aliases.get(value.stage_id, value.stage_id), checks=())
                for value in original_values
                if value.occurrence[0] not in selected_native
            ]
            for index, (stage_id, column) in selected_native.items():
                values.append(FormulaInput((index,), stage_id, column, False, keys=first.keys))
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


def original_input_slot(kind: int, occurrence: tuple[int, ...], name: str) -> str:
    return f"attribute.original.{kind}.{'.'.join(map(str, occurrence))}.{name}"


def attribute_parent_key(stage_id: str) -> str:
    return "attribute-parent:" + stage_id


def attribute_preparation_key(stage_id: str) -> str:
    """Name the exact prepared check relation paired with one Attribute read."""
    return "attribute-preparation:" + stage_id


def lower_attribute_inputs(
    layout: AttributeRootLayout,
    table: ir.Table,
    field_columns: dict[str, str],
    preparation: ir.Table,
    preparation_checks: tuple[tuple[str, str, str], ...],
) -> tuple[ir.Table, tuple[tuple[str, NumericCheck], ...], tuple[AttributeCheckLayout, ...]]:
    """Lower every captured fiber into one actual root statement."""
    groups: dict[int, list[AttributeLeafInput]] = {}
    for leaf in layout.leaves:
        groups.setdefault(leaf.fiber, []).append(leaf)
    relations: list[ir.Table] = []
    checks: list[tuple[str, NumericCheck]] = []
    scopes: list[AttributeCheckLayout] = []
    for fiber, leaves in groups.items():
        local = replace(layout, leaves=tuple(leaves))
        source = table.filter(table[layout.fiber] == fiber)
        relation, local_checks, local_scopes = _lower_attribute_fiber(local, source, field_columns)
        scopes.extend(local_scopes)
        relations.append(relation.mutate(**{layout.fiber: ibis.literal(fiber)}))
        checks.extend(local_checks)
        if preparation_checks:
            prepared = preparation.filter(preparation[layout.fiber] == fiber)
            assert read_reduction(leaves[0].read) is not None
            axes = {slot: prepared[name] for slot, name in layout.scoped_columns}
            axes.update(
                (slot, prepared[field_columns[path]])
                for slot, path in require_reduction(leaves[0].read).axes
                if field_columns[path] in prepared.columns
            )
            actual_columns = tuple(column for column in layout.columns if column.slot_id in axes)
            for name, expected, received in preparation_checks:
                failed = prepared.select(**axes, **{name: prepared[name]})
                failed = failed.filter(failed[name])
                relations.append(
                    _first_failure(failed, tuple(axes)).mutate(
                        **{layout.kind: ibis.literal(4), layout.fiber: ibis.literal(fiber)}
                    )
                )
                checks.append((name, NumericCheck(prepared[name], expected, received)))
                scopes.append(
                    AttributeCheckLayout(name, "preparation", tuple(leaves), actual_columns)
                )
    return _union_attribute_relations(tuple(relations)), tuple(checks), tuple(scopes)


def _union_attribute_relations(relations: tuple[ir.Table, ...]) -> ir.Table:
    names = tuple(dict.fromkeys(name for relation in relations for name in relation.columns))
    dtypes = {
        name: next(relation[name].type() for relation in relations if name in relation.columns)
        for name in names
    }
    aligned = tuple(
        relation.select(
            **{
                name: relation[name].cast(dtypes[name].copy(nullable=True))
                if name in relation.columns
                else (
                    ibis.literal(0).cast(dtypes[name].copy(nullable=True))
                    if name.startswith("__original_id.")
                    else ibis.null().cast(dtypes[name].copy(nullable=True))
                )
                for name in names
            }
        )
        for relation in relations
    )
    return aligned[0].union(*aligned[1:], distinct=False) if len(aligned) > 1 else aligned[0]


def _lower_attribute_fiber(
    layout: AttributeRootLayout, table: ir.Table, field_columns: dict[str, str]
) -> tuple[ir.Table, tuple[tuple[str, NumericCheck], ...], tuple[AttributeCheckLayout, ...]]:
    """Return aggregates and bounded witnesses for operation-specific constraints."""
    fields = {path: table[column] for path, column in field_columns.items()}
    representative = layout.leaves[0].read
    assert read_reduction(representative) is not None
    axes = {
        slot: fields[path]
        for slot, path in require_reduction(representative).axes
        if layout.scope == "fine" or slot in layout.parent_keys
    }
    axes.update(
        (slot, table[column])
        for slot, column in layout.scoped_columns
        if slot in {slot for slot, _ in require_reduction(representative).calendars}
    )
    parent_axes = {slot: table[column] for slot, column in layout.scoped_columns}
    parent_active: ir.BooleanValue = ibis.literal(True)
    if not layout.parent_keys:
        import ibis.expr.operations as ops

        selected_parent = next(
            (
                leaf
                for leaf in table.op().find(ops.UnboundTable)
                if leaf.name == layout.parent_parameter.placeholder
            ),
            None,
        )
        if selected_parent is None:
            input_failure(
                "missing_component_proof", "the captured complete parent parameter is missing"
            )
        parent_active = selected_parent.to_expr().count() > 0
    checks: list[tuple[str, NumericCheck]] = []
    check_scopes: list[AttributeCheckLayout] = []

    def aggregate(source: ir.Table, keys: dict[str, ir.Value], kind: int) -> ir.Table:
        local_fields = {path: source[column] for path, column in field_columns.items()}
        values: dict[str, ir.Value] = {}
        originals: list[ir.Table] = []
        for leaf in layout.leaves:
            reduction = read_reduction(leaf.read)
            assert reduction is not None
            node = reduction.node
            from marivo.analysis.compiler.observation import lower_observation

            original_reduction = replace(
                reduction,
                axes=tuple((slot, path) for slot, path in reduction.axes if slot in keys),
                calendars=tuple(
                    (slot, calendar) for slot, calendar in reduction.calendars if slot in keys
                ),
            )
            original_table, original_checks = lower_observation(
                original_reduction, source, field_columns, ()
            )
            aliases = {
                name: name
                if name in keys or name == reduction.value_slot
                else original_input_slot(kind, leaf.read.occurrence, name)
                for name in original_table.columns
            }
            originals.append(
                original_table.rename(
                    **{target: name for name, target in aliases.items() if name != target}
                )
            )
            checks.extend((aliases[name], check) for name, check in original_checks)
            check_scopes.extend(
                AttributeCheckLayout(
                    aliases[name],
                    "parent" if kind else "fine",
                    (leaf,),
                    tuple(column for column in layout.columns if column.slot_id in keys),
                )
                for name, _ in original_checks
            )
            original = lower_source_reduction(node, source, local_fields)
            values[leaf.support.slot_id] = source.count()
            if isinstance(node, EntityCount):
                effective = source.count()
            elif isinstance(node, SourceAggregate):
                item = lower_row(node.operand, local_fields).value
                effective = item.nunique() if node.reducer == "count_distinct" else item.count()
            else:
                value_item = lower_row(node.value, local_fields).value
                weight_item = lower_row(node.weight, local_fields).value
                effective = (
                    (value_item.notnull() & weight_item.notnull()).cast("int64").sum().fill_null(0)
                )
            values[leaf.effective.slot_id] = effective
            for index, check in enumerate(original.checks):
                name = f"attribute.check.{kind}.{'.'.join(map(str, leaf.read.occurrence))}.{index}"
                values[name] = check.violation
                checks.append((name, check))
                check_scopes.append(
                    AttributeCheckLayout(
                        name,
                        "parent" if kind else "fine",
                        (leaf,),
                        tuple(column for column in layout.columns if column.slot_id in keys),
                    )
                )
            for role, column in leaf.roles:
                if role == "additive_value":
                    value = original.value
                elif isinstance(node, SourceAggregate):
                    component = replace(
                        node,
                        reducer="count" if role == "valid_value_count" else "sum",
                    )
                    reduced = lower_source_reduction(component, source, local_fields)
                    value = reduced.value.fill_null(0)
                    component_checks: tuple[NumericCheck, ...]
                    if node.reducer == "mean" and role == "valid_value_sum":
                        item = lower_row(node.operand, local_fields).value
                        value = (
                            (item.cast("decimal(38,0)") if item.type().is_integer() else item)
                            .sum()
                            .fill_null(0)
                        )
                        component_checks = ()
                    else:
                        component_checks = reduced.checks
                    for index, check in enumerate(component_checks):
                        name = f"attribute.componentcheck.{kind}.{'.'.join(map(str, leaf.read.occurrence))}.{role}.{index}"
                        values[name] = check.violation
                        checks.append((name, check))
                        check_scopes.append(
                            AttributeCheckLayout(
                                name,
                                "parent" if kind else "fine",
                                (leaf,),
                                tuple(
                                    column for column in layout.columns if column.slot_id in keys
                                ),
                            )
                        )
                elif isinstance(node, SourceWeightedMean):
                    value_item = lower_row(node.value, local_fields).value
                    weight = lower_row(node.weight, local_fields).value
                    pair = value_item.notnull() & weight.notnull()
                    if role == "pair_count":
                        value = pair.cast("int64").sum().fill_null(0)
                    else:
                        item = (
                            numeric_binary(
                                "multiply",
                                lower_row(node.value, local_fields),
                                lower_row(node.weight, local_fields),
                            ).value
                            if role == "pair_product_sum"
                            else weight
                        )
                        if item.type().is_integer():
                            item = item.cast("decimal(38,0)")
                        value = pair.ifelse(item, ibis.null().cast(item.type())).sum().fill_null(0)
                else:
                    assert isinstance(node, EntityCount)
                    value = source.count()
                values[column.slot_id] = value
        result = source.group_by(**keys).aggregate(**values) if keys else source.aggregate(**values)
        for original_table in originals:
            joined = result.inner_join(
                original_table,
                [result[key].identical_to(original_table[key]) for key in keys]
                if keys
                else ibis.literal(True),
            )
            result = joined.select(
                result,
                *(original_table[name] for name in original_table.columns if name not in keys),
            )
        for native in layout.native_formulas:
            if native.fiber != layout.leaves[0].fiber:
                continue
            bindings = {
                value.occurrence: NumericValue(result[value.column.slot_id])
                for value in native.program.values
                if value.column.slot_id in result.columns
            }
            original = lower_metric_formula(
                native.node, native.occurrence, bindings, dict(native.program.declared)
            )
            result = result.mutate(**{native.column.slot_id: original.value})
            for check_index, check in enumerate(original.checks):
                name = original_input_slot(kind, native.path, f"native_check.{check_index}")
                result = result.mutate(**{name: check.violation})
                checks.append((name, check))
                check_scopes.append(
                    AttributeCheckLayout(
                        name,
                        "parent" if kind else "fine",
                        tuple(
                            leaf
                            for leaf in layout.leaves
                            if leaf.use.metric == native.use.metric
                            and leaf.use.occurrence[: len(native.occurrence)] == native.occurrence
                        ),
                        tuple(column for column in layout.columns if column.slot_id in keys),
                    )
                )
        if kind == 1 and not layout.parent_keys:
            result = result.filter(parent_active)
        return result.mutate(**{layout.kind: ibis.literal(kind)})

    if layout.scope == "parent":
        relations: tuple[ir.Table, ...] = (aggregate(table, parent_axes, 1),)
    else:
        fine_source = table.filter(table[layout.assigned]) if layout.partition else table
        fine = aggregate(
            fine_source,
            {slot: fine_source[value.get_name()] for slot, value in axes.items()},
            0,
        )
        diagnostics: list[ir.Table] = []
        if layout.population is not None:
            domain = ibis.table(
                {column.slot_id: ibis_type(column.dtype) for column in layout.population.columns},
                name=layout.population.placeholder,
            )
            missing = fine.anti_join(
                domain,
                [
                    fine[key].identical_to(domain[column.slot_id])
                    for key, column in zip(layout.keys, layout.population.columns, strict=True)
                ],
            )
            diagnostics.append(
                _first_failure(missing.select(*layout.keys), layout.keys).mutate(
                    **{layout.kind: ibis.literal(6)}
                )
            )
        identities = {column.slot_id: table[name] for column, name in layout.identity}
        if layout.partition:
            roots = table.select(**parent_axes, **identities).distinct()
            assigned = fine_source.select(
                **{slot: fine_source[value.get_name()] for slot, value in axes.items()},
                **{column.slot_id: fine_source[name] for column, name in layout.identity},
            ).distinct()
            root_keys = (*layout.parent_keys, *identities)
            missing = roots.anti_join(
                assigned,
                [roots[key].identical_to(assigned[key]) for key in root_keys],
            )
            diagnostics.append(
                _first_failure(missing, root_keys).mutate(**{layout.kind: ibis.literal(2)})
            )
            diagnostics.append(
                _partition_failure(assigned, root_keys, tuple(axes)).mutate(
                    **{layout.kind: ibis.literal(3)}
                )
            )
        for index, leaf in enumerate(layout.leaves):
            if leaf.distinct is None:
                continue
            node = require_reduction(leaf.read).node
            assert isinstance(node, SourceAggregate)
            value = lower_row(
                node.operand, {path: fine_source[column] for path, column in field_columns.items()}
            ).value
            assigned = fine_source.select(
                **{slot: fine_source[item.get_name()] for slot, item in axes.items()},
                **{leaf.distinct.slot_id: value},
            ).distinct()
            assigned = assigned.filter(assigned[leaf.distinct.slot_id].notnull())
            diagnostics.append(
                _partition_failure(
                    assigned, (*layout.parent_keys, leaf.distinct.slot_id), tuple(axes)
                ).mutate(**{layout.kind: ibis.literal(5), "__attribute_leaf": ibis.literal(index)})
            )
        relations = (fine, *diagnostics)
    return _union_attribute_relations(relations), tuple(checks), tuple(check_scopes)


def _first_failure(table: ir.Table, keys: tuple[str, ...]) -> ir.Table:
    """Sort failed candidates only, selecting one exact native key."""
    return (
        table.order_by(*(table[key].asc(nulls_first=True) for key in keys)).limit(1)
        if keys
        else table.limit(1)
    )


def _partition_failure(
    assigned: ir.Table, keys: tuple[str, ...], groups: tuple[str, ...]
) -> ir.Table:
    counts = assigned.group_by(*keys).aggregate(__attribute_groups=assigned.count())
    failed = _first_failure(counts.filter(counts.__attribute_groups > 1), keys)
    samples = assigned.semi_join(failed, [assigned[key].identical_to(failed[key]) for key in keys])
    return samples.order_by(*(samples[key].asc(nulls_first=True) for key in groups)).limit(6)


def attribute_input_program(
    operation: AttributeInputsValues,
    parent: DataSchema,
    left: DataSchema,
    right: DataSchema,
    left_layouts: tuple[AttributeRootLayout, ...],
    right_layouts: tuple[AttributeRootLayout, ...],
) -> AttributeInputProgram:
    """Fix the actual sufficient component matrix, without contribution arithmetic."""
    from marivo.analysis.core.attribute import (
        AttributeInputProgram,
        AttributeMatrixColumn,
        attribute_schema,
    )
    from marivo.analysis.core.data_schema import DataSchema

    base = attribute_schema(left, right, parent, operation.mapping)
    from marivo.analysis.core.attribute import AttributeMatrixSource, AttributeWindowInput

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
                    from marivo._definition.types import CalendarCoordinate

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
                    slot, calendar = next(iter(require_reduction(leaf.read).calendars))
                    from marivo._definition.types import CalendarCoordinate

                    assert isinstance(calendar, CalendarCoordinate)
                    window = AttributeWindowInput(
                        binding.window_key, slot, binding.period, binding.time, calendar
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
    from marivo.analysis.binding.attribute_rules import (
        AdditiveSource,
        RuleAdd,
        RuleNegate,
        RuleScale,
        RuleSubtract,
    )

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
