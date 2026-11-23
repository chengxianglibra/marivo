"""Shared immutable capture and expression validation."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import (
    ClassVar,
    Generic,
    Literal,
    NoReturn,
    TypeAlias,
    TypeVar,
)

from ibis.common.deferred import Attr, BinaryOperator, Call, Deferred, Just, Resolver
from ibis.common.deferred import Sequence as RecipeSequence
from typing_extensions import override

from marivo._definition.deferred import (
    NamedResolver,
    RelationshipParameter,
    inspect_capture,
    resolver_of,
)
from marivo._definition.relations import PopulationParameter
from marivo._definition.types import (
    DefinitionGraphError,
    InputContext,
    check_literal,
    merge_input_contexts,
)
from marivo._definition.windows import AllHistory, Since, ToDate, TrailingDuration, TrailingPeriods
from marivo._kernel.grouping import Grouping
from marivo._kernel.input_context import captured_context
from marivo._kernel.paths import _PathValue as Path
from marivo._kernel.paths import path_parameter, relationship_binding
from marivo._kernel.render import Card, RenderableResult
from marivo._kernel.temporal import Grain, InstantAnchor, TimeScope
from marivo.semantic._deferred_semantics import (
    capture_source_time,
    consume_metric,
    validate_closed_time_operation,
)
from marivo.semantic._formula_display import render_recipe_formula
from marivo.semantic._metric_time_disclosure import add_metric_time
from marivo.semantic.errors import ErrorKind, SemanticExpressionError, _error, _stage_error
from marivo.semantic.expressions.inputs import (
    CatalogInput,
    CatalogRelationshipInput,
    RecipeInput,
    _OmittedTime,
    expression_recipe,
)
from marivo.semantic.metric_time_bindings import metric_time_bindings

RowRole: TypeAlias = Literal["dimension", "time", "numeric", "boolean"]

MetricRole: TypeAlias = Literal["numeric", "boolean"]

_RowRoleT = TypeVar("_RowRoleT", bound=RowRole, covariant=True)

_MetricRoleT = TypeVar("_MetricRoleT", bound=MetricRole, covariant=True)

NumericLiteral: TypeAlias = int | float | Decimal

Window: TypeAlias = AllHistory | Since | ToDate | TrailingDuration | TrailingPeriods


class _GrainOmitted:
    @override
    def __repr__(self) -> str:
        return "omitted"


_GRAIN_OMITTED = _GrainOmitted()


def _source_arguments(receiver: Resolver, time: object, **kwargs: object) -> dict[str, object]:

    if not isinstance(time, _OmittedTime):
        capture_source_time(receiver, _parameter(time))
        kwargs["time"] = time
    elif (default := capture_source_time(receiver, None)) is not None:
        kwargs["time"] = Deferred(default)
    return kwargs


def _parameter(value: object) -> Resolver:

    if isinstance(value, _ExpressionCarrier):
        return expression_recipe(value)
    if isinstance(value, Deferred):
        return resolver_of(value)
    if isinstance(value, Path):
        return Just(path_parameter(value))
    if isinstance(value, CatalogRelationshipInput):
        return NamedResolver(relationship_binding(value).ref, Just(relationship_binding(value)))
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

    if isinstance(value, (Grouping, Path, PopulationParameter)):
        return (captured_context(value),)
    if isinstance(value, CatalogInput):
        return (captured_context(value),)
    if isinstance(value, InputContext):
        return (value,)
    if isinstance(value, Deferred):
        return (inspect_capture(value).context,)
    if isinstance(value, _ExpressionCarrier):
        return (captured_context(value),)
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


class _ExpressionCarrier(RenderableResult, RecipeInput):
    __slots__ = ("_captured_context", "_captured_recipe", "_captured_role")
    _carrier_stage: ClassVar[Literal["row", "metric", "sample"] | None] = None
    _captured_recipe: Resolver
    _captured_context: InputContext
    _captured_role: str
    # Declaring identity equality keeps carriers without predicate operators unhashable.
    __eq__ = object.__eq__

    def __init__(self, recipe: Resolver, context: InputContext, role: str) -> None:
        object.__setattr__(self, "_captured_recipe", recipe)
        object.__setattr__(self, "_captured_context", context)
        object.__setattr__(self, "_captured_role", role)
        try:
            inspection = inspect_capture(Deferred(recipe))
            if not inspection.symbols and self._carrier_stage in ("metric", "sample"):
                validate_closed_time_operation(recipe)
            object.__setattr__(
                self, "_captured_context", merge_input_contexts(context, inspection.context)
            )
        except DefinitionGraphError as error:
            raise SemanticExpressionError.from_definition(
                error, operation="expression capture"
            ) from error

    @override
    def __setattr__(self, name: str, value: object) -> NoReturn:
        raise AttributeError("Semantic expressions are immutable.")

    @property
    @override
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

    def _require_numeric_operand(self, other: object) -> None:
        self._require("numeric")
        stage = "row" if self._carrier_stage == "row" else "metric"
        if isinstance(other, _ExpressionCarrier) and (
            expression_stage(other) != stage or expression_role(other) != "numeric"
        ):
            _stage_error(
                "Arithmetic requires numeric expressions from the same stage.",
                received=f"{type(self).__name__}[{self._role}] and {type(other).__name__}[{expression_role(other)}]",
            )

    def _arithmetic_recipe(
        self,
        other: object,
        operation: Callable[[object, object], object],
        *,
        reflected: bool = False,
    ) -> Resolver:
        self._require_numeric_operand(other)
        if isinstance(other, bool):
            _error("Boolean literals are not numeric operands.")
        operand = _parameter(other)
        left, right = (operand, self._recipe) if reflected else (self._recipe, operand)
        return BinaryOperator(operation, left, right)

    def _require_boolean_operand(self, other: object) -> None:
        self._require("boolean")
        if isinstance(other, bool):
            return
        if (
            isinstance(other, _ExpressionCarrier)
            and expression_role(other) == "boolean"
            and self._carrier_stage in ("row", "metric")
            and self._carrier_stage == expression_stage(other)
        ):
            return
        stage = "row" if self._carrier_stage == "row" else "Metric"
        raise SemanticExpressionError(
            kind=ErrorKind.INVALID_EXPRESSION_STAGE,
            message="Boolean composition requires predicates from the same stage or bool literals.",
            expected=f"a {stage}-stage predicate or bool literal",
            received=(
                f"{type(other).__name__}[{expression_role(other)}]"
                if isinstance(other, _ExpressionCarrier)
                else type(other).__name__
            ),
            action=(
                "Pass where=(scope, predicate) to session.observe to combine a report TimeScope "
                "with a row predicate. TimeScope does not support & or |."
                if isinstance(other, TimeScope) and self._carrier_stage == "row"
                else f"Combine {stage}-stage predicates or bool literals with & or |; "
                "keep report TimeScopes separate in session.observe."
            ),
            help_target="predicate",
        )

    def _method(
        self, name: str, args: tuple[object, ...] = (), kwargs: Mapping[str, object] | None = None
    ) -> Resolver:
        parameters = {
            key: value
            for key, value in (kwargs or {}).items()
            if not (key == "where" and value is None)
            and not (key == "nulls" and type(value) is str and value == "ignore")
            and not (key == "include_null" and value is False)
        }
        return Call(
            Attr(self._recipe, name),
            *(_parameter(value) for value in args),
            **{key: _parameter(value) for key, value in parameters.items()},
        )

    @override
    def _repr_identity(self) -> str:
        return f"{type(self).__name__} {self._role} expression"

    @override
    def _card(self) -> Card:
        inspection = inspect_capture(Deferred(self._recipe))
        card = (
            Card(identity=self._repr_identity(), available=(".show()",))
            .field("role", self._role)
            .field("formula", render_recipe_formula(self._recipe)[:400])
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
        if self._carrier_stage == "metric" and self._role == "numeric":
            if inspection.symbols:
                card.field("report_time", "unknown: resolve the authoring symbols in a Catalog")
            else:
                try:
                    facts = consume_metric(self)
                except SemanticExpressionError as error:
                    if error.details.get("defer_to_scope") or error.kind in {
                        ErrorKind.CROSS_ROOT_ROW_EXPRESSION,
                        ErrorKind.INCOMMENSURABLE_LINEAR_UNITS,
                        ErrorKind.MISSING_SUM_PERMISSION,
                        ErrorKind.INVALID_WHERE_SCOPE,
                        ErrorKind.INVALID_TIME_PATH,
                    }:
                        card.field(
                            "report_time",
                            f"unknown: receiving scope needed ({error.kind})",
                        )
                    else:
                        card.field(
                            "report_time",
                            f"unknown: captured expression inspection failed ({error.kind}): "
                            f"{error.message}",
                        )
                    if error.repair is not None:
                        card.field("time_inspection_repair", error.repair.action)
                else:
                    add_metric_time(
                        card,
                        metric_time_bindings(facts),
                        recovery="pass max_output_bytes=None to show all time bindings",
                    )
        return card

    @override
    def __repr__(self) -> str:
        return f"{self._repr_identity()} (use .show())"


def expression_stage(value: _ExpressionCarrier) -> Literal["row", "metric", "sample"] | None:
    return value._carrier_stage  # noqa: SLF001 - Restrict cross-stage composition at the carrier boundary.


def expression_role(value: _ExpressionCarrier) -> str:
    """Read the role checked when the expression was captured."""
    return value._role  # noqa: SLF001 - Role inspection for typed Semantic consumers.


class _RowCarrier(_ExpressionCarrier, Generic[_RowRoleT]):
    """Identify row inputs and their role at the Metric boundary."""

    _carrier_stage = "row"
