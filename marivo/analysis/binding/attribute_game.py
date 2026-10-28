"""Closed whole-Metric replacement games and grouped sufficient-state requests."""

from __future__ import annotations

import operator
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass, replace
from typing import Literal

from ibis.common.deferred import Attr, BinaryOperator, Call, Just, Resolver, Variable

from marivo._definition.correspondence import ComparisonAxis
from marivo._definition.expressions import (
    ContributionFilter,
    DeclaredMetricRef,
    EntityCount,
    MetricExpression,
    MetricNode,
    PredicateExpression,
    RowExpression,
    RowField,
    RowNode,
    SourceAggregate,
    SourceWeightedMean,
)
from marivo._definition.types import ExactRef
from marivo.analysis.core.attribute_game_errors import AttributeGameError


@dataclass(frozen=True, slots=True)
class GameLeaf:
    occurrence: tuple[int, ...]
    kind: Literal["sum", "count", "mean", "weighted_mean", "min", "max", "quantile", "distinct"]
    slots: tuple[int, ...]
    source: SourceAggregate | SourceWeightedMean | EntityCount
    q: float | None = None
    nulls: Literal["ignore", "error"] = "ignore"


@dataclass(frozen=True, slots=True)
class AttributeGame:
    node: MetricNode
    declared: tuple[tuple[ExactRef, MetricNode], ...]
    leaves: tuple[GameLeaf, ...]
    quantities: tuple[tuple[str, MetricNode], ...]
    target_slot: int = 0
    metric: str = "target"


def _rows(node: RowNode) -> tuple[RowField, ...]:
    from marivo._definition.expressions import MeasureAssertion, RowExpression, RowField

    if isinstance(node, RowField):
        return (node,)
    if isinstance(node, MeasureAssertion):
        return _rows(node.operand)
    assert isinstance(node, RowExpression)
    return tuple(field for item in node.inputs for field in _rows(item))


