"""Controlled, source-free inspection of native Ibis deferred recipes.

The recipe remains an Ibis Resolver. These records describe its captured semantic
inputs and uses; they are not an executable numeric AST. No inspection path calls
``resolve`` or an author callback. Physical binding and semantic admission belong
to the consuming semantic layer.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import operator
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Final, Literal, NoReturn

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir
from ibis.common.bases import FrozenSlotted
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
    Variable,
)

from marivo._definition.types import (
    ExactRef,
    FieldRead,
    SourceLocation,
    SumAll,
    SumAllExcept,
    SumAxes,
    SumNone,
    SumPermission,
    check_literal,
    invalid,
    literal_token,
)
from marivo._definition.windows import AllHistory, Since, ToDate, TrailingDuration, TrailingPeriods
from marivo._symbolic import SymbolResolver
from marivo._temporal import InstantAnchor, _BuiltinGrain, _SemanticGrain
from marivo.refs import DomainKind, Ref, SemanticKind, SemanticKindTag

if TYPE_CHECKING:
    from marivo._definition.relations import PopulationParameter
    from marivo.datasource.typing import AiContextValue

RECIPE_RULE_VERSION: Final = 2
_LITERAL_TYPES: Final = (str, bool, int, float, Decimal, date, datetime, InstantAnchor)
_IBIS_LITERAL_TYPES: Final = (
    ir.IntegerScalar,
    ir.FloatingScalar,
    ir.DecimalScalar,
    ir.StringScalar,
    ir.BooleanScalar,
    ir.DateScalar,
    ir.TimestampScalar,
    ir.NullScalar,
)
NATIVE_METHODS: Final = frozenset(
    {
        "abs",
        "sqrt",
        "round",
        "cast",
        "ifelse",
        "coalesce",
        "nullif",
        "isnull",
        "notnull",
        "name",
        "clip",
        "log",
        "exp",
        "least",
        "greatest",
        "cases",
        "isin",
    }
)
SEMANTIC_METHODS: Final = frozenset(
    {
        "sum",
        "mean",
        "min",
        "max",
        "median",
        "quantile",
        "count",
        "count_distinct",
        "first",
        "last",
        "approx_median",
        "approx_nunique",
        "weighted_mean",
        "approx_quantile",
        "via",
        "at",
        "within",
        "where",
        "per",
        "window",
    }
)
_TOP_FUNCTIONS: Final = (
    (ibis.ifelse, inspect.unwrap(ibis.ifelse), "ifelse"),
    (ibis.coalesce, inspect.unwrap(ibis.coalesce), "coalesce"),
    (ibis.cases, inspect.unwrap(ibis.cases), "cases"),
    (ibis.least, inspect.unwrap(ibis.least), "least"),
    (ibis.greatest, inspect.unwrap(ibis.greatest), "greatest"),
    (ibis.literal, inspect.unwrap(ibis.literal), "literal"),
)
_BINARY_SYMBOLS: Final = (
    (operator.add, "add"),
    (operator.sub, "subtract"),
    (operator.mul, "multiply"),
    (operator.truediv, "divide"),
    (operator.floordiv, "floor_divide"),
    (operator.mod, "modulus"),
    (operator.pow, "power"),
    (operator.eq, "equal"),
    (operator.ne, "not_equal"),
    (operator.lt, "less"),
    (operator.le, "less_equal"),
    (operator.gt, "greater"),
    (operator.ge, "greater_equal"),
    (operator.and_, "and"),
    (operator.or_, "or"),
    (operator.xor, "xor"),
)


@dataclass(frozen=True, slots=True)
class SemanticInput:
    """Frozen physical field facts captured by an owning completed scope."""

    reference: ExactRef
    root: ExactRef
    unit: str | None
    permission: SumPermission
    default_time: ExactRef | None = None
    coordinates: tuple[ExactRef, ...] = ()
    field_read: FieldRead | None = None
    versioned: bool = False


@dataclass(frozen=True, slots=True)
class RelationshipParameter:
    """Frozen directed relationship facts accepted as semantic path parameters."""

    ref: ExactRef
    from_entity: ExactRef
    to_entity: ExactRef
    cardinality: Literal["one_to_one", "many_to_one", "one_to_many", "many_to_many"]


@dataclass(frozen=True, slots=True)
class _RelationshipValue:
    """Neutral private backing shared by the public relationship facade."""

    _parameter: RelationshipParameter


@dataclass(frozen=True, slots=True)
class DeclarationRecord:
    """Local declaration identity, separate from its deferred computation."""

    kind: Literal["measure", "metric"]
    name: str
    domain: Ref[DomainKind] | None = None
    unit: str | None = None
    permission: SumPermission | None = None
    ai_context: AiContextValue | None = None
    location: SourceLocation | None = None
    reference: ExactRef | None = None
    pending_additivity: tuple[Literal["over", "all", "none"], tuple[str, ...]] | None = None
    pending_assertion: bool = False


# Ibis does not ship py.typed. Keep its untyped inheritance at this one
# runtime boundary, with the exact initializer shape used by these resolvers.
if TYPE_CHECKING:

    class _FrozenResolver(Resolver):
        def __init__(self, **values: object) -> None: ...
else:

    class _FrozenResolver(FrozenSlotted, Resolver):
        __slots__ = ()


class SemanticResolver(_FrozenResolver):
    """A semantic leaf that cannot be resolved through Ibis's generic context."""

    __slots__ = ("input",)
    input: SemanticInput

    def __init__(self, value: SemanticInput) -> None:
        super().__init__(input=value)

    def resolve(self, context: dict[str, object]) -> NoReturn:
        invalid("Semantic inputs require Marivo binding; use the semantic consumer.")


