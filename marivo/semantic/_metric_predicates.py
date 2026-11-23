"""Typed comparisons and Boolean composition for Metric expressions."""

from __future__ import annotations

import operator
from typing import Generic, Literal, TypeVar

from ibis.common.deferred import BinaryOperator, UnaryOperator
from typing_extensions import override

from marivo.semantic._comparison_operators import _ComparisonOperators
from marivo.semantic._expression_carrier import (
    _ExpressionCarrier,
    _merge,
    _MetricRoleT,
    _parameter,
    expression_stage,
)

_MetricT = TypeVar("_MetricT")
_PredicateT = TypeVar("_PredicateT")


class _MetricPredicates(
    _ComparisonOperators[_MetricT, _PredicateT], Generic[_MetricRoleT, _MetricT, _PredicateT]
):
    """Compose predicates with the owning Metric expression's constructor."""

    @override
    def _comparison_accepts(self, other: _ExpressionCarrier) -> bool:
        return expression_stage(other) == "metric"

    def __and__(
        self: _MetricPredicates[Literal["boolean"], _MetricT, _PredicateT],
        other: _PredicateT | bool,
    ) -> _PredicateT:
        self._require_boolean_operand(other)
        return self._new_predicate(
            BinaryOperator(operator.and_, self._recipe, _parameter(other)),
            _merge(self, other),
        )

    def __or__(
        self: _MetricPredicates[Literal["boolean"], _MetricT, _PredicateT],
        other: _PredicateT | bool,
    ) -> _PredicateT:
        self._require_boolean_operand(other)
        return self._new_predicate(
            BinaryOperator(operator.or_, self._recipe, _parameter(other)),
            _merge(self, other),
        )

    def __rand__(
        self: _MetricPredicates[Literal["boolean"], _MetricT, _PredicateT], other: bool
    ) -> _PredicateT:
        self._require_boolean_operand(other)
        return self._new_predicate(
            BinaryOperator(operator.and_, _parameter(other), self._recipe),
            _merge(self, other),
        )

    def __ror__(
        self: _MetricPredicates[Literal["boolean"], _MetricT, _PredicateT], other: bool
    ) -> _PredicateT:
        self._require_boolean_operand(other)
        return self._new_predicate(
            BinaryOperator(operator.or_, _parameter(other), self._recipe),
            _merge(self, other),
        )

    def __invert__(
        self: _MetricPredicates[Literal["boolean"], _MetricT, _PredicateT],
    ) -> _PredicateT:
        self._require("boolean")
        return self._new_predicate(UnaryOperator(operator.invert, self._recipe), self._context)
