"""Typed immutable row expressions over the controlled Ibis recipe graph."""

from __future__ import annotations

import operator
from datetime import date, datetime
from inspect import unwrap
from typing import (
    TYPE_CHECKING,
    Literal,
    TypeAlias,
    cast,
    overload,
)

import ibis
from ibis.common.deferred import BinaryOperator, Call, Deferred, Just, Resolver, UnaryOperator
from ibis.common.deferred import Sequence as RecipeSequence

from marivo._definition.population_parameters import has_parameters
from marivo._definition.types import (
    InputContext,
    LiteralValue,
)
from marivo._kernel.paths import _PathValue as Path
from marivo._kernel.temporal import InstantAnchor
from marivo.semantic._deferred_semantics import (
    consume_field,
)
from marivo.semantic.errors import _error, _stage_error
from marivo.telemetry import traced

if TYPE_CHECKING:
    from marivo._kernel.symbolic import PathSymbol
from marivo.semantic._expression_carrier import (
    NumericLiteral,
    RowRole,
    _merge,
    _parameter,
    _RowRoleT,
)
from marivo.semantic._expression_carrier import (
    _ExpressionCarrier as _ExpressionCarrier,
)
from marivo.semantic._metric_expression import (
    MetricExpression as MetricExpression,
)
from marivo.semantic._metric_expression import (
    MetricPredicate as MetricPredicate,
)
from marivo.semantic._metric_expression import (
    NumericMetric as NumericMetric,
)
from marivo.semantic._metric_expression import (
    _MetricSamples as _MetricSamples,
)
from marivo.semantic._row_reductions import _RowReductions


