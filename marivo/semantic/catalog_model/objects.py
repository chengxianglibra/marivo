"""Typed entries and the read contract used by captured Catalog objects."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    ClassVar,
    Generic,
    Literal,
    NoReturn,
    Protocol,
    TypeAlias,
    TypeVar,
    cast,
    final,
)

from ibis.common.deferred import Attr, Call, Deferred, Resolver

from marivo._definition.deferred import (
    NamedResolver,
    RelationshipParameter,
    SemanticInput,
    SemanticResolver,
    root_declaration,
)
from marivo._definition.types import (
    DefinitionGraphError,
    InputContext,
    RelationshipStepParameter,
    SumNone,
)
from marivo._kernel.refs import (
    DatasourceKind,
    DimensionKind,
    DomainKind,
    EntityKind,
    MeasureKind,
    MetricKind,
    Ref,
    RelationshipKind,
    SemanticKind,
    SemanticKindTag,
    TimeDimensionKind,
)
from marivo._kernel.render import (
    Card,
    RenderableResult,
)
from marivo._kernel.symbolic import (
    DatasourceIdentityHandle,
    LookupSymbol,
    _DatasourceIdentity,
)
from marivo.semantic._expression_values import (
    MetricExpression,
    NumericMetric,
    RowExpression,
    TimeExpression,
    _merge,
    _parameter,
    _source_arguments,
)
from marivo.semantic._metric_time_disclosure import add_metric_time
from marivo.semantic.catalog_entries import (
    CatalogPage,
    DatasourceDefinition,
    DomainDefinition,
    EntityDefinition,
    EntryDefinition,
    EntryProvenance,
    FieldDefinition,
    JsonValue,
    MetricDefinition,
    RelationshipDefinition,
    _json_value,
)
from marivo.semantic.catalog_model.details import (
    _CatalogObjectDetails,
)
from marivo.semantic.definition_resolution import (
    ResolvedValue,
)
from marivo.semantic.errors import (
    ErrorKind,
    SemanticExpressionError,
    SemanticRuntimeError,
    _raise,
)
from marivo.semantic.expressions.inputs import (
    _OMITTED_TIME,
    CatalogInput,
    CatalogRelationshipInput,
    _OmittedTime,
)
from marivo.semantic.expressions.objects import (
    _Entity,
    _Relationship,
)
from marivo.semantic.static_check.source_timezone import (
    effective_source_timezone,
    source_timezone_axis,
)
from marivo.telemetry import traced

if TYPE_CHECKING:
    from marivo._kernel.temporal import InstantAnchor
    from marivo.semantic.compiled_types import CompiledSemanticState
    from marivo.semantic.static_check.registry import Registry


CatalogEntryKindT = TypeVar("CatalogEntryKindT", bound=SemanticKindTag, covariant=True)


@dataclass(frozen=True, repr=False, eq=False)
class _SemanticObject(RenderableResult, CatalogInput, Generic[CatalogEntryKindT]):
    """One immutable browsable object in one compiled semantic catalog."""

    _ref: Ref[CatalogEntryKindT]
    _details: _CatalogObjectDetails | None
    _catalog: CatalogLookup

    _navigation_names: ClassVar[tuple[str, ...]] = ()

    __hash__ = None  # type: ignore[assignment]  # Semantic objects have no implicit business equality.

    @property
    def _context(self) -> InputContext:
        return InputContext(Path(self._catalog.workspace_dir).resolve())

    @property
    def id(self) -> str:
        """Canonical copyable identity for exact catalog lookup."""
        return self._ref.key

    @property
    def kind(self) -> str:
        """The semantic kind; concrete entry variants narrow this literal."""
        return self._ref.kind.value

    @property
    def path(self) -> str:
        """Complete semantic path within the loaded scope."""
        return self._ref.path

    @property
    def name(self) -> str:
        """Local authored name."""
        return self._ref.name

    @property
    def domain(self) -> str | None:
        """Declared owning domain, or None for project-wide datasources."""
        return None if self._ref.kind is SemanticKind.DATASOURCE else self.path.split(".")[0]

    @property
    def summary(self) -> str:
        """Complete authored business definition, without a display budget."""
        return self.definition.context.business_definition or self.name

    @property
    def definition(self) -> EntryDefinition:
        """Complete kind-specific captured definition; no source data is read."""
        return self._catalog._definition(self._ref)

    @property
    def provenance(self) -> EntryProvenance:
        """Captured declaration source and compiled snapshot identity."""
        captured = self._catalog._state.definitions.get(self._ref)
        location = getattr(captured, "location", None)
        if location is None and self._ref.kind in {SemanticKind.METRIC, SemanticKind.MEASURE}:
            value = self._catalog._resolved_value(self._ref)
            declaration = root_declaration(value) if isinstance(value, Deferred) else None
            location = declaration.location if declaration is not None else None
        details = self._details
        location = location or getattr(details, "source_location", None)
        return EntryProvenance(
            str(getattr(location, "file", getattr(location, "path", ""))),
            int(getattr(location, "line", 0)),
            str(getattr(captured, "python_symbol", getattr(details, "python_symbol", ""))),
            self._catalog.definition_fingerprint,
        )

    @traced(surface="semantic", operation="semantic_object.related", kind="method")
    def related(
        self, relation: RelatedRelation, /, *, limit: int = 10, cursor: str | None = None
    ) -> CatalogPage[SemanticObject]:
        """Browse loaded related entries with explicit missing dependency identities.

        Args:
            relation: dependencies, dependents, fields, metrics, relationships or members.
            limit: Page size.
            cursor: Continuation from the same relation and snapshot.

        Returns:
            A bounded page in this Catalog's full loaded scope.

        Example:
            metric.related("dependencies").show()

        Constraints:
            Candidates do not guarantee Analysis composition validity.
        """
        return self._catalog._related(self, relation, limit=limit, cursor=cursor)

    @traced(surface="semantic", operation="semantic_object.to_dict", kind="method")
    def to_dict(self) -> dict[str, JsonValue]:
        """Return complete JSON-compatible definition and provenance data.

        Returns:
            A fresh dictionary containing captured metadata and schema_version.

        Example:
            definition = entry.to_dict()["definition"]

        Constraints:
            No execution values, registry objects, model calls or datasource reads.
        """
        return {
            "schema_version": 1,
            "id": self.id,
            "kind": self.kind,
            "path": self.path,
            "name": self.name,
            "domain": self.domain,
            "summary": self.summary,
            "definition": _json_value(self.definition),
            "provenance": _json_value(self.provenance),
        }

    @traced(surface="semantic", operation="semantic_object.to_json", kind="method")
    def to_json(self) -> str:
        """Encode this complete entry without an executable value or display budget.

        Returns:
            JSON text for the complete versioned entry projection.

        Example:
            text = entry.to_json()

        Constraints:
            Captured metadata only; encoding does not execute the semantic value.
        """
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    @traced(surface="semantic", operation="semantic_object.show", kind="method")
    def show(
        self,
        *,
        section: Literal["definition", "context"] | None = None,
        max_output_bytes: int | None = 8192,
    ) -> None:
        """Print captured entry facts, optionally selecting the full definition/context.

        Args:
            section: Definition or context projection.
            max_output_bytes: Display budget; None prints the complete selected projection.

        Returns:
            None.

        Example:
            entry.show(section="definition", max_output_bytes=None)

        Constraints:
            No datasource or model call; .to_json() is always complete.
        """
        if section not in {None, "definition", "context"}:
            _raise(
                ErrorKind.INVALID_REF,
                f"Unknown Object display section {section!r}.",
                cls=SemanticRuntimeError,
                expected="definition, context or None",
                received=str(section),
                hint="Use entry.show(section='definition') or entry.show(section='context').",
            )
        if section is None:
            print(self._card().render(max_output_bytes=max_output_bytes))
        else:
            value = self.definition.context if section == "context" else self.definition
            card = Card(
                identity=self._repr_identity(),
                available=(".definition", ".to_json()", ".show(max_output_bytes=None)"),
            )
            card.field(section, json.dumps(_json_value(value), ensure_ascii=False))
            print(card.render(max_output_bytes=max_output_bytes))

    def _repr_identity(self) -> str:
        return f"{type(self).__name__} {self.id}"

    def _card(self) -> Card:
        definition = self.definition
        available = [
            ".definition",
            ".to_json()",
            '.related("dependencies")',
            '.show(section="definition", max_output_bytes=None)',
        ]
        if isinstance(definition, (MetricDefinition, FieldDefinition, EntityDefinition)):
            available.append("direct typed expression and Analysis input")
        elif isinstance(definition, RelationshipDefinition):
            available.append(
                "direct relationship path input"
                if definition.cardinality != "outside_scope"
                else "ms.load(domains=...) -> include outside_scope endpoints"
            )
        elif isinstance(definition, (DomainDefinition, DatasourceDefinition)):
            available.append('.related("members")')
        card = Card(identity=self._repr_identity(), available=tuple(available)).field(
            "business_definition", self.summary
        )
        card.field("capture_workspace", str(self._catalog.workspace_dir))
        card.field("capture_fingerprint", self._catalog.definition_fingerprint)
        if definition.context.guardrails:
            card.listing("guardrails", definition.context.guardrails)
        if isinstance(definition, MetricDefinition):
            card.field("formula", definition.formula)
            card.field("explanation", definition.explanation)
            card.field("unit", definition.unit or "(none)")
            add_metric_time(
                card, definition.time_bindings, recovery="read .definition.time_bindings"
            )
            for dependency in definition.dependency_context:
                if dependency.guardrails:
                    card.listing(f"guardrails from {dependency.id}", dependency.guardrails)
        elif isinstance(definition, FieldDefinition):
            card.field("expression", definition.expression)
            card.field("unit", definition.unit or "(none)")
            dimension = self._catalog._reg.dimensions.get(self._ref.path)
            if dimension is not None and dimension.is_time_dimension:
                source = effective_source_timezone(
                    self._catalog._reg,
                    source_timezone_axis(self._catalog._reg, self._ref.path),
                )
                if source is not None:
                    owner = self._catalog._reg.entities[dimension.entity].datasource
                    location = f"datasource {owner}" if source.origin == "datasource" else "field"
                    inherited = (
                        f"; inherited from {source.inherited_from}" if source.inherited_from else ""
                    )
                    card.field(
                        "source_timezone",
                        f"naive values: {source.timezone} ({location}){inherited}",
                    )
                else:
                    card.field("source_timezone", "naive values: missing declaration")

        elif isinstance(definition, EntityDefinition):
            card.field("expression", definition.expression)
            card.field("primary_key", ", ".join(definition.primary_key))
            card.field("datasource", definition.datasource)
            card.field("default_time", definition.default_time or "(none)")
        if isinstance(definition, DomainDefinition):
            card.field("owner", definition.owner)
        elif isinstance(definition, DatasourceDefinition):
            card.field("backend_type", definition.backend_type)
            card.field("wall_clock_timezone", definition.wall_clock_timezone or "(none)")
        elif isinstance(definition, RelationshipDefinition):
            card.field("direction", f"{definition.from_entity} -> {definition.to_entity}")
            card.field("keys", str(definition.keys))
            card.field("cardinality", definition.cardinality)
        return card


@dataclass(frozen=True, repr=False, eq=False)
class Domain(_SemanticObject[DomainKind]):
    """Immutable domain catalog entry."""

    @property
    def kind(self) -> Literal["domain"]:
        return "domain"

    @property
    def definition(self) -> DomainDefinition:
        return cast("DomainDefinition", super().definition)


@dataclass(frozen=True, repr=False, eq=False)
class Datasource(_SemanticObject[DatasourceKind], DatasourceIdentityHandle):
    """Immutable datasource catalog entry."""

    @property
    def kind(self) -> Literal["datasource"]:
        return "datasource"

    @property
    def _datasource_identity(self) -> _DatasourceIdentity:
        return _DatasourceIdentity(self._ref, self._catalog.workspace_dir.resolve())

    @property
    def definition(self) -> DatasourceDefinition:
        return cast("DatasourceDefinition", super().definition)


RelatedRelation: TypeAlias = Literal[
    "dependencies", "dependents", "fields", "metrics", "relationships", "members"
]


@dataclass(frozen=True, repr=False, eq=False)
class Entity(_SemanticObject[EntityKind]):
    """A named Entity with captured metadata and direct identity operations."""

    @property
    def kind(self) -> Literal["entity"]:
        return "entity"

    @property
    def definition(self) -> EntityDefinition:
        return cast("EntityDefinition", super().definition)

    @property
    def primary_key(self) -> tuple[str, ...]:
        """Ordered complete Entity output identity.

        Naive timestamp keys preserve wall-clock values and exact precision without a
        timezone. TimeDimension parsers and Session timezones do not rewrite identity.
        Naive and aware keys require explicitly compatible representations before matching.
        """
        return self.definition.primary_key

    @traced(surface="semantic", operation="entity.count", kind="method")
    def count(self, *, time: TimeExpression | _OmittedTime | None = _OMITTED_TIME) -> NumericMetric:
        """Capture an anonymous Metric counting this Entity's contributing rows.

        Args:
            time: Contribution TimeDimension. Omit to use this Entity's default if declared.

        Returns:
            A numeric MetricExpression retaining the Entity identity and input context.

        Example:
            ``session.observe({"orders": orders.count(time=paid_at)}, where=period)``.

        Constraints:
            Construction reads no source. The receiver binds the named Entity in its
            completed scope; use a Mapping to give this anonymous output a name.
        """

        value = self._catalog._resolved_value(self._ref)
        assert isinstance(value, _Entity)
        # Keep the Entity boundary inside count, so receiver scope owns identity facts.

        leaf = NamedResolver(
            value._exact_ref,
            SemanticResolver(
                SemanticInput(
                    value._exact_ref,
                    value._exact_ref,
                    "1",
                    SumNone(),
                    default_time=value._default_time,
                )
            ),
        )
        recipe = Call(
            Attr(leaf, "count"),
            **{key: _parameter(item) for key, item in _source_arguments(leaf, time).items()},
        )
        return MetricExpression(recipe, _merge(self._context, time), "numeric")


@dataclass(frozen=True, repr=False, eq=False)
class Dimension(_SemanticObject[DimensionKind], RowExpression[Literal["dimension"]]):
    """A named Dimension with captured metadata and row comparison operations."""

    @property
    def kind(self) -> Literal["dimension"]:
        return "dimension"

    @property
    def definition(self) -> FieldDefinition:
        return cast("FieldDefinition", super().definition)

    @property
    def _role(self) -> Literal["dimension"]:
        return "dimension"

    @property
    def _recipe(self) -> Resolver:

        return self._catalog._named_recipe(self._ref)


@dataclass(frozen=True, repr=False, eq=False)
class TimeDimension(_SemanticObject[TimeDimensionKind], RowExpression[Literal["time"]]):
    """A named TimeDimension with captured metadata and time operations."""

    @property
    def kind(self) -> Literal["time_dimension"]:
        return "time_dimension"

    @property
    def definition(self) -> FieldDefinition:
        return cast("FieldDefinition", super().definition)

    @property
    def _role(self) -> Literal["time"]:
        return "time"

    @property
    def _recipe(self) -> Resolver:

        return self._catalog._named_recipe(self._ref)


@dataclass(frozen=True, repr=False, eq=False)
class Measure(_SemanticObject[MeasureKind], RowExpression[Literal["numeric"]]):
    """A named Measure with captured metadata and numeric row operations."""

    @property
    def kind(self) -> Literal["measure"]:
        return "measure"

    @property
    def definition(self) -> FieldDefinition:
        return cast("FieldDefinition", super().definition)

    @property
    def _role(self) -> Literal["numeric"]:
        return "numeric"

    @property
    def _recipe(self) -> Resolver:

        return self._catalog._named_recipe(self._ref)


@dataclass(frozen=True, repr=False, eq=False)
class Metric(_SemanticObject[MetricKind], MetricExpression[Literal["numeric"]]):
    """A named Metric with captured metadata and completed metric operations."""

    @property
    def kind(self) -> Literal["metric"]:
        return "metric"

    @property
    def definition(self) -> MetricDefinition:
        return cast("MetricDefinition", super().definition)

    @property
    def _role(self) -> Literal["numeric"]:
        return "numeric"

    @property
    def _recipe(self) -> Resolver:

        return self._catalog._named_recipe(self._ref)


@dataclass(frozen=True, repr=False, eq=False)
class Relationship(_SemanticObject[RelationshipKind], CatalogRelationshipInput):
    """A named directed Relationship with captured metadata and endpoint navigation."""

    @property
    def kind(self) -> Literal["relationship"]:
        return "relationship"

    @property
    def definition(self) -> RelationshipDefinition:
        return cast("RelationshipDefinition", super().definition)

    @property
    def _parameter(self) -> RelationshipParameter:
        value = self._catalog._resolved_value(self._ref)
        assert isinstance(value, _Relationship)
        return value._parameter

    @property
    def from_entity(self) -> Entity:
        value = self._catalog.get(self.definition.from_entity)
        assert isinstance(value, Entity)
        return value

    @property
    def to_entity(self) -> Entity:
        value = self._catalog.get(self.definition.to_entity)
        assert isinstance(value, Entity)
        return value

    @property
    def cardinality(self) -> str:
        return self.definition.cardinality

    @traced(surface="semantic", operation="relationship.at", kind="method")
    def at(self, anchor: InstantAnchor) -> RelationshipStep:
        """Select an exact version of this step's target Entity.

        Args: anchor: An InstantAnchor from session.instant(...).
        Returns: A RelationshipStep for mv.path(...).
        Example: ``mv.path(order_customer.at(customer_time), customer_region)``.
        Constraints: The target must be versioned. The consuming operation must use its version row.
        """
        if anchor is None:
            raise SemanticExpressionError(
                kind=ErrorKind.INVALID_TIME_PATH,
                message="Relationship.at requires an exact instant.",
                expected="an InstantAnchor from session.instant(...)",
                received="None",
                action="Pass session.instant(...) or use the unanchored Relationship directly.",
                help_target="relationship.at",
            )
        return _step(self, anchor)


SemanticObject: TypeAlias = (
    Domain | Datasource | Entity | Dimension | TimeDimension | Measure | Metric | Relationship
)

_ENTRY_TYPE_BY_KIND: dict[SemanticKind, type[_SemanticObject[SemanticKindTag]]] = {
    SemanticKind.ENTITY: Entity,
    SemanticKind.DIMENSION: Dimension,
    SemanticKind.TIME_DIMENSION: TimeDimension,
    SemanticKind.MEASURE: Measure,
    SemanticKind.METRIC: Metric,
    SemanticKind.RELATIONSHIP: Relationship,
    SemanticKind.DOMAIN: Domain,
    SemanticKind.DATASOURCE: Datasource,
}


@final
@dataclass(frozen=True, slots=True, init=False, repr=False)
class RelationshipStep:
    """One captured directed Relationship and optional target version instant."""

    relationship: Relationship = field(compare=False, hash=False)
    target_at: InstantAnchor | None
    _parameter: RelationshipStepParameter

    def __init__(self) -> None:
        raise TypeError("Use relationship.at(anchor) or mv.path(...).steps.")

    def __init_subclass__(cls) -> NoReturn:
        raise TypeError("RelationshipStep is a closed value type.")

    @property
    def _context(self) -> InputContext:
        return self.relationship._context

    def __repr__(self) -> str:
        return f"<RelationshipStep {self.relationship.id[:150]}; use .show()>"

    def show(self) -> None:
        """Print the captured Relationship identity and target instant."""
        print(repr(self))
        print(f"target_at={self.target_at}")


def _step(relationship: Relationship, anchor: InstantAnchor | None) -> RelationshipStep:
    try:
        facts = RelationshipStepParameter(relationship._parameter, anchor)
    except DefinitionGraphError as error:
        raise SemanticExpressionError(
            kind=ErrorKind.INVALID_TIME_PATH,
            message="Relationship target version selection is invalid.",
            expected="an exact anchor on a versioned Relationship target",
            received=str(error),
            action="Use relationship.at(session.instant(...)) on a versioned target or omit its anchor.",
            help_target="relationship.at",
        ) from error
    value = object.__new__(RelationshipStep)
    object.__setattr__(value, "relationship", relationship)
    object.__setattr__(value, "target_at", anchor)
    object.__setattr__(value, "_parameter", facts)
    return value


class CatalogLookup(Protocol):
    """Captured identity and definition reads needed by named objects."""

    @property
    def workspace_dir(self) -> Path: ...

    @property
    def definition_fingerprint(self) -> str: ...

    @property
    def _state(self) -> CompiledSemanticState: ...

    @property
    def _reg(self) -> Registry: ...

    def _definition(self, ref: Ref[SemanticKindTag]) -> EntryDefinition: ...

    def _resolved_value(self, ref: Ref[SemanticKindTag]) -> ResolvedValue: ...

    def _named_recipe(self, ref: Ref[SemanticKindTag]) -> Resolver: ...

    def _related(
        self,
        entry: _SemanticObject[SemanticKindTag],
        relation: RelatedRelation,
        *,
        limit: int,
        cursor: str | None,
    ) -> CatalogPage[SemanticObject]: ...

    def get(self, id: str | LookupSymbol, /) -> SemanticObject: ...
