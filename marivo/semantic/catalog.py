"""SemanticCatalog — unified agent-facing read surface for marivo.semantic.

Public entrypoint: ms.load() -> SemanticCatalog
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, NoReturn

from marivo.datasource.ir import AiContextIR, DatasourceIR, DatasourceSourceLocation
from marivo.semantic.dtos import DatasetSource, FileSource, TableSource
from marivo.semantic.errors import ErrorKind, SemanticRuntimeError, _raise
from marivo.semantic.ir import (
    DimensionIR,
    DimensionKind,
    DomainIR,
    EntityIR,
    EntityVersioningIR,
    MetricIR,
    ParityStatus,
    RelationshipIR,
    SnapshotVersioningIR,
    SourceLocation,
    SymbolKind,
)
from marivo.semantic.parity import propagated_parity_status

if TYPE_CHECKING:
    from marivo.semantic.reader import SemanticProject
    from marivo.semantic.readiness import ReadinessReport
    from marivo.semantic.validator import Registry

# list[SemanticObject] return annotations inside SemanticCatalog shadow the
# built-in list type because the class has a method named list().  Use this
# alias to avoid the name collision.
_ListOfSemanticObject = list["SemanticObject"]

__all__ = [
    "AiContextView",
    "DatasetVersioning",
    "DatasourceDetails",
    "DimensionDetails",
    "DomainDetails",
    "EntityDetails",
    "MetricDetails",
    "RelationshipDetails",
    "SemanticCatalog",
    "SemanticKind",
    "SemanticKindInput",
    "SemanticObject",
    "SemanticObjectDetails",
    "SemanticObjectList",
    "SemanticRef",
    "SemanticRefInput",
    "SnapshotVersioning",
    "TimeDimensionDetails",
    "ValidityVersioning",
    "load",
]

# SemanticKind is a stable alias for the internal SymbolKind enum.
# Both share the same values: domain, datasource, entity, dimension,
# time_dimension, metric, relationship.
SemanticKind = SymbolKind


@dataclass(frozen=True)
class SemanticRef:
    """Stable semantic identifier that can be handed off to analysis APIs.

    Args:
        ref: Full semantic ref string such as ``"sales.revenue"``.
        kind: Semantic kind of the object this ref identifies.

    Returns:
        SemanticRef whose ``str()`` representation is the plain ref string.

    Example:
        >>> r = SemanticRef(ref="sales.revenue", kind=SemanticKind.METRIC)
        >>> str(r)
        'sales.revenue'
        >>> session.observe(metric=r)

    Constraints:
        Only full ref strings are accepted. Short names such as ``"revenue"``
        are not valid SemanticRef values.
    """

    ref: str
    kind: SemanticKind

    def __str__(self) -> str:
        return self.ref

    def __repr__(self) -> str:
        return f"SemanticRef({self.ref!r}, kind={str(self.kind)!r})"


@dataclass(frozen=True)
class AiContextView:
    """Read-only view of the ai_context authored on a semantic object.

    Args:
        business_definition: Business meaning of the object.
        guardrails: Usage constraints and safety rules.
        synonyms: Alternative names for the object.
        examples: Illustrative values or usage examples.
        instructions: Agent-facing usage instructions.
        owner_notes: Internal notes from the object owner.

    Returns:
        Frozen AiContextView with all authored ai_context fields.

    Example:
        >>> revenue = catalog.get("sales.revenue")
        >>> revenue.context.business_definition
        'Gross revenue from completed orders.'

    Constraints:
        ``business_definition`` and ``guardrails`` are the authoritative
        source of business meaning. ``SemanticObject.description`` is a
        short display summary only.
    """

    business_definition: str | None
    guardrails: tuple[str, ...]
    synonyms: tuple[str, ...]
    examples: tuple[str, ...]
    instructions: str | None
    owner_notes: str | None


# ---------------------------------------------------------------------------
# Versioning types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SnapshotVersioning:
    """Snapshot versioning metadata for a dataset."""

    kind: Literal["snapshot"]
    partition_field: str
    grain: Literal["day"]
    timezone: str | None = None
    format: str | None = None


@dataclass(frozen=True)
class ValidityVersioning:
    """SCD2 validity-window versioning metadata for a dataset."""

    kind: Literal["validity"]
    valid_from: str
    valid_to: str
    interval: Literal["closed_open", "closed_closed"]
    open_end: tuple[Any, ...]
    timezone: str | None = None


DatasetVersioning = SnapshotVersioning | ValidityVersioning


# ---------------------------------------------------------------------------
# Kind-specific details
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DatasourceDetails:
    """Details for a datasource object."""

    ref: SemanticRef
    kind: SemanticKind
    name: str
    domain: None
    description: str | None
    context: AiContextView
    source_location: SourceLocation
    parents: tuple[SemanticRef, ...]
    children: tuple[SemanticRef, ...]
    dependents: tuple[SemanticRef, ...]
    backend_type: str


@dataclass(frozen=True)
class DomainDetails:
    """Details for a domain object."""

    ref: SemanticRef
    kind: SemanticKind
    name: str
    domain: str
    description: str | None
    context: AiContextView
    source_location: SourceLocation
    parents: tuple[SemanticRef, ...]
    children: tuple[SemanticRef, ...]
    dependents: tuple[SemanticRef, ...]


@dataclass(frozen=True)
class EntityDetails:
    """Details for an entity object."""

    ref: SemanticRef
    kind: SemanticKind
    name: str
    domain: str
    description: str | None
    context: AiContextView
    source_location: SourceLocation
    parents: tuple[SemanticRef, ...]
    children: tuple[SemanticRef, ...]
    dependents: tuple[SemanticRef, ...]
    datasource: SemanticRef
    source: DatasetSource
    primary_key: tuple[str, ...]
    versioning: DatasetVersioning | None


@dataclass(frozen=True)
class DimensionDetails:
    """Details for a dimension or measure field."""

    ref: SemanticRef
    kind: SemanticKind
    name: str
    domain: str
    description: str | None
    context: AiContextView
    source_location: SourceLocation
    parents: tuple[SemanticRef, ...]
    children: tuple[SemanticRef, ...]
    dependents: tuple[SemanticRef, ...]
    entity: SemanticRef
    dimension_kind: Literal["categorical", "measure"]


@dataclass(frozen=True)
class TimeDimensionDetails:
    """Details for a time dimension object."""

    ref: SemanticRef
    kind: SemanticKind
    name: str
    domain: str
    description: str | None
    context: AiContextView
    source_location: SourceLocation
    parents: tuple[SemanticRef, ...]
    children: tuple[SemanticRef, ...]
    dependents: tuple[SemanticRef, ...]
    entity: SemanticRef
    data_type: str | None
    granularity: str | None
    format: str | None
    timezone: str | None
    required_prefix: str | None
    is_default: bool


@dataclass(frozen=True)
class MetricDetails:
    """Details for a metric (entity-backed, derived, or cross-entity)."""

    ref: SemanticRef
    kind: SemanticKind
    name: str
    domain: str
    description: str | None
    context: AiContextView
    source_location: SourceLocation
    parents: tuple[SemanticRef, ...]
    children: tuple[SemanticRef, ...]
    dependents: tuple[SemanticRef, ...]
    entities: tuple[SemanticRef, ...]
    root_entity: SemanticRef | None
    is_derived: bool
    component_metrics: tuple[SemanticRef, ...]
    required_relationships: tuple[SemanticRef, ...]
    decomposition: Literal["sum", "ratio", "weighted_average"]
    additivity: Literal["additive", "semi_additive", "non_additive"] | None
    fanout_policy: Literal["block", "aggregate_then_join"]
    unit: str | None
    verification_mode: Literal["sql_parity", "python_native"] | None
    parity_status: ParityStatus
    source_sql: str | None
    source_dialect: str | None
    source_document: str | None
    source_notes: str | None
    python_symbol: str


@dataclass(frozen=True)
class RelationshipDetails:
    """Details for a relationship between entities."""

    ref: SemanticRef
    kind: SemanticKind
    name: str
    domain: str
    description: str | None
    context: AiContextView
    source_location: SourceLocation
    parents: tuple[SemanticRef, ...]
    children: tuple[SemanticRef, ...]
    dependents: tuple[SemanticRef, ...]
    from_entity: SemanticRef
    to_entity: SemanticRef
    from_dimensions: tuple[str, ...]
    to_dimensions: tuple[str, ...]


SemanticObjectDetails = (
    DatasourceDetails
    | DomainDetails
    | EntityDetails
    | DimensionDetails
    | TimeDimensionDetails
    | MetricDetails
    | RelationshipDetails
)


@dataclass(frozen=True)
class SemanticObject:
    """Single read shape for all loaded semantic objects.

    Args:
        ref: Stable semantic identifier, passable directly to analysis APIs.
        kind: Semantic kind of this object.
        name: Short leaf name (no domain prefix).
        domain: Owning domain name, or None for datasources.
        description: Short display summary (not business meaning).
        context: Business meaning, guardrails, and usage guidance from ai_context.
        source_location: Location in the user-authored semantic file.

    Returns:
        SemanticObject with all common fields and kind-specific detail via details().

    Example:
        >>> revenue = catalog.get("sales.revenue")
        >>> revenue.ref           # SemanticRef("sales.revenue", kind="metric")
        >>> revenue.description   # "Gross revenue."
        >>> revenue.context.business_definition
        >>> revenue.details().additivity

    Constraints:
        ``description`` is a short display summary only. Business meaning and
        guardrails live under ``context``. Use ``catalog.list(parent=...)``
        for hierarchy browsing — SemanticObject does not expose navigation methods.
    """

    ref: SemanticRef
    kind: SemanticKind
    name: str
    domain: str | None
    description: str | None
    context: AiContextView
    source_location: SourceLocation
    python_symbol: str
    _details: SemanticObjectDetails

    def details(self) -> SemanticObjectDetails:
        """Return the typed kind-specific details for this object.

        Args:
            None

        Returns:
            Kind-specific details dataclass (EntityDetails, MetricDetails, etc.)
            including parents, children, dependents, and structural facts.

        Example:
            >>> d = catalog.get("sales.revenue").details()
            >>> d.additivity
            >>> d.component_metrics

        Constraints:
            The returned object never exposes internal IR instances.
        """
        return self._details


class SemanticObjectList:
    """Browsing result returned by catalog.list(...).

    Args:
        items: Ordered tuple of SemanticObject results.
        parent_label: String label of the parent used for rendering (e.g. 'sales.orders').
        kind_filter: Kind filter string used in the request, or None.

    Returns:
        SemanticObjectList with render/show for display and refs()/objects for consumption.

    Example:
        >>> result = catalog.list("sales.orders")
        >>> result.show()
        >>> result.refs()          # tuple[SemanticRef, ...]
        >>> result.objects         # tuple[SemanticObject, ...]

    Constraints:
        render() never omits items from the objects tuple unless explicitly
        truncated with a message.
    """

    def __init__(
        self,
        items: tuple[SemanticObject, ...],
        parent_label: str | None,
        kind_filter: str | None,
    ) -> None:
        self._items = items
        self._parent_label = parent_label
        self._kind_filter = kind_filter

    @property
    def objects(self) -> tuple[SemanticObject, ...]:
        """Return all SemanticObject results."""
        return self._items

    def refs(self) -> tuple[SemanticRef, ...]:
        """Return the SemanticRef for every object in this list."""
        return tuple(obj.ref for obj in self._items)

    def ids(self) -> list[str]:
        """Return plain-string refs for every object in this list."""
        return [obj.ref.ref for obj in self._items]

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[SemanticObject]:
        return iter(self._items)

    def __getitem__(self, index: int) -> SemanticObject:
        return self._items[index]

    def __repr__(self) -> str:
        return f"<SemanticObjectList items={len(self._items)}; call .show() to inspect>"

    def render(self) -> str:
        """Return bounded plain-text browsing card without a trailing newline."""
        lines: list[str] = []
        if self._parent_label:
            lines.append(self._parent_label)
        if not self._items:
            filter_note = f" kind={self._kind_filter!r}" if self._kind_filter else ""
            parent_note = self._parent_label or "catalog"
            lines.append(f"  (no objects found under {parent_note!r}{filter_note})")
            lines.append("next steps:")
            lines.append(
                "  catalog.list().show()           # browse top-level domains and datasources"
            )
            return "\n".join(lines)

        for obj in self._items:
            kind_str = str(obj.kind)
            ref_str = obj.ref.ref
            lines.append(f"  {kind_str:<12}{ref_str}")

        lines.append("")
        lines.append("next steps:")
        if self._items:
            first_ref = self._items[0].ref.ref
            lines.append(
                f"  catalog.get({first_ref!r}){'': <4}# retrieve a SemanticObject by full ref"
            )
        lines.append(
            "  result.refs()                   # obtain all SemanticRef values for analysis handoff"
        )
        return "\n".join(lines).rstrip("\n")

    def show(self) -> None:
        """Print render() output and return None."""
        print(self.render())


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

SemanticRefInput = SemanticRef | str
SemanticKindInput = SemanticKind | str

_VALID_KINDS: frozenset[str] = frozenset(str(k) for k in SymbolKind)

_BROWSABLE_PARENT_KINDS: frozenset[str] = frozenset(
    {
        str(SymbolKind.DOMAIN),
        str(SymbolKind.ENTITY),
        str(SymbolKind.DATASOURCE),
    }
)


def _to_ref_str(ref: SemanticRefInput) -> str:
    return str(ref)  # works for both str and SemanticRef


def _validate_kind(kind_input: SemanticKindInput) -> SemanticKind:
    kind_str = str(kind_input).lower()
    if kind_str not in _VALID_KINDS:
        sorted_values = ", ".join(sorted(_VALID_KINDS))
        _raise(
            ErrorKind.UNSUPPORTED_KIND,
            f"Unsupported semantic kind {kind_input!r}. Supported values: {sorted_values}.",
            cls=SemanticRuntimeError,
        )
    return SymbolKind(kind_str)


def _ai_context_from_ir(ir: AiContextIR) -> AiContextView:
    return AiContextView(
        business_definition=ir.business_definition,
        guardrails=ir.guardrails,
        synonyms=ir.synonyms,
        examples=ir.examples,
        instructions=ir.instructions,
        owner_notes=ir.owner_notes,
    )


def _normalize_location(loc: SourceLocation | DatasourceSourceLocation) -> SourceLocation:
    return SourceLocation(file=loc.file, line=loc.line)


def _versioning_from_ir(
    ir_v: EntityVersioningIR | None,
) -> DatasetVersioning | None:
    if ir_v is None:
        return None
    if isinstance(ir_v, SnapshotVersioningIR):
        return SnapshotVersioning(
            kind="snapshot",
            partition_field=ir_v.partition_field,
            grain=ir_v.grain,
            timezone=ir_v.timezone,
            format=ir_v.format,
        )
    return ValidityVersioning(
        kind="validity",
        valid_from=ir_v.valid_from,
        valid_to=ir_v.valid_to,
        interval=ir_v.interval,
        open_end=ir_v.open_end,
        timezone=ir_v.timezone,
    )


def _source_from_ir(source_ir: Any) -> DatasetSource:
    from marivo.semantic.ir import TableSourceIR

    if isinstance(source_ir, TableSourceIR):
        return TableSource(table=source_ir.table, database=source_ir.database)
    return FileSource(path=source_ir.path, format=source_ir.format)


def _build_datasource_object(ds_ir: DatasourceIR, reg: Registry) -> SemanticObject:
    ref = SemanticRef(ref=ds_ir.semantic_id, kind=SemanticKind.DATASOURCE)
    dependents = tuple(
        SemanticRef(ref=d.semantic_id, kind=SemanticKind.ENTITY)
        for d in reg.datasets.values()
        if d.datasource == ds_ir.semantic_id
    )
    details = DatasourceDetails(
        ref=ref,
        kind=SemanticKind.DATASOURCE,
        name=ds_ir.name,
        domain=None,
        description=ds_ir.description,
        context=_ai_context_from_ir(ds_ir.ai_context),
        source_location=_normalize_location(ds_ir.location),
        parents=(),
        children=(),
        dependents=dependents,
        backend_type=ds_ir.backend_type,
    )
    return SemanticObject(
        ref=ref,
        kind=SemanticKind.DATASOURCE,
        name=ds_ir.name,
        domain=None,
        description=ds_ir.description,
        context=_ai_context_from_ir(ds_ir.ai_context),
        source_location=_normalize_location(ds_ir.location),
        python_symbol=ds_ir.python_symbol,
        _details=details,
    )


def _build_domain_object(model_ir: DomainIR, reg: Registry) -> SemanticObject:
    ref = SemanticRef(ref=model_ir.name, kind=SemanticKind.DOMAIN)
    datasets_refs = tuple(
        SemanticRef(ref=d.semantic_id, kind=SemanticKind.ENTITY)
        for d in reg.datasets.values()
        if d.domain == model_ir.name
    )
    metrics_refs = tuple(
        SemanticRef(ref=m.semantic_id, kind=SemanticKind.METRIC)
        for m in reg.metrics.values()
        if m.domain == model_ir.name
    )
    children = datasets_refs + metrics_refs
    details = DomainDetails(
        ref=ref,
        kind=SemanticKind.DOMAIN,
        name=model_ir.name,
        domain=model_ir.name,
        description=model_ir.description,
        context=_ai_context_from_ir(model_ir.ai_context),
        source_location=model_ir.location,
        parents=(),
        children=children,
        dependents=(),
    )
    return SemanticObject(
        ref=ref,
        kind=SemanticKind.DOMAIN,
        name=model_ir.name,
        domain=model_ir.name,
        description=model_ir.description,
        context=_ai_context_from_ir(model_ir.ai_context),
        source_location=model_ir.location,
        python_symbol="",
        _details=details,
    )


def _build_entity_object(ds_ir: EntityIR, reg: Registry) -> SemanticObject:
    ref = SemanticRef(ref=ds_ir.semantic_id, kind=SemanticKind.ENTITY)
    ds_ref = SemanticRef(ref=ds_ir.datasource, kind=SemanticKind.DATASOURCE)
    fields_refs = tuple(
        SemanticRef(
            ref=f.semantic_id,
            kind=SemanticKind.TIME_DIMENSION if f.is_time_dimension else SemanticKind.DIMENSION,
        )
        for f in reg.fields.values()
        if f.entity == ds_ir.semantic_id
    )
    rels_refs = tuple(
        SemanticRef(ref=r.semantic_id, kind=SemanticKind.RELATIONSHIP)
        for r in reg.relationships.values()
        if r.from_entity == ds_ir.semantic_id or r.to_entity == ds_ir.semantic_id
    )
    children = fields_refs + rels_refs
    metric_dependents = tuple(
        SemanticRef(ref=m.semantic_id, kind=SemanticKind.METRIC)
        for m in reg.metrics.values()
        if ds_ir.semantic_id in m.entities
    )
    source = _source_from_ir(ds_ir.source)
    details = EntityDetails(
        ref=ref,
        kind=SemanticKind.ENTITY,
        name=ds_ir.name,
        domain=ds_ir.domain,
        description=ds_ir.description,
        context=_ai_context_from_ir(ds_ir.ai_context),
        source_location=ds_ir.location,
        parents=(ds_ref,),
        children=children,
        dependents=metric_dependents,
        datasource=ds_ref,
        source=source,
        primary_key=ds_ir.primary_key,
        versioning=_versioning_from_ir(ds_ir.versioning),
    )
    return SemanticObject(
        ref=ref,
        kind=SemanticKind.ENTITY,
        name=ds_ir.name,
        domain=ds_ir.domain,
        description=ds_ir.description,
        context=_ai_context_from_ir(ds_ir.ai_context),
        source_location=ds_ir.location,
        python_symbol=ds_ir.python_symbol,
        _details=details,
    )


def _build_dimension_object(f_ir: DimensionIR, reg: Registry) -> SemanticObject:
    is_time = f_ir.is_time_dimension
    kind = SemanticKind.TIME_DIMENSION if is_time else SemanticKind.DIMENSION
    ref = SemanticRef(ref=f_ir.semantic_id, kind=kind)
    ds_ref = SemanticRef(ref=f_ir.entity, kind=SemanticKind.ENTITY)
    if is_time:
        details: SemanticObjectDetails = TimeDimensionDetails(
            ref=ref,
            kind=kind,
            name=f_ir.name,
            domain=f_ir.domain,
            description=f_ir.description,
            context=_ai_context_from_ir(f_ir.ai_context),
            source_location=f_ir.location,
            parents=(ds_ref,),
            children=(),
            dependents=(),
            entity=ds_ref,
            data_type=f_ir.data_type,
            granularity=f_ir.granularity,
            format=f_ir.format,
            timezone=f_ir.timezone,
            required_prefix=f_ir.required_prefix,
            is_default=f_ir.is_default,
        )
    else:
        dimension_kind: Literal["categorical", "measure"] = (
            "measure" if f_ir.kind == DimensionKind.MEASURE else "categorical"
        )
        details = DimensionDetails(
            ref=ref,
            kind=kind,
            name=f_ir.name,
            domain=f_ir.domain,
            description=f_ir.description,
            context=_ai_context_from_ir(f_ir.ai_context),
            source_location=f_ir.location,
            parents=(ds_ref,),
            children=(),
            dependents=(),
            entity=ds_ref,
            dimension_kind=dimension_kind,
        )
    return SemanticObject(
        ref=ref,
        kind=kind,
        name=f_ir.name,
        domain=f_ir.domain,
        description=f_ir.description,
        context=_ai_context_from_ir(f_ir.ai_context),
        source_location=f_ir.location,
        python_symbol=f_ir.python_symbol,
        _details=details,
    )


def _build_metric_object(m_ir: MetricIR, reg: Registry, project: SemanticProject) -> SemanticObject:
    ref = SemanticRef(ref=m_ir.semantic_id, kind=SemanticKind.METRIC)
    entity_refs = tuple(SemanticRef(ref=ds, kind=SemanticKind.ENTITY) for ds in m_ir.entities)
    root_entity_ref = (
        SemanticRef(ref=m_ir.root_entity, kind=SemanticKind.ENTITY) if m_ir.root_entity else None
    )
    component_refs = tuple(
        SemanticRef(ref=comp_ref, kind=SemanticKind.METRIC)
        for comp_ref in m_ir.decomposition.components.values()
    )
    required_rels: tuple[SemanticRef, ...] = ()
    if len(m_ir.entities) > 1:
        required_rels = tuple(
            SemanticRef(ref=r.semantic_id, kind=SemanticKind.RELATIONSHIP)
            for r in reg.relationships.values()
            if r.domain == m_ir.domain
            and r.from_entity in m_ir.entities
            and r.to_entity in m_ir.entities
        )
    parents = entity_refs + component_refs + required_rels
    dependents = tuple(
        SemanticRef(ref=m2.semantic_id, kind=SemanticKind.METRIC)
        for m2 in reg.metrics.values()
        if m_ir.semantic_id in m2.decomposition.components.values()
    )
    parity_status = propagated_parity_status(project, m_ir.semantic_id)
    details = MetricDetails(
        ref=ref,
        kind=SemanticKind.METRIC,
        name=m_ir.name,
        domain=m_ir.domain,
        description=m_ir.description,
        context=_ai_context_from_ir(m_ir.ai_context),
        source_location=m_ir.location,
        parents=parents,
        children=(),
        dependents=dependents,
        entities=entity_refs,
        root_entity=root_entity_ref,
        is_derived=m_ir.is_derived,
        component_metrics=component_refs,
        required_relationships=required_rels,
        decomposition=m_ir.decomposition.kind,
        additivity=m_ir.additivity,
        fanout_policy=m_ir.fanout_policy,
        unit=m_ir.unit,
        verification_mode=m_ir.provenance.verification_mode,
        parity_status=parity_status,
        source_sql=m_ir.provenance.source_sql,
        source_dialect=m_ir.provenance.source_dialect,
        source_document=m_ir.provenance.source_document,
        source_notes=m_ir.provenance.source_notes,
        python_symbol=m_ir.python_symbol,
    )
    return SemanticObject(
        ref=ref,
        kind=SemanticKind.METRIC,
        name=m_ir.name,
        domain=m_ir.domain,
        description=m_ir.description,
        context=_ai_context_from_ir(m_ir.ai_context),
        source_location=m_ir.location,
        python_symbol=m_ir.python_symbol,
        _details=details,
    )


def _build_relationship_object(r_ir: RelationshipIR, reg: Registry) -> SemanticObject:
    ref = SemanticRef(ref=r_ir.semantic_id, kind=SemanticKind.RELATIONSHIP)
    from_ref = SemanticRef(ref=r_ir.from_entity, kind=SemanticKind.ENTITY)
    to_ref = SemanticRef(ref=r_ir.to_entity, kind=SemanticKind.ENTITY)
    details = RelationshipDetails(
        ref=ref,
        kind=SemanticKind.RELATIONSHIP,
        name=r_ir.name,
        domain=r_ir.domain,
        description=r_ir.description,
        context=_ai_context_from_ir(r_ir.ai_context),
        source_location=r_ir.location,
        parents=(from_ref, to_ref),
        children=(),
        dependents=(),
        from_entity=from_ref,
        to_entity=to_ref,
        from_dimensions=r_ir.from_dimensions,
        to_dimensions=r_ir.to_dimensions,
    )
    return SemanticObject(
        ref=ref,
        kind=SemanticKind.RELATIONSHIP,
        name=r_ir.name,
        domain=r_ir.domain,
        description=r_ir.description,
        context=_ai_context_from_ir(r_ir.ai_context),
        source_location=r_ir.location,
        python_symbol="",
        _details=details,
    )


# ---------------------------------------------------------------------------
# SemanticCatalog
# ---------------------------------------------------------------------------


class SemanticCatalog:
    """Read-only object graph over a loaded semantic project.

    Args:
        project: A loaded SemanticProject instance (status must be 'ready').

    Returns:
        SemanticCatalog with list(), get(), and readiness() methods.

    Example:
        >>> catalog = ms.load()
        >>> catalog.list().show()
        >>> catalog.list("sales").show()
        >>> revenue = catalog.get("sales.revenue")
        >>> revenue.details().additivity

    Constraints:
        catalog is obtained via ms.load(), not constructed directly.
        SemanticCatalog objects do not expose internal IR instances.
    """

    def __init__(self, project: SemanticProject) -> None:
        self._project = project
        self._reg = project._registry

    def list(
        self,
        parent: SemanticRefInput | None = None,
        *,
        kind: SemanticKindInput | None = None,
    ) -> SemanticObjectList:
        """Browse the semantic hierarchy under the given parent ref.

        Args:
            parent: Full semantic ref of the parent to browse under.
                None returns top-level domains and datasources.
                A domain ref (e.g. "sales") returns entities and metrics.
                A dataset ref (e.g. "sales.orders") returns fields, time fields,
                relationships, and a filtered metric view.
            kind: Optional kind filter. Accepts SemanticKind values or strings
                such as "metric", "dimension". Raises an error on unsupported values.

        Returns:
            SemanticObjectList with .show(), .refs(), and .objects.

        Example:
            >>> catalog.list().show()
            >>> catalog.list("sales").show()
            >>> catalog.list("sales.orders", kind="metric").show()

        Constraints:
            Only full semantic refs are accepted as parents. Non-container refs
            (metric, field, time_field, relationship) raise an unsupported-parent error.
        """
        reg = self._reg
        assert reg is not None, "registry is None — project must be loaded"

        validated_kind = _validate_kind(kind) if kind is not None else None

        if parent is None:
            items = self._list_top_level(reg, validated_kind)
            return SemanticObjectList(
                items=tuple(items),
                parent_label=None,
                kind_filter=str(kind) if kind else None,
            )

        parent_str = _to_ref_str(parent)

        # Resolve parent kind from registry
        parent_kind = self._resolve_kind_of(parent_str, reg)
        if parent_kind is None:
            self._raise_not_found(parent_str)

        # Guard: only model, datasource, and dataset refs can be browsed
        if str(parent_kind) not in _BROWSABLE_PARENT_KINDS:
            _raise(
                ErrorKind.UNSUPPORTED_LIST_PARENT,
                f"Semantic object {parent_str!r} is a {parent_kind} and cannot be used as a "
                f"catalog list parent. Use catalog.get({parent_str!r}).details() to inspect dependencies.",
                cls=SemanticRuntimeError,
                refs=(parent_str,),
            )

        if parent_kind == SemanticKind.DOMAIN:
            items = self._list_under_model(parent_str, reg, validated_kind)
        elif parent_kind == SemanticKind.DATASOURCE:
            items = self._list_under_datasource(parent_str, reg, validated_kind)
        else:
            items = self._list_under_dataset(parent_str, reg, validated_kind)

        return SemanticObjectList(
            items=tuple(items),
            parent_label=parent_str,
            kind_filter=str(kind) if kind else None,
        )

    def _list_top_level(
        self,
        reg: Registry,
        kind_filter: SemanticKind | None,
    ) -> _ListOfSemanticObject:
        items: list[SemanticObject] = []
        if kind_filter is None or kind_filter == SemanticKind.DOMAIN:
            for model_ir in reg.models.values():
                items.append(_build_domain_object(model_ir, reg))
        if kind_filter is None or kind_filter == SemanticKind.DATASOURCE:
            datasource_irs = self._project._datasource_irs or tuple(reg.datasources.values())
            for ds_ir in datasource_irs:
                items.append(_build_datasource_object(ds_ir, reg))
        return items

    def _list_under_model(
        self,
        model_name: str,
        reg: Registry,
        kind_filter: SemanticKind | None,
    ) -> _ListOfSemanticObject:
        items: list[SemanticObject] = []
        if kind_filter is None or kind_filter == SemanticKind.ENTITY:
            for ds_ir in reg.datasets.values():
                if ds_ir.domain == model_name:
                    items.append(_build_entity_object(ds_ir, reg))
        if kind_filter is None or kind_filter == SemanticKind.METRIC:
            for m_ir in reg.metrics.values():
                if m_ir.domain == model_name:
                    items.append(_build_metric_object(m_ir, reg, self._project))
        return items

    def _list_under_datasource(
        self,
        datasource_ref: str,
        reg: Registry,
        kind_filter: SemanticKind | None,
    ) -> _ListOfSemanticObject:
        items: list[SemanticObject] = []
        if kind_filter is None or kind_filter == SemanticKind.ENTITY:
            for ds_ir in reg.datasets.values():
                if ds_ir.datasource == datasource_ref:
                    items.append(_build_entity_object(ds_ir, reg))
        return items

    def _list_under_dataset(
        self,
        dataset_ref: str,
        reg: Registry,
        kind_filter: SemanticKind | None,
    ) -> _ListOfSemanticObject:
        items: list[SemanticObject] = []
        if kind_filter is None or kind_filter == SemanticKind.DIMENSION:
            for f_ir in reg.fields.values():
                if f_ir.entity == dataset_ref and not f_ir.is_time_dimension:
                    items.append(_build_dimension_object(f_ir, reg))
        if kind_filter is None or kind_filter == SemanticKind.TIME_DIMENSION:
            for f_ir in reg.fields.values():
                if f_ir.entity == dataset_ref and f_ir.is_time_dimension:
                    items.append(_build_dimension_object(f_ir, reg))
        if kind_filter is None or kind_filter == SemanticKind.RELATIONSHIP:
            for r_ir in reg.relationships.values():
                if r_ir.from_entity == dataset_ref or r_ir.to_entity == dataset_ref:
                    items.append(_build_relationship_object(r_ir, reg))
        if kind_filter is None or kind_filter == SemanticKind.METRIC:
            seen: set[str] = set()
            for m_ir in reg.metrics.values():
                if dataset_ref in m_ir.entities and m_ir.semantic_id not in seen:
                    seen.add(m_ir.semantic_id)
                    items.append(_build_metric_object(m_ir, reg, self._project))
        return items

    def _resolve_kind_of(self, ref_str: str, reg: Registry) -> SemanticKind | None:
        if ref_str in reg.models:
            return SemanticKind.DOMAIN
        datasource_irs = self._project._datasource_irs or tuple(reg.datasources.values())
        for ds_ir in datasource_irs:
            if ds_ir.semantic_id == ref_str:
                return SemanticKind.DATASOURCE
        if ref_str in reg.datasets:
            return SemanticKind.ENTITY
        if ref_str in reg.fields:
            f = reg.fields[ref_str]
            return SemanticKind.TIME_DIMENSION if f.is_time_dimension else SemanticKind.DIMENSION
        if ref_str in reg.metrics:
            return SemanticKind.METRIC
        if ref_str in reg.relationships:
            return SemanticKind.RELATIONSHIP
        return None

    def _raise_not_found(self, ref_str: str) -> NoReturn:
        _raise(
            ErrorKind.NOT_FOUND,
            f"Semantic object {ref_str!r} was not found. "
            f"`catalog.get(...)` requires a full semantic ref such as 'sales.revenue'.\n"
            f"Use catalog.list().show(), catalog.list('<domain>').show(), and then\n"
            f"catalog.list('<domain.entity>').show() to browse object refs.",
            cls=SemanticRuntimeError,
            refs=(ref_str,),
        )

    def get(self, ref: SemanticRefInput) -> SemanticObject:
        """Retrieve a single semantic object by full ref.

        Args:
            ref: Full semantic ref string or SemanticRef (e.g. "sales.revenue").

        Returns:
            SemanticObject for the requested ref.

        Example:
            >>> revenue = catalog.get("sales.revenue")
            >>> revenue.details().additivity

        Constraints:
            Raises a typed not-found error when no object exists. Does not return None.
            Short names such as "revenue" raise the not-found error with browse guidance.
        """
        reg = self._reg
        assert reg is not None
        ref_str = _to_ref_str(ref)
        obj = self._get_object(ref_str, reg)
        if obj is None:
            self._raise_not_found(ref_str)
        return obj

    def _get_object(self, ref_str: str, reg: Registry) -> SemanticObject | None:
        if ref_str in reg.models:
            return _build_domain_object(reg.models[ref_str], reg)
        datasource_irs = self._project._datasource_irs or tuple(reg.datasources.values())
        for ds_ir in datasource_irs:
            if ds_ir.semantic_id == ref_str:
                return _build_datasource_object(ds_ir, reg)
        if ref_str in reg.datasets:
            return _build_entity_object(reg.datasets[ref_str], reg)
        if ref_str in reg.fields:
            return _build_dimension_object(reg.fields[ref_str], reg)
        if ref_str in reg.metrics:
            return _build_metric_object(reg.metrics[ref_str], reg, self._project)
        if ref_str in reg.relationships:
            return _build_relationship_object(reg.relationships[ref_str], reg)
        return None

    def readiness(
        self,
        refs: Sequence[SemanticRefInput] | None = None,
    ) -> ReadinessReport:
        """Run readiness closeout gate for the given semantic refs.

        Args:
            refs: Semantic refs to check. Resolves the full dependency closure
                for each ref. None checks all loaded objects.

        Returns:
            ReadinessReport indicating whether analysis handoff is safe.

        Example:
            >>> report = catalog.readiness(refs=[revenue.ref, region.ref])
            >>> if report.blocked:
            ...     report.show()
            ...     raise SystemExit

        Constraints:
            This is the required semantic gate before passing refs to analysis APIs.
        """
        str_refs = [_to_ref_str(r) for r in refs] if refs is not None else None
        return self._project.readiness(refs=str_refs)


def load(
    *,
    workspace_dir: str | Path | None = None,
) -> SemanticCatalog:
    """Load a semantic project and return a browseable SemanticCatalog.

    Args:
        workspace_dir: Path to the project root containing ``.marivo/``.
            Defaults to the current working directory when omitted.

    Returns:
        SemanticCatalog on success.

    Example:
        >>> import marivo.semantic as ms
        >>> catalog = ms.load()
        >>> catalog.list().show()

    Constraints:
        Raises a typed load error on failure. Does not return a partial catalog.
        Does not print to stdout.
    """
    from marivo.project import resolve_project_root
    from marivo.semantic.reader import SemanticProject

    if workspace_dir is None:
        workspace_dir = resolve_project_root()

    project = SemanticProject(workspace_dir=workspace_dir)
    result = project.load()
    if result.status != "ready":
        from marivo.semantic.errors import SemanticLoadFailed

        raise SemanticLoadFailed(result.errors)
    return SemanticCatalog(project)
