"""Typed, immutable facades over the existing controlled Ibis recipe graph."""

from __future__ import annotations

import operator
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import (
    TYPE_CHECKING,
    Generic,
    Literal,
    NoReturn,
    TypeAlias,
    TypeVar,
    cast,
    overload,
)

from ibis.common.deferred import Attr, BinaryOperator, Call, Deferred, Just, Resolver, UnaryOperator
from ibis.common.deferred import Sequence as RecipeSequence

from marivo._definition.deferred import inspect_capture, resolver_of
from marivo._definition.types import (
    DefinitionGraphError,
    InputContext,
    LiteralValue,
    NullPolicy,
    check_literal,
    merge_input_contexts,
)
from marivo._definition.windows import AllHistory, Since, ToDate, TrailingDuration, TrailingPeriods
from marivo._temporal import Grain, InstantAnchor, TimeScope
from marivo.render import Card, RenderableResult
from marivo.semantic.errors import ErrorKind, SemanticExpressionError

if TYPE_CHECKING:
    from marivo._definition.relations import PopulationParameter
    from marivo._grouping import Grouping
    from marivo.semantic.catalog import Relationship

RowRole: TypeAlias = Literal["dimension", "time", "numeric", "boolean"]
MetricRole: TypeAlias = Literal["numeric", "boolean"]
_RowRoleT = TypeVar("_RowRoleT", bound=RowRole, covariant=True)
_MetricRoleT = TypeVar("_MetricRoleT", bound=MetricRole, covariant=True)
NumericLiteral: TypeAlias = int | float | Decimal
Window: TypeAlias = AllHistory | Since | ToDate | TrailingDuration | TrailingPeriods


def _error(message: str) -> NoReturn:
    raise SemanticExpressionError.from_definition(
        DefinitionGraphError(message, kind="invalid_value"), operation="expression capture"
    )


def _stage_error(
    message: str,
    *,
    received: str = "an incompatible expression role",
    expected: str = "expressions with the required semantic stage and role",
) -> NoReturn:
    raise SemanticExpressionError(
        kind=ErrorKind.INVALID_EXPRESSION_STAGE,
        message=message,
        expected=expected,
        received=received,
        action="Use Measure row expressions before reduction and completed Metric expressions afterwards; keep boolean conditions in their matching stage.",
    )


def _parameter(value: object) -> Resolver:
    from marivo._definition.deferred import RelationshipParameter
    from marivo._definition.relations import PopulationParameter
    from marivo._grouping import Grouping
    from marivo.semantic.catalog import Relationship

    if isinstance(value, _ExpressionCarrier):
        return value._recipe
    if isinstance(value, Deferred):
        return resolver_of(value)
    if isinstance(value, Relationship):
        from marivo._definition.deferred import NamedResolver

        return NamedResolver(value._parameter.ref, Just(value._parameter))
    if isinstance(
        value,
        (
            Grouping,
            PopulationParameter,
            Grain,
            TimeScope,
            InstantAnchor,
            AllHistory,
            Since,
            ToDate,
            TrailingDuration,
            TrailingPeriods,
            RelationshipParameter,
        ),
    ):
        return Just(value)
    if isinstance(value, (tuple, list)):
        return RecipeSequence(tuple(_parameter(item) for item in value))
    if isinstance(value, Mapping):
        return Just(tuple((key, _parameter(item)) for key, item in value.items()))
    if value is None or isinstance(
        value, (str, bool, int, float, Decimal, date, datetime, timedelta)
    ):
        try:
            check_literal(value, role="expression argument")
        except DefinitionGraphError as error:
            raise SemanticExpressionError.from_definition(
                error, operation="expression capture"
            ) from error
        return Just(value)
    _error(
        f"Expected a typed semantic expression or immutable argument; received {type(value).__name__}."
    )


def _contexts(value: object) -> tuple[InputContext, ...]:
    from marivo._definition.relations import PopulationParameter
    from marivo._grouping import Grouping
    from marivo.semantic.catalog import _SemanticObject

    if isinstance(value, (Grouping, PopulationParameter)):
        return (value._context,)
    if isinstance(value, _SemanticObject):
        return (value._context,)
    if isinstance(value, InputContext):
        return (value,)
    if isinstance(value, Deferred):
        return (inspect_capture(value).context,)
    if isinstance(value, _ExpressionCarrier):
        return (value._context,)
    if isinstance(value, (tuple, list)):
        return tuple(context for item in value for context in _contexts(item))
    if isinstance(value, Mapping):
        return tuple(context for item in value.values() for context in _contexts(item))
    return ()


def _merge(*values: object) -> InputContext:
    try:
        return merge_input_contexts(*(context for value in values for context in _contexts(value)))
    except DefinitionGraphError as error:
        raise SemanticExpressionError.from_definition(
            error, operation="expression capture"
        ) from error


def _formula(recipe: Resolver, depth: int = 0) -> str:
    """Render captured recipe syntax, without interpreting business facts."""
    from marivo._definition.deferred import (
        DeclarationResolver,
        NamedResolver,
        RescaleResolver,
        SemanticResolver,
        attribute_name,
    )
    from marivo._symbolic import SymbolResolver

    if depth > 5:
        return "..."

    def child(value: Resolver) -> str:
        return _formula(value, depth + 1)

    if isinstance(recipe, NamedResolver):
        return recipe.reference.ref.key
    if isinstance(recipe, SymbolResolver):
        return recipe.reference.key
    if isinstance(recipe, SemanticResolver):
        return recipe.input.reference.ref.key
    if isinstance(recipe, DeclarationResolver):
        return child(recipe.recipe)
    if isinstance(recipe, RescaleResolver):
        return f"rescale({child(recipe.recipe)}, divisor={recipe.divisor!r}, unit={recipe.unit!r})"
    if isinstance(recipe, BinaryOperator):
        symbols = (
            (operator.add, "+"),
            (operator.sub, "-"),
            (operator.mul, "*"),
            (operator.truediv, "/"),
            (operator.mod, "%"),
            (operator.pow, "**"),
            (operator.eq, "=="),
            (operator.ne, "!="),
            (operator.lt, "<"),
            (operator.le, "<="),
            (operator.gt, ">"),
            (operator.ge, ">="),
            (operator.and_, "&"),
            (operator.or_, "|"),
        )
        symbol = next((label for function, label in symbols if recipe.func is function), "operator")
        return f"({child(recipe.left)} {symbol} {child(recipe.right)})"
    if isinstance(recipe, UnaryOperator):
        return f"{'~' if recipe.func is operator.invert else '-'}{child(recipe.arg)}"
    if isinstance(recipe, Attr):
        return f"{child(recipe.obj)}.{attribute_name(recipe)}"
    if isinstance(recipe, Call):
        args = [child(value) for value in recipe.args[:4]]
        args.extend(f"{key}={child(value)}" for key, value in tuple(recipe.kwargs.items())[:4])
        return f"{child(recipe.func)}({', '.join(args)})"
    if isinstance(recipe, RecipeSequence):
        return "(" + ", ".join(child(value) for value in recipe.values[:4]) + ")"
    if isinstance(recipe, Just):
        return repr(recipe.value)[:80]
    return type(recipe).__name__


