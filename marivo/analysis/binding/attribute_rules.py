"""Recognize closed contribution rules without reading or evaluating source data."""

from __future__ import annotations

import math
import operator
from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Generic, Literal, NoReturn, TypeAlias, TypeVar

from ibis.common.deferred import Attr, BinaryOperator, Call, Just, Resolver, UnaryOperator, Variable

from marivo._definition.correspondence import ComparisonAxis
from marivo._definition.deferred import attribute_name
from marivo._definition.expressions import (
    ContributionFilter,
    DeclaredMetricRef,
    EntityCount,
    MeasureAssertion,
    MetricExpression,
    MetricNode,
    MetricWindow,
    PerReduce,
    PerWeightedMean,
    RowField,
    RowNode,
    SourceAggregate,
    SourceWeightedMean,
)
from marivo._definition.observations import ContributionBinding
from marivo._definition.types import (
    AxisBinding,
    CalendarCoordinate,
    ExactRef,
    FieldRead,
    FiniteNumber,
    SumAll,
    SumAllExcept,
    SumAxes,
    SumPermission,
    literal_token,
)
from marivo._definition.windows import ObservationWindow
from marivo.analysis.core.model import CoreRuleError

AttributeComponentRole: TypeAlias = Literal[
    "additive_value",
    "valid_value_sum",
    "valid_value_count",
    "pair_product_sum",
    "pair_weight_sum",
    "pair_count",
]
AttributeSupport: TypeAlias = Literal["rows", "non_null_value", "identity", "co_valid_value_weight"]
AttributeRuleReason: TypeAlias = Literal["unsupported_expression", "missing_component_proof"]
_RuleT = TypeVar("_RuleT", bound="AttributeRule", covariant=True)


class AttributeRuleError(CoreRuleError):
    """A static contribution rule lacks a required expression or binding fact."""

    def __init__(
        self,
        *,
        reason: AttributeRuleReason,
        metric: str,
        occurrence: tuple[int, ...],
        expected: str,
        received: str,
        mismatch: AttributeRuleMismatch | None = None,
    ) -> None:
        self.reason = reason
        self.metric = metric
        self.occurrence = occurrence
        self.mismatch = mismatch
        super().__init__(
            expected=expected,
            received=f"{metric} at {occurrence}: {received}",
            repair=(
                "Provide the exact captured component and sample proof for this Metric, "
                "or investigate explicitly with population_from, in_population and a finer observe/compare."
                if reason == "missing_component_proof"
                else "Use a supported additive, component ratio, or fixed linear Metric, "
                "or investigate explicitly with population_from, in_population and a finer observe/compare."
            ),
            location="frame.attribute",
            canonical_id="analysis_frame.attribute",
        )


@dataclass(frozen=True, slots=True)
class AttributeWindowProof:
    """A finite captured obligation; actual same-input proof belongs to execution."""

    rule: ObservationWindow
    bindings: tuple[ContributionBinding, ...]


@dataclass(frozen=True, slots=True)
class AttributeSourceRequest:
    """Components from one actual source use; dynamic proof belongs to its consumer."""

    binding: ContributionBinding
    roles: tuple[AttributeComponentRole, ...]
    support: AttributeSupport
    window: AttributeWindowProof | None = None


@dataclass(frozen=True, slots=True)
class AdditiveSource:
    source: AttributeSourceRequest
    distinct_partition: bool


@dataclass(frozen=True, slots=True)
class FixedScalar:
    value: FiniteNumber
    token: tuple[str, str]


@dataclass(frozen=True, slots=True)
class AttributeRuleMismatch:
    path: tuple[int, ...]
    left_operator: str
    right_operator: str
    left_coefficient: FixedScalar | None
    right_coefficient: FixedScalar | None


@dataclass(frozen=True, slots=True)
class RuleAdd(Generic[_RuleT]):
    left: _RuleT
    right: _RuleT


