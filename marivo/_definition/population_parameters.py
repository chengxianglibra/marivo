"""Finite native-Deferred parameter captures for grouping and populations."""

from __future__ import annotations

from collections.abc import Callable

from ibis.common.deferred import (
    Attr,
    Call,
    Deferred,
    Item,
    Just,
    Resolver,
    Sequence,
)

from marivo._definition import population_builders as build
from marivo._definition.correspondence import ComparisonAxis
from marivo._definition.deferred import inspect_capture
from marivo._definition.deferred_values import (
    PopulationContainsResolver,
    PopulationCountResolver,
    captured_relationship,
    relationship_carrier,
    resolver_of,
)
from marivo._definition.grouping import (
    _CoordinateGrouping,
    _MembershipGrouping,
    grouping_binding,
)
from marivo._definition.parameter_recipes import (
    AxisValue,
    PathValue,
)
from marivo._definition.parameter_recipes import ContainsParameter as ContainsParameter
from marivo._definition.parameter_recipes import (
    CoordinatesParameter as CoordinatesParameter,
)
from marivo._definition.parameter_recipes import (
    IdentityParameter as IdentityParameter,
)
from marivo._definition.parameter_recipes import (
    MembershipParameter as MembershipParameter,
)
from marivo._definition.parameter_recipes import (
    Parameter as Parameter,
)
from marivo._definition.parameter_recipes import (
    ParameterResolver as ParameterResolver,
)
from marivo._definition.parameter_recipes import (
    PathRecipe as PathRecipe,
)
from marivo._definition.parameter_recipes import (
    ProductParameter as ProductParameter,
)
from marivo._definition.parameter_recipes import has_parameters as has_parameters
from marivo._definition.parameter_recipes import (
    parameter_inputs as parameter_inputs,
)
from marivo._definition.parameter_recipes import parameter_role as parameter_role
from marivo._definition.parameter_recipes import (
    validate_parameter as validate_parameter,
)
from marivo._definition.parameter_recipes import (
    validate_relationship_recipe as validate_relationship_recipe,
)
from marivo._definition.population_types import (
    CalendarKeys,
    IntervalKeys,
    MembershipBinding,
    PopulationParameter,
    PopulationProgram,
    PredicateExpression,
)
from marivo._definition.types import (
    AxisBinding,
    FieldRead,
    IntervalCoordinate,
    field_axis,
)
from marivo._kernel.errors import invalid
from marivo._kernel.grouping import Grouping
from marivo._kernel.input_context import (
    InputContext,
    merge_input_contexts,
)
from marivo._kernel.paths import (
    PathParameter,
    RelationshipParameter,
    RelationshipStepParameter,
    _PathValue,
    _RelationshipValue,
)
from marivo._kernel.refs import ExactRef, Ref, SemanticKindTag
from marivo._kernel.symbolic import native_symbol_ref
from marivo._kernel.temporal import (
    CalendarAxis,
    InstantAnchor,
    TimeScope,
    anchor_from_datetime,
)
from marivo._kernel.temporal_grid import instant


def _contexts(*values: object) -> InputContext:
    contexts: list[InputContext] = []
    for value in values:
        if isinstance(value, (_RelationshipValue, PathParameter)):
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
            else ((value.target.ref.key, value.steps[-1].relationship.target_keys),)
            if isinstance(value, PathParameter)
            else _keys(*value)
            if isinstance(value, tuple)
            else ()
        )
        for identity, keys in items:
            if identity in result and result[identity] != keys:
                invalid("Population axes have incompatible complete primary keys")
            result[identity] = keys
    return tuple(result.items())


def parameter(value: Parameter) -> Deferred:
    recipe = ParameterResolver(value)
    validate_parameter(recipe)
    for item in parameter_inputs(recipe):
        inspect_capture(item)
    return Deferred(recipe)


def interval(scope: TimeScope) -> PopulationParameter:
    if not isinstance(scope, TimeScope):
        invalid("an absolute Population interval requires a TimeScope with a frozen timezone")
    zone = scope.boundary_timezone
    start = anchor_from_datetime(instant(scope.start, zone))
    end = anchor_from_datetime(instant(scope.end, zone))
    coordinate = IntervalCoordinate(
        f"interval:{start.isoformat()}:{end.isoformat()}:{zone}",
        start,
        end,
        zone,
    )
    return PopulationParameter(PopulationProgram(IntervalKeys(coordinate), None, (coordinate,)))


