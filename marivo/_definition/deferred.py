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
from marivo._temporal import InstantAnchor, _BuiltinGrain, _SemanticGrain
from marivo.refs import DomainKind, Ref

if TYPE_CHECKING:
    from marivo.datasource.typing import AiContextValue

RECIPE_RULE_VERSION: Final = 1
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
        "divide",
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


# Ibis does not ship py.typed. Keep its untyped inheritance at this one
# runtime boundary, with the exact initializer shape used by these resolvers.
if TYPE_CHECKING:

    class _FrozenResolver:
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

    @property
    def semantic_calls(self) -> tuple[RecipeUse, ...]:
        return tuple(use for use in self.operations if use.symbol in SEMANTIC_METHODS)


def admitted_function(value: object) -> bool:
    """Check identity of approved public Ibis constructors, without calling them."""
    return any(value is public for public, _, _ in _TOP_FUNCTIONS) or value is ibis.literal


def semantic_input(value: SemanticInput) -> Deferred:
    """Capture frozen semantic facts without constructing a physical Ibis Value."""
    return Deferred(SemanticResolver(value))


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


class _Inspector:
    def __init__(self, slots: tuple[str, ...] = ()) -> None:
        self.slots = slots
        self.tokens: list[str] = [str(RECIPE_RULE_VERSION)]
        self.dependencies: dict[tuple[str, str], ExactRef] = {}
        self.uses: list[SemanticUse] = []
        self.operations: list[RecipeUse] = []
        self.declarations: list[DeclarationRecord] = []

    def token(self, *values: str) -> None:
        self.tokens.extend(values)

    def dependency(self, value: ExactRef) -> None:
        self.dependencies[_reference(value)] = value

    def visit(self, recipe: Resolver, path: tuple[str, ...]) -> None:
        if type(recipe) is SemanticResolver:
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
        elif type(recipe) is Sequence:
            if recipe.typ is not tuple and recipe.typ is not list:
                invalid(
                    "Expected a captured tuple or list; use a built-in sequence.",
                    path=path,
                )
            values: tuple[Resolver, ...] = recipe.values
            self.token("tuple", str(len(values)))
            for index, value in enumerate(values):
                self.visit(value, (*path, f"item:{index}"))
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
            PopulationPredicateParameter,
        )

        if isinstance(
            value, (MembershipBinding, PopulationParameter, PopulationPredicateParameter)
        ):
            refs: set[ExactRef] = set()
            if isinstance(value, PopulationParameter):
                self.token("population", computation_fingerprint(value.definition))
                refs.update(direct_refs(value.definition))
            elif isinstance(value, PopulationPredicateParameter):
                self.token("population_predicate", fingerprint(_encode(value.condition, {})))
                _collect_refs(value.condition, refs)
            else:
                self.token("membership", fingerprint(_encode(value, {})))
                _collect_refs(value, refs)
            for reference in refs:
                self.dependency(reference)
            return
        from marivo._temporal import (
            TimeScope,
            _AbsoluteTimeScope,
            _CalendarPeriodTimeScope,
            _SessionTimeScope,
            _TemporalOccurrenceTimeScope,
        )

        if type(value) in (
            TimeScope,
            _AbsoluteTimeScope,
            _SessionTimeScope,
            _CalendarPeriodTimeScope,
            _TemporalOccurrenceTimeScope,
        ):
            assert isinstance(value, TimeScope)
            self.token(
                "time_scope",
                json.dumps(
                    value.contract().model_dump(mode="json"), sort_keys=True, separators=(",", ":")
                ),
            )
            return
        if type(value) is AllHistory:
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
                self.token("window", "to_date", grain.calendar.key, grain.level)
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
            if symbol not in NATIVE_METHODS | SEMANTIC_METHODS:
                self.reject(recipe, path)
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
            self.visit(arg, (*path, role))
        if symbol == "divide":
            kwargs.setdefault("zero_denominator", Just("null"))
        if symbol == "sum":
            kwargs.setdefault("empty", Just("zero"))
        for key, value in sorted(kwargs.items()):
            self.token("parameter", key)
            self.visit(value, (*path, f"parameter:{key}"))
        self.token("end_call")


def inspect_deferred(value: Deferred) -> DeferredInspection:
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
    inspector = _Inspector()
    inspector.visit(recipe, ())
    if not inspector.uses:
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
