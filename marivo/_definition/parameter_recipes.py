"""Shape checks for the finite symbolic Population and path recipe grammar."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Literal, NoReturn, TypeAlias

from ibis.common.deferred import (
    Attr,
    BinaryOperator,
    Call,
    Deferred,
    Item,
    Just,
    Resolver,
    Sequence,
    UnaryOperator,
)
from typing_extensions import override

from marivo._definition.deferred_values import (
    DeclarationResolver,
    PopulationContainsResolver,
    PopulationCountResolver,
    RescaleResolver,
    _FrozenResolver,
    captured_relationship,
    resolver_of,
)
from marivo._definition.population_types import PopulationParameter
from marivo._definition.types import (
    CoordinateValue,
    check_identity_anchor,
    check_literal,
)
from marivo._kernel.errors import invalid
from marivo._kernel.grouping import Grouping
from marivo._kernel.input_context import InputContext
from marivo._kernel.paths import PathParameter, RelationshipStepParameter, _PathValue
from marivo._kernel.refs import ExactRef, SemanticKind
from marivo._kernel.symbolic import SymbolResolver, native_symbol_ref
from marivo._kernel.temporal import CalendarAxis, TimeScope
from marivo._kernel.time_types import InstantAnchor
from marivo._kernel.validation import is_instance

PathValue: TypeAlias = Deferred | PathParameter
AxisValue: TypeAlias = Deferred | Grouping
FactorValue: TypeAlias = Deferred | PopulationParameter | CalendarAxis | TimeScope


@dataclass(frozen=True, slots=True, eq=False)
class IdentityParameter:
    target: Deferred | ExactRef | PathParameter
    _context: InputContext = dataclass_field(default=InputContext(), kw_only=True)


@dataclass(frozen=True, slots=True, eq=False)
class MembershipParameter:
    fields: tuple[Deferred, ...]
    paths: tuple[PathValue, ...]
    unmatched: Literal["exclude", "null"]
    _context: InputContext = dataclass_field(default=InputContext(), kw_only=True)


@dataclass(frozen=True, slots=True, eq=False)
class CoordinatesParameter:
    by: tuple[AxisValue, ...]
    keys: tuple[tuple[CoordinateValue, ...], ...]
    _context: InputContext = dataclass_field(default=InputContext(), kw_only=True)
    _identity_keys: tuple[tuple[str, tuple[str, ...]], ...] = dataclass_field(
        default=(), kw_only=True
    )


@dataclass(frozen=True, slots=True, eq=False)
class ProductParameter:
    factors: tuple[FactorValue, ...]
    _context: InputContext = dataclass_field(default=InputContext(), kw_only=True)


@dataclass(frozen=True, slots=True, eq=False)
class ContainsParameter:
    population: Deferred
    _context: InputContext = dataclass_field(default=InputContext(), kw_only=True)


@dataclass(frozen=True, slots=True, eq=False)
class PathRecipe:
    steps: tuple[Deferred | RelationshipStepParameter, ...]
    source_at: InstantAnchor | None
    _context: InputContext = dataclass_field(default=InputContext(), kw_only=True)


Parameter: TypeAlias = (
    IdentityParameter
    | MembershipParameter
    | CoordinatesParameter
    | ProductParameter
    | ContainsParameter
    | PathRecipe
)
ParameterRole: TypeAlias = Literal["population", "grouping", "axes", "predicate", "metric", "path"]


def _parameter_role(value: Parameter) -> ParameterRole:
    if isinstance(value, PathRecipe):
        return "path"
    if isinstance(value, (IdentityParameter, MembershipParameter)):
        return "grouping"
    if isinstance(value, ContainsParameter):
        return "predicate"
    return "population"


def parameter_role(recipe: Resolver) -> ParameterRole | None:
    """Identify finite helper results without treating count as a Population."""
    if isinstance(recipe, ParameterResolver):
        return _parameter_role(recipe.parameter)
    if isinstance(recipe, PopulationCountResolver):
        return "metric"
    if isinstance(recipe, PopulationContainsResolver):
        return "predicate"
    if isinstance(recipe, DeclarationResolver) and recipe.declaration.kind == "metric":
        return "metric"
    if isinstance(recipe, Just):
        if isinstance(recipe.value, (PathParameter, _PathValue)):
            return "path"
        if isinstance(recipe.value, PopulationParameter):
            return "population"
        if isinstance(recipe.value, Grouping):
            return "grouping"
    if (
        isinstance(recipe, Attr)
        and parameter_role(recipe.obj) == "population"
        and isinstance(recipe.name, Just)
        and recipe.name.value == "axes"
    ):
        return "axes"
    if isinstance(recipe, Item) and parameter_role(recipe.obj) == "axes":
        return "grouping"
    if (
        isinstance(recipe, Call)
        and isinstance(recipe.func, Attr)
        and parameter_role(recipe.func.obj) == "population"
        and isinstance(recipe.func.name, Just)
    ):
        if recipe.func.name.value == "count":
            return "metric"
        if recipe.func.name.value in {
            "project",
            "union",
            "intersection",
            "difference",
            "classify",
            "identities",
            "image",
            "preimage",
            "where",
        }:
            return "population"
    return None


def validate_parameter_call(recipe: Resolver, path: tuple[str, ...] = ()) -> None:
    """Check known helper roles and required argument shapes at capture."""
    if isinstance(recipe, Item):
        role = parameter_role(recipe.obj)
        if role is not None and role != "axes":
            invalid("only Population.axes supports a constant integer selection", path=path)
        return
    if not isinstance(recipe, (Attr, Call)):
        return
    function = recipe.func if isinstance(recipe, Call) else recipe
    if not isinstance(function, Attr):
        return
    role = parameter_role(function.obj)
    if role in {"grouping", "axes"}:
        invalid(
            "Grouping parameters have no callable methods; construct Population helpers at module scope",
            path=path,
        )
    if role != "population":
        return
    if not isinstance(function.name, Just) or not isinstance(function.name.value, str):
        invalid("Population parameters require a named finite operation", path=path)
    name = function.name.value
    if not isinstance(recipe, Call):
        if name != "axes":
            invalid("Population parameters expose only axes as an attribute", path=path)
        return
    _validate_population_method(recipe, name, path)


def _validate_population_method(recipe: Call, name: str, path: tuple[str, ...]) -> None:
    required = {
        "project": {"by"},
        "classify": {"by"},
        "identities": {"on"},
        "image": {"via"},
        "preimage": {"via"},
    }.get(name, set())
    if required - set(recipe.kwargs):
        invalid(
            f"Population.{name} requires explicit {', '.join(sorted(required))} parameters",
            path=path,
        )
    if name in {"project", "classify"}:
        by = recipe.kwargs["by"]
        if isinstance(by, Sequence):
            for index, item in enumerate(by.values):
                _validate_field_recipe(item, (*path, "by", str(index)), grouping=True)
        elif parameter_role(by) != "axes":
            invalid(
                f"Population.{name} requires by=(field_or_grouping, ...) or Population.axes",
                path=path,
            )
    if name in {"image", "preimage"} and parameter_role(recipe.kwargs["via"]) != "path":
        invalid(f"Population.{name} requires via=mv.path(...)", path=path)
    on = recipe.kwargs.get("on")
    if (
        on is not None
        and parameter_role(on) != "grouping"
        and not (isinstance(on, Just) and on.value is None and name != "identities")
    ):
        invalid(f"Population.{name} requires an identity Grouping for on", path=path)


def validate_relationship_recipe(recipe: Resolver, path: tuple[str, ...]) -> None:
    if captured_relationship(recipe):
        return
    if (
        isinstance(recipe, Call)
        and isinstance(recipe.func, Attr)
        and isinstance(recipe.func.name, Just)
        and recipe.func.name.value == "at"
        and captured_relationship(recipe.func.obj)
        and len(recipe.args) == 1
        and not recipe.kwargs
        and isinstance(recipe.args[0], Just)
        and isinstance(recipe.args[0].value, InstantAnchor)
        and recipe.args[0].value.utcoffset() is not None
    ):
        return
    invalid(
        "via requires native Relationship symbols or closed Relationship values; expressions and literals cannot form a relationship path",
        path=path,
    )


def _validate_field_recipe(recipe: Resolver, path: tuple[str, ...], *, grouping: bool) -> None:
    if isinstance(recipe, SymbolResolver) and recipe.reference.kind not in {
        SemanticKind.DIMENSION,
        SemanticKind.TIME_DIMENSION,
        SemanticKind.MEASURE,
    }:
        invalid(
            "coordinate and membership fields require Dimension, TimeDimension or Measure symbols",
            path=path,
        )
    role = parameter_role(recipe)
    if role is not None and not (grouping and role == "grouping"):
        invalid(
            "coordinate and membership fields require field inputs with the matching semantic role",
            path=path,
        )


def has_parameters(value: object) -> bool:
    """Identify unbound dependencies without asking Deferred to evaluate."""
    if isinstance(value, Deferred):
        return has_parameters(resolver_of(value))
    if isinstance(value, (SymbolResolver, ParameterResolver)):
        return True
    if isinstance(value, (DeclarationResolver, RescaleResolver)):
        return has_parameters(value.recipe)
    if isinstance(value, (tuple, list)):
        return any(has_parameters(item) for item in value)
    if isinstance(value, Attr):
        return has_parameters(value.obj)
    if isinstance(value, Item):
        return has_parameters(value.obj) or has_parameters(value.indexer)
    if isinstance(value, Call):
        return has_parameters(value.func) or any(
            has_parameters(item) for item in (*value.args, *value.kwargs.values())
        )
    if isinstance(value, Sequence):
        return any(has_parameters(item) for item in value.values)
    if isinstance(value, BinaryOperator):
        return has_parameters(value.left) or has_parameters(value.right)
    if isinstance(value, UnaryOperator):
        return has_parameters(value.arg)
    if isinstance(value, Just):
        return has_parameters(value.value)
    return False


class ParameterResolver(_FrozenResolver):
    __slots__ = ("parameter",)
    parameter: Parameter

    def __init__(self, parameter: Parameter) -> None:
        super().__init__(parameter=parameter)

    @override
    def resolve(self, context: dict[str, object]) -> NoReturn:
        invalid("Symbolic grouping and Population parameters require loader binding.")


def parameter_inputs(recipe: ParameterResolver) -> tuple[Deferred, ...]:
    value = recipe.parameter
    if isinstance(value, IdentityParameter):
        return (value.target,) if isinstance(value.target, Deferred) else ()
    if isinstance(value, PathRecipe):
        return tuple(item for item in value.steps if isinstance(item, Deferred))
    if isinstance(value, MembershipParameter):
        return (
            *value.fields,
            *(item for item in value.paths if isinstance(item, Deferred)),
        )
    if isinstance(value, CoordinatesParameter):
        return tuple(item for item in value.by if isinstance(item, Deferred))
    if isinstance(value, ProductParameter):
        return tuple(item for item in value.factors if isinstance(item, Deferred))
    return (value.population,)


def validate_parameter(
    recipe: ParameterResolver, path: tuple[str, ...] = ()
) -> tuple[Deferred, ...]:
    value = recipe.parameter
    if isinstance(value, IdentityParameter):
        if isinstance(value.target, Deferred):
            reference = native_symbol_ref(value.target)
            if (reference is None or reference.kind is not SemanticKind.ENTITY) and (
                parameter_role(resolver_of(value.target)) != "path"
            ):
                invalid("identity requires an Entity or mv.path(...)", path=path)
    elif isinstance(value, PathRecipe):
        check_identity_anchor(value.source_at)
        if not value.steps:
            invalid("mv.path requires at least one directed relationship", path=path)
        for index, step in enumerate(value.steps):
            if isinstance(step, Deferred):
                validate_relationship_recipe(resolver_of(step), (*path, "step", str(index)))
    elif isinstance(value, MembershipParameter):
        if not value.fields or value.unmatched not in ("exclude", "null"):
            invalid("membership requires fields and an explicit exclude or null policy", path=path)
        if any(
            isinstance(item, Deferred) and parameter_role(resolver_of(item)) != "path"
            for item in value.paths
        ):
            invalid("membership via requires Paths constructed with mv.path(...)", path=path)
        for index, item in enumerate(value.fields):
            _validate_field_recipe(resolver_of(item), (*path, "field", str(index)), grouping=False)
    else:
        _validate_population_parameter(value, path)
    return parameter_inputs(recipe)


def _validate_population_parameter(
    value: CoordinatesParameter | ProductParameter | ContainsParameter, path: tuple[str, ...]
) -> None:
    if isinstance(value, CoordinatesParameter):
        for index, coordinate in enumerate(value.by):
            if isinstance(coordinate, Deferred):
                _validate_field_recipe(
                    resolver_of(coordinate), (*path, "axis", str(index)), grouping=True
                )
        for key in value.keys:
            for literal in key:
                if isinstance(literal, tuple):
                    for component in literal:
                        check_literal(component, role="literal Population identity")
                else:
                    check_literal(literal, role="literal Population coordinate")
    elif isinstance(value, ProductParameter):
        if len(value.factors) < 2:
            invalid("product requires at least two factors", path=path)
        for index, factor in enumerate(value.factors):
            if isinstance(factor, Deferred) and parameter_role(resolver_of(factor)) != "population":
                invalid(
                    "product requires Population, CalendarAxis or TimeScope factors",
                    path=(*path, "factor", str(index)),
                )
    elif (
        is_instance(value, ContainsParameter)
        and parameter_role(resolver_of(value.population)) != "population"
    ):
        invalid("in_population requires a Population parameter", path=path)
