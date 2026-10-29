"""Finite native-Deferred parameter captures for grouping and populations."""

from __future__ import annotations

from collections.abc import Callable
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

from marivo._definition import population_builders as build
from marivo._definition.correspondence import ComparisonAxis
from marivo._definition.deferred import (
    DeclarationResolver,
    NamedResolver,
    PopulationContainsResolver,
    PopulationCountResolver,
    RelationshipParameter,
    RescaleResolver,
    _FrozenResolver,
    _RelationshipValue,
    inspect_capture,
    relationship_carrier,
    resolver_of,
)
from marivo._definition.expressions import PredicateExpression
from marivo._definition.grouping import _CoordinateGrouping, _MembershipGrouping
from marivo._definition.relations import (
    CalendarKeys,
    IntervalKeys,
    MembershipBinding,
    PopulationParameter,
    PopulationProgram,
)
from marivo._definition.types import (
    AxisBinding,
    CoordinateValue,
    ExactRef,
    FieldRead,
    InputContext,
    IntervalCoordinate,
    check_literal,
    invalid,
    merge_input_contexts,
)
from marivo._grouping import Grouping
from marivo._symbolic import SymbolResolver, native_symbol_ref
from marivo._temporal import (
    CalendarAxis,
    InstantAnchor,
    TimeScope,
    _SessionTimeScope,
    anchor_from_datetime,
)
from marivo._temporal_grid import instant
from marivo.refs import Ref, SemanticKind, SemanticKindTag

PathValue: TypeAlias = Deferred | RelationshipParameter
AxisValue: TypeAlias = Deferred | Grouping
FactorValue: TypeAlias = Deferred | PopulationParameter | CalendarAxis | TimeScope


@dataclass(frozen=True, slots=True, eq=False)
class IdentityParameter:
    target: Deferred | ExactRef | tuple[PathValue, ...]
    at: InstantAnchor | None
    _context: InputContext = dataclass_field(default=InputContext(), kw_only=True)


@dataclass(frozen=True, slots=True, eq=False)
class MembershipParameter:
    fields: tuple[Deferred, ...]
    paths: tuple[tuple[PathValue, ...], ...]
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


Parameter: TypeAlias = (
    IdentityParameter
    | MembershipParameter
    | CoordinatesParameter
    | ProductParameter
    | ContainsParameter
)
ParameterRole: TypeAlias = Literal["population", "grouping", "axes", "predicate", "metric"]


def parameter_role(recipe: Resolver) -> ParameterRole | None:
    """Identify finite helper results without treating count as a Population."""
    if isinstance(recipe, ParameterResolver):
        value = recipe.parameter
        if isinstance(value, (IdentityParameter, MembershipParameter)):
            return "grouping"
        if isinstance(value, ContainsParameter):
            return "predicate"
        return "population"
    if isinstance(recipe, PopulationCountResolver):
        return "metric"
    if isinstance(recipe, PopulationContainsResolver):
        return "predicate"
    if isinstance(recipe, DeclarationResolver) and recipe.declaration.kind == "metric":
        return "metric"
    if isinstance(recipe, Just):
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
    required = {"project": {"by"}, "classify": {"by"}, "image": {"via"}, "preimage": {"via"}}.get(
        name, set()
    )
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
    if name in {"image", "preimage"}:
        via = recipe.kwargs["via"]
        if not isinstance(via, Sequence) or not via.values:
            invalid(f"Population.{name} requires a nonempty directed relationship tuple", path=path)
        for index, step in enumerate(via.values):
            _validate_relationship_recipe(step, (*path, "via", str(index)))
        at = recipe.kwargs.get("at")
        if at is not None and (
            not isinstance(at, Just)
            or (at.value is not None and not isinstance(at.value, InstantAnchor))
        ):
            invalid("Population.image requires an exact InstantAnchor at parameter", path=path)


def _validate_relationship_recipe(recipe: Resolver, path: tuple[str, ...]) -> None:
    if type(recipe) is SymbolResolver and recipe.reference.kind is SemanticKind.RELATIONSHIP:
        return
    if isinstance(recipe, NamedResolver) and recipe.reference.ref.kind is SemanticKind.RELATIONSHIP:
        return
    if isinstance(recipe, Just) and isinstance(
        recipe.value, (RelationshipParameter, _RelationshipValue)
    ):
        return
    if relationship_carrier(recipe) is not None:
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

    def resolve(self, context: dict[str, object]) -> NoReturn:
        invalid("Symbolic grouping and Population parameters require loader binding.")