def build_game(
    node: MetricNode,
    declared: Mapping[ExactRef, MetricNode],
    *,
    metric: str,
    by: tuple[ComparisonAxis, ...] = (),
) -> AttributeGame:
    """Bind a state route without reading business data or changing the formula."""
    leaves: list[GameLeaf] = []
    quantities: list[tuple[str, MetricNode]] = [("target", node)]

    def unsupported(current: MetricNode, occurrence: tuple[int, ...]) -> None:
        raise AttributeGameError(
            reason="unsupported_state",
            metric=metric,
            occurrence=occurrence,
            received=f"{type(current).__name__}: no qualified replacement-game state route",
        )

    def visit(
        current: MetricNode, occurrence: tuple[int, ...], filters: tuple[ContributionFilter, ...]
    ) -> None:
        if isinstance(current, DeclaredMetricRef):
            visit(declared[current.ref], (*occurrence, 0), filters)
            return
        if isinstance(current, ContributionFilter):
            visit(current.operand, (*occurrence, 0), (*filters, current))
            return
        if isinstance(current, MetricExpression):
            for index, operand in enumerate(current.operands):
                visit(operand, (*occurrence, index), filters)
            return
        states: tuple[MetricNode, ...]
        kind: Literal["sum", "count", "mean", "weighted_mean", "min", "max", "quantile", "distinct"]
        if isinstance(current, EntityCount):
            kind, states = "count", (current, current)
        elif isinstance(current, SourceAggregate) and current.reducer in (
            "sum",
            "count",
            "mean",
            "min",
            "max",
        ):
            if current.reducer == "sum":
                from marivo.analysis.binding.attribute_rules import _row_allows

                if not by or not all(_row_allows(current.operand, axis) for axis in by):
                    unsupported(current, occurrence)
            kind = current.reducer
            if current.reducer == "count":
                states = (
                    replace(current, nulls="ignore"),
                    EntityCount(_rows(current.operand)[0].root, current.time),
                )
            else:
                states = (
                    replace(
                        current,
                        reducer="sum" if current.reducer == "mean" else current.reducer,
                        nulls="ignore",
                    ),
                    replace(current, reducer="count", nulls="ignore"),
                    EntityCount(next(item.root for item in _rows(current.operand)), current.time),
                )
        elif isinstance(current, SourceAggregate) and current.reducer in (
            "median",
            "quantile",
            "count_distinct",
        ):
            kind, states = (
                ("distinct" if current.reducer == "count_distinct" else "quantile"),
                (replace(current, nulls="ignore"),),
            )
        elif isinstance(current, SourceWeightedMean):
            kind = "weighted_mean"
            from marivo._definition.expressions import MeasureAssertion
            from marivo.semantic._deferred_semantics import _rename_slots

            inputs: list[RowField | MeasureAssertion] = []

            def row_recipe(row: RowNode) -> Resolver:
                if isinstance(row, RowExpression):
                    recipe = _rename_slots(row.recipe, len(inputs))
                    inputs.extend(row.inputs)
                    return recipe
                recipe = Variable(f"slot_{len(inputs)}")
                inputs.append(row)
                return recipe

            value_recipe, weight_recipe = row_recipe(current.value), row_recipe(current.weight)
            product = RowExpression(
                BinaryOperator(operator.mul, value_recipe, weight_recipe), tuple(inputs)
            )
            valid_weight = RowExpression(
                Call(
                    Attr(Call(Attr(value_recipe, "notnull")), "ifelse"), weight_recipe, Just(None)
                ),
                tuple(inputs),
            )
            states = (
                SourceAggregate(product, "sum", current.time, nulls="ignore"),
                SourceAggregate(valid_weight, "sum", current.time, nulls="ignore"),
                SourceAggregate(product, "count", current.time),
                EntityCount(_rows(current.value)[0].root, current.time),
            )
        else:
            unsupported(current, occurrence)
            return
        slots = []
        for original_state in states:
            state: MetricNode = original_state
            for condition in reversed(filters):
                state = ContributionFilter(state, condition.predicate)
            slots.append(len(quantities))
            quantities.append((f"state_{len(quantities)}", state))
        leaves.append(
            GameLeaf(
                occurrence,
                kind,
                tuple(slots),
                current,
                current.q
                if isinstance(current, SourceAggregate) and current.q is not None
                else 0.5
                if kind == "quantile"
                else None,
                current.nulls
                if isinstance(current, (SourceAggregate, SourceWeightedMean))
                else "ignore",
            )
        )

    visit(node, (0,), ())
    # Static operation admission uses the owning numeric lowerer. Physical input
    # types are still qualified by the prepared source plan before any data read.
    import ibis

    from marivo.semantic._numeric_lowering import NumericValue, lower_metric_formula
    from marivo.semantic.errors import SemanticExpressionError

    static = ibis.table(
        {f"leaf_{index}": "float64" for index in range(len(leaves))}, name="_attribute_static_types"
    )
    try:
        original = lower_metric_formula(
            node,
            (0,),
            {
                leaf.occurrence: NumericValue(static[f"leaf_{index}"])
                for index, leaf in enumerate(leaves)
            },
            declared,
        )
    except SemanticExpressionError as error:
        raise AttributeGameError(
            reason="unsupported_state",
            metric=metric,
            received=f"{error.expected}; {error.received}",
        ) from error
    if not original.value.type().is_numeric():
        raise AttributeGameError(
            reason="unsupported_state",
            metric=metric,
            received=f"the complete Metric has nonnumeric type {original.value.type()}",
        )
    return AttributeGame(
        node, tuple(declared.items()), tuple(leaves), tuple(quantities), metric=metric
    )


