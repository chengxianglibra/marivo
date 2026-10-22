"""Pure structural recognition keeps contribution roles separate from runtime proof."""

import operator
from dataclasses import replace
from decimal import Decimal

import pytest
from ibis.common.deferred import Attr, BinaryOperator, Call, Just, Resolver, UnaryOperator, Variable

from marivo._definition.expressions import (
    ContributionFilter,
    DeclaredMetricRef,
    EntityCount,
    MeasureAssertion,
    MemberCount,
    MetricExpression,
    MetricNode,
    MetricWindow,
    PerReduce,
    PerWeightedMean,
    RowExpression,
    RowField,
    SourceAggregate,
    SourceWeightedMean,
    TimeBinding,
)
from marivo._definition.graph import children
from marivo._definition.observations import ContributionBinding, ObservationPeriod
from marivo._definition.relations import LiteralPopulation
from marivo._definition.types import (
    Authorization,
    AxisBinding,
    CalendarCoordinate,
    ExactRef,
    FieldRead,
    SumAll,
    SumAllExcept,
    SumAxes,
    SumNone,
    SumPermission,
)
from marivo._definition.windows import AllHistory
from marivo._temporal import Grain, anchor_from_datetime, builtin_grain, semantic_grain
from marivo.analysis.binding.attribute_rules import (
    AdditiveRule,
    AdditiveSource,
    AttributeRule,
    AttributeRuleError,
    DirectRatio,
    LinearRule,
    NativeMean,
    NativeWeightedMean,
    RatioComponentRule,
    RuleScale,
    RuleSubtract,
    build_attribute_rule,
    component_requests,
    rules_match,
)
from marivo.analysis.dsl.temporal import exact_scope
from marivo.refs import ref

ROOT = ExactRef(ref.entity("sales.order"), "v1")
GROUP = ExactRef(ref.dimension("sales.order.channel"), "v1")
TIME = ExactRef(ref.time_dimension("sales.order.created_at"), "v1")
BY = (AxisBinding(ROOT, GROUP, (), "field"),)
ALL_AXES = SumAll()


def field(name: str = "amount", permission: SumPermission = ALL_AXES) -> RowField:
    return RowField(ExactRef(ref.measure(f"sales.order.{name}"), "v1"), ROOT, "USD", permission)


def total(name: str = "amount", permission: SumPermission = ALL_AXES) -> SourceAggregate:
    return SourceAggregate(field(name, permission), "sum", TimeBinding(TIME))


def formula(recipe: Resolver, *operands: MetricNode) -> MetricExpression:
    return MetricExpression(recipe, operands)


def binary(function: object, left: MetricNode, right: MetricNode) -> MetricExpression:
    return formula(BinaryOperator(function, Variable("slot_0"), Variable("slot_1")), left, right)


def bindings(
    node: MetricNode,
    declared: dict[ExactRef, MetricNode] | None = None,
    path: tuple[int, ...] = (0,),
) -> tuple[ContributionBinding, ...]:
    declared = declared or {}
    if isinstance(node, DeclaredMetricRef):
        return bindings(declared[node.ref], declared, (*path, 0))
    if isinstance(node, (SourceAggregate, SourceWeightedMean, EntityCount)):
        return (
            ContributionBinding(
                path,
                node,
                ROOT,
                (),
                None,
                (),
                (FieldRead(GROUP, ROOT),),
                node.time,
                None,
                output_axis_count=1,
            ),
        )
    return tuple(
        binding
        for edge in children(node)
        if edge.role in ("operand", "value", "weight")
        for binding in bindings(edge.child, declared, (*path, edge.index))
    )


def build(
    node: MetricNode,
    declared: dict[ExactRef, MetricNode] | None = None,
    captured: tuple[ContributionBinding, ...] | None = None,
) -> AttributeRule:
    return build_attribute_rule(
        node,
        declared or {},
        bindings(node, declared) if captured is None else captured,
        occurrence=(0,),
        by=BY,
        metric="revenue",
    )


