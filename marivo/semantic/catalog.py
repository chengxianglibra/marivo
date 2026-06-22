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
from marivo.refs import SemanticRef
from marivo.render import format_bounded_card, result_repr
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
from marivo.semantic.refs import as_ref_id, make_ref

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
    "DatasourceDetails",
    "DerivedMetricDetails",
    "DimensionDetails",
    "DomainDetails",
    "EntityDetails",
    "EntityVersioning",
    "MeasureDetails",
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
    "SimpleMetricDetails",
    "SnapshotVersioning",
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


def _common_detail_lines(
    *,
    context: AiContextView,
    python_symbol: str,
    source_location: SourceLocation,
    parents: tuple[SemanticRef, ...],
    children: tuple[SemanticRef, ...],
    dependents: tuple[SemanticRef, ...],
) -> list[str]:
    lines = [
        f"business_definition: {context.business_definition or '(none)'}",
        "guardrails:",
    ]
    lines.extend(f"- {guardrail}" for guardrail in context.guardrails[:6])
    if not context.guardrails:
        lines.append("- (none)")
    if len(context.guardrails) > 6:
        lines.append(f"- ... (+{len(context.guardrails) - 6} more)")
    if context.synonyms:
        lines.append(f"synonyms: {_format_tuple_values(context.synonyms)}")
    if context.examples:
        lines.append("examples:")
        lines.extend(f"- {example}" for example in context.examples[:3])
        if len(context.examples) > 3:
            lines.append(f"- ... (+{len(context.examples) - 3} more)")
    if context.instructions:
        lines.append(f"instructions: {context.instructions}")
    if context.owner_notes:
        lines.append(f"owner_notes: {context.owner_notes}")
    lines.extend(
        (
            f"source_location: {_source_location_text(source_location)}",
            f"python_symbol: {python_symbol or '(none)'}",
            f"parents: {_format_refs(parents)}",
            f"children: {_format_refs(children)}",
            f"dependents: {_format_refs(dependents)}",
        )
    )
    return lines


@dataclass(frozen=True, repr=False)
class _DetailsBase:
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

    def __repr__(self) -> str:
        return result_repr(self._repr_identity())

    def render(self) -> str:
        raise NotImplementedError

    def show(self) -> None:
        print(self.render())


@dataclass(frozen=True, repr=False)
class DatasourceDetails(_DetailsBase):
    """Details for a datasource object."""

    backend_type: str
    fields: dict[str, object]
    env_refs: dict[str, str]

    def render(self) -> str:
        extra = _common_detail_lines(
            context=self.context,
            python_symbol=self.python_symbol,
            source_location=self.source_location,
            parents=self.parents,
            children=self.children,
            dependents=self.dependents,
        )
        extra.extend(
            (
                f"backend_type: {self.backend_type}",
                f"fields: {_format_mapping(self.fields)}",
                f"env_refs: {_format_mapping(self.env_refs)}",
            )
        )
        return _render_details_card(
            identity=self._repr_identity(),
            status=self.context.business_definition,
            extra_lines=tuple(extra),
        )


@dataclass(frozen=True, repr=False)
class DomainDetails(_DetailsBase):
    """Details for a domain object."""

    default: bool

    def render(self) -> str:
        extra = _common_detail_lines(
            context=self.context,
            python_symbol=self.python_symbol,
            source_location=self.source_location,
            parents=self.parents,
            children=self.children,
            dependents=self.dependents,
        )
        extra.append(f"default: {self.default}")
        return _render_details_card(
            identity=self._repr_identity(),
            status=self.context.business_definition,
            extra_lines=tuple(extra),
        )