@dataclass(frozen=True, slots=True)
class RuleSubtract(Generic[_RuleT]):
    left: _RuleT
    right: _RuleT


@dataclass(frozen=True, slots=True)
class RuleNegate(Generic[_RuleT]):
    operand: _RuleT


@dataclass(frozen=True, slots=True)
class RuleScale(Generic[_RuleT]):
    operand: _RuleT
    scalar: FixedScalar
    side: Literal["left", "right"]


@dataclass(frozen=True, slots=True)
class RuleDivide(Generic[_RuleT]):
    operand: _RuleT
    scalar: FixedScalar


@dataclass(frozen=True, slots=True)
class RuleRescale(Generic[_RuleT]):
    operand: _RuleT
    scalar: FixedScalar
    unit: str


LinearOperation: TypeAlias = (
    RuleAdd["AttributeRule"]
    | RuleSubtract["AttributeRule"]
    | RuleNegate["AttributeRule"]
    | RuleScale["AttributeRule"]
    | RuleDivide["AttributeRule"]
    | RuleRescale["AttributeRule"]
)


@dataclass(frozen=True, slots=True)
class AdditiveRule:
    operation: (
        AdditiveSource
        | RuleAdd[AdditiveRule]
        | RuleSubtract[AdditiveRule]
        | RuleNegate[AdditiveRule]
        | RuleScale[AdditiveRule]
        | RuleDivide[AdditiveRule]
        | RuleRescale[AdditiveRule]
    )


@dataclass(frozen=True, slots=True)
class DirectRatio:
    numerator: AdditiveRule
    denominator: AdditiveRule


@dataclass(frozen=True, slots=True)
class NativeMean:
    source: AttributeSourceRequest


@dataclass(frozen=True, slots=True)
class NativeWeightedMean:
    source: AttributeSourceRequest


@dataclass(frozen=True, slots=True)
class RatioComponentRule:
    operation: DirectRatio | NativeMean | NativeWeightedMean


@dataclass(frozen=True, slots=True)
class LinearRule:
    operation: LinearOperation


AttributeRule: TypeAlias = AdditiveRule | RatioComponentRule | LinearRule
_RecipeValue: TypeAlias = AttributeRule | FixedScalar


def _allows(permission: SumPermission, axis: ComparisonAxis) -> bool:
    if isinstance(permission, SumAll):
        return True
    if not isinstance(axis, AxisBinding):
        return False
    ref = axis.field.ref if axis.field is not None else axis.entity.ref
    if isinstance(permission, SumAxes):
        return ref in {item.ref for item in permission.axes}
    if isinstance(permission, SumAllExcept):
        return ref not in {item.ref for item in permission.axes}
    return False


def _row_allows(node: RowNode, axis: ComparisonAxis) -> bool:
    if isinstance(node, RowField):
        return _allows(node.permission, axis)
    if isinstance(node, MeasureAssertion):
        return (
            _allows(node.permission, axis)
            if node.permission is not None
            else _row_allows(node.operand, axis)
        )

    def visit(recipe: Resolver) -> bool | None:
        from marivo._definition.deferred import RescaleResolver

        if isinstance(recipe, RescaleResolver):
            return visit(recipe.recipe)
        if type(recipe) is Variable:
            for index, item in enumerate(node.inputs):
                if recipe.name == f"slot_{index}":
                    return _row_allows(item, axis)
            return False
        if type(recipe) is Just:
            return None
        if type(recipe) is UnaryOperator and recipe.func is operator.neg:
            return visit(recipe.arg)
        if type(recipe) is BinaryOperator:
            left, right = visit(recipe.left), visit(recipe.right)
            rows = tuple(item for item in (left, right) if item is not None)
            if recipe.func in (operator.add, operator.sub):
                return all(rows) if rows else None
            if recipe.func is operator.mul and len(rows) == 1:
                return rows[0]
            if recipe.func is operator.truediv and left is not None and right is None:
                return left
            return False
        if type(recipe) is Call and type(recipe.func) is Attr:
            name = attribute_name(recipe.func)
            if name == "name":
                return visit(recipe.func.obj)
            if name == "divide" and len(recipe.args) == 1 and visit(recipe.args[0]) is None:
                return visit(recipe.func.obj)
        return False

    return visit(node.recipe) is True


