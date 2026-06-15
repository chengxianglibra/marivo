"""SemanticCatalog — unified agent-facing read surface for marivo.semantic.

Public entrypoint: ms.load() -> SemanticCatalog
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NoReturn

from marivo.datasource.ir import AiContextIR, DatasourceIR, DatasourceSourceLocation
from marivo.datasource.scan import ScanScope
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
from marivo.render import format_bounded_card, result_repr
from marivo.semantic.constraints import ConstraintId
from marivo.semantic.dtos import DatasetSource
from marivo.semantic.errors import ErrorKind, SemanticLoadFailed, SemanticRuntimeError, _raise
from marivo.semantic.ir import (
    DimensionIR,
    DimensionKind,
    DomainIR,
    EntityIR,
    EntityVersioningIR,
    MetricIR,
    ParityStatus,
    RelationshipIR,
    SampleIntervalIR,
    SnapshotVersioningIR,
    SourceLocation,
    SymbolKind,
    ValidityVersioningIR,
)
from marivo.semantic.parity import propagated_parity_status

if TYPE_CHECKING:
    from marivo.semantic.dtos import VerifyResult
    from marivo.semantic.reader import SemanticProject
    from marivo.semantic.readiness import ReadinessReport
    from marivo.semantic.resolver import SemanticResolver
    from marivo.semantic.validator import Registry

from marivo.semantic.reader import _suggest_ref_level

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
AiContextView = AiContextIR
SnapshotVersioning = SnapshotVersioningIR
ValidityVersioning = ValidityVersioningIR
DatasetVersioning = EntityVersioningIR


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


# ---------------------------------------------------------------------------
# Kind-specific details
# ---------------------------------------------------------------------------


def _render_details_card(
    *,
    identity: str,
    status: str | None = None,
    extra_lines: tuple[str, ...] = (),
) -> str:
    """Return a bounded plain-text details card without a trailing newline."""
    lines: list[str] = [identity]
    if status:
        lines.append(f"status: {status}")
    for line in extra_lines:
        lines.append(line)
    lines.append("available:")
    lines.append("- .show()")
    return "\n".join(lines)


@dataclass(frozen=True, repr=False)
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

    def _repr_identity(self) -> str:
        return f"DatasourceDetails ref={self.ref.ref}"

    def __repr__(self) -> str:
        return result_repr(self._repr_identity())

    def render(self) -> str:
        return _render_details_card(
            identity=self._repr_identity(),
            status=self.description,
            extra_lines=(f"backend_type: {self.backend_type}",),
        )

    def show(self) -> None:
        print(self.render())


@dataclass(frozen=True, repr=False)
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

    def _repr_identity(self) -> str:
        return f"DomainDetails ref={self.ref.ref}"

    def __repr__(self) -> str:
        return result_repr(self._repr_identity())

    def render(self) -> str:
        child_count = len(self.children)
        return _render_details_card(
            identity=self._repr_identity(),
            status=self.description,
            extra_lines=(f"children: {child_count} objects",),
        )

    def show(self) -> None:
        print(self.render())


@dataclass(frozen=True, repr=False)
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

    def _repr_identity(self) -> str:
        return f"EntityDetails ref={self.ref.ref}"

    def __repr__(self) -> str:
        return result_repr(self._repr_identity())

    def render(self) -> str:
        child_count = len(self.children)
        return _render_details_card(
            identity=self._repr_identity(),
            status=self.description,
            extra_lines=(
                f"datasource: {self.datasource.ref}",
                f"children: {child_count} objects",
            ),
        )

    def show(self) -> None:
        print(self.render())


@dataclass(frozen=True, repr=False)
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

    def _repr_identity(self) -> str:
        return f"DimensionDetails ref={self.ref.ref}"

    def __repr__(self) -> str:
        return result_repr(self._repr_identity())

    def render(self) -> str:
        return _render_details_card(
            identity=self._repr_identity(),
            status=self.description,
            extra_lines=(
                f"entity: {self.entity.ref}",
                f"dimension_kind: {self.dimension_kind}",
            ),
        )

    def show(self) -> None:
        print(self.render())


@dataclass(frozen=True, repr=False)
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
    sample_interval: SampleIntervalIR | None

    def _repr_identity(self) -> str:
        return f"TimeDimensionDetails ref={self.ref.ref}"

    def __repr__(self) -> str:
        return result_repr(self._repr_identity())

    def render(self) -> str:
        return _render_details_card(
            identity=self._repr_identity(),
            status=self.description,
            extra_lines=(
                f"entity: {self.entity.ref}",
                f"granularity: {self.granularity}",
                f"timezone: {self.timezone!r}",
            ),
        )

    def show(self) -> None:
        print(self.render())


@dataclass(frozen=True, repr=False)
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
    components: tuple[tuple[str, SemanticRef], ...]
    required_relationships: tuple[SemanticRef, ...]
    decomposition: Literal["sum", "ratio", "weighted_average"]
    additivity: Literal["additive", "semi_additive", "non_additive"] | None
    fanout_policy: Literal["block", "aggregate_then_join"]
    unit: str | None
    verification_mode: Literal["sql_parity"] | None
    parity_status: ParityStatus
    source_sql: str | None
    source_dialect: str | None
    python_symbol: str
    time_fold: str | None
    status_time_dimension: str | None

    def _repr_identity(self) -> str:
        return f"MetricDetails ref={self.ref.ref}"

    def __repr__(self) -> str:
        return result_repr(self._repr_identity())

    def render(self) -> str:
        extra = [f"decomposition: {self.decomposition}"]
        if self.additivity:
            extra.append(f"additivity: {self.additivity}")
        if self.is_derived:
            extra.append("is_derived: True")
        return _render_details_card(
            identity=self._repr_identity(),
            status=self.description,
            extra_lines=tuple(extra),
        )

    def show(self) -> None:
        print(self.render())


@dataclass(frozen=True, repr=False)
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

    def _repr_identity(self) -> str:
        return f"RelationshipDetails ref={self.ref.ref}"

    def __repr__(self) -> str:
        return result_repr(self._repr_identity())

    def render(self) -> str:
        return _render_details_card(
            identity=self._repr_identity(),
            status=self.description,
            extra_lines=(
                f"from: {self.from_entity.ref}",
                f"to: {self.to_entity.ref}",
            ),
        )

    def show(self) -> None:
        print(self.render())


SemanticObjectDetails = (
    DatasourceDetails
    | DomainDetails
    | EntityDetails
    | DimensionDetails
    | TimeDimensionDetails
    | MetricDetails
    | RelationshipDetails
)


@dataclass(frozen=True, repr=False)
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
        >>> revenue.children      # tuple[SemanticRef, ...]

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

    @property
    def children(self) -> tuple[SemanticRef, ...]:
        """Return the children refs for this object.

        Returns:
            Tuple of SemanticRef values for child objects. Non-container objects
            (metrics, dimensions, relationships) return an empty tuple.

        Example:
            >>> domain = catalog.get("sales")
            >>> domain.children  # (SemanticRef("sales.orders", ...), ...)

        Constraints:
            The returned refs are read-only; they cannot be used to modify
            the semantic model.
        """
        return self._details.children

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
            The returned object exposes stable catalog value views and shared
            immutable value types where the semantic and datasource layers
            already use the same representation.
        """
        return self._details

    def _repr_identity(self) -> str:
        return f"SemanticObject kind={self.kind} ref={self.ref.ref}"

    def render(self) -> str:
        """Return a bounded plain-text object card without a trailing newline."""
        return format_bounded_card(
            identity=self._repr_identity(),
            status=self.description,
            available=(".details()", ".show()"),
        )

    def __repr__(self) -> str:
        return result_repr(self._repr_identity())

    def show(self) -> None:
        """Print render() output and return None."""
        print(self.render())


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

    def _repr_identity(self) -> str:
        label = self._parent_label or "catalog"
        filter_note = f" kind={self._kind_filter}" if self._kind_filter else ""
        return f"SemanticObjectList parent={label}{filter_note} count={len(self._items)}"

    def __repr__(self) -> str:
        return result_repr(self._repr_identity())

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


