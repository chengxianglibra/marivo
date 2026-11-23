"""Immutable physical metadata and source-free expression contracts."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, NoReturn, TypeAlias, cast

from ibis.common.deferred import Deferred
from typing_extensions import override

from marivo._definition.deferred import (
    RelationshipParameter,
    SemanticInput,
    _RelationshipValue,
    semantic_input,
)
from marivo._definition.types import (
    ExactRef,
    SumAll,
    SumAllExcept,
    SumAxes,
    SumNone,
    SumPermission,
)
from marivo._kernel.paths import relationship_binding
from marivo._kernel.refs import (
    Ref,
    SemanticKindTag,
)
from marivo._kernel.render import Card, RenderableResult
from marivo._kernel.symbolic import (
    EntitySymbol,
    LookupSymbol,
    RelationshipSymbol,
    symbol_factory,
    symbol_from_ref,
)
from marivo._kernel.temporal import InstantAnchor
from marivo.semantic.errors import ErrorKind, SemanticExpressionError
from marivo.semantic.expressions.inputs import (
    _OMITTED_TIME,
    _OmittedTime,
)
from marivo.semantic.typing import AiContextValue

if TYPE_CHECKING:
    from marivo.datasource.spi import AiContextIR

Cardinality: TypeAlias = Literal["one_to_one", "many_to_one", "one_to_many", "many_to_many"]

RowOperator: TypeAlias = Literal["add", "subtract", "multiply", "divide"]

_SYMBOLS: dict[RowOperator, str] = {"add": "+", "subtract": "-", "multiply": "*", "divide": "/"}


def _fail(kind: ErrorKind, message: str, *, expected: str, received: str, action: str) -> NoReturn:
    raise SemanticExpressionError(
        kind=kind, message=message, expected=expected, received=received, action=action
    )


def _received(value: object) -> str:
    if isinstance(value, int) and value.bit_length() > 192:
        # Describe huge ints by size; decimal conversion is slow and can exceed the str limit.
        return f"{type(value).__name__} with {value.bit_length()} bits"
    return f"{type(value).__name__} {repr(value)[:60]}"


@dataclass(frozen=True, slots=True)
class DefinitionFacts:
    """Declared metadata of one resolved definition; it never enters a computation."""

    name: str
    ai_context: AiContextIR | AiContextValue | None
    location: str
    reads: tuple[Ref[SemanticKindTag], ...]
    details: tuple[tuple[str, str], ...] = ()


ExpressionStage: TypeAlias = Literal[
    "entity",
    "dimension",
    "time_field",
    "measure",
    "row_expression",
    "declared_measure",
    "metric",
    "declared_metric",
    "published_metric",
    "metric_samples",
    "predicate",
    "relationship",
]


@dataclass(frozen=True, repr=False)
class ExpressionContract(RenderableResult):
    """Bounded definition contract of one expression value.

    It discloses the stage, identity, contribution roots, unit, sum permission,
    computation fingerprint, the semantic refs it reads, declared
    metadata and the operations valid at this stage. Reading it never touches
    a source, executes a query, or enumerates a domain.
    """

    stage: ExpressionStage
    _ref: ExactRef | None
    _roots: tuple[ExactRef, ...]
    unit: str | None
    sum_permission: SumPermission | None
    computation: str | None
    _reads: tuple[Ref[SemanticKindTag], ...]
    name: str | None
    business_definition: str | None
    guardrails: tuple[str, ...]
    location: str | None
    facts: tuple[tuple[str, str], ...]
    operations: tuple[str, ...]

    @property
    def ref(self) -> LookupSymbol | None:
        """Return the optional declared identity as a native lookup symbol."""
        return symbol_from_ref(self._ref.ref) if self._ref is not None else None

    @property
    def roots(self) -> tuple[EntitySymbol, ...]:
        """Return the contribution-root lookup symbols in captured order."""
        return tuple(symbol_factory.entity(root.ref.path) for root in self._roots)

    def reads(self) -> tuple[LookupSymbol, ...]:
        """Return the captured definition dependencies as lookup symbols."""
        return tuple(symbol_from_ref(reference) for reference in self._reads)

    @override
    def _repr_identity(self) -> str:
        identity = self._ref.ref.key if self._ref is not None else (self.name or "anonymous")
        return f"ExpressionContract {self.stage} {identity}"

    @override
    def _card(self) -> Card:
        card = Card(identity=self._repr_identity(), available=(".show()",))
        card = card.field("stage", self.stage)
        if self._ref is not None:
            card = card.field("ref", f"{self._ref.ref.key}@{self._ref.version[:19]}")
        if self.name is not None:
            card = card.field("name", self.name)
        if self._roots:
            card = card.field("roots", _bounded_refs(root.ref for root in self._roots))
        if self.unit is not None:
            card = card.field("unit", self.unit)
        if self.sum_permission is not None:
            card = card.field("sum_permission", _permission_text(self.sum_permission))
        if self.computation is not None:
            card = card.field("computation", self.computation[:26])
        card = card.field("reads", _bounded_refs(self._reads) or "(none)")
        for label, value in self.facts[:8]:
            card = card.field(label, value[:160])
        if self.business_definition is not None:
            card = card.field("business_definition", self.business_definition[:200])
        if self.guardrails:
            card = card.field("guardrails", "; ".join(self.guardrails[:3])[:200])
        if self.location is not None:
            card = card.field("declared_at", self.location)
        if self.operations:
            card = card.field("operations", ", ".join(self.operations))
        return card.field(
            "boundary", "definition only; no source read, query, or domain enumeration"
        )


def _bounded_refs(refs: Iterable[Ref[SemanticKindTag]], *, limit: int = 6) -> str:
    keys = [ref.key for ref in refs]
    extra = f" (+{len(keys) - limit} more)" if len(keys) > limit else ""
    return ", ".join(keys[:limit]) + extra


def _permission_text(permission: SumPermission) -> str:
    match permission:
        case SumAll():
            return "sum over every axis"
        case SumNone():
            return "no sum"
        case SumAxes(axes):
            return "sum over " + _bounded_refs(axis.ref for axis in axes)
        case SumAllExcept(axes):
            return "sum over every axis except " + _bounded_refs(axis.ref for axis in axes)


class _ExpressionDisplay:
    """Bounded metadata-only inspection shared by constructed expression values."""

    __slots__ = ()

    def contract(self) -> ExpressionContract:
        """Return this value's bounded definition contract.

        Returns: An ExpressionContract with stage, identity, roots, unit, sum
            permission, computation fingerprint, refs read,
            declared metadata and the operations valid at this stage.
        Example: ``amount.contract().show()``.
        Constraints: Definition facts only; never reads a source or executes.
        """
        return _expression_contract(self)

    def show(self) -> None:
        """Print this value's bounded definition contract without reading source data.

        Returns: None.
        Example: ``amount.show()``.
        Constraints: Definition facts only, not execution evidence.
        """
        self.contract().show()


@dataclass(frozen=True, slots=True)
class _Entity(_ExpressionDisplay):
    """Resolved Entity: one contribution root with its complete primary key."""

    _exact_ref: ExactRef
    primary_key: tuple[str, ...] = field(
        metadata={
            "meaning": "Ordered complete Entity output identity. Naive timestamp keys preserve wall-clock values and exact precision without a timezone; TimeDimension parser and Session timezones do not rewrite identity. Naive and aware identities require explicit compatible representations before matching."
        }
    )
    _facts: DefinitionFacts | None = field(default=None, compare=False, repr=False, kw_only=True)
    _default_time: ExactRef | None = field(default=None, repr=False, kw_only=True)

    @property
    def ref(self) -> EntitySymbol:
        """Return a native lookup symbol for this Entity's identity."""
        return symbol_factory.entity(self._exact_ref.ref.path)

    def count(self, *, time: Deferred | _OmittedTime | None = _OMITTED_TIME) -> Deferred:
        """Capture a count of complete contribution rows without reading a source."""

        value = semantic_input(
            SemanticInput(
                self._exact_ref, self._exact_ref, "1", SumNone(), default_time=self._default_time
            )
        )
        return cast(
            "Deferred", value.count() if isinstance(time, _OmittedTime) else value.count(time=time)
        )

    @override
    def __repr__(self) -> str:
        return f"Entity {self._exact_ref.ref.path}@{self._exact_ref.version[:19]} (use .show())"