@pytest.mark.parametrize(
    ("node", "support", "distinct"),
    [
        (total(), "non_null_value", False),
        (SourceAggregate(field(), "count", None, nulls="error"), "non_null_value", False),
        (EntityCount(ROOT, None), "rows", False),
        (SourceAggregate(field(), "count_distinct", None), "identity", True),
    ],
)
def test_additive_source_roles_are_explicit(node: MetricNode, support: str, distinct: bool) -> None:
    rule = build(node)
    assert isinstance(rule, AdditiveRule) and isinstance(rule.operation, AdditiveSource)
    assert rule.operation.distinct_partition is distinct
    request = component_requests(rule)[0]
    assert request.roles == ("additive_value",)
    assert request.support == support
    assert request.binding.definition is node


@pytest.mark.parametrize(
    ("permission", "allowed"),
    [
        (SumAll(), True),
        (SumAxes((GROUP,)), True),
        (SumAxes((TIME,)), False),
        (SumAllExcept((TIME,)), True),
        (SumAllExcept((GROUP,)), False),
        (SumNone(), False),
    ],
)
def test_sum_permission_applies_to_actual_classification_axes(
    permission: SumPermission, allowed: bool
) -> None:
    if allowed:
        assert isinstance(build(total(permission=permission)), AdditiveRule)
    else:
        with pytest.raises(AttributeRuleError, match="sum permission") as caught:
            build(total(permission=permission))
        assert caught.value.reason == "unsupported_expression"


def test_row_permission_preserves_source_stage_and_explicit_assertions() -> None:
    multiplied = RowExpression(
        BinaryOperator(operator.mul, Variable("slot_0"), Variable("slot_1")),
        (field(), field("price")),
    )
    invalid_sum = SourceAggregate(multiplied, "sum", None)
    with pytest.raises(AttributeRuleError, match="sum permission"):
        build(invalid_sum)
    asserted = MeasureAssertion(
        multiplied,
        None,
        SumAll(),
        Authorization(ExactRef(ref.measure("sales.order.extended"), "v1")),
    )
    assert isinstance(build(SourceAggregate(asserted, "sum", None)), AdditiveRule)
    scaled = RowExpression(BinaryOperator(operator.mul, Variable("slot_0"), Just(2)), (field(),))
    assert isinstance(build(SourceAggregate(scaled, "sum", None)), AdditiveRule)


def test_mean_and_weighted_mean_require_distinct_common_masks() -> None:
    mean = SourceAggregate(field(), "mean", None, nulls="error")
    weighted = SourceWeightedMean(field(), field("weight"), None, "error", "error")
    mean_rule, weighted_rule = build(mean), build(weighted)
    assert isinstance(mean_rule, RatioComponentRule)
    assert isinstance(mean_rule.operation, NativeMean)
    assert component_requests(mean_rule)[0].roles == ("valid_value_sum", "valid_value_count")
    assert component_requests(mean_rule)[0].support == "non_null_value"
    assert isinstance(weighted_rule, RatioComponentRule)
    assert isinstance(weighted_rule.operation, NativeWeightedMean)
    assert component_requests(weighted_rule)[0].roles == (
        "pair_product_sum",
        "pair_weight_sum",
        "pair_count",
    )
    assert component_requests(weighted_rule)[0].support == "co_valid_value_weight"
    assert component_requests(weighted_rule)[0].binding.definition is weighted
    assert not rules_match(mean_rule, weighted_rule)


def test_additive_priority_and_composite_ratio_keep_ordered_occurrences() -> None:
    numerator = binary(operator.add, total("a"), total("b"))
    denominator = binary(operator.sub, total("c"), total("d"))
    assert isinstance(build(numerator), AdditiveRule)
    rule = build(binary(operator.truediv, numerator, denominator))
    assert isinstance(rule, RatioComponentRule) and isinstance(rule.operation, DirectRatio)
    assert isinstance(rule.operation.numerator, AdditiveRule)
    assert isinstance(rule.operation.denominator, AdditiveRule)
    assert tuple(request.binding.occurrence for request in component_requests(rule)) == (
        (0, 0, 0),
        (0, 0, 1),
        (0, 1, 0),
        (0, 1, 1),
    )
    with pytest.raises(AttributeRuleError, match="two ordered additive"):
        build(binary(operator.truediv, binary(operator.truediv, total(), total()), total()))