class DeclarationResolver(_FrozenResolver):
    """An immutable local declaration around the original Ibis recipe."""

    __slots__ = ("declaration", "recipe")
    declaration: DeclarationRecord
    recipe: Resolver

    def __init__(self, recipe: Resolver, declaration: DeclarationRecord) -> None:
        super().__init__(recipe=recipe, declaration=declaration)

    def resolve(self, context: dict[str, object]) -> NoReturn:
        invalid("Semantic declarations require Marivo binding; use the semantic consumer.")


class RescaleResolver(_FrozenResolver):
    """Explicit numeric division and target-unit assertion on one recipe."""

    __slots__ = ("divisor", "recipe", "unit")
    recipe: Resolver
    divisor: int | float | Decimal
    unit: str

    def __init__(self, recipe: Resolver, divisor: int | float | Decimal, unit: str) -> None:
        super().__init__(recipe=recipe, divisor=divisor, unit=unit)

    def resolve(self, context: dict[str, object]) -> NoReturn:
        invalid("Rescale expressions require Marivo semantic binding.")


class PopulationCountResolver(_FrozenResolver):
    """A closed Metric carrying its explicit identity Population parameter."""

    __slots__ = ("population",)
    population: PopulationParameter

    def __init__(self, population: PopulationParameter) -> None:
        super().__init__(population=population)

    def resolve(self, context: dict[str, object]) -> NoReturn:
        invalid("Population counts require Marivo semantic binding.")


class PopulationContainsResolver(_FrozenResolver):
    """Invocation carrier for one membership leaf; binding never calls resolve."""

    __slots__ = ("population",)
    population: PopulationParameter

    def __init__(self, population: PopulationParameter) -> None:
        super().__init__(population=population)

    def resolve(self, context: dict[str, object]) -> NoReturn:
        invalid("Population membership requires Marivo predicate binding.")


@dataclass(frozen=True, slots=True)
class SemanticUse:
    """One captured input occurrence, including repeated shared inputs."""

    path: tuple[str, ...]
    input: SemanticInput


@dataclass(frozen=True, slots=True)
class RecipeUse:
    """One operation occurrence with its original Ibis resolver and role path."""

    path: tuple[str, ...]
    symbol: str
    recipe: Resolver


@dataclass(frozen=True, slots=True)
class DeferredInspection:
    """Source-free capture evidence; this does not certify execution eligibility."""

    recipe: Resolver
    declaration: DeclarationRecord | None
    dependencies: tuple[ExactRef, ...]
    uses: tuple[SemanticUse, ...]
    operations: tuple[RecipeUse, ...]
    declarations: tuple[DeclarationRecord, ...]
    fingerprint: str
    symbols: tuple[Ref[SemanticKindTag], ...] = ()

    @property
    def semantic_calls(self) -> tuple[RecipeUse, ...]:
        return tuple(use for use in self.operations if use.symbol in SEMANTIC_METHODS)


def admitted_function(value: object) -> bool:
    """Check identity of approved public Ibis constructors, without calling them."""
    return any(value is public for public, _, _ in _TOP_FUNCTIONS)


def semantic_input(value: SemanticInput) -> Deferred:
    """Capture frozen semantic facts without constructing a physical Ibis Value."""
    return Deferred(SemanticResolver(value))


def attribute_name(attribute: Attr) -> str:
    """Read Ibis's actual Just carrier despite its incorrect str annotation."""
    name: object = attribute.name
    if isinstance(name, Just) and isinstance(name.value, str):
        return name.value
    invalid("A native deferred attribute requires one constant string name.")


def resolver_of(value: Deferred) -> Resolver:
    recipe: object = value._resolver
    if not isinstance(recipe, Resolver):
        invalid("Expected an Ibis deferred recipe; received an invalid carrier.")
    return recipe


def root_declaration(value: Deferred) -> DeclarationRecord | None:
    """Return only a root declaration; anonymous derivations do not inherit it."""
    recipe = resolver_of(value)
    return recipe.declaration if type(recipe) is DeclarationResolver else None


def declare(value: Deferred, declaration: DeclarationRecord) -> Deferred:
    """Attach a local declaration after inspecting the same anonymous recipe."""
    inspect_deferred(value)
    return Deferred(DeclarationResolver(resolver_of(value), declaration))


def _reference(value: ExactRef) -> tuple[str, str]:
    return (value.ref.key, value.version)


