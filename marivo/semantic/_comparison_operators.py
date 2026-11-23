"""Comparison operators shared by Row and Metric expressions."""

from __future__ import annotations

import operator
from typing import Generic, Literal, TypeAlias, TypeVar

from ibis.common.deferred import BinaryOperator, Resolver
from typing_extensions import override

from marivo._definition.types import InputContext, LiteralValue
from marivo.semantic._expression_carrier import (
    _ExpressionCarrier,
    _merge,
    _parameter,
    expression_role,
)
from marivo.semantic.errors import _stage_error

_OperandT = TypeVar("_OperandT")
_PredicateT = TypeVar("_PredicateT")


_Comparison: TypeAlias = Literal["eq", "ne", "lt", "le", "gt", "ge"]
_OPERATORS = {
    "eq": operator.eq,
    "ne": operator.ne,
    "lt": operator.lt,
    "le": operator.le,
    "gt": operator.gt,
    "ge": operator.ge,
}


class _ComparisonOperators(_ExpressionCarrier, Generic[_OperandT, _PredicateT]):
    """Resolve equality and ordering into the receiving expression's predicate type."""

    def _new_predicate(self, recipe: Resolver, context: InputContext) -> _PredicateT:
        raise NotImplementedError

    def _comparison_accepts(self, other: _ExpressionCarrier) -> bool:
        raise NotImplementedError

    def _comparison(self, other: _OperandT | LiteralValue, operation: _Comparison) -> _PredicateT:
        if isinstance(other, _ExpressionCarrier) and not self._comparison_accepts(other):
            _stage_error(
                "Comparison requires expressions from the same stage.",
                received=f"{type(self).__name__}[{self._role}] and {type(other).__name__}[{expression_role(other)}]",
            )
        return self._new_predicate(
            BinaryOperator(_OPERATORS[operation], self._recipe, _parameter(other)),
            _merge(self, other),
        )

    @override
    def __eq__(self, other: _OperandT | LiteralValue) -> _PredicateT:  # type: ignore[override]  # DSL equality builds a predicate.
        return self._comparison(other, "eq")

    @override
    def __ne__(self, other: _OperandT | LiteralValue) -> _PredicateT:  # type: ignore[override]  # DSL inequality builds a predicate.
        return self._comparison(other, "ne")

    def __lt__(self, other: _OperandT | LiteralValue) -> _PredicateT:
        return self._comparison(other, "lt")

    def __le__(self, other: _OperandT | LiteralValue) -> _PredicateT:
        return self._comparison(other, "le")

    def __gt__(self, other: _OperandT | LiteralValue) -> _PredicateT:
        return self._comparison(other, "gt")

    def __ge__(self, other: _OperandT | LiteralValue) -> _PredicateT:
        return self._comparison(other, "ge")