def _normalize_location(loc: SourceLocation | DatasourceSourceLocation) -> SourceLocation:
    return SourceLocation(file=loc.file, line=loc.line)


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
        context=ds_ir.ai_context,
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
        context=ds_ir.ai_context,
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
        context=model_ir.ai_context,
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
        context=model_ir.ai_context,
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
    details = EntityDetails(
        ref=ref,
        kind=SemanticKind.ENTITY,
        name=ds_ir.name,
        domain=ds_ir.domain,
        description=ds_ir.description,
        context=ds_ir.ai_context,
        source_location=ds_ir.location,
        parents=(ds_ref,),
        children=children,
        dependents=metric_dependents,
        datasource=ds_ref,
        source=ds_ir.source,
        primary_key=ds_ir.primary_key,
        versioning=ds_ir.versioning,
    )
    return SemanticObject(
        ref=ref,
        kind=SemanticKind.ENTITY,
        name=ds_ir.name,
        domain=ds_ir.domain,
        description=ds_ir.description,
        context=ds_ir.ai_context,
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
            context=f_ir.ai_context,
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
            sample_interval=f_ir.sample_interval,
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
            context=f_ir.ai_context,
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
        context=f_ir.ai_context,
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
    components = tuple(
        (role, SemanticRef(ref=comp_ref, kind=SemanticKind.METRIC))
        for role, comp_ref in m_ir.decomposition.components.items()
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
        context=m_ir.ai_context,
        source_location=m_ir.location,
        parents=parents,
        children=(),
        dependents=dependents,
        entities=entity_refs,
        root_entity=root_entity_ref,
        is_derived=m_ir.is_derived,
        component_metrics=component_refs,
        components=components,
        required_relationships=required_rels,
        decomposition=m_ir.decomposition.kind,
        additivity=m_ir.additivity,
        fanout_policy=m_ir.fanout_policy,
        unit=m_ir.unit,
        verification_mode=m_ir.provenance.verification_mode,
        parity_status=parity_status,
        source_sql=m_ir.provenance.source_sql,
        source_dialect=m_ir.provenance.source_dialect,
        python_symbol=m_ir.python_symbol,
        time_fold=m_ir.time_fold.label() if m_ir.time_fold is not None else None,
        status_time_dimension=m_ir.status_time_dimension,
    )
    return SemanticObject(
        ref=ref,
        kind=SemanticKind.METRIC,
        name=m_ir.name,
        domain=m_ir.domain,
        description=m_ir.description,
        context=m_ir.ai_context,
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
        context=r_ir.ai_context,
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
        context=r_ir.ai_context,
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
        SemanticCatalog with list(), get(), preview(), readiness(), and
        verify_object() methods.

    Example:
        >>> catalog = ms.load()
        >>> catalog.list().show()
        >>> catalog.list("sales").show()
        >>> catalog.list(kind="metric").show()          # all metrics across domains
        >>> catalog.list(domain="sales", kind="metric").show()
        >>> revenue = catalog.get("sales.revenue")
        >>> revenue.details().additivity

    Constraints:
        catalog is obtained via ms.load(), not constructed directly.
        SemanticCatalog objects do not expose internal IR instances.
    """

    def __init__(self, project: SemanticProject) -> None:
        self._project = project
        self._reg = project._registry

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
        models: str | Sequence[str] | None = None,
    ) -> None:
        """Reload the semantic project from disk and refresh the catalog registry.

        Args:
            models: When specified, only those model directories are loaded.
                Pass a single model name as a string or a list of names.
                When omitted, the previously active filter (if any) is reused.

        Example:
            >>> catalog.load(models="sales")
            >>> catalog.load(models=["sales", "inventory"])
        """
        if isinstance(models, str):
            models = [models]
        resolved = (
            models
            if models is not None
            else (
                list(self._project._filtered_domains) if self._project._filtered_domains else None
            )
        )
        result = self._project.load(models=resolved)
        self._reg = self._project._registry
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

    def list(
        self,
        parent: SemanticRefInput | None = None,
        *,
        kind: SemanticKindInput | None = None,
        domain: str | None = None,
    ) -> SemanticObjectList:
        """Browse the semantic hierarchy under the given parent ref.

        Args:
            parent: Full semantic ref of the parent to browse under.
                None returns top-level domains and datasources.
                A domain ref (e.g. "sales") returns entities, metrics, and
                relationships.
                A dataset ref (e.g. "sales.orders") returns fields, time fields,
                relationships, and a filtered metric view.
            kind: Optional kind filter. Accepts SemanticKind values or strings
                such as "metric", "dimension". Raises an error on unsupported values.
                At the top level (no parent, no domain), leaf kinds such as
                "metric" search across all domains.
            domain: Optional domain name to scope results. Equivalent to using
                ``parent`` with a domain ref, but can be combined with ``kind``
                for filtered domain-level browsing. Mutually exclusive with
                ``parent``.

        Returns:
            SemanticObjectList with .show(), .refs(), and .objects.

        Example:
            >>> catalog.list().show()
            >>> catalog.list("sales").show()
            >>> catalog.list("sales.orders", kind="metric").show()
            >>> catalog.list(kind="metric").show()             # all metrics
            >>> catalog.list(domain="sales", kind="metric").show()  # metrics in one domain

        Constraints:
            Only full semantic refs are accepted as parents. Non-container refs
            (metric, field, time_field, relationship) raise an unsupported-parent error.
            ``parent`` and ``domain`` are mutually exclusive.
        """
        reg = self._require_ready()

        if parent is not None and domain is not None:
            _raise(
                ErrorKind.CONFLICTING_PARAMETERS,
                "catalog.list() 'parent' and 'domain' are mutually exclusive. "
                "Use catalog.list(domain=...) with an optional kind= filter, "
                "or catalog.list(parent=...) for hierarchy browsing.",
                cls=SemanticRuntimeError,
                constraint_id=ConstraintId.CATALOG_PARAMETERS_COMPATIBLE,
            )

        validated_kind = _validate_kind(kind) if kind is not None else None

        # Domain shortcut: scope to a single domain
        if domain is not None:
            if domain not in reg.models:
                available = sorted(reg.models.keys())
                _raise(
                    ErrorKind.NOT_FOUND,
                    f"Domain {domain!r} was not found. Available domains: {available}.",
                    cls=SemanticRuntimeError,
                    refs=(domain,),
                )
            items = self._list_under_model(domain, reg, validated_kind)
            return SemanticObjectList(
                items=tuple(items),
                parent_label=domain,
                kind_filter=str(kind) if kind else None,
            )

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
        if kind_filter == SemanticKind.ENTITY:
            for entity_ir in reg.datasets.values():
                items.append(_build_entity_object(entity_ir, reg))
        if kind_filter == SemanticKind.DIMENSION:
            for f_ir in reg.fields.values():
                if not f_ir.is_time_dimension:
                    items.append(_build_dimension_object(f_ir, reg))
        if kind_filter == SemanticKind.TIME_DIMENSION:
            for f_ir in reg.fields.values():
                if f_ir.is_time_dimension:
                    items.append(_build_dimension_object(f_ir, reg))
        if kind_filter == SemanticKind.METRIC:
            for m_ir in reg.metrics.values():
                items.append(_build_metric_object(m_ir, reg, self._project))
        if kind_filter == SemanticKind.RELATIONSHIP:
            for r_ir in reg.relationships.values():
                items.append(_build_relationship_object(r_ir, reg))
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
        if kind_filter is None or kind_filter == SemanticKind.RELATIONSHIP:
            for r_ir in reg.relationships.values():
                if r_ir.domain == model_name:
                    items.append(_build_relationship_object(r_ir, reg))
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
        reg = self._reg
        suggestion = _suggest_ref_level(reg, ref_str) if reg is not None else None
        if suggestion is not None:
            message = f"Semantic object {ref_str!r} was not found. {suggestion}"
        else:
            message = (
                f"Semantic object {ref_str!r} was not found. "
                f"`catalog.get(...)` requires a full semantic ref such as 'sales.revenue'.\n"
                f"Use catalog.list().show(), catalog.list('<domain>').show(), and then\n"
                f"catalog.list('<domain.entity>').show() to browse object refs."
            )
        _raise(
            ErrorKind.NOT_FOUND,
            message,
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
        reg = self._require_ready()
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
        """Run structural readiness check for the given semantic refs.

        Performs pure in-memory checks without datasource connectivity.
        For runtime validation, use ``catalog.preview(...)``,
        ``project.parity_check(...)``, and ``project.richness()``.

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
        self._require_ready()
        str_refs = [_to_ref_str(r) for r in refs] if refs is not None else None
        return self._project.readiness(refs=str_refs)

    def verify_object(
        self,
        ref: SemanticRefInput,
        *,
        scope: ScanScope | None = None,
    ) -> VerifyResult:
        """Verify a single authored semantic object is reachable and valid.

        Automatically reloads the catalog from disk so that newly authored
        objects are visible without a separate ``catalog.load()`` call.

        For domains, relationships, and dimensions this is a static-only check.
        For entities, a scoped preview confirms the datasource is reachable and
        the expression is valid. For time dimensions, metrics, and derived
        metrics, the check is static and auto-records a decision into the
        evidence ledger (``time_dimension_identity`` or ``metric_decomposition``
        respectively).

        Args:
            ref: Full semantic ref string or SemanticRef to verify.
            scope: Scan scope controlling partition, max rows, and timeout.
                Defaults to ``ScanScope()``.

        Returns:
            VerifyResult with status, issues, and optional scan report.

        Example:
            >>> result = catalog.verify_object("sales.orders")
            >>> if result.status == "failed":
            ...     result.show()

        Constraints:
            ``verify_object`` is enforced by the authoring ladder: prepare APIs
            for dimensions, time dimensions, metrics, relationships, and
            cross-entity metrics raise ``LadderOrderError`` if the entity has
            not passed verification.
        """
        with contextlib.suppress(SemanticLoadFailed):
            # Project failed to load; let _project.verify_object handle it
            # so we get a proper VerifyResult with the real load errors
            # instead of an unhandled exception.
            self.load()
        ref_str = _to_ref_str(ref)
        result = self._project.verify_object(ref_str, scope=scope)
        self._reg = self._project._registry
        return result

    def _resolver(
        self,
        *,
        connections: object | None = None,
        sample_size: int | None = None,
    ) -> SemanticResolver:
        """Return an internal resolver backed by Materializer."""
        self._require_ready()
        if connections is None:
            connections = self._project._connection_service()
        from marivo.semantic.resolver import SemanticResolver

        return SemanticResolver(self, connections=connections, sample_size=sample_size)

    def preview(
        self,
        ref: SemanticRefInput,
        *,
        limit: int = PREVIEW_DEFAULT_LIMIT,
        include_types: bool = True,
        context_columns: Iterable[str] | None = None,
    ) -> PreviewResult:
        """Return a bounded preview for an entity, dimension, time dimension, or metric.

        Args:
            ref: Full semantic ref string or SemanticRef to preview.
            limit: Maximum number of preview rows to return.
            include_types: Whether to include backend schema type strings.
            context_columns: Optional parent-entity columns to include before a
                dimension or time-dimension preview value.

        Returns:
            PreviewResult with bounded rows, display columns, warnings, and
            sample policy metadata.

        Example:
            >>> catalog.preview("sales.orders.region", context_columns=("order_id",))
            >>> catalog.preview("sales.revenue").warnings

        Constraints:
            ``context_columns`` is valid only for dimension and time-dimension
            refs. Metric previews use the existing approximate pre-aggregate
            sample behavior.
        """
        reg = self._require_ready()
        ref_str = _to_ref_str(ref)
        kind = self._resolve_kind_of(ref_str, reg)
        if kind is None:
            self._raise_not_found(ref_str)
        resolver = self._resolver(
            sample_size=METRIC_PREVIEW_SAMPLE_SIZE if kind == SemanticKind.METRIC else None
        )
        if kind == SemanticKind.ENTITY:
            if context_columns is not None:
                _raise(
                    ErrorKind.MATERIALIZE_FAILED,
                    "catalog.preview(..., context_columns=...) is only valid for dimension refs.",
                    cls=SemanticRuntimeError,
                    refs=(ref_str,),
                )
            preview_limit = validate_preview_limit(limit)
            table = resolver.table(SemanticRef(ref_str, kind=SemanticKind.ENTITY))
            return preview_ibis_table(
                table,
                kind="semantic_dataset",
                ref=ref_str,
                limit=preview_limit,
                sample_policy=PreviewSamplePolicy(method="bounded_limit", limit=preview_limit),
                include_types=include_types,
            )
        if kind in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION}:
            preview_limit = validate_preview_limit(limit)
            field_ir = reg.fields[ref_str]
            parent_table = resolver.table(SemanticRef(field_ir.entity, kind=SemanticKind.ENTITY))
            field_value = resolver.dimension(SemanticRef(ref_str, kind=kind))
            field_column_name = ref_str.rsplit(".", 1)[-1]
            if context_columns is None:
                selected_context = tuple(
                    column for column in parent_table.columns if column != field_column_name
                )[:3]
            else:
                selected_context = tuple(context_columns)
            missing_context = [
                column for column in selected_context if column not in parent_table.columns
            ]
            if missing_context:
                _raise(
                    ErrorKind.MATERIALIZE_FAILED,
                    f"Field preview context columns are not present on parent dataset: {missing_context}",
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
            )
        if kind == SemanticKind.METRIC:
            if context_columns is not None:
                _raise(
                    ErrorKind.MATERIALIZE_FAILED,
                    "catalog.preview(..., context_columns=...) is only valid for dimension refs.",
                    cls=SemanticRuntimeError,
                    refs=(ref_str,),
                )
            preview_limit = validate_preview_limit(limit)
            metric_value = resolver.metric(SemanticRef(ref_str, kind=SemanticKind.METRIC))
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
                warnings=(
                    *result.warnings,
                    PreviewWarning(
                        kind="approximate_preview",
                        message=f"metric computed on {METRIC_PREVIEW_SAMPLE_SIZE} row sample, result is approximate",
                    ),
                ),
                sample_policy=result.sample_policy,
            )
        _raise(
            ErrorKind.MATERIALIZE_FAILED,
            f"catalog.preview() does not support {kind} refs.",
            cls=SemanticRuntimeError,
            refs=(ref_str,),
            details={"kind": str(kind)},
        )