def test_linear_over_ratio_and_additive_scaling_have_separate_kinds() -> None:
    ratio = binary(operator.truediv, total(), total("orders"))
    scaled = formula(BinaryOperator(operator.mul, Just(2), Variable("slot_0")), total())
    assert isinstance(build(scaled), AdditiveRule)
    mixed = build(binary(operator.sub, ratio, scaled))
    assert isinstance(mixed, LinearRule) and isinstance(mixed.operation, RuleSubtract)
    negative = build(formula(UnaryOperator(operator.neg, Variable("slot_0")), ratio))
    assert isinstance(negative, LinearRule)


def test_ratio_rejects_retired_divide_recipe() -> None:
    old = formula(
        Call(Attr(Variable("slot_0"), "divide"), Variable("slot_1")), total(), total("orders")
    )
    with pytest.raises(AttributeRuleError, match="unsupported Metric recipe"):
        build(old)


def test_signature_preserves_actual_topology_and_exact_fixed_scalars() -> None:
    def scaled(value: int | float | Decimal, side: str = "left") -> MetricExpression:
        scalar, slot = Just(value), Variable("slot_0")
        return formula(
            BinaryOperator(
                operator.mul, scalar if side == "left" else slot, slot if side == "left" else scalar
            ),
            total("b"),
        )

    original = build(binary(operator.sub, total("a"), scaled(2)))
    assert rules_match(original, build(binary(operator.sub, total("a"), scaled(2))))
    for other in (scaled(3), scaled(2.0), scaled(Decimal("2")), scaled(2, "right")):
        assert not rules_match(original, build(binary(operator.sub, total("a"), other)))
    assert not rules_match(original, build(binary(operator.add, total("a"), scaled(-2))))
    scale_rule = build(scaled(2))
    assert isinstance(scale_rule, AdditiveRule) and isinstance(scale_rule.operation, RuleScale)
    assert scale_rule.operation.scalar.value == 2


def test_signature_preserves_child_order_and_association_without_normalizing() -> None:
    amount = total()
    count = SourceAggregate(field(), "count", None)
    assert not rules_match(
        build(binary(operator.add, amount, count)),
        build(binary(operator.add, count, amount)),
    )
    assert not rules_match(
        build(binary(operator.add, binary(operator.add, amount, count), amount)),
        build(binary(operator.add, amount, binary(operator.add, count, amount))),
    )


def test_signature_does_not_gate_on_definition_versions_or_row_recipe() -> None:
    original = total()
    changed_root, changed_group = replace(ROOT, version="v2"), replace(GROUP, version="v2")
    changed = replace(
        original,
        operand=RowExpression(
            BinaryOperator(operator.mul, Variable("slot_0"), Just(3)),
            (replace(field(), root=changed_root, ref=replace(field().ref, version="v2")),),
        ),
        time=TimeBinding(replace(TIME, version="v2")),
    )
    captured = tuple(
        replace(item, root=changed_root, coordinates=(FieldRead(changed_group, changed_root),))
        for item in bindings(changed)
    )
    assert rules_match(build(original), build(changed, captured=captured))
    different_policy = replace(original, nulls="error")
    assert not rules_match(build(original), build(different_policy))


def test_signature_rejects_changed_root_time_and_classification_roles() -> None:
    node = total()
    original = build(node)
    cases = (
        replace(
            bindings(node)[0],
            time=TimeBinding(ExactRef(ref.time_dimension("sales.order.shipped_at"), "v1")),
        ),
        replace(bindings(node)[0], coordinates=(FieldRead(TIME, ROOT),)),
    )
    for captured in cases:
        assert not rules_match(original, build(node, captured=(captured,)))
    changed_root = ExactRef(ref.entity("sales.return"), "v1")
    changed = replace(node, operand=replace(field(), root=changed_root))
    captured = replace(bindings(changed)[0], root=changed_root)
    assert not rules_match(original, build(changed, captured=(captured,)))


