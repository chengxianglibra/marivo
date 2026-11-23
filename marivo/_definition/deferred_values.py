"""Typed inert carriers for captured Ibis resolver inputs."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Literal, NoReturn

from ibis.common.bases import FrozenSlotted
from ibis.common.deferred import Attr, Deferred, Just, JustUnhashable, Resolver
from ibis.util import PseudoHashable

from marivo._definition.population_types import PopulationParameter
from marivo._definition.types import (
    FieldRead,
    SourceLocation,
    SumPermission,
)
from marivo._kernel.ai_context import AiContextValue
from marivo._kernel.errors import invalid
from marivo._kernel.paths import RelationshipParameter, _RelationshipValue
from marivo._kernel.refs import DomainKind, ExactRef, Ref, SemanticKind
from marivo._kernel.symbolic import SymbolResolver


@dataclass(frozen=True, slots=True)
class SemanticInput:
    """Frozen physical field facts captured by an owning completed scope."""

    reference: ExactRef
    root: ExactRef
    unit: str | None
    permission: SumPermission
    coordinates: tuple[ExactRef, ...] = ()
    field_read: FieldRead | None = None
    versioned: bool = False
    default_time: ExactRef | None = None


def relationship_carrier(recipe: Resolver) -> _RelationshipValue | None:
    """Read only Ibis's exact literal wrappers around the nominal relationship bridge."""
    candidate: object
    if isinstance(recipe, Just):
        candidate = recipe.value
    elif isinstance(recipe, JustUnhashable):
        wrapped: object = recipe.value
        if not isinstance(wrapped, PseudoHashable):
            return None
        candidate = wrapped.obj
    else:
        return None
    return candidate if isinstance(candidate, _RelationshipValue) else None


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


class CapturedDefaultTimeResolver(_FrozenResolver):
    """An already selected Entity default, including the absence of one."""

    __slots__ = ("field",)
    field: ExactRef | None

    def __init__(self, field: ExactRef | None) -> None:
        super().__init__(field=field)

    def resolve(self, context: dict[str, object]) -> NoReturn:
        invalid("A captured default is a semantic contribution-time fact.")


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


class NamedResolver(_FrozenResolver):
    """One named boundary in the existing recipe graph, with captured display facts."""

    __slots__ = ("recipe", "reference")
    reference: ExactRef
    recipe: Resolver

    def __init__(self, reference: ExactRef, recipe: Resolver) -> None:
        super().__init__(reference=reference, recipe=recipe)

    def resolve(self, context: dict[str, object]) -> NoReturn:
        invalid("Named semantic inputs require the receiving Marivo scope.")


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


def captured_relationship(recipe: Resolver) -> bool:
    if isinstance(recipe, NamedResolver):
        return recipe.reference.ref.kind is SemanticKind.RELATIONSHIP
    return (
        (isinstance(recipe, SymbolResolver) and recipe.reference.kind is SemanticKind.RELATIONSHIP)
        or (isinstance(recipe, Just) and isinstance(recipe.value, RelationshipParameter))
        or relationship_carrier(recipe) is not None
    )
