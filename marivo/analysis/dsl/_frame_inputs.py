"""Strong invocation inputs kept outside inert Population definitions."""

from __future__ import annotations

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
    DeclarationResolver,
    NamedResolver,
    PopulationContainsResolver,
    resolver_of,
)
from marivo._definition.relations import PopulationParameter
from marivo._definition.types import Owner
from marivo.analysis.core.frame_data import FrameKeyDependency
from marivo.analysis.model.frame_contracts import merge_frame_key_inputs
from marivo.semantic._expression_values import _ExpressionCarrier


def _recipe_inputs(recipe: Resolver) -> tuple[FrameKeyDependency, ...]:
    from marivo._definition.deferred import PopulationCountResolver, RescaleResolver
    from marivo.analysis.dsl.population import Population

    if isinstance(recipe, (PopulationCountResolver, PopulationContainsResolver)):
        value = recipe.population
        return value._frame_inputs if isinstance(value, Population) else ()
    if isinstance(recipe, (DeclarationResolver, NamedResolver, RescaleResolver)):
        return _recipe_inputs(recipe.recipe)
    if isinstance(recipe, Just):
        value = recipe.value
        return value._frame_inputs if isinstance(value, Population) else ()
    if isinstance(recipe, BinaryOperator):
        return (*_recipe_inputs(recipe.left), *_recipe_inputs(recipe.right))
    if isinstance(recipe, UnaryOperator):
        return _recipe_inputs(recipe.arg)
    if isinstance(recipe, Sequence):
        return tuple(item for value in recipe.values for item in _recipe_inputs(value))
    if isinstance(recipe, Attr):
        return _recipe_inputs(recipe.obj)
    if isinstance(recipe, Call):
        return tuple(
            item
            for value in (recipe.func, *recipe.args, *recipe.kwargs.values())
            for item in _recipe_inputs(value)
        )
    return ()


def collect_frame_key_inputs(
    *values: Deferred | _ExpressionCarrier | PopulationParameter | None,
    owner: Owner | None = None,
) -> tuple[FrameKeyDependency, ...]:
    """Extract original special literals before semantic consumption drops wrappers."""
    from marivo.analysis.dsl.population import Population

    groups: list[tuple[FrameKeyDependency, ...]] = []
    for value in values:
        if isinstance(value, Population):
            groups.append(value._frame_inputs)
        elif isinstance(value, _ExpressionCarrier):
            groups.append(_recipe_inputs(value._recipe))
        elif isinstance(value, Deferred):
            groups.append(_recipe_inputs(resolver_of(value)))
    return merge_frame_key_inputs(*groups, owner=owner)