def _permission(value: SumPermission | None) -> tuple[str, ...]:
    if value is None:
        return ("inherited",)
    if type(value) is SumAll:
        return ("all",)
    if type(value) is SumNone:
        return ("none",)
    if isinstance(value, (SumAxes, SumAllExcept)):
        return (
            "axes" if type(value) is SumAxes else "except",
            *sorted(json.dumps(_reference(axis)) for axis in value.axes),
        )
    invalid("Expected a closed sum permission; supply a semantic permission rule.")


def _captured_field_kind(recipe: Resolver) -> SemanticKind | None:
    """Read a field role only from native leaves and admitted field modifiers."""
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
        return _captured_field_kind(recipe.func.obj)
    return None


def _captured_scalar_path(recipe: Resolver) -> bool:
    if isinstance(recipe, SemanticResolver):
        return recipe.input.field_read is not None and bool(recipe.input.field_read.path)
    if isinstance(recipe, Call) and isinstance(recipe.func, Attr):
        name = attribute_name(recipe.func)
        return name == "via" or (name in {"at", "name"} and _captured_scalar_path(recipe.func.obj))
    return False


def _captured_relationship(recipe: Resolver) -> bool:
    return (
        isinstance(recipe, SymbolResolver) and recipe.reference.kind is SemanticKind.RELATIONSHIP
    ) or (
        isinstance(recipe, Just)
        and isinstance(recipe.value, (RelationshipParameter, _RelationshipValue))
    )


def _capture_sample(recipe: Resolver, path: tuple[str, ...]) -> None:
    """Validate the finite sample-parameter grammar without resolving fields."""
    if isinstance(recipe, Just) and isinstance(recipe.value, (_BuiltinGrain, _SemanticGrain)):
        return
    from marivo._definition.population_parameters import (
        IdentityParameter,
        MembershipParameter,
        ParameterResolver,
        parameter_role,
    )
    from marivo._definition.relations import MembershipBinding
    from marivo._definition.types import AxisBinding
    from marivo._grouping import Grouping

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
        membership = (
            isinstance(member, ParameterResolver)
            and isinstance(member.parameter, MembershipParameter)
        ) or (
            isinstance(member, Just)
            and isinstance(member.value, Grouping)
            and isinstance(member.value._binding, MembershipBinding)
        )
        if membership:
            if not population:
                if isinstance(member, ParameterResolver) and isinstance(
                    member.parameter, MembershipParameter
                ):
                    if any(
                        _captured_field_kind(resolver_of(field))
                        not in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION}
                        or _captured_scalar_path(resolver_of(field))
                        for field in member.parameter.fields
                    ):
                        invalid(
                            "Internal membership samples require native Dimension or TimeDimension fields.",
                            path=path,
                        )
                elif (
                    isinstance(member, Just)
                    and isinstance(member.value, Grouping)
                    and isinstance(member.value._binding, MembershipBinding)
                    and any(
                        read.field.ref.kind
                        not in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION}
                        or read.path
                        for read in member.value._binding.fields
                    )
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
        if isinstance(member, Just) and isinstance(member.value, Grouping):
            binding = member.value._binding
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
        if _captured_scalar_path(member) or _captured_field_kind(member) not in {
            SemanticKind.DIMENSION,
            SemanticKind.TIME_DIMENSION,
        }:
            invalid(
                "per requires Dimension or TimeDimension fields, grouping rules, or a Population.",
                path=(*path, f"item:{index}"),
            )