def _contexts(*values: object) -> InputContext:
    contexts: list[InputContext] = []
    for value in values:
        if isinstance(value, _RelationshipValue):
            contexts.append(value._context)
        elif isinstance(value, (Deferred, Resolver, Grouping, PopulationParameter)):
            contexts.append(
                value._context
                if isinstance(value, (Grouping, PopulationParameter))
                else inspect_capture(
                    value if isinstance(value, Deferred) else Deferred(value)
                ).context
            )
        elif isinstance(value, tuple):
            contexts.append(_contexts(*value))
    return merge_input_contexts(*contexts)


def _keys(*values: object) -> tuple[tuple[str, tuple[str, ...]], ...]:
    result: dict[str, tuple[str, ...]] = {}
    for value in values:
        items = (
            value._identity_keys
            if isinstance(value, (Grouping, PopulationParameter))
            else _keys(*value)
            if isinstance(value, tuple)
            else ()
        )
        for identity, keys in items:
            if identity in result and result[identity] != keys:
                invalid("Population axes have incompatible complete primary keys")
            result[identity] = keys
    return tuple(result.items())


def parameter_inputs(recipe: ParameterResolver) -> tuple[Deferred, ...]:
    value = recipe.parameter
    if isinstance(value, IdentityParameter):
        return (
            (value.target,)
            if isinstance(value.target, Deferred)
            else tuple(item for item in value.target if isinstance(item, Deferred))
            if isinstance(value.target, tuple)
            else ()
        )
    if isinstance(value, MembershipParameter):
        return (
            *value.fields,
            *(item for path in value.paths for item in path if isinstance(item, Deferred)),
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
        build.anchor(value.at)
        if isinstance(value.target, Deferred):
            reference = native_symbol_ref(value.target)
            if reference is None or reference.kind is not SemanticKind.ENTITY:
                invalid("identity requires a native Entity symbol", path=path)
        elif isinstance(value.target, tuple):
            if not value.target:
                invalid("identity requires a nonempty relationship path", path=path)
            _validate_paths((value.target,), path)
    elif isinstance(value, MembershipParameter):
        if not value.fields or value.unmatched not in ("exclude", "null"):
            invalid("membership requires fields and an explicit exclude or null policy", path=path)
        _validate_paths(value.paths, path)
        for index, item in enumerate(value.fields):
            _validate_field_recipe(resolver_of(item), (*path, "field", str(index)), grouping=False)
    elif isinstance(value, CoordinatesParameter):
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
        isinstance(value, ContainsParameter)
        and parameter_role(resolver_of(value.population)) != "population"
    ):
        invalid("in_population requires a Population parameter", path=path)
    return parameter_inputs(recipe)


def _validate_paths(paths: tuple[tuple[PathValue, ...], ...], path: tuple[str, ...]) -> None:
    for steps in paths:
        if not steps:
            invalid("relationship paths must be nonempty", path=path)
        for step in steps:
            if isinstance(step, Deferred):
                _validate_relationship_recipe(resolver_of(step), path)
            elif not isinstance(step, RelationshipParameter):
                invalid("paths require closed relationship facts", path=path)


def parameter(value: Parameter) -> Deferred:
    recipe = ParameterResolver(value)
    validate_parameter(recipe)
    from marivo._definition.deferred import inspect_capture

    for item in parameter_inputs(recipe):
        inspect_capture(item)
    return Deferred(recipe)


def interval(scope: TimeScope) -> PopulationParameter:
    if scope.kind == "absolute" and not isinstance(scope, _SessionTimeScope):
        invalid("an absolute Population interval requires a TimeScope with a frozen timezone")
    zone = scope.boundary_timezone
    start = anchor_from_datetime(instant(scope.start, zone))
    end = anchor_from_datetime(instant(scope.end, zone))
    coordinate = IntervalCoordinate(
        f"interval:{start.isoformat()}:{end.isoformat()}:{zone}", start, end, zone
    )
    return PopulationParameter(PopulationProgram(IntervalKeys(coordinate), None, (coordinate,)))


def _literal(value: Deferred) -> object:
    recipe = resolver_of(value)
    if isinstance(recipe, Just):
        return recipe.value
    if relationship_carrier(recipe) is not None:
        return recipe.value
    return value


