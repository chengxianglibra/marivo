"""Replacement formula identity uses current contracts without version pinning."""

import operator
from dataclasses import replace

import pytest
from ibis.common.deferred import BinaryOperator, Just, Variable

from marivo._definition.expressions import (
    DeclaredMetricRef,
    MetricExpression,
    RowField,
    SourceAggregate,
    TimeBinding,
)
from marivo._definition.types import AxisBinding, ExactRef, SumAll
from marivo.analysis.binding.attribute_game import (
    AttributeGame,
    build_game,
    combine_games,
    formula_id,
    require_games_match,
)
from marivo.analysis.core.attribute_game_errors import AttributeGameError
from marivo.refs import ref

ROOT = ExactRef(ref.entity("sales.facts"), "v1")
FIELD = RowField(ExactRef(ref.measure("sales.facts.value"), "v1"), ROOT, "1", SumAll())
BY = (AxisBinding(ROOT, ExactRef(ref.dimension("sales.facts.group"), "v1"), (), "field"),)


def test_formula_identity_ignores_definition_versions_and_declared_aliases() -> None:
    first = SourceAggregate(FIELD, "quantile", None, q=0.5)
    changed = replace(
        first,
        operand=replace(
            FIELD, ref=replace(FIELD.ref, version="v2"), root=replace(ROOT, version="v2")
        ),
    )
    a, b = ExactRef(ref.metric("sales.old"), "v1"), ExactRef(ref.metric("sales.new"), "v9")
    left = build_game(DeclaredMetricRef(a), {a: first}, metric="left", by=BY)
    right = build_game(DeclaredMetricRef(b), {b: changed}, metric="right", by=BY)
    require_games_match(left, right, metric="change")
    assert formula_id(left) == formula_id(right)


@pytest.mark.parametrize(
    "changed",
    [
        replace(SourceAggregate(FIELD, "quantile", None, q=0.5), q=0.9),
        SourceAggregate(FIELD, "median", None),
        SourceAggregate(FIELD, "quantile", None, q=0.5, nulls="error"),
        SourceAggregate(replace(FIELD, unit="USD"), "quantile", None, q=0.5),
        SourceAggregate(
            FIELD,
            "quantile",
            TimeBinding(ExactRef(ref.time_dimension("sales.facts.other_time"), "v1")),
            q=0.5,
        ),
    ],
)
def test_formula_identity_preserves_quantile_null_unit_and_time_roles(changed) -> None:
    original = build_game(SourceAggregate(FIELD, "quantile", None, q=0.5), {}, metric="q", by=BY)
    other = build_game(changed, {}, metric="q", by=BY)
    with pytest.raises(AttributeGameError) as error:
        require_games_match(original, other, metric="change")
    assert error.value.reason == "formula_mismatch"


def test_constant_recipe_changes_are_not_treated_as_data_changes() -> None:
    total = SourceAggregate(FIELD, "sum", None)

    def expression(value):
        return MetricExpression(
            BinaryOperator(operator.mul, Variable("slot_0"), Just(value)), (total,)
        )

    a = build_game(expression(2), {}, metric="a", by=BY)
    b = build_game(expression(3), {}, metric="b", by=BY)
    with pytest.raises(AttributeGameError):
        require_games_match(a, b, metric="change")


def test_same_actual_input_group_deduplicates_states_without_merging_occurrences() -> None:
    total = SourceAggregate(FIELD, "sum", None)
    node = MetricExpression(
        BinaryOperator(operator.mul, Variable("slot_0"), Variable("slot_1")), (total, total)
    )
    built = build_game(node, {}, metric="q", by=BY)
    combined = combine_games((built, built), ("a", "b"))
    assert len(combined[0].leaves) == 2
    assert combined[0].leaves[0].slots == combined[0].leaves[1].slots
    assert combined[0].leaves[0].occurrence != combined[0].leaves[1].occurrence
    assert combined[0].quantities == combined[1].quantities


@pytest.mark.parametrize("reducer", ["first", "last"])
def test_unqualified_source_algorithms_do_not_get_a_replacement_route(reducer) -> None:
    node = SourceAggregate(FIELD, reducer, None, q=0.5 if reducer == "approx_quantile" else None)
    with pytest.raises(AttributeGameError) as error:
        build_game(node, {}, metric="q", by=BY)
    assert error.value.reason == "unsupported_state"