def test_calendar_roles_match_across_periods_without_comparing_endpoint_directories() -> None:
    node = total()

    def captured(start: str, end: str, grain: Grain, zone: str = "UTC") -> ContributionBinding:
        scope = exact_scope(start, end, zone)
        grid = scope.by(grain).grid
        calendar = CalendarCoordinate(
            grid.identity,
            grid.grain_spec,
            grid.boundary_timezone,
            grid.snapshot_digest,
            tuple(
                (
                    anchor_from_datetime(cell.original_start),
                    anchor_from_datetime(cell.original_end),
                    anchor_from_datetime(cell.start),
                    anchor_from_datetime(cell.end),
                )
                for cell in grid.cells
            ),
            grid.calendar_snapshot,
            civil_bounds=grid.civil_bounds,
        )
        return replace(
            bindings(node)[0],
            period=ObservationPeriod(scope.start, scope.end, zone),
            calendars=(("key.0", calendar),),
        )

    july = captured("2026-07-01", "2026-07-03", builtin_grain("day"))
    august = captured("2026-08-01", "2026-08-03", builtin_grain("day"))
    assert july.period != august.period
    assert july.calendars[0][1].identity != august.calendars[0][1].identity
    assert july.calendars[0][1].cells != august.calendars[0][1].cells
    july_rule = build(node, captured=(july,))
    assert rules_match(july_rule, build(node, captured=(august,)))

    for incompatible in (
        captured("2026-08-01", "2026-09-01", builtin_grain("month")),
        captured("2026-08-01", "2026-08-03", builtin_grain("day"), "Asia/Shanghai"),
        replace(august, calendars=(("key.1", august.calendars[0][1]),)),
    ):
        assert not rules_match(july_rule, build(node, captured=(incompatible,)))

    # Role matching deliberately does not replace each endpoint's separate
    # frozen directory/snapshot validation in the parent projection.
    coordinate = august.calendars[0][1]
    fiscal = semantic_grain(calendar=ref.period_calendar("sales.fiscal"), level="month")
    named = replace(coordinate, grain_spec=fiscal, snapshot_digest="captured-left")
    named_left = replace(august, calendars=(("key.0", named),))
    named_right = replace(
        july,
        calendars=(
            (
                "key.0",
                replace(named, identity="another-directory", snapshot_digest="captured-right"),
            ),
        ),
    )
    assert rules_match(build(node, captured=(named_left,)), build(node, captured=(named_right,)))
    for other_grain in (
        builtin_grain("month"),
        semantic_grain(calendar=ref.period_calendar("sales.other"), level="month"),
        semantic_grain(calendar=ref.period_calendar("sales.fiscal"), level="quarter"),
    ):
        other = replace(named_right, calendars=(("key.0", replace(named, grain_spec=other_grain)),))
        assert not rules_match(build(node, captured=(named_left,)), build(node, captured=(other,)))


def test_declared_filtered_and_repeated_uses_have_distinct_paths() -> None:
    declared_ref = ExactRef(ref.metric("sales.revenue"), "v1")
    predicate = RowExpression(Just(True), ())
    node = binary(
        operator.add,
        DeclaredMetricRef(declared_ref),
        ContributionFilter(DeclaredMetricRef(declared_ref), predicate),
    )
    rule = build(node, {declared_ref: total()})
    assert tuple(request.binding.occurrence for request in component_requests(rule)) == (
        (0, 0, 0),
        (0, 1, 0, 0),
    )
    leaf = total()
    repeated = build(binary(operator.add, leaf, leaf))
    assert tuple(request.binding.occurrence for request in component_requests(repeated)) == (
        (0, 0),
        (0, 1),
    )


@pytest.mark.parametrize("failure", ["missing", "duplicate", "root", "definition", "sample"])
def test_missing_or_mismatched_component_binding_fails_closed(failure: str) -> None:
    node = total()
    captured = bindings(node)
    if failure == "missing":
        captured = ()
    elif failure == "duplicate":
        captured = (*captured, *captured)
    elif failure == "root":
        captured = (replace(captured[0], root=ExactRef(ref.entity("sales.return"), "v1")),)
    elif failure == "definition":
        captured = (replace(captured[0], definition=replace(node, reducer="mean")),)
    else:
        captured = (replace(captured[0], sample_axes=BY),)
    with pytest.raises(AttributeRuleError) as caught:
        build(node, captured=captured)
    assert caught.value.reason == "missing_component_proof"
    assert caught.value.metric == "revenue" and caught.value.occurrence == (0,)
    assert caught.value.expected and caught.value.received and caught.value.repair


@pytest.mark.parametrize("reducer", ["min", "max", "quantile", "approx_nunique", "approx_median"])
def test_unsupported_source_reducers_do_not_gain_a_rule(reducer: str) -> None:
    node = replace(total(), reducer=reducer, q=0.95 if reducer == "quantile" else None)
    with pytest.raises(AttributeRuleError) as caught:
        build(node)
    assert caught.value.reason == "unsupported_expression"