class _Inspector:
    def __init__(self, slots: tuple[str, ...] = (), *, capture: bool = False) -> None:
        self.slots = slots
        self.capture = capture
        self.tokens: list[str] = [str(RECIPE_RULE_VERSION)]
        self.dependencies: dict[tuple[str, str], ExactRef] = {}
        self.uses: list[SemanticUse] = []
        self.operations: list[RecipeUse] = []
        self.declarations: list[DeclarationRecord] = []
        self.symbols: dict[str, Ref[SemanticKindTag]] = {}
        self.has_population_count = False
        self.has_population_contains = False

    def token(self, *values: str) -> None:
        self.tokens.extend(values)

    def dependency(self, value: ExactRef) -> None:
        self.dependencies[_reference(value)] = value

    def visit(self, recipe: Resolver, path: tuple[str, ...], *, identity: bool = False) -> None:
        from marivo._definition.population_parameters import (
            ParameterResolver,
            validate_parameter,
            validate_parameter_call,
        )

        if self.capture:
            validate_parameter_call(recipe, path)

        if isinstance(recipe, ParameterResolver):
            self.token("parameter", type(recipe.parameter).__name__)
            for index, parameter_value in enumerate(validate_parameter(recipe, path)):
                self.visit(resolver_of(parameter_value), (*path, f"input:{index}"), identity=True)
        elif type(recipe) is SymbolResolver:
            if (
                self.capture
                and not identity
                and recipe.reference.kind
                not in {
                    SemanticKind.DIMENSION,
                    SemanticKind.TIME_DIMENSION,
                    SemanticKind.MEASURE,
                    SemanticKind.METRIC,
                }
            ):
                invalid(
                    f"A {recipe.reference.kind.value} symbol is an identity parameter, not a numeric or predicate expression.",
                    path=path,
                )
            self.symbols[recipe.reference.key] = recipe.reference
            self.token("symbol", recipe.reference.key)
        elif type(recipe) is PopulationContainsResolver:
            self.has_population_contains = True
            from marivo._definition.graph import computation_fingerprint, direct_refs

            definition = recipe.population._definition
            self.token("population_contains", computation_fingerprint(definition))
            for reference in direct_refs(definition):
                self.dependency(reference)
        elif type(recipe) is PopulationCountResolver:
            self.has_population_count = True
            from marivo._definition.graph import computation_fingerprint

            self.token("population_count", computation_fingerprint(recipe.population._definition))
        elif type(recipe) is SemanticResolver:
            value = recipe.input
            self.uses.append(SemanticUse(path, value))
            self.dependency(value.reference)
            self.dependency(value.root)
            self.token("semantic", *_reference(value.reference), *_reference(value.root))
            self.token("unit", value.unit or "", *_permission(value.permission))
            if value.default_time is not None:
                self.dependency(value.default_time)
                self.token("time", *_reference(value.default_time))
            for axis in value.coordinates:
                self.dependency(axis)
                self.token("axis", *_reference(axis))
            if value.field_read is not None:
                read = value.field_read
                self.token("field_read", *_reference(read.field), *_reference(read.entity))
                for step in read.path:
                    self.dependency(step)
                    self.token("via", *_reference(step))
                self.token("at", "" if read.at is None else read.at.isoformat())
            self.token("versioned", str(value.versioned))
            self.token("end_semantic")
        elif type(recipe) is RescaleResolver:
            self.operations.append(RecipeUse(path, "rescale", recipe))
            self.token("rescale", recipe.unit)
            self.literal(recipe.divisor, (*path, "divisor"))
            self.visit(recipe.recipe, (*path, "rescale"))
            self.token("end_rescale")
        elif type(recipe) is DeclarationResolver:
            declaration = recipe.declaration
            self.declarations.append(declaration)
            if declaration.reference is not None:
                self.dependency(declaration.reference)
            asserted = declaration.unit is not None or declaration.permission is not None
            if asserted:
                self.operations.append(RecipeUse(path, "assertion", recipe))
                self.token(
                    "assertion", declaration.unit or "", *_permission(declaration.permission)
                )
            self.visit(recipe.recipe, (*path, "assertion") if asserted else path)
            if asserted:
                self.token("end_assertion")
        elif type(recipe) is Variable and recipe.name in self.slots:
            self.token("slot", recipe.name)
        elif type(recipe) is Just:
            self.literal(recipe.value, path)
        elif type(recipe) is BinaryOperator:
            symbol = next(
                (name for function, name in _BINARY_SYMBOLS if recipe.func is function), None
            )
            if symbol is None:
                self.reject(recipe, path)
            self.call(symbol, (recipe.left, recipe.right), {}, recipe, path)
        elif type(recipe) is UnaryOperator:
            if recipe.func is operator.neg:
                symbol = "negate"
            elif recipe.func is operator.invert:
                symbol = "not"
            else:
                self.reject(recipe, path)
            self.call(symbol, (recipe.arg,), {}, recipe, path)
        elif type(recipe) is Call:
            self.visit_call(recipe, path)
        elif isinstance(recipe, (Attr, Item)) and parameter_recipe(recipe):
            if isinstance(recipe, Attr):
                if attribute_name(recipe) != "axes":
                    self.reject(recipe, path)
                self.visit(recipe.obj, (*path, "population"))
            else:
                if not isinstance(recipe.indexer, Just) or type(recipe.indexer.value) is not int:
                    self.reject(recipe, path)
                self.visit(recipe.obj, (*path, "axes"))
                self.literal(recipe.indexer.value, (*path, "index"))
        elif type(recipe) is Sequence:
            if recipe.typ is not tuple and recipe.typ is not list:
                invalid(
                    "Expected a captured tuple or list; use a built-in sequence.",
                    path=path,
                )
            values: tuple[Resolver, ...] = recipe.values
            self.token("tuple", str(len(values)))
            for index, value in enumerate(values):
                self.visit(value, (*path, f"item:{index}"), identity=identity)
            self.token("end_tuple")
        else:
            self.reject(recipe, path)

    def reject(self, recipe: Resolver, path: tuple[str, ...]) -> NoReturn:
        invalid(
            f"Expected an approved Ibis recipe; received {type(recipe).__name__}. "
            "Use captured semantic inputs and approved numeric operations.",
            path=path,
        )

    def literal(self, value: object, path: tuple[str, ...]) -> None:
        from marivo._definition.graph import (
            _collect_refs,
            _encode,
            computation_fingerprint,
            direct_refs,
            fingerprint,
        )
        from marivo._definition.relations import (
            MembershipBinding,
            PopulationParameter,
        )
        from marivo._grouping import Grouping

        if isinstance(value, _RelationshipValue):
            value = value._parameter

        if isinstance(value, Grouping):
            from marivo._definition.types import AxisBinding, CalendarCoordinate

            value = value._binding
            if isinstance(value, (AxisBinding, CalendarCoordinate)):
                self.token("grouping", fingerprint(_encode(value, {})))
                grouping_refs: set[ExactRef] = set()
                _collect_refs(value, grouping_refs)
                for reference in grouping_refs:
                    self.dependency(reference)
                return

        if isinstance(value, (MembershipBinding, PopulationParameter)):
            refs: set[ExactRef] = set()
            if isinstance(value, PopulationParameter):
                self.token("population", computation_fingerprint(value._definition))
                refs.update(direct_refs(value._definition))
            else:
                self.token("membership", fingerprint(_encode(value, {})))
                _collect_refs(value, refs)
            for reference in refs:
                self.dependency(reference)
            return
        from marivo._temporal import (
            TimeScope,
            _CalendarPeriodTimeScope,
            _SessionTimeScope,
            _TemporalOccurrenceTimeScope,
        )

        if type(value) in (
            TimeScope,
            _SessionTimeScope,
            _CalendarPeriodTimeScope,
            _TemporalOccurrenceTimeScope,
        ):
            assert isinstance(value, TimeScope)
            self.token(
                "time_scope",
                json.dumps(
                    value._contract().model_dump(mode="json"), sort_keys=True, separators=(",", ":")
                ),
            )
            return
        if isinstance(value, _BuiltinGrain):
            self.token("grain", value.unit, str(value.count))
        elif isinstance(value, _SemanticGrain):
            self.token(
                "grain",
                value._calendar.key,
                value.level,
                "unbound" if value._snapshot is None else value._snapshot.snapshot_digest,
            )
        elif type(value) is AllHistory:
            self.token("window", "all_history")
        elif isinstance(value, TrailingDuration) and type(value) is TrailingDuration:
            self.token(
                "window",
                "duration",
                str(value.duration.days),
                str(value.duration.seconds),
                str(value.duration.microseconds),
            )
        elif isinstance(value, TrailingPeriods) and type(value) is TrailingPeriods:
            self.token("window", "periods", str(value.count))
        elif isinstance(value, Since) and type(value) is Since:
            self.token("window", "since", value.start.isoformat())
        elif isinstance(value, ToDate) and type(value) is ToDate:
            grain = value.grain
            if isinstance(grain, _BuiltinGrain):
                self.token("window", "to_date", grain.unit, str(grain.count))
            elif isinstance(grain, _SemanticGrain):
                self.token(
                    "window",
                    "to_date",
                    grain._calendar.key,
                    grain.level,
                    "unbound" if grain._snapshot is None else grain._snapshot.snapshot_digest,
                )
            else:
                invalid("Expected a closed calendar grain for to_date.", path=path)
        elif isinstance(value, RelationshipParameter):
            self.token("relationship", value.cardinality)
            for reference in (value.ref, value.from_entity, value.to_entity):
                self.dependency(reference)
                self.token(*_reference(reference))
            self.token("end_relationship")
        elif value is None or type(value) in _LITERAL_TYPES:
            if not (value is None or isinstance(value, (str, bool, int, float, Decimal, date))):
                invalid("Expected an immutable literal.", path=path)
            self.token("literal", *literal_token(check_literal(value, role="deferred argument")))
        elif type(value) in _IBIS_LITERAL_TYPES and isinstance(value, ir.Value):
            operation = value.op()
            if type(operation) is not ops.Literal:
                invalid(
                    "Expected a captured semantic input or Ibis Literal; received an Ibis Value. "
                    "Resolve a semantic field through its completed scope.",
                    path=path,
                )
            self.token("ibis_literal", str(operation.dtype))
            self.literal(operation.value, path)
            self.token("end_ibis_literal")
        else:
            invalid(
                f"Expected an immutable literal; received {type(value).__name__}. "
                "Remove callbacks and mutable values from the deferred expression.",
                path=path,
            )

    def visit_call(self, recipe: Call, path: tuple[str, ...]) -> None:
        function = recipe.func
        if type(function) is Just:
            symbol = next(
                (name for _, actual, name in _TOP_FUNCTIONS if function.value is actual), None
            )
            if symbol is None:
                self.reject(recipe, path)
            self.top_signature(symbol, recipe, path)
            self.call(symbol, recipe.args, dict(recipe.kwargs), recipe, path)
        elif type(function) is Attr:
            # Ibis 12 annotates Attr.name as str but stores a Just resolver.
            name: object = function.name
            if (
                not isinstance(name, Just)
                or type(name) is not Just
                or not isinstance(name.value, str)
            ):
                self.reject(recipe, path)
            symbol = name.value
            from marivo._definition.population_parameters import parameter_role

            population = parameter_role(function.obj) == "population"
            if population:
                allowed = {
                    "project",
                    "union",
                    "intersection",
                    "difference",
                    "classify",
                    "image",
                    "preimage",
                    "where",
                    "count",
                }
                if symbol not in allowed:
                    self.reject(recipe, path)
            elif symbol not in NATIVE_METHODS | SEMANTIC_METHODS:
                self.reject(recipe, path)
            if self.capture:
                self.signature(symbol, recipe, path, population=population)
            if symbol == "name":
                if len(recipe.args) != 1 or recipe.kwargs or type(recipe.args[0]) is not Just:
                    self.reject(recipe, path)
                label: object = recipe.args[0].value
                if not isinstance(label, str):
                    self.reject(recipe, path)
                self.visit(function.obj, path)
                return
            self.call(symbol, (function.obj, *recipe.args), dict(recipe.kwargs), recipe, path)
        else:
            self.reject(recipe, path)

    def top_signature(self, symbol: str, recipe: Call, path: tuple[str, ...]) -> None:
        """Validate the finite top-level constructor grammar without calling it."""
        args, kwargs = recipe.args, dict(recipe.kwargs)
        if symbol == "ifelse":
            if len(args) != 3 or kwargs:
                invalid("ibis.ifelse requires exactly three positional expressions.", path=path)
        elif symbol in {"coalesce", "least", "greatest"}:
            if not args or kwargs:
                invalid(
                    f"ibis.{symbol} requires at least one positional expression and no keywords.",
                    path=path,
                )
        elif symbol == "cases":
            if (
                set(kwargs) - {"branch", "else_"}
                or (args and "branch" in kwargs)
                or (not args and "branch" not in kwargs)
            ):
                invalid(
                    "ibis.cases requires one first branch, optional additional positional branches, and optional else_.",
                    path=path,
                )
            branches = args if args else (kwargs["branch"],)
            for index, branch in enumerate(branches):
                if not isinstance(branch, Sequence) or len(branch.values) != 2:
                    invalid(
                        "ibis.cases requires immutable (condition, value) branch pairs.",
                        path=(*path, f"branch:{index}"),
                    )
        elif symbol == "literal":
            if (
                len(args) > 2
                or set(kwargs) - {"value", "type"}
                or (args and "value" in kwargs)
                or (len(args) == 2 and "type" in kwargs)
                or (not args and "value" not in kwargs)
            ):
                invalid(
                    "ibis.literal requires one scalar value and an optional type, with each argument supplied once.",
                    path=path,
                )
            value = args[0] if args else kwargs["value"]
            dtype = args[1] if len(args) == 2 else kwargs.get("type")
            if not isinstance(value, Just) or not (
                value.value is None
                or type(value.value) in _LITERAL_TYPES
                or type(value.value) in _IBIS_LITERAL_TYPES
            ):
                invalid(
                    "ibis.literal requires an immutable scalar literal, not a semantic expression or parameter.",
                    path=path,
                )
            self.literal(value.value, (*path, "value"))
            if dtype is not None and (
                not isinstance(dtype, Just)
                or (dtype.value is not None and type(dtype.value) is not str)
            ):
                invalid(
                    "ibis.literal type requires an immutable dtype string or None.",
                    path=(*path, "type"),
                )
        else:
            self.reject(recipe, path)

    def signature(
        self, symbol: str, recipe: Call, path: tuple[str, ...], *, population: bool = False
    ) -> None:
        """Reject known method arity and keyword mistakes at capture time."""
        reducers = {
            "sum",
            "mean",
            "min",
            "max",
            "median",
            "count",
            "count_distinct",
            "first",
            "last",
            "approx_median",
            "approx_nunique",
            "weighted_mean",
            "quantile",
            "approx_quantile",
        }
        signatures = {
            "via": (1, 1, set()),
            "at": (1, 1, set()),
            "within": (1, 1, set()),
            "where": (1, 1, set()),
            "per": (1, 1, set()),
            "window": (1, 1, set()),
            "abs": (0, 0, set()),
            "sqrt": (0, 0, set()),
            "exp": (0, 0, set()),
            "isnull": (0, 0, set()),
            "notnull": (0, 0, set()),
            "cast": (1, 1, set()),
            "ifelse": (2, 2, set()),
            "nullif": (1, 1, set()),
            "round": (0, 1, {"digits"}),
            "name": (1, 1, set()),
            "isin": (1, 1, set()),
            "clip": (0, 2, {"lower", "upper"}),
            "log": (0, 1, {"base"}),
            "coalesce": (0, len(recipe.args), set()),
            "greatest": (0, len(recipe.args), set()),
            "least": (0, len(recipe.args), set()),
            "cases": (1, max(1, len(recipe.args)), {"else_"}),
        }
        if population:
            signatures = {
                "count": (0, 0, set()),
                "project": (0, 0, {"by"}),
                "classify": (0, 0, {"by"}),
                "union": (1, 1, set()),
                "intersection": (1, 1, set()),
                "difference": (1, 1, set()),
                "image": (0, 0, {"via", "at"}),
                "preimage": (1, 1, {"via"}),
                "where": (1, 1, set()),
            }
        elif symbol in reducers:
            keywords = {"time", "nulls", "where"}
            if symbol in {"quantile", "approx_quantile"}:
                keywords.add("q")
            if symbol in {"first", "last"}:
                keywords.update({"order_by", "include_null"})
            if symbol == "weighted_mean":
                keywords.add("weight")
            signatures[symbol] = (
                0,
                1 if symbol in {"quantile", "approx_quantile"} else 0,
                keywords,
            )
        if symbol in signatures:
            minimum, maximum, keywords = signatures[symbol]
            if not minimum <= len(recipe.args) <= maximum or set(recipe.kwargs) - keywords:
                invalid(
                    f"unsupported arguments for {symbol}; expected {minimum}..{maximum} positional arguments and keywords {sorted(keywords)}.",
                    path=path,
                )
        if not population:
            positional_names = {
                "round": ("digits",),
                "clip": ("lower", "upper"),
                "log": ("base",),
                "quantile": ("q",),
                "approx_quantile": ("q",),
            }.get(symbol, ())
            duplicate = set(positional_names[: len(recipe.args)]) & set(recipe.kwargs)
            if duplicate:
                invalid(
                    f"{symbol} assigns these parameters more than once: {', '.join(sorted(duplicate))}.",
                    path=path,
                )
            if symbol in {"quantile", "approx_quantile"}:
                if (not recipe.args and "q" not in recipe.kwargs) or (
                    recipe.args and "q" in recipe.kwargs
                ):
                    invalid(f"{symbol} requires exactly one q parameter.", path=path)
                q = recipe.args[0] if recipe.args else recipe.kwargs["q"]
                if (
                    not isinstance(q, Just)
                    or type(q.value) not in {int, float}
                    or not 0 <= q.value <= 1
                ):
                    invalid("quantile requires one finite numeric q in [0, 1].", path=path)
            if symbol == "weighted_mean" and "weight" not in recipe.kwargs:
                invalid("weighted_mean requires an explicit weight expression.", path=path)
            nulls = recipe.kwargs.get("nulls")
            if nulls is not None and (
                not isinstance(nulls, Just)
                or type(nulls.value) is not str
                or nulls.value not in {"ignore", "error"}
            ):
                invalid('nulls requires "ignore" or "error".', path=(*path, "parameter:nulls"))
            include_null = recipe.kwargs.get("include_null")
            if include_null is not None and (
                not isinstance(include_null, Just) or type(include_null.value) is not bool
            ):
                invalid(
                    "include_null requires a boolean literal.",
                    path=(*path, "parameter:include_null"),
                )
            if symbol == "round":
                digits = recipe.args[0] if recipe.args else recipe.kwargs.get("digits")
                if digits is not None and (
                    not isinstance(digits, Just) or type(digits.value) is not int
                ):
                    invalid("round digits requires an integer literal.", path=path)
            if symbol == "cast" and (
                not isinstance(recipe.args[0], Just) or type(recipe.args[0].value) is not str
            ):
                invalid("cast requires one immutable dtype string.", path=path)
            if symbol == "via":
                via = recipe.args[0]
                if not isinstance(via, Sequence) or via.typ is not tuple or not via.values:
                    invalid(
                        "via requires a nonempty ordered tuple of relationship parameters.",
                        path=path,
                    )
                for index, step in enumerate(via.values):
                    if not _captured_relationship(step):
                        invalid(
                            "via requires native Relationship symbols or closed Relationship facts.",
                            path=(*path, "path", f"item:{index}"),
                        )
            for parameter in ("time", "order_by"):
                value = recipe.kwargs.get(parameter)
                if (
                    value is not None
                    and not (
                        parameter == "time" and isinstance(value, Just) and value.value is None
                    )
                    and _captured_field_kind(value) is not SemanticKind.TIME_DIMENSION
                ):
                    invalid(
                        f"{parameter} requires a native or closed TimeDimension field, including supported field paths.",
                        path=(*path, f"parameter:{parameter}"),
                    )
            if symbol == "per":
                _capture_sample(recipe.args[0], (*path, "samples"))
            if symbol == "at" and (
                not isinstance(recipe.args[0], Just)
                or not isinstance(recipe.args[0].value, InstantAnchor)
                or recipe.args[0].value.utcoffset() is None
            ):
                invalid("at requires one exact InstantAnchor.", path=path)
            if symbol == "within":
                from marivo._temporal import TimeScope

                if not isinstance(recipe.args[0], Just) or not isinstance(
                    recipe.args[0].value, TimeScope
                ):
                    invalid("within requires one immutable TimeScope.", path=path)
            if symbol == "window" and (
                not isinstance(recipe.args[0], Just)
                or not isinstance(
                    recipe.args[0].value,
                    (AllHistory, Since, ToDate, TrailingDuration, TrailingPeriods),
                )
            ):
                invalid("window requires one immutable observation-window rule.", path=path)
            weight = recipe.kwargs.get("weight")
            if weight is not None and (
                (
                    isinstance(weight, SymbolResolver)
                    and weight.reference.kind not in {SemanticKind.MEASURE, SemanticKind.METRIC}
                )
                or isinstance(weight, Just)
            ):
                invalid(
                    "weight requires a numeric row or Metric expression.",
                    path=(*path, "parameter:weight"),
                )
            if symbol == "cases" and any(
                not isinstance(branch, Sequence) or len(branch.values) != 2
                for branch in recipe.args
            ):
                invalid("cases requires immutable (match, value) branch pairs.", path=path)
        receiver = recipe.func.obj if isinstance(recipe.func, Attr) else None
        if not population and receiver is not None and symbol in {"via", "at", "within"}:
            kind = _captured_field_kind(receiver)
            if kind is None or (symbol == "within" and kind is not SemanticKind.TIME_DIMENSION):
                invalid(
                    f"{symbol} requires a semantic {'TimeDimension' if symbol == 'within' else 'field'} receiver.",
                    path=path,
                )
        if isinstance(receiver, SymbolResolver):
            kind = receiver.reference.kind
            if kind is SemanticKind.ENTITY and symbol != "count":
                invalid("An Entity symbol supports only count().", path=path)
            if kind not in {
                SemanticKind.ENTITY,
                SemanticKind.DIMENSION,
                SemanticKind.TIME_DIMENSION,
                SemanticKind.MEASURE,
                SemanticKind.METRIC,
            }:
                invalid(
                    f"A {kind.value} symbol is an identity parameter and cannot receive {symbol}().",
                    path=path,
                )

    def call(
        self,
        symbol: str,
        args: tuple[Resolver, ...],
        kwargs: dict[str, Resolver],
        recipe: Resolver,
        path: tuple[str, ...],
    ) -> None:
        self.operations.append(RecipeUse(path, symbol, recipe))
        self.token("call", symbol, str(len(args)))
        roles = (
            ("condition", "true", "false")
            if symbol == "ifelse"
            else (("numerator", "denominator") if symbol == "divide" else ())
        )
        for index, arg in enumerate(args):
            role = roles[index] if index < len(roles) else f"operand:{index}"
            self.token(role)
            self.visit(
                arg,
                (*path, role),
                identity=(symbol == "via" and index == 1) or (symbol == "count" and index == 0),
            )
        for key, value in sorted(kwargs.items()):
            self.token("parameter", key)
            self.visit(value, (*path, f"parameter:{key}"), identity=key == "via")
        self.token("end_call")