def _roots(node: RowNode) -> tuple[ExactRef, ...]:
    if isinstance(node, RowField):
        return (node.root,)
    if isinstance(node, MeasureAssertion):
        return _roots(node.operand)
    return tuple(root for item in node.inputs for root in _roots(item))


def build_attribute_rule(
    node: MetricNode,
    declared: Mapping[ExactRef, MetricNode],
    contributions: tuple[ContributionBinding, ...],
    *,
    occurrence: tuple[int, ...],
    by: tuple[ComparisonAxis, ...],
    metric: str,
) -> AttributeRule:
    """Capture a rule for one named output, retaining its exact source-use paths.

    ``occurrence`` is the output's existing observation path, normally ``(i,)``.
    The returned requests require runtime partition, coverage and route checks;
    successful recognition does not certify those checks.
    """
    active: set[ExactRef] = set()
    windows: list[ObservationWindow] = []

    def fail(
        path: tuple[int, ...],
        received: str,
        *,
        reason: AttributeRuleReason = "unsupported_expression",
    ) -> NoReturn:
        raise AttributeRuleError(
            reason=reason,
            metric=metric,
            occurrence=path,
            expected="a closed exact contribution rule with captured component roles",
            received=received,
        )

    def source(
        leaf: SourceAggregate | SourceWeightedMean | EntityCount,
        path: tuple[int, ...],
        roles: tuple[AttributeComponentRole, ...],
        support: AttributeSupport,
    ) -> AttributeSourceRequest:
        matches = tuple(
            item for item in contributions if (item.logical_occurrence or item.occurrence) == path
        )
        if not matches or (not windows and len(matches) != 1):
            fail(path, f"{len(matches)} captured source bindings", reason="missing_component_proof")
        binding = matches[0]
        roots = (
            (leaf.entity,)
            if isinstance(leaf, EntityCount)
            else (
                (*_roots(leaf.value), *_roots(leaf.weight))
                if isinstance(leaf, SourceWeightedMean)
                else _roots(leaf.operand)
            )
        )
        if (
            binding.definition != leaf
            or not roots
            or any(root.ref != binding.root.ref for root in roots)
        ):
            fail(path, "a mismatched captured source leaf/root", reason="missing_component_proof")
        if any(item.sample_axes for item in matches):
            fail(path, "sampled or windowed source input", reason="missing_component_proof")
        if windows:
            if any(
                item.window_key is None
                or item.period is None
                or item.time is None
                or not item.calendars
                or item.definition != leaf
                or item.root.ref != binding.root.ref
                for item in matches
            ) or len({item.window_key for item in matches}) != len(matches):
                fail(path, "incomplete fixed window fibers", reason="missing_component_proof")
            return AttributeSourceRequest(
                binding, roles, support, AttributeWindowProof(windows[-1], matches)
            )
        if binding.window_key is not None or binding.logical_occurrence is not None:
            fail(path, "uncaptured window wrapper", reason="missing_component_proof")
        return AttributeSourceRequest(binding, roles, support)

    def combine(operation: LinearOperation) -> AdditiveRule | LinearRule:
        if isinstance(operation, (RuleAdd, RuleSubtract)):
            if isinstance(operation.left, AdditiveRule) and isinstance(
                operation.right, AdditiveRule
            ):
                if isinstance(operation, RuleAdd):
                    return AdditiveRule(RuleAdd(operation.left, operation.right))
                return AdditiveRule(RuleSubtract(operation.left, operation.right))
        elif isinstance(operation.operand, AdditiveRule):
            if isinstance(operation, RuleNegate):
                return AdditiveRule(RuleNegate(operation.operand))
            if isinstance(operation, RuleDivide):
                return AdditiveRule(RuleDivide(operation.operand, operation.scalar))
            if isinstance(operation, RuleRescale):
                return AdditiveRule(
                    RuleRescale(operation.operand, operation.scalar, operation.unit)
                )
            return AdditiveRule(RuleScale(operation.operand, operation.scalar, operation.side))
        return LinearRule(operation)

    def visit(item: MetricNode, path: tuple[int, ...]) -> AttributeRule:
        if isinstance(item, DeclaredMetricRef):
            if item.ref in active or item.ref not in declared:
                fail(
                    path,
                    "an unresolved or cyclic declared Metric",
                    reason="missing_component_proof",
                )
            active.add(item.ref)
            result = visit(declared[item.ref], (*path, 0))
            active.remove(item.ref)
            return result
        if isinstance(item, ContributionFilter):
            return visit(item.operand, (*path, 0))
        if isinstance(item, MetricWindow):
            if windows:
                fail(path, "nested windows", reason="missing_component_proof")
            windows.append(item.rule)
            result = visit(item.operand, (*path, 0))
            windows.pop()
            if not isinstance(result, AdditiveRule):
                fail(
                    path,
                    "fixed window requires an additive child",
                    reason="missing_component_proof",
                )
            return result
        if isinstance(item, (PerReduce, PerWeightedMean)):
            fail(path, type(item).__name__, reason="missing_component_proof")
        if isinstance(item, EntityCount):
            return AdditiveRule(
                AdditiveSource(source(item, path, ("additive_value",), "rows"), False)
            )
        if isinstance(item, SourceWeightedMean):
            return RatioComponentRule(
                NativeWeightedMean(
                    source(
                        item,
                        path,
                        ("pair_product_sum", "pair_weight_sum", "pair_count"),
                        "co_valid_value_weight",
                    )
                )
            )
        if isinstance(item, SourceAggregate):
            if item.reducer == "mean":
                return RatioComponentRule(
                    NativeMean(
                        source(
                            item, path, ("valid_value_sum", "valid_value_count"), "non_null_value"
                        )
                    )
                )
            if item.reducer not in ("sum", "count", "count_distinct"):
                fail(path, f"source reducer {item.reducer}")
            if item.reducer == "sum" and (
                not by or not all(_row_allows(item.operand, axis) for axis in by)
            ):
                fail(path, "sum permission does not cover every requested classification axis")
            distinct = item.reducer == "count_distinct"
            return AdditiveRule(
                AdditiveSource(
                    source(
                        item,
                        path,
                        ("additive_value",),
                        "identity" if distinct else "non_null_value",
                    ),
                    distinct,
                )
            )
        if not isinstance(item, MetricExpression):
            fail(path, type(item).__name__)
        used: list[int] = []

        def rule(value: _RecipeValue) -> AttributeRule:
            if isinstance(value, FixedScalar):
                fail(path, "a constant offset or constant-only Metric")
            return value

        def ratio(
            left: _RecipeValue,
            right: _RecipeValue,
        ) -> RatioComponentRule:
            if not isinstance(left, AdditiveRule) or not isinstance(right, AdditiveRule):
                fail(path, "a divide without two ordered additive components")
            return RatioComponentRule(DirectRatio(left, right))

        def recipe(value: Resolver) -> _RecipeValue:
            from marivo._definition.deferred import RescaleResolver

            if isinstance(value, RescaleResolver):
                scalar = recipe(Just(value.divisor))
                assert isinstance(scalar, FixedScalar)
                return combine(RuleRescale(rule(recipe(value.recipe)), scalar, value.unit))
            if type(value) is Variable:
                for index, operand in enumerate(item.operands):
                    if value.name == f"slot_{index}":
                        used.append(index)
                        return visit(operand, (*path, index))
                fail(path, f"unknown Metric slot {value.name}")
            if type(value) is Just:
                scalar = value.value
                if (
                    type(scalar) not in (int, float, Decimal)
                    or (isinstance(scalar, float) and not math.isfinite(scalar))
                    or (isinstance(scalar, Decimal) and not scalar.is_finite())
                ):
                    fail(path, "a non-finite or non-numeric fixed scalar")
                assert isinstance(scalar, (int, float, Decimal))
                return FixedScalar(scalar, literal_token(scalar))
            if type(value) is UnaryOperator and value.func is operator.neg:
                return combine(RuleNegate(rule(recipe(value.arg))))
            if type(value) is BinaryOperator:
                left, right = recipe(value.left), recipe(value.right)
                if value.func is operator.add:
                    return combine(RuleAdd(rule(left), rule(right)))
                if value.func is operator.sub:
                    return combine(RuleSubtract(rule(left), rule(right)))
                if value.func is operator.truediv:
                    if isinstance(right, FixedScalar) and not isinstance(left, FixedScalar):
                        if right.value == 0:
                            fail(path, "a zero fixed divisor")
                        return combine(RuleDivide(left, right))
                    return ratio(left, right)
                if value.func is operator.mul:
                    if isinstance(left, FixedScalar) and not isinstance(right, FixedScalar):
                        return combine(RuleScale(right, left, "left"))
                    if isinstance(right, FixedScalar) and not isinstance(left, FixedScalar):
                        return combine(RuleScale(left, right, "right"))
                fail(path, "an unsupported completed-Metric operator")
            fail(path, f"unsupported Metric recipe {type(value).__name__}")

        result = rule(recipe(item.recipe))
        if sorted(used) != list(range(len(item.operands))):
            fail(
                path,
                "Metric slots must each have one captured use",
                reason="missing_component_proof",
            )
        return result

    return visit(node, occurrence)