def entity_exact(value: _Entity) -> ExactRef:
    """Consume the closed Entity identity without entering Deferred navigation."""
    return value._exact_ref  # noqa: SLF001 - Nominal Entity identity bridge.


@dataclass(frozen=True, slots=True)
class _Relationship(_RelationshipValue, _ExpressionDisplay):
    """Resolved directed relationship, keeping its closed parameter private."""

    _parameter: RelationshipParameter
    _facts: DefinitionFacts | None = field(default=None, compare=False, repr=False, kw_only=True)

    @property
    def ref(self) -> RelationshipSymbol:
        """Return this relationship's lookup symbol."""
        return symbol_factory.relationship(self._parameter.ref.ref.path)

    @property
    def from_entity(self) -> EntitySymbol:
        """Return the source Entity lookup symbol."""
        return symbol_factory.entity(self._parameter.from_entity.ref.path)

    @property
    def to_entity(self) -> EntitySymbol:
        """Return the destination Entity lookup symbol."""
        return symbol_factory.entity(self._parameter.to_entity.ref.path)

    @property
    def cardinality(self) -> Literal["one_to_one", "many_to_one", "one_to_many", "many_to_many"]:
        """Return the captured relationship cardinality."""
        return self._parameter.cardinality

    @override
    def __repr__(self) -> str:
        exact = self._parameter.ref
        return f"Relationship {exact.ref.path}@{exact.version[:19]} (use .show())"