class _ExpressionCarrier(RenderableResult):
    __slots__ = ("_captured_context", "_captured_recipe", "_captured_role")
    _captured_recipe: Resolver
    _captured_context: InputContext
    _captured_role: str
    __hash__ = None  # type: ignore[assignment]  # Expressions define predicate equality and are never hash keys.

    def __init__(self, recipe: Resolver, context: InputContext, role: str) -> None:
        object.__setattr__(self, "_captured_recipe", recipe)
        object.__setattr__(self, "_captured_context", context)
        object.__setattr__(self, "_captured_role", role)
        try:
            inspection = inspect_capture(Deferred(recipe))
            object.__setattr__(
                self, "_captured_context", merge_input_contexts(context, inspection.context)
            )
        except DefinitionGraphError as error:
            raise SemanticExpressionError.from_definition(
                error, operation="expression capture"
            ) from error

    def __setattr__(self, name: str, value: object) -> NoReturn:
        raise AttributeError("Semantic expressions are immutable.")

    @property
    def _recipe(self) -> Resolver:
        return self._captured_recipe

    @property
    def _context(self) -> InputContext:
        return self._captured_context

    @property
    def _role(self) -> str:
        return self._captured_role

    def __bool__(self) -> NoReturn:
        _error(
            "A semantic expression has no Python truth value. Use &, |, ~ and parentheses for conditions."
        )

    def _require(self, *roles: str) -> None:
        if self._role not in roles:
            _stage_error(
                f"Expected expression role {' or '.join(roles)}; received {self._role}.",
                expected=" or ".join(roles),
                received=f"{type(self).__name__}[{self._role}]",
            )

    def _method(
        self, name: str, args: tuple[object, ...] = (), kwargs: Mapping[str, object] | None = None
    ) -> Resolver:
        parameters = {
            key: value
            for key, value in (kwargs or {}).items()
            if not (key in {"time", "where"} and value is None)
            and not (key == "nulls" and type(value) is str and value == "ignore")
            and not (key == "include_null" and value is False)
        }
        return Call(
            Attr(self._recipe, name),
            *(_parameter(value) for value in args),
            **{key: _parameter(value) for key, value in parameters.items()},
        )

    def _repr_identity(self) -> str:
        return f"{type(self).__name__} {self._role} expression"

    def _card(self) -> Card:
        inspection = inspect_capture(Deferred(self._recipe))
        return (
            Card(identity=self._repr_identity(), available=(".show()",))
            .field("role", self._role)
            .field("formula", _formula(self._recipe)[:400])
            .field(
                "dependencies",
                ", ".join(item.ref.key for item in inspection.dependencies) or "(none)",
            )
            .field(
                "project",
                str(self._context.project_root)
                if self._context.project_root is not None
                else "unbound authoring",
            )
            .field(
                "owner",
                f"{self._context.owner.session_id}/{self._context.owner.owner_id}"
                if self._context.owner is not None
                else "(none)",
            )
            .field("boundary", "captured expression; no source read or execution")
        )

    def __repr__(self) -> str:
        return f"{self._repr_identity()} (use .show())"