def parameter_recipe(recipe: Resolver) -> bool:
    """Recognize the finite helper receiver chain without evaluating it."""
    from marivo._definition.population_parameters import ParameterResolver
    from marivo._definition.relations import PopulationParameter

    if isinstance(recipe, ParameterResolver):
        return True
    if isinstance(recipe, Just):
        return isinstance(recipe.value, PopulationParameter)
    if isinstance(recipe, (Attr, Item)):
        return parameter_recipe(recipe.obj)
    if isinstance(recipe, Call) and isinstance(recipe.func, Attr):
        return parameter_recipe(recipe.func.obj)
    return False


def inspect_deferred(value: Deferred, *, capture: bool = False) -> DeferredInspection:
    """Inspect every recipe occurrence and compute a source-free stable digest.

    Unknown resolver classes, callbacks, mutable arguments and relational Ibis
    Values fail before execution. This structural boundary intentionally leaves
    stage, unit, method signatures and physical dtype checking to their owners.
    """
    if not isinstance(value, Deferred):
        invalid(
            f"Expected a controlled Ibis Deferred; received {type(value).__name__}. "
            "Build an expression from captured semantic inputs."
        )
    recipe = resolver_of(value)
    inspector = _Inspector(capture=capture)
    inspector.visit(recipe, (), identity=True)
    if (
        not inspector.uses
        and not inspector.symbols
        and not inspector.has_population_count
        and not parameter_recipe(recipe)
        and not inspector.has_population_contains
    ):
        invalid(
            "Expected a semantic dependency; a literal alone cannot declare a Measure or Metric."
        )
    digest = hashlib.sha256(
        json.dumps(inspector.tokens, separators=(",", ":")).encode()
    ).hexdigest()
    return DeferredInspection(
        recipe,
        root_declaration(value),
        tuple(value for _, value in sorted(inspector.dependencies.items())),
        tuple(inspector.uses),
        tuple(inspector.operations),
        tuple(inspector.declarations),
        digest,
        tuple(value for _, value in sorted(inspector.symbols.items())),
    )


def canonical_recipe(recipe: Resolver, slots: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Return canonical tokens for an internal recipe with explicit bound slots.

    Only the supplied Variable names are accepted. Tokens are a non-executable
    traversal summary; the original Resolver remains the computation owner.
    """
    inspector = _Inspector(slots)
    inspector.visit(recipe, ())
    return tuple(inspector.tokens)


def recipe_fingerprint(recipe: Resolver, slots: tuple[str, ...] = ()) -> str:
    """Hash the versioned canonical recipe without evaluating a resolver."""
    payload = json.dumps(canonical_recipe(recipe, slots), separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def inspect_capture(value: Deferred) -> DeferredInspection:
    """Validate the complete authoring recipe without binding forward lookups."""
    return inspect_deferred(value, capture=True)


def capture_function_call(
    function: object, args: tuple[object, ...], kwargs: dict[str, object]
) -> None:
    """Validate one admitted top constructor before a declaration invokes it."""
    actual = next((actual for public, actual, _ in _TOP_FUNCTIONS if function is public), None)
    if actual is None:
        invalid("Expected an admitted top-level Ibis constructor.")
    _Inspector(capture=True).visit(Call(Just(actual), *args, **kwargs), (), identity=True)
