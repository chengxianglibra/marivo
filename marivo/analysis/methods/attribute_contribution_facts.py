"""Capture typed method facts for one original Attribute target."""

from __future__ import annotations

from marivo.analysis.binding.attribute_rules import (
    AdditiveRule,
    AttributeRule,
    DirectRatio,
    NativeMean,
    NativeWeightedMean,
    RatioComponentRule,
    RuleAdd,
    RuleDivide,
    RuleNegate,
    RuleRescale,
    RuleScale,
    RuleSubtract,
    component_requests,
)
from marivo.analysis.core.attribute import (
    _logical,
)
from marivo.analysis.core.frame_level_facts import (
    FrameLevelAdditiveMethod,
    FrameLevelDivisionMethod,
    FrameLevelLinearMethod,
    FrameLevelLinearTerm,
    FrameLevelRatioMethod,
    FrameLevelSupport,
)
from marivo.analysis.core.frame_method_facts import (
    FrameAdditiveMethod,
    FrameDivisionMethod,
    FrameLinearMethod,
    FrameLinearTerm,
    FrameMethodSupport,
    FrameRatioMethod,
)
from marivo.analysis.methods.attribute_contribution_evaluation import (
    ContributionEvaluation,
    _binary,
)
from marivo.analysis.methods.attribute_numeric import (
    _Value,
    capture_method_value,
)
from marivo.analysis.model.method_values import FrameMethodValue


def _has_division(rule: AdditiveRule) -> bool:
    """Keep scaling explanations inside otherwise collapsible additive rules."""
    operation = rule.operation
    if isinstance(operation, (RuleDivide, RuleRescale)):
        return True
    if isinstance(operation, (RuleAdd, RuleSubtract)):
        return _has_division(operation.left) or _has_division(operation.right)
    if isinstance(operation, (RuleNegate, RuleScale)):
        return _has_division(operation.operand)
    return False


def supports(
    evaluation: ContributionEvaluation, rule: AttributeRule, index: int
) -> tuple[FrameMethodSupport, ...]:
    result: list[FrameMethodSupport] = []
    seen: set[tuple[int, ...]] = set()
    for request in component_requests(rule):
        occurrence = _logical(request)
        if occurrence in seen:
            continue
        seen.add(occurrence)
        counts = [
            evaluation.inputs.values[(side, "parent", occurrence, role)].values.iloc[index]
            for side in ("left", "right")
            for role in ("input_count", "effective_count")
        ]
        result.append(
            FrameMethodSupport(
                "root." + ".".join(map(str, occurrence)),
                request.support,
                int(counts[0]),
                int(counts[2]),
                int(counts[1]),
                int(counts[3]),
            )
        )
    return tuple(result)


def method(
    evaluation: ContributionEvaluation,
    rule: AttributeRule,
    other_rule: AttributeRule,
    index: int,
    totals: tuple[_Value, _Value] | None = None,
    path: tuple[int, ...] | None = None,
) -> FrameAdditiveMethod | FrameRatioMethod | FrameLinearMethod | FrameDivisionMethod:
    if path is None:
        path = (evaluation.metric_index,)
    a, b = totals or (
        evaluation.evaluate(rule, "left", "parent", original=True, path=path),
        evaluation.evaluate(other_rule, "right", "parent", original=True, path=path),
    )
    change = _binary("sub", a, b)
    common = (
        capture_method_value(a, index),
        capture_method_value(b, index),
        capture_method_value(change, index),
    )
    if isinstance(rule.operation, (RuleDivide, RuleRescale)):
        division = rule.operation
        assert isinstance(other_rule.operation, (RuleDivide, RuleRescale))
        return FrameDivisionMethod(
            "division",
            *common,
            division.scalar.value,
            "rescale" if isinstance(division, RuleRescale) else "divide",
            method(
                evaluation, division.operand, other_rule.operation.operand, index, path=(*path, 0)
            ),
        )
    if isinstance(rule, AdditiveRule) and not _has_division(rule):
        return FrameAdditiveMethod("additive", *common, supports(evaluation, rule, index))
    if isinstance(rule, RatioComponentRule):
        components: list[FrameMethodValue] = []
        ns: list[FrameMethodValue] = []
        ds: list[FrameMethodValue] = []
        for side, current_rule in (("left", rule), ("right", other_rule)):
            assert isinstance(current_rule, RatioComponentRule)
            operation = current_rule.operation
            if isinstance(operation, DirectRatio):
                n, d = (
                    evaluation.evaluate(operation.numerator, side, "parent"),
                    evaluation.evaluate(operation.denominator, side, "parent"),
                )
            else:
                roles = (
                    ("valid_value_sum", "valid_value_count")
                    if isinstance(operation, NativeMean)
                    else ("pair_product_sum", "pair_weight_sum")
                )
                n, d = (
                    evaluation.inputs.values[(side, "parent", _logical(operation.source), role)]
                    for role in roles
                )
            ns.append(capture_method_value(n, index))
            ds.append(capture_method_value(d, index))
        components = [*ns, *ds]
        return FrameRatioMethod(
            "ratio_component",
            *common,
            components[0],
            components[1],
            components[2],
            components[3],
            supports(evaluation, rule, index),
        )
    linear_operation, other_operation = rule.operation, other_rule.operation
    terms: list[FrameLinearTerm] = []
    if isinstance(linear_operation, (RuleAdd, RuleSubtract)):
        assert isinstance(other_operation, type(linear_operation))
        terms.append(
            FrameLinearTerm(
                1,
                method(
                    evaluation, linear_operation.left, other_operation.left, index, path=(*path, 0)
                ),
            )
        )
        terms.append(
            FrameLinearTerm(
                1 if isinstance(linear_operation, RuleAdd) else -1,
                method(
                    evaluation,
                    linear_operation.right,
                    other_operation.right,
                    index,
                    path=(*path, 1),
                ),
            )
        )
    else:
        assert isinstance(linear_operation, (RuleNegate, RuleScale)) and isinstance(
            other_operation, (RuleNegate, RuleScale)
        )
        terms.append(
            FrameLinearTerm(
                -1 if isinstance(linear_operation, RuleNegate) else linear_operation.scalar.value,
                method(
                    evaluation,
                    linear_operation.operand,
                    other_operation.operand,
                    index,
                    path=(*path, 0),
                ),
            )
        )
    return FrameLinearMethod("linear", *common, tuple(terms))