def relationship_parameter(value: _Relationship) -> RelationshipParameter:
    """Consume the immutable internal relationship facts."""
    return relationship_binding(value)


class _FieldMetadata(_ExpressionDisplay):
    """Private physical facts; author-facing inputs use native Deferred."""

    path: tuple[_Relationship, ...]
    anchor: InstantAnchor | None
    _default_time: ExactRef | None

    def require_native(self, *, operation: str) -> None:
        if self.path or self.anchor is not None:
            _fail(
                ErrorKind.INVALID_TIME_PATH,
                "A bound field cannot be used in physical authoring.",
                expected="an unbound native field",
                received=operation,
                action="Use the native captured field Ref in physical declarations.",
            )


@dataclass(frozen=True, slots=True, eq=False)
class Dimension(_FieldMetadata):
    ref: ExactRef
    root: ExactRef
    anchor: InstantAnchor | None = field(default=None, kw_only=True)
    _versioned: bool = field(default=False, compare=False, repr=False, kw_only=True)
    path: tuple[_Relationship, ...] = field(default=(), kw_only=True)
    _facts: DefinitionFacts | None = field(default=None, compare=False, repr=False, kw_only=True)
    _default_time: ExactRef | None = field(default=None, repr=False, kw_only=True)


@dataclass(frozen=True, slots=True, eq=False)
class TimeField(_FieldMetadata):
    ref: ExactRef
    root: ExactRef
    path: tuple[_Relationship, ...] = ()
    anchor: InstantAnchor | None = field(default=None, kw_only=True)
    _versioned: bool = field(default=False, compare=False, repr=False, kw_only=True)
    _facts: DefinitionFacts | None = field(default=None, compare=False, repr=False, kw_only=True)
    _default_time: ExactRef | None = field(default=None, repr=False, kw_only=True)


@dataclass(frozen=True, slots=True, eq=False)
class Measure(_FieldMetadata):
    ref: ExactRef
    root: ExactRef
    unit: str | None
    permission: SumPermission
    coordinates: tuple[ExactRef, ...] = ()
    anchor: InstantAnchor | None = field(default=None, kw_only=True)
    _versioned: bool = field(default=False, compare=False, repr=False, kw_only=True)
    path: tuple[_Relationship, ...] = field(default=(), kw_only=True)
    _facts: DefinitionFacts | None = field(default=None, compare=False, repr=False, kw_only=True)
    _default_time: ExactRef | None = field(default=None, repr=False, kw_only=True)


def _expression_contract(value: object) -> ExpressionContract:
    """Bounded metadata disclosure; numerical definitions belong to Catalog/Query."""
    if not isinstance(value, (_Entity, _Relationship, Dimension, TimeField, Measure)):
        raise TypeError(f"no physical definition contract for {type(value).__name__}")
    facts = value._facts  # noqa: SLF001 - Closed physical expression metadata.
    if isinstance(value, _Entity):
        stage: ExpressionStage = "entity"
        roots: tuple[ExactRef, ...] = (entity_exact(value),)
        operations: tuple[str, ...] = ("count",)
    elif isinstance(value, _Relationship):
        stage, roots, operations = (
            "relationship",
            (relationship_binding(value).from_entity, relationship_binding(value).to_entity),
            ("via",),
        )
    else:
        stage = (
            "measure"
            if isinstance(value, Measure)
            else "time_field"
            if isinstance(value, TimeField)
            else "dimension"
        )
        roots, operations = (value.root,), ()
    details = facts.details if facts else ()
    if isinstance(value, _Relationship):
        details = (
            ("from", relationship_binding(value).from_entity.ref.key),
            ("to", relationship_binding(value).to_entity.ref.key),
            ("cardinality", value.cardinality),
            *details,
        )
    return ExpressionContract(
        stage=stage,
        _ref=entity_exact(value)
        if isinstance(value, _Entity)
        else relationship_binding(value).ref
        if isinstance(value, _Relationship)
        else value.ref,
        _roots=roots,
        unit=value.unit if isinstance(value, Measure) else None,
        sum_permission=value.permission if isinstance(value, Measure) else None,
        computation=None,
        _reads=facts.reads if facts else (),
        name=facts.name
        if facts
        else (
            entity_exact(value)
            if isinstance(value, _Entity)
            else relationship_binding(value).ref
            if isinstance(value, _Relationship)
            else value.ref
        ).ref.name,
        business_definition=getattr(facts.ai_context, "business_definition", None)
        if facts
        else None,
        guardrails=tuple(getattr(facts.ai_context, "guardrails", ()) or ()) if facts else (),
        location=facts.location if facts else None,
        facts=details,
        operations=operations,
    )


def expression_default_time(value: _Entity | _FieldMetadata) -> ExactRef | None:
    return value._default_time  # noqa: SLF001 - Captured native contribution clock.


def field_is_versioned(value: Dimension | TimeField | Measure) -> bool:
    return value._versioned  # noqa: SLF001 - Physical identity version facts of the field.
