"""Source-free units shared by Attribute capture and compiled components."""

from __future__ import annotations

from typing import Literal

from ibis.common.deferred import Resolver

from marivo._definition.expressions import (
    EntityCount,
    MeasureAssertion,
    RowField,
    RowNode,
    SourceAggregate,
    SourceWeightedMean,
)
from marivo.analysis.binding.attribute_rules import (
    AdditiveSource,
    AttributeRule,
    AttributeSourceRequest,
    DirectRatio,
    NativeMean,
    NativeWeightedMean,
    RatioComponentRule,
    RuleAdd,
    RuleRescale,
    RuleSubtract,
)
from marivo.analysis.core.model import CoreRuleError


def row_unit(node: RowNode) -> str | None:
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
    from marivo.semantic.expressions import _arith_unit
    from marivo.semantic.unit_algebra import linear_unit

    if isinstance(node, RowField):
        return node.unit
    if isinstance(node, MeasureAssertion):
        return node.unit if node.unit is not None else row_unit(node.operand)
    inputs = tuple(row_unit(item) for item in node.inputs)

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
            raise CoreRuleError(
                expected="a captured row unit recipe",
                received=type(recipe).__name__,
                repair="Use a supported semantic expression.",
                location="frame.attribute",
                canonical_id="analysis_frame.attribute",
            )
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


def component_unit(
    node: SourceAggregate | SourceWeightedMean | EntityCount, role: str
) -> str | None:
    from marivo.semantic.unit_algebra import multiply_unit_states, render_unit, unit_state

    if role in ("input_count", "effective_count", "valid_value_count", "pair_count") or isinstance(
        node, EntityCount
    ):
        return "1"
    if isinstance(node, SourceWeightedMean):
        value, weight = row_unit(node.value), row_unit(node.weight)
        if role == "pair_weight_sum":
            return weight
        if role == "pair_product_sum":
            return render_unit(multiply_unit_states(unit_state(value), unit_state(weight)))
        return value
    return "1" if node.reducer in ("count", "count_distinct") else row_unit(node.operand)


def source_component_unit(source: AttributeSourceRequest, role: str) -> str | None:
    node = source.binding.definition
    assert isinstance(node, (SourceAggregate, SourceWeightedMean, EntityCount))
    return component_unit(node, role)


def rule_unit(rule: AttributeRule) -> str | None:
    """Derive a closed rule's unit without preparing or reading sources."""
    from marivo.semantic.unit_algebra import linear_unit, ratio_unit

    operation = rule.operation
    if isinstance(operation, AdditiveSource):
        return source_component_unit(operation.source, "additive_value")
    if isinstance(operation, DirectRatio):
        return ratio_unit(rule_unit(operation.numerator), rule_unit(operation.denominator))
    if isinstance(operation, (NativeMean, NativeWeightedMean)):
        return source_component_unit(operation.source, "value")
    if isinstance(operation, RuleRescale):
        return operation.unit
    if isinstance(operation, (RuleAdd, RuleSubtract)):
        return linear_unit((rule_unit(operation.left), rule_unit(operation.right)))
    return rule_unit(operation.operand)


def ratio_component_units(rule: RatioComponentRule) -> tuple[str | None, str | None]:
    """Retain the independent numerator and denominator semantic units."""
    operation = rule.operation
    if isinstance(operation, DirectRatio):
        return rule_unit(operation.numerator), rule_unit(operation.denominator)
    roles = (
        ("valid_value_sum", "valid_value_count")
        if isinstance(operation, NativeMean)
        else ("pair_product_sum", "pair_weight_sum")
    )
    return source_component_unit(operation.source, roles[0]), source_component_unit(
        operation.source, roles[1]
    )