def _literal(value: Deferred) -> object:
    recipe = resolver_of(value)
    if isinstance(recipe, Just):
        return recipe.value._parameter if isinstance(recipe.value, _PathValue) else recipe.value
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
) -> Grouping | PopulationParameter | PathParameter | Resolver:
    value = recipe.parameter

    def path(item: PathValue) -> PathParameter:
        current = _literal(bind(item)) if isinstance(item, Deferred) else item
        if not isinstance(current, PathParameter):
            invalid("This parameter requires a bound Path from mv.path(...)")
        return current

    def by(items: tuple[AxisValue, ...]) -> tuple[ComparisonAxis, ...]:
        result: list[ComparisonAxis] = []
        for item in items:
            current = _literal(bind(item)) if isinstance(item, Deferred) else item
            if isinstance(current, Grouping):
                binding = grouping_binding(current)
                if isinstance(binding, MembershipBinding):
                    invalid("Population coordinates cannot contain membership allocation rules")
                result.append(binding)
            elif isinstance(current, Deferred):
                result.append(field_axis(field(current)))
            else:
                invalid("Population axes require closed fields or coordinate groupings")
        return build.axes(tuple(result))

    if isinstance(value, PathRecipe):
        steps: list[RelationshipStepParameter] = []
        for step in value.steps:
            current = _literal(bind(step)) if isinstance(step, Deferred) else step
            steps.append(
                current
                if isinstance(current, RelationshipStepParameter)
                else RelationshipStepParameter(_relationship(current))
            )
        return PathParameter(tuple(steps), value.source_at, _context=value._context)
    if isinstance(value, IdentityParameter):
        if isinstance(value.target, Deferred):
            reference = native_symbol_ref(value.target)
            target = _entity(resolve(reference)) if reference is not None else path(value.target)
        else:
            target = value.target
        return _CoordinateGrouping(build.identity(target), value._context, _keys(target))
    if isinstance(value, MembershipParameter):
        return _MembershipGrouping(
            build.membership(
                tuple(field(bind(item)) for item in value.fields),
                tuple(path(item) for item in value.paths),
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
                coordinate = grouping_binding(current)
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


def _subject_argument(value: object, method: str) -> ComparisonAxis | None:
    if value is None:
        return None
    binding = grouping_binding(value) if isinstance(value, Grouping) else None
    if binding is None or isinstance(binding, MembershipBinding):
        invalid(f"Population.{method} requires on=identity_grouping")
    return binding


def _axis_arguments(
    values: tuple[object, ...], field: Callable[[Deferred], FieldRead]
) -> tuple[tuple[ComparisonAxis, ...], tuple[bool, ...]]:
    result: list[ComparisonAxis] = []
    for item in values:
        binding = grouping_binding(item) if isinstance(item, Grouping) else None
        if binding is not None and not isinstance(binding, MembershipBinding):
            result.append(binding)
        elif isinstance(item, Deferred):
            result.append(field_axis(field(item)))
        else:
            invalid("Population coordinates require closed fields or groupings")
    return tuple(result), tuple(isinstance(item, Grouping) for item in values)


def bind_parameter_call(
    recipe: Resolver,
    bind: Callable[[Deferred], Deferred],
    *,
    field: Callable[[Deferred], FieldRead],
    predicate: Callable[[Deferred], PredicateExpression],
) -> Resolver | None:
    """Interpret only the explicitly supported native helper operations."""

    def bound(item: Resolver) -> object:
        current = resolver_of(bind(Deferred(item)))
        if isinstance(current, Just):
            return (
                current.value._parameter if isinstance(current.value, _PathValue) else current.value
            )
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
    if (
        isinstance(recipe.func.name, Just)
        and recipe.func.name.value == "at"
        and captured_relationship(recipe.func.obj)
    ):
        validate_relationship_recipe(recipe, ())
        anchor = bound(recipe.args[0])
        assert isinstance(anchor, InstantAnchor)
        return Just(RelationshipStepParameter(_relationship(bound(recipe.func.obj)), anchor))
    receiver = bound(recipe.func.obj)
    if not isinstance(receiver, PopulationParameter) or not isinstance(recipe.func.name, Just):
        return None
    method = recipe.func.name.value
    args = tuple(bound(item) for item in recipe.args)
    kwargs = {key: bound(item) for key, item in recipe.kwargs.items()}

    def empty() -> None:
        if args or kwargs:
            invalid(f"Population.{method} does not accept these arguments")

    if method == "count":
        empty()
        return PopulationCountResolver(receiver)
    if method in ("project", "classify"):
        by = kwargs.get("by")
        allowed = {"by", "on"} if method == "classify" else {"by"}
        if args or set(kwargs) - allowed or not isinstance(by, tuple):
            invalid(f"Population.{method} requires by=(field_or_grouping, ...)")
        values, exact = _axis_arguments(by, field)
        definition = (
            build.project(receiver._definition, values)
            if method == "project"
            else build.classify(
                receiver._definition,
                values,
                _subject_argument(kwargs.get("on"), method),
                exact=exact,
            )
        )
    elif method == "identities":
        selected = _subject_argument(kwargs.get("on"), method)
        if args or set(kwargs) != {"on"} or selected is None:
            invalid("Population.identities requires on=identity_grouping")
        definition = build.identities(receiver._definition, selected)
    elif method in ("union", "intersection", "difference"):
        if len(args) != 1 or kwargs or not isinstance(args[0], PopulationParameter):
            invalid(f"Population.{method} requires one Population")
        definition = build.set_operation(receiver._definition, args[0]._definition, method)
    elif method == "where":
        if len(args) != 1 or kwargs or not isinstance(args[0], Deferred):
            invalid("Population.where requires one Deferred membership predicate")
        definition = build.where(receiver._definition, predicate(args[0]))
    elif method in ("image", "preimage"):
        path = kwargs.get("via")
        if not isinstance(path, PathParameter) or set(kwargs) - {"via", "on"}:
            invalid(f"Population.{method} requires via=mv.path(...) and optional on=")
        if method == "image":
            if args:
                invalid("Population.image uses keyword arguments via= and on=")
            definition = build.image(
                receiver._definition, path, _subject_argument(kwargs.get("on"), method)
            )
        else:
            if len(args) != 1 or not isinstance(args[0], PopulationParameter):
                invalid("Population.preimage requires one target Population and via")
            definition = build.preimage(
                receiver._definition,
                args[0]._definition,
                path,
                _subject_argument(kwargs.get("on"), method),
            )
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