class RowExpression(_ExpressionCarrier, Generic[_RowRoleT]):
    """An immutable row expression with one closed semantic role."""

    def __init__(self, recipe: Resolver, context: InputContext, role: _RowRoleT) -> None:
        if role not in ("dimension", "time", "numeric", "boolean"):
            _stage_error("RowExpression requires a closed row role.", received=repr(role))
        super().__init__(recipe, context, role)

    def __add__(
        self: RowExpression[Literal["numeric"]],
        other: RowExpression[Literal["numeric"]] | NumericLiteral,
    ) -> RowExpression[Literal["numeric"]]:
        self._require("numeric")
        if isinstance(other, _ExpressionCarrier) and (
            not isinstance(other, RowExpression) or other._role != "numeric"
        ):
            _stage_error(
                "Arithmetic requires numeric expressions from the same stage.",
                received=f"{type(self).__name__}[{self._role}] and {type(other).__name__}[{other._role}]",
            )
        if isinstance(other, bool):
            _error("Boolean literals are not numeric operands.")
        return RowExpression(
            BinaryOperator(operator.add, self._recipe, _parameter(other)),
            _merge(self, other),
            "numeric",
        )

    def __radd__(
        self: RowExpression[Literal["numeric"]],
        other: RowExpression[Literal["numeric"]] | NumericLiteral,
    ) -> RowExpression[Literal["numeric"]]:
        self._require("numeric")
        if isinstance(other, _ExpressionCarrier) and (
            not isinstance(other, RowExpression) or other._role != "numeric"
        ):
            _stage_error(
                "Arithmetic requires numeric expressions from the same stage.",
                received=f"{type(self).__name__}[{self._role}] and {type(other).__name__}[{other._role}]",
            )
        if isinstance(other, bool):
            _error("Boolean literals are not numeric operands.")
        return RowExpression(
            BinaryOperator(operator.add, _parameter(other), self._recipe),
            _merge(self, other),
            "numeric",
        )

    def __sub__(
        self: RowExpression[Literal["numeric"]],
        other: RowExpression[Literal["numeric"]] | NumericLiteral,
    ) -> RowExpression[Literal["numeric"]]:
        self._require("numeric")
        if isinstance(other, _ExpressionCarrier) and (
            not isinstance(other, RowExpression) or other._role != "numeric"
        ):
            _stage_error(
                "Arithmetic requires numeric expressions from the same stage.",
                received=f"{type(self).__name__}[{self._role}] and {type(other).__name__}[{other._role}]",
            )
        if isinstance(other, bool):
            _error("Boolean literals are not numeric operands.")
        return RowExpression(
            BinaryOperator(operator.sub, self._recipe, _parameter(other)),
            _merge(self, other),
            "numeric",
        )

    def __rsub__(
        self: RowExpression[Literal["numeric"]],
        other: RowExpression[Literal["numeric"]] | NumericLiteral,
    ) -> RowExpression[Literal["numeric"]]:
        self._require("numeric")
        if isinstance(other, _ExpressionCarrier) and (
            not isinstance(other, RowExpression) or other._role != "numeric"
        ):
            _stage_error(
                "Arithmetic requires numeric expressions from the same stage.",
                received=f"{type(self).__name__}[{self._role}] and {type(other).__name__}[{other._role}]",
            )
        if isinstance(other, bool):
            _error("Boolean literals are not numeric operands.")
        return RowExpression(
            BinaryOperator(operator.sub, _parameter(other), self._recipe),
            _merge(self, other),
            "numeric",
        )

    def __mul__(
        self: RowExpression[Literal["numeric"]],
        other: RowExpression[Literal["numeric"]] | NumericLiteral,
    ) -> RowExpression[Literal["numeric"]]:
        self._require("numeric")
        if isinstance(other, _ExpressionCarrier) and (
            not isinstance(other, RowExpression) or other._role != "numeric"
        ):
            _stage_error(
                "Arithmetic requires numeric expressions from the same stage.",
                received=f"{type(self).__name__}[{self._role}] and {type(other).__name__}[{other._role}]",
            )
        if isinstance(other, bool):
            _error("Boolean literals are not numeric operands.")
        return RowExpression(
            BinaryOperator(operator.mul, self._recipe, _parameter(other)),
            _merge(self, other),
            "numeric",
        )

    def __rmul__(
        self: RowExpression[Literal["numeric"]],
        other: RowExpression[Literal["numeric"]] | NumericLiteral,
    ) -> RowExpression[Literal["numeric"]]:
        self._require("numeric")
        if isinstance(other, _ExpressionCarrier) and (
            not isinstance(other, RowExpression) or other._role != "numeric"
        ):
            _stage_error(
                "Arithmetic requires numeric expressions from the same stage.",
                received=f"{type(self).__name__}[{self._role}] and {type(other).__name__}[{other._role}]",
            )
        if isinstance(other, bool):
            _error("Boolean literals are not numeric operands.")
        return RowExpression(
            BinaryOperator(operator.mul, _parameter(other), self._recipe),
            _merge(self, other),
            "numeric",
        )

    def __truediv__(
        self: RowExpression[Literal["numeric"]],
        other: RowExpression[Literal["numeric"]] | NumericLiteral,
    ) -> RowExpression[Literal["numeric"]]:
        self._require("numeric")
        if isinstance(other, _ExpressionCarrier) and (
            not isinstance(other, RowExpression) or other._role != "numeric"
        ):
            _stage_error(
                "Arithmetic requires numeric expressions from the same stage.",
                received=f"{type(self).__name__}[{self._role}] and {type(other).__name__}[{other._role}]",
            )
        if isinstance(other, bool):
            _error("Boolean literals are not numeric operands.")
        return RowExpression(
            BinaryOperator(operator.truediv, self._recipe, _parameter(other)),
            _merge(self, other),
            "numeric",
        )

    def __rtruediv__(
        self: RowExpression[Literal["numeric"]],
        other: RowExpression[Literal["numeric"]] | NumericLiteral,
    ) -> RowExpression[Literal["numeric"]]:
        self._require("numeric")
        if isinstance(other, _ExpressionCarrier) and (
            not isinstance(other, RowExpression) or other._role != "numeric"
        ):
            _stage_error(
                "Arithmetic requires numeric expressions from the same stage.",
                received=f"{type(self).__name__}[{self._role}] and {type(other).__name__}[{other._role}]",
            )
        if isinstance(other, bool):
            _error("Boolean literals are not numeric operands.")
        return RowExpression(
            BinaryOperator(operator.truediv, _parameter(other), self._recipe),
            _merge(self, other),
            "numeric",
        )

    def __mod__(
        self: RowExpression[Literal["numeric"]],
        other: RowExpression[Literal["numeric"]] | NumericLiteral,
    ) -> RowExpression[Literal["numeric"]]:
        self._require("numeric")
        if isinstance(other, _ExpressionCarrier) and (
            not isinstance(other, RowExpression) or other._role != "numeric"
        ):
            _stage_error(
                "Arithmetic requires numeric expressions from the same stage.",
                received=f"{type(self).__name__}[{self._role}] and {type(other).__name__}[{other._role}]",
            )
        if isinstance(other, bool):
            _error("Boolean literals are not numeric operands.")
        return RowExpression(
            BinaryOperator(operator.mod, self._recipe, _parameter(other)),
            _merge(self, other),
            "numeric",
        )

    def __rmod__(
        self: RowExpression[Literal["numeric"]],
        other: RowExpression[Literal["numeric"]] | NumericLiteral,
    ) -> RowExpression[Literal["numeric"]]:
        self._require("numeric")
        if isinstance(other, _ExpressionCarrier) and (
            not isinstance(other, RowExpression) or other._role != "numeric"
        ):
            _stage_error(
                "Arithmetic requires numeric expressions from the same stage.",
                received=f"{type(self).__name__}[{self._role}] and {type(other).__name__}[{other._role}]",
            )
        if isinstance(other, bool):
            _error("Boolean literals are not numeric operands.")
        return RowExpression(
            BinaryOperator(operator.mod, _parameter(other), self._recipe),
            _merge(self, other),
            "numeric",
        )

    def __pow__(
        self: RowExpression[Literal["numeric"]],
        other: RowExpression[Literal["numeric"]] | NumericLiteral,
    ) -> RowExpression[Literal["numeric"]]:
        self._require("numeric")
        if isinstance(other, _ExpressionCarrier) and (
            not isinstance(other, RowExpression) or other._role != "numeric"
        ):
            _stage_error(
                "Arithmetic requires numeric expressions from the same stage.",
                received=f"{type(self).__name__}[{self._role}] and {type(other).__name__}[{other._role}]",
            )
        if isinstance(other, bool):
            _error("Boolean literals are not numeric operands.")
        return RowExpression(
            BinaryOperator(operator.pow, self._recipe, _parameter(other)),
            _merge(self, other),
            "numeric",
        )

    def __rpow__(
        self: RowExpression[Literal["numeric"]],
        other: RowExpression[Literal["numeric"]] | NumericLiteral,
    ) -> RowExpression[Literal["numeric"]]:
        self._require("numeric")
        if isinstance(other, _ExpressionCarrier) and (
            not isinstance(other, RowExpression) or other._role != "numeric"
        ):
            _stage_error(
                "Arithmetic requires numeric expressions from the same stage.",
                received=f"{type(self).__name__}[{self._role}] and {type(other).__name__}[{other._role}]",
            )
        if isinstance(other, bool):
            _error("Boolean literals are not numeric operands.")
        return RowExpression(
            BinaryOperator(operator.pow, _parameter(other), self._recipe),
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
        self._require("boolean")
        return RowExpression(
            BinaryOperator(operator.and_, self._recipe, _parameter(other)),
            _merge(self, other),
            "boolean",
        )

    def __or__(self: RowPredicate, other: RowPredicate | bool) -> RowPredicate:
        self._require("boolean")
        return RowExpression(
            BinaryOperator(operator.or_, self._recipe, _parameter(other)),
            _merge(self, other),
            "boolean",
        )

    def __rand__(self: RowPredicate, other: bool) -> RowPredicate:
        self._require("boolean")
        return RowExpression(
            BinaryOperator(operator.and_, _parameter(other), self._recipe),
            _merge(self, other),
            "boolean",
        )

    def __ror__(self: RowPredicate, other: bool) -> RowPredicate:
        self._require("boolean")
        return RowExpression(
            BinaryOperator(operator.or_, _parameter(other), self._recipe),
            _merge(self, other),
            "boolean",
        )

    def __invert__(self: RowPredicate) -> RowPredicate:
        self._require("boolean")
        return RowExpression(UnaryOperator(operator.invert, self._recipe), self._context, "boolean")

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
        from inspect import unwrap

        import ibis

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

    def via(self, path: tuple[Relationship, ...]) -> RowExpression[_RowRoleT]:
        """Capture a field through one explicit directed relationship path.

        Args:
            path: The explicit path captured for this operation.

        Returns:
            A typed expression with the stage and role indicated by this method.

        Examples:
            ``region.via((buyer,))``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        from marivo.semantic.catalog import Relationship

        if (
            type(path) is not tuple
            or not path
            or any(type(step) is not Relationship for step in path)
        ):
            _error("via requires a non-empty ordered tuple of loaded Relationship objects.")
        return RowExpression(
            self._method("via", (path,)), _merge(self, path), cast("_RowRoleT", self._role)
        )

    def at(self, anchor: InstantAnchor) -> RowExpression[_RowRoleT]:
        """Capture a versioned field at one exact temporal anchor.

        Args:
            anchor: The explicit anchor captured for this operation.

        Returns:
            A typed expression with the stage and role indicated by this method.

        Examples:
            ``region.at(anchor)``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return RowExpression(
            self._method("at", (anchor,)), self._context, cast("_RowRoleT", self._role)
        )

    def within(self: TimeExpression, scope: TimeScope) -> RowPredicate:
        """Capture the half-open bounds of an immutable time scope.

        Args:
            scope: The explicit scope captured for this operation.

        Returns:
            A typed expression with the stage and role indicated by this method.

        Examples:
            ``paid_at.within(period)``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        self._require("time")
        return RowExpression(self._method("within", (scope,)), self._context, "boolean")

    def sum(
        self: RowExpression[Literal["numeric"]],
        *,
        time: TimeExpression | None = None,
        nulls: NullPolicy = "ignore",
        where: RowPredicate | None = None,
    ) -> NumericMetric:
        """Capture the sum reduction using the existing row rules.

        Args:
            time: The explicit time captured for this operation.
            nulls: The explicit nulls captured for this operation.
            where: Optional same-root source-row condition captured before reduction.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``amount.sum()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return MetricExpression(
            self._method("sum", kwargs={"time": time, "nulls": nulls, "where": where}),
            _merge(self, time, where),
            "numeric",
        )

    def mean(
        self: RowExpression[Literal["numeric"]],
        *,
        time: TimeExpression | None = None,
        nulls: NullPolicy = "ignore",
        where: RowPredicate | None = None,
    ) -> NumericMetric:
        """Capture the mean reduction using the existing row rules.

        Args:
            time: The explicit time captured for this operation.
            nulls: The explicit nulls captured for this operation.
            where: Optional same-root source-row condition captured before reduction.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``amount.mean()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return MetricExpression(
            self._method("mean", kwargs={"time": time, "nulls": nulls, "where": where}),
            _merge(self, time, where),
            "numeric",
        )

    def min(
        self: RowExpression[Literal["numeric"]],
        *,
        time: TimeExpression | None = None,
        nulls: NullPolicy = "ignore",
        where: RowPredicate | None = None,
    ) -> NumericMetric:
        """Capture the min reduction using the existing row rules.

        Args:
            time: The explicit time captured for this operation.
            nulls: The explicit nulls captured for this operation.
            where: Optional same-root source-row condition captured before reduction.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``amount.min()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return MetricExpression(
            self._method("min", kwargs={"time": time, "nulls": nulls, "where": where}),
            _merge(self, time, where),
            "numeric",
        )

    def max(
        self: RowExpression[Literal["numeric"]],
        *,
        time: TimeExpression | None = None,
        nulls: NullPolicy = "ignore",
        where: RowPredicate | None = None,
    ) -> NumericMetric:
        """Capture the max reduction using the existing row rules.

        Args:
            time: The explicit time captured for this operation.
            nulls: The explicit nulls captured for this operation.
            where: Optional same-root source-row condition captured before reduction.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``amount.max()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return MetricExpression(
            self._method("max", kwargs={"time": time, "nulls": nulls, "where": where}),
            _merge(self, time, where),
            "numeric",
        )

    def median(
        self: RowExpression[Literal["numeric"]],
        *,
        time: TimeExpression | None = None,
        nulls: NullPolicy = "ignore",
        where: RowPredicate | None = None,
    ) -> NumericMetric:
        """Capture the median reduction using the existing row rules.

        Args:
            time: The explicit time captured for this operation.
            nulls: The explicit nulls captured for this operation.
            where: Optional same-root source-row condition captured before reduction.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``amount.median()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return MetricExpression(
            self._method("median", kwargs={"time": time, "nulls": nulls, "where": where}),
            _merge(self, time, where),
            "numeric",
        )

    def count(
        self,
        *,
        time: TimeExpression | None = None,
        nulls: NullPolicy = "ignore",
        where: RowPredicate | None = None,
    ) -> NumericMetric:
        """Capture the count reduction using the existing row rules.

        Args:
            time: The explicit time captured for this operation.
            nulls: The explicit nulls captured for this operation.
            where: Optional same-root source-row condition captured before reduction.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``amount.count()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return MetricExpression(
            self._method("count", kwargs={"time": time, "nulls": nulls, "where": where}),
            _merge(self, time, where),
            "numeric",
        )

    def count_distinct(
        self,
        *,
        time: TimeExpression | None = None,
        nulls: NullPolicy = "ignore",
        where: RowPredicate | None = None,
    ) -> NumericMetric:
        """Capture the count_distinct reduction using the existing row rules.

        Args:
            time: The explicit time captured for this operation.
            nulls: The explicit nulls captured for this operation.
            where: Optional same-root source-row condition captured before reduction.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``amount.count_distinct()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return MetricExpression(
            self._method("count_distinct", kwargs={"time": time, "nulls": nulls, "where": where}),
            _merge(self, time, where),
            "numeric",
        )

    def approx_nunique(
        self,
        *,
        time: TimeExpression | None = None,
        nulls: NullPolicy = "ignore",
        where: RowPredicate | None = None,
    ) -> NumericMetric:
        """Capture the approx_nunique reduction using the existing row rules.

        Args:
            time: The explicit time captured for this operation.
            nulls: The explicit nulls captured for this operation.
            where: Optional same-root source-row condition captured before reduction.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``amount.approx_nunique()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return MetricExpression(
            self._method("approx_nunique", kwargs={"time": time, "nulls": nulls, "where": where}),
            _merge(self, time, where),
            "numeric",
        )

    def approx_median(
        self: RowExpression[Literal["numeric"]],
        *,
        time: TimeExpression | None = None,
        nulls: NullPolicy = "ignore",
        where: RowPredicate | None = None,
    ) -> NumericMetric:
        """Capture the approx_median reduction using the existing row rules.

        Args:
            time: The explicit time captured for this operation.
            nulls: The explicit nulls captured for this operation.
            where: Optional same-root source-row condition captured before reduction.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``amount.approx_median()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return MetricExpression(
            self._method("approx_median", kwargs={"time": time, "nulls": nulls, "where": where}),
            _merge(self, time, where),
            "numeric",
        )

    def quantile(
        self: RowExpression[Literal["numeric"]],
        q: float,
        *,
        time: TimeExpression | None = None,
        nulls: NullPolicy = "ignore",
        where: RowPredicate | None = None,
    ) -> NumericMetric:
        """Capture the quantile reduction using the existing row rules.

        Args:
            q: The explicit q captured for this operation.
            time: The explicit time captured for this operation.
            nulls: The explicit nulls captured for this operation.
            where: Optional same-root source-row condition captured before reduction.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``amount.quantile(0.95)``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return MetricExpression(
            self._method("quantile", (q,), {"time": time, "nulls": nulls, "where": where}),
            _merge(self, time, where),
            "numeric",
        )

    def approx_quantile(
        self: RowExpression[Literal["numeric"]],
        q: float,
        *,
        time: TimeExpression | None = None,
        nulls: NullPolicy = "ignore",
        where: RowPredicate | None = None,
    ) -> NumericMetric:
        """Capture the approx_quantile reduction using the existing row rules.

        Args:
            q: The explicit q captured for this operation.
            time: The explicit time captured for this operation.
            nulls: The explicit nulls captured for this operation.
            where: Optional same-root source-row condition captured before reduction.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``amount.approx_quantile(0.95)``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return MetricExpression(
            self._method("approx_quantile", (q,), {"time": time, "nulls": nulls, "where": where}),
            _merge(self, time, where),
            "numeric",
        )

    def weighted_mean(
        self: RowExpression[Literal["numeric"]],
        weight: RowExpression[Literal["numeric"]],
        *,
        time: TimeExpression | None = None,
        nulls: NullPolicy = "ignore",
        where: RowPredicate | None = None,
    ) -> NumericMetric:
        """Capture the weighted_mean reduction using the existing row rules.

        Args:
            weight: The explicit weight captured for this operation.
            time: The explicit time captured for this operation.
            nulls: The explicit nulls captured for this operation.
            where: Optional same-root source-row condition captured before reduction.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``amount.weighted_mean(weight=quantity)``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return MetricExpression(
            self._method(
                "weighted_mean",
                kwargs={"weight": weight, "time": time, "nulls": nulls, "where": where},
            ),
            _merge(self, weight, time, where),
            "numeric",
        )


class MetricExpression(_ExpressionCarrier, Generic[_MetricRoleT]):
    """An immutable metric expression with one closed semantic role."""

    def __init__(self, recipe: Resolver, context: InputContext, role: _MetricRoleT) -> None:
        if role not in ("numeric", "boolean"):
            _stage_error("MetricExpression requires a closed metric role.", received=repr(role))
        super().__init__(recipe, context, role)

    def __add__(
        self: MetricExpression[Literal["numeric"]],
        other: MetricExpression[Literal["numeric"]] | NumericLiteral,
    ) -> MetricExpression[Literal["numeric"]]:
        self._require("numeric")
        if isinstance(other, _ExpressionCarrier) and (
            not isinstance(other, MetricExpression) or other._role != "numeric"
        ):
            _stage_error(
                "Arithmetic requires numeric expressions from the same stage.",
                received=f"{type(self).__name__}[{self._role}] and {type(other).__name__}[{other._role}]",
            )
        if isinstance(other, bool):
            _error("Boolean literals are not numeric operands.")
        return MetricExpression(
            BinaryOperator(operator.add, self._recipe, _parameter(other)),
            _merge(self, other),
            "numeric",
        )

    def __radd__(
        self: MetricExpression[Literal["numeric"]],
        other: MetricExpression[Literal["numeric"]] | NumericLiteral,
    ) -> MetricExpression[Literal["numeric"]]:
        self._require("numeric")
        if isinstance(other, _ExpressionCarrier) and (
            not isinstance(other, MetricExpression) or other._role != "numeric"
        ):
            _stage_error(
                "Arithmetic requires numeric expressions from the same stage.",
                received=f"{type(self).__name__}[{self._role}] and {type(other).__name__}[{other._role}]",
            )
        if isinstance(other, bool):
            _error("Boolean literals are not numeric operands.")
        return MetricExpression(
            BinaryOperator(operator.add, _parameter(other), self._recipe),
            _merge(self, other),
            "numeric",
        )

    def __sub__(
        self: MetricExpression[Literal["numeric"]],
        other: MetricExpression[Literal["numeric"]] | NumericLiteral,
    ) -> MetricExpression[Literal["numeric"]]:
        self._require("numeric")
        if isinstance(other, _ExpressionCarrier) and (
            not isinstance(other, MetricExpression) or other._role != "numeric"
        ):
            _stage_error(
                "Arithmetic requires numeric expressions from the same stage.",
                received=f"{type(self).__name__}[{self._role}] and {type(other).__name__}[{other._role}]",
            )
        if isinstance(other, bool):
            _error("Boolean literals are not numeric operands.")
        return MetricExpression(
            BinaryOperator(operator.sub, self._recipe, _parameter(other)),
            _merge(self, other),
            "numeric",
        )

    def __rsub__(
        self: MetricExpression[Literal["numeric"]],
        other: MetricExpression[Literal["numeric"]] | NumericLiteral,
    ) -> MetricExpression[Literal["numeric"]]:
        self._require("numeric")
        if isinstance(other, _ExpressionCarrier) and (
            not isinstance(other, MetricExpression) or other._role != "numeric"
        ):
            _stage_error(
                "Arithmetic requires numeric expressions from the same stage.",
                received=f"{type(self).__name__}[{self._role}] and {type(other).__name__}[{other._role}]",
            )
        if isinstance(other, bool):
            _error("Boolean literals are not numeric operands.")
        return MetricExpression(
            BinaryOperator(operator.sub, _parameter(other), self._recipe),
            _merge(self, other),
            "numeric",
        )

    def __mul__(
        self: MetricExpression[Literal["numeric"]],
        other: MetricExpression[Literal["numeric"]] | NumericLiteral,
    ) -> MetricExpression[Literal["numeric"]]:
        self._require("numeric")
        if isinstance(other, _ExpressionCarrier) and (
            not isinstance(other, MetricExpression) or other._role != "numeric"
        ):
            _stage_error(
                "Arithmetic requires numeric expressions from the same stage.",
                received=f"{type(self).__name__}[{self._role}] and {type(other).__name__}[{other._role}]",
            )
        if isinstance(other, bool):
            _error("Boolean literals are not numeric operands.")
        return MetricExpression(
            BinaryOperator(operator.mul, self._recipe, _parameter(other)),
            _merge(self, other),
            "numeric",
        )

    def __rmul__(
        self: MetricExpression[Literal["numeric"]],
        other: MetricExpression[Literal["numeric"]] | NumericLiteral,
    ) -> MetricExpression[Literal["numeric"]]:
        self._require("numeric")
        if isinstance(other, _ExpressionCarrier) and (
            not isinstance(other, MetricExpression) or other._role != "numeric"
        ):
            _stage_error(
                "Arithmetic requires numeric expressions from the same stage.",
                received=f"{type(self).__name__}[{self._role}] and {type(other).__name__}[{other._role}]",
            )
        if isinstance(other, bool):
            _error("Boolean literals are not numeric operands.")
        return MetricExpression(
            BinaryOperator(operator.mul, _parameter(other), self._recipe),
            _merge(self, other),
            "numeric",
        )

    def __truediv__(
        self: MetricExpression[Literal["numeric"]],
        other: MetricExpression[Literal["numeric"]] | NumericLiteral,
    ) -> MetricExpression[Literal["numeric"]]:
        self._require("numeric")
        if isinstance(other, _ExpressionCarrier) and (
            not isinstance(other, MetricExpression) or other._role != "numeric"
        ):
            _stage_error(
                "Arithmetic requires numeric expressions from the same stage.",
                received=f"{type(self).__name__}[{self._role}] and {type(other).__name__}[{other._role}]",
            )
        if isinstance(other, bool):
            _error("Boolean literals are not numeric operands.")
        return MetricExpression(
            BinaryOperator(operator.truediv, self._recipe, _parameter(other)),
            _merge(self, other),
            "numeric",
        )

    def __rtruediv__(
        self: MetricExpression[Literal["numeric"]],
        other: MetricExpression[Literal["numeric"]] | NumericLiteral,
    ) -> MetricExpression[Literal["numeric"]]:
        self._require("numeric")
        if isinstance(other, _ExpressionCarrier) and (
            not isinstance(other, MetricExpression) or other._role != "numeric"
        ):
            _stage_error(
                "Arithmetic requires numeric expressions from the same stage.",
                received=f"{type(self).__name__}[{self._role}] and {type(other).__name__}[{other._role}]",
            )
        if isinstance(other, bool):
            _error("Boolean literals are not numeric operands.")
        return MetricExpression(
            BinaryOperator(operator.truediv, _parameter(other), self._recipe),
            _merge(self, other),
            "numeric",
        )

    def __mod__(
        self: MetricExpression[Literal["numeric"]],
        other: MetricExpression[Literal["numeric"]] | NumericLiteral,
    ) -> MetricExpression[Literal["numeric"]]:
        self._require("numeric")
        if isinstance(other, _ExpressionCarrier) and (
            not isinstance(other, MetricExpression) or other._role != "numeric"
        ):
            _stage_error(
                "Arithmetic requires numeric expressions from the same stage.",
                received=f"{type(self).__name__}[{self._role}] and {type(other).__name__}[{other._role}]",
            )
        if isinstance(other, bool):
            _error("Boolean literals are not numeric operands.")
        return MetricExpression(
            BinaryOperator(operator.mod, self._recipe, _parameter(other)),
            _merge(self, other),
            "numeric",
        )

    def __rmod__(
        self: MetricExpression[Literal["numeric"]],
        other: MetricExpression[Literal["numeric"]] | NumericLiteral,
    ) -> MetricExpression[Literal["numeric"]]:
        self._require("numeric")
        if isinstance(other, _ExpressionCarrier) and (
            not isinstance(other, MetricExpression) or other._role != "numeric"
        ):
            _stage_error(
                "Arithmetic requires numeric expressions from the same stage.",
                received=f"{type(self).__name__}[{self._role}] and {type(other).__name__}[{other._role}]",
            )
        if isinstance(other, bool):
            _error("Boolean literals are not numeric operands.")
        return MetricExpression(
            BinaryOperator(operator.mod, _parameter(other), self._recipe),
            _merge(self, other),
            "numeric",
        )

    def __pow__(
        self: MetricExpression[Literal["numeric"]],
        other: MetricExpression[Literal["numeric"]] | NumericLiteral,
    ) -> MetricExpression[Literal["numeric"]]:
        self._require("numeric")
        if isinstance(other, _ExpressionCarrier) and (
            not isinstance(other, MetricExpression) or other._role != "numeric"
        ):
            _stage_error(
                "Arithmetic requires numeric expressions from the same stage.",
                received=f"{type(self).__name__}[{self._role}] and {type(other).__name__}[{other._role}]",
            )
        if isinstance(other, bool):
            _error("Boolean literals are not numeric operands.")
        return MetricExpression(
            BinaryOperator(operator.pow, self._recipe, _parameter(other)),
            _merge(self, other),
            "numeric",
        )

    def __rpow__(
        self: MetricExpression[Literal["numeric"]],
        other: MetricExpression[Literal["numeric"]] | NumericLiteral,
    ) -> MetricExpression[Literal["numeric"]]:
        self._require("numeric")
        if isinstance(other, _ExpressionCarrier) and (
            not isinstance(other, MetricExpression) or other._role != "numeric"
        ):
            _stage_error(
                "Arithmetic requires numeric expressions from the same stage.",
                received=f"{type(self).__name__}[{self._role}] and {type(other).__name__}[{other._role}]",
            )
        if isinstance(other, bool):
            _error("Boolean literals are not numeric operands.")
        return MetricExpression(
            BinaryOperator(operator.pow, _parameter(other), self._recipe),
            _merge(self, other),
            "numeric",
        )

    def __neg__(self: MetricExpression[Literal["numeric"]]) -> MetricExpression[Literal["numeric"]]:
        self._require("numeric")
        return MetricExpression(UnaryOperator(operator.neg, self._recipe), self._context, "numeric")

    def __eq__(self, other: MetricExpression[MetricRole] | LiteralValue) -> MetricPredicate:  # type: ignore[override]  # Python equality constructs a typed predicate, never identity equality.
        if isinstance(other, _ExpressionCarrier) and not isinstance(other, MetricExpression):
            _stage_error(
                "Comparison requires expressions from the same stage.",
                received=f"{type(self).__name__}[{self._role}] and {type(other).__name__}[{other._role}]",
            )
        return MetricExpression(
            BinaryOperator(operator.eq, self._recipe, _parameter(other)),
            _merge(self, other),
            "boolean",
        )

    def __ne__(self, other: MetricExpression[MetricRole] | LiteralValue) -> MetricPredicate:  # type: ignore[override]  # Python equality constructs a typed predicate, never identity equality.
        if isinstance(other, _ExpressionCarrier) and not isinstance(other, MetricExpression):
            _stage_error(
                "Comparison requires expressions from the same stage.",
                received=f"{type(self).__name__}[{self._role}] and {type(other).__name__}[{other._role}]",
            )
        return MetricExpression(
            BinaryOperator(operator.ne, self._recipe, _parameter(other)),
            _merge(self, other),
            "boolean",
        )

    def __lt__(self, other: MetricExpression[MetricRole] | LiteralValue) -> MetricPredicate:
        if isinstance(other, _ExpressionCarrier) and not isinstance(other, MetricExpression):
            _stage_error(
                "Comparison requires expressions from the same stage.",
                received=f"{type(self).__name__}[{self._role}] and {type(other).__name__}[{other._role}]",
            )
        return MetricExpression(
            BinaryOperator(operator.lt, self._recipe, _parameter(other)),
            _merge(self, other),
            "boolean",
        )

    def __le__(self, other: MetricExpression[MetricRole] | LiteralValue) -> MetricPredicate:
        if isinstance(other, _ExpressionCarrier) and not isinstance(other, MetricExpression):
            _stage_error(
                "Comparison requires expressions from the same stage.",
                received=f"{type(self).__name__}[{self._role}] and {type(other).__name__}[{other._role}]",
            )
        return MetricExpression(
            BinaryOperator(operator.le, self._recipe, _parameter(other)),
            _merge(self, other),
            "boolean",
        )

    def __gt__(self, other: MetricExpression[MetricRole] | LiteralValue) -> MetricPredicate:
        if isinstance(other, _ExpressionCarrier) and not isinstance(other, MetricExpression):
            _stage_error(
                "Comparison requires expressions from the same stage.",
                received=f"{type(self).__name__}[{self._role}] and {type(other).__name__}[{other._role}]",
            )
        return MetricExpression(
            BinaryOperator(operator.gt, self._recipe, _parameter(other)),
            _merge(self, other),
            "boolean",
        )

    def __ge__(self, other: MetricExpression[MetricRole] | LiteralValue) -> MetricPredicate:
        if isinstance(other, _ExpressionCarrier) and not isinstance(other, MetricExpression):
            _stage_error(
                "Comparison requires expressions from the same stage.",
                received=f"{type(self).__name__}[{self._role}] and {type(other).__name__}[{other._role}]",
            )
        return MetricExpression(
            BinaryOperator(operator.ge, self._recipe, _parameter(other)),
            _merge(self, other),
            "boolean",
        )

    def __and__(self: MetricPredicate, other: MetricPredicate | bool) -> MetricPredicate:
        self._require("boolean")
        return MetricExpression(
            BinaryOperator(operator.and_, self._recipe, _parameter(other)),
            _merge(self, other),
            "boolean",
        )

    def __or__(self: MetricPredicate, other: MetricPredicate | bool) -> MetricPredicate:
        self._require("boolean")
        return MetricExpression(
            BinaryOperator(operator.or_, self._recipe, _parameter(other)),
            _merge(self, other),
            "boolean",
        )

    def __rand__(self: MetricPredicate, other: bool) -> MetricPredicate:
        self._require("boolean")
        return MetricExpression(
            BinaryOperator(operator.and_, _parameter(other), self._recipe),
            _merge(self, other),
            "boolean",
        )

    def __ror__(self: MetricPredicate, other: bool) -> MetricPredicate:
        self._require("boolean")
        return MetricExpression(
            BinaryOperator(operator.or_, _parameter(other), self._recipe),
            _merge(self, other),
            "boolean",
        )

    def __invert__(self: MetricPredicate) -> MetricPredicate:
        self._require("boolean")
        return MetricExpression(
            UnaryOperator(operator.invert, self._recipe), self._context, "boolean"
        )

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
        from inspect import unwrap

        import ibis

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

    def per(
        self: NumericMetric,
        by: FieldExpression
        | tuple[FieldExpression | Grouping, ...]
        | Grouping
        | PopulationParameter
        | Grain,
    ) -> _MetricSamples:
        """Capture the internal sample domain before its final reduction.

        Args:
            by: The explicit by captured for this operation.

        Returns:
            An internal sample value requiring a final reduction.

        Examples:
            ``revenue.per(region).mean()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        from marivo._temporal import CalendarAxis

        if isinstance(by, CalendarAxis):
            raise SemanticExpressionError(
                kind=ErrorKind.INVALID_EXPRESSION_STAGE,
                message="Metric.per requires an internal sample axis.",
                expected="field or Grain sample axis",
                received="CalendarAxis",
                action='Use metric.per(mv.grain("day")) and pass CalendarAxis in observe.by.',
            )
        return _MetricSamples(self._method("per", (by,)), _merge(self, by), "samples")

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
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return MetricExpression(self._method("window", (window,)), self._context, "numeric")


DimensionExpression: TypeAlias = RowExpression[Literal["dimension"]]
TimeExpression: TypeAlias = RowExpression[Literal["time"]]
NumericRow: TypeAlias = RowExpression[Literal["numeric"]]
RowPredicate: TypeAlias = RowExpression[Literal["boolean"]]
NumericMetric: TypeAlias = MetricExpression[Literal["numeric"]]
MetricPredicate: TypeAlias = MetricExpression[Literal["boolean"]]
FieldExpression: TypeAlias = DimensionExpression | TimeExpression | NumericRow


class _MetricSamples(_ExpressionCarrier):
    """The existing intermediate sample role, requiring a final reduction."""

    def sum(self, *, nulls: NullPolicy = "ignore") -> NumericMetric:
        """Capture the sum reduction using the existing metric rules.

        Args:
            nulls: The explicit nulls captured for this operation.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``revenue.sum()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return MetricExpression(
            self._method("sum", kwargs={"nulls": nulls}), self._context, "numeric"
        )

    def mean(self, *, nulls: NullPolicy = "ignore") -> NumericMetric:
        """Capture the mean reduction using the existing metric rules.

        Args:
            nulls: The explicit nulls captured for this operation.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``revenue.mean()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return MetricExpression(
            self._method("mean", kwargs={"nulls": nulls}), self._context, "numeric"
        )

    def min(self, *, nulls: NullPolicy = "ignore") -> NumericMetric:
        """Capture the min reduction using the existing metric rules.

        Args:
            nulls: The explicit nulls captured for this operation.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``revenue.min()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return MetricExpression(
            self._method("min", kwargs={"nulls": nulls}), self._context, "numeric"
        )

    def max(self, *, nulls: NullPolicy = "ignore") -> NumericMetric:
        """Capture the max reduction using the existing metric rules.

        Args:
            nulls: The explicit nulls captured for this operation.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``revenue.max()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return MetricExpression(
            self._method("max", kwargs={"nulls": nulls}), self._context, "numeric"
        )

    def median(self, *, nulls: NullPolicy = "ignore") -> NumericMetric:
        """Capture the median reduction using the existing metric rules.

        Args:
            nulls: The explicit nulls captured for this operation.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``revenue.median()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return MetricExpression(
            self._method("median", kwargs={"nulls": nulls}), self._context, "numeric"
        )

    def count(self, *, nulls: NullPolicy = "ignore") -> NumericMetric:
        """Capture the count reduction using the existing metric rules.

        Args:
            nulls: The explicit nulls captured for this operation.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``revenue.count()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return MetricExpression(
            self._method("count", kwargs={"nulls": nulls}), self._context, "numeric"
        )

    def count_distinct(self, *, nulls: NullPolicy = "ignore") -> NumericMetric:
        """Capture the count_distinct reduction using the existing metric rules.

        Args:
            nulls: The explicit nulls captured for this operation.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``revenue.count_distinct()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return MetricExpression(
            self._method("count_distinct", kwargs={"nulls": nulls}), self._context, "numeric"
        )

    def approx_nunique(self, *, nulls: NullPolicy = "ignore") -> NumericMetric:
        """Capture the approx_nunique reduction using the existing metric rules.

        Args:
            nulls: The explicit nulls captured for this operation.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``revenue.approx_nunique()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return MetricExpression(
            self._method("approx_nunique", kwargs={"nulls": nulls}), self._context, "numeric"
        )

    def approx_median(self, *, nulls: NullPolicy = "ignore") -> NumericMetric:
        """Capture the approx_median reduction using the existing metric rules.

        Args:
            nulls: The explicit nulls captured for this operation.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``revenue.approx_median()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return MetricExpression(
            self._method("approx_median", kwargs={"nulls": nulls}), self._context, "numeric"
        )

    def first(
        self,
        *,
        order_by: TimeExpression | None = None,
        include_null: bool = False,
        nulls: NullPolicy = "ignore",
    ) -> NumericMetric:
        """Capture the first reduction using the existing metric rules.

        Args:
            order_by: The explicit order by captured for this operation.
            include_null: The explicit include null captured for this operation.
            nulls: The explicit nulls captured for this operation.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``revenue.first()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        kwargs: dict[str, object] = {"include_null": include_null, "nulls": nulls}
        if order_by is not None:
            kwargs["order_by"] = order_by
        return MetricExpression(
            self._method("first", kwargs=kwargs), _merge(self, order_by), "numeric"
        )

    def last(
        self,
        *,
        order_by: TimeExpression | None = None,
        include_null: bool = False,
        nulls: NullPolicy = "ignore",
    ) -> NumericMetric:
        """Capture the last reduction using the existing metric rules.

        Args:
            order_by: The explicit order by captured for this operation.
            include_null: The explicit include null captured for this operation.
            nulls: The explicit nulls captured for this operation.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``revenue.last()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        kwargs: dict[str, object] = {"include_null": include_null, "nulls": nulls}
        if order_by is not None:
            kwargs["order_by"] = order_by
        return MetricExpression(
            self._method("last", kwargs=kwargs), _merge(self, order_by), "numeric"
        )

    def weighted_mean(
        self, *, weight: NumericMetric, nulls: NullPolicy = "ignore"
    ) -> NumericMetric:
        """Capture the weighted_mean reduction using the existing metric rules.

        Args:
            weight: The explicit weight captured for this operation.
            nulls: The explicit nulls captured for this operation.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``amount.weighted_mean(weight=quantity)``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return MetricExpression(
            self._method("weighted_mean", kwargs={"weight": weight, "nulls": nulls}),
            _merge(self, weight),
            "numeric",
        )

    def quantile(self, q: float, *, nulls: NullPolicy = "ignore") -> NumericMetric:
        """Capture the quantile reduction using the existing metric rules.

        Args:
            q: The explicit q captured for this operation.
            nulls: The explicit nulls captured for this operation.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``amount.quantile(0.95)``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return MetricExpression(
            self._method("quantile", (q,), {"nulls": nulls}), self._context, "numeric"
        )

    def approx_quantile(self, q: float, *, nulls: NullPolicy = "ignore") -> NumericMetric:
        """Capture the approx_quantile reduction using the existing metric rules.

        Args:
            q: The explicit q captured for this operation.
            nulls: The explicit nulls captured for this operation.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``amount.approx_quantile(0.95)``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return MetricExpression(
            self._method("approx_quantile", (q,), {"nulls": nulls}), self._context, "numeric"
        )


def _as_deferred(value: _ExpressionCarrier) -> Deferred:
    """Expose only the controlled internal recipe to existing semantic owners."""
    return Deferred(value._recipe)
