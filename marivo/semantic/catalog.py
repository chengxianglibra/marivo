"""SemanticCatalog — unified agent-facing read surface for marivo.semantic.

Public entrypoint: ms.load() -> SemanticCatalog
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar, Literal, NoReturn, cast, overload

from marivo.datasource.engines import require_profile_for_backend_type
from marivo.datasource.ir import AiContextIR, DatasourceIR, DatasourceSourceLocation
from marivo.datasource.runtime import DatasourceConnectionService
from marivo.datasource.source import AuthoringScope
from marivo.preview import (
    METRIC_PREVIEW_SAMPLE_SIZE,
    PREVIEW_DEFAULT_LIMIT,
    PreviewResult,
    PreviewSamplePolicy,
    PreviewWarning,
    preview_from_pandas,
    preview_ibis_table,
    validate_preview_limit,
)
from marivo.refs import (
    DatasourceKind,
    DimensionKind,
    DomainKind,
    EntityKind,
    FieldKind,
    MeasureKind,
    MetricKind,
    Ref,
    RelationshipKind,
    SemanticKind,
    SemanticKindTag,
    TimeDimensionKind,
)
from marivo.render import Card, FieldSection, ListSection, RenderableResult, Section
from marivo.semantic.constraints import ConstraintId
from marivo.semantic.dtos import DatasetSource, PreviewBatchResult
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
    RatioComposition,
    RelationshipIR,
    SampleIntervalIR,
    SemiAdditive,
    SnapshotVersioningIR,
    SourceLocation,
    SqlProvenance,
    StrptimeParse,
    TimestampParse,
    ValidityVersioningIR,
    WhereValue,
    additivity_bucket,
    composition_components,
)
from marivo.semantic.parity import propagated_parity_status
from marivo.semantic.preview_checks import (
    NormalizedPreviewBindings,
    PreviewUsing,
    normalize_preview_batch_bindings,
    normalize_preview_bindings,
    persist_preview_check,
)

if TYPE_CHECKING:
    from marivo._authoring.model import AuthoringContract
    from marivo.semantic._compiled_state import CompiledSemanticState
    from marivo.semantic.dtos import VerifyResult
    from marivo.semantic.reader import SemanticProject
    from marivo.semantic.readiness import ReadinessReport
    from marivo.semantic.resolver import SemanticResolver
    from marivo.semantic.validator import Registry

__all__ = [
    "AiContextView",
    "CatalogCollection",
    "CatalogEntry",
    "DatasourceDetails",
    "DatasourceEntry",
    "DerivedMetricDetails",
    "DimensionDetails",
    "DimensionEntry",
    "DomainDetails",
    "DomainEntry",
    "EntityDetails",
    "EntityEntry",
    "EntityVersioning",
    "MeasureDetails",
    "MeasureEntry",
    "MetricDetails",
    "MetricEntry",
    "RelationshipDetails",
    "RelationshipEntry",
    "SemanticCatalog",
    "SemanticKind",
    "SimpleMetricDetails",
    "SnapshotVersioning",
    "TimeDimensionDetails",
    "TimeDimensionEntry",
    "ValidityVersioning",
    "load",
]

AiContextView = AiContextIR
SnapshotVersioning = SnapshotVersioningIR
ValidityVersioning = ValidityVersioningIR
EntityVersioning = EntityVersioningIR


def _metric_preview_table(
    resolver: SemanticResolver,
    registry: Registry,
    ref: Ref[MetricKind],
    *,
    alias: str,
) -> Any:
    """Return one executable table for a scalar metric preview.

    Ibis cannot turn a scalar spanning independent base relations into a table
    directly.  Derived metrics already carry a closed composition, so lower
    their scalar components to one-row tables and combine those tables before
    applying the composition.
    """
    metric = registry.metrics[ref.path]
    composition = metric.composition
    if isinstance(composition, RatioComposition):
        numerator_ref = Ref.metric(composition.numerator)
        denominator_ref = Ref.metric(composition.denominator)
        numerator_alias = f"{alias}__numerator"
        denominator_alias = f"{alias}__denominator"
        numerator = _metric_preview_table(
            resolver,
            registry,
            numerator_ref,
            alias=numerator_alias,
        )
        denominator = _metric_preview_table(
            resolver,
            registry,
            denominator_ref,
            alias=denominator_alias,
        )
        combined = numerator.cross_join(denominator)
        return combined.select(
            (combined[numerator_alias] / combined[denominator_alias]).name(alias)
        )
    if isinstance(composition, LinearComposition):
        tables = []
        aliases = []
        for index, term in enumerate(composition.terms):
            term_alias = f"{alias}__term_{index}"
            aliases.append(term_alias)
            tables.append(
                _metric_preview_table(
                    resolver,
                    registry,
                    Ref.metric(term.metric),
                    alias=term_alias,
                )
            )
        combined = tables[0]
        for table in tables[1:]:
            combined = combined.cross_join(table)
        value = combined[aliases[0]]
        if composition.terms[0].sign == "-":
            value = -value
        for term, term_alias in zip(composition.terms[1:], aliases[1:], strict=True):
            value = (
                value + combined[term_alias] if term.sign == "+" else value - combined[term_alias]
            )
        return combined.select(value.name(alias))
    return resolver.metric(ref).name(alias).as_table()


@overload
def _make_ref(path: str, kind: Literal[SemanticKind.DOMAIN]) -> Ref[DomainKind]: ...


@overload
def _make_ref(path: str, kind: Literal[SemanticKind.DATASOURCE]) -> Ref[DatasourceKind]: ...


@overload
def _make_ref(path: str, kind: Literal[SemanticKind.ENTITY]) -> Ref[EntityKind]: ...


@overload
def _make_ref(path: str, kind: Literal[SemanticKind.DIMENSION]) -> Ref[DimensionKind]: ...


@overload
def _make_ref(path: str, kind: Literal[SemanticKind.TIME_DIMENSION]) -> Ref[TimeDimensionKind]: ...


@overload
def _make_ref(path: str, kind: Literal[SemanticKind.MEASURE]) -> Ref[MeasureKind]: ...


@overload
def _make_ref(path: str, kind: Literal[SemanticKind.METRIC]) -> Ref[MetricKind]: ...


@overload
def _make_ref(path: str, kind: Literal[SemanticKind.RELATIONSHIP]) -> Ref[RelationshipKind]: ...


@overload
def _make_ref(path: str, kind: SemanticKind) -> Ref[SemanticKindTag]: ...


def _make_ref(path: str, kind: SemanticKind) -> Ref[SemanticKindTag]:
    factory = {
        SemanticKind.DOMAIN: Ref.domain,
        SemanticKind.DATASOURCE: Ref.datasource,
        SemanticKind.ENTITY: Ref.entity,
        SemanticKind.DIMENSION: Ref.dimension,
        SemanticKind.TIME_DIMENSION: Ref.time_dimension,
        SemanticKind.MEASURE: Ref.measure,
        SemanticKind.METRIC: Ref.metric,
        SemanticKind.RELATIONSHIP: Ref.relationship,
    }[kind]
    return factory(path)


@dataclass(frozen=True)
class _BatchPreviewItem:
    order: int
    ref: Ref[SemanticKindTag]
    kind: SemanticKind
    bindings: NormalizedPreviewBindings


# ---------------------------------------------------------------------------
# Kind-specific details
# ---------------------------------------------------------------------------


def _source_location_text(source_location: SourceLocation) -> str:
    return f"{source_location.file}:{source_location.line}"


def _format_ref(ref: Ref[SemanticKindTag] | None) -> str:
    return ref.key if ref is not None else "(none)"


def _format_refs(refs: tuple[Ref[SemanticKindTag], ...], *, limit: int = 6) -> str:
    if not refs:
        return "(none)"
    visible = [ref.key for ref in refs[:limit]]
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


def _format_mapping(mapping: Mapping[str, object]) -> str:
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
    parents: tuple[Ref[SemanticKindTag], ...],
    children: tuple[Ref[SemanticKindTag], ...],
    dependents: tuple[Ref[SemanticKindTag], ...],
) -> list[Section]:
    sections: list[Section] = [
        FieldSection(label="business_definition", value=context.business_definition or "(none)"),
        ListSection(label="guardrails", items=tuple(context.guardrails) or ()),
    ]
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

    ref: Ref[SemanticKindTag]
    kind: SemanticKind
    name: str
    domain: str | None
    context: AiContextView
    source_location: SourceLocation
    parents: tuple[Ref[SemanticKindTag], ...]
    children: tuple[Ref[SemanticKindTag], ...]
    dependents: tuple[Ref[SemanticKindTag], ...]
    python_symbol: str

    def _repr_identity(self) -> str:
        return f"{self.__class__.__name__} ref={self.ref.key}"

    def _detail_sections(self) -> list[Section]:
        raise NotImplementedError

    def _card(self) -> Card:
        card = Card(identity=self._repr_identity(), available=(".show()",))
        for section in self._detail_sections():
            card = card.section(section)
        card = card.listing(
            label="suggested next calls",
            items=(
                f"catalog.verify(ms.Ref.{self.ref.kind.value}({self.ref.path!r}))",
                f"catalog.readiness(refs=[ms.Ref.{self.ref.kind.value}({self.ref.path!r})])",
            ),
        )
        return card


@dataclass(frozen=True, repr=False)
class DatasourceDetails(_DetailsBase):
    """Details for a datasource object."""

    backend_type: str
    fields: Mapping[str, object]
    env_refs: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", MappingProxyType(dict(self.fields)))
        object.__setattr__(self, "env_refs", MappingProxyType(dict(self.env_refs)))

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

    datasource: Ref[DatasourceKind]
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
                FieldSection(label="datasource", value=self.datasource.key),
                FieldSection(label="source", value=_source_text(self.source)),
                FieldSection(label="primary_key", value=_format_tuple_values(self.primary_key)),
                FieldSection(label="versioning", value=_versioning_text(self.versioning)),
            )
        )
        return sections


@dataclass(frozen=True, repr=False)
class DimensionDetails(_DetailsBase):
    """Details for a categorical dimension object."""

    entity: Ref[SemanticKindTag]

    def _detail_sections(self) -> list[Section]:
        sections = _common_detail_sections(
            context=self.context,
            python_symbol=self.python_symbol,
            source_location=self.source_location,
            parents=self.parents,
            children=self.children,
            dependents=self.dependents,
        )
        sections.append(FieldSection(label="entity", value=self.entity.key))
        return sections


@dataclass(frozen=True, repr=False)
class MeasureDetails(_DetailsBase):
    """Details for a row-level quantitative measure object."""

    entity: Ref[SemanticKindTag]
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
                FieldSection(label="entity", value=self.entity.key),
                FieldSection(label="additivity", value=self.additivity),
            )
        )
        if self.unit:
            sections.append(FieldSection(label="unit", value=self.unit))
        return sections


@dataclass(frozen=True, repr=False)
class TimeDimensionDetails(_DetailsBase):
    """Details for a time dimension object."""

    entity: Ref[SemanticKindTag]
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
                FieldSection(label="entity", value=self.entity.key),
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
    entities: tuple[Ref[SemanticKindTag], ...],
    effective_entities: tuple[Ref[SemanticKindTag], ...],
    candidate_dimensions: tuple[Ref[SemanticKindTag], ...],
    candidate_time_dimensions: tuple[Ref[SemanticKindTag], ...],
    measure_lineage: tuple[tuple[str, Ref[SemanticKindTag]], ...],
    root_entity: Ref[SemanticKindTag] | None,
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
        FieldSection(label="effective_entities", value=_format_refs(effective_entities)),
        FieldSection(label="candidate_dimensions", value=_format_refs(candidate_dimensions)),
        FieldSection(
            label="candidate_time_dimensions",
            value=_format_refs(candidate_time_dimensions),
        ),
        FieldSection(label="root_entity", value=_format_ref(root_entity)),
        FieldSection(label="type", value=metric_type),
        FieldSection(label="additivity", value=additivity),
    ]
    if measure_lineage:
        sections.append(
            FieldSection(
                label="measure_lineage",
                value=", ".join(f"{role}={ref.key}" for role, ref in measure_lineage),
            )
        )
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

    entities: tuple[Ref[SemanticKindTag], ...]
    root_entity: Ref[SemanticKindTag] | None
    aggregation: str | None
    measure: Ref[SemanticKindTag] | None
    additivity: Literal["additive", "semi_additive", "non_additive"]
    fold: str | None
    status_time_dimension: str | None
    fanout_policy: Literal["block", "aggregate_then_join"]
    unit: str | None
    provenance: SqlProvenance | None
    parity_status: ParityStatus
    aggregation_target: Ref[SemanticKindTag] | None = None
    aggregation_target_kind: Literal["measure", "entity"] | None = None
    filter: tuple[tuple[str, WhereValue], ...] | None = None
    effective_entities: tuple[Ref[SemanticKindTag], ...] = ()
    candidate_dimensions: tuple[Ref[SemanticKindTag], ...] = ()
    candidate_time_dimensions: tuple[Ref[SemanticKindTag], ...] = ()
    measure_lineage: tuple[tuple[str, Ref[SemanticKindTag]], ...] = ()
    weighted_mean_value: Ref[SemanticKindTag] | None = None
    weighted_mean_weight: Ref[SemanticKindTag] | None = None

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
                effective_entities=self.effective_entities,
                candidate_dimensions=self.candidate_dimensions,
                candidate_time_dimensions=self.candidate_time_dimensions,
                measure_lineage=self.measure_lineage,
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
            sections.append(FieldSection(label="measure", value=self.measure.key))
        if self.weighted_mean_value is not None and self.weighted_mean_weight is not None:
            sections.append(
                FieldSection(
                    label="inputs",
                    value=(
                        f"value={self.weighted_mean_value.key}, "
                        f"weight={self.weighted_mean_weight.key}"
                    ),
                )
            )
        if self.aggregation_target is not None and self.aggregation_target_kind != "measure":
            sections.append(
                FieldSection(
                    label="target",
                    value=f"{self.aggregation_target_kind} {self.aggregation_target.key}",
                )
            )
        if self.filter:
            sections.append(
                FieldSection(
                    label="filter",
                    value=", ".join(f"{column}={value}" for column, value in self.filter),
                )
            )
        return sections


@dataclass(frozen=True, repr=False)
class DerivedMetricDetails(_DetailsBase):
    """Details for a derived (composed) metric.

    Derived metrics are declared with ``ms.ratio(...)``, ``ms.cumulative(...)``,
    or ``ms.linear(...)``. They always carry a composition kind and components;
    they never have aggregation or measure.
    """

    entities: tuple[Ref[SemanticKindTag], ...]
    root_entity: Ref[SemanticKindTag] | None
    composition: Literal["ratio", "linear", "cumulative"]
    components: tuple[tuple[str, Ref[SemanticKindTag]], ...]
    linear_terms: tuple[tuple[str, str], ...]
    required_relationships: tuple[Ref[SemanticKindTag], ...]
    additivity: Literal["additive", "semi_additive", "non_additive"]
    fold: str | None
    status_time_dimension: str | None
    fanout_policy: Literal["block", "aggregate_then_join"]
    unit: str | None
    provenance: SqlProvenance | None
    parity_status: ParityStatus
    effective_entities: tuple[Ref[SemanticKindTag], ...] = ()
    candidate_dimensions: tuple[Ref[SemanticKindTag], ...] = ()
    candidate_time_dimensions: tuple[Ref[SemanticKindTag], ...] = ()
    measure_lineage: tuple[tuple[str, Ref[SemanticKindTag]], ...] = ()

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
                effective_entities=self.effective_entities,
                candidate_dimensions=self.candidate_dimensions,
                candidate_time_dimensions=self.candidate_time_dimensions,
                measure_lineage=self.measure_lineage,
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
                    value=", ".join(f"{role}={ref.key}" for role, ref in self.components),
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

    from_entity: Ref[SemanticKindTag]
    to_entity: Ref[SemanticKindTag]
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
                FieldSection(label="from", value=self.from_entity.key),
                FieldSection(label="to", value=self.to_entity.key),
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
class CatalogEntry[KindT: SemanticKindTag](RenderableResult):
    """One immutable browsable object in one compiled semantic catalog."""

    ref: Ref[KindT]
    _details: _CatalogObjectDetails
    _catalog: SemanticCatalog

    _navigation_names: ClassVar[tuple[str, ...]] = ()

    @property
    def kind(self) -> SemanticKind:
        return self.ref.kind

    @property
    def path(self) -> str:
        return self.ref.path

    @property
    def key(self) -> str:
        return self.ref.key

    @property
    def name(self) -> str:
        return self.ref.name

    def details(self) -> _CatalogObjectDetails:
        return self._details

    def __eq__(self, other: object) -> bool:
        return (
            type(self) is type(other)
            and isinstance(other, CatalogEntry)
            and self.ref == other.ref
            and self._catalog is other._catalog
        )

    def __hash__(self) -> int:
        return hash((type(self), id(self._catalog), self.ref))

    def _repr_identity(self) -> str:
        return f"{type(self).__name__} {self.ref.key}"

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
            collection = cast(
                "CatalogCollection[SemanticKindTag]",
                getattr(self, name),
            )
            card = card.field(label=name, value=f"{len(collection)} -> .{name}")
        return card

    def contract(self) -> AuthoringContract:
        """Return the mechanical continuation contract for this catalog object.

        The contract exposes verify, preview (for executable kinds), and
        readiness transitions scoped to this object's ref.
        """
        from marivo.semantic._capabilities.contracts import contract_for_catalog_object

        return contract_for_catalog_object(self.ref.path, self.ref.kind.value)


class DomainEntry(CatalogEntry[DomainKind]):
    """Loaded semantic domain with typed child collections."""

    ref: Ref[DomainKind]
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
    def entities(self) -> CatalogCollection[EntityKind]:
        return self._catalog._collection(EntityEntry, SemanticKind.ENTITY, scope_ref=self.ref)

    @property
    def dimensions(self) -> CatalogCollection[DimensionKind]:
        return self._catalog._collection(
            DimensionEntry,
            SemanticKind.DIMENSION,
            scope_ref=self.ref,
        )

    @property
    def time_dimensions(self) -> CatalogCollection[TimeDimensionKind]:
        return self._catalog._collection(
            TimeDimensionEntry,
            SemanticKind.TIME_DIMENSION,
            scope_ref=self.ref,
        )

    @property
    def measures(self) -> CatalogCollection[MeasureKind]:
        return self._catalog._collection(MeasureEntry, SemanticKind.MEASURE, scope_ref=self.ref)

    @property
    def metrics(self) -> CatalogCollection[MetricKind]:
        return self._catalog._collection(MetricEntry, SemanticKind.METRIC, scope_ref=self.ref)

    @property
    def relationships(self) -> CatalogCollection[RelationshipKind]:
        return self._catalog._collection(
            RelationshipEntry,
            SemanticKind.RELATIONSHIP,
            scope_ref=self.ref,
        )


class DatasourceEntry(CatalogEntry[DatasourceKind]):
    """Loaded datasource with the entities it backs."""

    ref: Ref[DatasourceKind]
    _navigation_names = ("entities",)

    def details(self) -> DatasourceDetails:
        return cast("DatasourceDetails", self._details)

    @property
    def entities(self) -> CatalogCollection[EntityKind]:
        return self._catalog._collection(EntityEntry, SemanticKind.ENTITY, scope_ref=self.ref)


class EntityEntry(CatalogEntry[EntityKind]):
    """Loaded semantic entity with applicable semantic collections."""

    ref: Ref[EntityKind]
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
    def dimensions(self) -> CatalogCollection[DimensionKind]:
        return self._catalog._collection(
            DimensionEntry,
            SemanticKind.DIMENSION,
            scope_ref=self.ref,
        )

    @property
    def time_dimensions(self) -> CatalogCollection[TimeDimensionKind]:
        return self._catalog._collection(
            TimeDimensionEntry,
            SemanticKind.TIME_DIMENSION,
            scope_ref=self.ref,
        )

    @property
    def measures(self) -> CatalogCollection[MeasureKind]:
        return self._catalog._collection(MeasureEntry, SemanticKind.MEASURE, scope_ref=self.ref)

    @property
    def metrics(self) -> CatalogCollection[MetricKind]:
        return self._catalog._collection(MetricEntry, SemanticKind.METRIC, scope_ref=self.ref)

    @property
    def relationships(self) -> CatalogCollection[RelationshipKind]:
        return self._catalog._collection(
            RelationshipEntry,
            SemanticKind.RELATIONSHIP,
            scope_ref=self.ref,
        )


class DimensionEntry(CatalogEntry[DimensionKind]):
    """Loaded categorical dimension."""

    ref: Ref[DimensionKind]

    def details(self) -> DimensionDetails:
        return cast("DimensionDetails", self._details)


class TimeDimensionEntry(CatalogEntry[TimeDimensionKind]):
    """Loaded time dimension."""

    ref: Ref[TimeDimensionKind]

    def details(self) -> TimeDimensionDetails:
        return cast("TimeDimensionDetails", self._details)


class MeasureEntry(CatalogEntry[MeasureKind]):
    """Loaded entity-owned quantitative measure."""

    ref: Ref[MeasureKind]

    def details(self) -> MeasureDetails:
        return cast("MeasureDetails", self._details)


class MetricEntry(CatalogEntry[MetricKind]):
    """Loaded analysis-ready metric."""

    ref: Ref[MetricKind]

    def details(self) -> MetricDetails:
        return cast("MetricDetails", self._details)

    def _card(self) -> Card:
        details = self.details()
        if isinstance(details, DerivedMetricDetails):
            composition = f"{details.composition} ({len(details.components)} components)"
        elif details.aggregation is not None:
            target = details.measure or details.aggregation_target
            composition = (
                f"{details.aggregation} of {target.key}"
                if target is not None
                else details.aggregation
            )
        else:
            composition = "expression body"
        scope = (
            f"{len(details.effective_entities)} effective entities; "
            f"{len(details.candidate_dimensions)} candidate dimensions; "
            f"{len(details.candidate_time_dimensions)} candidate time dimensions"
        )
        return (
            super()
            ._card()
            .field(label="composition", value=composition)
            .field(label="analysis_scope", value=scope)
            .field(
                label="inspect",
                value=".details().show() for definition, candidate axes, and measure lineage",
            )
        )


class RelationshipEntry(CatalogEntry[RelationshipKind]):
    """Loaded relationship with typed entity endpoints."""

    ref: Ref[RelationshipKind]

    def details(self) -> RelationshipDetails:
        return cast("RelationshipDetails", self._details)

    @property
    def from_entity(self) -> EntityEntry:
        obj = self._catalog.require(self.details().from_entity)
        if not isinstance(obj, EntityEntry):
            raise AssertionError(f"relationship endpoint is not an EntityEntry: {obj.key}")
        return obj

    @property
    def to_entity(self) -> EntityEntry:
        obj = self._catalog.require(self.details().to_entity)
        if not isinstance(obj, EntityEntry):
            raise AssertionError(f"relationship endpoint is not an EntityEntry: {obj.key}")
        return obj

    def _card(self) -> Card:
        return (
            super()
            ._card()
            .field(label="from_entity", value=self.from_entity.key)
            .field(label="to_entity", value=self.to_entity.key)
        )


def _object_from_details[CatalogObjectT](
    object_type: type[CatalogObjectT],
    details: _CatalogObjectDetails,
    catalog: SemanticCatalog,
) -> CatalogObjectT:
    return cast(
        "CatalogObjectT",
        cast("Any", object_type)(
            ref=details.ref,
            _details=details,
            _catalog=catalog,
        ),
    )


class CatalogCollection[KindT: SemanticKindTag](RenderableResult):
    """Read-only typed collection scoped by exact kind and optional owner."""

    def __init__(
        self,
        catalog: SemanticCatalog,
        object_type: type[CatalogEntry[KindT]],
        kind: SemanticKind,
        *,
        scope_ref: Ref[SemanticKindTag] | None = None,
    ) -> None:
        self._catalog = catalog
        self._object_type = object_type
        self._kind = kind
        self._scope_ref = scope_ref

    @property
    def items(self) -> tuple[CatalogEntry[KindT], ...]:
        return self._catalog._require_index().objects(
            self._object_type,
            scope_ref=self._scope_ref,
        )

    @property
    def refs(self) -> tuple[Ref[KindT], ...]:
        return tuple(item.ref for item in self.items)

    @overload
    def get(self: CatalogCollection[DomainKind], key: str) -> DomainEntry: ...

    @overload
    def get(self: CatalogCollection[DatasourceKind], key: str) -> DatasourceEntry: ...

    @overload
    def get(self: CatalogCollection[EntityKind], key: str) -> EntityEntry: ...

    @overload
    def get(self: CatalogCollection[DimensionKind], key: str) -> DimensionEntry: ...

    @overload
    def get(
        self: CatalogCollection[TimeDimensionKind],
        key: str,
    ) -> TimeDimensionEntry: ...

    @overload
    def get(self: CatalogCollection[MeasureKind], key: str) -> MeasureEntry: ...

    @overload
    def get(self: CatalogCollection[MetricKind], key: str) -> MetricEntry: ...

    @overload
    def get(
        self: CatalogCollection[RelationshipKind],
        key: str,
    ) -> RelationshipEntry: ...

    # Overloads encode the closed KindT-to-entry mapping that Python's generic
    # syntax cannot otherwise express while the runtime signature stays CatalogEntry[K].
    def get(self, key: str) -> CatalogEntry[KindT]:  # type: ignore[misc]
        return self._catalog._get_from_collection(self, key)

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self) -> Iterator[CatalogEntry[KindT]]:
        return iter(self.items)

    def __getitem__(self, index: int) -> CatalogEntry[KindT]:
        return self.items[index]

    def _repr_identity(self) -> str:
        scope = self._scope_ref.key if self._scope_ref is not None else "catalog"
        return (
            f"CatalogCollection type={self._object_type.__name__} scope={scope} count={len(self)}"
        )

    def _card(self) -> Card:
        rows = [(item.key, item.name) for item in self.items]
        return Card(
            identity=self._repr_identity(),
            available=(
                ".items",
                ".refs",
                ".get(...)",
                ".render()",
                ".show()",
            ),
        ).table(
            columns=("ref", "name"),
            rows=rows,
            row_count=len(rows),
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_OBJECT_TYPE_BY_KIND: dict[SemanticKind, type[CatalogEntry[SemanticKindTag]]] = {
    SemanticKind.DOMAIN: DomainEntry,
    SemanticKind.DATASOURCE: DatasourceEntry,
    SemanticKind.ENTITY: EntityEntry,
    SemanticKind.DIMENSION: DimensionEntry,
    SemanticKind.TIME_DIMENSION: TimeDimensionEntry,
    SemanticKind.MEASURE: MeasureEntry,
    SemanticKind.METRIC: MetricEntry,
    SemanticKind.RELATIONSHIP: RelationshipEntry,
}


def _require_semantic_ref(value: object, *, parameter: str) -> Ref[SemanticKindTag]:
    if type(value) is Ref:
        return cast("Ref[SemanticKindTag]", value)
    _raise(
        ErrorKind.INVALID_REF,
        f"catalog.{parameter} requires an exact Ref[kind]; received {type(value).__name__}. "
        "Pass entry.ref when starting from catalog navigation, or construct one "
        "with the exact ms.Ref.<kind>(path) factory.",
        cls=SemanticRuntimeError,
        constraint_id=ConstraintId.REF_SHAPE,
        details={
            "operation": f"catalog.{parameter}",
            "expected": "exact Ref[kind]",
            "received_type": type(value).__name__,
        },
    )


def _normalize_location(loc: SourceLocation | DatasourceSourceLocation) -> SourceLocation:
    return SourceLocation(file=loc.file, line=loc.line)


def _build_datasource_object(
    ds_ir: DatasourceIR, reg: Registry, catalog: SemanticCatalog
) -> DatasourceEntry:
    ref = Ref.datasource(ds_ir.semantic_id)
    dependents = tuple(
        _make_ref(d.semantic_id, SemanticKind.ENTITY)
        for d in reg.entities.values()
        if Ref.datasource(d.datasource) == ref
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
    return _object_from_details(DatasourceEntry, details, catalog)


def _build_domain_object(
    model_ir: DomainIR, reg: Registry, catalog: SemanticCatalog
) -> DomainEntry:
    ref = _make_ref(model_ir.name, SemanticKind.DOMAIN)
    datasets_refs = tuple(
        _make_ref(d.semantic_id, SemanticKind.ENTITY)
        for d in reg.entities.values()
        if d.domain == model_ir.name
    )
    metrics_refs = tuple(
        _make_ref(m.semantic_id, SemanticKind.METRIC)
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
    return _object_from_details(DomainEntry, details, catalog)


def _build_entity_object(ds_ir: EntityIR, reg: Registry, catalog: SemanticCatalog) -> EntityEntry:
    ref = _make_ref(ds_ir.semantic_id, SemanticKind.ENTITY)
    ds_ref = Ref.datasource(ds_ir.datasource)
    fields_refs = tuple(
        _make_ref(
            f.semantic_id,
            SemanticKind.TIME_DIMENSION if f.is_time_dimension else SemanticKind.DIMENSION,
        )
        for f in reg.dimensions.values()
        if f.entity == ds_ir.semantic_id
    )
    measure_refs = tuple(
        _make_ref(m.semantic_id, SemanticKind.MEASURE)
        for m in reg.measures.values()
        if m.entity == ds_ir.semantic_id
    )
    rels_refs = tuple(
        _make_ref(r.semantic_id, SemanticKind.RELATIONSHIP)
        for r in reg.relationships.values()
        if r.from_entity == ds_ir.semantic_id or r.to_entity == ds_ir.semantic_id
    )
    metric_refs = tuple(
        _make_ref(m.semantic_id, SemanticKind.METRIC)
        for m in reg.metrics.values()
        if ds_ir.semantic_id in m.entities
    )
    children = fields_refs + measure_refs + metric_refs + rels_refs
    metric_dependents = tuple(
        _make_ref(m.semantic_id, SemanticKind.METRIC)
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
    return _object_from_details(EntityEntry, details, catalog)


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


def _expression_dependency_refs(
    catalog: SemanticCatalog,
    ref: Ref[SemanticKindTag],
) -> tuple[Ref[SemanticKindTag], ...]:
    """Return deterministic bound field refs without exposing body callables."""
    sidecar = catalog._project._expression_sidecar
    if sidecar is None:
        return ()
    body = sidecar.bodies.get(ref)
    if body is None:
        return ()
    ordered: dict[Ref[SemanticKindTag], None] = {}
    for binding in body.bindings:
        ordered.setdefault(binding.to_ref(), None)
    return tuple(ordered)


def _build_dimension_object(
    f_ir: DimensionIR, reg: Registry, catalog: SemanticCatalog
) -> CatalogEntry[SemanticKindTag]:
    is_time = f_ir.is_time_dimension
    kind = SemanticKind.TIME_DIMENSION if is_time else SemanticKind.DIMENSION
    ref = _make_ref(f_ir.semantic_id, kind)
    ds_ref = _make_ref(f_ir.entity, SemanticKind.ENTITY)
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
            parents=(ds_ref, *_expression_dependency_refs(catalog, ref)),
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
            parents=(ds_ref, *_expression_dependency_refs(catalog, ref)),
            children=(),
            dependents=(),
            python_symbol=f_ir.python_symbol,
            entity=ds_ref,
        )
    return _object_from_details(TimeDimensionEntry if is_time else DimensionEntry, details, catalog)


def _build_measure_object(m_ir: MeasureIR, reg: Registry, catalog: SemanticCatalog) -> MeasureEntry:
    ref = _make_ref(m_ir.semantic_id, SemanticKind.MEASURE)
    entity_ref = _make_ref(m_ir.entity, SemanticKind.ENTITY)
    dependents = tuple(
        _make_ref(metric.semantic_id, SemanticKind.METRIC)
        for metric in reg.metrics.values()
        if metric.measure == m_ir.semantic_id
        or (
            metric.weighted_mean is not None
            and m_ir.semantic_id in {metric.weighted_mean.value, metric.weighted_mean.weight}
        )
    )
    details = MeasureDetails(
        ref=ref,
        kind=SemanticKind.MEASURE,
        name=m_ir.name,
        domain=m_ir.domain,
        context=m_ir.ai_context,
        source_location=m_ir.location,
        parents=(entity_ref, *_expression_dependency_refs(catalog, ref)),
        children=(),
        dependents=dependents,
        python_symbol=m_ir.python_symbol,
        entity=entity_ref,
        additivity=additivity_bucket(m_ir.additivity),
        unit=m_ir.unit,
    )
    return _object_from_details(MeasureEntry, details, catalog)


def _format_agg(agg: object) -> str | None:
    if agg is None:
        return None
    if isinstance(agg, tuple):
        return f"{agg[0]}({agg[1]})"
    return str(agg)


def _aggregation_target_ref(m_ir: MetricIR) -> Ref[SemanticKindTag] | None:
    target = m_ir.aggregation_target or m_ir.measure
    target_kind = m_ir.aggregation_target_kind or ("measure" if m_ir.measure else None)
    if target is None or target_kind is None:
        return None
    kind = {
        "measure": SemanticKind.MEASURE,
        "entity": SemanticKind.ENTITY,
    }[target_kind]
    return _make_ref(target, kind)


def _metric_analysis_metadata(
    metric_ir: MetricIR,
    registry: Registry,
) -> tuple[
    tuple[Ref[SemanticKindTag], ...],
    tuple[Ref[SemanticKindTag], ...],
    tuple[Ref[SemanticKindTag], ...],
    tuple[tuple[str, Ref[SemanticKindTag]], ...],
]:
    """Project recursive metric dependencies into static analysis metadata."""
    effective_entity_ids: dict[str, None] = {}
    measure_lineage: list[tuple[str, Ref[SemanticKindTag]]] = []

    def visit(current: MetricIR, *, role_path: str, active: frozenset[str]) -> None:
        if current.semantic_id in active:
            raise AssertionError(f"metric composition cycle reached catalog: {current.semantic_id}")
        next_active = active | {current.semantic_id}
        for entity_id in current.entities:
            effective_entity_ids.setdefault(entity_id, None)
        if current.measure is not None:
            measure_lineage.append(
                (role_path or "measure", _make_ref(current.measure, SemanticKind.MEASURE))
            )
        if current.weighted_mean is not None:
            prefix = f"{role_path}." if role_path else ""
            measure_lineage.extend(
                (
                    (
                        f"{prefix}value",
                        _make_ref(current.weighted_mean.value, SemanticKind.MEASURE),
                    ),
                    (
                        f"{prefix}weight",
                        _make_ref(current.weighted_mean.weight, SemanticKind.MEASURE),
                    ),
                )
            )
        if current.composition is None:
            return
        for role, component_id in composition_components(current.composition).items():
            component = registry.metrics.get(component_id)
            if component is None:
                raise AssertionError(
                    f"metric composition component missing from ready catalog: {component_id}"
                )
            component_path = f"{role_path}.{role}" if role_path else role
            visit(component, role_path=component_path, active=next_active)

    visit(metric_ir, role_path="", active=frozenset())
    effective_entities = tuple(
        _make_ref(entity_id, SemanticKind.ENTITY) for entity_id in effective_entity_ids
    )
    candidate_dimensions: list[Ref[SemanticKindTag]] = []
    candidate_time_dimensions: list[Ref[SemanticKindTag]] = []
    for dimension in sorted(registry.dimensions.values(), key=lambda item: item.semantic_id):
        if dimension.entity not in effective_entity_ids:
            continue
        target = candidate_time_dimensions if dimension.is_time_dimension else candidate_dimensions
        target.append(
            _make_ref(
                dimension.semantic_id,
                SemanticKind.TIME_DIMENSION
                if dimension.is_time_dimension
                else SemanticKind.DIMENSION,
            )
        )
    return (
        effective_entities,
        tuple(candidate_dimensions),
        tuple(candidate_time_dimensions),
        tuple(measure_lineage),
    )


def _build_metric_object(
    m_ir: MetricIR, reg: Registry, project: SemanticProject, catalog: SemanticCatalog
) -> MetricEntry:
    ref = _make_ref(m_ir.semantic_id, SemanticKind.METRIC)
    entity_refs = tuple(_make_ref(ds, SemanticKind.ENTITY) for ds in m_ir.entities)
    root_entity_ref = _make_ref(m_ir.root_entity, SemanticKind.ENTITY) if m_ir.root_entity else None
    comp_map = composition_components(m_ir.composition) if m_ir.composition is not None else {}
    components = tuple(
        (role, _make_ref(comp_ref, SemanticKind.METRIC)) for role, comp_ref in comp_map.items()
    )
    component_refs = tuple(r for _, r in components)
    aggregation_target = _aggregation_target_ref(m_ir)
    (
        effective_entities,
        candidate_dimensions,
        candidate_time_dimensions,
        measure_lineage,
    ) = _metric_analysis_metadata(m_ir, reg)
    linear_terms = (
        tuple((t.sign, t.metric) for t in m_ir.composition.terms)
        if isinstance(m_ir.composition, LinearComposition)
        else ()
    )
    required_rels: tuple[Ref[SemanticKindTag], ...] = ()
    if len(m_ir.entities) > 1:
        required_rels = tuple(
            _make_ref(r.semantic_id, SemanticKind.RELATIONSHIP)
            for r in reg.relationships.values()
            if r.domain == m_ir.domain
            and r.from_entity in m_ir.entities
            and r.to_entity in m_ir.entities
        )
    weighted_mean_refs = (
        (
            _make_ref(m_ir.weighted_mean.value, SemanticKind.MEASURE),
            _make_ref(m_ir.weighted_mean.weight, SemanticKind.MEASURE),
        )
        if m_ir.weighted_mean is not None
        else ()
    )
    parents = (
        entity_refs
        + component_refs
        + weighted_mean_refs
        + required_rels
        + _expression_dependency_refs(catalog, ref)
    )
    dependents = tuple(
        _make_ref(m2.semantic_id, SemanticKind.METRIC)
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
            effective_entities=effective_entities,
            candidate_dimensions=candidate_dimensions,
            candidate_time_dimensions=candidate_time_dimensions,
            measure_lineage=measure_lineage,
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
            aggregation=(
                "weighted_mean" if m_ir.weighted_mean is not None else _format_agg(m_ir.aggregation)
            ),
            measure=_make_ref(m_ir.measure, SemanticKind.MEASURE) if m_ir.measure else None,
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
            filter=m_ir.filter,
            effective_entities=effective_entities,
            candidate_dimensions=candidate_dimensions,
            candidate_time_dimensions=candidate_time_dimensions,
            measure_lineage=measure_lineage,
            weighted_mean_value=(
                _make_ref(m_ir.weighted_mean.value, SemanticKind.MEASURE)
                if m_ir.weighted_mean is not None
                else None
            ),
            weighted_mean_weight=(
                _make_ref(m_ir.weighted_mean.weight, SemanticKind.MEASURE)
                if m_ir.weighted_mean is not None
                else None
            ),
        )
    return _object_from_details(MetricEntry, details, catalog)


def _build_relationship_object(
    r_ir: RelationshipIR, reg: Registry, catalog: SemanticCatalog
) -> RelationshipEntry:
    ref = _make_ref(r_ir.semantic_id, SemanticKind.RELATIONSHIP)
    from_ref = _make_ref(r_ir.from_entity, SemanticKind.ENTITY)
    to_ref = _make_ref(r_ir.to_entity, SemanticKind.ENTITY)
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
    return _object_from_details(RelationshipEntry, details, catalog)


# ---------------------------------------------------------------------------
# _CatalogIndex — private query layer for typed catalog objects
# ---------------------------------------------------------------------------


class _CatalogIndex:
    """Immutable exact-ref and scoped-navigation index for one catalog."""

    def __init__(
        self,
        catalog: SemanticCatalog,
        project: SemanticProject,
        registry: Registry,
    ) -> None:
        self.catalog = catalog
        self.project = project
        self.registry = registry
        objects = self._build_objects()
        self._by_ref = {obj.ref: obj for obj in objects}
        self._by_name: dict[str, tuple[CatalogEntry[SemanticKindTag], ...]] = {}
        for name in sorted({obj.name for obj in objects}):
            self._by_name[name] = tuple(
                sorted(
                    (obj for obj in objects if obj.name == name),
                    key=lambda obj: obj.key,
                )
            )

    def _build_objects(self) -> tuple[CatalogEntry[SemanticKindTag], ...]:
        reg = self.registry
        result: list[CatalogEntry[SemanticKindTag]] = []
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
        return tuple(sorted(result, key=lambda obj: obj.key))

    def require(self, ref: Ref[SemanticKindTag]) -> CatalogEntry[SemanticKindTag] | None:
        return self._by_ref.get(ref)

    def named(self, name: str) -> tuple[CatalogEntry[SemanticKindTag], ...]:
        return self._by_name.get(name, ())

    def objects[CatalogObjectT](
        self,
        object_type: type[CatalogObjectT],
        *,
        scope_ref: Ref[SemanticKindTag] | None = None,
    ) -> tuple[CatalogObjectT, ...]:
        candidates = tuple(
            cast("CatalogObjectT", obj) for obj in self._by_ref.values() if type(obj) is object_type
        )
        if scope_ref is None:
            return candidates
        scope = self._by_ref.get(scope_ref)
        if scope is None:
            return ()
        selected = tuple(
            obj
            for obj in candidates
            if self._in_scope(cast("CatalogEntry[SemanticKindTag]", obj), scope)
        )
        return selected

    def details_under(
        self,
        kind: SemanticKind,
        *,
        scope_ref: Ref[SemanticKindTag] | None = None,
    ) -> tuple[_CatalogObjectDetails, ...]:
        object_type = _OBJECT_TYPE_BY_KIND[kind]
        return tuple(obj.details() for obj in self.objects(object_type, scope_ref=scope_ref))

    def semantic_ids(
        self,
        kind: SemanticKind,
        *,
        scope_ref: Ref[SemanticKindTag] | None = None,
    ) -> tuple[str, ...]:
        return tuple(details.ref.path for details in self.details_under(kind, scope_ref=scope_ref))

    def _in_scope(
        self,
        obj: CatalogEntry[SemanticKindTag],
        scope: CatalogEntry[SemanticKindTag],
    ) -> bool:
        details = obj.details()
        if isinstance(scope, DomainEntry):
            return details.domain == scope.ref.path
        if isinstance(scope, DatasourceEntry):
            return isinstance(details, EntityDetails) and details.datasource == scope.ref
        if isinstance(scope, EntityEntry):
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


class SemanticCatalog:
    """Read-only object graph over a loaded semantic project.

    Args:
        project: A loaded SemanticProject instance (status must be 'ready').

    Returns:
        SemanticCatalog with typed collection properties (domains, metrics, etc.),
        require(), preview(), readiness(), and verify() methods.

    Example:
        >>> catalog = ms.load()
        >>> catalog.domains.show()
        >>> catalog.metrics.show()  # all metrics across domains
        >>> revenue = catalog.require(ms.Ref.metric("sales.revenue"))
        >>> revenue.details().additivity

    Constraints:
        catalog is obtained via ms.load(), not constructed directly.
        Typed collection properties return CatalogCollection[CatalogEntry].
        SemanticCatalog objects do not expose internal IR instances.
    """

    __slots__ = ("_index", "_project", "_reg", "_state")

    _project: SemanticProject
    _state: CompiledSemanticState
    _reg: Registry
    _index: _CatalogIndex

    def __init__(self, project: SemanticProject) -> None:
        if project._compiled_state is None:
            raise SemanticLoadFailed(project.errors())
        object.__setattr__(self, "_project", project)
        object.__setattr__(self, "_state", project._compiled_state)
        object.__setattr__(self, "_reg", self._state.registry)
        object.__setattr__(self, "_index", _CatalogIndex(self, project, self._reg))

    def __setattr__(self, name: str, value: object) -> NoReturn:
        del name, value
        raise AttributeError("SemanticCatalog instances are immutable; call ms.load() again")

    @property
    def definition_fingerprint(self) -> str:
        """Canonical identity of this catalog's immutable compiled graph."""
        return self._state.definition_fingerprint

    @property
    def semantic_root(self) -> Path:
        """Return the semantic root path (models/semantic/)."""
        return self._project.semantic_root

    @property
    def workspace_dir(self) -> Path:
        """Return the workspace directory path."""
        return self._project.workspace_dir

    # Collection property names exposed by SemanticCatalog. Used to teach the
    # common ``catalog.list_metrics()`` mistake (catalog exposes properties, not
    # ``list_xxx()`` methods). See issue #32.
    _COLLECTION_PROPERTIES = frozenset(
        {
            "domains",
            "datasources",
            "entities",
            "dimensions",
            "time_dimensions",
            "measures",
            "metrics",
            "relationships",
        }
    )

    def __getattr__(self, name: str) -> NoReturn:
        if name.startswith("list_"):
            property_name = name[len("list_") :]
            if property_name in self._COLLECTION_PROPERTIES:
                raise AttributeError(
                    f"{type(self).__name__!r} has no attribute {name!r}; "
                    f"catalog exposes collection properties, not list_xxx() methods. "
                    f"Use catalog.{property_name} (e.g. catalog.{property_name}.show())."
                )
        raise AttributeError(f"{type(self).__name__!r} object has no attribute {name!r}")

    def _require_ready(self) -> Registry:
        reg = self._reg
        if self._project.is_ready() and reg is not None:
            return reg
        errors = self._project.errors()
        if errors:
            raise SemanticLoadFailed(errors)
        _raise(
            ErrorKind.PROJECT_NOT_LOADED,
            "Semantic catalog is not loaded. Construct a fresh catalog with ms.load().",
            cls=SemanticRuntimeError,
        )

    def _require_index(self) -> _CatalogIndex:
        self._require_ready()
        return self._index

    def _collection[KindT: SemanticKindTag](
        self,
        object_type: type[CatalogEntry[KindT]],
        kind: SemanticKind,
        *,
        scope_ref: Ref[SemanticKindTag] | None = None,
    ) -> CatalogCollection[KindT]:
        self._require_ready()
        return CatalogCollection(self, object_type, kind, scope_ref=scope_ref)

    def _get_from_collection[KindT: SemanticKindTag](
        self,
        collection: CatalogCollection[KindT],
        key: str,
    ) -> CatalogEntry[KindT]:
        if type(key) is not str:
            _raise(
                ErrorKind.INVALID_REF,
                f"CatalogCollection.get(...) expected str, got {type(key).__name__}.",
                cls=SemanticRuntimeError,
            )
        if "." in key or ":" in key:
            _raise(
                ErrorKind.INVALID_REF,
                "CatalogCollection.get(...) accepts one local name segment only. "
                "Use catalog.require(ms.Ref.<kind>(path)) for global lookup.",
                cls=SemanticRuntimeError,
                refs=(key,),
            )
        from marivo.refs import _validate_segment

        try:
            _validate_segment(key, role="catalog collection local name")
        except ValueError as exc:
            _raise(
                ErrorKind.INVALID_REF,
                str(exc),
                cls=SemanticRuntimeError,
                refs=(key,),
            )
        items = collection.items
        matches = tuple(item for item in items if item.name == key)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            calls = tuple(
                f"catalog.require(ms.Ref.{item.kind.value}({item.path!r}))" for item in matches
            )
            _raise(
                ErrorKind.AMBIGUOUS_REFERENCE,
                f"Local name {key!r} matched {len(matches)} objects in this collection. "
                f"Use one exact call: {', '.join(calls)}.",
                cls=SemanticRuntimeError,
                refs=tuple(item.key for item in matches),
            )
        available = tuple(item.name for item in items[:12])
        scope = collection._scope_ref.key if collection._scope_ref is not None else "catalog"
        _raise(
            ErrorKind.NOT_FOUND,
            f"Local name {key!r} was not found in {collection._kind.value} "
            f"collection scoped to {scope}. Available names: {available!r}.",
            cls=SemanticRuntimeError,
            refs=(key,),
        )

    @property
    def domains(self) -> CatalogCollection[DomainKind]:
        return self._collection(DomainEntry, SemanticKind.DOMAIN)

    @property
    def datasources(self) -> CatalogCollection[DatasourceKind]:
        return self._collection(DatasourceEntry, SemanticKind.DATASOURCE)

    @property
    def entities(self) -> CatalogCollection[EntityKind]:
        return self._collection(EntityEntry, SemanticKind.ENTITY)

    @property
    def dimensions(self) -> CatalogCollection[DimensionKind]:
        return self._collection(DimensionEntry, SemanticKind.DIMENSION)

    @property
    def time_dimensions(self) -> CatalogCollection[TimeDimensionKind]:
        return self._collection(TimeDimensionEntry, SemanticKind.TIME_DIMENSION)

    @property
    def measures(self) -> CatalogCollection[MeasureKind]:
        return self._collection(MeasureEntry, SemanticKind.MEASURE)

    @property
    def metrics(self) -> CatalogCollection[MetricKind]:
        return self._collection(MetricEntry, SemanticKind.METRIC)

    @property
    def relationships(self) -> CatalogCollection[RelationshipKind]:
        return self._collection(RelationshipEntry, SemanticKind.RELATIONSHIP)

    def require[KindT: SemanticKindTag](self, ref: Ref[KindT], /) -> CatalogEntry[KindT]:
        """Require exact membership of one typed ref in this compiled catalog."""
        exact_ref = _require_semantic_ref(ref, parameter="require(ref)")
        found = self._require_index().require(exact_ref)
        if found is not None:
            return cast("CatalogEntry[KindT]", found)
        candidates = tuple(
            item.ref
            for item in self._require_index()._by_ref.values()
            if item.kind is exact_ref.kind
        )[:12]
        calls = tuple(
            f"ms.Ref.{candidate.kind.value}({candidate.path!r})" for candidate in candidates
        )
        _raise(
            ErrorKind.NOT_FOUND,
            f"Ref {exact_ref.key!r} is not present in this compiled catalog. "
            f"Loaded {exact_ref.kind.value} candidates: {calls!r}.",
            cls=SemanticRuntimeError,
            refs=(exact_ref.key,),
            details={
                "catalog_definition_fingerprint": self.definition_fingerprint,
                "filtered_domains": self._project._filtered_domains,
                "candidates": tuple(candidate.key for candidate in candidates),
            },
        )

    def readiness(
        self,
        refs: Sequence[Ref[SemanticKindTag]] | None = None,
    ) -> ReadinessReport:
        """Return explicit certification and diagnostics for the given semantic refs.

        Reads loaded state plus persisted row-free preview evidence without
        acquiring, refreshing, or querying. Missing evidence produces exact
        next calls for the caller to execute explicitly.

        ``analysis_ready_refs`` contains only directly selected refs whose full
        dependency closure has no blocker. Warnings, including missing or stale
        runtime preview certification, remain visible without excluding a ref.

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
            requests technical certification. Analysis APIs do not invoke
            readiness automatically.
        """
        self._require_ready()
        scoped_refs: list[Ref[SemanticKindTag]] | None = None
        if refs is not None:
            exact_refs = tuple(
                _require_semantic_ref(ref, parameter="readiness(refs=...)") for ref in refs
            )
            if not exact_refs:
                _raise(
                    ErrorKind.INVALID_REF,
                    "catalog.readiness(refs=...) requires a non-empty sequence.",
                    cls=SemanticRuntimeError,
                )
            if len(set(exact_refs)) != len(exact_refs):
                ordered_duplicates = tuple(
                    dict.fromkeys(
                        ref for index, ref in enumerate(exact_refs) if ref in exact_refs[:index]
                    )
                )
                _raise(
                    ErrorKind.INVALID_REF,
                    "catalog.readiness(refs=...) requires unique exact refs; received "
                    f"{[ref.key for ref in ordered_duplicates]}.",
                    cls=SemanticRuntimeError,
                    refs=tuple(ref.key for ref in ordered_duplicates),
                )
            scoped_refs = [self.require(ref).ref for ref in exact_refs]
        return self._project.readiness(refs=scoped_refs)

    def verify(self, ref: Ref[SemanticKindTag], /) -> VerifyResult:
        """Statically verify one exact loaded ref without reloading or querying."""
        entry = self.require(_require_semantic_ref(ref, parameter="verify(ref)"))
        return self._project._verify(entry.ref)

    def contract(self) -> AuthoringContract:
        """Return the mechanical continuation contract for this catalog.

        The contract exposes catalog-level browse and load affordances, not
        per-object transitions. Use ``CatalogEntry.contract()`` for
        object-scoped verify, preview, and readiness transitions.
        """
        from marivo.semantic._capabilities.contracts import contract_for_semantic_catalog

        return contract_for_semantic_catalog()

    def _semantic_resolver(
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
        ref: Ref[SemanticKindTag],
        /,
        *,
        using: PreviewUsing,
        limit: int = PREVIEW_DEFAULT_LIMIT,
        include_types: bool = True,
        context_columns: Iterable[str] | None = None,
    ) -> PreviewResult:
        """Return one bounded runtime preview for an exact loaded ref."""
        return self._preview_one(
            _require_semantic_ref(ref, parameter="preview(ref)"),
            using=using,
            limit=limit,
            include_types=include_types,
            context_columns=context_columns,
        )

    def preview_many(
        self,
        refs: Sequence[Ref[SemanticKindTag]],
        /,
        *,
        using: PreviewUsing,
        limit: int = PREVIEW_DEFAULT_LIMIT,
        include_types: bool = True,
    ) -> PreviewBatchResult:
        """Return a bounded batch preview for a non-empty exact ref sequence."""
        return self._preview_batch(
            refs,
            using=using,
            limit=limit,
            include_types=include_types,
        )

    def _preview_one(
        self,
        ref: Ref[SemanticKindTag],
        *,
        using: PreviewUsing,
        limit: int = PREVIEW_DEFAULT_LIMIT,
        include_types: bool = True,
        context_columns: Iterable[str] | None = None,
    ) -> PreviewResult:
        """Execute the existing one-object preview contract."""
        reg = self._require_ready()
        ref_obj = _require_semantic_ref(ref, parameter="preview(ref)")
        self.require(ref_obj)
        ref_str = ref_obj.path
        kind = ref_obj.kind
        sidecar = self._project._expression_sidecar
        if sidecar is None:
            _raise(
                ErrorKind.PROJECT_NOT_LOADED,
                "Semantic catalog expression sidecar is unavailable. Construct a fresh catalog with ms.load().",
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
            catalog_definition_fingerprint=self.definition_fingerprint,
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
            resolver = self._semantic_resolver(
                connections=connections,
                sample_size=(METRIC_PREVIEW_SAMPLE_SIZE if kind == SemanticKind.METRIC else None),
                entity_scopes=bindings.entity_scopes,
            )
            if kind == SemanticKind.ENTITY:
                table = resolver.table(_make_ref(ref_str, SemanticKind.ENTITY))
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
                parent_table = resolver.table(_make_ref(measure_ir.entity, SemanticKind.ENTITY))
                measure_value = resolver.measure(_make_ref(ref_str, SemanticKind.MEASURE))
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
                parent_table = resolver.table(_make_ref(field_ir.entity, SemanticKind.ENTITY))
                field_value = resolver.dimension(cast("Ref[FieldKind]", _make_ref(ref_str, kind)))
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
                metric_ref = _make_ref(ref_str, SemanticKind.METRIC)
                sample_policy = PreviewSamplePolicy(
                    method="pre_aggregate_limit",
                    limit=preview_limit,
                )
                result = preview_ibis_table(
                    _metric_preview_table(
                        resolver,
                        reg,
                        metric_ref,
                        alias="value",
                    ),
                    kind="semantic_metric",
                    ref=ref_str,
                    limit=preview_limit,
                    sample_policy=sample_policy,
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
                left = resolver.table(_make_ref(relationship.from_entity, SemanticKind.ENTITY))
                right = resolver.table(_make_ref(relationship.to_entity, SemanticKind.ENTITY))
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
                        resolver.dimension_on(
                            cast("Ref[FieldKind]", _make_ref(from_key, from_kind)), left
                        ).name(left_name)
                    )
                    right_values.append(
                        resolver.dimension_on(
                            cast("Ref[FieldKind]", _make_ref(to_key, to_kind)), right
                        ).name(right_name)
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

    def _preview_batch(
        self,
        refs: Sequence[Ref[SemanticKindTag]],
        *,
        using: PreviewUsing,
        limit: int,
        include_types: bool,
    ) -> PreviewBatchResult:
        reg = self._require_ready()
        if not refs:
            _raise(
                ErrorKind.INVALID_REF,
                "catalog.preview_many(refs, using=...) requires a non-empty sequence.",
                cls=SemanticRuntimeError,
                details={"query_executed": False},
            )
        ref_objects = tuple(
            _require_semantic_ref(value, parameter="preview_many(refs)") for value in refs
        )
        for ref_obj in ref_objects:
            self.require(ref_obj)
        seen_refs: set[Ref[SemanticKindTag]] = set()
        duplicate_seen: set[Ref[SemanticKindTag]] = set()
        duplicate_list: list[Ref[SemanticKindTag]] = []
        for ref_obj in ref_objects:
            if ref_obj in seen_refs and ref_obj not in duplicate_seen:
                duplicate_list.append(ref_obj)
                duplicate_seen.add(ref_obj)
            seen_refs.add(ref_obj)
        duplicate_refs = tuple(duplicate_list)
        if duplicate_refs:
            _raise(
                ErrorKind.INVALID_REF,
                "catalog.preview_many(refs, using=...) received duplicate refs: "
                f"{[ref.key for ref in duplicate_refs]}.",
                cls=SemanticRuntimeError,
                refs=tuple(ref.key for ref in duplicate_refs),
                details={"query_executed": False},
            )

        supported_kinds = {
            SemanticKind.ENTITY,
            SemanticKind.DIMENSION,
            SemanticKind.TIME_DIMENSION,
            SemanticKind.MEASURE,
            SemanticKind.METRIC,
            SemanticKind.RELATIONSHIP,
        }
        resolved: list[tuple[str, SemanticKind]] = []
        for ref_obj in ref_objects:
            kind = ref_obj.kind
            if kind not in supported_kinds:
                _raise(
                    ErrorKind.MATERIALIZE_FAILED,
                    f"catalog.preview_many(refs, using=...) does not support {kind} refs.",
                    cls=SemanticRuntimeError,
                    refs=(ref_obj.path,),
                    details={"query_executed": False, "kind": str(kind)},
                )
            resolved.append((ref_obj.path, kind))

        preview_limit = validate_preview_limit(limit)
        sidecar = self._project._expression_sidecar
        if sidecar is None:
            _raise(
                ErrorKind.PROJECT_NOT_LOADED,
                "Semantic catalog sidecar is unavailable. Reload the catalog before previewing.",
                cls=SemanticRuntimeError,
                refs=tuple(ref.path for ref in ref_objects),
                details={"query_executed": False},
            )
        normalized = normalize_preview_batch_bindings(
            refs=resolved,
            using=using,
            registry=reg,
            sidecar=sidecar,
            project_root=self.workspace_dir,
            catalog_definition_fingerprint=self.definition_fingerprint,
        )
        items = tuple(
            _BatchPreviewItem(order, ref_obj, kind, bindings)
            for order, (ref_obj, (_ref_id, kind), bindings) in enumerate(
                zip(ref_objects, resolved, normalized, strict=True)
            )
        )

        groups: dict[tuple[object, ...], list[_BatchPreviewItem]] = {}
        row_kinds = {
            SemanticKind.ENTITY,
            SemanticKind.DIMENSION,
            SemanticKind.TIME_DIMENSION,
            SemanticKind.MEASURE,
        }
        for item in items:
            identity = (
                item.bindings.datasource_id,
                item.bindings.entity_ids,
                tuple(snapshot.id for snapshot in item.bindings.snapshots),
                item.bindings.timeout_seconds,
            )
            key: tuple[object, ...]
            if item.kind in row_kinds:
                key = ("row", *identity)
            elif item.kind == SemanticKind.METRIC:
                key = ("metric", *identity)
            else:
                key = ("relationship", item.order)
            groups.setdefault(key, []).append(item)

        connections = self._project._connection_service()
        by_order: dict[int, PreviewResult] = {}
        for group_key, group_items in groups.items():
            try:
                if group_key[0] == "row":
                    raw_results = self._preview_row_group(
                        tuple(group_items),
                        connections=connections,
                        limit=preview_limit,
                        include_types=include_types,
                    )
                    results = tuple(
                        persist_preview_check(
                            result,
                            bindings=item.bindings,
                            project_root=self.workspace_dir,
                        )
                        for item, result in zip(group_items, raw_results, strict=True)
                    )
                elif group_key[0] == "metric":
                    raw_results = self._preview_metric_group(
                        tuple(group_items),
                        connections=connections,
                        limit=preview_limit,
                        include_types=include_types,
                    )
                    results = tuple(
                        persist_preview_check(
                            result,
                            bindings=item.bindings,
                            project_root=self.workspace_dir,
                        )
                        for item, result in zip(group_items, raw_results, strict=True)
                    )
                else:
                    item = group_items[0]
                    results = (
                        self._preview_one(
                            item.ref,
                            using=(
                                item.bindings.snapshots[0]
                                if len(item.bindings.entity_ids) == 1
                                else {
                                    _make_ref(entity_id, SemanticKind.ENTITY): snapshot
                                    for entity_id, snapshot in zip(
                                        item.bindings.entity_ids,
                                        item.bindings.snapshots,
                                        strict=True,
                                    )
                                }
                            ),
                            limit=preview_limit,
                            include_types=include_types,
                        ),
                    )
            except SemanticRuntimeError:
                raise
            except Exception as exc:
                _raise(
                    ErrorKind.MATERIALIZE_FAILED,
                    f"Batch preview {group_key[0]} group failed: {exc}",
                    cls=SemanticRuntimeError,
                    refs=tuple(item.ref.path for item in group_items),
                    details={"group": str(group_key[0])},
                )
            by_order.update(
                (item.order, result) for item, result in zip(group_items, results, strict=True)
            )
        return PreviewBatchResult(results=tuple(by_order[index] for index in range(len(items))))

    def _preview_row_group(
        self,
        items: tuple[_BatchPreviewItem, ...],
        *,
        connections: DatasourceConnectionService,
        limit: int,
        include_types: bool,
    ) -> tuple[PreviewResult, ...]:
        reg = self._require_ready()
        bindings = items[0].bindings
        entity_id = bindings.entity_ids[0]
        resolver = self._semantic_resolver(
            connections=connections,
            entity_scopes=bindings.entity_scopes,
        )
        parent_table = resolver.table(_make_ref(entity_id, SemanticKind.ENTITY))
        raw_columns = tuple(parent_table.columns)
        include_entity = any(item.kind == SemanticKind.ENTITY for item in items)
        raw_selected = set(raw_columns if include_entity else ())
        aliases: dict[int, str] = {}
        contexts: dict[int, tuple[str, ...]] = {}
        semantic_values = []
        used_names = set(raw_columns)
        for item in items:
            if item.kind == SemanticKind.ENTITY:
                continue
            alias = f"__marivo_preview_{item.order}"
            while alias in used_names:
                alias = f"_{alias}"
            used_names.add(alias)
            aliases[item.order] = alias
            if item.kind in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION}:
                field_ir = reg.dimensions[item.ref.path]
                field_name = item.ref.name
                context = tuple(column for column in raw_columns if column != field_name)[:3]
                contexts[item.order] = context
                raw_selected.update(context)
                value = resolver.dimension(cast("Ref[FieldKind]", item.ref))
            else:
                value = resolver.measure(cast("Ref[MeasureKind]", item.ref))
            semantic_values.append(value.name(alias))

        selected_raw_columns = tuple(column for column in raw_columns if column in raw_selected)
        preview_table = parent_table.select(
            *[parent_table[column] for column in selected_raw_columns],
            *semantic_values,
        )
        profile = require_profile_for_backend_type(bindings.backend)
        timeout = profile.authoring_timeout
        if timeout is None:
            _raise(
                ErrorKind.MATERIALIZE_FAILED,
                "catalog.preview() requires an adapter-enforced authoring timeout.",
                cls=SemanticRuntimeError,
                refs=tuple(item.ref.path for item in items),
                details={"query_executed": False, "backend": bindings.backend},
            )
        backend = connections.session_backend(bindings.datasource_id)
        with timeout(backend, bindings.timeout_seconds):
            dataframe = preview_table.limit(limit + 1).execute()
        schema_types = {name: str(dtype) for name, dtype in preview_table.schema().items()}
        from marivo.datasource.timezone import system_timezone_name

        report_tz = system_timezone_name()
        results: list[PreviewResult] = []
        for item in items:
            if item.kind == SemanticKind.ENTITY:
                columns = raw_columns
                frame = dataframe.loc[:, list(columns)]
                result_types = (
                    {column: schema_types[column] for column in columns} if include_types else {}
                )
                kind: Literal["semantic_dataset", "semantic_field", "semantic_measure"] = (
                    "semantic_dataset"
                )
                timezones: dict[str, dict[str, str | None]] = {}
            else:
                alias = aliases[item.order]
                semantic_name = item.ref.name
                columns = (*contexts.get(item.order, ()), alias)
                frame = dataframe.loc[:, list(columns)].rename(columns={alias: semantic_name})
                result_types = (
                    {
                        **{column: schema_types[column] for column in columns if column != alias},
                        semantic_name: schema_types[alias],
                    }
                    if include_types
                    else {}
                )
                kind = (
                    "semantic_field"
                    if item.kind in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION}
                    else "semantic_measure"
                )
                timezones = {}
                if item.kind == SemanticKind.TIME_DIMENSION:
                    field_ir = reg.dimensions[item.ref.path]
                    entity_ir = reg.entities[field_ir.entity]
                    engine_tz_method = getattr(resolver.connections, "engine_timezone", None)
                    datasource_timezone = (
                        engine_tz_method(entity_ir.datasource)
                        if callable(engine_tz_method)
                        else None
                    )
                    timezones = _preview_timezones_for_field(
                        column_name=semantic_name,
                        field_ir=field_ir,
                        datasource_timezone=datasource_timezone,
                        report_tz=report_tz,
                    )
            results.append(
                preview_from_pandas(
                    frame,
                    kind=kind,
                    ref=item.ref.path,
                    requested_limit=limit,
                    sample_policy=PreviewSamplePolicy(method="bounded_limit", limit=limit),
                    types=result_types,
                    timezones=timezones,
                    report_tz=report_tz,
                )
            )
        return tuple(results)

    def _preview_metric_group(
        self,
        items: tuple[_BatchPreviewItem, ...],
        *,
        connections: DatasourceConnectionService,
        limit: int,
        include_types: bool,
    ) -> tuple[PreviewResult, ...]:
        bindings = items[0].bindings
        registry = self._require_ready()
        resolver = self._semantic_resolver(
            connections=connections,
            sample_size=METRIC_PREVIEW_SAMPLE_SIZE,
            entity_scopes=bindings.entity_scopes,
        )
        aliases = tuple(f"__marivo_metric_{item.order}" for item in items)
        if len(bindings.entity_ids) == 1:
            values = tuple(
                resolver.metric(cast("Ref[MetricKind]", item.ref)).name(alias)
                for item, alias in zip(items, aliases, strict=True)
            )
            parent_table = resolver.table(_make_ref(bindings.entity_ids[0], SemanticKind.ENTITY))
            preview_table = parent_table.aggregate(list(values))
        else:
            metric_tables = tuple(
                _metric_preview_table(
                    resolver,
                    registry,
                    cast("Ref[MetricKind]", item.ref),
                    alias=alias,
                )
                for item, alias in zip(items, aliases, strict=True)
            )
            preview_table = metric_tables[0]
            for metric_table in metric_tables[1:]:
                preview_table = preview_table.cross_join(metric_table)

        profile = require_profile_for_backend_type(bindings.backend)
        timeout = profile.authoring_timeout
        if timeout is None:
            _raise(
                ErrorKind.MATERIALIZE_FAILED,
                "catalog.preview() requires an adapter-enforced authoring timeout.",
                cls=SemanticRuntimeError,
                refs=tuple(item.ref.path for item in items),
                details={"query_executed": False, "backend": bindings.backend},
            )
        backend = connections.session_backend(bindings.datasource_id)
        with timeout(backend, bindings.timeout_seconds):
            dataframe = preview_table.limit(limit + 1).execute()
        schema_types = {name: str(dtype) for name, dtype in preview_table.schema().items()}
        results: list[PreviewResult] = []
        for item, alias in zip(items, aliases, strict=True):
            frame = dataframe.loc[:, [alias]].rename(columns={alias: "value"})
            results.append(
                preview_from_pandas(
                    frame,
                    kind="semantic_metric",
                    ref=item.ref.path,
                    requested_limit=limit,
                    sample_policy=PreviewSamplePolicy(
                        method="pre_aggregate_limit",
                        limit=limit,
                    ),
                    types={"value": schema_types[alias]} if include_types else {},
                    warnings=(
                        PreviewWarning(
                            kind="approximate_preview",
                            message=f"metric computed on {METRIC_PREVIEW_SAMPLE_SIZE} row sample, result is approximate",
                        ),
                    ),
                )
            )
        return tuple(results)


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
