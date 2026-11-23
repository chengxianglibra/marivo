"""Validate captured sample shapes before resolving semantic fields."""

from __future__ import annotations

from ibis.common.deferred import Attr, Call, Just, Resolver, Sequence

from marivo._definition.deferred_values import (
    NamedResolver,
    SemanticResolver,
    attribute_name,
    resolver_of,
)
from marivo._definition.grouping import grouping_binding
from marivo._definition.parameter_recipes import (
    IdentityParameter,
    MembershipParameter,
    ParameterResolver,
    parameter_role,
)
from marivo._definition.population_types import MembershipBinding
from marivo._definition.types import AxisBinding
from marivo._kernel.errors import invalid
from marivo._kernel.grouping import Grouping
from marivo._kernel.refs import SemanticKind
from marivo._kernel.symbolic import SymbolResolver
from marivo._kernel.temporal import Grain


def captured_field_kind(recipe: Resolver) -> SemanticKind | None:
    """Read a field role only from native leaves and admitted field modifiers."""
    if isinstance(recipe, NamedResolver):
        return captured_field_kind(recipe.recipe)
    if isinstance(recipe, SymbolResolver):
        return (
            recipe.reference.kind
            if recipe.reference.kind
            in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION, SemanticKind.MEASURE}
            else None
        )
    if isinstance(recipe, SemanticResolver):
        return (
            recipe.input.reference.ref.kind
            if recipe.input.reference.ref.kind
            in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION, SemanticKind.MEASURE}
            else None
        )
    if (
        isinstance(recipe, Call)
        and isinstance(recipe.func, Attr)
        and attribute_name(recipe.func) in {"via", "at", "name"}
    ):
        return captured_field_kind(recipe.func.obj)
    return None


def captured_scalar_path(recipe: Resolver) -> bool:
    if isinstance(recipe, NamedResolver):
        return captured_scalar_path(recipe.recipe)
    if isinstance(recipe, SemanticResolver):
        return recipe.input.field_read is not None and bool(recipe.input.field_read.path)
    if isinstance(recipe, Call) and isinstance(recipe.func, Attr):
        name = attribute_name(recipe.func)
        return name == "via" or (name in {"at", "name"} and captured_scalar_path(recipe.func.obj))
    return False


def capture_sample(recipe: Resolver, path: tuple[str, ...]) -> None:
    """Validate the finite sample-parameter grammar without resolving fields."""
    if isinstance(recipe, Just) and isinstance(recipe.value, (Grain,)):
        return
    if isinstance(recipe, Sequence):
        if recipe.typ is not tuple or not recipe.values:
            invalid("per requires a nonempty tuple of fields or grouping parameters.", path=path)
        members = recipe.values
    else:
        members = (recipe,)
    population = parameter_role(members[0]) == "population"
    for index, member in enumerate(members):
        role = parameter_role(member)
        if index == 0 and population:
            continue
        binding = (
            grouping_binding(member.value)
            if isinstance(member, Just) and isinstance(member.value, Grouping)
            else None
        )
        membership = (
            isinstance(member, ParameterResolver)
            and isinstance(member.parameter, MembershipParameter)
        ) or isinstance(binding, MembershipBinding)
        if membership:
            if not population:
                if isinstance(member, ParameterResolver) and isinstance(
                    member.parameter, MembershipParameter
                ):
                    if any(
                        captured_field_kind(resolver_of(field))
                        not in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION}
                        or captured_scalar_path(resolver_of(field))
                        for field in member.parameter.fields
                    ):
                        invalid(
                            "Internal membership samples require native Dimension or TimeDimension fields.",
                            path=path,
                        )
                elif isinstance(binding, MembershipBinding) and any(
                    read.field.ref.kind not in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION}
                    or read.path
                    for read in binding.fields
                ):
                    invalid(
                        "Internal membership samples require native Dimension or TimeDimension fields.",
                        path=path,
                    )
            continue
        if population:
            invalid(
                "A sample Population can be followed only by membership rules.",
                path=(*path, f"item:{index}"),
            )
        if isinstance(member, ParameterResolver) and isinstance(
            member.parameter, IdentityParameter
        ):
            invalid(
                "per requires native Dimension or TimeDimension coordinates, not an identity grouping.",
                path=path,
            )
        if binding is not None:
            if (
                not isinstance(binding, AxisBinding)
                or binding.role != "field"
                or binding.path
                or binding.field is None
                or binding.field.ref.kind
                not in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION}
            ):
                invalid(
                    "per requires native Dimension or TimeDimension coordinate groupings.",
                    path=path,
                )
            continue
        if role == "grouping":
            continue
        if captured_scalar_path(member) or captured_field_kind(member) not in {
            SemanticKind.DIMENSION,
            SemanticKind.TIME_DIMENSION,
        }:
            invalid(
                "per requires Dimension or TimeDimension fields, grouping rules, or a Population.",
                path=(*path, f"item:{index}"),
            )
