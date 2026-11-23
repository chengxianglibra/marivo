"""Typed comparisons and Boolean composition for Metric expressions."""

from __future__ import annotations

import operator
from typing import Generic, Literal, TypeVar

from ibis.common.deferred import BinaryOperator, Resolver, UnaryOperator

from marivo._definition.types import InputContext, LiteralValue
from marivo.semantic._expression_carrier import _ExpressionCarrier, _merge, _MetricRoleT, _parameter
from marivo.semantic.errors import _stage_error

_MetricT = TypeVar("_MetricT")
_PredicateT = TypeVar("_PredicateT")


class _MetricPredicates(_ExpressionCarrier, Generic[_MetricRoleT, _MetricT, _PredicateT]):
    """Compose predicates with the owning Metric expression's constructor."""

    def _new_predicate(self, recipe: Resolver, context: InputContext) -> _PredicateT:
        raise NotImplementedError

    def __eq__(self, other: _MetricT | LiteralValue) -> _PredicateT:  # type: ignore[override]  # Python equality constructs a typed predicate, never identity equality.
        if isinstance(other, _ExpressionCarrier) and other._carrier_stage != "metric":
            _stage_error(
                "Comparison requires expressions from the same stage.",
                received=f"{type(self).__name__}[{self._role}] and {type(other).__name__}[{other._role}]",
            )
        return self._new_predicate(
            BinaryOperator(operator.eq, self._recipe, _parameter(other)),
            _merge(self, other),
        )

    def __ne__(self, other: _MetricT | LiteralValue) -> _PredicateT:  # type: ignore[override]  # Python equality constructs a typed predicate, never identity equality.
        if isinstance(other, _ExpressionCarrier) and other._carrier_stage != "metric":
            _stage_error(
                "Comparison requires expressions from the same stage.",
                received=f"{type(self).__name__}[{self._role}] and {type(other).__name__}[{other._role}]",
            )
        return self._new_predicate(
            BinaryOperator(operator.ne, self._recipe, _parameter(other)),
            _merge(self, other),
        )

    def __lt__(self, other: _MetricT | LiteralValue) -> _PredicateT:
        if isinstance(other, _ExpressionCarrier) and other._carrier_stage != "metric":
            _stage_error(
                "Comparison requires expressions from the same stage.",
                received=f"{type(self).__name__}[{self._role}] and {type(other).__name__}[{other._role}]",
            )
        return self._new_predicate(
            BinaryOperator(operator.lt, self._recipe, _parameter(other)),
            _merge(self, other),
        )

    def __le__(self, other: _MetricT | LiteralValue) -> _PredicateT:
        if isinstance(other, _ExpressionCarrier) and other._carrier_stage != "metric":
            _stage_error(
                "Comparison requires expressions from the same stage.",
                received=f"{type(self).__name__}[{self._role}] and {type(other).__name__}[{other._role}]",
            )
        return self._new_predicate(
            BinaryOperator(operator.le, self._recipe, _parameter(other)),
            _merge(self, other),
        )

    def __gt__(self, other: _MetricT | LiteralValue) -> _PredicateT:
        if isinstance(other, _ExpressionCarrier) and other._carrier_stage != "metric":
            _stage_error(
                "Comparison requires expressions from the same stage.",
                received=f"{type(self).__name__}[{self._role}] and {type(other).__name__}[{other._role}]",
            )
        return self._new_predicate(
            BinaryOperator(operator.gt, self._recipe, _parameter(other)),
            _merge(self, other),
        )

    def __ge__(self, other: _MetricT | LiteralValue) -> _PredicateT:
        if isinstance(other, _ExpressionCarrier) and other._carrier_stage != "metric":
            _stage_error(
                "Comparison requires expressions from the same stage.",
                received=f"{type(self).__name__}[{self._role}] and {type(other).__name__}[{other._role}]",
            )
        return self._new_predicate(
            BinaryOperator(operator.ge, self._recipe, _parameter(other)),
            _merge(self, other),
        )

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
