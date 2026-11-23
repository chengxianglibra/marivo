"""Compose the effect decomposition of admitted Attribute recipes."""

from __future__ import annotations

from dataclasses import replace

import pandas as pd

from marivo.analysis.binding.attribute_rules import (
    AdditiveRule,
    AttributeRule,
    RatioComponentRule,
    RuleAdd,
    RuleDivide,
    RuleNegate,
    RuleRescale,
    RuleScale,
    RuleSubtract,
)
from marivo.analysis.core.attribute import (
    _coefficient_column,
    _rule_column,
    _type,
)
from marivo.analysis.methods.attribute_contribution_evaluation import ContributionEvaluation
from marivo.analysis.methods.attribute_numeric import (
    _Value,
    _with_roundoff,
)
from marivo.analysis.methods.numeric_state import (
    finish_float64,
)
from marivo.analysis.model.data_types import (
    ScalarDataType,
)


def ratio_effects(
    evaluation: ContributionEvaluation,
    rule: RatioComponentRule,
    other: RatioComponentRule,
    path: tuple[int, ...],
) -> dict[str, _Value]:
    dtype = _type(
        "sub",
        _rule_column(evaluation.program.input_program, rule, "left"),
        _rule_column(evaluation.program.input_program, other, "right"),
    )
    r1, w1, d1 = evaluation.rate_endpoint(rule, "left", path)
    r0, w0, d0 = evaluation.rate_endpoint(other, "right", path)
    r1, w1, r0, w0 = (_with_roundoff(value) for value in (r1, w1, r0, w0))
    surviving = ((d1.values > 0) & (d0.values > 0)).fillna(False)
    # Undefined rates stay undefined in original diagnostics. The masked
    # working values prevent undefined arithmetic outside surviving support.
    r1 = replace(
        r1,
        values=r1.values.where(surviving, 0),
        roundoff=None if r1.roundoff is None else r1.roundoff.where(surviving, 0),
    )
    r0 = replace(
        r0,
        values=r0.values.where(surviving, 0),
        roundoff=None if r0.roundoff is None else r0.roundoff.where(surviving, 0),
    )
    two = _Value(pd.Series([2] * len(r1.values), dtype=object), _coefficient_column(2))
    within = evaluation.arithmetic(
        "mul",
        evaluation.arithmetic(
            "div",
            evaluation.arithmetic("add", w0, w1, rule, "both", "fine"),
            two,
            rule,
            "both",
            "fine",
        ),
        evaluation.arithmetic("sub", r1, r0, rule, "both", "fine"),
        rule,
        "both",
        "fine",
        output=dtype,
    )
    mix = evaluation.arithmetic(
        "mul",
        evaluation.arithmetic(
            "div",
            evaluation.arithmetic("add", r0, r1, rule, "both", "fine"),
            two,
            rule,
            "both",
            "fine",
        ),
        evaluation.arithmetic("sub", w1, w0, rule, "both", "fine"),
        rule,
        "both",
        "fine",
        output=dtype,
    )
    allocation = evaluation.arithmetic(
        "sub",
        _with_roundoff(evaluation.evaluate(rule, "left", "fine", path=path)),
        _with_roundoff(evaluation.evaluate(other, "right", "fine", path=path)),
        rule,
        "both",
        "fine",
        output=dtype,
    )
    entry = replace(
        allocation,
        values=allocation.values.where(~surviving, 0),
        roundoff=None if allocation.roundoff is None else allocation.roundoff.where(~surviving, 0),
    )
    result = {"within": within, "mix": mix, "entry_exit": entry}
    combined = evaluation.arithmetic(
        "add",
        evaluation.arithmetic("add", within, mix, rule, "both", "fine"),
        entry,
        rule,
        "both",
        "fine",
        output=dtype,
    )
    evaluation.assert_same(combined, allocation, rule, "both", "fine", rounded=True)
    return result


def effects(
    evaluation: ContributionEvaluation,
    rule: AttributeRule,
    other: AttributeRule,
    path: tuple[int, ...],
) -> dict[str, _Value]:
    if isinstance(rule, RatioComponentRule):
        assert isinstance(other, RatioComponentRule)
        return ratio_effects(evaluation, rule, other, path)
    if isinstance(rule, AdditiveRule):
        return {
            "additive": evaluation.arithmetic(
                "sub",
                _with_roundoff(evaluation.evaluate(rule, "left", "fine", path=path)),
                _with_roundoff(evaluation.evaluate(other, "right", "fine", path=path)),
                rule,
                "both",
                "fine",
            )
        }
    operation, other_operation = rule.operation, other.operation
    if isinstance(operation, (RuleAdd, RuleSubtract)):
        assert isinstance(other_operation, type(operation))
        left = effects(evaluation, operation.left, other_operation.left, (*path, 0))
        right = effects(evaluation, operation.right, other_operation.right, (*path, 1))
        result: dict[str, _Value] = {}
        for role in dict.fromkeys((*left, *right)):
            a, b = left.get(role), right.get(role)
            if a is not None and b is not None:
                result[role] = evaluation.arithmetic(
                    "add" if isinstance(operation, RuleAdd) else "sub",
                    a,
                    b,
                    rule,
                    "both",
                    "fine",
                )
            elif a is not None:
                result[role] = a
            else:
                assert b is not None
                result[role] = (
                    evaluation.arithmetic(
                        "mul",
                        b,
                        _Value(pd.Series([-1] * len(b.values)), _coefficient_column(-1)),
                        rule,
                        "both",
                        "fine",
                    )
                    if isinstance(operation, RuleSubtract)
                    else b
                )
        return result
    assert isinstance(operation, (RuleNegate, RuleScale, RuleDivide, RuleRescale))
    assert isinstance(other_operation, type(operation))
    children = effects(evaluation, operation.operand, other_operation.operand, (*path, 0))
    scalar = -1 if isinstance(operation, RuleNegate) else operation.scalar.value
    scaled: dict[str, _Value] = {}
    for role, value in children.items():
        coefficient = _Value(
            pd.Series([scalar] * len(value.values), dtype=object), _coefficient_column(scalar)
        )
        dividing = isinstance(operation, (RuleDivide, RuleRescale))
        if dividing and _type("div", value.column, coefficient.column) == ScalarDataType("float64"):
            value, coefficient = (
                _Value(
                    finish_float64(item.values, "Attribute effect division operand"),
                    replace(item.column, dtype=ScalarDataType("float64")),
                    item.roundoff,
                )
                for item in (value, coefficient)
            )
        scaled[role] = evaluation.arithmetic(
            "div" if isinstance(operation, (RuleDivide, RuleRescale)) else "mul",
            value,
            coefficient,
            rule,
            "both",
            "fine",
        )
    return scaled
