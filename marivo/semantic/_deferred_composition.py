"""Compose compatible recipe operands and preserve their declared units."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from decimal import Decimal
from typing import Literal, cast

from ibis.common.deferred import (
    Attr,
    BinaryOperator,
    Call,
    Just,
    Resolver,
    Sequence,
    UnaryOperator,
    Variable,
)

from marivo._definition.deferred import (
    RescaleResolver,
)
from marivo._definition.expressions import (
    MeasureAssertion,
    MetricExpression,
    MetricNode,
    RowExpression,
    RowField,
)
from marivo._definition.types import (
    SumNone,
    SumPermission,
)
from marivo.semantic._deferred_facts import (
    InspectRecipe,
    MetricFacts,
    RowFacts,
    _Facts,
    _fail,
    _Samples,
    _Scalar,
)
from marivo.semantic.errors import ErrorKind
from marivo.semantic.expressions.operations import _arith_unit, _intersect
from marivo.semantic.unit_algebra import (
    FactorizedUnitV2,
    linear_unit,
    linear_units_conflict,
    unit_state,
)


def _rename_slots(recipe: Resolver, offset: int) -> Resolver:
    if isinstance(recipe, RescaleResolver):
        return RescaleResolver(_rename_slots(recipe.recipe, offset), recipe.divisor, recipe.unit)
    if isinstance(recipe, Variable):
        name = str(recipe.name)
        return Variable(f"slot_{int(name.removeprefix('slot_')) + offset}")
    if isinstance(recipe, BinaryOperator):
        return BinaryOperator(
            recipe.func, _rename_slots(recipe.left, offset), _rename_slots(recipe.right, offset)
        )
    if isinstance(recipe, UnaryOperator):
        return UnaryOperator(recipe.func, _rename_slots(recipe.arg, offset))
    if isinstance(recipe, Call):
        return Call(
            _rename_slots(recipe.func, offset),
            *(_rename_slots(arg, offset) for arg in recipe.args),
            **{key: _rename_slots(arg, offset) for key, arg in recipe.kwargs.items()},
        )
    if isinstance(recipe, Attr):
        return Attr(_rename_slots(recipe.obj, offset), recipe.name)
    if isinstance(recipe, Sequence):
        return Sequence(tuple(_rename_slots(item, offset) for item in recipe.values))
    return recipe


def _row_recipe(value: RowFacts, inputs: list[RowField | MeasureAssertion]) -> Resolver:
    if isinstance(value.node, RowExpression):
        recipe = _rename_slots(value.node.recipe, len(inputs))
        inputs.extend(value.node.inputs)
        return recipe
    if isinstance(value.node, (RowField, MeasureAssertion)):
        recipe = Variable(f"slot_{len(inputs)}")
        inputs.append(value.node)
        return recipe
    raise AssertionError("native row consumption produced an obsolete numeric node")


def _result_branches(
    recipe: Resolver, children: tuple[_Facts, ...], operation: str, consume: InspectRecipe
) -> tuple[_Facts, ...]:
    if operation == "ifelse":
        return children[1:]
    if operation == "cases" and isinstance(recipe, Call):
        results: list[_Facts] = []
        for branch in recipe.args:
            if not isinstance(branch, Sequence) or len(branch.values) != 2:
                _fail(
                    "Case branches require match/result pairs.",
                    expected="two-element case branches",
                    received=type(branch).__name__,
                )
            results.append(consume(branch.values[1]))
        results.append(consume(recipe.kwargs.get("else_", Just(None))))
        return tuple(results)
    return children


def _validate_operands(
    children: tuple[_Facts, ...], rows: tuple[RowFacts, ...], operation: str
) -> None:
    numerical = operation not in {
        "eq",
        "ne",
        "lt",
        "le",
        "gt",
        "ge",
        "and_",
        "or_",
        "invert",
        "isnull",
        "notnull",
        "is_null",
        "isin",
        "ifelse",
        "coalesce",
        "nullif",
        "cases",
        "name",
    }
    arithmetic = operation in {"add", "sub", "mul", "truediv", "divide", "pow", "mod", "neg", "abs"}
    comparison = operation in {"eq", "ne", "lt", "le", "gt", "ge"}
    for child in children[:2] if operation == "divide" else children:
        if not isinstance(child, _Scalar):
            continue
        if arithmetic and (
            isinstance(child.value, bool) or not isinstance(child.value, (int, float, Decimal))
        ):
            _fail(
                "Numeric arithmetic requires numeric scalar literals.",
                expected="a finite number",
                received=type(child.value).__name__,
                kind=ErrorKind.INVALID_EXPRESSION_VALUE,
            )
        if comparison and isinstance(child.recipe, Sequence):
            _fail(
                "Scalar comparison cannot consume a sequence.",
                expected="one scalar or isin(sequence)",
                received="sequence",
                kind=ErrorKind.INVALID_EXPRESSION_VALUE,
            )
        if comparison and any(row.numeric for row in rows) and isinstance(child.value, bool):
            _fail(
                "Numeric comparison cannot consume a boolean.",
                expected="a numeric scalar",
                received="bool",
                kind=ErrorKind.INVALID_EXPRESSION_VALUE,
            )
        if operation in {"and_", "or_"} and not isinstance(child.value, bool):
            _fail(
                "Boolean composition requires predicates.",
                expected="boolean predicates",
                received=type(child.value).__name__,
            )
    if numerical and any(
        row.field is not None
        and row.field.field_read is not None
        and (row.field.field_read.path or row.field.field_read.at is not None)
        for row in rows
    ):
        _fail(
            "Scalar path bindings do not authorize row arithmetic.",
            expected="native fields for numerical row computation",
            received=operation,
        )
    if numerical and any(not row.numeric or row.boolean for row in rows):
        _fail(
            "A categorical or time field is not a Measure.",
            expected="numeric Measure inputs",
            received=operation,
        )


def _composed_unit(
    children: tuple[_Facts, ...], branches: tuple[_Facts, ...], operation: str, *, row_stage: bool
) -> str | None:
    units = tuple(item.unit for item in children if isinstance(item, (RowFacts, MetricFacts)))

    unit = units[0] if units else None
    if operation in {"eq", "ne", "lt", "le", "gt", "ge", "least", "greatest", "clip"}:
        if linear_units_conflict(units):
            _fail(
                "Native inputs have incompatible units.",
                expected="commensurable comparison, bound, or selection units",
                received=f"{operation}{units}",
                scope_dependent=True,
            )
        unit = linear_unit(units)
    if operation in {"add", "sub", "mul", "truediv", "divide"} and len(children) >= 2:
        left, right = children[:2]
        unit = _arith_unit(
            cast(
                "Literal['add', 'subtract', 'multiply', 'divide']",
                {
                    "add": "add",
                    "sub": "subtract",
                    "mul": "multiply",
                    "truediv": "divide",
                    "divide": "divide",
                }[operation],
            ),
            left.unit if isinstance(left, (RowFacts, MetricFacts)) else None,
            right.unit if isinstance(right, (RowFacts, MetricFacts)) else None,
            constant="left"
            if isinstance(left, _Scalar)
            else "right"
            if isinstance(right, _Scalar)
            else None,
            stage="Row" if row_stage else "Metric",
        )
    if operation in {"ifelse", "coalesce", "nullif", "cases"}:
        branch_units = tuple(
            item.unit
            for item in branches
            if isinstance(item, (RowFacts, MetricFacts)) and not item.boolean
        )
        if linear_units_conflict(branch_units):
            _fail(
                "Native branches have incompatible units.",
                expected="commensurable branch units",
                received=str(branch_units),
                scope_dependent=True,
            )
        unit = linear_unit(branch_units)
    if operation == "sqrt" and unit not in (None, "1"):
        state = unit_state(unit)
        if not isinstance(state, FactorizedUnitV2) or any(
            count % 2
            for factors in (state.numerator, state.denominator)
            for count in Counter(factors).values()
        ):
            _fail(
                "Square root requires even unit exponents.",
                expected="a factorable unit with even exponents",
                received=str(unit),
                scope_dependent=True,
            )
        assert isinstance(state, FactorizedUnitV2)
        unit = FactorizedUnitV2(
            "metric-unit-algebra/v2",
            tuple(
                atom for atom, count in Counter(state.numerator).items() for _ in range(count // 2)
            ),
            tuple(
                atom
                for atom, count in Counter(state.denominator).items()
                for _ in range(count // 2)
            ),
        ).render()
    if operation in {"log", "ln", "exp"} and unit not in (None, "1"):
        _fail(
            "This native function requires a dimensionless argument.",
            expected="unit 1",
            received=f"{operation}({unit})",
            scope_dependent=True,
        )
    return unit


def _compose_rows(
    children: tuple[_Facts, ...],
    rows: tuple[RowFacts, ...],
    branches: tuple[_Facts, ...],
    construct: Callable[[tuple[Resolver, ...]], Resolver],
    operation: str,
    unit: str | None,
    boolean: bool,
) -> RowFacts:
    root = rows[0].root
    if any(row.root.ref != root.ref for row in rows):
        _fail(
            "Row expression crosses contribution roots.",
            kind=ErrorKind.CROSS_ROOT_ROW_EXPRESSION,
            expected=root.ref.key,
            received=", ".join(row.root.ref.key for row in rows),
        )
    inputs: list[RowField | MeasureAssertion] = []
    captured = tuple(
        _row_recipe(item, inputs) if isinstance(item, RowFacts) else item.recipe
        for item in children
        if isinstance(item, (RowFacts, _Scalar))
    )
    permission: SumPermission = rows[0].permission
    if operation in {"add", "sub"}:
        for row in rows[1:]:
            permission = _intersect(permission, row.permission)
    elif operation not in {"neg", "name"} and not (
        len(rows) == 1
        and (
            operation == "mul"
            or (operation in {"truediv", "divide"} and isinstance(children[0], RowFacts))
        )
    ):
        permission = SumNone()
    return RowFacts(
        RowExpression(
            construct(captured),
            tuple(inputs),
            tuple(
                dict.fromkeys(
                    origin
                    for row in rows
                    if isinstance(row.node, RowExpression)
                    for origin in row.node.origins
                )
            ),
        ),
        root,
        unit,
        permission,
        tuple(dict.fromkeys(axis for row in rows for axis in row.coordinates)),
        numeric=all(
            row.numeric
            for row in (
                tuple(item for item in branches if isinstance(item, RowFacts))
                if operation in {"ifelse", "cases"}
                else rows
            )
        ),
        boolean=boolean,
        default_time=rows[0].default_time,
    )


def _compose_metrics(
    children: tuple[_Facts, ...],
    metrics: tuple[MetricFacts, ...],
    construct: Callable[[tuple[Resolver, ...]], Resolver],
    unit: str | None,
    boolean: bool,
) -> MetricFacts:
    operands: list[MetricNode] = []
    captured_list: list[Resolver] = []
    for child in children:
        if isinstance(child, MetricFacts):
            if isinstance(child.node, MetricExpression) and isinstance(child.node.recipe, Sequence):
                captured_list.append(_rename_slots(child.node.recipe, len(operands)))
                operands.extend(child.node.operands)
            else:
                captured_list.append(Variable(f"slot_{len(operands)}"))
                operands.append(child.node)
        elif isinstance(child, _Scalar):
            captured_list.append(child.recipe)
    return MetricFacts(
        MetricExpression(construct(tuple(captured_list)), tuple(operands)),
        tuple(dict.fromkeys(root for metric in metrics for root in metric.roots)),
        unit,
        tuple(pair for metric in metrics for pair in metric._declared),
        boolean,
    )


def _compose(
    recipe: Resolver,
    children: tuple[_Facts, ...],
    rebuild: Callable[[tuple[Resolver, ...]], Resolver],
    consume: InspectRecipe,
    *,
    operation: str,
) -> _Facts:
    rows = tuple(item for item in children if isinstance(item, RowFacts))
    metrics = tuple(item for item in children if isinstance(item, MetricFacts))
    if any(isinstance(item, _Samples) for item in children) or (rows and metrics):
        _fail(
            "Expression combines incompatible stages.",
            expected="only row values or only completed Metrics",
            received=operation,
        )
    if not rows and not metrics:
        return _Scalar(recipe, None)
    branches = _result_branches(recipe, children, operation, consume)
    boolean = operation in {
        "eq",
        "ne",
        "lt",
        "le",
        "gt",
        "ge",
        "and_",
        "or_",
        "invert",
        "isnull",
        "notnull",
        "is_null",
        "isin",
    }
    if operation in {"ifelse", "coalesce", "nullif", "cases"}:
        boolean = bool(branches) and all(
            item.boolean
            if isinstance(item, (RowFacts, MetricFacts))
            else isinstance(item, _Scalar) and isinstance(item.value, bool)
            for item in branches
        )
    _validate_operands(children, rows, operation)
    unit = _composed_unit(children, branches, operation, row_stage=bool(rows))
    if boolean:
        unit = None
    if rows:
        return _compose_rows(children, rows, branches, rebuild, operation, unit, boolean)
    return _compose_metrics(children, metrics, rebuild, unit, boolean)