@dataclass(frozen=True, repr=False)
class EntityDetails(_DetailsBase):
    """Details for an entity object."""

    datasource: SemanticRef
    source: DatasetSource
    primary_key: tuple[str, ...]
    versioning: EntityVersioning | None

    def render(self) -> str:
        extra = _common_detail_lines(
            context=self.context,
            python_symbol=self.python_symbol,
            source_location=self.source_location,
            parents=self.parents,
            children=self.children,
            dependents=self.dependents,
        )
        extra.extend(
            (
                f"datasource: {self.datasource.id}",
                f"source: {_source_text(self.source)}",
                f"primary_key: {_format_tuple_values(self.primary_key)}",
                f"versioning: {_versioning_text(self.versioning)}",
            )
        )
        return _render_details_card(
            identity=self._repr_identity(),
            status=self.context.business_definition,
            extra_lines=tuple(extra),
        )


@dataclass(frozen=True, repr=False)
class DimensionDetails(_DetailsBase):
    """Details for a categorical dimension object."""

    entity: SemanticRef

    def render(self) -> str:
        extra = _common_detail_lines(
            context=self.context,
            python_symbol=self.python_symbol,
            source_location=self.source_location,
            parents=self.parents,
            children=self.children,
            dependents=self.dependents,
        )
        extra.append(f"entity: {self.entity.id}")
        return _render_details_card(
            identity=self._repr_identity(),
            status=self.context.business_definition,
            extra_lines=tuple(extra),
        )


@dataclass(frozen=True, repr=False)
class MeasureDetails(_DetailsBase):
    """Details for a row-level quantitative measure object."""

    entity: SemanticRef
    additivity: Literal["additive", "semi_additive", "non_additive"]
    unit: str | None

    def render(self) -> str:
        extra = _common_detail_lines(
            context=self.context,
            python_symbol=self.python_symbol,
            source_location=self.source_location,
            parents=self.parents,
            children=self.children,
            dependents=self.dependents,
        )
        extra.extend((f"entity: {self.entity.id}", f"additivity: {self.additivity}"))
        if self.unit:
            extra.append(f"unit: {self.unit}")
        return _render_details_card(
            identity=self._repr_identity(),
            status=self.context.business_definition,
            extra_lines=tuple(extra),
        )


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

    def render(self) -> str:
        extra = _common_detail_lines(
            context=self.context,
            python_symbol=self.python_symbol,
            source_location=self.source_location,
            parents=self.parents,
            children=self.children,
            dependents=self.dependents,
        )
        parse_kind_display = self.parse_kind or "(inferred)"
        extra.extend(
            (
                f"entity: {self.entity.id}",
                f"parse_kind: {parse_kind_display}",
                f"granularity: {self.granularity}",
                f"format: {self.format!r}",
                f"timezone: {self.timezone!r}",
                f"is_default: {self.is_default}",
                f"sample_interval: {self.sample_interval.to_token() if self.sample_interval else '(none)'}",
            )
        )
        return _render_details_card(
            identity=self._repr_identity(),
            status=self.context.business_definition,
            extra_lines=tuple(extra),
        )


