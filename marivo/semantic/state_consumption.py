"""Bind a recipe through the captured published definitions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING

from ibis.common.deferred import (
    Attr,
    BinaryOperator,
    Call,
    Deferred,
    Just,
    Resolver,
    Sequence,
    UnaryOperator,
)

from marivo._definition.deferred import (
    CapturedDefaultTimeResolver,
    DeclarationResolver,
    NamedResolver,
    RescaleResolver,
    SemanticInput,
    SemanticResolver,
    resolver_of,
)
from marivo._definition.types import (
    SumNone,
)
from marivo._kernel.paths import relationship_binding
from marivo.semantic._deferred_facts import MetricFacts, _fail
from marivo.semantic._deferred_semantics import _inspect, consume_metric
from marivo.semantic.definition_resolution import _Resolver, author_value
from marivo.semantic.errors import ErrorKind
from marivo.semantic.expressions.objects import (
    _Entity,
    _Relationship,
    entity_exact,
    expression_default_time,
)

if TYPE_CHECKING:
    from marivo.semantic.compiled_types import CompiledSemanticState


def _bind_declaration(
    recipe: DeclarationResolver,
    active: frozenset[str],
    state: CompiledSemanticState,
    resolver: _Resolver,
    bind: Callable[[Resolver, frozenset[str]], Resolver],
) -> Resolver:
    declaration = recipe.declaration
    if declaration.reference is not None and declaration.kind == "metric":
        target = declaration.reference.ref
        if target.path in active:
            _fail(
                "Published Metric dependency is cyclic.",
                expected="an acyclic declaration closure",
                received=target.key,
            )
        current = state.registry.declared_metrics.get(target.path)
        if current is None:
            _fail(
                "Published Metric is absent from this consumption scope.",
                kind=ErrorKind.MISSING_METRIC_REF,
                expected="a Metric in the frozen catalog",
                received=target.key,
            )
        captured = resolver_of(current)
        assert isinstance(captured, DeclarationResolver)
        return DeclarationResolver(
            bind(captured.recipe, active | {target.path}),
            replace(
                captured.declaration,
                reference=resolver.exact(target),
                domain=declaration.domain,
            ),
        )
    return DeclarationResolver(bind(recipe.recipe, active), declaration)


def consume_metric_in_state(value: Deferred, state: CompiledSemanticState) -> MetricFacts:
    """Consume published references through the consumer's frozen catalog scope.

    Captured local expressions stay immutable. Only published kind/path lookups
    select the scope's completed definition; its body is not executed or imported.
    """

    resolver = _Resolver(state)

    def bind(recipe: Resolver, active: frozenset[str]) -> Resolver:
        if isinstance(recipe, CapturedDefaultTimeResolver) and recipe.field is not None:
            target = recipe.field.ref
            if target not in state.definitions:
                _fail(
                    "Captured contribution time is absent from the receiving scope.",
                    kind=ErrorKind.INVALID_TIME_PATH,
                    expected=target.key,
                    received="no TimeDimension at that identity",
                    action="Recreate the reduction from the receiving Catalog.",
                )
            return CapturedDefaultTimeResolver(resolver.exact(target))
        if isinstance(recipe, NamedResolver):
            named_value = author_value(resolver.value(recipe.reference.ref))
            if isinstance(named_value, Deferred):
                return bind(resolver_of(named_value), active)
            if isinstance(named_value, _Entity):
                return SemanticResolver(
                    SemanticInput(
                        entity_exact(named_value),
                        entity_exact(named_value),
                        "1",
                        SumNone(),
                        default_time=expression_default_time(named_value),
                    )
                )
            if isinstance(named_value, _Relationship):
                return Just(relationship_binding(named_value))
            _fail(
                "Named input has an invalid computation role.",
                expected="an Entity, field, Metric, or Relationship",
                received=type(named_value).__name__,
            )
        if isinstance(recipe, RescaleResolver):
            return RescaleResolver(bind(recipe.recipe, active), recipe.divisor, recipe.unit)
        if isinstance(recipe, DeclarationResolver):
            return _bind_declaration(recipe, active, state, resolver, bind)
        if isinstance(recipe, BinaryOperator):
            return BinaryOperator(
                recipe.func, bind(recipe.left, active), bind(recipe.right, active)
            )
        if isinstance(recipe, UnaryOperator):
            return UnaryOperator(recipe.func, bind(recipe.arg, active))
        if isinstance(recipe, Attr):
            return Attr(bind(recipe.obj, active), recipe.name)
        if isinstance(recipe, Call):
            return Call(
                bind(recipe.func, active),
                *(bind(arg, active) for arg in recipe.args),
                **{key: bind(arg, active) for key, arg in recipe.kwargs.items()},
            )
        if isinstance(recipe, Sequence):
            return Sequence(tuple(bind(item, active) for item in recipe.values))
        return recipe

    original = _inspect(value)
    return consume_metric(Deferred(bind(original.recipe, frozenset())))