@pytest.mark.parametrize("function", [operator.mul, operator.pow, operator.floordiv])
def test_variable_or_nonlinear_metric_operations_reject(function: object) -> None:
    with pytest.raises(AttributeRuleError) as caught:
        build(binary(function, total(), total("price")))
    assert caught.value.reason == "unsupported_expression"


@pytest.mark.parametrize("scalar", [True, float("inf"), Decimal("NaN"), "2"])
def test_only_finite_typed_fixed_numbers_are_scalars(scalar: object) -> None:
    with pytest.raises(AttributeRuleError):
        build(formula(BinaryOperator(operator.mul, Just(scalar), Variable("slot_0")), total()))


def test_offsets_conditionals_and_unmapped_slots_reject_without_execution() -> None:
    with pytest.raises(AttributeRuleError, match="constant offset"):
        build(formula(BinaryOperator(operator.add, Variable("slot_0"), Just(1)), total()))
    with pytest.raises(AttributeRuleError, match="unsupported Metric recipe"):
        build(formula(Call(Attr(Variable("slot_0"), "ifelse"), Just(1), Just(2)), total()))
    with pytest.raises(AttributeRuleError) as caught:
        build(formula(Variable("slot_0"), total(), total()))
    assert caught.value.reason == "missing_component_proof"
    with pytest.raises(AttributeRuleError) as caught:
        build(
            formula(
                BinaryOperator(operator.add, Variable("slot_0"), Variable("slot_0")),
                total(),
            )
        )
    assert caught.value.reason == "missing_component_proof"

    def forbidden() -> None:
        raise AssertionError("Rule inspection executed a captured callback")

    with pytest.raises(AttributeRuleError, match="unsupported Metric recipe"):
        build(formula(Call(Just(forbidden))))


def test_per_and_window_require_exact_proof_and_never_use_outer_reducer() -> None:
    for node in (
        MetricWindow(total(), AllHistory()),
        PerReduce(total(), BY, "sum"),
        PerReduce(SourceAggregate(field(), "mean", None), BY, "sum"),
        PerWeightedMean(total(), total("weight"), BY),
    ):
        with pytest.raises(AttributeRuleError) as caught:
            build(node)
        assert caught.value.reason == "missing_component_proof"
    with pytest.raises(AttributeRuleError) as caught:
        build(MemberCount(LiteralPopulation(BY, (), "test")))
    assert caught.value.reason == "unsupported_expression"


def test_fixed_additive_window_retains_each_expanded_binding_obligation() -> None:
    node = MetricWindow(total(), AllHistory())
    grid = exact_scope("2026-07-01", "2026-07-03", "UTC").by(builtin_grain("day")).grid
    calendar = CalendarCoordinate(
        grid.identity,
        grid.grain_spec,
        grid.boundary_timezone,
        grid.snapshot_digest,
        tuple(
            (
                anchor_from_datetime(c.original_start),
                anchor_from_datetime(c.original_end),
                anchor_from_datetime(c.start),
                anchor_from_datetime(c.end),
            )
            for c in grid.cells
        ),
        grid.calendar_snapshot,
        civil_bounds=grid.civil_bounds,
    )
    leaf = bindings(node)[0]
    captured = tuple(
        replace(
            leaf,
            occurrence=(*leaf.occurrence, i),
            logical_occurrence=leaf.occurrence,
            window_key=i,
            period=ObservationPeriod(None, anchor_from_datetime(cell.end), "UTC"),
            calendars=(("key.0", calendar),),
        )
        for i, cell in enumerate(grid.cells)
    )
    rule = build(node, captured=captured)
    assert isinstance(rule, AdditiveRule) and isinstance(rule.operation, AdditiveSource)
    proof = rule.operation.source.window
    assert proof is not None and proof.rule == AllHistory() and proof.bindings == captured
    requests = component_requests(rule)
    assert tuple(request.binding for request in requests) == captured
    assert all(request.window is proof for request in requests)
    for broken in (
        (replace(captured[0], period=None), captured[1]),
        (captured[0], replace(captured[1], window_key=0)),
    ):
        with pytest.raises(AttributeRuleError) as caught:
            build(node, captured=broken)
        assert caught.value.reason == "missing_component_proof"