def load(
    *,
    workspace_dir: str | Path | None = None,
    models: str | Sequence[str] | None = None,
) -> SemanticCatalog:
    """Load a semantic project and return a browseable SemanticCatalog.

    Args:
        workspace_dir: Path to the project root containing ``marivo.toml``.
            Defaults to the current working directory when omitted.
        models: When specified, only those model directories are loaded.
            Pass a single model name as a string or a list of names.
            Cross-model references to filtered-out models produce warnings
            instead of errors, so the registry remains usable.

    Returns:
        SemanticCatalog on success.

    Example:
        >>> import marivo.semantic as ms
        >>> catalog = ms.load()
        >>> catalog.list().show()
        >>> catalog = ms.load(models=["sales"])
        >>> catalog.list().show()

    Constraints:
        Raises a typed load error on failure. Does not return a partial catalog.
        Does not print to stdout.
    """
    import os

    from marivo.semantic.reader import SemanticProject

    if workspace_dir is None:
        env = os.environ.get("MARIVO_PROJECT_ROOT")
        workspace_dir = env if env else Path.cwd()

    project = SemanticProject(workspace_dir=workspace_dir)
    result = project.load(models=models)
    if result.status != "ready":
        from marivo.semantic.errors import SemanticLoadFailed

        raise SemanticLoadFailed(result.errors)
    return SemanticCatalog(project)
