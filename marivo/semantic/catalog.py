"""SemanticCatalog — unified agent-facing read surface for marivo.semantic.

Public entrypoint: ms.load() -> SemanticCatalog
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Literal, NoReturn, cast

from marivo.datasource.authoring import DatasourceRef
from marivo.datasource.engines import require_profile_for_backend_type
from marivo.datasource.ir import AiContextIR, DatasourceIR, DatasourceSourceLocation
from marivo.datasource.source import AuthoringScope
from marivo.preview import (
    METRIC_PREVIEW_SAMPLE_SIZE,
    PREVIEW_DEFAULT_LIMIT,
    PreviewResult,
    PreviewSamplePolicy,
    PreviewWarning,
    preview_ibis_table,
    preview_ibis_value,
    validate_preview_limit,
)
from marivo.refs import SemanticRef
from marivo.render import Card, FieldSection, ListSection, RenderableResult, Section
from marivo.semantic.constraints import ConstraintId
from marivo.semantic.dtos import DatasetSource
from marivo.semantic.errors import ErrorKind, SemanticLoadFailed, SemanticRuntimeError, _raise
from marivo.semantic.ir import (
    DateParse,
    DatetimeParse,
    DimensionIR,
    DomainIR,
    EntityIR,
    EntityVersioningIR,
    HourPrefixParse,
    LinearComposition,
    MeasureIR,
    MetricIR,
    ParityStatus,
    RelationshipIR,
    SampleIntervalIR,
    SemiAdditive,
    SnapshotVersioningIR,
    SourceLocation,
    SqlProvenance,
    StrptimeParse,
    SymbolKind,
    TimestampParse,
    ValidityVersioningIR,
    additivity_bucket,
    composition_components,
)
from marivo.semantic.parity import propagated_parity_status
from marivo.semantic.preview_checks import (
    PreviewUsing,
    normalize_preview_bindings,
    persist_preview_check,
)
from marivo.semantic.refs import make_ref

if TYPE_CHECKING:
    from marivo.introspection.live.model import AuthoringContract
    from marivo.semantic.dtos import VerifyResult
    from marivo.semantic.reader import SemanticProject
    from marivo.semantic.readiness import ReadinessReport
    from marivo.semantic.resolver import SemanticResolver
    from marivo.semantic.validator import Registry

__all__ = [
    "AiContextView",
    "CatalogCollection",
    "CatalogObject",
    "Datasource",
    "DatasourceDetails",
    "DerivedMetricDetails",
    "Dimension",
    "DimensionDetails",
    "Domain",
    "DomainDetails",
    "Entity",
    "EntityDetails",
    "EntityVersioning",
    "Measure",
    "MeasureDetails",
    "Metric",
    "MetricDetails",
    "Relationship",
    "RelationshipDetails",
    "SemanticCatalog",
    "SemanticKind",
    "SemanticRef",
    "SimpleMetricDetails",
    "SnapshotVersioning",
    "TimeDimension",
    "TimeDimensionDetails",
    "ValidityVersioning",
    "load",
]

# SemanticKind is a stable alias for the internal SymbolKind enum.
# Both share the same values: domain, datasource, entity, dimension,
# measure, time_dimension, metric, relationship.
SemanticKind = SymbolKind
AiContextView = AiContextIR
SnapshotVersioning = SnapshotVersioningIR
ValidityVersioning = ValidityVersioningIR
EntityVersioning = EntityVersioningIR


# ---------------------------------------------------------------------------
# Kind-specific details
# ---------------------------------------------------------------------------


def _source_location_text(source_location: SourceLocation) -> str:
    return f"{source_location.file}:{source_location.line}"


def _format_ref(ref: SemanticRef | None) -> str:
    return ref.id if ref is not None else "(none)"


def _format_refs(refs: tuple[SemanticRef, ...], *, limit: int = 6) -> str:
    if not refs:
        return "(none)"
    visible = [ref.id for ref in refs[:limit]]
    if len(refs) > limit:
        visible.append(f"... (+{len(refs) - limit} more)")
    return ", ".join(visible)


def _format_tuple_values(values: tuple[str, ...], *, limit: int = 6) -> str:
    if not values:
        return "(none)"
    visible = list(values[:limit])
    if len(values) > limit:
        visible.append(f"... (+{len(values) - limit} more)")
    return ", ".join(visible)


def _format_mapping(mapping: dict[str, object] | dict[str, str]) -> str:
    if not mapping:
        return "(none)"
    return ", ".join(f"{key}: {value}" for key, value in sorted(mapping.items()))


def _source_text(source: DatasetSource) -> str:
    if hasattr(source, "to_dict"):
        return str(source.to_dict())
    return repr(source)


def _versioning_text(versioning: EntityVersioning | None) -> str:
    if versioning is None:
        return "(none)"
    return repr(versioning)


def _provenance_text(provenance: SqlProvenance | None) -> str:
    if provenance is None:
        return "(none)"
    return f"{provenance.kind} dialect={provenance.dialect} sql={provenance.sql!r}"


def _common_detail_sections(
    *,
    context: AiContextView,
    python_symbol: str,
    source_location: SourceLocation,
    parents: tuple[SemanticRef, ...],
    children: tuple[SemanticRef, ...],
    dependents: tuple[SemanticRef, ...],
) -> list[Section]:
    sections: list[Section] = [
        FieldSection(label="business_definition", value=context.business_definition or "(none)"),
        ListSection(label="guardrails", items=tuple(context.guardrails) or ()),
    ]
    if context.synonyms:
        sections.append(
            FieldSection(label="synonyms", value=_format_tuple_values(context.synonyms))
        )
    if context.examples:
        sections.append(ListSection(label="examples", items=tuple(context.examples)))
    if context.instructions:
        sections.append(FieldSection(label="instructions", value=context.instructions))
    if context.owner_notes:
        sections.append(FieldSection(label="owner_notes", value=context.owner_notes))
    sections.extend(
        (
            FieldSection(label="source_location", value=_source_location_text(source_location)),
            FieldSection(label="python_symbol", value=python_symbol or "(none)"),
            FieldSection(label="parents", value=_format_refs(parents)),
            FieldSection(label="children", value=_format_refs(children)),
            FieldSection(label="dependents", value=_format_refs(dependents)),
        )
    )
    return sections


@dataclass(frozen=True, repr=False)
class _DetailsBase(RenderableResult):
    """Common fields and result protocol shared by all *Details classes."""

    ref: SemanticRef
    kind: SemanticKind
    name: str
    domain: str | None
    context: AiContextView
    source_location: SourceLocation
    parents: tuple[SemanticRef, ...]
    children: tuple[SemanticRef, ...]
    dependents: tuple[SemanticRef, ...]
    python_symbol: str

    def _repr_identity(self) -> str:
        return f"{self.__class__.__name__} ref={self.ref.id}"

    def _detail_sections(self) -> list[Section]:
        raise NotImplementedError

    def _card(self) -> Card:
        card = Card(identity=self._repr_identity(), available=(".show()",))
        for section in self._detail_sections():
            card = card.section(section)
        typed_id = _catalog_typed_id(self.ref.id, self.kind)
        card = card.listing(
            label="suggested next calls",
            items=(
                f"catalog.verify_object(catalog.get('{typed_id}').ref) to confirm reachability",
                f"catalog.readiness(refs=[catalog.get('{typed_id}').ref]) to certify authored changes",
            ),
        )
        return card


@dataclass(frozen=True, repr=False)
class DatasourceDetails(_DetailsBase):
    """Details for a datasource object."""

    backend_type: str
    fields: dict[str, object]
    env_refs: dict[str, str]

    def _detail_sections(self) -> list[Section]:
        sections = _common_detail_sections(
            context=self.context,
            python_symbol=self.python_symbol,
            source_location=self.source_location,
            parents=self.parents,
            children=self.children,
            dependents=self.dependents,
        )
        sections.extend(
            (
                FieldSection(label="backend_type", value=self.backend_type),
                FieldSection(label="fields", value=_format_mapping(self.fields)),
                FieldSection(label="env_refs", value=_format_mapping(self.env_refs)),
            )
        )
        return sections


@dataclass(frozen=True, repr=False)
class DomainDetails(_DetailsBase):
    """Details for a domain object."""

    owner: str
    default: bool

    def _detail_sections(self) -> list[Section]:
        sections = _common_detail_sections(
            context=self.context,
            python_symbol=self.python_symbol,
            source_location=self.source_location,
            parents=self.parents,
            children=self.children,
            dependents=self.dependents,
        )
        sections.extend(
            (
                FieldSection(label="owner", value=self.owner),
                FieldSection(label="default", value=str(self.default)),
            )
        )
        return sections


@dataclass(frozen=True, repr=False)
class EntityDetails(_DetailsBase):
    """Details for an entity object."""

    datasource: DatasourceRef
    source: DatasetSource
    primary_key: tuple[str, ...]
    versioning: EntityVersioning | None

    def _detail_sections(self) -> list[Section]:
        sections = _common_detail_sections(
            context=self.context,
            python_symbol=self.python_symbol,
            source_location=self.source_location,
            parents=self.parents,
            children=self.children,
            dependents=self.dependents,
        )
        sections.extend(
            (
                FieldSection(label="datasource", value=self.datasource.id),
                FieldSection(label="source", value=_source_text(self.source)),
                FieldSection(label="primary_key", value=_format_tuple_values(self.primary_key)),
                FieldSection(label="versioning", value=_versioning_text(self.versioning)),
            )
        )
        return sections


@dataclass(frozen=True, repr=False)
class DimensionDetails(_DetailsBase):
    """Details for a categorical dimension object."""

    entity: SemanticRef

    def _detail_sections(self) -> list[Section]:
        sections = _common_detail_sections(
            context=self.context,
            python_symbol=self.python_symbol,
            source_location=self.source_location,
            parents=self.parents,
            children=self.children,
            dependents=self.dependents,
        )
        sections.append(FieldSection(label="entity", value=self.entity.id))
        return sections


@dataclass(frozen=True, repr=False)
class MeasureDetails(_DetailsBase):
    """Details for a row-level quantitative measure object."""

    entity: SemanticRef
    additivity: Literal["additive", "semi_additive", "non_additive"]
    unit: str | None

    def _detail_sections(self) -> list[Section]:
        sections = _common_detail_sections(
            context=self.context,
            python_symbol=self.python_symbol,
            source_location=self.source_location,
            parents=self.parents,
            children=self.children,
            dependents=self.dependents,
        )
        sections.extend(
            (
                FieldSection(label="entity", value=self.entity.id),
                FieldSection(label="additivity", value=self.additivity),
            )
        )
        if self.unit:
            sections.append(FieldSection(label="unit", value=self.unit))
        return sections


@dataclass(frozen=True, repr=False)
class TimeDimensionDetails(_DetailsBase):
    """Details for a time dimension object."""

    entity: SemanticRef
    parse_kind: Literal["date", "datetime", "timestamp", "strptime", "hour_prefix"] | None
    data_type: str | None
    granularity: str | None
    format: str | None
    timezone: str | None
    is_default: bool
    sample_interval: SampleIntervalIR | None

    def _detail_sections(self) -> list[Section]:
        sections = _common_detail_sections(
            context=self.context,
            python_symbol=self.python_symbol,
            source_location=self.source_location,
            parents=self.parents,
            children=self.children,
            dependents=self.dependents,
        )
        parse_kind_display = self.parse_kind or "(inferred)"
        sections.extend(
            (
                FieldSection(label="entity", value=self.entity.id),
                FieldSection(label="parse_kind", value=parse_kind_display),
                FieldSection(label="granularity", value=str(self.granularity)),
                FieldSection(label="format", value=repr(self.format)),
                FieldSection(label="timezone", value=repr(self.timezone)),
                FieldSection(label="is_default", value=str(self.is_default)),
                FieldSection(
                    label="sample_interval",
                    value=self.sample_interval.to_token() if self.sample_interval else "(none)",
                ),
            )
        )
        return sections


def _metric_common_sections(
    *,
    entities: tuple[SemanticRef, ...],
    root_entity: SemanticRef | None,
    metric_type: Literal["simple", "derived"],
    additivity: Literal["additive", "semi_additive", "non_additive"],
    fold: str | None,
    status_time_dimension: str | None,
    fanout_policy: Literal["block", "aggregate_then_join"],
    unit: str | None,
    provenance: SqlProvenance | None,
    parity_status: ParityStatus,
) -> list[Section]:
    """Render sections shared by all metric detail variants."""
    sections: list[Section] = [
        FieldSection(label="entities", value=_format_refs(entities)),
        FieldSection(label="root_entity", value=_format_ref(root_entity)),
        FieldSection(label="type", value=metric_type),
        FieldSection(label="additivity", value=additivity),
    ]
    if fold is not None:
        sections.append(FieldSection(label="fold", value=f"{fold} over {status_time_dimension}"))
    sections.append(FieldSection(label="fanout_policy", value=fanout_policy))
    if unit:
        sections.append(FieldSection(label="unit", value=unit))
    sections.append(FieldSection(label="provenance", value=_provenance_text(provenance)))
    sections.append(FieldSection(label="parity_status", value=str(parity_status)))
    return sections


@dataclass(frozen=True, repr=False)
class SimpleMetricDetails(_DetailsBase):
    """Details for a simple (entity-backed) metric.

    Simple metrics are declared with ``@ms.metric(...)`` or ``ms.aggregate(...)``.
    They have an optional aggregation and measure reference; they never have
    composition, components, or linear_terms.
    """

    entities: tuple[SemanticRef, ...]
    root_entity: SemanticRef | None
    aggregation: str | None
    measure: SemanticRef | None
    additivity: Literal["additive", "semi_additive", "non_additive"]
    fold: str | None
    status_time_dimension: str | None
    fanout_policy: Literal["block", "aggregate_then_join"]
    unit: str | None
    provenance: SqlProvenance | None
    parity_status: ParityStatus
    aggregation_target: SemanticRef | None = None
    aggregation_target_kind: Literal["measure", "entity"] | None = None

    @property
    def metric_type(self) -> Literal["simple"]:
        return "simple"

    def _detail_sections(self) -> list[Section]:
        sections = _common_detail_sections(
            context=self.context,
            python_symbol=self.python_symbol,
            source_location=self.source_location,
            parents=self.parents,
            children=self.children,
            dependents=self.dependents,
        )
        sections.extend(
            _metric_common_sections(
                entities=self.entities,
                root_entity=self.root_entity,
                metric_type=self.metric_type,
                additivity=self.additivity,
                fold=self.fold,
                status_time_dimension=self.status_time_dimension,
                fanout_policy=self.fanout_policy,
                unit=self.unit,
                provenance=self.provenance,
                parity_status=self.parity_status,
            )
        )
        if self.aggregation is not None:
            sections.append(FieldSection(label="aggregation", value=self.aggregation))
        if self.measure is not None:
            sections.append(FieldSection(label="measure", value=self.measure.id))
        if self.aggregation_target is not None and self.aggregation_target_kind != "measure":
            sections.append(
                FieldSection(
                    label="target",
                    value=f"{self.aggregation_target_kind} {self.aggregation_target.id}",
                )
            )
        return sections


@dataclass(frozen=True, repr=False)
class DerivedMetricDetails(_DetailsBase):
    """Details for a derived (composed) metric.

    Derived metrics are declared with ``ms.ratio(...)``, ``ms.weighted_average(...)``,
    or ``ms.linear(...)``.  They always carry a composition kind and components;
    they never have aggregation or measure.
    """

    entities: tuple[SemanticRef, ...]
    root_entity: SemanticRef | None
    composition: Literal["ratio", "weighted_average", "linear", "cumulative"]
    components: tuple[tuple[str, SemanticRef], ...]
    linear_terms: tuple[tuple[str, str], ...]
    required_relationships: tuple[SemanticRef, ...]
    additivity: Literal["additive", "semi_additive", "non_additive"]
    fold: str | None
    status_time_dimension: str | None
    fanout_policy: Literal["block", "aggregate_then_join"]
    unit: str | None
    provenance: SqlProvenance | None
    parity_status: ParityStatus

    @property
    def metric_type(self) -> Literal["derived"]:
        return "derived"

    def _detail_sections(self) -> list[Section]:
        sections = _common_detail_sections(
            context=self.context,
            python_symbol=self.python_symbol,
            source_location=self.source_location,
            parents=self.parents,
            children=self.children,
            dependents=self.dependents,
        )
        sections.extend(
            _metric_common_sections(
                entities=self.entities,
                root_entity=self.root_entity,
                metric_type=self.metric_type,
                additivity=self.additivity,
                fold=self.fold,
                status_time_dimension=self.status_time_dimension,
                fanout_policy=self.fanout_policy,
                unit=self.unit,
                provenance=self.provenance,
                parity_status=self.parity_status,
            )
        )
        sections.append(FieldSection(label="composition", value=self.composition))
        if self.components:
            sections.append(
                FieldSection(
                    label="components",
                    value=", ".join(f"{role}={ref.id}" for role, ref in self.components),
                )
            )
        if self.linear_terms:
            sections.append(
                FieldSection(
                    label="linear_terms",
                    value=", ".join(f"{sign}{metric}" for sign, metric in self.linear_terms),
                )
            )
        if self.required_relationships:
            sections.append(
                FieldSection(
                    label="required_relationships",
                    value=_format_refs(self.required_relationships),
                )
            )
        return sections


MetricDetails = SimpleMetricDetails | DerivedMetricDetails


@dataclass(frozen=True, repr=False)
class RelationshipDetails(_DetailsBase):
    """Details for a relationship between entities."""

    from_entity: SemanticRef
    to_entity: SemanticRef
    from_keys: tuple[str, ...]
    to_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        # Compatibility: these are no longer stored directly on RelationshipIR,
        # but RelationshipDetails still exposes them for catalog consumers.
        # Set by _build_relationship_object from JoinKey pairs.
        pass

    def _detail_sections(self) -> list[Section]:
        sections = _common_detail_sections(
            context=self.context,
            python_symbol=self.python_symbol,
            source_location=self.source_location,
            parents=self.parents,
            children=self.children,
            dependents=self.dependents,
        )
        sections.extend(
            (
                FieldSection(label="from", value=self.from_entity.id),
                FieldSection(label="to", value=self.to_entity.id),
                FieldSection(
                    label="join_keys",
                    value=", ".join(
                        f"{left}={right}"
                        for left, right in zip(self.from_keys, self.to_keys, strict=True)
                    ),
                ),
            )
        )
        return sections


_CatalogObjectDetails = (
    DatasourceDetails
    | DomainDetails
    | EntityDetails
    | DimensionDetails
    | MeasureDetails
    | TimeDimensionDetails
    | MetricDetails
    | RelationshipDetails
)


@dataclass(frozen=True, repr=False, eq=False)
class CatalogObject(RenderableResult):
    """Immutable base value view for one loaded catalog object.

    Args:
        ref: Typed SemanticRef for authoring and runtime handoff.
        name: Local name (no domain prefix).
        _details: Kind-specific details payload.
        _catalog: Owning SemanticCatalog (for navigation in subclasses).

    Returns:
        CatalogObject subclass instance with typed ``id``, ``ref``, ``name``,
        ``details()``, ``render()``, and ``show()``.

    Example:
        >>> revenue = catalog.get("metric.sales.revenue")
        >>> revenue.id           # "metric.sales.revenue"
        >>> revenue.name         # "revenue"
        >>> revenue.ref          # SemanticRef("sales.revenue", METRIC)
        >>> revenue.details().additivity

    Constraints:
        Objects do not expose ``semantic_id``, ``kind``, ``domain``,
        ``context``, ``source_location``, or ``python_symbol`` at the top
        level. Use ``details()`` for rich structural data.
    """

    ref: SemanticRef
    name: str
    _details: _CatalogObjectDetails
    _catalog: SemanticCatalog

    _kind: ClassVar[SemanticKind]
    _navigation_names: ClassVar[tuple[str, ...]] = ()

    @property
    def id(self) -> str:
        return _catalog_typed_id(self.ref.id, self._kind)

    def details(self) -> _CatalogObjectDetails:
        return self._details

    def __eq__(self, other: object) -> bool:
        return (
            type(self) is type(other) and isinstance(other, CatalogObject) and self.id == other.id
        )

    def __hash__(self) -> int:
        return hash((type(self), self.id))

    def _repr_identity(self) -> str:
        return f"{type(self).__name__} id={self.id}"

    def _card(self) -> Card:
        available = (
            *(f".{name}" for name in self._navigation_names),
            ".details()",
            ".contract()",
            ".render()",
            ".show()",
        )
        card = Card(identity=self._repr_identity(), available=available).field(
            label="business_definition",
            value=self._details.context.business_definition or "(none)",
        )
        for name in self._navigation_names:
            collection = cast("CatalogCollection[CatalogObject]", getattr(self, name))
            card = card.field(label=name, value=f"{len(collection)} -> .{name}")
        return card

    def contract(self) -> AuthoringContract:
        """Return the mechanical continuation contract for this catalog object.

        The contract exposes verify, preview (for executable kinds), and
        readiness transitions scoped to this object's ref.
        """
        from marivo.semantic._capabilities.contracts import contract_for_catalog_object

        return contract_for_catalog_object(self.ref.id, self.ref.kind.value)


class Domain(CatalogObject):
    """Loaded semantic domain with typed child collections."""

    _kind = SemanticKind.DOMAIN
    _navigation_names = (
        "entities",
        "dimensions",
        "time_dimensions",
        "measures",
        "metrics",
        "relationships",
    )

    def details(self) -> DomainDetails:
        return cast("DomainDetails", self._details)

    @property
    def entities(self) -> CatalogCollection[Entity]:
        return self._catalog._collection(Entity, scope_id=self.id)

    @property
    def dimensions(self) -> CatalogCollection[Dimension]:
        return self._catalog._collection(Dimension, scope_id=self.id)

    @property
    def time_dimensions(self) -> CatalogCollection[TimeDimension]:
        return self._catalog._collection(TimeDimension, scope_id=self.id)

    @property
    def measures(self) -> CatalogCollection[Measure]:
        return self._catalog._collection(Measure, scope_id=self.id)

    @property
    def metrics(self) -> CatalogCollection[Metric]:
        return self._catalog._collection(Metric, scope_id=self.id)

    @property
    def relationships(self) -> CatalogCollection[Relationship]:
        return self._catalog._collection(Relationship, scope_id=self.id)


class Datasource(CatalogObject):
    """Loaded datasource with the entities it backs."""

    _kind = SemanticKind.DATASOURCE
    _navigation_names = ("entities",)

    def details(self) -> DatasourceDetails:
        return cast("DatasourceDetails", self._details)

    @property
    def entities(self) -> CatalogCollection[Entity]:
        return self._catalog._collection(Entity, scope_id=self.id)


class Entity(CatalogObject):
    """Loaded semantic entity with applicable semantic collections."""

    _kind = SemanticKind.ENTITY
    _navigation_names = (
        "dimensions",
        "time_dimensions",
        "measures",
        "metrics",
        "relationships",
    )

    def details(self) -> EntityDetails:
        return cast("EntityDetails", self._details)

    @property
    def dimensions(self) -> CatalogCollection[Dimension]:
        return self._catalog._collection(Dimension, scope_id=self.id)

    @property
    def time_dimensions(self) -> CatalogCollection[TimeDimension]:
        return self._catalog._collection(TimeDimension, scope_id=self.id)

    @property
    def measures(self) -> CatalogCollection[Measure]:
        return self._catalog._collection(Measure, scope_id=self.id)

    @property
    def metrics(self) -> CatalogCollection[Metric]:
        return self._catalog._collection(Metric, scope_id=self.id)

    @property
    def relationships(self) -> CatalogCollection[Relationship]:
        return self._catalog._collection(Relationship, scope_id=self.id)


class Dimension(CatalogObject):
    """Loaded categorical dimension."""

    _kind = SemanticKind.DIMENSION

    def details(self) -> DimensionDetails:
        return cast("DimensionDetails", self._details)


class TimeDimension(CatalogObject):
    """Loaded time dimension."""

    _kind = SemanticKind.TIME_DIMENSION

    def details(self) -> TimeDimensionDetails:
        return cast("TimeDimensionDetails", self._details)


class Measure(CatalogObject):
    """Loaded entity-owned quantitative measure."""

    _kind = SemanticKind.MEASURE

    def details(self) -> MeasureDetails:
        return cast("MeasureDetails", self._details)


class Metric(CatalogObject):
    """Loaded analysis-ready metric."""

    _kind = SemanticKind.METRIC

    def details(self) -> MetricDetails:
        return cast("MetricDetails", self._details)


class Relationship(CatalogObject):
    """Loaded relationship with typed entity endpoints."""

    _kind = SemanticKind.RELATIONSHIP

    def details(self) -> RelationshipDetails:
        return cast("RelationshipDetails", self._details)

    @property
    def from_entity(self) -> Entity:
        obj = self._catalog.get(f"entity.{self.details().from_entity.id}")
        if not isinstance(obj, Entity):
            raise AssertionError(f"relationship endpoint is not an Entity: {obj.id}")
        return obj

    @property
    def to_entity(self) -> Entity:
        obj = self._catalog.get(f"entity.{self.details().to_entity.id}")
        if not isinstance(obj, Entity):
            raise AssertionError(f"relationship endpoint is not an Entity: {obj.id}")
        return obj

    def _card(self) -> Card:
        return (
            super()
            ._card()
            .field(label="from_entity", value=self.from_entity.id)
            .field(label="to_entity", value=self.to_entity.id)
        )


def _object_from_details[CatalogObjectT: CatalogObject](
    object_type: type[CatalogObjectT],
    details: _CatalogObjectDetails,
    catalog: SemanticCatalog,
) -> CatalogObjectT:
    return object_type(
        ref=details.ref,
        name=details.name,
        _details=details,
        _catalog=catalog,
    )


class CatalogCollection[CatalogObjectT: CatalogObject](RenderableResult):
    """Read-only, typed, deterministic view over catalog objects.

    Every global and scoped collection on ``SemanticCatalog`` and its
    container objects returns a ``CatalogCollection[T]``. Items are sorted
    by typed ID, so order is stable across loads and module declaration
    order.

    Args:
        catalog: Owning SemanticCatalog.
        object_type: Concrete CatalogObject subclass held by this collection.
        scope_id: Optional typed ID that scopes the collection to children
            of that object. None means the whole project.

    Returns:
        CatalogCollection with ``.items``, ``.ids()``, ``.refs()``,
        ``.get(key)``, ``.render()``, and ``.show()``.

    Example:
        >>> metrics = catalog.metrics
        >>> metrics.ids()
        ['metric.sales.revenue']
        >>> revenue = metrics.get("revenue")

    Constraints:
        Collections are read-only. ``.items`` always returns the complete
        tuple; ``render()`` may truncate display rows.
    """

    def __init__(
        self,
        catalog: SemanticCatalog,
        object_type: type[CatalogObjectT],
        *,
        scope_id: str | None = None,
    ) -> None:
        self._catalog = catalog
        self._object_type = object_type
        self._scope_id = scope_id

    @property
    def items(self) -> tuple[CatalogObjectT, ...]:
        return self._catalog._require_index().objects(
            self._object_type,
            scope_id=self._scope_id,
        )

    def ids(self) -> list[str]:
        return [item.id for item in self.items]

    def refs(self) -> tuple[SemanticRef, ...]:
        return tuple(item.ref for item in self.items)

    def get(self, key: str) -> CatalogObjectT:
        return self._catalog._get_from_collection(self, key)

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self) -> Iterator[CatalogObjectT]:
        return iter(self.items)

    def __getitem__(self, index: int) -> CatalogObjectT:
        return self.items[index]

    def _repr_identity(self) -> str:
        scope = self._scope_id or "catalog"
        return (
            f"CatalogCollection type={self._object_type.__name__} scope={scope} count={len(self)}"
        )

    def _card(self) -> Card:
        rows = [(item.id, item.name) for item in self.items]
        return Card(
            identity=self._repr_identity(),
            available=(
                ".items",
                ".ids()",
                ".refs()",
                ".get(...)",
                ".render()",
                ".show()",
            ),
        ).table(
            columns=("id", "name"),
            rows=rows,
            row_count=len(rows),
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_VALID_KINDS: frozenset[str] = frozenset(str(k) for k in SymbolKind)

_OBJECT_TYPE_BY_KIND: dict[SemanticKind, type[CatalogObject]] = {
    SemanticKind.DOMAIN: Domain,
    SemanticKind.DATASOURCE: Datasource,
    SemanticKind.ENTITY: Entity,
    SemanticKind.DIMENSION: Dimension,
    SemanticKind.TIME_DIMENSION: TimeDimension,
    SemanticKind.MEASURE: Measure,
    SemanticKind.METRIC: Metric,
    SemanticKind.RELATIONSHIP: Relationship,
}

_COLLECTION_NAME_BY_TYPE: dict[type[CatalogObject], str] = {
    Domain: "domains",
    Datasource: "datasources",
    Entity: "entities",
    Dimension: "dimensions",
    TimeDimension: "time_dimensions",
    Measure: "measures",
    Metric: "metrics",
    Relationship: "relationships",
}


def _parity_snapshot(project: SemanticProject) -> tuple[tuple[str, bool], ...]:
    """Return a hashable snapshot of project parity results for index invalidation."""
    return tuple(sorted((k, v.ok) for k, v in project._parity_results.items()))


def _catalog_typed_id(ref_id: str, kind: SemanticKind) -> str:
    if kind == SemanticKind.DATASOURCE:
        return ref_id
    return f"{kind.value}.{ref_id}"


def _require_semantic_ref(value: object, *, parameter: str) -> SemanticRef:
    if isinstance(value, CatalogObject):
        return value.ref
    if isinstance(value, SemanticRef):
        return value
    _raise(
        ErrorKind.INVALID_REF,
        f"catalog.{parameter} requires a CatalogObject or SemanticRef; got {type(value).__name__}.",
        cls=SemanticRuntimeError,
        constraint_id=ConstraintId.REF_SHAPE,
    )


def _typed_ref_candidates(reg: Registry | None, semantic_id: str) -> tuple[str, ...]:
    if reg is None:
        return ()
    candidates: list[str] = []
    if semantic_id in reg.domains:
        candidates.append(_catalog_typed_id(semantic_id, SemanticKind.DOMAIN))
    datasource_irs = tuple(reg.datasources.values())
    for ds_ir in datasource_irs:
        datasource_ref = DatasourceRef.from_id(ds_ir.semantic_id).id
        datasource_name = datasource_ref.removeprefix("datasource.")
        if semantic_id in {datasource_ref, datasource_name, ds_ir.semantic_id}:
            candidates.append(datasource_ref)
    if semantic_id in reg.entities:
        candidates.append(_catalog_typed_id(semantic_id, SemanticKind.ENTITY))
    if semantic_id in reg.dimensions:
        kind = (
            SemanticKind.TIME_DIMENSION
            if reg.dimensions[semantic_id].is_time_dimension
            else SemanticKind.DIMENSION
        )
        candidates.append(_catalog_typed_id(semantic_id, kind))
    if semantic_id in reg.measures:
        candidates.append(_catalog_typed_id(semantic_id, SemanticKind.MEASURE))
    if semantic_id in reg.metrics:
        candidates.append(_catalog_typed_id(semantic_id, SemanticKind.METRIC))
    if semantic_id in reg.relationships:
        candidates.append(_catalog_typed_id(semantic_id, SemanticKind.RELATIONSHIP))
    return tuple(dict.fromkeys(candidates))


def _candidate_guidance(*, candidates: tuple[str, ...]) -> str:
    if not candidates:
        return ""
    calls = [f'catalog.get("{candidate}")' for candidate in candidates]
    return " Did you mean: " + ", ".join(calls) + "?"


def _parse_typed_ref_id(
    raw: object,
    *,
    method: str,
    reg: Registry | None = None,
) -> SemanticRef:
    allowed = ", ".join(sorted(_VALID_KINDS))
    if not isinstance(raw, str):
        _raise(
            ErrorKind.INVALID_REF,
            f"catalog.{method}(...) requires a string in '<kind>.<semantic_id>' format "
            f"with kind one of: {allowed}; got {type(raw).__name__}.",
            cls=SemanticRuntimeError,
            constraint_id=ConstraintId.REF_SHAPE,
        )
    kind_raw, separator, semantic_id = raw.partition(".")
    if not separator or not kind_raw or not semantic_id or ".." in semantic_id:
        candidates = _typed_ref_candidates(reg, raw)
        _raise(
            ErrorKind.INVALID_REF,
            f"catalog.{method}(...) requires '<kind>.<semantic_id>', for example "
            "'metric.sales.revenue' or 'dimension.sales.orders.region'; "
            f"kind one of: {allowed}."
            f"{_candidate_guidance(candidates=candidates)}",
            refs=(raw,),
            cls=SemanticRuntimeError,
            constraint_id=ConstraintId.REF_SHAPE,
        )
    try:
        kind = SymbolKind(kind_raw)
    except ValueError:
        _raise(
            ErrorKind.INVALID_REF,
            f"catalog.{method}(...) kind {kind_raw!r} is not supported; "
            f"expected '<kind>.<semantic_id>' with kind one of: {allowed}.",
            refs=(raw,),
            cls=SemanticRuntimeError,
            constraint_id=ConstraintId.REF_SHAPE,
        )
    try:
        return make_ref(semantic_id, kind)
    except ValueError as exc:
        _raise(
            ErrorKind.INVALID_REF,
            f"catalog.{method}(...) could not build {kind.value} ref from {semantic_id!r}: {exc}",
            refs=(raw,),
            cls=SemanticRuntimeError,
            constraint_id=ConstraintId.REF_SHAPE,
        )


def _normalize_location(loc: SourceLocation | DatasourceSourceLocation) -> SourceLocation:
    return SourceLocation(file=loc.file, line=loc.line)


def _build_datasource_object(
    ds_ir: DatasourceIR, reg: Registry, catalog: SemanticCatalog
) -> Datasource:
    ref = DatasourceRef.from_id(ds_ir.semantic_id)
    dependents = tuple(
        make_ref(d.semantic_id, SemanticKind.ENTITY)
        for d in reg.entities.values()
        if DatasourceRef.from_id(d.datasource) == ref
    )
    details = DatasourceDetails(
        ref=ref,
        kind=SemanticKind.DATASOURCE,
        name=ds_ir.name,
        domain=None,
        context=ds_ir.ai_context,
        source_location=_normalize_location(ds_ir.location),
        parents=(),
        children=(),
        dependents=dependents,
        python_symbol=ds_ir.python_symbol,
        backend_type=ds_ir.backend_type,
        fields=dict(ds_ir.fields),
        env_refs=dict(ds_ir.env_refs),
    )
    return _object_from_details(Datasource, details, catalog)


def _build_domain_object(model_ir: DomainIR, reg: Registry, catalog: SemanticCatalog) -> Domain:
    ref = make_ref(model_ir.name, SemanticKind.DOMAIN)
    datasets_refs = tuple(
        make_ref(d.semantic_id, SemanticKind.ENTITY)
        for d in reg.entities.values()
        if d.domain == model_ir.name
    )
    metrics_refs = tuple(
        make_ref(m.semantic_id, SemanticKind.METRIC)
        for m in reg.metrics.values()
        if m.domain == model_ir.name
    )
    children = datasets_refs + metrics_refs
    details = DomainDetails(
        ref=ref,
        kind=SemanticKind.DOMAIN,
        name=model_ir.name,
        domain=model_ir.name,
        context=model_ir.ai_context,
        source_location=model_ir.location,
        parents=(),
        children=children,
        dependents=(),
        python_symbol="",
        owner=model_ir.owner,
        default=model_ir.default,
    )
    return _object_from_details(Domain, details, catalog)


def _build_entity_object(ds_ir: EntityIR, reg: Registry, catalog: SemanticCatalog) -> Entity:
    ref = make_ref(ds_ir.semantic_id, SemanticKind.ENTITY)
    ds_ref = DatasourceRef.from_id(ds_ir.datasource)
    fields_refs = tuple(
        make_ref(
            f.semantic_id,
            SemanticKind.TIME_DIMENSION if f.is_time_dimension else SemanticKind.DIMENSION,
        )
        for f in reg.dimensions.values()
        if f.entity == ds_ir.semantic_id
    )
    measure_refs = tuple(
        make_ref(m.semantic_id, SemanticKind.MEASURE)
        for m in reg.measures.values()
        if m.entity == ds_ir.semantic_id
    )
    rels_refs = tuple(
        make_ref(r.semantic_id, SemanticKind.RELATIONSHIP)
        for r in reg.relationships.values()
        if r.from_entity == ds_ir.semantic_id or r.to_entity == ds_ir.semantic_id
    )
    metric_refs = tuple(
        make_ref(m.semantic_id, SemanticKind.METRIC)
        for m in reg.metrics.values()
        if ds_ir.semantic_id in m.entities
    )
    children = fields_refs + measure_refs + metric_refs + rels_refs
    metric_dependents = tuple(
        make_ref(m.semantic_id, SemanticKind.METRIC)
        for m in reg.metrics.values()
        if ds_ir.semantic_id in m.entities
    )
    details = EntityDetails(
        ref=ref,
        kind=SemanticKind.ENTITY,
        name=ds_ir.name,
        domain=ds_ir.domain,
        context=ds_ir.ai_context,
        source_location=ds_ir.location,
        parents=(ds_ref,),
        children=children,
        dependents=metric_dependents,
        python_symbol=ds_ir.python_symbol,
        datasource=ds_ref,
        source=ds_ir.source,
        primary_key=ds_ir.primary_key,
        versioning=ds_ir.versioning,
    )
    return _object_from_details(Entity, details, catalog)


def _preview_timezones_for_field(
    *,
    column_name: str,
    field_ir: DimensionIR,
    datasource_timezone: object | None,
    report_tz: str,
) -> dict[str, dict[str, str | None]]:
    if not field_ir.is_time_dimension or field_ir.parse is None:
        return {}
    declared = getattr(field_ir.parse, "timezone", None)
    read_tz = declared
    read_resolution: str | None = "declared" if declared is not None else None
    if read_tz is None and datasource_timezone is not None:
        read_tz = getattr(datasource_timezone, "engine_timezone_name", None)
        read_resolution = getattr(datasource_timezone, "read_tz_resolution", None)
    kind = "instant" if read_tz is None else "localizable_wall_clock"
    return {
        column_name: {
            "kind": kind,
            "read_tz": read_tz,
            "report_tz": report_tz,
            "read_tz_resolution": read_resolution,
        }
    }


def _build_dimension_object(
    f_ir: DimensionIR, reg: Registry, catalog: SemanticCatalog
) -> CatalogObject:
    is_time = f_ir.is_time_dimension
    kind = SemanticKind.TIME_DIMENSION if is_time else SemanticKind.DIMENSION
    ref = make_ref(f_ir.semantic_id, kind)
    ds_ref = make_ref(f_ir.entity, SemanticKind.ENTITY)
    if is_time:
        # Extract time-dimension metadata from the parse variant
        parse = f_ir.parse
        data_type: str | None = None
        fmt: str | None = None
        tz: str | None = None
        sample_interval: SampleIntervalIR | None = None
        if parse is None:
            parse_kind: (
                Literal["date", "datetime", "timestamp", "strptime", "hour_prefix"] | None
            ) = None
        elif isinstance(parse, DateParse):
            parse_kind = "date"
            data_type = "date"
        elif isinstance(parse, DatetimeParse):
            parse_kind = "datetime"
            data_type = "datetime"
            tz = parse.timezone
            sample_interval = parse.sample_interval
        elif isinstance(parse, TimestampParse):
            parse_kind = "timestamp"
            data_type = "timestamp"
            tz = parse.timezone
            sample_interval = parse.sample_interval
        elif isinstance(parse, StrptimeParse):
            parse_kind = "strptime"
            fmt = parse.format
            tz = parse.timezone
            sample_interval = parse.sample_interval
        elif isinstance(parse, HourPrefixParse):
            parse_kind = "hour_prefix"
            sample_interval = parse.sample_interval
        else:
            raise AssertionError(f"unsupported time parse variant: {type(parse).__name__}")
        details: _CatalogObjectDetails = TimeDimensionDetails(
            ref=ref,
            kind=kind,
            name=f_ir.name,
            domain=f_ir.domain,
            context=f_ir.ai_context,
            source_location=f_ir.location,
            parents=(ds_ref,),
            children=(),
            dependents=(),
            python_symbol=f_ir.python_symbol,
            entity=ds_ref,
            parse_kind=parse_kind,
            data_type=data_type,
            granularity=f_ir.granularity,
            format=fmt,
            timezone=tz,
            is_default=f_ir.is_default,
            sample_interval=sample_interval,
        )
    else:
        details = DimensionDetails(
            ref=ref,
            kind=kind,
            name=f_ir.name,
            domain=f_ir.domain,
            context=f_ir.ai_context,
            source_location=f_ir.location,
            parents=(ds_ref,),
            children=(),
            dependents=(),
            python_symbol=f_ir.python_symbol,
            entity=ds_ref,
        )
    return _object_from_details(TimeDimension if is_time else Dimension, details, catalog)


def _build_measure_object(m_ir: MeasureIR, reg: Registry, catalog: SemanticCatalog) -> Measure:
    ref = make_ref(m_ir.semantic_id, SemanticKind.MEASURE)
    entity_ref = make_ref(m_ir.entity, SemanticKind.ENTITY)
    dependents = tuple(
        make_ref(metric.semantic_id, SemanticKind.METRIC)
        for metric in reg.metrics.values()
        if metric.measure == m_ir.semantic_id
    )
    details = MeasureDetails(
        ref=ref,
        kind=SemanticKind.MEASURE,
        name=m_ir.name,
        domain=m_ir.domain,
        context=m_ir.ai_context,
        source_location=m_ir.location,
        parents=(entity_ref,),
        children=(),
        dependents=dependents,
        python_symbol=m_ir.python_symbol,
        entity=entity_ref,
        additivity=additivity_bucket(m_ir.additivity),
        unit=m_ir.unit,
    )
    return _object_from_details(Measure, details, catalog)


def _format_agg(agg: object) -> str | None:
    if agg is None:
        return None
    if isinstance(agg, tuple):
        return f"{agg[0]}({agg[1]})"
    return str(agg)


def _aggregation_target_ref(m_ir: MetricIR) -> SemanticRef | None:
    target = m_ir.aggregation_target or m_ir.measure
    target_kind = m_ir.aggregation_target_kind or ("measure" if m_ir.measure else None)
    if target is None or target_kind is None:
        return None
    kind = {
        "measure": SemanticKind.MEASURE,
        "entity": SemanticKind.ENTITY,
    }[target_kind]
    return make_ref(target, kind)


def _build_metric_object(
    m_ir: MetricIR, reg: Registry, project: SemanticProject, catalog: SemanticCatalog
) -> Metric:
    ref = make_ref(m_ir.semantic_id, SemanticKind.METRIC)
    entity_refs = tuple(make_ref(ds, SemanticKind.ENTITY) for ds in m_ir.entities)
    root_entity_ref = make_ref(m_ir.root_entity, SemanticKind.ENTITY) if m_ir.root_entity else None
    comp_map = composition_components(m_ir.composition) if m_ir.composition is not None else {}
    components = tuple(
        (role, make_ref(comp_ref, SemanticKind.METRIC)) for role, comp_ref in comp_map.items()
    )
    component_refs = tuple(r for _, r in components)
    aggregation_target = _aggregation_target_ref(m_ir)
    linear_terms = (
        tuple((t.sign, t.metric) for t in m_ir.composition.terms)
        if isinstance(m_ir.composition, LinearComposition)
        else ()
    )
    required_rels: tuple[SemanticRef, ...] = ()
    if len(m_ir.entities) > 1:
        required_rels = tuple(
            make_ref(r.semantic_id, SemanticKind.RELATIONSHIP)
            for r in reg.relationships.values()
            if r.domain == m_ir.domain
            and r.from_entity in m_ir.entities
            and r.to_entity in m_ir.entities
        )
    parents = entity_refs + component_refs + required_rels
    dependents = tuple(
        make_ref(m2.semantic_id, SemanticKind.METRIC)
        for m2 in reg.metrics.values()
        if m2.composition is not None
        and m_ir.semantic_id in composition_components(m2.composition).values()
    )
    parity_status = propagated_parity_status(project, m_ir.semantic_id)
    add = m_ir.additivity
    if m_ir.metric_type == "derived":
        assert m_ir.composition is not None, (
            f"Derived metric {m_ir.semantic_id!r} has no composition IR"
        )
        details: MetricDetails = DerivedMetricDetails(
            ref=ref,
            kind=SemanticKind.METRIC,
            name=m_ir.name,
            domain=m_ir.domain,
            context=m_ir.ai_context,
            source_location=m_ir.location,
            parents=parents,
            children=(),
            dependents=dependents,
            python_symbol=m_ir.python_symbol,
            entities=entity_refs,
            root_entity=root_entity_ref,
            composition=m_ir.composition.kind,
            components=components,
            linear_terms=linear_terms,
            required_relationships=required_rels,
            additivity=additivity_bucket(add) if add is not None else "non_additive",
            fold=add.fold.label() if isinstance(add, SemiAdditive) else None,
            status_time_dimension=add.over if isinstance(add, SemiAdditive) else None,
            fanout_policy=m_ir.fanout_policy,
            unit=m_ir.unit,
            provenance=m_ir.provenance,
            parity_status=parity_status,
        )
    else:
        details = SimpleMetricDetails(
            ref=ref,
            kind=SemanticKind.METRIC,
            name=m_ir.name,
            domain=m_ir.domain,
            context=m_ir.ai_context,
            source_location=m_ir.location,
            parents=parents,
            children=(),
            dependents=dependents,
            python_symbol=m_ir.python_symbol,
            entities=entity_refs,
            root_entity=root_entity_ref,
            aggregation=_format_agg(m_ir.aggregation),
            measure=make_ref(m_ir.measure, SemanticKind.MEASURE) if m_ir.measure else None,
            additivity=additivity_bucket(add) if add is not None else "non_additive",
            fold=add.fold.label() if isinstance(add, SemiAdditive) else None,
            status_time_dimension=add.over if isinstance(add, SemiAdditive) else None,
            fanout_policy=m_ir.fanout_policy,
            unit=m_ir.unit,
            provenance=m_ir.provenance,
            parity_status=parity_status,
            aggregation_target=aggregation_target,
            aggregation_target_kind=m_ir.aggregation_target_kind
            or ("measure" if m_ir.measure else None),
        )
    return _object_from_details(Metric, details, catalog)


def _build_relationship_object(
    r_ir: RelationshipIR, reg: Registry, catalog: SemanticCatalog
) -> Relationship:
    ref = make_ref(r_ir.semantic_id, SemanticKind.RELATIONSHIP)
    from_ref = make_ref(r_ir.from_entity, SemanticKind.ENTITY)
    to_ref = make_ref(r_ir.to_entity, SemanticKind.ENTITY)
    details = RelationshipDetails(
        ref=ref,
        kind=SemanticKind.RELATIONSHIP,
        name=r_ir.name,
        domain=r_ir.domain,
        context=r_ir.ai_context,
        source_location=r_ir.location,
        parents=(from_ref, to_ref),
        children=(),
        dependents=(),
        python_symbol="",
        from_entity=from_ref,
        to_entity=to_ref,
        from_keys=tuple(k.from_key for k in r_ir.keys),
        to_keys=tuple(k.to_key for k in r_ir.keys),
    )
    return _object_from_details(Relationship, details, catalog)


# ---------------------------------------------------------------------------
# _CatalogIndex — private query layer for typed catalog objects
# ---------------------------------------------------------------------------


class _CatalogIndex:
    """Private index that owns typed-ID lookup, local-name indexes, and
    ownership/applicability edges for all loaded catalog objects.

    The index is built once from the registry and rebuilt when the registry
    object changes (after ``catalog.load()``). It is the sole query layer
    for ``CatalogCollection`` and the temporary ``list(...)`` API.
    """

    def __init__(
        self,
        catalog: SemanticCatalog,
        project: SemanticProject,
        registry: Registry,
    ) -> None:
        self.catalog = catalog
        self.project = project
        self.registry = registry
        self._parity_snapshot = _parity_snapshot(project)
        objects = self._build_objects()
        self._by_id = {obj.id: obj for obj in objects}
        self._by_name: dict[str, tuple[CatalogObject, ...]] = {}
        for name in sorted({obj.name for obj in objects}):
            self._by_name[name] = tuple(
                sorted(
                    (obj for obj in objects if obj.name == name),
                    key=lambda obj: obj.id,
                )
            )

    def _build_objects(self) -> tuple[CatalogObject, ...]:
        reg = self.registry
        result: list[CatalogObject] = []
        result.extend(
            _build_domain_object(item, reg, self.catalog) for item in reg.domains.values()
        )
        datasource_irs = self.project._datasource_irs or tuple(reg.datasources.values())
        result.extend(_build_datasource_object(item, reg, self.catalog) for item in datasource_irs)
        result.extend(
            _build_entity_object(item, reg, self.catalog) for item in reg.entities.values()
        )
        result.extend(
            _build_dimension_object(item, reg, self.catalog) for item in reg.dimensions.values()
        )
        result.extend(
            _build_measure_object(item, reg, self.catalog) for item in reg.measures.values()
        )
        result.extend(
            _build_metric_object(item, reg, self.project, self.catalog)
            for item in reg.metrics.values()
        )
        result.extend(
            _build_relationship_object(item, reg, self.catalog)
            for item in reg.relationships.values()
        )
        return tuple(sorted(result, key=lambda obj: obj.id))

    def get(self, typed_id: str) -> CatalogObject | None:
        return self._by_id.get(typed_id)

    def typed_candidates(self, semantic_id: str) -> tuple[CatalogObject, ...]:
        return tuple(obj for obj in self._by_id.values() if obj.ref.id == semantic_id)

    def named(self, name: str) -> tuple[CatalogObject, ...]:
        return self._by_name.get(name, ())

    def kind_of(self, semantic_id: str) -> SemanticKind | None:
        reg = self.registry
        if semantic_id in reg.domains:
            return SemanticKind.DOMAIN
        datasource_irs = self.project._datasource_irs or tuple(reg.datasources.values())
        if any(
            DatasourceRef.from_id(item.semantic_id).id == semantic_id for item in datasource_irs
        ):
            return SemanticKind.DATASOURCE
        if semantic_id in reg.entities:
            return SemanticKind.ENTITY
        if semantic_id in reg.dimensions:
            return (
                SemanticKind.TIME_DIMENSION
                if reg.dimensions[semantic_id].is_time_dimension
                else SemanticKind.DIMENSION
            )
        if semantic_id in reg.measures:
            return SemanticKind.MEASURE
        if semantic_id in reg.metrics:
            return SemanticKind.METRIC
        if semantic_id in reg.relationships:
            return SemanticKind.RELATIONSHIP
        return None

    def objects[CatalogObjectT: CatalogObject](
        self,
        object_type: type[CatalogObjectT],
        *,
        scope_id: str | None = None,
    ) -> tuple[CatalogObjectT, ...]:
        candidates = tuple(obj for obj in self._by_id.values() if type(obj) is object_type)
        if scope_id is None:
            return candidates
        scope = self._by_id.get(scope_id)
        if scope is None:
            return ()
        selected = tuple(obj for obj in candidates if self._in_scope(obj, scope))
        return selected

    def details_under(
        self,
        kind: SemanticKind,
        *,
        scope_id: str | None = None,
    ) -> tuple[_CatalogObjectDetails, ...]:
        object_type = _OBJECT_TYPE_BY_KIND[kind]
        return tuple(obj.details() for obj in self.objects(object_type, scope_id=scope_id))

    def semantic_ids(
        self,
        kind: SemanticKind,
        *,
        scope_id: str | None = None,
    ) -> tuple[str, ...]:
        return tuple(details.ref.id for details in self.details_under(kind, scope_id=scope_id))

    def _in_scope(self, obj: CatalogObject, scope: CatalogObject) -> bool:
        details = obj.details()
        if isinstance(scope, Domain):
            return details.domain == scope.ref.id
        if isinstance(scope, Datasource):
            return isinstance(details, EntityDetails) and details.datasource == scope.ref
        if isinstance(scope, Entity):
            if isinstance(details, (DimensionDetails, TimeDimensionDetails, MeasureDetails)):
                return details.entity == scope.ref
            if isinstance(details, (SimpleMetricDetails, DerivedMetricDetails)):
                return scope.ref in details.entities
            if isinstance(details, RelationshipDetails):
                return scope.ref in {details.from_entity, details.to_entity}
        return False


# ---------------------------------------------------------------------------
# SemanticCatalog
# ---------------------------------------------------------------------------


def _attach_analysis_handoff(
    report: ReadinessReport,
    catalog: SemanticCatalog,
    requested_refs: tuple[str, ...] | None = None,
) -> ReadinessReport:
    """Attach a typed SemanticToAnalysisHandoff to a readiness report.

    Returns the report unchanged (handoff=None) when blocked or no ref is
    ready. The handoff is built from the catalog index and workspace dir so
    its fingerprints match Session.validate_semantic_handoff exactly.

    ``requested_refs`` are the seeds the caller passed to ``readiness(refs=...)``.
    The handoff carries only these scoped refs (not the full dependency
    closure in ``report.analysis_ready_refs``), so the validator's re-run of
    ``readiness(refs=list(handoff.ready_refs))`` re-expands to the same
    closure and requires preview evidence only for the requested executable
    refs. When ``requested_refs is None`` (the no-args ``readiness()`` case),
    every ready ref is handed off.
    """
    from dataclasses import replace

    from marivo.introspection.live.fingerprints import (
        catalog_fingerprint,
        project_fingerprint,
    )
    from marivo.introspection.live.model import (
        EnvironmentFingerprint,
        LiveHelpTarget,
        SemanticToAnalysisHandoff,
    )

    if report.status == "blocked" or not report.analysis_ready_refs:
        return replace(report, analysis_handoff=None)

    requested_set = set(requested_refs) if requested_refs is not None else None
    scoped_ready = tuple(
        ref for ref in report.analysis_ready_refs if requested_set is None or ref in requested_set
    )
    index = catalog._require_index()
    ready_refs = tuple(
        make_ref(ref, kind) for ref in scoped_ready if (kind := index.kind_of(ref)) is not None
    )
    if not ready_refs:
        return replace(report, analysis_handoff=None)

    if report.status == "ready_with_warnings":
        readiness_status: Literal["ready", "ready_with_warnings"] = "ready_with_warnings"
        caveats: tuple[str, ...] = (
            "readiness includes non-blocking warnings; acceptance is a skill/user decision",
        )
    else:
        readiness_status = "ready"
        caveats = ()

    handoff = SemanticToAnalysisHandoff(
        help_target=LiveHelpTarget(surface="analysis", canonical_id="boundary.semantic_handoff"),
        ready_refs=ready_refs,
        project_fingerprint=project_fingerprint(catalog.workspace_dir),
        catalog_fingerprint=catalog_fingerprint(obj.id for obj in index._by_id.values()),
        environment_fingerprint=EnvironmentFingerprint.current(),
        readiness_status=readiness_status,
        warning_ids=tuple(sorted(w.kind for w in report.warnings)),
        preview_evidence_ids=(),
        caveats=caveats,
    )
    return replace(report, analysis_handoff=handoff)


class SemanticCatalog:
    """Read-only object graph over a loaded semantic project.

    Args:
        project: A loaded SemanticProject instance (status must be 'ready').

    Returns:
        SemanticCatalog with typed collection properties (domains, metrics, etc.),
        get(), preview(), readiness(), and verify_object() methods.

    Example:
        >>> catalog = ms.load()
        >>> catalog.domains.show()
        >>> catalog.metrics.show()  # all metrics across domains
        >>> revenue = catalog.get("metric.sales.revenue")
        >>> revenue.details().additivity

    Constraints:
        catalog is obtained via ms.load(), not constructed directly.
        Typed collection properties return CatalogCollection[CatalogObject].
        SemanticCatalog objects do not expose internal IR instances.
    """

    def __init__(self, project: SemanticProject) -> None:
        self._project = project
        self._reg = project._registry
        self._index: _CatalogIndex | None = None

    @property
    def semantic_root(self) -> Path:
        """Return the semantic root path (models/semantic/)."""
        return self._project.semantic_root

    @property
    def workspace_dir(self) -> Path:
        """Return the workspace directory path."""
        return self._project.workspace_dir

    def load(
        self,
        *,
        domains: str | Sequence[str] | None = None,
    ) -> None:
        """Reload the semantic project from disk and refresh the catalog registry.

        Args:
            domains: When specified, only those domain directories are loaded.
                Pass a single domain name as a string or a list of names.
                When omitted, the previously active filter (if any) is reused.

            Reload uses the same workspace config as ``ms.load()``: the local
            ``models/`` root plus any external models roots declared in
            ``marivo.toml [semantic].layer_paths``.

        Example:
            >>> catalog.load(domains="sales")
            >>> catalog.load(domains=["sales", "inventory"])
        """
        if isinstance(domains, str):
            domains = [domains]
        resolved = (
            domains
            if domains is not None
            else (
                list(self._project._filtered_domains) if self._project._filtered_domains else None
            )
        )
        result = self._project.load(domains=resolved)
        self._reg = self._project._registry
        self._index = None
        if result.status != "ready":
            raise SemanticLoadFailed(result.errors)

    def _require_ready(self) -> Registry:
        reg = self._reg
        if self._project.is_ready() and reg is not None:
            return reg
        errors = self._project.errors()
        if errors:
            raise SemanticLoadFailed(errors)
        _raise(
            ErrorKind.PROJECT_NOT_LOADED,
            "Semantic catalog is not loaded. Call catalog.load() before browsing.",
            cls=SemanticRuntimeError,
        )

    def _require_index(self) -> _CatalogIndex:
        reg = self._require_ready()
        if (
            self._index is None
            or self._index.registry is not reg
            or self._index._parity_snapshot != _parity_snapshot(self._project)
        ):
            self._index = _CatalogIndex(self, self._project, reg)
        return self._index

    def _collection[CatalogObjectT: CatalogObject](
        self,
        object_type: type[CatalogObjectT],
        *,
        scope_id: str | None = None,
    ) -> CatalogCollection[CatalogObjectT]:
        self._require_ready()
        return CatalogCollection(self, object_type, scope_id=scope_id)

    def _get_from_collection[CatalogObjectT: CatalogObject](
        self,
        collection: CatalogCollection[CatalogObjectT],
        key: str,
    ) -> CatalogObjectT:
        if not isinstance(key, str):
            _raise(
                ErrorKind.INVALID_REF,
                f"CatalogCollection.get(...) expected str, got {type(key).__name__}.",
                cls=SemanticRuntimeError,
            )
        index = self._require_index()
        items = collection.items
        if key.startswith(tuple(f"{kind.value}." for kind in SemanticKind)):
            found = index.get(key)
            if found is None:
                self._raise_collection_not_found(collection, key)
            if type(found) is not collection._object_type:
                self._raise_collection_wrong_type(collection, found)
            if found not in items:
                self._raise_collection_out_of_scope(collection, found)
            return found
        if "." in key:
            self._raise_bare_semantic_id(collection, key)
        matches = tuple(item for item in items if item.name == key)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            self._raise_collection_ambiguous(collection, key, matches)
        self._raise_collection_not_found(collection, key)

    @property
    def domains(self) -> CatalogCollection[Domain]:
        return self._collection(Domain)

    @property
    def datasources(self) -> CatalogCollection[Datasource]:
        return self._collection(Datasource)

    @property
    def entities(self) -> CatalogCollection[Entity]:
        return self._collection(Entity)

    @property
    def dimensions(self) -> CatalogCollection[Dimension]:
        return self._collection(Dimension)

    @property
    def time_dimensions(self) -> CatalogCollection[TimeDimension]:
        return self._collection(TimeDimension)

    @property
    def measures(self) -> CatalogCollection[Measure]:
        return self._collection(Measure)

    @property
    def metrics(self) -> CatalogCollection[Metric]:
        return self._collection(Metric)

    @property
    def relationships(self) -> CatalogCollection[Relationship]:
        return self._collection(Relationship)

    def _resolve_kind_of(self, ref_str: str, reg: Registry) -> SemanticKind | None:
        if ref_str in reg.domains:
            return SemanticKind.DOMAIN
        datasource_irs = self._project._datasource_irs or tuple(reg.datasources.values())
        for ds_ir in datasource_irs:
            if DatasourceRef.from_id(ds_ir.semantic_id).id == ref_str:
                return SemanticKind.DATASOURCE
        if ref_str in reg.entities:
            return SemanticKind.ENTITY
        if ref_str in reg.dimensions:
            f = reg.dimensions[ref_str]
            return SemanticKind.TIME_DIMENSION if f.is_time_dimension else SemanticKind.DIMENSION
        if ref_str in reg.measures:
            return SemanticKind.MEASURE
        if ref_str in reg.metrics:
            return SemanticKind.METRIC
        if ref_str in reg.relationships:
            return SemanticKind.RELATIONSHIP
        return None

    # ------------------------------------------------------------------
    # Collection lookup error helpers
    # ------------------------------------------------------------------

    def _collection_property_name(self, object_type: type[CatalogObject]) -> str:
        return _COLLECTION_NAME_BY_TYPE.get(object_type, object_type.__name__.lower())

    def _raise_collection_not_found(
        self,
        collection: CatalogCollection[CatalogObject],
        key: str,
    ) -> NoReturn:
        available = ", ".join(collection.ids()) or "(empty)"
        prop = self._collection_property_name(collection._object_type)
        scope = collection._scope_id or "catalog"
        _raise(
            ErrorKind.NOT_FOUND,
            f"Catalog object {key!r} was not found in {prop} (scope={scope}). "
            f"Available IDs: {available}. "
            f'Use catalog.{prop}.get("<typed_id>") or '
            f'catalog.{prop}.get("<unique_short_name>") to retrieve an object.',
            cls=SemanticRuntimeError,
            refs=(key,),
        )

    def _raise_collection_wrong_type(
        self,
        collection: CatalogCollection[CatalogObject],
        found: CatalogObject,
    ) -> NoReturn:
        expected_type = collection._object_type
        found_prop = self._collection_property_name(type(found))
        expected_prop = self._collection_property_name(expected_type)
        _raise(
            ErrorKind.INVALID_REF,
            f"Catalog object {found.id!r} is a {type(found).__name__}, not a "
            f"{expected_type.__name__}. "
            f'Use catalog.{found_prop}.get("{found.id}") to retrieve this object, '
            f'or catalog.{expected_prop}.get("<typed_id>") for a {expected_type.__name__}.',
            cls=SemanticRuntimeError,
            refs=(found.id,),
        )

    def _raise_collection_out_of_scope(
        self,
        collection: CatalogCollection[CatalogObject],
        found: CatalogObject,
    ) -> NoReturn:
        expected_type = collection._object_type
        expected_prop = self._collection_property_name(expected_type)
        scope = collection._scope_id or "catalog"
        _raise(
            ErrorKind.NOT_FOUND,
            f"Catalog object {found.id!r} exists globally but is outside "
            f"the current scope {scope!r}. "
            f'Use catalog.{expected_prop}.get("{found.id}") to retrieve it globally.',
            cls=SemanticRuntimeError,
            refs=(found.id,),
        )

    def _raise_collection_ambiguous(
        self,
        collection: CatalogCollection[CatalogObject],
        key: str,
        matches: tuple[CatalogObject, ...],
    ) -> NoReturn:
        sorted_ids = sorted(item.id for item in matches)
        calls = "\n".join(
            f'  catalog.{self._collection_property_name(collection._object_type)}.get("{tid}")'
            for tid in sorted_ids
        )
        _raise(
            ErrorKind.AMBIGUOUS_REFERENCE,
            f"Short name {key!r} matched {len(matches)} objects. "
            f"Use the exact typed ID or narrow the scope:\n{calls}",
            cls=SemanticRuntimeError,
            refs=tuple(sorted_ids),
        )

    def _raise_bare_semantic_id(
        self,
        collection: CatalogCollection[CatalogObject],
        key: str,
    ) -> NoReturn:
        index = self._require_index()
        candidates = index.typed_candidates(key)
        if len(candidates) == 1:
            typed_id = candidates[0].id
            prop = self._collection_property_name(type(candidates[0]))
            _raise(
                ErrorKind.INVALID_REF,
                f"CatalogCollection.get(...) received a bare semantic id {key!r}. "
                f"Use the typed ID {typed_id!r}: "
                f'catalog.{prop}.get("{typed_id}") '
                f'or catalog.get("{typed_id}").',
                cls=SemanticRuntimeError,
                refs=(key,),
            )
        if len(candidates) > 1:
            grouped = sorted(candidates, key=lambda obj: obj.id)
            calls = "\n".join(f'  catalog.get("{obj.id}")' for obj in grouped)
            _raise(
                ErrorKind.INVALID_REF,
                f"CatalogCollection.get(...) received a bare semantic id {key!r} "
                f"that matches {len(candidates)} objects. Use a typed ID:\n{calls}",
                cls=SemanticRuntimeError,
                refs=tuple(obj.id for obj in grouped),
            )
        self._raise_collection_not_found(collection, key)

    def _raise_not_found(self, ref_str: str) -> NoReturn:
        from marivo.semantic.reader import _suggest_ref_level

        reg = self._reg
        suggestion = _suggest_ref_level(reg, ref_str) if reg is not None else None
        if suggestion is not None:
            message = f"Semantic object {ref_str!r} was not found. {suggestion}"
        else:
            message = (
                f"Semantic object {ref_str!r} was not found. "
                "`catalog.get(...)` requires '<kind>.<semantic_id>' such as "
                "'metric.sales.revenue'.\n"
                "Browse objects via catalog.domains, catalog.metrics, etc."
            )
        _raise(
            ErrorKind.NOT_FOUND,
            message,
            cls=SemanticRuntimeError,
            refs=(ref_str,),
        )

    def get(self, ref: str) -> CatalogObject:
        """Retrieve a single semantic object by typed id.

        Args:
            ref: Typed semantic id string (e.g. "metric.sales.revenue").

        Returns:
            CatalogObject for the requested ref.

        Example:
            >>> revenue = catalog.get("metric.sales.revenue")
            >>> revenue.details().additivity

        Constraints:
            Raises a typed not-found error when no object exists. Does not return None.
            Bare semantic ids such as "sales.revenue" raise an invalid-ref error.
            Short names such as "revenue" are rejected with a teaching error
            that includes the exact typed ID and collection call.
        """
        reg = self._require_ready()
        if isinstance(ref, str) and "." not in ref:
            index = self._require_index()
            candidates = index.named(ref)
            if len(candidates) == 1:
                typed_id = candidates[0].id
                prop = self._collection_property_name(type(candidates[0]))
                _raise(
                    ErrorKind.INVALID_REF,
                    f"catalog.get(...) received short name {ref!r}. "
                    f'Use catalog.get("{typed_id}") or '
                    f'catalog.{prop}.get("{ref}").',
                    cls=SemanticRuntimeError,
                    refs=(ref,),
                )
            if len(candidates) > 1:
                grouped = sorted(candidates, key=lambda obj: obj.id)
                calls = "\n".join(f'  catalog.get("{obj.id}")' for obj in grouped)
                _raise(
                    ErrorKind.INVALID_REF,
                    f"catalog.get(...) received short name {ref!r} that matched "
                    f"{len(candidates)} objects. Use a typed ID:\n{calls}",
                    cls=SemanticRuntimeError,
                    refs=tuple(obj.id for obj in grouped),
                )
            _raise(
                ErrorKind.INVALID_REF,
                f"catalog.get(...) received short name {ref!r}, but no object "
                f"with that name was found. Use a typed ID such as "
                f"'metric.sales.revenue' or browse via "
                f"catalog.domains, catalog.metrics, etc.",
                cls=SemanticRuntimeError,
                refs=(ref,),
            )
        typed_ref = _parse_typed_ref_id(ref, method="get", reg=reg)
        typed_id = _catalog_typed_id(typed_ref.id, typed_ref.kind)
        obj = self._require_index().get(typed_id)
        if obj is None:
            from marivo.semantic.reader import _suggest_ref_level

            ref_str = typed_ref.id
            suggestion = _suggest_ref_level(reg, ref_str)
            guidance = (
                suggestion
                if suggestion is not None
                else "Browse objects via catalog.domains, catalog.metrics, etc."
            )
            _raise(
                ErrorKind.NOT_FOUND,
                f"Semantic object {ref_str!r} was not found for kind {typed_ref.kind}. {guidance}",
                cls=SemanticRuntimeError,
                refs=(ref_str,),
            )
        if obj.ref.kind != typed_ref.kind:
            ref_str = typed_ref.id
            _raise(
                ErrorKind.NOT_FOUND,
                f"Semantic object {ref_str!r} is loaded as {obj.ref.kind}, not {typed_ref.kind}. "
                f"Use catalog.get('{obj.ref.kind}.{ref_str}') for this object.",
                cls=SemanticRuntimeError,
                refs=(ref_str,),
            )
        return obj

    def readiness(
        self,
        refs: Sequence[CatalogObject | SemanticRef] | None = None,
    ) -> ReadinessReport:
        """Return explicit certification and diagnostics for the given semantic refs.

        Reads loaded state plus persisted row-free preview evidence without
        acquiring, refreshing, or querying. Missing evidence produces exact
        next calls for the caller to execute explicitly.

        When readiness is not blocked and at least one requested ref is
        analysis-ready, the report carries a typed
        ``SemanticToAnalysisHandoff`` whose fingerprints are computed from
        the same catalog index and project root the analysis-side
        ``Session.validate_semantic_handoff`` validator checks. The handoff
        is ``None`` when readiness is blocked or no ref is ready.

        Args:
            refs: Semantic refs to check. Resolves the full dependency closure
                for each ref. None checks all loaded objects.

        Returns:
            ReadinessReport indicating whether the selected refs satisfy the
            current certification contract.

        Example:
            >>> report = catalog.readiness(refs=[revenue.ref, region.ref])
            >>> if report.status == "blocked":
            ...     report.show()
            ...     raise SystemExit

        Constraints:
            Use after authoring or changing semantic objects, or when a workflow
            requests fresh technical certification. Analysis APIs do not invoke
            readiness automatically.
        """
        self._require_ready()
        str_refs = (
            [_require_semantic_ref(r, parameter="readiness(refs=...)").id for r in refs]
            if refs is not None
            else None
        )
        report = self._project.readiness(refs=str_refs)
        return _attach_analysis_handoff(
            report, self, tuple(str_refs) if str_refs is not None else None
        )

    def verify_object(
        self,
        ref: CatalogObject | SemanticRef,
    ) -> VerifyResult:
        """Statically verify a single authored semantic object.

        Automatically reloads the catalog from disk so that newly authored
        objects are visible without a separate ``catalog.load()`` call.

        Verification applies project load, assembly, dependency, type, cycle,
        and expression-contract checks without opening a datasource. Use
        ``catalog.preview(...)`` for runtime execution checks.

        Args:
            ref: CatalogObject or SemanticRef to verify.

        Returns:
            VerifyResult with static validation status, issues, and warnings.

        Example:
            >>> orders = catalog.get("entity.sales.orders")
            >>> result = catalog.verify_object(orders.ref)
            >>> if result.status == "failed":
            ...     result.show()

        Constraints:
            ``verify_object`` validates only static authored-project contracts
            and performs no datasource query.
        """
        result = self._project.verify_object(ref)
        self._reg = self._project._registry
        return result

    def contract(self) -> AuthoringContract:
        """Return the mechanical continuation contract for this catalog.

        The contract exposes catalog-level browse and load affordances, not
        per-object transitions. Use ``CatalogObject.contract()`` for
        object-scoped verify, preview, and readiness transitions.
        """
        from marivo.semantic._capabilities.contracts import contract_for_semantic_catalog

        return contract_for_semantic_catalog()

    def _resolver(
        self,
        *,
        connections: object | None = None,
        sample_size: int | None = None,
        entity_scopes: Mapping[str, AuthoringScope] | None = None,
    ) -> SemanticResolver:
        """Return an internal resolver backed by Materializer."""
        self._require_ready()
        if connections is None:
            connections = self._project._connection_service()
        from marivo.semantic.resolver import SemanticResolver

        return SemanticResolver(
            self,
            connections=connections,
            sample_size=sample_size,
            entity_scopes=entity_scopes,
        )

    def preview(
        self,
        ref: CatalogObject | SemanticRef,
        *,
        using: PreviewUsing,
        limit: int = PREVIEW_DEFAULT_LIMIT,
        include_types: bool = True,
        context_columns: Iterable[str] | None = None,
    ) -> PreviewResult:
        """Return a bounded preview for one executable semantic object.

        Args:
            ref: Full semantic ref string or SemanticRef to preview.
            using: Exact discovery snapshot binding for every dependency entity.
            limit: Maximum number of preview rows to return.
            include_types: Whether to include backend schema type strings.
            context_columns: Optional parent-entity columns to include before a
                dimension or time-dimension preview value.

        Returns:
            PreviewResult with bounded rows, display columns, warnings, and
            sample policy metadata.

        Example:
            >>> region = catalog.get("dimension.sales.orders.region")
            >>> amount = catalog.get("measure.sales.orders.amount")
            >>> revenue = catalog.get("metric.sales.revenue")
            >>> catalog.preview(region.ref, using=orders_snapshot, context_columns=("order_id",))
            >>> catalog.preview(amount.ref, using=orders_snapshot)
            >>> catalog.preview(revenue.ref, using=orders_snapshot).warnings

        Constraints:
            ``context_columns`` is valid only for dimension and time-dimension
            refs. Measure previews show bounded row-level values. Metric previews
            use the existing approximate pre-aggregate sample behavior.
        """
        reg = self._require_ready()
        ref_obj = _require_semantic_ref(ref, parameter="preview(ref=...)")
        ref_str = ref_obj.id
        kind = self._resolve_kind_of(ref_str, reg)
        if kind is None:
            self._raise_not_found(ref_str)
        if kind != ref_obj.kind:
            _raise(
                ErrorKind.NOT_FOUND,
                f"Semantic object {ref_str!r} is loaded as {kind}, not {ref_obj.kind}. "
                "Use catalog.get('<kind>.<semantic_id>').ref to obtain the correctly typed ref.",
                cls=SemanticRuntimeError,
                refs=(ref_str,),
            )
        sidecar = self._project._sidecar
        if sidecar is None:
            _raise(
                ErrorKind.PROJECT_NOT_LOADED,
                "Semantic catalog sidecar is unavailable. Reload the catalog before previewing.",
                cls=SemanticRuntimeError,
                refs=(ref_str,),
            )
        bindings = normalize_preview_bindings(
            ref=ref_str,
            kind=kind,
            using=using,
            registry=reg,
            sidecar=sidecar,
            project_root=self.workspace_dir,
        )
        preview_limit = validate_preview_limit(limit)
        is_field_preview = kind in {
            SemanticKind.DIMENSION,
            SemanticKind.TIME_DIMENSION,
        }
        if context_columns is not None and not is_field_preview:
            _raise(
                ErrorKind.MATERIALIZE_FAILED,
                "catalog.preview(..., context_columns=...) is only valid for dimension refs.",
                cls=SemanticRuntimeError,
                refs=(ref_str,),
                details={"query_executed": False},
            )
        selected_context_input = tuple(context_columns) if context_columns is not None else None
        from marivo.datasource.timezone import system_timezone_name

        profile = require_profile_for_backend_type(bindings.backend)
        timeout = profile.authoring_timeout
        if timeout is None:
            _raise(
                ErrorKind.MATERIALIZE_FAILED,
                "catalog.preview() requires an adapter-enforced authoring timeout.",
                cls=SemanticRuntimeError,
                refs=(ref_str,),
                details={"query_executed": False, "backend": bindings.backend},
            )
        connections = self._project._connection_service()
        backend = connections.session_backend(bindings.datasource_id)

        def execute_preview() -> PreviewResult:
            resolver = self._resolver(
                connections=connections,
                sample_size=(METRIC_PREVIEW_SAMPLE_SIZE if kind == SemanticKind.METRIC else None),
                entity_scopes=bindings.entity_scopes,
            )
            if kind == SemanticKind.ENTITY:
                table = resolver.table(make_ref(ref_str, SemanticKind.ENTITY))
                return preview_ibis_table(
                    table,
                    kind="semantic_dataset",
                    ref=ref_str,
                    limit=preview_limit,
                    sample_policy=PreviewSamplePolicy(method="bounded_limit", limit=preview_limit),
                    include_types=include_types,
                    report_tz=system_timezone_name(),
                )
            if kind == SemanticKind.MEASURE:
                measure_ir = reg.measures[ref_str]
                parent_table = resolver.table(make_ref(measure_ir.entity, SemanticKind.ENTITY))
                measure_value = resolver.measure(make_ref(ref_str, SemanticKind.MEASURE))
                measure_column_name = ref_str.rsplit(".", 1)[-1]
                preview_table = parent_table.select(measure_value.name(measure_column_name))
                return preview_ibis_table(
                    preview_table,
                    kind="semantic_measure",
                    ref=ref_str,
                    limit=preview_limit,
                    sample_policy=PreviewSamplePolicy(method="bounded_limit", limit=preview_limit),
                    include_types=include_types,
                    report_tz=system_timezone_name(),
                )
            if is_field_preview:
                field_ir = reg.dimensions[ref_str]
                parent_table = resolver.table(make_ref(field_ir.entity, SemanticKind.ENTITY))
                field_value = resolver.dimension(make_ref(ref_str, kind))
                field_column_name = ref_str.rsplit(".", 1)[-1]
                report_tz = system_timezone_name()
                datasource_timezone = None
                if kind == SemanticKind.TIME_DIMENSION:
                    entity_ir = reg.entities[field_ir.entity]
                    engine_tz_method = getattr(
                        resolver.connections,
                        "engine_timezone",
                        None,
                    )
                    if callable(engine_tz_method):
                        datasource_timezone = engine_tz_method(entity_ir.datasource)
                selected_context = selected_context_input
                if selected_context is None:
                    selected_context = tuple(
                        column for column in parent_table.columns if column != field_column_name
                    )[:3]
                missing_context = [
                    column for column in selected_context if column not in parent_table.columns
                ]
                if missing_context:
                    _raise(
                        ErrorKind.MATERIALIZE_FAILED,
                        "Field preview context columns are not present on parent "
                        f"dataset: {missing_context}",
                        cls=SemanticRuntimeError,
                        refs=(ref_str,),
                    )
                preview_table = parent_table.select(
                    *[parent_table[column] for column in selected_context],
                    field_value.name(field_column_name),
                )
                return preview_ibis_table(
                    preview_table,
                    kind="semantic_field",
                    ref=ref_str,
                    limit=preview_limit,
                    sample_policy=PreviewSamplePolicy(method="bounded_limit", limit=preview_limit),
                    include_types=include_types,
                    timezones=_preview_timezones_for_field(
                        column_name=field_column_name,
                        field_ir=field_ir,
                        datasource_timezone=datasource_timezone,
                        report_tz=report_tz,
                    ),
                    report_tz=report_tz,
                )
            if kind == SemanticKind.METRIC:
                metric_value = resolver.metric(make_ref(ref_str, SemanticKind.METRIC))
                result = preview_ibis_value(
                    metric_value,
                    kind="semantic_metric",
                    ref=ref_str,
                    limit=preview_limit,
                    column_name="value",
                    sample_policy=PreviewSamplePolicy(
                        method="pre_aggregate_limit", limit=preview_limit
                    ),
                    include_types=include_types,
                )
                return PreviewResult(
                    kind=result.kind,
                    ref=result.ref,
                    columns=result.columns,
                    types=result.types,
                    rows=result.rows,
                    requested_limit=result.requested_limit,
                    returned_row_count=result.returned_row_count,
                    is_truncated=result.is_truncated,
                    status=result.status,
                    coverage=result.coverage,
                    warnings=(
                        *result.warnings,
                        PreviewWarning(
                            kind="approximate_preview",
                            message=f"metric computed on {METRIC_PREVIEW_SAMPLE_SIZE} row sample, result is approximate",
                        ),
                    ),
                    sample_policy=result.sample_policy,
                    timezones=result.timezones,
                )
            if kind == SemanticKind.RELATIONSHIP:
                relationship = reg.relationships[ref_str]
                left = resolver.table(make_ref(relationship.from_entity, SemanticKind.ENTITY))
                right = resolver.table(make_ref(relationship.to_entity, SemanticKind.ENTITY))
                left_names: list[str] = []
                right_names: list[str] = []
                left_values = []
                right_values = []
                for index, key in enumerate(relationship.keys, start=1):
                    from_key, to_key = key.to_tuple()
                    from_kind = (
                        SemanticKind.TIME_DIMENSION
                        if reg.dimensions[from_key].is_time_dimension
                        else SemanticKind.DIMENSION
                    )
                    to_kind = (
                        SemanticKind.TIME_DIMENSION
                        if reg.dimensions[to_key].is_time_dimension
                        else SemanticKind.DIMENSION
                    )
                    left_name = f"from_key_{index}"
                    right_name = f"to_key_{index}"
                    left_names.append(left_name)
                    right_names.append(right_name)
                    left_values.append(
                        resolver.dimension_on(make_ref(from_key, from_kind), left).name(left_name)
                    )
                    right_values.append(
                        resolver.dimension_on(make_ref(to_key, to_kind), right).name(right_name)
                    )
                left_keys = left.select(*left_values)
                right_keys = right.select(*right_values)
                joined = left_keys.join(
                    right_keys,
                    predicates=[
                        left_keys[left_name] == right_keys[right_name]
                        for left_name, right_name in zip(left_names, right_names, strict=True)
                    ],
                    how="inner",
                ).select(*(left_names + right_names))
                return preview_ibis_table(
                    joined,
                    kind="semantic_dataset",
                    ref=ref_str,
                    limit=preview_limit,
                    sample_policy=PreviewSamplePolicy(method="bounded_limit", limit=preview_limit),
                    include_types=include_types,
                    report_tz=system_timezone_name(),
                )
            _raise(
                ErrorKind.MATERIALIZE_FAILED,
                f"catalog.preview() does not support {kind} refs.",
                cls=SemanticRuntimeError,
                refs=(ref_str,),
                details={"kind": str(kind)},
            )

        with timeout(backend, bindings.timeout_seconds):
            result = execute_preview()
        return persist_preview_check(
            result,
            bindings=bindings,
            project_root=self.workspace_dir,
        )


def load(
    *,
    workspace_dir: str | Path | None = None,
    domains: str | Sequence[str] | None = None,
) -> SemanticCatalog:
    """Load a semantic project and return a browseable SemanticCatalog.

    Args:
        workspace_dir: Path to the project root containing ``marivo.toml``.
            Defaults to the current working directory when omitted. The local
            ``models/`` root is always loaded; external models roots can be
            added with ``marivo.toml [semantic].layer_paths``.
        domains: When specified, only those domain directories are loaded.
            Pass a single domain name as a string or a list of names.
            Cross-domain references to filtered-out domains produce warnings
            instead of errors, so the registry remains usable.

    Returns:
        SemanticCatalog on success.

    Example:
        >>> import marivo.semantic as ms
        >>> catalog = ms.load()
        >>> catalog.domains.show()
        >>> catalog = ms.load(domains=["sales"])
        >>> catalog.domains.show()

    Constraints:
        Raises a typed load error on failure. Does not return a partial catalog.
        Does not print to stdout.
        Configured layer paths must point at authored ``models/`` roots that
        contain both ``datasources/`` and ``semantic/``.
    """
    import os

    from marivo.semantic.reader import SemanticProject

    if workspace_dir is None:
        env = os.environ.get("MARIVO_PROJECT_ROOT")
        workspace_dir = env if env else Path.cwd()

    project = SemanticProject(workspace_dir=workspace_dir)
    result = project.load(domains=domains)
    if result.status != "ready":
        from marivo.semantic.errors import SemanticLoadFailed

        raise SemanticLoadFailed(result.errors)
    return SemanticCatalog(project)