@pytest.mark.parametrize("reducer", ["approx_median", "approx_quantile"])
def test_original_approximate_quantile_is_captured_without_claiming_a_state(reducer) -> None:
    node = SourceAggregate(FIELD, reducer, None, q=0.5 if reducer == "approx_quantile" else None)
    game = build_game(node, {}, metric="q", by=BY)
    assert game.node is node
    assert game.leaves[0].source is node
    assert game.leaves[0].state_eligible is False


def test_unsupported_numeric_power_is_rejected_during_game_binding() -> None:
    total = SourceAggregate(FIELD, "sum", None)
    power = MetricExpression(BinaryOperator(operator.pow, Variable("slot_0"), Just(2)), (total,))
    with pytest.raises(AttributeGameError) as error:
        build_game(power, {}, metric="power", by=BY)
    assert error.value.reason == "unsupported_state"


def test_formula_identity_keeps_inline_row_operations_and_ignores_only_versions() -> None:
    from marivo._definition.expressions import RowExpression

    def scaled(factor: int, version: str = "v1") -> AttributeGame:
        field = replace(FIELD, ref=replace(FIELD.ref, version=version))
        operand = RowExpression(
            BinaryOperator(operator.mul, Variable("slot_0"), Just(factor)), (field,)
        )
        return build_game(SourceAggregate(operand, "sum", None), {}, metric="q", by=BY)

    require_games_match(scaled(2), scaled(2, "v2"), metric="change")
    with pytest.raises(AttributeGameError, match="formulas differ"):
        require_games_match(scaled(2), scaled(3), metric="change")
    assert formula_id(scaled(2)) != formula_id(scaled(3))


def test_formula_identity_keeps_metric_filter_predicates() -> None:
    from marivo._definition.expressions import (
        ContributionFilter,
        PredicateExpression,
        RowExpression,
    )

    def filtered(bound: int) -> AttributeGame:
        predicate = PredicateExpression(
            Variable("slot_0"),
            (
                RowExpression(
                    BinaryOperator(operator.gt, Variable("slot_0"), Just(bound)), (FIELD,)
                ),
            ),
        )
        return build_game(
            ContributionFilter(SourceAggregate(FIELD, "sum", None), predicate),
            {},
            metric="q",
            by=BY,
        )

    require_games_match(filtered(0), filtered(0), metric="change")
    with pytest.raises(AttributeGameError, match="formulas differ"):
        require_games_match(filtered(0), filtered(1), metric="change")
    assert formula_id(filtered(0)) != formula_id(filtered(1))


def test_filter_population_keeps_keys_without_pinning_semantic_versions() -> None:
    from marivo._definition.expressions import ContributionFilter, PredicateExpression
    from marivo._definition.relations import LiteralPopulation, PopulationContains

    def filtered(version: str, member: str) -> AttributeGame:
        field = BY[0].field
        assert field is not None
        axis = replace(
            BY[0],
            entity=replace(ROOT, version=version),
            field=replace(field, version=version),
        )
        population = LiteralPopulation((axis,), ((member,),), "coordinates-v1")
        return build_game(
            ContributionFilter(
                SourceAggregate(FIELD, "sum", None),
                PredicateExpression(Variable("slot_0"), (PopulationContains(population),)),
            ),
            {},
            metric="q",
            by=BY,
        )

    require_games_match(filtered("v1", "A"), filtered("v2", "A"), metric="change")
    with pytest.raises(AttributeGameError, match="formulas differ"):
        require_games_match(filtered("v1", "A"), filtered("v1", "B"), metric="change")


def test_original_approximate_distinct_never_claims_a_mergeable_count_state() -> None:
    node = SourceAggregate(FIELD, "approx_nunique", None)
    game = build_game(node, {}, metric="distinct", by=BY)
    assert game.node is node and game.leaves[0].source is node
    assert game.leaves[0].kind == "distinct"
    assert game.leaves[0].state_eligible is False
    assert game.leaves[0].q is None
    exact = build_game(replace(node, reducer="count_distinct"), {}, metric="distinct", by=BY)
    assert exact.leaves[0].state_eligible is True
    assert formula_id(exact) != formula_id(game)
    with pytest.raises(AttributeGameError, match="formulas differ"):
        require_games_match(game, exact, metric="distinct")