def _metric_common_lines(
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
) -> list[str]:
    """Render lines shared by all metric detail variants."""
    lines = [
        f"entities: {_format_refs(entities)}",
        f"root_entity: {_format_ref(root_entity)}",
        f"type: {metric_type}",
        f"additivity: {additivity}",
    ]
    if fold is not None:
        lines.append(f"fold: {fold} over {status_time_dimension}")
    lines.append(f"fanout_policy: {fanout_policy}")
    if unit:
        lines.append(f"unit: {unit}")
    lines.append(f"provenance: {_provenance_text(provenance)}")
    lines.append(f"parity_status: {parity_status}")
    return lines


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

    def render(self) -> str:
        extra = _common_detail_lines(
            context=self.context,
            python_symbol=self.python_symbol,
            source_location=self.source_location,
            parents=self.parents,
            children=self.children,
            dependents=self.dependents,
        )
        extra.extend(
            _metric_common_lines(
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
            extra.append(f"aggregation: {self.aggregation}")
        if self.measure is not None:
            extra.append(f"measure: {self.measure.id}")
        if self.aggregation_target is not None and self.aggregation_target_kind != "measure":
            extra.append(f"target: {self.aggregation_target_kind} {self.aggregation_target.id}")
        return _render_details_card(
            identity=self._repr_identity(),
            status=self.context.business_definition,
            extra_lines=tuple(extra),
        )


@dataclass(frozen=True, repr=False)
class DerivedMetricDetails(_DetailsBase):
    """Details for a derived (composed) metric.

    Derived metrics are declared with ``ms.ratio(...)``, ``ms.weighted_average(...)``,
    or ``ms.linear(...)``.  They always carry a composition kind and components;
    they never have aggregation or measure.
    """

    entities: tuple[SemanticRef, ...]
    root_entity: SemanticRef | None
    composition: Literal["ratio", "weighted_average", "linear"]
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

    def render(self) -> str:
        extra = _common_detail_lines(
            context=self.context,
            python_symbol=self.python_symbol,
            source_location=self.source_location,
            parents=self.parents,
            children=self.children,
            dependents=self.dependents,
        )
        extra.extend(
            _metric_common_lines(
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
        extra.append(f"composition: {self.composition}")
        if self.components:
            extra.append(
                "components: " + ", ".join(f"{role}={ref.id}" for role, ref in self.components)
            )
        if self.linear_terms:
            extra.append(
                "linear_terms: "
                + ", ".join(f"{sign}{metric}" for sign, metric in self.linear_terms)
            )
        if self.required_relationships:
            extra.append(f"required_relationships: {_format_refs(self.required_relationships)}")
        return _render_details_card(
            identity=self._repr_identity(),
            status=self.context.business_definition,
            extra_lines=tuple(extra),
        )


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

    def render(self) -> str:
        extra = _common_detail_lines(
            context=self.context,
            python_symbol=self.python_symbol,
            source_location=self.source_location,
            parents=self.parents,
            children=self.children,
            dependents=self.dependents,
        )
        extra.extend(
            (
                f"from: {self.from_entity.id}",
                f"to: {self.to_entity.id}",
                "join_keys: "
                + ", ".join(
                    f"{left}={right}"
                    for left, right in zip(self.from_keys, self.to_keys, strict=True)
                ),
            )
        )
        return _render_details_card(
            identity=self._repr_identity(),
            status=self.context.business_definition,
            extra_lines=tuple(extra),
        )


SemanticObjectDetails = (
    DatasourceDetails
    | DomainDetails
    | EntityDetails
    | DimensionDetails
    | MeasureDetails
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
        context: Business meaning, guardrails, and usage guidance from ai_context.
        source_location: Location in the user-authored semantic file.

    Returns:
        SemanticObject with all common fields and kind-specific detail via details().

    Example:
        >>> revenue = catalog.get("sales.revenue")
        >>> revenue.ref           # make_ref("sales.revenue", "metric")
        >>> revenue.context.business_definition
        >>> revenue.details().additivity
        >>> revenue.children      # tuple[SemanticRef, ...]

    Constraints:
        Business meaning and guardrails live under ``context``. Use
        ``catalog.list(parent=...)`` for hierarchy browsing — SemanticObject
        does not expose navigation methods.
    """

    ref: SemanticRef
    kind: SemanticKind
    name: str
    domain: str | None
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
            >>> d.components

        Constraints:
            The returned object exposes stable catalog value views and shared
            immutable value types where the semantic and datasource layers
            already use the same representation.
        """
        return self._details

    def _repr_identity(self) -> str:
        return f"SemanticObject kind={self.kind} ref={self.ref.id}"

    def render(self) -> str:
        """Return a bounded plain-text object card without a trailing newline."""
        return format_bounded_card(
            identity=self._repr_identity(),
            status=self.context.business_definition,
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
        return [obj.ref.id for obj in self._items]

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
            ref_str = obj.ref.id
            lines.append(f"  {kind_str:<12}{ref_str}")

        lines.append("")
        lines.append("next steps:")
        if self._items:
            first_ref = self._items[0].ref.id
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
    return as_ref_id(ref)


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
    ref = make_ref(ds_ir.semantic_id, SemanticKind.DATASOURCE)
    dependents = tuple(
        make_ref(d.semantic_id, SemanticKind.ENTITY)
        for d in reg.entities.values()
        if d.datasource == ds_ir.semantic_id
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
    return SemanticObject(
        ref=ref,
        kind=SemanticKind.DATASOURCE,
        name=ds_ir.name,
        domain=None,
        context=ds_ir.ai_context,
        source_location=_normalize_location(ds_ir.location),
        python_symbol=ds_ir.python_symbol,
        _details=details,
    )


def _build_domain_object(model_ir: DomainIR, reg: Registry) -> SemanticObject:
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
        default=model_ir.default,
    )
    return SemanticObject(
        ref=ref,
        kind=SemanticKind.DOMAIN,
        name=model_ir.name,
        domain=model_ir.name,
        context=model_ir.ai_context,
        source_location=model_ir.location,
        python_symbol="",
        _details=details,
    )


def _build_entity_object(ds_ir: EntityIR, reg: Registry) -> SemanticObject:
    ref = make_ref(ds_ir.semantic_id, SemanticKind.ENTITY)
    ds_ref = make_ref(ds_ir.datasource, SemanticKind.DATASOURCE)
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
    return SemanticObject(
        ref=ref,
        kind=SemanticKind.ENTITY,
        name=ds_ir.name,
        domain=ds_ir.domain,
        context=ds_ir.ai_context,
        source_location=ds_ir.location,
        python_symbol=ds_ir.python_symbol,
        _details=details,
    )


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


def _build_dimension_object(f_ir: DimensionIR, reg: Registry) -> SemanticObject:
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
        details: SemanticObjectDetails = TimeDimensionDetails(
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
    return SemanticObject(
        ref=ref,
        kind=kind,
        name=f_ir.name,
        domain=f_ir.domain,
        context=f_ir.ai_context,
        source_location=f_ir.location,
        python_symbol=f_ir.python_symbol,
        _details=details,
    )


def _build_measure_object(m_ir: MeasureIR, reg: Registry) -> SemanticObject:
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
    return SemanticObject(
        ref=ref,
        kind=SemanticKind.MEASURE,
        name=m_ir.name,
        domain=m_ir.domain,
        context=m_ir.ai_context,
        source_location=m_ir.location,
        python_symbol=m_ir.python_symbol,
        _details=details,
    )


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


def _build_metric_object(m_ir: MetricIR, reg: Registry, project: SemanticProject) -> SemanticObject:
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
    return SemanticObject(
        ref=ref,
        kind=SemanticKind.METRIC,
        name=m_ir.name,
        domain=m_ir.domain,
        context=m_ir.ai_context,
        source_location=m_ir.location,
        python_symbol=m_ir.python_symbol,
        _details=details,
    )


def _build_relationship_object(r_ir: RelationshipIR, reg: Registry) -> SemanticObject:
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
    return SemanticObject(
        ref=ref,
        kind=SemanticKind.RELATIONSHIP,
        name=r_ir.name,
        domain=r_ir.domain,
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
        domains: str | Sequence[str] | None = None,
    ) -> None:
        """Reload the semantic project from disk and refresh the catalog registry.

        Args:
            domains: When specified, only those domain directories are loaded.
                Pass a single domain name as a string or a list of names.
                When omitted, the previously active filter (if any) is reused.

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
                An entity ref (e.g. "sales.orders") returns dimensions, time dimensions,
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
            if domain not in reg.domains:
                available = sorted(reg.domains.keys())
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
            for model_ir in reg.domains.values():
                items.append(_build_domain_object(model_ir, reg))
        if kind_filter is None or kind_filter == SemanticKind.DATASOURCE:
            datasource_irs = self._project._datasource_irs or tuple(reg.datasources.values())
            for ds_ir in datasource_irs:
                items.append(_build_datasource_object(ds_ir, reg))
        if kind_filter == SemanticKind.ENTITY:
            for entity_ir in reg.entities.values():
                items.append(_build_entity_object(entity_ir, reg))
        if kind_filter == SemanticKind.DIMENSION:
            for f_ir in reg.dimensions.values():
                if not f_ir.is_time_dimension:
                    items.append(_build_dimension_object(f_ir, reg))
        if kind_filter == SemanticKind.TIME_DIMENSION:
            for f_ir in reg.dimensions.values():
                if f_ir.is_time_dimension:
                    items.append(_build_dimension_object(f_ir, reg))
        if kind_filter == SemanticKind.METRIC:
            for m_ir in reg.metrics.values():
                items.append(_build_metric_object(m_ir, reg, self._project))
        if kind_filter == SemanticKind.RELATIONSHIP:
            for r_ir in reg.relationships.values():
                items.append(_build_relationship_object(r_ir, reg))
        if kind_filter == SemanticKind.MEASURE:
            for meas_ir in reg.measures.values():
                items.append(_build_measure_object(meas_ir, reg))
        return items

    def _list_under_model(
        self,
        model_name: str,
        reg: Registry,
        kind_filter: SemanticKind | None,
    ) -> _ListOfSemanticObject:
        items: list[SemanticObject] = []
        if kind_filter is None or kind_filter == SemanticKind.ENTITY:
            for ds_ir in reg.entities.values():
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
        if kind_filter is None or kind_filter == SemanticKind.MEASURE:
            for meas_ir in reg.measures.values():
                if meas_ir.domain == model_name:
                    items.append(_build_measure_object(meas_ir, reg))
        return items

    def _list_under_datasource(
        self,
        datasource_ref: str,
        reg: Registry,
        kind_filter: SemanticKind | None,
    ) -> _ListOfSemanticObject:
        items: list[SemanticObject] = []
        if kind_filter is None or kind_filter == SemanticKind.ENTITY:
            for ds_ir in reg.entities.values():
                if ds_ir.datasource == datasource_ref:
                    items.append(_build_entity_object(ds_ir, reg))
        if kind_filter is None or kind_filter == SemanticKind.MEASURE:
            entity_ids_for_datasource = {
                e.semantic_id for e in reg.entities.values() if e.datasource == datasource_ref
            }
            for meas_ir in reg.measures.values():
                if meas_ir.entity in entity_ids_for_datasource:
                    items.append(_build_measure_object(meas_ir, reg))
        return items

    def _list_under_dataset(
        self,
        dataset_ref: str,
        reg: Registry,
        kind_filter: SemanticKind | None,
    ) -> _ListOfSemanticObject:
        items: list[SemanticObject] = []
        if kind_filter is None or kind_filter == SemanticKind.DIMENSION:
            for f_ir in reg.dimensions.values():
                if f_ir.entity == dataset_ref and not f_ir.is_time_dimension:
                    items.append(_build_dimension_object(f_ir, reg))
        if kind_filter is None or kind_filter == SemanticKind.TIME_DIMENSION:
            for f_ir in reg.dimensions.values():
                if f_ir.entity == dataset_ref and f_ir.is_time_dimension:
                    items.append(_build_dimension_object(f_ir, reg))
        if kind_filter is None or kind_filter == SemanticKind.MEASURE:
            for meas_ir in reg.measures.values():
                if meas_ir.entity == dataset_ref:
                    items.append(_build_measure_object(meas_ir, reg))
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
        if ref_str in reg.domains:
            return SemanticKind.DOMAIN
        datasource_irs = self._project._datasource_irs or tuple(reg.datasources.values())
        for ds_ir in datasource_irs:
            if ds_ir.semantic_id == ref_str:
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
        if ref_str in reg.domains:
            return _build_domain_object(reg.domains[ref_str], reg)
        datasource_irs = self._project._datasource_irs or tuple(reg.datasources.values())
        for ds_ir in datasource_irs:
            if ds_ir.semantic_id == ref_str:
                return _build_datasource_object(ds_ir, reg)
        if ref_str in reg.entities:
            return _build_entity_object(reg.entities[ref_str], reg)
        if ref_str in reg.dimensions:
            return _build_dimension_object(reg.dimensions[ref_str], reg)
        if ref_str in reg.measures:
            return _build_measure_object(reg.measures[ref_str], reg)
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
            >>> if report.status == "blocked":
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
        evidence ledger (``time_dimension_identity`` or ``metric_composition``
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
        """Return a bounded preview for an entity, dimension, time dimension, measure, or metric.

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
            >>> catalog.preview("sales.orders.amount")
            >>> catalog.preview("sales.revenue").warnings

        Constraints:
            ``context_columns`` is valid only for dimension and time-dimension
            refs. Measure previews show bounded row-level values. Metric previews
            use the existing approximate pre-aggregate sample behavior.
        """
        reg = self._require_ready()
        ref_str = _to_ref_str(ref)
        kind = self._resolve_kind_of(ref_str, reg)
        if kind is None:
            self._raise_not_found(ref_str)
        from marivo.datasource.timezone import system_timezone_name

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
            table = resolver.table(make_ref(ref_str, SemanticKind.ENTITY))
            report_tz = system_timezone_name()
            return preview_ibis_table(
                table,
                kind="semantic_dataset",
                ref=ref_str,
                limit=preview_limit,
                sample_policy=PreviewSamplePolicy(method="bounded_limit", limit=preview_limit),
                include_types=include_types,
                report_tz=report_tz,
            )
        if kind == SemanticKind.MEASURE:
            if context_columns is not None:
                _raise(
                    ErrorKind.MATERIALIZE_FAILED,
                    "catalog.preview(..., context_columns=...) is only valid for dimension refs.",
                    cls=SemanticRuntimeError,
                    refs=(ref_str,),
                )
            preview_limit = validate_preview_limit(limit)
            measure_ir = reg.measures[ref_str]
            parent_table = resolver.table(make_ref(measure_ir.entity, SemanticKind.ENTITY))
            measure_value = resolver.measure(make_ref(ref_str, SemanticKind.MEASURE))
            measure_column_name = ref_str.rsplit(".", 1)[-1]
            preview_table = parent_table.select(measure_value.name(measure_column_name))
            report_tz = system_timezone_name()
            return preview_ibis_table(
                preview_table,
                kind="semantic_measure",
                ref=ref_str,
                limit=preview_limit,
                sample_policy=PreviewSamplePolicy(method="bounded_limit", limit=preview_limit),
                include_types=include_types,
                report_tz=report_tz,
            )
        if kind in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION}:
            preview_limit = validate_preview_limit(limit)
            field_ir = reg.dimensions[ref_str]
            parent_table = resolver.table(make_ref(field_ir.entity, SemanticKind.ENTITY))
            field_value = resolver.dimension(make_ref(ref_str, kind))
            field_column_name = ref_str.rsplit(".", 1)[-1]
            report_tz = system_timezone_name()
            datasource_timezone = None
            if kind == SemanticKind.TIME_DIMENSION:
                entity_ir = reg.entities[field_ir.entity]
                connections = getattr(resolver, "connections", None)
                engine_tz_method = getattr(connections, "engine_timezone", None)
                if callable(engine_tz_method):
                    datasource_timezone = engine_tz_method(entity_ir.datasource)
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
                timezones=_preview_timezones_for_field(
                    column_name=field_column_name,
                    field_ir=field_ir,
                    datasource_timezone=datasource_timezone,
                    report_tz=report_tz,
                ),
                report_tz=report_tz,
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
    domains: str | Sequence[str] | None = None,
) -> SemanticCatalog:
    """Load a semantic project and return a browseable SemanticCatalog.

    Args:
        workspace_dir: Path to the project root containing ``marivo.toml``.
            Defaults to the current working directory when omitted.
        domains: When specified, only those domain directories are loaded.
            Pass a single domain name as a string or a list of names.
            Cross-domain references to filtered-out domains produce warnings
            instead of errors, so the registry remains usable.

    Returns:
        SemanticCatalog on success.

    Example:
        >>> import marivo.semantic as ms
        >>> catalog = ms.load()
        >>> catalog.list().show()
        >>> catalog = ms.load(domains=["sales"])
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
    result = project.load(domains=domains)
    if result.status != "ready":
        from marivo.semantic.errors import SemanticLoadFailed

        raise SemanticLoadFailed(result.errors)
    return SemanticCatalog(project)