def _entity(value: object) -> ExactRef:
    if isinstance(value, ExactRef):
        return value
    exact = getattr(value, "_exact_ref", None)
    if isinstance(exact, ExactRef):
        return exact
    invalid("symbolic identity binding requires closed Entity facts")


def _relationship(value: object) -> RelationshipParameter:
    if isinstance(value, RelationshipParameter):
        return value
    if isinstance(value, _RelationshipValue):
        return value._parameter
    invalid("symbolic path binding requires closed Relationship facts")


def bind_parameter(
    recipe: ParameterResolver,
    bind: Callable[[Deferred], Deferred],
    resolve: Callable[[Ref[SemanticKindTag]], object],
    *,
    field: Callable[[Deferred], FieldRead],
    predicate: Callable[[Deferred], PredicateExpression],
) -> Grouping | PopulationParameter | Resolver:
    value = recipe.parameter

    def path(steps: tuple[PathValue, ...]) -> tuple[RelationshipParameter, ...]:
        values: list[RelationshipParameter] = []
        for step in steps:
            if isinstance(step, Deferred):
                reference = native_symbol_ref(step)
                values.append(
                    _relationship(resolve(reference))
                    if reference is not None
                    else _relationship(_literal(bind(step)))
                )
            else:
                values.append(_relationship(step))
        return tuple(values)

    def by(items: tuple[AxisValue, ...]) -> tuple[ComparisonAxis, ...]:
        result: list[ComparisonAxis] = []
        for item in items:
            current = _literal(bind(item)) if isinstance(item, Deferred) else item
            if isinstance(current, Grouping):
                binding = current._binding
                if isinstance(binding, MembershipBinding):
                    invalid("Population coordinates cannot contain membership allocation rules")
                result.append(binding)
            elif isinstance(current, Deferred):
                result.append(build.axis(field(current)))
            else:
                invalid("Population axes require closed fields or coordinate groupings")
        return build.axes(tuple(result))

    if isinstance(value, IdentityParameter):
        if isinstance(value.target, Deferred):
            reference = native_symbol_ref(value.target)
            assert reference is not None
            target = _entity(resolve(reference))
        elif isinstance(value.target, tuple):
            return _CoordinateGrouping(build.identity(path(value.target), value.at), value._context)
        else:
            target = value.target
        return _CoordinateGrouping(build.identity(target, value.at), value._context)
    if isinstance(value, MembershipParameter):
        return _MembershipGrouping(
            build.membership(
                tuple(field(bind(item)) for item in value.fields),
                tuple(path(steps) for steps in value.paths),
                value.unmatched,
            ),
            value._context,
        )
    if isinstance(value, CoordinatesParameter):
        return PopulationParameter(
            build.coordinates(by(value.by), value.keys),
            _context=value._context,
            _identity_keys=value._identity_keys,
        )
    if isinstance(value, ProductParameter):
        factors: list[PopulationParameter] = []
        for item in value.factors:
            current = _literal(bind(item)) if isinstance(item, Deferred) else item
            if isinstance(current, CalendarAxis):
                coordinate = current._binding
                factors.append(
                    PopulationParameter(
                        PopulationProgram(CalendarKeys(coordinate), None, (coordinate,))
                    )
                )
            elif isinstance(current, TimeScope):
                factors.append(interval(current))
            elif isinstance(current, PopulationParameter):
                factors.append(current)
            else:
                invalid("product requires Population, CalendarAxis or TimeScope factors")
        return PopulationParameter(
            build.product(tuple(item._definition for item in factors)),
            _context=merge_input_contexts(value._context, _contexts(tuple(factors))),
            _identity_keys=_keys(tuple(factors)),
        )
    current = _literal(bind(value.population))
    if not isinstance(current, PopulationParameter):
        invalid("in_population requires a bound Population parameter")
    return PopulationContainsResolver(current)


