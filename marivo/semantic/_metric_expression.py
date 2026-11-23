"""Typed immutable Metric expressions and their intermediate samples."""

from __future__ import annotations

import operator
from inspect import unwrap
from typing import (
    Literal,
    TypeAlias,
    cast,
    overload,
)

import ibis
from ibis.common.deferred import BinaryOperator, Call, Just, Resolver, UnaryOperator
from ibis.common.deferred import Sequence as RecipeSequence

from marivo._definition.deferred_sample_validation import captured_field_kind, captured_scalar_path
from marivo._definition.relations import PopulationParameter
from marivo._definition.types import (
    InputContext,
    LiteralValue,
)
from marivo._kernel.grouping import Grouping
from marivo._kernel.refs import SemanticKind
from marivo._kernel.render import Card
from marivo._kernel.temporal import CalendarAxis, Grain
from marivo.semantic._expression_carrier import (
    _GRAIN_OMITTED,
    MetricRole,
    NumericLiteral,
    Window,
    _ExpressionCarrier,
    _GrainOmitted,
    _merge,
    _MetricRoleT,
    _parameter,
    _RowCarrier,
)
from marivo.semantic._metric_predicates import _MetricPredicates
from marivo.semantic._sample_reductions import _SampleReductions
from marivo.semantic.errors import ErrorKind, SemanticExpressionError, _error, _stage_error
from marivo.telemetry import traced

TimeExpression: TypeAlias = _RowCarrier[Literal["time"]]
RowPredicate: TypeAlias = _RowCarrier[Literal["boolean"]]
FieldExpression: TypeAlias = _RowCarrier[Literal["dimension", "time", "numeric"]]


class _MetricOperators(
    _MetricPredicates[
        _MetricRoleT, "MetricExpression[MetricRole]", "MetricExpression[Literal['boolean']]"
    ],
):
    """Build same-stage Metric arithmetic and predicate recipes."""

    def __add__(
        self: _MetricOperators[Literal["numeric"]],
        other: MetricExpression[Literal["numeric"]] | NumericLiteral,
    ) -> MetricExpression[Literal["numeric"]]:
        return MetricExpression(
            self._arithmetic_recipe(other, operator.add),
            _merge(self, other),
            "numeric",
        )

    def __radd__(
        self: _MetricOperators[Literal["numeric"]],
        other: MetricExpression[Literal["numeric"]] | NumericLiteral,
    ) -> MetricExpression[Literal["numeric"]]:
        return MetricExpression(
            self._arithmetic_recipe(other, operator.add, reflected=True),
            _merge(self, other),
            "numeric",
        )

    def __sub__(
        self: _MetricOperators[Literal["numeric"]],
        other: MetricExpression[Literal["numeric"]] | NumericLiteral,
    ) -> MetricExpression[Literal["numeric"]]:
        return MetricExpression(
            self._arithmetic_recipe(other, operator.sub),
            _merge(self, other),
            "numeric",
        )

    def __rsub__(
        self: _MetricOperators[Literal["numeric"]],
        other: MetricExpression[Literal["numeric"]] | NumericLiteral,
    ) -> MetricExpression[Literal["numeric"]]:
        return MetricExpression(
            self._arithmetic_recipe(other, operator.sub, reflected=True),
            _merge(self, other),
            "numeric",
        )

    def __mul__(
        self: _MetricOperators[Literal["numeric"]],
        other: MetricExpression[Literal["numeric"]] | NumericLiteral,
    ) -> MetricExpression[Literal["numeric"]]:
        return MetricExpression(
            self._arithmetic_recipe(other, operator.mul),
            _merge(self, other),
            "numeric",
        )

    def __rmul__(
        self: _MetricOperators[Literal["numeric"]],
        other: MetricExpression[Literal["numeric"]] | NumericLiteral,
    ) -> MetricExpression[Literal["numeric"]]:
        return MetricExpression(
            self._arithmetic_recipe(other, operator.mul, reflected=True),
            _merge(self, other),
            "numeric",
        )

    def __truediv__(
        self: _MetricOperators[Literal["numeric"]],
        other: MetricExpression[Literal["numeric"]] | NumericLiteral,
    ) -> MetricExpression[Literal["numeric"]]:
        return MetricExpression(
            self._arithmetic_recipe(other, operator.truediv),
            _merge(self, other),
            "numeric",
        )

    def __rtruediv__(
        self: _MetricOperators[Literal["numeric"]],
        other: MetricExpression[Literal["numeric"]] | NumericLiteral,
    ) -> MetricExpression[Literal["numeric"]]:
        return MetricExpression(
            self._arithmetic_recipe(other, operator.truediv, reflected=True),
            _merge(self, other),
            "numeric",
        )

    def __mod__(
        self: _MetricOperators[Literal["numeric"]],
        other: MetricExpression[Literal["numeric"]] | NumericLiteral,
    ) -> MetricExpression[Literal["numeric"]]:
        return MetricExpression(
            self._arithmetic_recipe(other, operator.mod),
            _merge(self, other),
            "numeric",
        )

    def __rmod__(
        self: _MetricOperators[Literal["numeric"]],
        other: MetricExpression[Literal["numeric"]] | NumericLiteral,
    ) -> MetricExpression[Literal["numeric"]]:
        return MetricExpression(
            self._arithmetic_recipe(other, operator.mod, reflected=True),
            _merge(self, other),
            "numeric",
        )

    def __pow__(
        self: _MetricOperators[Literal["numeric"]],
        other: MetricExpression[Literal["numeric"]] | NumericLiteral,
    ) -> MetricExpression[Literal["numeric"]]:
        return MetricExpression(
            self._arithmetic_recipe(other, operator.pow),
            _merge(self, other),
            "numeric",
        )

    def __rpow__(
        self: _MetricOperators[Literal["numeric"]],
        other: MetricExpression[Literal["numeric"]] | NumericLiteral,
    ) -> MetricExpression[Literal["numeric"]]:
        return MetricExpression(
            self._arithmetic_recipe(other, operator.pow, reflected=True),
            _merge(self, other),
            "numeric",
        )

    def __neg__(self: _MetricOperators[Literal["numeric"]]) -> MetricExpression[Literal["numeric"]]:
        self._require("numeric")
        return MetricExpression(UnaryOperator(operator.neg, self._recipe), self._context, "numeric")