def component_requests(rule: AttributeRule) -> tuple[AttributeSourceRequest, ...]:
    """Return each source use in exact child order, without deduplicating roots."""
    operation = rule.operation
    if isinstance(operation, AdditiveSource):
        source = operation.source
        return (
            (source,)
            if source.window is None
            else tuple(replace(source, binding=binding) for binding in source.window.bindings)
        )
    if isinstance(operation, (NativeMean, NativeWeightedMean)):
        return (operation.source,)
    if isinstance(operation, DirectRatio):
        return (
            *component_requests(operation.numerator),
            *component_requests(operation.denominator),
        )
    if isinstance(operation, (RuleAdd, RuleSubtract)):
        return (*component_requests(operation.left), *component_requests(operation.right))
    return component_requests(operation.operand)


def _source_signature(request: AttributeSourceRequest) -> tuple[object, ...]:
    binding = request.binding
    node = binding.definition
    time = binding.time
    identity = (
        binding.root.ref.key,
        (time.field.ref.key, tuple(step.ref.key for step in time.path), time.at)
        if time is not None
        else None,
        tuple(
            (
                read.entity.ref.key,
                read.field.ref.key,
                tuple(step.ref.key for step in read.path),
                read.at,
            )
            if isinstance(read, FieldRead)
            else (
                read.entity.ref.key,
                tuple(step.ref.key for step in read.path),
                read.at,
                read.components,
            )
            for read in binding.coordinates
        ),
        # Grain equality compares builtin unit/count or semantic calendar/level.
        # A grid identity also captures its endpoint directory and scope, whose
        # independent validation belongs to that side's parent projection.
        tuple(
            (
                slot,
                axis.grain_spec if isinstance(axis, CalendarCoordinate) else "interval",
                axis.timezone,
            )
            for slot, axis in binding.calendars
        ),
        binding.period.timezone if binding.period is not None else None,
        request.roles,
        request.support,
        None if request.window is None else request.window.rule,
    )
    if isinstance(node, SourceAggregate):
        return (*identity, node.reducer, node.nulls, node.q, node.rule_version)
    if isinstance(node, SourceWeightedMean):
        return (*identity, "weighted_mean", node.nulls, node.rule_version)
    return (*identity, "entity_count")