def level_method(
    evaluation: ContributionEvaluation,
    rule: AttributeRule,
    index: int,
    total: _Value | None = None,
    path: tuple[int, ...] = (0,),
) -> (
    FrameLevelAdditiveMethod
    | FrameLevelRatioMethod
    | FrameLevelLinearMethod
    | FrameLevelDivisionMethod
):
    value = capture_method_value(
        total
        if total is not None
        else evaluation.evaluate(rule, "left", "parent", original=True, path=path),
        index,
    )
    supporting = []
    for request in component_requests(rule):
        occurrence = _logical(request)
        count = evaluation.inputs.values[("left", "parent", occurrence, "input_count")].values.iloc[
            index
        ]
        effective = evaluation.inputs.values[
            ("left", "parent", occurrence, "effective_count")
        ].values.iloc[index]
        supporting.append(
            FrameLevelSupport(
                "root." + ".".join(map(str, occurrence)),
                request.support,
                int(count),
                int(effective),
            )
        )
    support = tuple(dict.fromkeys(supporting))
    operation = rule.operation
    if isinstance(operation, (RuleDivide, RuleRescale)):
        return FrameLevelDivisionMethod(
            "level_division",
            value,
            operation.scalar.value,
            "rescale" if isinstance(operation, RuleRescale) else "divide",
            level_method(evaluation, operation.operand, index, path=(*path, 0)),
        )
    if isinstance(rule, AdditiveRule) and not _has_division(rule):
        return FrameLevelAdditiveMethod("level_additive", value, support)
    if isinstance(rule, RatioComponentRule):
        if isinstance(operation, DirectRatio):
            n, d = (
                evaluation.evaluate(operation.numerator, "left", "parent"),
                evaluation.evaluate(operation.denominator, "left", "parent"),
            )
        else:
            assert isinstance(operation, (NativeMean, NativeWeightedMean))
            roles = (
                ("valid_value_sum", "valid_value_count")
                if isinstance(operation, NativeMean)
                else ("pair_product_sum", "pair_weight_sum")
            )
            n, d = (
                evaluation.inputs.values[("left", "parent", _logical(operation.source), role)]
                for role in roles
            )
        return FrameLevelRatioMethod(
            "level_ratio_component",
            value,
            capture_method_value(n, index),
            capture_method_value(d, index),
            support,
        )
    if isinstance(operation, (RuleAdd, RuleSubtract)):
        terms: tuple[FrameLevelLinearTerm, ...] = (
            FrameLevelLinearTerm(
                1, level_method(evaluation, operation.left, index, path=(*path, 0))
            ),
            FrameLevelLinearTerm(
                1 if isinstance(operation, RuleAdd) else -1,
                level_method(evaluation, operation.right, index, path=(*path, 1)),
            ),
        )
    else:
        assert isinstance(operation, (RuleNegate, RuleScale))
        terms = (
            FrameLevelLinearTerm(
                -1 if isinstance(operation, RuleNegate) else operation.scalar.value,
                level_method(evaluation, operation.operand, index, path=(*path, 0)),
            ),
        )
    return FrameLevelLinearMethod("level_linear", value, terms)