def bind_parameter_call(
    recipe: Resolver,
    bind: Callable[[Deferred], Deferred],
    resolve: Callable[[Ref[SemanticKindTag]], object],
    *,
    field: Callable[[Deferred], FieldRead],
    predicate: Callable[[Deferred], PredicateExpression],
) -> Resolver | None:
    """Interpret only the explicitly supported native helper operations."""

    def bound(item: Resolver) -> object:
        current = resolver_of(bind(Deferred(item)))
        if isinstance(current, Just):
            return current.value
        carrier = relationship_carrier(current)
        if carrier is not None:
            return carrier
        if isinstance(current, Sequence):
            return tuple(bound(child) for child in current.values)
        return Deferred(current)

    if isinstance(recipe, Attr):
        receiver = bound(recipe.obj)
        if (
            isinstance(receiver, PopulationParameter)
            and isinstance(recipe.name, Just)
            and recipe.name.value == "axes"
        ):
            return Just(
                tuple(
                    _CoordinateGrouping(
                        axis,
                        InputContext(receiver._context.project_root),
                        tuple(
                            (identity, keys)
                            for identity, keys in receiver._identity_keys
                            if isinstance(axis, AxisBinding)
                            and axis.role == "identity"
                            and axis.entity.ref.key == identity
                        ),
                    )
                    for axis in receiver._definition.axes
                )
            )
        return None
    if isinstance(recipe, Item):
        indexer: object = recipe.indexer
        if not isinstance(indexer, Resolver):
            invalid("Population axis selection requires a native constant index")
        receiver, index = bound(recipe.obj), bound(indexer)
        if isinstance(receiver, tuple) and type(index) is int:
            return Just(receiver[index])
        return None
    if not isinstance(recipe, Call) or not isinstance(recipe.func, Attr):
        return None
    receiver = bound(recipe.func.obj)
    if not isinstance(receiver, PopulationParameter) or not isinstance(recipe.func.name, Just):
        return None
    method = recipe.func.name.value
    args = tuple(bound(item) for item in recipe.args)
    kwargs = {key: bound(item) for key, item in recipe.kwargs.items()}

    def empty() -> None:
        if args or kwargs:
            invalid(f"Population.{method} does not accept these arguments")

    def axes_arg() -> tuple[ComparisonAxis, ...]:
        value = kwargs.get("by")
        if args or set(kwargs) != {"by"} or not isinstance(value, tuple):
            invalid(f"Population.{method} requires by=(field_or_grouping, ...)")
        result: list[ComparisonAxis] = []
        for item in value:
            if isinstance(item, Grouping) and not isinstance(item._binding, MembershipBinding):
                result.append(item._binding)
            elif isinstance(item, Deferred):
                result.append(build.axis(field(item)))
            else:
                invalid("Population coordinates require closed fields or groupings")
        return build.axes(tuple(result))

    if method == "count":
        empty()
        return PopulationCountResolver(receiver)
    if method in ("project", "classify"):
        values = axes_arg()
        definition = (
            build.project(receiver._definition, values)
            if method == "project"
            else build.classify(receiver._definition, values)
        )
    elif method in ("union", "intersection", "difference"):
        if len(args) != 1 or kwargs or not isinstance(args[0], PopulationParameter):
            invalid(f"Population.{method} requires one Population")
        definition = build.set_operation(receiver._definition, args[0]._definition, method)
    elif method == "where":
        if len(args) != 1 or kwargs or not isinstance(args[0], Deferred):
            invalid("Population.where requires one Deferred membership predicate")
        definition = build.where(receiver._definition, predicate(args[0]))
    elif method in ("image", "preimage"):
        steps = kwargs.get("via")
        if not isinstance(steps, tuple) or not steps:
            invalid(f"Population.{method} requires an explicit nonempty via tuple")
        path: list[RelationshipParameter] = []
        for step in steps:
            reference = native_symbol_ref(step)
            path.append(
                _relationship(resolve(reference)) if reference is not None else _relationship(step)
            )
        if method == "image":
            at = kwargs.get("at")
            if (
                args
                or set(kwargs) - {"via", "at"}
                or (at is not None and not isinstance(at, InstantAnchor))
            ):
                invalid("Population.image requires via and an optional exact at anchor")
            definition = build.image(receiver._definition, tuple(path), at)
        else:
            if (
                len(args) != 1
                or set(kwargs) != {"via"}
                or not isinstance(args[0], PopulationParameter)
            ):
                invalid("Population.preimage requires one target Population and via")
            definition = build.preimage(receiver._definition, args[0]._definition, tuple(path))
    else:
        invalid(f"Unsupported symbolic Population operation {method!r}")
    return Just(
        PopulationParameter(
            definition,
            _context=merge_input_contexts(
                receiver._context, _contexts(args, tuple(kwargs.values()))
            ),
            _identity_keys=_keys(receiver, args, tuple(kwargs.values())),
        )
    )