def _signature(rule: AttributeRule) -> tuple[object, ...]:
    operation = rule.operation
    kind = type(rule).__name__
    if isinstance(operation, AdditiveSource):
        return (kind, _source_signature(operation.source), operation.distinct_partition)
    if isinstance(operation, (NativeMean, NativeWeightedMean)):
        return (kind, type(operation).__name__, _source_signature(operation.source))
    if isinstance(operation, DirectRatio):
        return (
            kind,
            "divide",
            _signature(operation.numerator),
            _signature(operation.denominator),
        )
    if isinstance(operation, (RuleAdd, RuleSubtract)):
        return (
            kind,
            type(operation).__name__,
            _signature(operation.left),
            _signature(operation.right),
        )
    if isinstance(operation, (RuleDivide, RuleRescale)):
        return (
            kind,
            type(operation).__name__,
            operation.scalar.token,
            operation.unit if isinstance(operation, RuleRescale) else None,
            _signature(operation.operand),
        )
    if isinstance(operation, RuleScale):
        return (
            kind,
            "scale",
            operation.side,
            operation.scalar.token,
            _signature(operation.operand),
        )
    return (kind, "negate", _signature(operation.operand))


def rules_match(left: AttributeRule, right: AttributeRule) -> bool:
    """Compare consumed topology and roles, excluding versions and Row definitions."""
    return _signature(left) == _signature(right)