class RowExpression(
    _RowReductions[
        _RowRoleT,
        "RowExpression[Literal['time']]",
        "RowExpression[Literal['boolean']]",
        "RowExpression[Literal['numeric']]",
    ],
):
    """An immutable row expression with one closed semantic role."""

    def __init__(self, recipe: Resolver, context: InputContext, role: _RowRoleT) -> None:
        if role not in ("dimension", "time", "numeric", "boolean"):
            _stage_error("RowExpression requires a closed row role.", received=repr(role))
        super().__init__(recipe, context, role)

    def __add__(
        self: RowExpression[Literal["numeric"]],
        other: RowExpression[Literal["numeric"]] | NumericLiteral,
    ) -> RowExpression[Literal["numeric"]]:
        return RowExpression(
            self._arithmetic_recipe(other, operator.add),
            _merge(self, other),
            "numeric",
        )

    def __radd__(
        self: RowExpression[Literal["numeric"]],
        other: RowExpression[Literal["numeric"]] | NumericLiteral,
    ) -> RowExpression[Literal["numeric"]]:
        return RowExpression(
            self._arithmetic_recipe(other, operator.add, reflected=True),
            _merge(self, other),
            "numeric",
        )

    def __sub__(
        self: RowExpression[Literal["numeric"]],
        other: RowExpression[Literal["numeric"]] | NumericLiteral,
    ) -> RowExpression[Literal["numeric"]]:
        return RowExpression(
            self._arithmetic_recipe(other, operator.sub),
            _merge(self, other),
            "numeric",
        )

    def __rsub__(
        self: RowExpression[Literal["numeric"]],
        other: RowExpression[Literal["numeric"]] | NumericLiteral,
    ) -> RowExpression[Literal["numeric"]]:
        return RowExpression(
            self._arithmetic_recipe(other, operator.sub, reflected=True),
            _merge(self, other),
            "numeric",
        )

    def __mul__(
        self: RowExpression[Literal["numeric"]],
        other: RowExpression[Literal["numeric"]] | NumericLiteral,
    ) -> RowExpression[Literal["numeric"]]:
        return RowExpression(
            self._arithmetic_recipe(other, operator.mul),
            _merge(self, other),
            "numeric",
        )

    def __rmul__(
        self: RowExpression[Literal["numeric"]],
        other: RowExpression[Literal["numeric"]] | NumericLiteral,
    ) -> RowExpression[Literal["numeric"]]:
        return RowExpression(
            self._arithmetic_recipe(other, operator.mul, reflected=True),
            _merge(self, other),
            "numeric",
        )

    def __truediv__(
        self: RowExpression[Literal["numeric"]],
        other: RowExpression[Literal["numeric"]] | NumericLiteral,
    ) -> RowExpression[Literal["numeric"]]:
        return RowExpression(
            self._arithmetic_recipe(other, operator.truediv),
            _merge(self, other),
            "numeric",
        )

    def __rtruediv__(
        self: RowExpression[Literal["numeric"]],
        other: RowExpression[Literal["numeric"]] | NumericLiteral,
    ) -> RowExpression[Literal["numeric"]]:
        return RowExpression(
            self._arithmetic_recipe(other, operator.truediv, reflected=True),
            _merge(self, other),
            "numeric",
        )

    def __mod__(
        self: RowExpression[Literal["numeric"]],
        other: RowExpression[Literal["numeric"]] | NumericLiteral,
    ) -> RowExpression[Literal["numeric"]]:
        return RowExpression(
            self._arithmetic_recipe(other, operator.mod),
            _merge(self, other),
            "numeric",
        )

    def __rmod__(
        self: RowExpression[Literal["numeric"]],
        other: RowExpression[Literal["numeric"]] | NumericLiteral,
    ) -> RowExpression[Literal["numeric"]]:
        return RowExpression(
            self._arithmetic_recipe(other, operator.mod, reflected=True),
            _merge(self, other),
            "numeric",
        )

    def __pow__(
        self: RowExpression[Literal["numeric"]],
        other: RowExpression[Literal["numeric"]] | NumericLiteral,
    ) -> RowExpression[Literal["numeric"]]:
        return RowExpression(
            self._arithmetic_recipe(other, operator.pow),
            _merge(self, other),
            "numeric",
        )

    def __rpow__(
        self: RowExpression[Literal["numeric"]],
        other: RowExpression[Literal["numeric"]] | NumericLiteral,
    ) -> RowExpression[Literal["numeric"]]:
        return RowExpression(
            self._arithmetic_recipe(other, operator.pow, reflected=True),
            _merge(self, other),
            "numeric",
        )

    def __neg__(self: RowExpression[Literal["numeric"]]) -> RowExpression[Literal["numeric"]]:
        self._require("numeric")
        return RowExpression(UnaryOperator(operator.neg, self._recipe), self._context, "numeric")

    def __eq__(self, other: RowExpression[RowRole] | LiteralValue) -> RowPredicate:  # type: ignore[override]  # Python equality constructs a typed predicate, never identity equality.
        if isinstance(other, _ExpressionCarrier) and not isinstance(other, RowExpression):
            _stage_error(
                "Comparison requires expressions from the same stage.",
                received=f"{type(self).__name__}[{self._role}] and {type(other).__name__}[{other._role}]",
            )
        return RowExpression(
            BinaryOperator(operator.eq, self._recipe, _parameter(other)),
            _merge(self, other),
            "boolean",
        )

    def __ne__(self, other: RowExpression[RowRole] | LiteralValue) -> RowPredicate:  # type: ignore[override]  # Python equality constructs a typed predicate, never identity equality.
        if isinstance(other, _ExpressionCarrier) and not isinstance(other, RowExpression):
            _stage_error(
                "Comparison requires expressions from the same stage.",
                received=f"{type(self).__name__}[{self._role}] and {type(other).__name__}[{other._role}]",
            )
        return RowExpression(
            BinaryOperator(operator.ne, self._recipe, _parameter(other)),
            _merge(self, other),
            "boolean",
        )

    def __lt__(self, other: RowExpression[RowRole] | LiteralValue) -> RowPredicate:
        if isinstance(other, _ExpressionCarrier) and not isinstance(other, RowExpression):
            _stage_error(
                "Comparison requires expressions from the same stage.",
                received=f"{type(self).__name__}[{self._role}] and {type(other).__name__}[{other._role}]",
            )
        return RowExpression(
            BinaryOperator(operator.lt, self._recipe, _parameter(other)),
            _merge(self, other),
            "boolean",
        )

    def __le__(self, other: RowExpression[RowRole] | LiteralValue) -> RowPredicate:
        if isinstance(other, _ExpressionCarrier) and not isinstance(other, RowExpression):
            _stage_error(
                "Comparison requires expressions from the same stage.",
                received=f"{type(self).__name__}[{self._role}] and {type(other).__name__}[{other._role}]",
            )
        return RowExpression(
            BinaryOperator(operator.le, self._recipe, _parameter(other)),
            _merge(self, other),
            "boolean",
        )

    def __gt__(self, other: RowExpression[RowRole] | LiteralValue) -> RowPredicate:
        if isinstance(other, _ExpressionCarrier) and not isinstance(other, RowExpression):
            _stage_error(
                "Comparison requires expressions from the same stage.",
                received=f"{type(self).__name__}[{self._role}] and {type(other).__name__}[{other._role}]",
            )
        return RowExpression(
            BinaryOperator(operator.gt, self._recipe, _parameter(other)),
            _merge(self, other),
            "boolean",
        )

    def __ge__(self, other: RowExpression[RowRole] | LiteralValue) -> RowPredicate:
        if isinstance(other, _ExpressionCarrier) and not isinstance(other, RowExpression):
            _stage_error(
                "Comparison requires expressions from the same stage.",
                received=f"{type(self).__name__}[{self._role}] and {type(other).__name__}[{other._role}]",
            )
        return RowExpression(
            BinaryOperator(operator.ge, self._recipe, _parameter(other)),
            _merge(self, other),
            "boolean",
        )

    def __and__(self: RowPredicate, other: RowPredicate | bool) -> RowPredicate:
        self._require_boolean_operand(other)
        return RowExpression(
            BinaryOperator(operator.and_, self._recipe, _parameter(other)),
            _merge(self, other),
            "boolean",
        )

    def __or__(self: RowPredicate, other: RowPredicate | bool) -> RowPredicate:
        self._require_boolean_operand(other)
        return RowExpression(
            BinaryOperator(operator.or_, self._recipe, _parameter(other)),
            _merge(self, other),
            "boolean",
        )

    def __rand__(self: RowPredicate, other: bool) -> RowPredicate:
        self._require_boolean_operand(other)
        return RowExpression(
            BinaryOperator(operator.and_, _parameter(other), self._recipe),
            _merge(self, other),
            "boolean",
        )

    def __ror__(self: RowPredicate, other: bool) -> RowPredicate:
        self._require_boolean_operand(other)
        return RowExpression(
            BinaryOperator(operator.or_, _parameter(other), self._recipe),
            _merge(self, other),
            "boolean",
        )

    def __invert__(self: RowPredicate) -> RowPredicate:
        self._require("boolean")
        return RowExpression(UnaryOperator(operator.invert, self._recipe), self._context, "boolean")

    @traced(surface="semantic", operation="dimension.isnull", kind="method")
    def isnull(self) -> RowPredicate:
        """Capture a boolean test for NULL at the current expression stage.


        Returns:
            A typed expression with the stage and role indicated by this method.

        Examples:
            ``amount.isnull()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return RowExpression(self._method("isnull"), self._context, "boolean")

    @traced(surface="semantic", operation="row.notnull", kind="method")
    def notnull(self) -> RowPredicate:
        """Capture a boolean test for non-NULL values at the current stage.


        Returns:
            A typed expression with the stage and role indicated by this method.

        Examples:
            ``amount.notnull()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return RowExpression(self._method("notnull"), self._context, "boolean")

    @traced(surface="semantic", operation="row.abs", kind="method")
    def abs(self: RowExpression[Literal["numeric"]]) -> RowExpression[Literal["numeric"]]:
        """Capture the absolute value of a numeric expression.


        Returns:
            A typed expression with the stage and role indicated by this method.

        Examples:
            ``amount.abs()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        self._require("numeric")
        return RowExpression(self._method("abs"), self._context, "numeric")

    @traced(surface="semantic", operation="row.sqrt", kind="method")
    def sqrt(self: RowExpression[Literal["numeric"]]) -> RowExpression[Literal["numeric"]]:
        """Capture the square root of a numeric expression.


        Returns:
            A typed expression with the stage and role indicated by this method.

        Examples:
            ``amount.sqrt()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        self._require("numeric")
        return RowExpression(self._method("sqrt"), self._context, "numeric")

    @traced(surface="semantic", operation="row.exp", kind="method")
    def exp(self: RowExpression[Literal["numeric"]]) -> RowExpression[Literal["numeric"]]:
        """Capture the exponential of a numeric expression.


        Returns:
            A typed expression with the stage and role indicated by this method.

        Examples:
            ``amount.exp()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        self._require("numeric")
        return RowExpression(self._method("exp"), self._context, "numeric")

    @traced(surface="semantic", operation="row.round", kind="method")
    def round(
        self: RowExpression[Literal["numeric"]], digits: int = 0
    ) -> RowExpression[Literal["numeric"]]:
        """Capture rounding to the requested number of decimal digits.

        Args:
            digits: The explicit digits captured for this operation.

        Returns:
            A typed expression with the stage and role indicated by this method.

        Examples:
            ``amount.round()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        self._require("numeric")
        return RowExpression(self._method("round", (digits,)), self._context, "numeric")

    @traced(surface="semantic", operation="row.cast", kind="method")
    def cast(
        self: RowExpression[Literal["numeric"]], target_type: str
    ) -> RowExpression[Literal["numeric"]]:
        """Capture an explicit numeric representation conversion.

        Args:
            target_type: The explicit target type captured for this operation.

        Returns:
            A typed expression with the stage and role indicated by this method.

        Examples:
            ``amount.cast("float64")``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        self._require("numeric")
        return RowExpression(self._method("cast", (target_type,)), self._context, "numeric")

    @traced(surface="semantic", operation="row.log", kind="method")
    def log(
        self: RowExpression[Literal["numeric"]], base: NumericLiteral | None = None
    ) -> RowExpression[Literal["numeric"]]:
        """Capture a logarithm with an optional base.

        Args:
            base: The explicit base captured for this operation.

        Returns:
            A typed expression with the stage and role indicated by this method.

        Examples:
            ``amount.log()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        self._require("numeric")
        return RowExpression(self._method("log", (base,)), self._context, "numeric")

    @traced(surface="semantic", operation="row.clip", kind="method")
    def clip(
        self: RowExpression[Literal["numeric"]],
        lower: RowExpression[Literal["numeric"]] | NumericLiteral | None = None,
        upper: RowExpression[Literal["numeric"]] | NumericLiteral | None = None,
    ) -> RowExpression[Literal["numeric"]]:
        """Capture numeric bounds without changing the expression stage.

        Args:
            lower: The explicit lower captured for this operation.
            upper: The explicit upper captured for this operation.

        Returns:
            A typed expression with the stage and role indicated by this method.

        Examples:
            ``amount.clip(lower=0)``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        self._require("numeric")
        return RowExpression(
            self._method(
                "clip",
                (
                    lower,
                    upper,
                ),
            ),
            _merge(self, lower, upper),
            "numeric",
        )

    @overload
    def coalesce(
        self: RowExpression[Literal["numeric"]],
        *others: RowExpression[Literal["numeric"]] | NumericLiteral,
    ) -> RowExpression[Literal["numeric"]]: ...

    @overload
    def coalesce(
        self: RowExpression[Literal["boolean"]], *others: RowPredicate | bool
    ) -> RowExpression[Literal["boolean"]]: ...

    @overload
    def coalesce(
        self: RowExpression[Literal["dimension"]], *others: DimensionExpression | str
    ) -> RowExpression[Literal["dimension"]]: ...

    @overload
    def coalesce(
        self: RowExpression[Literal["time"]], *others: TimeExpression | date | datetime
    ) -> RowExpression[Literal["time"]]: ...

    @traced(surface="semantic", operation="row.coalesce", kind="method")
    def coalesce(self, *others: RowExpression[RowRole] | LiteralValue) -> RowExpression[RowRole]:
        """Capture the first non-NULL value while retaining its semantic role.

        Args:
            others: The explicit others captured for this operation.

        Returns:
            A typed expression with the stage and role indicated by this method.

        Examples:
            ``amount.coalesce(0)``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return RowExpression(
            self._method("coalesce", others), _merge(self, others), cast("RowRole", self._role)
        )

    @traced(surface="semantic", operation="row.least", kind="method")
    def least(
        self: RowExpression[Literal["numeric"]],
        *others: RowExpression[Literal["numeric"]] | NumericLiteral,
    ) -> RowExpression[Literal["numeric"]]:
        """Capture the least of numeric expressions or literals.

        Args:
            others: The explicit others captured for this operation.

        Returns:
            A typed expression with the stage and role indicated by this method.

        Examples:
            ``amount.least()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        self._require("numeric")
        return RowExpression(self._method("least", others), _merge(self, others), "numeric")

    @traced(surface="semantic", operation="row.greatest", kind="method")
    def greatest(
        self: RowExpression[Literal["numeric"]],
        *others: RowExpression[Literal["numeric"]] | NumericLiteral,
    ) -> RowExpression[Literal["numeric"]]:
        """Capture the greatest of numeric expressions or literals.

        Args:
            others: The explicit others captured for this operation.

        Returns:
            A typed expression with the stage and role indicated by this method.

        Examples:
            ``amount.greatest()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        self._require("numeric")
        return RowExpression(self._method("greatest", others), _merge(self, others), "numeric")

    @overload
    def nullif(
        self: RowExpression[Literal["numeric"]],
        other: RowExpression[Literal["numeric"]] | NumericLiteral,
    ) -> RowExpression[Literal["numeric"]]: ...

    @overload
    def nullif(
        self: RowExpression[Literal["boolean"]], other: RowPredicate | bool
    ) -> RowExpression[Literal["boolean"]]: ...

    @overload
    def nullif(
        self: RowExpression[Literal["dimension"]], other: DimensionExpression | str
    ) -> RowExpression[Literal["dimension"]]: ...

    @overload
    def nullif(
        self: RowExpression[Literal["time"]], other: TimeExpression | date | datetime
    ) -> RowExpression[Literal["time"]]: ...

    @traced(surface="semantic", operation="row.nullif", kind="method")
    def nullif(self, other: RowExpression[RowRole] | LiteralValue) -> RowExpression[RowRole]:
        """Capture NULL when this expression equals the supplied value.

        Args:
            other: The explicit other captured for this operation.

        Returns:
            A typed expression with the stage and role indicated by this method.

        Examples:
            ``amount.nullif(0)``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return RowExpression(
            self._method("nullif", (other,)), _merge(self, other), cast("RowRole", self._role)
        )

    @overload
    def cases(  # type: ignore[overload-overlap]  # bool scalar is a Python int subtype; preserve its boolean role.
        self, *branches: tuple[LiteralValue, RowPredicate | bool], else_: RowPredicate | bool
    ) -> RowExpression[Literal["boolean"]]: ...

    @overload
    def cases(
        self,
        *branches: tuple[LiteralValue, RowExpression[Literal["numeric"]] | NumericLiteral],
        else_: RowExpression[Literal["numeric"]] | NumericLiteral,
    ) -> RowExpression[Literal["numeric"]]: ...

    @overload
    def cases(
        self,
        *branches: tuple[LiteralValue, DimensionExpression | str],
        else_: DimensionExpression | str,
    ) -> RowExpression[Literal["dimension"]]: ...

    @overload
    def cases(
        self,
        *branches: tuple[LiteralValue, TimeExpression | date | datetime],
        else_: TimeExpression | date | datetime,
    ) -> RowExpression[Literal["time"]]: ...

    @traced(surface="semantic", operation="row.cases", kind="method")
    def cases(
        self,
        *branches: tuple[LiteralValue, RowExpression[RowRole] | LiteralValue],
        else_: RowExpression[RowRole] | LiteralValue,
    ) -> RowExpression[RowRole]:
        """Capture ordered match/result branches with an explicit fallback.

        Args:
            branches: The explicit branches captured for this operation.
            else_: The explicit else  captured for this operation.

        Returns:
            A typed expression with the stage and role indicated by this method.

        Examples:
            ``status.cases(("paid", 1), else_=0)``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        values = (*tuple(value for _, value in branches), else_)
        roles = tuple(value._role for value in values if isinstance(value, _ExpressionCarrier))
        if len(set(roles)) > 1:
            _error("Case results require the same semantic role.")
        result_role = (
            roles[0]
            if roles
            else "boolean"
            if all(isinstance(value, bool) for value in values)
            else "dimension"
            if all(isinstance(value, str) for value in values)
            else "numeric"
        )

        return RowExpression(
            Call(
                Just(unwrap(ibis.cases)),
                *(
                    RecipeSequence(
                        (
                            BinaryOperator(operator.eq, self._recipe, _parameter(match)),
                            _parameter(value),
                        )
                    )
                    for match, value in branches
                ),
                else_=_parameter(else_),
            ),
            _merge(self, branches, else_),
            cast("RowRole", result_role),
        )

    @overload
    def ifelse(  # type: ignore[overload-overlap]  # bool is an int subclass; boolean branches retain their role.
        self: RowPredicate, true: RowPredicate | bool, false: RowPredicate | bool
    ) -> RowExpression[Literal["boolean"]]: ...

    @overload
    def ifelse(
        self: RowPredicate,
        true: RowExpression[Literal["numeric"]] | NumericLiteral,
        false: RowExpression[Literal["numeric"]] | NumericLiteral,
    ) -> RowExpression[Literal["numeric"]]: ...

    @overload
    def ifelse(
        self: RowPredicate, true: DimensionExpression | str, false: DimensionExpression | str
    ) -> RowExpression[Literal["dimension"]]: ...

    @overload
    def ifelse(
        self: RowPredicate,
        true: TimeExpression | date | datetime,
        false: TimeExpression | date | datetime,
    ) -> RowExpression[Literal["time"]]: ...

    @traced(surface="semantic", operation="row.ifelse", kind="method")
    def ifelse(
        self: RowPredicate,
        true: RowExpression[RowRole] | LiteralValue,
        false: RowExpression[RowRole] | LiteralValue,
    ) -> RowExpression[RowRole]:
        """Capture same-stage branches selected by this boolean condition.

        Args:
            true: The explicit true captured for this operation.
            false: The explicit false captured for this operation.

        Returns:
            A typed expression with the stage and role indicated by this method.

        Examples:
            ``(amount > 0).ifelse(amount, 0)``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        self._require("boolean")
        roles = tuple(
            value._role for value in (true, false) if isinstance(value, _ExpressionCarrier)
        )
        if len(set(roles)) > 1:
            _error("Conditional branches require the same semantic role.")
        branch_role = (
            roles[0]
            if roles
            else "boolean"
            if isinstance(true, bool) and isinstance(false, bool)
            else "dimension"
            if isinstance(true, str) and isinstance(false, str)
            else "numeric"
        )
        return RowExpression(
            self._method("ifelse", (true, false)),
            _merge(self, true, false),
            cast("RowRole", branch_role),
        )

    @traced(surface="semantic", operation="field.isin", kind="method")
    def isin(self, values: list[LiteralValue] | tuple[LiteralValue, ...]) -> RowPredicate:
        """Capture a row membership condition over a frozen literal collection.

        Args:
            values: The explicit values captured for this operation.

        Returns:
            A typed expression with the stage and role indicated by this method.

        Examples:
            ``status.isin(["paid", "shipped"])``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        if type(values) not in (list, tuple):
            _error("isin requires an immutable literal tuple or a list captured at this call.")
        return RowExpression(self._method("isin", (tuple(values),)), self._context, "boolean")

    @traced(surface="semantic", operation="field.via", kind="method")
    def via(self, path: Path | PathSymbol) -> RowExpression[_RowRoleT]:
        """Capture a field through one explicit directed relationship path.

        Args:
            path: The explicit path captured for this operation.

        Returns:
            A typed expression with the stage and role indicated by this method.

        Examples:
            ``region.via(mv.path(buyer))``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """

        if not isinstance(path, (Path, Deferred)):
            _error("via requires a Path from mv.path(...).")
        return self._bind_field_version("via", path)

    def _bind_field_version(
        self, operation: Literal["via", "at"], value: object
    ) -> RowExpression[_RowRoleT]:

        recipe = self._method(operation, (value,))
        result = RowExpression(recipe, _merge(self, value), cast("_RowRoleT", self._role))
        if not has_parameters(recipe):
            consume_field(result)
        return result

    @traced(surface="semantic", operation="field.at", kind="method")
    def at(self, anchor: InstantAnchor | TimeExpression) -> RowExpression[_RowRoleT]:
        """Capture a versioned attribute at a fixed instant or each fact's time.

        Args:
            anchor: One InstantAnchor, or a named, unanchored TimeDimension on
                the observation's unversioned or snapshot fact root for per-row
                classification, including a snapshot partition TimeDimension.

        Returns:
            A typed expression with the stage and role indicated by this method.

        Examples:
            ``region.via(mv.path(buyer)).at(order_time)``.

        Notes:
            Constraints: A fixed instant applies one attribute definition to the
            whole question. Row time supports same-source, single-root observe
            classification on qualified backends; validity fact roots, read,
            classify, windows and internal samples require fixed anchors.
            Captures without source access.
        """
        return self._bind_field_version("at", anchor)


DimensionExpression: TypeAlias = RowExpression[Literal["dimension"]]

TimeExpression: TypeAlias = RowExpression[Literal["time"]]

NumericRow: TypeAlias = RowExpression[Literal["numeric"]]

RowPredicate: TypeAlias = RowExpression[Literal["boolean"]]

FieldExpression: TypeAlias = DimensionExpression | TimeExpression | NumericRow