def game_signature(game: AttributeGame) -> tuple[object, ...]:
    """Describe the replacement formula without pinning source definition versions."""
    from marivo._definition.deferred import canonical_recipe

    declared = dict(game.declared)

    def row_signature(row: RowNode) -> tuple[object, ...]:
        from marivo._definition.expressions import MeasureAssertion

        if isinstance(row, RowField):
            read = row.read
            return (
                "field",
                row.root.ref.key,
                row.ref.ref.key,
                row.unit,
                None
                if read is None
                else (
                    read.entity.ref.key,
                    read.field.ref.key,
                    tuple(step.ref.key for step in read.path),
                    read.at,
                ),
            )
        if isinstance(row, MeasureAssertion):
            return (
                "assertion",
                row.unit,
                input_signature(row.permission),
                row_signature(row.operand),
            )
        return (
            "row",
            canonical_recipe(row.recipe, slots=tuple(f"slot_{i}" for i in range(len(row.inputs)))),
            tuple(row_signature(item) for item in row.inputs),
        )

    def predicate_signature(predicate: PredicateExpression) -> tuple[object, ...]:
        return (
            canonical_recipe(
                predicate.recipe, slots=tuple(f"slot_{i}" for i in range(len(predicate.inputs)))
            ),
            tuple(
                row_signature(item)
                if isinstance(item, RowExpression)
                else ("population", input_signature(item.population))
                for item in predicate.inputs
            ),
        )

    def input_signature(value: object) -> object:
        # Closed predicate inputs retain their full captured structure. ExactRef
        # versions describe provenance; a version alone is not formula identity.
        if isinstance(value, ExactRef):
            return value.ref.key
        if isinstance(value, RowExpression):
            return row_signature(value)
        if isinstance(value, PredicateExpression):
            return predicate_signature(value)
        if isinstance(value, MetricExpression):
            return signature(value)
        if isinstance(value, tuple):
            return tuple(input_signature(item) for item in value)
        if is_dataclass(value) and not isinstance(value, type):
            return (
                type(value).__name__,
                tuple(
                    (field.name, input_signature(getattr(value, field.name)))
                    for field in fields(value)
                    if field.name != "origins"
                ),
            )
        return value

    def signature(node: MetricNode) -> tuple[object, ...]:
        if isinstance(node, DeclaredMetricRef):
            return signature(declared[node.ref])
        if isinstance(node, ContributionFilter):
            return ("filtered_input", predicate_signature(node.predicate), signature(node.operand))
        if isinstance(node, MetricExpression):
            return (
                "formula",
                canonical_recipe(
                    node.recipe, slots=tuple(f"slot_{i}" for i in range(len(node.operands)))
                ),
                tuple(signature(child) for child in node.operands),
            )
        time = (
            node.time
            if isinstance(node, (SourceAggregate, SourceWeightedMean, EntityCount))
            else None
        )
        time_signature = (
            None
            if time is None
            else (time.field.ref.key, tuple(ref.ref.key for ref in time.path), time.at)
        )
        if isinstance(node, EntityCount):
            return ("count", node.entity.ref.key, time_signature)
        from marivo.analysis.binding.attribute_units import row_unit

        units = (
            (row_unit(node.operand),)
            if isinstance(node, SourceAggregate)
            else (row_unit(node.value), row_unit(node.weight))
            if isinstance(node, SourceWeightedMean)
            else ()
        )
        inputs = (
            (row_signature(node.operand),)
            if isinstance(node, SourceAggregate)
            else (row_signature(node.value), row_signature(node.weight))
            if isinstance(node, SourceWeightedMean)
            else (),
            units,
        )
        if isinstance(node, SourceAggregate):
            return (
                "aggregate",
                inputs,
                node.reducer,
                node.q,
                node.nulls,
                node.rule_version,
                time_signature,
            )
        if isinstance(node, SourceWeightedMean):
            return ("weighted_mean", inputs, node.nulls, node.rule_version, time_signature)
        raise AssertionError("unqualified game leaf")

    return signature(game.node)


def require_games_match(left: AttributeGame, right: AttributeGame, *, metric: str) -> None:
    if game_signature(left) != game_signature(right):
        raise AttributeGameError(
            reason="formula_mismatch",
            metric=metric,
            received="the two endpoint replacement formulas differ",
        )


def formula_id(game: AttributeGame) -> str:
    import hashlib

    return hashlib.sha256(repr(game_signature(game)).encode()).hexdigest()


def combine_games(
    games: tuple[AttributeGame, ...], names: tuple[str, ...]
) -> tuple[AttributeGame, ...]:
    """Share one source capture only among the same retained source-group identity."""
    quantities: list[tuple[str, MetricNode]] = [
        (name, game.node) for name, game in zip(names, games, strict=True)
    ]
    remapped: list[AttributeGame] = []
    for target_slot, game in enumerate(games):
        mapping = {}
        for slot, (_, node) in enumerate(game.quantities[1:], start=1):
            existing = next(
                (index for index, (_, captured) in enumerate(quantities) if captured == node), None
            )
            if existing is None:
                existing = len(quantities)
                quantities.append((f"__attribute_state_{existing}", node))
            mapping[slot] = existing
        leaves = tuple(
            replace(leaf, slots=tuple(mapping[slot] for slot in leaf.slots)) for leaf in game.leaves
        )
        remapped.append(replace(game, leaves=leaves, target_slot=target_slot))
    return tuple(replace(game, quantities=tuple(quantities)) for game in remapped)


def state_unit(node: MetricNode) -> str | None:
    from marivo.analysis.binding.attribute_units import component_unit

    while isinstance(node, ContributionFilter):
        node = node.operand
    assert isinstance(node, (SourceAggregate, SourceWeightedMean, EntityCount))
    return component_unit(node, "value")