class MetricExpression(_MetricOperators[_MetricRoleT]):
    """An immutable metric expression with one closed semantic role."""

    _carrier_stage = "metric"

    def __init__(self, recipe: Resolver, context: InputContext, role: _MetricRoleT) -> None:
        if role not in ("numeric", "boolean"):
            _stage_error("MetricExpression requires a closed metric role.", received=repr(role))
        super().__init__(recipe, context, role)

    def _new_predicate(self, recipe: Resolver, context: InputContext) -> MetricPredicate:
        return MetricExpression(recipe, context, "boolean")

    def _card(self) -> Card:
        return super()._card().field("next", "session.observe({'output': metric})")

    @traced(surface="semantic", operation="metric.isnull", kind="method")
    def isnull(self) -> MetricPredicate:
        """Capture a boolean test for NULL at the current expression stage.


        Returns:
            A typed expression with the stage and role indicated by this method.

        Examples:
            ``amount.isnull()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return MetricExpression(self._method("isnull"), self._context, "boolean")

    @traced(surface="semantic", operation="metric.notnull", kind="method")
    def notnull(self) -> MetricPredicate:
        """Capture a boolean test for non-NULL values at the current stage.


        Returns:
            A typed expression with the stage and role indicated by this method.

        Examples:
            ``amount.notnull()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return MetricExpression(self._method("notnull"), self._context, "boolean")

    @traced(surface="semantic", operation="metric.abs", kind="method")
    def abs(self: MetricExpression[Literal["numeric"]]) -> MetricExpression[Literal["numeric"]]:
        """Capture the absolute value of a numeric expression.


        Returns:
            A typed expression with the stage and role indicated by this method.

        Examples:
            ``revenue.abs()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        self._require("numeric")
        return MetricExpression(self._method("abs"), self._context, "numeric")

    @traced(surface="semantic", operation="metric.sqrt", kind="method")
    def sqrt(self: MetricExpression[Literal["numeric"]]) -> MetricExpression[Literal["numeric"]]:
        """Capture the square root of a numeric expression.


        Returns:
            A typed expression with the stage and role indicated by this method.

        Examples:
            ``revenue.sqrt()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        self._require("numeric")
        return MetricExpression(self._method("sqrt"), self._context, "numeric")

    @traced(surface="semantic", operation="metric.exp", kind="method")
    def exp(self: MetricExpression[Literal["numeric"]]) -> MetricExpression[Literal["numeric"]]:
        """Capture the exponential of a numeric expression.


        Returns:
            A typed expression with the stage and role indicated by this method.

        Examples:
            ``revenue.exp()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        self._require("numeric")
        return MetricExpression(self._method("exp"), self._context, "numeric")

    @traced(surface="semantic", operation="metric.round", kind="method")
    def round(
        self: MetricExpression[Literal["numeric"]], digits: int = 0
    ) -> MetricExpression[Literal["numeric"]]:
        """Capture rounding to the requested number of decimal digits.

        Args:
            digits: The explicit digits captured for this operation.

        Returns:
            A typed expression with the stage and role indicated by this method.

        Examples:
            ``revenue.round()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        self._require("numeric")
        return MetricExpression(self._method("round", (digits,)), self._context, "numeric")

    @traced(surface="semantic", operation="metric.cast", kind="method")
    def cast(
        self: MetricExpression[Literal["numeric"]], target_type: str
    ) -> MetricExpression[Literal["numeric"]]:
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
        return MetricExpression(self._method("cast", (target_type,)), self._context, "numeric")

    @traced(surface="semantic", operation="metric.log", kind="method")
    def log(
        self: MetricExpression[Literal["numeric"]], base: NumericLiteral | None = None
    ) -> MetricExpression[Literal["numeric"]]:
        """Capture a logarithm with an optional base.

        Args:
            base: The explicit base captured for this operation.

        Returns:
            A typed expression with the stage and role indicated by this method.

        Examples:
            ``revenue.log()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        self._require("numeric")
        return MetricExpression(self._method("log", (base,)), self._context, "numeric")

    @traced(surface="semantic", operation="metric.clip", kind="method")
    def clip(
        self: MetricExpression[Literal["numeric"]],
        lower: MetricExpression[Literal["numeric"]] | NumericLiteral | None = None,
        upper: MetricExpression[Literal["numeric"]] | NumericLiteral | None = None,
    ) -> MetricExpression[Literal["numeric"]]:
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
        return MetricExpression(
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
        self: MetricExpression[Literal["numeric"]],
        *others: MetricExpression[Literal["numeric"]] | NumericLiteral,
    ) -> MetricExpression[Literal["numeric"]]: ...

    @overload
    def coalesce(
        self: MetricExpression[Literal["boolean"]], *others: MetricPredicate | bool
    ) -> MetricExpression[Literal["boolean"]]: ...

    @traced(surface="semantic", operation="metric.coalesce", kind="method")
    def coalesce(
        self, *others: MetricExpression[MetricRole] | LiteralValue
    ) -> MetricExpression[MetricRole]:
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
        return MetricExpression(
            self._method("coalesce", others), _merge(self, others), cast("MetricRole", self._role)
        )

    @traced(surface="semantic", operation="metric.least", kind="method")
    def least(
        self: MetricExpression[Literal["numeric"]],
        *others: MetricExpression[Literal["numeric"]] | NumericLiteral,
    ) -> MetricExpression[Literal["numeric"]]:
        """Capture the least of numeric expressions or literals.

        Args:
            others: The explicit others captured for this operation.

        Returns:
            A typed expression with the stage and role indicated by this method.

        Examples:
            ``revenue.least()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        self._require("numeric")
        return MetricExpression(self._method("least", others), _merge(self, others), "numeric")

    @traced(surface="semantic", operation="metric.greatest", kind="method")
    def greatest(
        self: MetricExpression[Literal["numeric"]],
        *others: MetricExpression[Literal["numeric"]] | NumericLiteral,
    ) -> MetricExpression[Literal["numeric"]]:
        """Capture the greatest of numeric expressions or literals.

        Args:
            others: The explicit others captured for this operation.

        Returns:
            A typed expression with the stage and role indicated by this method.

        Examples:
            ``revenue.greatest()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        self._require("numeric")
        return MetricExpression(self._method("greatest", others), _merge(self, others), "numeric")

    @overload
    def nullif(
        self: MetricExpression[Literal["numeric"]],
        other: MetricExpression[Literal["numeric"]] | NumericLiteral,
    ) -> MetricExpression[Literal["numeric"]]: ...

    @overload
    def nullif(
        self: MetricExpression[Literal["boolean"]], other: MetricPredicate | bool
    ) -> MetricExpression[Literal["boolean"]]: ...

    @traced(surface="semantic", operation="metric.nullif", kind="method")
    def nullif(
        self, other: MetricExpression[MetricRole] | LiteralValue
    ) -> MetricExpression[MetricRole]:
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
        return MetricExpression(
            self._method("nullif", (other,)), _merge(self, other), cast("MetricRole", self._role)
        )

    @overload
    def cases(  # type: ignore[overload-overlap]  # bool scalar is a Python int subtype; preserve its boolean role.
        self, *branches: tuple[LiteralValue, MetricPredicate | bool], else_: MetricPredicate | bool
    ) -> MetricExpression[Literal["boolean"]]: ...

    @overload
    def cases(
        self,
        *branches: tuple[LiteralValue, MetricExpression[Literal["numeric"]] | NumericLiteral],
        else_: MetricExpression[Literal["numeric"]] | NumericLiteral,
    ) -> MetricExpression[Literal["numeric"]]: ...

    @traced(surface="semantic", operation="metric.cases", kind="method")
    def cases(
        self,
        *branches: tuple[LiteralValue, MetricExpression[MetricRole] | LiteralValue],
        else_: MetricExpression[MetricRole] | LiteralValue,
    ) -> MetricExpression[MetricRole]:
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

        return MetricExpression(
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
            cast("MetricRole", result_role),
        )

    @overload
    def ifelse(  # type: ignore[overload-overlap]  # bool is an int subclass; boolean branches retain their role.
        self: MetricPredicate, true: MetricPredicate | bool, false: MetricPredicate | bool
    ) -> MetricExpression[Literal["boolean"]]: ...

    @overload
    def ifelse(
        self: MetricPredicate,
        true: MetricExpression[Literal["numeric"]] | NumericLiteral,
        false: MetricExpression[Literal["numeric"]] | NumericLiteral,
    ) -> MetricExpression[Literal["numeric"]]: ...

    @traced(surface="semantic", operation="metric.ifelse", kind="method")
    def ifelse(
        self: MetricPredicate,
        true: MetricExpression[MetricRole] | LiteralValue,
        false: MetricExpression[MetricRole] | LiteralValue,
    ) -> MetricExpression[MetricRole]:
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
        return MetricExpression(
            self._method("ifelse", (true, false)),
            _merge(self, true, false),
            cast("MetricRole", branch_role),
        )

    @traced(surface="semantic", operation="metric.where", kind="method")
    def where(self: NumericMetric, predicate: RowPredicate) -> NumericMetric:
        """Capture a source-row condition on a completed Metric.

        Args:
            predicate: The explicit predicate captured for this operation.

        Returns:
            A typed expression with the stage and role indicated by this method.

        Examples:
            ``revenue.where(status == "paid")``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return MetricExpression(
            self._method("where", (predicate,)), _merge(self, predicate), "numeric"
        )

    @overload
    def per(self: NumericMetric, by: TimeExpression, *, grain: Grain) -> _MetricSamples: ...

    @overload
    def per(
        self: NumericMetric,
        by: FieldExpression
        | tuple[FieldExpression | Grouping | PopulationParameter, ...]
        | Grouping
        | PopulationParameter
        | Grain,
    ) -> _MetricSamples: ...

    @traced(surface="semantic", operation="metric.per", kind="method")
    def per(
        self: NumericMetric,
        by: FieldExpression
        | tuple[FieldExpression | Grouping | PopulationParameter, ...]
        | Grouping
        | PopulationParameter
        | Grain,
        *,
        grain: Grain | _GrainOmitted = _GRAIN_OMITTED,
    ) -> _MetricSamples:
        """Capture the internal sample domain before its final reduction.

        Args:
            by: An internal sample field, grouping, population or Grain.
            grain: A builtin Grain for one explicit native TimeField.

        Returns:
            An internal sample value requiring a final reduction.

        Examples:
            ``revenue.per(region).mean()``.
            ``revenue.per(created_at, grain=mv.grain("day")).last()``.

        Notes:
            An explicit Population selects samples matching all shared complete
            keys of the report and enclosing sample layers.
            Builtin calendar samples use the receiving Session's report timezone
            and the actual contributions. An explicit field selects the sample
            time independently of the contribution's time role.
        """

        if grain is not _GRAIN_OMITTED:
            field = _parameter(by)
            if (
                not isinstance(grain, Grain)
                or captured_field_kind(field) is not SemanticKind.TIME_DIMENSION
                or captured_scalar_path(field)
            ):
                raise SemanticExpressionError(
                    kind=ErrorKind.INVALID_EXPRESSION_STAGE,
                    message="Explicit calendar samples require one native TimeField and a builtin Grain.",
                    expected="Metric.per(TimeField, grain=mv.grain(...))",
                    received=f"{type(by).__name__} with grain={grain!r}",
                    action='Use a native TimeField with mv.grain("day"), or omit grain for ordinary samples.',
                    help_target="metric.per",
                )
            return _MetricSamples(
                self._method("per", (by,), {"grain": grain}), _merge(self, by), "samples"
            )

        if isinstance(by, CalendarAxis):
            raise SemanticExpressionError(
                kind=ErrorKind.INVALID_EXPRESSION_STAGE,
                message="Metric.per requires an internal sample axis.",
                expected="field or Grain sample axis",
                received="CalendarAxis",
                action='Use metric.per(mv.grain("day")) and pass a report TimeScope in observe(where=...).',
            )
        return _MetricSamples(self._method("per", (by,)), _merge(self, by), "samples")

    @traced(surface="semantic", operation="metric.window", kind="method")
    def window(self: NumericMetric, window: Window) -> NumericMetric:
        """Capture an observation window on a completed Metric.

        Args:
            window: The explicit window captured for this operation.

        Returns:
            A typed expression with the stage and role indicated by this method.

        Examples:
            ``revenue.window(rule)``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules. Only clocked
            branches change their contribution range; independent branches retain their scope
            and combine once at each established endpoint. Ordinary time grouping retains endpoints
            with actual window contributions, including history; use explicit by=Population
            to retain endpoints with no contributions. A surrounding builtin calendar sample
            supplies its own endpoints; otherwise the report supplies them. For example,
            ``revenue.window(rule).per(mv.grain("day")).mean()`` averages actual daily
            window values, while ``revenue.per(mv.grain("day")).mean().window(rule)``
            averages actual daily revenues inside each report window. Different-field
            sample cohorts and fixed source predicates remain in force. Custom calendar
            sample combinations and nested windows are unsupported.
        """
        return MetricExpression(self._method("window", (window,)), self._context, "numeric")


NumericMetric: TypeAlias = MetricExpression[Literal["numeric"]]
MetricPredicate: TypeAlias = MetricExpression[Literal["boolean"]]


class _MetricSamples(_SampleReductions[NumericMetric]):
    """The intermediate sample role, requiring a final reduction."""

    def _numeric_metric(
        self, recipe: Resolver, context: InputContext, role: Literal["numeric"]
    ) -> NumericMetric:
        return MetricExpression(recipe, context, role)