def require_rules_match(left: AttributeRule, right: AttributeRule, *, metric: str) -> None:
    """Reject the first actual rule mismatch with its closed typed witness."""

    def visit(a: AttributeRule, b: AttributeRule, path: tuple[int, ...]) -> None:
        x, y = a.operation, b.operation
        same = type(a) is type(b) and type(x) is type(y)
        if (
            same
            and isinstance(x, (RuleDivide, RuleRescale))
            and isinstance(y, (RuleDivide, RuleRescale))
            and x.scalar.token == y.scalar.token
            and (
                not isinstance(x, RuleRescale) or (isinstance(y, RuleRescale) and x.unit == y.unit)
            )
        ):
            visit(x.operand, y.operand, (*path, 0))
            return
        if (
            same
            and isinstance(x, (RuleAdd, RuleSubtract))
            and isinstance(y, (RuleAdd, RuleSubtract))
        ):
            visit(x.left, y.left, (*path, 0))
            visit(x.right, y.right, (*path, 1))
            return
        if (
            same
            and isinstance(x, (RuleNegate, RuleScale))
            and isinstance(y, (RuleNegate, RuleScale))
        ):
            if isinstance(x, RuleNegate) and isinstance(y, RuleNegate):
                visit(x.operand, y.operand, (*path, 0))
                return
            if (
                isinstance(x, RuleScale)
                and isinstance(y, RuleScale)
                and x.scalar.token == y.scalar.token
                and x.side == y.side
            ):
                visit(x.operand, y.operand, (*path, 0))
                return
        if same and isinstance(x, DirectRatio) and isinstance(y, DirectRatio):
            visit(x.numerator, y.numerator, (*path, 0))
            visit(x.denominator, y.denominator, (*path, 1))
            return
        if _signature(a) == _signature(b):
            return
        mismatch = AttributeRuleMismatch(
            path,
            type(x).__name__,
            type(y).__name__,
            x.scalar if isinstance(x, (RuleScale, RuleDivide, RuleRescale)) else None,
            y.scalar if isinstance(y, (RuleScale, RuleDivide, RuleRescale)) else None,
        )
        raise AttributeRuleError(
            reason="unsupported_expression",
            metric=metric,
            occurrence=path,
            expected="identical ordered contribution operators and typed coefficients",
            received=repr(mismatch),
            mismatch=mismatch,
        )

    visit(left, right, ())
