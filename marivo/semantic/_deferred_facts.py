"""Immutable facts emitted by the closed row and Metric recipe inspector."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import NoReturn, TypeAlias

from ibis.common.deferred import (
    Resolver,
)

from marivo._definition.deferred import (
    SemanticInput,
)
from marivo._definition.expressions import (
    MetricNode,
    PredicateExpression,
    RowNode,
    SampleDomainSpec,
)
from marivo._definition.types import (
    ExactRef,
    SumPermission,
)
from marivo.semantic.errors import ErrorKind, SemanticExpressionError


@dataclass(frozen=True, slots=True)
class RowFacts:
    node: RowNode
    root: ExactRef
    unit: str | None
    permission: SumPermission
    coordinates: tuple[ExactRef, ...] = ()
    numeric: bool = True
    boolean: bool = False
    field: SemanticInput | None = None
    default_time: ExactRef | None = None


@dataclass(frozen=True, slots=True)
class MetricFacts:
    node: MetricNode
    roots: tuple[ExactRef, ...]
    unit: str | None
    declared: tuple[tuple[ExactRef, MetricNode], ...] = ()
    boolean: bool = False


@dataclass(frozen=True, slots=True)
class PredicateFacts:
    node: PredicateExpression
    roots: tuple[ExactRef, ...]


@dataclass(frozen=True, slots=True)
class _Samples:
    metric: MetricFacts
    axes: SampleDomainSpec


@dataclass(frozen=True, slots=True)
class _Scalar:
    recipe: Resolver
    value: object


_Facts: TypeAlias = RowFacts | MetricFacts | _Samples | _Scalar
InspectRecipe: TypeAlias = Callable[[Resolver], _Facts]

_REDUCERS = frozenset(
    {
        "sum",
        "mean",
        "min",
        "max",
        "median",
        "quantile",
        "count",
        "count_distinct",
        "approx_nunique",
        "approx_median",
        "approx_quantile",
        "first",
        "last",
    }
)


def _fail(
    message: str,
    *,
    expected: str,
    received: str,
    kind: ErrorKind = ErrorKind.INVALID_EXPRESSION_STAGE,
    action: str | None = None,
    help_target: str = "expressions",
    scope_dependent: bool = False,
) -> NoReturn:
    raise SemanticExpressionError(
        kind=kind,
        message=message,
        expected=expected,
        received=received,
        help_target=help_target,
        details={"defer_to_scope": True} if scope_dependent else None,
        action=action
        or (
            "Declare a Measure with an explicit additivity grant before summing this expression."
            if kind is ErrorKind.MISSING_SUM_PERMISSION
            else "Use resolved semantic fields at the same stage and reduce rows before combining Metrics."
        ),
    )
