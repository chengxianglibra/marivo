"""Expand published Metrics in a captured Catalog scope."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import fields as dataclass_fields
from dataclasses import is_dataclass
from dataclasses import replace as dataclass_replace
from typing import TYPE_CHECKING, TypeVar, cast

from ibis.common.deferred import Deferred

from marivo._definition.expressions import (
    DeclaredMetricRef,
    MetricNode,
)
from marivo._definition.graph import (
    DefinitionClosure,
    walk,
)
from marivo._definition.types import (
    DefinitionGraphError,
    ExactRef,
)
from marivo._kernel.refs import (
    SemanticKind,
)
from marivo.semantic._consumption import bind_metric
from marivo.semantic._deferred_semantics import consume_metric
from marivo.semantic._expression_values import MetricExpression
from marivo.semantic.catalog_model.objects import lookup_state
from marivo.semantic.errors import SemanticExpressionError
from marivo.semantic.state_consumption import consume_metric_in_state

if TYPE_CHECKING:
    from marivo.semantic._expression_values import NumericMetric
    from marivo.semantic.catalog import SemanticCatalog


def catalog_metric_definition(catalog: SemanticCatalog, target: ExactRef) -> MetricNode | None:
    """The stored body of the published Metric at ``target``'s path; None when absent.

    Lookup is by kind and path in this scope: a reference built against an
    earlier same-path definition reaches the current one.
    """
    declared = lookup_state(catalog).registry.declared_metrics.get(target.ref.path)
    if declared is None or target.ref.kind is not SemanticKind.METRIC:
        return None

    return consume_metric(declared).node


_RebindT = TypeVar("_RebindT")


def _rebind_declared(
    value: _RebindT,
    current: Callable[[DeclaredMetricRef], DeclaredMetricRef],
    memo: dict[int, object],
) -> _RebindT:
    """Rebuild ``value`` with every declared Metric reference rebound by ``current``.

    Unchanged subgraphs are returned as the same objects, so shared nodes stay
    shared; the input graph is never mutated.
    """
    if id(value) in memo:
        return cast("_RebindT", memo[id(value)])  # memo stores the rebuilt same-type value
    result: object
    if isinstance(value, DeclaredMetricRef):
        result = current(value)
    elif isinstance(value, tuple):
        items = tuple(_rebind_declared(item, current, memo) for item in value)
        result = items if any(a is not b for a, b in zip(items, value, strict=True)) else value
    elif is_dataclass(value) and not isinstance(value, (type, ExactRef)):
        changes = {
            item.name: rebuilt
            for item in dataclass_fields(value)
            if (rebuilt := _rebind_declared(getattr(value, item.name), current, memo))
            is not getattr(value, item.name)
        }
        result = dataclass_replace(value, **changes) if changes else value
    else:
        result = value
    memo[id(value)] = result
    return cast("_RebindT", result)  # rebinding preserves each value's type


def catalog_closure(catalog: SemanticCatalog, value: Deferred | NumericMetric) -> DefinitionClosure:
    """Expand ``value`` through the published Metrics of ``catalog`` into one graph.

    Declared Metric references resolve by kind and path in this scope; a
    reference built against an earlier same-path definition is rebound to the
    current definition and version, so the closure describes what is actually
    consumed. ``value`` itself is unchanged. Each ``where`` over a published
    Metric is checked again against its current body. Cycles, unpublished
    Metrics and filters over a now-composite Metric fail with a structured
    error. No source is read.
    """
    state = lookup_state(catalog)

    def expand(target: ExactRef) -> MetricNode | None:
        return catalog_metric_definition(catalog, target)

    bound = bind_metric(value, catalog) if isinstance(value, MetricExpression) else value
    root = consume_metric_in_state(bound, state).node
    try:
        closure = walk((root,), resolve=expand)
    except DefinitionGraphError as error:
        raise SemanticExpressionError.from_definition(error, operation="Metric closure") from error
    return closure
