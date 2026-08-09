"""SemanticCatalog — unified agent-facing read surface for marivo.semantic.

Public entrypoint: ms.load() -> SemanticCatalog
"""

from __future__ import annotations

import base64
import binascii
import inspect
import json
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar, Literal, NoReturn, cast, overload

from marivo._temporal import (
    Grain,
    PeriodCalendarSnapshotV1,
    TemporalOccurrenceRecord,
    TemporalResolver,
    TemporalSetSnapshotStore,
    TemporalSetSnapshotV1,
    TimeScope,
    WorkScheduleSnapshotStore,
    WorkScheduleSnapshotV1,
)
from marivo.datasource.engines import require_profile_for_backend_type
from marivo.datasource.ir import AiContextIR, DatasourceIR, DatasourceSourceLocation
from marivo.datasource.runtime import DatasourceConnectionService
from marivo.datasource.snapshot import DiscoverySnapshot
from marivo.datasource.source import AuthoringScope
from marivo.preview import (
    METRIC_PREVIEW_SAMPLE_SIZE,
    PREVIEW_DEFAULT_LIMIT,
    PreviewCoverage,
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
    EventKind,
    FieldKind,
    MeasureKind,
    MetricKind,
    PeriodCalendarKind,
    Ref,
    RelationshipKind,
    SemanticKind,
    SemanticKindTag,
    StateModelKind,
    TemporalSetKind,
    TimeDimensionKind,
    WorkScheduleKind,
)
from marivo.refs import (
    ref as ref_factory,
)
from marivo.render import Card, FieldSection, ListSection, RenderableResult, Section
from marivo.semantic._capabilities.catalog_members import (
    CATALOG_COLLECTION_PROPERTIES,
    CATALOG_MEMBER_CONTRACTS,
)
from marivo.semantic.constraints import ConstraintId
from marivo.semantic.dtos import DatasetSource, PreviewBatchResult
from marivo.semantic.errors import (
    ErrorKind,
    SemanticLoadFailed,
    SemanticRuntimeError,
    _raise,
    repair,
)
from marivo.semantic.event import _event_fingerprint as _event_definition_fingerprint
from marivo.semantic.ir import (
    DateParse,
    DatetimeParse,
    DimensionIR,
    DomainIR,
    EntityIR,
    EntityVersioningIR,
    EventIR,
    EventParticipantIR,
    HourPrefixParse,
    LinearComposition,
    MeasureIR,
    MetricIR,
    ParityStatus,
    PeriodCalendarIR,
    RatioComposition,
    RelationshipIR,
    SampleIntervalIR,
    SemiAdditive,
    SnapshotVersioningIR,
    SourceLocation,
    SqlProvenance,
    StateModelIR,
    StrptimeParse,
    TemporalSetIR,
    TimestampParse,
    ValidityVersioningIR,
    WhereValue,
    WorkScheduleIR,
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
from marivo.semantic.runtime_metric import (
    RuntimeAggregateExpr,
    RuntimeMetricExpr,
    RuntimeRatioExpr,
    RuntimeSliceExpr,
    RuntimeWeightedMeanExpr,
    replay_payload,
    runtime_metric_leaf_refs,
)
from marivo.semantic.state_model import _state_model_fingerprint

CalendarDate = date

if TYPE_CHECKING:
    from marivo._authoring.model import AuthoringContract, AuthoringRepair
    from marivo.semantic._compiled_state import CompiledSemanticState
    from marivo.semantic.dtos import VerifyResult
    from marivo.semantic.reader import SemanticProject
    from marivo.semantic.readiness import ReadinessReport
    from marivo.semantic.resolver import SemanticResolver
    from marivo.semantic.validator import Registry

__all__ = [
    "AiContextView",
    "CalendarLevelDetails",
    "CalendarPeriodPage",
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
    "EventDetails",
    "EventEntry",
    "MeasureDetails",
    "MeasureEntry",
    "MetricDetails",
    "MetricEntry",
    "PeriodCalendarDetails",
    "PeriodCalendarEntry",
    "RelationshipDetails",
    "RelationshipEntry",
    "SemanticCatalog",
    "SemanticKind",
    "SimpleMetricDetails",
    "SnapshotVersioning",
    "StateModelDetails",
    "StateModelEntry",
    "TemporalOccurrencePage",
    "TemporalSetDetails",
    "TemporalSetEntry",
    "TimeDimensionDetails",
    "TimeDimensionEntry",
    "ValidityVersioning",
    "WorkScheduleDetails",
    "WorkScheduleEntry",
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
        numerator_ref = ref_factory.metric(composition.numerator)
        denominator_ref = ref_factory.metric(composition.denominator)
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
                    ref_factory.metric(term.metric),
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
def _make_ref(path: str, kind: Literal[SemanticKind.EVENT]) -> Ref[EventKind]: ...


@overload
def _make_ref(path: str, kind: Literal[SemanticKind.STATE_MODEL]) -> Ref[StateModelKind]: ...


@overload
def _make_ref(
    path: str, kind: Literal[SemanticKind.PERIOD_CALENDAR]
) -> Ref[PeriodCalendarKind]: ...


@overload
def _make_ref(path: str, kind: Literal[SemanticKind.TEMPORAL_SET]) -> Ref[TemporalSetKind]: ...


@overload
def _make_ref(path: str, kind: Literal[SemanticKind.WORK_SCHEDULE]) -> Ref[WorkScheduleKind]: ...


@overload
def _make_ref(path: str, kind: SemanticKind) -> Ref[SemanticKindTag]: ...


def _make_ref(path: str, kind: SemanticKind) -> Ref[SemanticKindTag]:
    factory = {
        SemanticKind.DOMAIN: ref_factory.domain,
        SemanticKind.DATASOURCE: ref_factory.datasource,
        SemanticKind.ENTITY: ref_factory.entity,
        SemanticKind.DIMENSION: ref_factory.dimension,
        SemanticKind.TIME_DIMENSION: ref_factory.time_dimension,
        SemanticKind.MEASURE: ref_factory.measure,
        SemanticKind.METRIC: ref_factory.metric,
        SemanticKind.RELATIONSHIP: ref_factory.relationship,
        SemanticKind.EVENT: ref_factory.event,
        SemanticKind.STATE_MODEL: ref_factory.state_model,
        SemanticKind.PERIOD_CALENDAR: ref_factory.period_calendar,
        SemanticKind.TEMPORAL_SET: ref_factory.temporal_set,
        SemanticKind.WORK_SCHEDULE: ref_factory.work_schedule,
    }[kind]
    return factory(path)


@dataclass(frozen=True)
class _BatchPreviewItem:
    order: int
    ref: Ref[SemanticKindTag]
    kind: SemanticKind
    bindings: NormalizedPreviewBindings


@dataclass(frozen=True)
class _OntologySemanticIndexes:
    """Immutable semantic-owned reverse indexes consumed by ontology discovery."""

    anchors_by_metric: Mapping[Ref[MetricKind], tuple[Ref[SemanticKindTag], ...]]
    metrics_by_endpoint: Mapping[Ref[SemanticKindTag], tuple[Ref[MetricKind], ...]]


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


def _format_card_refs(
    refs: tuple[Ref[SemanticKindTag], ...],
    *,
    empty: str = "none",
    limit: int = 6,
) -> str:
    if not refs:
        return empty
    rendered = _format_refs(refs, limit=limit)
    if len(refs) > limit:
        rendered += "; full: .details().show()"
    return rendered


def _format_event_participant(
    participant: tuple[
        str,
        Ref[EntityKind],
        str,
        tuple[Ref[RelationshipKind], ...],
    ],
    *,
    bounded: bool,
) -> str:
    name, endpoint, cardinality, path = participant
    if not path:
        path_text = "self"
    elif bounded:
        path_text = _format_card_refs(path)
    else:
        path_text = " -> ".join(ref.key for ref in path)
    return f"{name}: endpoint={endpoint.key}; cardinality={cardinality}; path={path_text}"


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
                f"catalog.verify(ms.ref.{self.ref.kind.value}({self.ref.path!r}))",
                f"catalog.readiness(refs=[ms.ref.{self.ref.kind.value}({self.ref.path!r})])",
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
                    value=", ".join(
                        (
                            f"{dimension_name} in {value!r}"
                            if isinstance(value, tuple)
                            else f"{dimension_name} = {value!r}"
                        )
                        for dimension_name, value in self.filter
                    ),
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


@dataclass(frozen=True, repr=False)
class EventDetails(_DetailsBase):
    """Details for one executable Event definition."""

    source_entity: Ref[EntityKind]
    identity: tuple[Ref[DimensionKind], ...]
    occurred_at: Ref[TimeDimensionKind]
    participants: tuple[tuple[str, Ref[EntityKind], str, tuple[Ref[RelationshipKind], ...]], ...]
    predicate_kind: Literal["all_rows", "filtered"]
    definition_fingerprint: str

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
                FieldSection(label="source_entity", value=self.source_entity.key),
                FieldSection(label="identity", value=_format_refs(self.identity)),
                FieldSection(label="occurred_at", value=self.occurred_at.key),
                FieldSection(label="predicate", value=self.predicate_kind),
                FieldSection(
                    label="definition_fingerprint",
                    value=self.definition_fingerprint,
                ),
                FieldSection(
                    label="participants",
                    value="; ".join(
                        _format_event_participant(participant, bounded=False)
                        for participant in self.participants
                    ),
                ),
            )
        )
        return sections


@dataclass(frozen=True, repr=False)
class StateModelDetails(_DetailsBase):
    """Complete details for one canonical StateModel."""

    subject: Ref[EntityKind]
    states: tuple[tuple[str, bool, bool], ...]
    inceptions: tuple[
        tuple[Ref[EventKind], str, tuple[Ref[RelationshipKind], ...]],
        ...,
    ]
    transitions: tuple[
        tuple[
            str,
            Ref[EventKind],
            str,
            tuple[Ref[RelationshipKind], ...],
            str,
        ],
        ...,
    ]
    definition_fingerprint: str

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
                FieldSection(label="subject", value=self.subject.key),
                FieldSection(
                    label="states",
                    value="; ".join(
                        f"{name}: initial={initial}; terminal={terminal}"
                        for name, initial, terminal in self.states
                    ),
                ),
                FieldSection(
                    label="inceptions",
                    value="; ".join(
                        (
                            f"{event.key}#{role}; path="
                            + ("self" if not path else " -> ".join(item.key for item in path))
                        )
                        for event, role, path in self.inceptions
                    )
                    or "(none)",
                ),
                FieldSection(
                    label="transitions",
                    value="; ".join(
                        (
                            f"{source} -- {event.key}#{role} --> {target}; path="
                            + ("self" if not path else " -> ".join(item.key for item in path))
                        )
                        for source, event, role, path, target in self.transitions
                    )
                    or "(none)",
                ),
                FieldSection(
                    label="definition_fingerprint",
                    value=self.definition_fingerprint,
                ),
            )
        )
        return sections


@dataclass(frozen=True, repr=False)
class CalendarLevelDetails(RenderableResult):
    """Certification-derived facts for one owned period-calendar level."""

    name: str
    key_ref: Ref[DimensionKind] | Ref[TimeDimensionKind]
    period_count: int | None
    direct_finer_levels: tuple[str, ...] | None
    direct_coarser_levels: tuple[str, ...] | None
    rollup_targets: tuple[str, ...] | None

    def _repr_identity(self) -> str:
        return f"CalendarLevelDetails name={self.name}"

    def _card(self) -> Card:
        return (
            Card(identity=self._repr_identity(), available=(".show()",))
            .field("key_ref", self.key_ref.key)
            .field("period_count", str(self.period_count))
            .field("direct_finer_levels", repr(self.direct_finer_levels))
            .field("direct_coarser_levels", repr(self.direct_coarser_levels))
            .field("rollup_targets", repr(self.rollup_targets))
        )


@dataclass(frozen=True, repr=False)
class PeriodCalendarDetails(_DetailsBase):
    """Static source contract for one governed period calendar."""

    date: Ref[TimeDimensionKind]
    boundary_timezone: str
    coverage: tuple[CalendarDate, CalendarDate]
    levels: tuple[CalendarLevelDetails, ...]
    correspondences: Mapping[str, str]
    _correspondence_fields: tuple[tuple[str, str, str], ...]
    _level_bindings: tuple[tuple[str, Ref[DimensionKind]], ...]
    snapshot_status: Literal["missing", "current", "stale", "invalid"]

    @property
    def source_date(self) -> Ref[TimeDimensionKind]:
        """The calendar's civil-date source field (target public spelling)."""
        return self.date

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
                FieldSection(label="source_date", value=self.date.key),
                FieldSection(label="boundary_timezone", value=self.boundary_timezone),
                FieldSection(
                    label="coverage",
                    value=f"[{self.coverage[0].isoformat()}, {self.coverage[1].isoformat()})",
                ),
                FieldSection(label="levels", value=", ".join(item.name for item in self.levels)),
                FieldSection(
                    label="correspondences",
                    value=", ".join(self.correspondences) or "(none)",
                ),
                FieldSection(label="snapshot_status", value=self.snapshot_status),
            )
        )
        return sections


@dataclass(frozen=True, repr=False)
class CalendarPeriodPage(RenderableResult):
    """Bounded snapshot-bound page of exact period scopes."""

    items: tuple[TimeScope, ...]
    next_cursor: str | None

    def _repr_identity(self) -> str:
        return f"CalendarPeriodPage items={len(self.items)}"

    def _card(self) -> Card:
        return Card(
            identity=self._repr_identity(),
            available=(".items", ".next_cursor", ".show()"),
        ).table(
            columns=("scope",),
            rows=((repr(item),) for item in self.items),
            row_count=len(self.items),
        )


@dataclass(frozen=True, repr=False)
class TemporalSetDetails(_DetailsBase):
    """Static source contract for one governed temporal set."""

    boundary_timezone: str
    coverage: tuple[CalendarDate, CalendarDate]
    occurrence_id: Ref[DimensionKind]
    start: Ref[TimeDimensionKind]
    end: Ref[TimeDimensionKind]
    category: Ref[DimensionKind] | None
    occurrence_count: int | None
    snapshot_status: Literal["missing", "current", "stale", "invalid"]

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
                FieldSection(label="boundary_timezone", value=self.boundary_timezone),
                FieldSection(
                    label="coverage",
                    value=f"[{self.coverage[0].isoformat()}, {self.coverage[1].isoformat()})",
                ),
                FieldSection(label="occurrence_id", value=self.occurrence_id.key),
                FieldSection(label="start", value=self.start.key),
                FieldSection(label="end", value=self.end.key),
                FieldSection(
                    label="category",
                    value=self.category.key if self.category is not None else "(none)",
                ),
                FieldSection(label="occurrence_count", value=str(self.occurrence_count)),
                FieldSection(label="snapshot_status", value=self.snapshot_status),
            )
        )
        return sections


@dataclass(frozen=True, repr=False)
class WorkScheduleDetails(_DetailsBase):
    """Static source contract for one governed final daily work schedule."""

    boundary_timezone: str
    coverage: tuple[CalendarDate, CalendarDate]
    date: Ref[TimeDimensionKind]
    is_working: Ref[DimensionKind]
    snapshot_status: Literal["missing", "current", "stale", "invalid"]

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
                FieldSection(label="boundary_timezone", value=self.boundary_timezone),
                FieldSection(
                    label="coverage",
                    value=f"[{self.coverage[0].isoformat()}, {self.coverage[1].isoformat()})",
                ),
                FieldSection(label="date", value=self.date.key),
                FieldSection(label="is_working", value=self.is_working.key),
                FieldSection(label="snapshot_status", value=self.snapshot_status),
            )
        )
        return sections


@dataclass(frozen=True, repr=False)
class TemporalOccurrencePage(RenderableResult):
    """Bounded snapshot-bound page of exact occurrence scopes."""

    items: tuple[TimeScope, ...]
    next_cursor: str | None

    def _repr_identity(self) -> str:
        return f"TemporalOccurrencePage items={len(self.items)}"

    def _card(self) -> Card:
        return Card(
            identity=self._repr_identity(),
            available=(".items", ".next_cursor", ".show()"),
        ).table(
            columns=("scope",),
            rows=((repr(item),) for item in self.items),
            row_count=len(self.items),
        )


_CatalogObjectDetails = (
    DatasourceDetails
    | DomainDetails
    | EntityDetails
    | DimensionDetails
    | MeasureDetails
    | TimeDimensionDetails
    | MetricDetails
    | RelationshipDetails
    | EventDetails
    | StateModelDetails
    | PeriodCalendarDetails
    | TemporalSetDetails
    | WorkScheduleDetails
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
            ".ref",
            *(f".{name}" for name in self._navigation_names),
            ".details()",
            ".contract()",
            ".render()",
            ".show()",
        )
        card = (
            Card(identity=self._repr_identity(), available=available)
            .field(label="kind", value=self.kind.value)
            .field(label="path", value=self.path)
            .field(label="ref", value=self.ref.key)
            .field(
                label="business_definition",
                value=self._details.context.business_definition or "(none)",
            )
        )
        if self._details.parents:
            card = card.field(
                label="parents",
                value=_format_card_refs(self._details.parents),
            )
        owner = getattr(self._details, "owner", None)
        if isinstance(owner, str) and owner:
            card = card.field(label="owner", value=owner)
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


type _SemanticInput[KindT: SemanticKindTag] = Ref[KindT] | CatalogEntry[KindT]


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
        "events",
        "state_models",
        "period_calendars",
        "temporal_sets",
        "work_schedules",
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

    @property
    def events(self) -> CatalogCollection[EventKind]:
        return self._catalog._collection(EventEntry, SemanticKind.EVENT, scope_ref=self.ref)

    @property
    def state_models(self) -> CatalogCollection[StateModelKind]:
        return self._catalog._collection(
            StateModelEntry,
            SemanticKind.STATE_MODEL,
            scope_ref=self.ref,
        )

    @property
    def period_calendars(self) -> CatalogCollection[PeriodCalendarKind]:
        return self._catalog._collection(
            PeriodCalendarEntry,
            SemanticKind.PERIOD_CALENDAR,
            scope_ref=self.ref,
        )

    @property
    def temporal_sets(self) -> CatalogCollection[TemporalSetKind]:
        return self._catalog._collection(
            TemporalSetEntry,
            SemanticKind.TEMPORAL_SET,
            scope_ref=self.ref,
        )

    @property
    def work_schedules(self) -> CatalogCollection[WorkScheduleKind]:
        return self._catalog._collection(
            WorkScheduleEntry,
            SemanticKind.WORK_SCHEDULE,
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
        "events",
        "state_models",
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

    @property
    def events(self) -> CatalogCollection[EventKind]:
        return self._catalog._collection(EventEntry, SemanticKind.EVENT, scope_ref=self.ref)

    @property
    def state_models(self) -> CatalogCollection[StateModelKind]:
        return self._catalog._collection(
            StateModelEntry,
            SemanticKind.STATE_MODEL,
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
        card = (
            super()
            ._card()
            .field(label="composition", value=composition)
            .field(label="analysis_scope", value=scope)
            .field(
                label="effective_entities",
                value=_format_card_refs(details.effective_entities),
            )
            .field(
                label="candidate_dimensions",
                value=_format_card_refs(details.candidate_dimensions),
            )
            .field(
                label="candidate_time_dimensions",
                value=_format_card_refs(details.candidate_time_dimensions),
            )
        )
        if details.measure_lineage:
            lineage = tuple(ref for _role, ref in details.measure_lineage)
            card = card.field(label="measure_lineage", value=_format_card_refs(lineage))
        if isinstance(details, DerivedMetricDetails):
            card = card.field(
                label="required_relationships",
                value=_format_card_refs(details.required_relationships),
            )
            component_refs = tuple(ref for _role, ref in details.components)
            card = card.field(
                label="component_metrics",
                value=_format_card_refs(component_refs),
            )
        return card


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


class EventEntry(CatalogEntry[EventKind]):
    """Loaded executable Event."""

    ref: Ref[EventKind]

    def details(self) -> EventDetails:
        return cast("EventDetails", self._details)

    def _card(self) -> Card:
        details = self.details()
        visible_participants = details.participants[:6]
        card = (
            super()
            ._card()
            .field(label="source_entity", value=details.source_entity.key)
            .field(label="identity", value=_format_card_refs(details.identity))
            .field(label="occurred_at", value=details.occurred_at.key)
            .field(label="participant_count", value=str(len(details.participants)))
        )
        for participant in visible_participants:
            card = card.field(
                label=f"participant.{participant[0]}",
                value=_format_event_participant(participant, bounded=True),
            )
        omitted = len(details.participants) - len(visible_participants)
        if omitted:
            card = card.field(
                label="participants_omitted",
                value=f"{omitted}; full: .details().show()",
            )
        return card


class StateModelEntry(CatalogEntry[StateModelKind]):
    """Loaded canonical StateModel."""

    ref: Ref[StateModelKind]

    def details(self) -> StateModelDetails:
        return cast("StateModelDetails", self._details)

    def _card(self) -> Card:
        details = self.details()
        members: list[tuple[str, str]] = [
            (
                f"state.{name}",
                f"initial={initial}; terminal={terminal}",
            )
            for name, initial, terminal in details.states
        ]
        members.extend(
            (
                f"inception.{index}",
                (f"{event.key}#{role}; path=" + ("self" if not path else _format_card_refs(path))),
            )
            for index, (event, role, path) in enumerate(details.inceptions, start=1)
        )
        members.extend(
            (
                f"transition.{index}",
                (
                    f"{source} -- {event.key}#{role} --> {target}; path="
                    + ("self" if not path else _format_card_refs(path))
                ),
            )
            for index, (source, event, role, path, target) in enumerate(
                details.transitions,
                start=1,
            )
        )
        card = (
            super()
            ._card()
            .field(label="subject", value=details.subject.key)
            .field(label="state_count", value=str(len(details.states)))
            .field(
                label="transition_count",
                value=str(len(details.inceptions) + len(details.transitions)),
            )
        )
        for label, value in members[:6]:
            card = card.field(label=label, value=value)
        omitted = len(members) - min(6, len(members))
        if omitted:
            card = card.field(
                label="members_omitted",
                value=f"{omitted}; full: .details().show()",
            )
        return card


class PeriodCalendarEntry(CatalogEntry[PeriodCalendarKind]):
    """Loaded period-calendar declaration with direct semantic-grain lookup."""

    ref: Ref[PeriodCalendarKind]

    def details(self) -> PeriodCalendarDetails:
        details = cast("PeriodCalendarDetails", self._details)
        status, snapshot = self._snapshot_with_status()
        if status != "current" or snapshot is None:
            return replace(
                details,
                snapshot_status=status,
                levels=_calendar_level_details(
                    details,
                    snapshot=None,
                ),
            )
        return replace(
            details,
            snapshot_status="current",
            levels=_calendar_level_details(details, snapshot=snapshot),
        )

    def grain(self, level: str, /) -> Grain:
        """Return the common semantic Grain for one declared calendar level."""
        details = self.details()
        if level not in {item.name for item in details.levels}:
            _raise_period_lookup(
                self.ref,
                "grain",
                f"Calendar level {level!r} is not declared by {self.ref.key}.",
                details={"level": level},
            )
        from marivo._temporal import semantic_grain

        return semantic_grain(calendar=self.ref, level=level)

    def period(self, level: str, key: str | int | float | bool, /) -> TimeScope:
        """Return the exact certified scope for one named period."""
        try:
            return self._snapshot().period_scope(level, key)
        except (KeyError, TypeError, ValueError):
            _raise_period_lookup(
                self.ref,
                "period",
                f"No certified {level!r} period matches key {key!r}.",
                details={"level": level, "key": key},
            )

    def period_on(self, level: str, value: date, /) -> TimeScope:
        """Return the exact certified scope containing one civil date."""
        try:
            snapshot = self._snapshot()
            period = TemporalResolver(snapshot).period_on(level, value)
            return snapshot.period_scope(level, period.key)
        except (KeyError, TypeError, ValueError):
            _raise_period_lookup(
                self.ref,
                "period_on",
                f"No certified {level!r} period contains date {value!r}.",
                details={"level": level, "date": str(value)},
            )

    def periods(
        self,
        level: str,
        /,
        *,
        limit: int = 20,
        cursor: str | None = None,
    ) -> CalendarPeriodPage:
        """Return a bounded ordinal page of certified periods for one level."""
        if type(limit) is not int or isinstance(limit, bool) or not 1 <= limit <= 100:
            _raise_period_lookup(
                self.ref,
                "periods",
                "period page limit must be an integer in [1, 100].",
                details={"limit": limit},
            )
        snapshot = self._snapshot()
        try:
            offset = _decode_period_cursor(
                cursor, snapshot_digest=snapshot.snapshot_digest, level=level
            )
        except (TypeError, ValueError):
            _raise_period_lookup(
                self.ref,
                "periods",
                "The cursor is invalid or belongs to a different certified snapshot/level.",
                details={"level": level, "cursor": "provided"},
            )
        if level not in snapshot.levels:
            _raise_period_lookup(
                self.ref,
                "periods",
                f"Calendar level {level!r} is not certified.",
                details={"level": level},
            )
        if level == "day":
            start, end = snapshot.coverage
            total = (end - start).days
            if offset >= total and total:
                _raise_period_lookup(
                    self.ref,
                    "periods",
                    "The cursor points beyond the current certified period page.",
                    details={"level": level, "cursor": "provided", "offset": offset},
                )
            page_items = tuple(
                snapshot.period_scope(
                    level,
                    (start + timedelta(days=index)).isoformat(),
                )
                for index in range(offset, min(offset + limit, total))
            )
            next_offset = offset + len(page_items)
            next_cursor = (
                _encode_period_cursor(snapshot.snapshot_digest, level, next_offset)
                if next_offset < total
                else None
            )
            return CalendarPeriodPage(items=page_items, next_cursor=next_cursor)

        values = tuple(period for period in snapshot.periods if period.level_name == level)
        if offset >= len(values) and values:
            _raise_period_lookup(
                self.ref,
                "periods",
                "The cursor points beyond the current certified period page.",
                details={"level": level, "cursor": "provided", "offset": offset},
            )
        page_values = values[offset : offset + limit]
        next_offset = offset + len(page_values)
        next_cursor = (
            _encode_period_cursor(snapshot.snapshot_digest, level, next_offset)
            if next_offset < len(values)
            else None
        )
        return CalendarPeriodPage(
            items=tuple(snapshot.period_scope(level, value.key) for value in page_values),
            next_cursor=next_cursor,
        )

    def _snapshot(self) -> PeriodCalendarSnapshotV1:
        status, snapshot = self._snapshot_with_status()
        if status != "current" or snapshot is None:
            _raise_period_lookup(
                self.ref,
                "snapshot",
                f"{self.ref.key} has no current certified period-calendar snapshot ({status}).",
                details={"snapshot_status": status},
            )
        return snapshot

    def _snapshot_with_status(
        self,
    ) -> tuple[Literal["missing", "current", "stale", "invalid"], PeriodCalendarSnapshotV1 | None]:
        from marivo._temporal import TemporalSnapshotStore, period_calendar_definition_digest
        from marivo.semantic._definition_identity import scoped_definition_fingerprint

        details = cast("PeriodCalendarDetails", self._details)
        definition_digest = period_calendar_definition_digest(
            calendar_ref=self.ref,
            boundary_timezone=details.boundary_timezone,
            coverage=(details.coverage[0], details.coverage[1]),
            levels=tuple((name, value.path) for name, value in details._level_bindings),
            correspondences=details._correspondence_fields,
            dependency_digest=scoped_definition_fingerprint(
                root=self.ref,
                definitions=self._catalog._state.definitions,
                dependencies=self._catalog._state.dependencies,
                sidecar=self._catalog._state.sidecar,
            ),
        )
        return TemporalSnapshotStore(self._catalog.workspace_dir).inspect_current(
            self.ref,
            definition_digest=definition_digest,
        )


class TemporalSetEntry(CatalogEntry[TemporalSetKind]):
    """Loaded temporal-set declaration with exact occurrence navigation."""

    ref: Ref[TemporalSetKind]

    def _card(self) -> Card:
        details = self.details()
        return (
            Card(
                identity=self._repr_identity(),
                available=(
                    ".ref",
                    ".occurrence(key)",
                    ".occurrences(...)",
                    ".details()",
                    ".contract()",
                    ".show()",
                ),
            )
            .field(label="kind", value=self.kind.value)
            .field(label="path", value=self.path)
            .field(label="boundary_timezone", value=details.boundary_timezone)
            .field(
                label="coverage",
                value=f"[{details.coverage[0].isoformat()}, {details.coverage[1].isoformat()})",
            )
            .field(label="occurrence_count", value=str(details.occurrence_count))
            .field(label="snapshot_status", value=details.snapshot_status)
        )

    def details(self) -> TemporalSetDetails:
        details = cast("TemporalSetDetails", self._details)
        status, snapshot = self._snapshot_with_status()
        return replace(
            details,
            snapshot_status=status,
            occurrence_count=len(snapshot.occurrences)
            if status == "current" and snapshot
            else None,
        )

    def occurrence(self, key: str | int | float | bool, /) -> TimeScope:
        """Return the exact certified scope for one named occurrence."""
        try:
            return self._snapshot().occurrence_scope(key)
        except (KeyError, TypeError, ValueError):
            _raise_temporal_set_lookup(
                self.ref,
                "occurrence",
                f"No certified temporal occurrence matches key {key!r}.",
                details={"key": key},
            )

    def occurrences(
        self,
        *,
        start: date | datetime | None = None,
        end: date | datetime | None = None,
        category: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> TemporalOccurrencePage:
        """Return deterministic, bounded certified occurrence scopes."""
        if type(limit) is not int or isinstance(limit, bool) or not 1 <= limit <= 100:
            _raise_temporal_set_lookup(
                self.ref,
                "occurrences",
                "occurrence page limit must be an integer in [1, 100].",
                details={"limit": limit},
            )
        if category is not None and (type(category) is not str or not category):
            _raise_temporal_set_lookup(
                self.ref,
                "occurrences",
                "category must be a non-empty string or null.",
                details={"category": category},
            )
        snapshot = self._snapshot()
        try:
            start, end = _normalize_occurrence_filters(
                snapshot,
                start=start,
                end=end,
            )
        except (TypeError, ValueError) as exc:
            _raise_temporal_set_lookup(
                self.ref,
                "occurrences",
                str(exc),
                details={"start": repr(start), "end": repr(end)},
            )
        filter_token = _occurrence_filter_token(start=start, end=end, category=category)
        try:
            offset = _decode_occurrence_cursor(
                cursor,
                snapshot_digest=snapshot.snapshot_digest,
                filter_token=filter_token,
            )
        except (TypeError, ValueError):
            _raise_temporal_set_lookup(
                self.ref,
                "occurrences",
                "The cursor is invalid or belongs to a different snapshot/filter.",
                details={"cursor": "provided"},
            )
        values = tuple(
            occurrence
            for occurrence in snapshot.occurrences
            if _occurrence_overlaps(occurrence, start=start, end=end)
            and (category is None or occurrence.category == category)
        )
        if offset > len(values):
            _raise_temporal_set_lookup(
                self.ref,
                "occurrences",
                "The cursor points beyond the current certified occurrence page.",
                details={"offset": offset},
            )
        page_values = values[offset : offset + limit]
        next_offset = offset + len(page_values)
        next_cursor = (
            _encode_occurrence_cursor(snapshot.snapshot_digest, filter_token, next_offset)
            if next_offset < len(values)
            else None
        )
        return TemporalOccurrencePage(
            items=tuple(snapshot.occurrence_scope(item.key) for item in page_values),
            next_cursor=next_cursor,
        )

    def _snapshot(self) -> TemporalSetSnapshotV1:
        status, snapshot = self._snapshot_with_status()
        if status != "current" or snapshot is None:
            _raise_temporal_set_lookup(
                self.ref,
                "snapshot",
                f"{self.ref.key} has no current certified temporal-set snapshot ({status}).",
                details={"snapshot_status": status},
            )
        return snapshot

    def _snapshot_with_status(
        self,
    ) -> tuple[Literal["missing", "current", "stale", "invalid"], TemporalSetSnapshotV1 | None]:
        from marivo._temporal import temporal_set_definition_digest
        from marivo.semantic._definition_identity import scoped_definition_fingerprint

        details = cast("TemporalSetDetails", self._details)
        definition_digest = temporal_set_definition_digest(
            temporal_set_ref=self.ref,
            boundary_timezone=details.boundary_timezone,
            coverage=(details.coverage[0], details.coverage[1]),
            occurrence_id=details.occurrence_id.path,
            start=details.start.path,
            end=details.end.path,
            category=details.category.path if details.category is not None else None,
            dependency_digest=scoped_definition_fingerprint(
                root=self.ref,
                definitions=self._catalog._state.definitions,
                dependencies=self._catalog._state.dependencies,
                sidecar=self._catalog._state.sidecar,
            ),
        )
        return TemporalSetSnapshotStore(self._catalog.workspace_dir).inspect_current(
            self.ref,
            definition_digest=definition_digest,
        )


class WorkScheduleEntry(CatalogEntry[WorkScheduleKind]):
    """Loaded work-schedule declaration with certified-status navigation."""

    ref: Ref[WorkScheduleKind]

    def _card(self) -> Card:
        details = self.details()
        return (
            Card(
                identity=self._repr_identity(),
                available=(".ref", ".details()", ".contract()", ".show()"),
            )
            .field(label="kind", value=self.kind.value)
            .field(label="path", value=self.path)
            .field(label="boundary_timezone", value=details.boundary_timezone)
            .field(
                label="coverage",
                value=f"[{details.coverage[0].isoformat()}, {details.coverage[1].isoformat()})",
            )
            .field(label="date", value=details.date.key)
            .field(label="is_working", value=details.is_working.key)
            .field(label="snapshot_status", value=details.snapshot_status)
        )

    def details(self) -> WorkScheduleDetails:
        details = cast("WorkScheduleDetails", self._details)
        status, _snapshot = self._snapshot_with_status()
        return replace(details, snapshot_status=status)

    def _snapshot(self) -> WorkScheduleSnapshotV1:
        status, snapshot = self._snapshot_with_status()
        if status != "current" or snapshot is None:
            _raise_work_schedule_lookup(
                self.ref,
                "snapshot",
                f"{self.ref.key} has no current certified work-schedule snapshot ({status}).",
                details={"snapshot_status": status},
            )
        return snapshot

    def _snapshot_with_status(
        self,
    ) -> tuple[Literal["missing", "current", "stale", "invalid"], WorkScheduleSnapshotV1 | None]:
        from marivo._temporal import work_schedule_definition_digest
        from marivo.semantic._definition_identity import scoped_definition_fingerprint

        details = cast("WorkScheduleDetails", self._details)
        definition_digest = work_schedule_definition_digest(
            work_schedule_ref=self.ref,
            boundary_timezone=details.boundary_timezone,
            coverage=(details.coverage[0], details.coverage[1]),
            date=details.date.path,
            is_working=details.is_working.path,
            dependency_digest=scoped_definition_fingerprint(
                root=self.ref,
                definitions=self._catalog._state.definitions,
                dependencies=self._catalog._state.dependencies,
                sidecar=self._catalog._state.sidecar,
            ),
        )
        return WorkScheduleSnapshotStore(self._catalog.workspace_dir).inspect_current(
            self.ref,
            definition_digest=definition_digest,
        )


def _calendar_level_details(
    details: PeriodCalendarDetails,
    *,
    snapshot: PeriodCalendarSnapshotV1 | None,
) -> tuple[CalendarLevelDetails, ...]:
    """Project static level bindings plus certified graph facts."""
    bindings: tuple[tuple[str, Ref[DimensionKind] | Ref[TimeDimensionKind]], ...] = (
        ("day", details.date),
        *details._level_bindings,
    )
    if snapshot is None:
        return tuple(
            CalendarLevelDetails(
                name=name,
                key_ref=key_ref,
                period_count=None,
                direct_finer_levels=None,
                direct_coarser_levels=None,
                rollup_targets=None,
            )
            for name, key_ref in bindings
        )

    all_edges = {(item.source_level, item.target_level) for item in snapshot.containments}
    # Certification stores the full containment closure.  Details expose the
    # mechanically derived transitive reduction as direct edges and retain all
    # reachable targets for rollup admission.
    direct_edges = {
        edge
        for edge in all_edges
        if not any(
            edge != (edge[0], middle)
            and (edge[0], middle) in all_edges
            and (middle, edge[1]) in all_edges
            for middle in snapshot.levels
        )
    }
    finer_by_target: dict[str, tuple[str, ...]] = {
        name: tuple(sorted(source for source, target in direct_edges if target == name))
        for name, _ in bindings
    }
    coarser_by_source: dict[str, tuple[str, ...]] = {
        name: tuple(sorted(target for source, target in direct_edges if source == name))
        for name, _ in bindings
    }
    targets_by_source: dict[str, tuple[str, ...]] = {
        name: tuple(sorted(target for source, target in all_edges if source == name))
        for name, _ in bindings
    }
    counts = {
        name: (
            (details.coverage[1] - details.coverage[0]).days
            if name == "day"
            else sum(1 for item in snapshot.periods if item.level_name == name)
        )
        for name, _ in bindings
    }
    return tuple(
        CalendarLevelDetails(
            name=name,
            key_ref=key_ref,
            period_count=counts.get(name, 0),
            direct_finer_levels=finer_by_target.get(name, ()),
            direct_coarser_levels=coarser_by_source.get(name, ()),
            rollup_targets=targets_by_source.get(name, ()),
        )
        for name, key_ref in bindings
    )


def _raise_period_lookup(
    calendar_ref: Ref[PeriodCalendarKind],
    operation: str,
    message: str,
    *,
    details: Mapping[str, object],
) -> NoReturn:
    """Raise one structured, bounded-retry catalog error for temporal lookup."""
    _raise(
        ErrorKind.NOT_FOUND,
        message,
        cls=SemanticRuntimeError,
        refs=(calendar_ref.key,),
        details={"operation": operation, **dict(details)},
        repair_value=repair(
            kind="retry",
            canonical_id="period_calendar",
            action=(
                "Inspect the calendar card for a certified level/key and retry the lookup "
                "with the exact current period snapshot."
            ),
        ),
    )


def _encode_period_cursor(snapshot_digest: str, level: str, offset: int) -> str:
    payload = json.dumps([snapshot_digest, level, offset], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_period_cursor(cursor: str | None, *, snapshot_digest: str, level: str) -> int:
    if cursor is None:
        return 0
    if type(cursor) is not str or not cursor:
        raise ValueError("period cursor must be an opaque non-empty string or None")
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw_digest, raw_level, raw_offset = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("period cursor is invalid") from exc
    if (
        raw_digest != snapshot_digest
        or raw_level != level
        or type(raw_offset) is not int
        or raw_offset < 0
    ):
        raise ValueError("period cursor does not match the current certified snapshot and level")
    return raw_offset


def _raise_temporal_set_lookup(
    temporal_set_ref: Ref[TemporalSetKind],
    operation: str,
    message: str,
    *,
    details: Mapping[str, object],
) -> NoReturn:
    """Raise one structured, bounded-retry temporal-set catalog error."""
    _raise(
        ErrorKind.NOT_FOUND,
        message,
        cls=SemanticRuntimeError,
        refs=(temporal_set_ref.key,),
        details={"operation": operation, **dict(details)},
        repair_value=repair(
            kind="retry",
            canonical_id="temporal_set",
            action=(
                "Inspect the temporal-set card for a certified occurrence key/filter and "
                "retry using the exact current snapshot."
            ),
        ),
    )


def _raise_work_schedule_lookup(
    work_schedule_ref: Ref[WorkScheduleKind],
    operation: str,
    message: str,
    *,
    details: Mapping[str, object],
) -> NoReturn:
    """Raise one structured, bounded-retry work-schedule catalog error."""
    _raise(
        ErrorKind.NOT_FOUND,
        message,
        cls=SemanticRuntimeError,
        refs=(work_schedule_ref.key,),
        details={"operation": operation, **dict(details)},
        repair_value=repair(
            kind="retry",
            canonical_id="work_schedule",
            action=(
                "Inspect the work-schedule card and retry after certifying the exact current "
                "daily status snapshot."
            ),
        ),
    )


def _occurrence_filter_token(
    *,
    start: date | datetime | None,
    end: date | datetime | None,
    category: str | None,
) -> str:
    def encode(value: date | datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    return json.dumps(
        [encode(start), encode(end), category],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _normalize_occurrence_filters(
    snapshot: TemporalSetSnapshotV1,
    *,
    start: date | datetime | None,
    end: date | datetime | None,
) -> tuple[date | datetime | None, date | datetime | None]:
    """Validate and normalize overlap filters to the certified encoding."""
    if start is not None and type(start) not in {date, datetime}:
        raise ValueError("start filter must be date or datetime")
    if end is not None and type(end) not in {date, datetime}:
        raise ValueError("end filter must be date or datetime")
    if start is not None and end is not None and type(start) is not type(end):
        raise ValueError("start and end filters must use the same date or datetime encoding")
    if snapshot.encoding == "date":
        if (start is not None and type(start) is not date) or (
            end is not None and type(end) is not date
        ):
            raise ValueError("date temporal sets require date overlap filters")
    elif (start is not None and type(start) is not datetime) or (
        end is not None and type(end) is not datetime
    ):
        raise ValueError("timestamp temporal sets require datetime overlap filters")
    if start is not None and type(start) is datetime:
        from marivo._temporal import _normalize_temporal_timestamp

        start = _normalize_temporal_timestamp(start, boundary_timezone=snapshot.boundary_timezone)
    if end is not None and type(end) is datetime:
        from marivo._temporal import _normalize_temporal_timestamp

        end = _normalize_temporal_timestamp(end, boundary_timezone=snapshot.boundary_timezone)
    if start is not None and end is not None and start >= end:
        raise ValueError("occurrence filter requires start < end")
    return start, end


def _occurrence_overlaps(
    occurrence: TemporalOccurrenceRecord,
    *,
    start: date | datetime | None,
    end: date | datetime | None,
) -> bool:
    if start is None and end is None:
        return True
    if type(occurrence.start) is not type(start) and start is not None:
        raise ValueError("occurrence filter encoding does not match the certified set")
    if type(occurrence.end) is not type(end) and end is not None:
        raise ValueError("occurrence filter encoding does not match the certified set")
    return (end is None or occurrence.start < end) and (start is None or occurrence.end > start)


def _encode_occurrence_cursor(snapshot_digest: str, filter_token: str, offset: int) -> str:
    payload = json.dumps([snapshot_digest, filter_token, offset], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_occurrence_cursor(
    cursor: str | None,
    *,
    snapshot_digest: str,
    filter_token: str,
) -> int:
    if cursor is None:
        return 0
    if type(cursor) is not str or not cursor:
        raise ValueError("occurrence cursor must be an opaque non-empty string or None")
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw_digest, raw_filter, raw_offset = json.loads(base64.urlsafe_b64decode(padded))
    except (binascii.Error, UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("occurrence cursor is invalid") from exc
    if (
        raw_digest != snapshot_digest
        or raw_filter != filter_token
        or type(raw_offset) is not int
        or raw_offset < 0
    ):
        raise ValueError("occurrence cursor does not match the current certified snapshot/filter")
    return raw_offset


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
    def get(
        self: CatalogCollection[DomainKind],
        key: str | Ref[DomainKind],
    ) -> DomainEntry: ...

    @overload
    def get(
        self: CatalogCollection[DatasourceKind],
        key: str | Ref[DatasourceKind],
    ) -> DatasourceEntry: ...

    @overload
    def get(
        self: CatalogCollection[EntityKind],
        key: str | Ref[EntityKind],
    ) -> EntityEntry: ...

    @overload
    def get(
        self: CatalogCollection[DimensionKind],
        key: str | Ref[DimensionKind],
    ) -> DimensionEntry: ...

    @overload
    def get(
        self: CatalogCollection[TimeDimensionKind],
        key: str | Ref[TimeDimensionKind],
    ) -> TimeDimensionEntry: ...

    @overload
    def get(
        self: CatalogCollection[MeasureKind],
        key: str | Ref[MeasureKind],
    ) -> MeasureEntry: ...

    @overload
    def get(
        self: CatalogCollection[MetricKind],
        key: str | Ref[MetricKind],
    ) -> MetricEntry: ...

    @overload
    def get(
        self: CatalogCollection[RelationshipKind],
        key: str | Ref[RelationshipKind],
    ) -> RelationshipEntry: ...

    @overload
    def get(
        self: CatalogCollection[EventKind],
        key: str | Ref[EventKind],
    ) -> EventEntry: ...

    @overload
    def get(
        self: CatalogCollection[StateModelKind],
        key: str | Ref[StateModelKind],
    ) -> StateModelEntry: ...

    @overload
    def get(
        self: CatalogCollection[PeriodCalendarKind],
        key: str | Ref[PeriodCalendarKind],
    ) -> PeriodCalendarEntry: ...

    @overload
    def get(
        self: CatalogCollection[TemporalSetKind],
        key: str | Ref[TemporalSetKind],
    ) -> TemporalSetEntry: ...

    @overload
    def get(
        self: CatalogCollection[WorkScheduleKind],
        key: str | Ref[WorkScheduleKind],
    ) -> WorkScheduleEntry: ...

    # Overloads encode the closed KindT-to-entry mapping that Python's generic
    # syntax cannot otherwise express while the runtime signature stays CatalogEntry[K].
    def get(self, key: str | Ref[KindT]) -> CatalogEntry[KindT]:  # type: ignore[misc]
        """Return one visible member by local name, full path, or same-kind ref.

        Args:
            key: A local name, an exact full semantic path, or an exact Ref
                whose kind matches this collection.

        Returns:
            The current catalog entry visible within this collection's scope.

        Example:
            >>> revenue = catalog.metrics.get("sales.revenue")
            >>> same = catalog.metrics.get(revenue.ref)

        Constraints:
            Full paths and refs do not widen a scoped collection. Ambiguous
            local names require an explicit full path.
        """
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


def _catalog_entry_type(
    entry_type_name: str,
) -> type[CatalogEntry[SemanticKindTag]]:
    candidate = globals().get(entry_type_name)
    if not isinstance(candidate, type) or not issubclass(candidate, CatalogEntry):
        raise RuntimeError(f"invalid catalog entry type contract: {entry_type_name}")
    return cast("type[CatalogEntry[SemanticKindTag]]", candidate)


_OBJECT_TYPE_BY_KIND: dict[SemanticKind, type[CatalogEntry[SemanticKindTag]]] = {
    member.kind: _catalog_entry_type(member.entry_type_name) for member in CATALOG_MEMBER_CONTRACTS
}

_COLLECTION_PROPERTY_BY_KIND: dict[SemanticKind, str] = {
    member.kind: member.property_name for member in CATALOG_MEMBER_CONTRACTS
}

_SEMANTIC_INPUT_CANDIDATE_LIMIT = 12
_ALL_SEMANTIC_KINDS = frozenset(SemanticKind)


def _same_kind_candidates(
    catalog: SemanticCatalog,
    allowed_kinds: frozenset[SemanticKind],
) -> tuple[Ref[SemanticKindTag], ...]:
    return tuple(
        item.ref for item in catalog._require_index()._by_ref.values() if item.kind in allowed_kinds
    )[:_SEMANTIC_INPUT_CANDIDATE_LIMIT]


def _semantic_input_expected(allowed_kinds: frozenset[SemanticKind]) -> str:
    kinds = ", ".join(sorted(kind.value for kind in allowed_kinds))
    return f"exact Ref or current CatalogEntry with kind in {{{kinds}}}"


def _semantic_input_ref(value: object) -> Ref[SemanticKindTag] | None:
    if type(value) is Ref:
        return cast("Ref[SemanticKindTag]", value)
    if isinstance(value, CatalogEntry) and type(value.ref) is Ref:
        return cast("Ref[SemanticKindTag]", value.ref)
    return None


def _semantic_input_received(value: object) -> str:
    exact_ref = _semantic_input_ref(value)
    if exact_ref is not None:
        if isinstance(value, CatalogEntry):
            return f"{type(value).__name__}({exact_ref.key})"
        return f"Ref[{exact_ref.kind.value}]({exact_ref.key})"
    if isinstance(value, CatalogEntry):
        return f"{type(value).__name__}(invalid ref type {type(value.ref).__name__})"
    return type(value).__name__


def _semantic_input_inspection_repair(
    *,
    action: str,
    candidates: tuple[Ref[SemanticKindTag], ...],
) -> AuthoringRepair:
    return repair(
        kind="inspect",
        canonical_id="load",
        action=action,
        candidates=tuple(candidate.key for candidate in candidates),
    )


def _raise_invalid_semantic_input(
    *,
    catalog: SemanticCatalog,
    value: object,
    allowed_kinds: frozenset[SemanticKind],
    location: str,
    message: str,
    kind: ErrorKind = ErrorKind.INVALID_REF,
    repair_value: AuthoringRepair | None = None,
    candidate_kinds: frozenset[SemanticKind] | None = None,
) -> NoReturn:
    candidates = _same_kind_candidates(
        catalog,
        candidate_kinds if candidate_kinds is not None else allowed_kinds,
    )
    received_ref = _semantic_input_ref(value)
    _raise(
        kind,
        f"{location}: {message}",
        cls=SemanticRuntimeError,
        refs=(received_ref.key,) if received_ref is not None else (),
        expected=_semantic_input_expected(allowed_kinds),
        received=_semantic_input_received(value),
        constraint_id=ConstraintId.REF_SHAPE,
        repair_value=repair_value
        or _semantic_input_inspection_repair(
            action="Inspect the current catalog and choose an exact allowed semantic object.",
            candidates=candidates,
        ),
        details={
            "operation": location,
            "allowed_kinds": tuple(sorted(kind.value for kind in allowed_kinds)),
            "received_type": type(value).__name__,
            "catalog_definition_fingerprint": catalog.definition_fingerprint,
            "candidates": tuple(candidate.key for candidate in candidates),
        },
    )


def _normalize_semantic_input[KindT: SemanticKindTag](
    catalog: SemanticCatalog,
    value: _SemanticInput[KindT],
    *,
    allowed_kinds: frozenset[SemanticKind],
    location: str,
) -> Ref[KindT]:
    """Normalize one exact current-catalog entry or ref to its canonical ref."""
    if not allowed_kinds:
        raise ValueError("allowed_kinds must not be empty")

    entry: CatalogEntry[SemanticKindTag] | None = None
    if type(value) is Ref:
        exact_ref = cast("Ref[SemanticKindTag]", value)
    elif isinstance(value, CatalogEntry):
        entry = cast("CatalogEntry[SemanticKindTag]", value)
        if type(entry.ref) is not Ref:
            _raise_invalid_semantic_input(
                catalog=catalog,
                value=value,
                allowed_kinds=allowed_kinds,
                location=location,
                message="the entry does not carry an exact sealed Ref value.",
            )
        exact_ref = entry.ref
        expected_entry_type = _OBJECT_TYPE_BY_KIND.get(exact_ref.kind)
        if type(entry) is not expected_entry_type:
            _raise_invalid_semantic_input(
                catalog=catalog,
                value=value,
                allowed_kinds=allowed_kinds,
                location=location,
                message=(
                    f"{type(value).__name__} is not a registered concrete catalog entry class."
                ),
            )
        if entry._catalog is not catalog:
            entry_kind = frozenset({exact_ref.kind})
            candidates = _same_kind_candidates(catalog, entry_kind)
            same_semantic_root = entry._catalog.semantic_root == catalog.semantic_root
            current = catalog._require_index().require(exact_ref)
            if (
                same_semantic_root
                and current is not None
                and current.kind in allowed_kinds
                and type(current) is expected_entry_type
            ):
                collection = _COLLECTION_PROPERTY_BY_KIND[exact_ref.kind]
                snippet = f"entry = catalog.{collection}.get({exact_ref.path!r})"
                _raise_invalid_semantic_input(
                    catalog=catalog,
                    value=value,
                    allowed_kinds=allowed_kinds,
                    location=location,
                    message=(
                        "the entry belongs to an earlier catalog instance for the same "
                        "semantic root; reacquire it from the current catalog."
                    ),
                    repair_value=repair(
                        kind="reacquire",
                        canonical_id="load",
                        action="Reacquire the exact same-kind path from the current catalog.",
                        snippet=snippet,
                        candidates=(exact_ref.key,),
                    ),
                    candidate_kinds=entry_kind,
                )
            ownership = "stale catalog instance" if same_semantic_root else "another catalog"
            _raise_invalid_semantic_input(
                catalog=catalog,
                value=value,
                allowed_kinds=allowed_kinds,
                location=location,
                message=(
                    f"the entry belongs to {ownership}; inspect the current catalog "
                    "without rebasing the old object."
                ),
                repair_value=_semantic_input_inspection_repair(
                    action="Inspect current same-kind catalog entries and choose explicitly.",
                    candidates=candidates,
                ),
                candidate_kinds=entry_kind,
            )
    else:
        _raise_invalid_semantic_input(
            catalog=catalog,
            value=value,
            allowed_kinds=allowed_kinds,
            location=location,
            message=(
                "expected an exact Ref or registered CatalogEntry; bare strings and "
                "duck-typed objects are not accepted."
            ),
        )

    if exact_ref.kind not in allowed_kinds:
        _raise_invalid_semantic_input(
            catalog=catalog,
            value=value,
            allowed_kinds=allowed_kinds,
            location=location,
            message=(
                f"received semantic kind {exact_ref.kind.value}; expected one of "
                f"{tuple(sorted(kind.value for kind in allowed_kinds))}."
            ),
        )

    current = catalog._require_index().require(exact_ref)
    if current is None:
        _raise_invalid_semantic_input(
            catalog=catalog,
            value=value,
            allowed_kinds=allowed_kinds,
            location=location,
            kind=ErrorKind.NOT_FOUND,
            message="the semantic ref is not a member of the current compiled catalog.",
            candidate_kinds=frozenset({exact_ref.kind}),
        )
    if entry is not None and current is not entry:
        _raise_invalid_semantic_input(
            catalog=catalog,
            value=value,
            allowed_kinds=allowed_kinds,
            location=location,
            message="the entry was not produced by the current compiled catalog.",
            candidate_kinds=frozenset({exact_ref.kind}),
        )
    return cast("Ref[KindT]", current.ref)


def _require_semantic_ref(value: object, *, parameter: str) -> Ref[SemanticKindTag]:
    if type(value) is Ref:
        return cast("Ref[SemanticKindTag]", value)
    _raise(
        ErrorKind.INVALID_REF,
        f"catalog.{parameter} requires an exact Ref[kind]; received {type(value).__name__}. "
        "Pass entry.ref when starting from catalog navigation, or construct one "
        "with the exact ms.ref.<kind>(path) factory.",
        cls=SemanticRuntimeError,
        constraint_id=ConstraintId.REF_SHAPE,
        details={
            "operation": f"catalog.{parameter}",
            "expected": "exact Ref[kind]",
            "received_type": type(value).__name__,
        },
    )


def _require_readiness_input(
    catalog: SemanticCatalog,
    value: object,
) -> Ref[SemanticKindTag] | RuntimeMetricExpr:
    if isinstance(
        value,
        (RuntimeAggregateExpr, RuntimeSliceExpr, RuntimeRatioExpr, RuntimeWeightedMeanExpr),
    ):
        return value
    if type(value) is Ref or isinstance(value, CatalogEntry):
        return _normalize_semantic_input(
            catalog,
            cast("_SemanticInput[SemanticKindTag]", value),
            allowed_kinds=_ALL_SEMANTIC_KINDS,
            location="catalog.readiness(refs=...)",
        )
    _raise(
        ErrorKind.INVALID_REF,
        "catalog.readiness(refs=...) requires an exact current CatalogEntry, "
        f"Ref[kind], or closed RuntimeMetricExpr; received {type(value).__name__}.",
        cls=SemanticRuntimeError,
        constraint_id=ConstraintId.REF_SHAPE,
        hint=(
            "Pass a current catalog entry or exact ms.ref.<kind>(path). For "
            "session-scoped metrics, "
            "pass the closed value returned by mv.runtime_metric.aggregate(...), "
            "weighted_mean(...), slice(...), or ratio(...)."
        ),
        details={
            "operation": "catalog.readiness(refs=...)",
            "expected": "current CatalogEntry, exact Ref[kind], or RuntimeMetricExpr",
            "received_type": type(value).__name__,
        },
    )


def _normalize_location(loc: SourceLocation | DatasourceSourceLocation) -> SourceLocation:
    return SourceLocation(file=loc.file, line=loc.line)


def _build_datasource_object(
    ds_ir: DatasourceIR, reg: Registry, catalog: SemanticCatalog
) -> DatasourceEntry:
    ref = ref_factory.datasource(ds_ir.semantic_id)
    dependents = tuple(
        _make_ref(d.semantic_id, SemanticKind.ENTITY)
        for d in reg.entities.values()
        if ref_factory.datasource(d.datasource) == ref
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
    event_refs = tuple(
        _make_ref(event.semantic_id, SemanticKind.EVENT)
        for event in reg.events.values()
        if event.domain == model_ir.name
    )
    state_model_refs = tuple(
        _make_ref(model.semantic_id, SemanticKind.STATE_MODEL)
        for model in reg.state_models.values()
        if model.domain == model_ir.name
    )
    period_calendar_refs = tuple(
        _make_ref(calendar.semantic_id, SemanticKind.PERIOD_CALENDAR)
        for calendar in reg.period_calendars.values()
        if calendar.domain == model_ir.name
    )
    temporal_set_refs = tuple(
        _make_ref(temporal_set.semantic_id, SemanticKind.TEMPORAL_SET)
        for temporal_set in reg.temporal_sets.values()
        if temporal_set.domain == model_ir.name
    )
    work_schedule_refs = tuple(
        _make_ref(schedule.semantic_id, SemanticKind.WORK_SCHEDULE)
        for schedule in reg.work_schedules.values()
        if schedule.domain == model_ir.name
    )
    children = (
        datasets_refs
        + metrics_refs
        + event_refs
        + state_model_refs
        + period_calendar_refs
        + temporal_set_refs
        + work_schedule_refs
    )
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
    ds_ref = ref_factory.datasource(ds_ir.datasource)
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
    event_refs = tuple(
        _make_ref(event.semantic_id, SemanticKind.EVENT)
        for event in reg.events.values()
        if event.source_entity == ds_ir.semantic_id
    )
    state_model_refs = tuple(
        _make_ref(model.semantic_id, SemanticKind.STATE_MODEL)
        for model in reg.state_models.values()
        if model.subject == ds_ir.semantic_id
    )
    children = fields_refs + measure_refs + metric_refs + rels_refs + event_refs + state_model_refs
    metric_dependents = tuple(
        _make_ref(m.semantic_id, SemanticKind.METRIC)
        for m in reg.metrics.values()
        if ds_ir.semantic_id in m.entities
    )
    state_model_dependents = tuple(
        _make_ref(model.semantic_id, SemanticKind.STATE_MODEL)
        for model in reg.state_models.values()
        if model.subject == ds_ir.semantic_id
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
        dependents=metric_dependents + state_model_dependents,
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


def _event_participant_endpoint(
    event_ir: EventIR,
    path: tuple[str, ...] | None,
    registry: Registry,
) -> str:
    endpoint = event_ir.source_entity
    for relationship_id in path or ():
        endpoint = registry.relationships[relationship_id].to_entity
    return endpoint


def _validate_event_preview(
    result: PreviewResult,
    *,
    event_ir: EventIR,
    participants: tuple[EventParticipantIR, ...],
) -> PreviewResult:
    identity_columns = tuple(f"__event_identity_{index}" for index in range(len(event_ir.identity)))
    observed: dict[
        tuple[object, ...],
        dict[str, list[tuple[object, ...]]],
    ] = {}
    for row in result.rows:
        identity = tuple(row.get(column) for column in identity_columns)
        if any(value is None for value in identity):
            _raise(
                ErrorKind.INVALID_EVENT_IDENTITY,
                f"Event {event_ir.semantic_id!r} preview observed a null identity component.",
                cls=SemanticRuntimeError,
                refs=(event_ir.semantic_id, *event_ir.identity),
                expected="a non-null occurrence identity tuple",
                received=repr(identity),
            )
        subjects_by_role = observed.setdefault(identity, {})
        for participant in participants:
            subject_prefix = f"__subject_{participant.name}_identity_"
            subject_columns = tuple(
                column for column in result.columns if column.startswith(subject_prefix)
            )
            subject = tuple(row.get(column) for column in subject_columns)
            if participant.cardinality == "one" and (
                not subject_columns or any(value is None for value in subject)
            ):
                _raise(
                    ErrorKind.INVALID_EVENT_PARTICIPANT_CARDINALITY,
                    (
                        f"Event {event_ir.semantic_id!r} participant "
                        f"{participant.name!r} preview did not resolve one subject."
                    ),
                    cls=SemanticRuntimeError,
                    refs=(event_ir.semantic_id,),
                    expected="one non-null endpoint primary-key tuple per occurrence",
                    received=repr(subject),
                )
            subjects_by_role.setdefault(participant.name, []).append(subject)
    for identity, subjects_by_role in observed.items():
        row_count = max((len(values) for values in subjects_by_role.values()), default=0)
        if row_count < 2:
            continue
        fanned_out = next(
            (
                (role, subjects)
                for role, subjects in subjects_by_role.items()
                if len(set(subjects)) > 1
            ),
            None,
        )
        if fanned_out is not None:
            role, subjects = fanned_out
            _raise(
                ErrorKind.INVALID_EVENT_PARTICIPANT_CARDINALITY,
                (
                    f"Event {event_ir.semantic_id!r} participant "
                    f"{role!r} preview fanned out one occurrence."
                ),
                cls=SemanticRuntimeError,
                refs=(event_ir.semantic_id,),
                expected="at most one subject identity per Event identity",
                received=repr({"event_identity": identity, "subjects": subjects}),
            )
        _raise(
            ErrorKind.INVALID_EVENT_IDENTITY,
            f"Event {event_ir.semantic_id!r} preview observed a duplicate occurrence identity.",
            cls=SemanticRuntimeError,
            refs=(event_ir.semantic_id, *event_ir.identity),
            expected="unique Event identity after applying the Event predicate",
            received=repr(identity),
        )
    return result


def _build_event_object(
    event_ir: EventIR,
    reg: Registry,
    catalog: SemanticCatalog,
) -> EventEntry:
    ref = ref_factory.event(event_ir.semantic_id)
    source_ref = ref_factory.entity(event_ir.source_entity)
    identity = tuple(ref_factory.dimension(path) for path in event_ir.identity)
    occurred_at = ref_factory.time_dimension(event_ir.occurred_at)
    participants = tuple(
        (
            participant.name,
            ref_factory.entity(_event_participant_endpoint(event_ir, participant.path, reg)),
            participant.cardinality,
            tuple(ref_factory.relationship(path) for path in participant.path or ()),
        )
        for participant in event_ir.participants
    )
    body = catalog._state.sidecar.bodies.get(ref)
    predicate_dimensions = (
        tuple(ref_factory.dimension(binding.field_ref.path) for binding in body.bindings)
        if body is not None
        else ()
    )
    parents = tuple(
        dict.fromkeys(
            (
                source_ref,
                occurred_at,
                *identity,
                *predicate_dimensions,
                *(
                    relationship
                    for _name, _endpoint, _cardinality, path in participants
                    for relationship in path
                ),
            )
        )
    )
    dependents = tuple(
        ref_factory.state_model(model.semantic_id)
        for model in reg.state_models.values()
        if any(item.trigger.event_ref == event_ir.semantic_id for item in model.inceptions)
        or any(item.trigger.event_ref == event_ir.semantic_id for item in model.transitions)
    )
    details = EventDetails(
        ref=ref,
        kind=SemanticKind.EVENT,
        name=event_ir.name,
        domain=event_ir.domain,
        context=event_ir.ai_context,
        source_location=event_ir.location,
        parents=parents,
        children=(),
        dependents=dependents,
        python_symbol=event_ir.python_symbol,
        source_entity=source_ref,
        identity=identity,
        occurred_at=occurred_at,
        participants=participants,
        predicate_kind=event_ir.predicate_kind,
        definition_fingerprint=_event_definition_fingerprint(
            ref,
            registry=reg,
            sidecar=catalog._state.sidecar,
        ),
    )
    return _object_from_details(EventEntry, details, catalog)


def _build_state_model_object(
    model_ir: StateModelIR,
    reg: Registry,
    catalog: SemanticCatalog,
) -> StateModelEntry:
    ref = ref_factory.state_model(model_ir.semantic_id)
    subject = ref_factory.entity(model_ir.subject)
    event_refs = tuple(
        dict.fromkeys(
            (
                *(item.trigger.event_ref for item in model_ir.inceptions),
                *(item.trigger.event_ref for item in model_ir.transitions),
            )
        )
    )
    events = tuple(ref_factory.event(path) for path in event_refs)

    def trigger_path(event_ref: str, role: str) -> tuple[Ref[RelationshipKind], ...]:
        event = reg.events[event_ref]
        participant = next(item for item in event.participants if item.name == role)
        return tuple(ref_factory.relationship(path) for path in participant.path or ())

    details = StateModelDetails(
        ref=ref,
        kind=SemanticKind.STATE_MODEL,
        name=model_ir.name,
        domain=model_ir.domain,
        context=model_ir.ai_context,
        source_location=model_ir.location,
        parents=(subject, *events),
        children=(),
        dependents=(),
        python_symbol=model_ir.python_symbol,
        subject=subject,
        states=tuple((state.name, state.initial, state.terminal) for state in model_ir.states),
        inceptions=tuple(
            (
                ref_factory.event(item.trigger.event_ref),
                item.trigger.participant_role,
                trigger_path(
                    item.trigger.event_ref,
                    item.trigger.participant_role,
                ),
            )
            for item in model_ir.inceptions
        ),
        transitions=tuple(
            (
                item.from_state,
                ref_factory.event(item.trigger.event_ref),
                item.trigger.participant_role,
                trigger_path(
                    item.trigger.event_ref,
                    item.trigger.participant_role,
                ),
                item.to_state,
            )
            for item in model_ir.transitions
        ),
        definition_fingerprint=_state_model_fingerprint(
            ref,
            registry=reg,
            sidecar=catalog._state.sidecar,
        ),
    )
    return _object_from_details(StateModelEntry, details, catalog)


def _build_period_calendar_object(
    calendar_ir: PeriodCalendarIR,
    reg: Registry,
    catalog: SemanticCatalog,
) -> PeriodCalendarEntry:
    ref = ref_factory.period_calendar(calendar_ir.semantic_id)
    date_ref = ref_factory.time_dimension(calendar_ir.date)
    levels = tuple((name, ref_factory.dimension(path)) for name, path in calendar_ir.levels)
    correspondences = {name: level for name, level, _baseline in calendar_ir.correspondences}
    correspondence_refs = tuple(
        ref_factory.dimension(baseline) for _name, _level, baseline in calendar_ir.correspondences
    )
    parents = (date_ref, *(value for _name, value in levels), *correspondence_refs)
    # Slice 1 has no metric IR edge that can reference a semantic calendar.
    # Do not turn unrelated cumulative anchors into fabricated dependents;
    # reverse edges will be added when Slice 2 persists calendar Grain refs.
    dependents: tuple[Ref[SemanticKindTag], ...] = ()
    declared_levels = tuple((name, value) for name, value in levels)
    details = PeriodCalendarDetails(
        ref=ref,
        kind=SemanticKind.PERIOD_CALENDAR,
        name=calendar_ir.name,
        domain=calendar_ir.domain,
        context=calendar_ir.ai_context,
        source_location=calendar_ir.location,
        parents=parents,
        children=(),
        dependents=dependents,
        python_symbol=calendar_ir.python_symbol,
        date=date_ref,
        boundary_timezone=calendar_ir.boundary_timezone,
        coverage=(
            date.fromisoformat(calendar_ir.coverage[0]),
            date.fromisoformat(calendar_ir.coverage[1]),
        ),
        levels=tuple(
            CalendarLevelDetails(
                name=name,
                key_ref=cast("Ref[DimensionKind] | Ref[TimeDimensionKind]", value),
                period_count=None,
                direct_finer_levels=None,
                direct_coarser_levels=None,
                rollup_targets=None,
            )
            for name, value in (("day", date_ref), *declared_levels)
        ),
        correspondences=MappingProxyType(correspondences),
        _correspondence_fields=calendar_ir.correspondences,
        _level_bindings=declared_levels,
        snapshot_status="missing",
    )
    return _object_from_details(PeriodCalendarEntry, details, catalog)


def _build_temporal_set_object(
    temporal_set_ir: TemporalSetIR,
    reg: Registry,
    catalog: SemanticCatalog,
) -> TemporalSetEntry:
    ref = ref_factory.temporal_set(temporal_set_ir.semantic_id)
    occurrence_ref = ref_factory.dimension(temporal_set_ir.occurrence_id)
    start_ref = ref_factory.time_dimension(temporal_set_ir.start)
    end_ref = ref_factory.time_dimension(temporal_set_ir.end)
    category_ref = (
        ref_factory.dimension(temporal_set_ir.category)
        if temporal_set_ir.category is not None
        else None
    )
    parents = tuple(
        value for value in (occurrence_ref, start_ref, end_ref, category_ref) if value is not None
    )
    details = TemporalSetDetails(
        ref=ref,
        kind=SemanticKind.TEMPORAL_SET,
        name=temporal_set_ir.name,
        domain=temporal_set_ir.domain,
        context=temporal_set_ir.ai_context,
        source_location=temporal_set_ir.location,
        parents=parents,
        children=(),
        dependents=(),
        python_symbol=temporal_set_ir.python_symbol,
        boundary_timezone=temporal_set_ir.boundary_timezone,
        coverage=(
            date.fromisoformat(temporal_set_ir.coverage[0]),
            date.fromisoformat(temporal_set_ir.coverage[1]),
        ),
        occurrence_id=occurrence_ref,
        start=start_ref,
        end=end_ref,
        category=category_ref,
        occurrence_count=None,
        snapshot_status="missing",
    )
    return _object_from_details(TemporalSetEntry, details, catalog)


def _build_work_schedule_object(
    work_schedule_ir: WorkScheduleIR,
    reg: Registry,
    catalog: SemanticCatalog,
) -> WorkScheduleEntry:
    ref = ref_factory.work_schedule(work_schedule_ir.semantic_id)
    date_ref = ref_factory.time_dimension(work_schedule_ir.date)
    working_ref = ref_factory.dimension(work_schedule_ir.is_working)
    details = WorkScheduleDetails(
        ref=ref,
        kind=SemanticKind.WORK_SCHEDULE,
        name=work_schedule_ir.name,
        domain=work_schedule_ir.domain,
        context=work_schedule_ir.ai_context,
        source_location=work_schedule_ir.location,
        parents=(date_ref, working_ref),
        children=(),
        dependents=(),
        python_symbol=work_schedule_ir.python_symbol,
        boundary_timezone=work_schedule_ir.boundary_timezone,
        coverage=(
            date.fromisoformat(work_schedule_ir.coverage[0]),
            date.fromisoformat(work_schedule_ir.coverage[1]),
        ),
        date=date_ref,
        is_working=working_ref,
        snapshot_status="missing",
    )
    return _object_from_details(WorkScheduleEntry, details, catalog)


def _preview_temporal_set(
    *,
    temporal_set_ref: Ref[TemporalSetKind],
    registry: Registry,
    project_root: Path,
    using: PreviewUsing,
    limit: int,
    dependency_digest: str,
) -> PreviewResult:
    """Certify a temporal set locally from one exhaustive persisted snapshot."""
    preview_limit = validate_preview_limit(limit)
    if not isinstance(using, DiscoverySnapshot):
        _raise(
            ErrorKind.MATERIALIZE_FAILED,
            "Temporal-set preview requires exactly one DiscoverySnapshot in using=.",
            cls=SemanticRuntimeError,
            refs=(temporal_set_ref.key,),
            details={"query_executed": False},
        )
    if using._project_root.resolve() != project_root.resolve():
        _raise(
            ErrorKind.MATERIALIZE_FAILED,
            "Temporal-set snapshot belongs to a different semantic project.",
            cls=SemanticRuntimeError,
            refs=(temporal_set_ref.key,),
            details={"query_executed": False},
        )
    if using.cache_status in {"stale", "mismatched"} or using.expires_at <= datetime.now(UTC):
        _raise(
            ErrorKind.MATERIALIZE_FAILED,
            "Temporal-set preview cannot certify stale or expired datasource evidence.",
            cls=SemanticRuntimeError,
            refs=(temporal_set_ref.key,),
            details={"query_executed": False, "cache_status": using.cache_status},
        )
    if using.value_evidence_state != "available" or not using.retained_values:
        _raise(
            ErrorKind.MATERIALIZE_FAILED,
            "Temporal-set preview requires retained persisted value evidence.",
            cls=SemanticRuntimeError,
            refs=(temporal_set_ref.key,),
            details={
                "query_executed": False,
                "value_evidence_state": using.value_evidence_state,
                "retained_row_count": len(using.retained_values),
            },
        )
    temporal_set = registry.temporal_sets.get(temporal_set_ref.path)
    if temporal_set is None:
        raise RuntimeError(f"missing compiled temporal set {temporal_set_ref.path!r}")
    occurrence_field = registry.dimensions.get(temporal_set.occurrence_id)
    start_field = registry.dimensions.get(temporal_set.start)
    end_field = registry.dimensions.get(temporal_set.end)
    category_field = (
        registry.dimensions.get(temporal_set.category)
        if temporal_set.category is not None
        else None
    )
    if (
        occurrence_field is None
        or start_field is None
        or end_field is None
        or occurrence_field.source_column is None
        or start_field.source_column is None
        or end_field.source_column is None
        or (category_field is not None and category_field.source_column is None)
    ):
        _raise(
            ErrorKind.MATERIALIZE_FAILED,
            "Temporal-set fields must be declared with physical source columns.",
            cls=SemanticRuntimeError,
            refs=(temporal_set_ref.key,),
            details={"query_executed": False},
        )
    source_entity = registry.entities.get(occurrence_field.entity)
    if (
        source_entity is None
        or using.datasource.path != source_entity.datasource
        or using.source != source_entity.source
        or using.coverage.scope_exhaustion != "exhaustive"
        or not using.persist_values
    ):
        _raise(
            ErrorKind.MATERIALIZE_FAILED,
            "Temporal-set preview requires an exhaustive snapshot from the occurrence entity source with persist_values=True.",
            cls=SemanticRuntimeError,
            refs=(temporal_set_ref.key,),
            details={
                "query_executed": False,
                "scope_exhaustion": using.coverage.scope_exhaustion,
                "persist_values": using.persist_values,
            },
        )
    from marivo._temporal import (
        TemporalSetSnapshotStore,
        certify_temporal_set_rows,
        temporal_set_definition_digest,
    )

    try:
        snapshot = certify_temporal_set_rows(
            temporal_set_ref=temporal_set_ref,
            boundary_timezone=temporal_set.boundary_timezone,
            coverage=(
                date.fromisoformat(temporal_set.coverage[0]),
                date.fromisoformat(temporal_set.coverage[1]),
            ),
            columns=using.columns,
            retained_values=using.retained_values,
            occurrence_id=occurrence_field.source_column,
            start=start_field.source_column,
            end=end_field.source_column,
            category=category_field.source_column if category_field is not None else None,
            start_parse=start_field.parse,
            end_parse=end_field.parse,
        )
    except (KeyError, TypeError, ValueError) as exc:
        _raise(
            ErrorKind.MATERIALIZE_FAILED,
            f"Temporal-set certification failed: {exc}",
            cls=SemanticRuntimeError,
            refs=(temporal_set_ref.key,),
            details={"query_executed": False, "certification_error": type(exc).__name__},
        )
    definition_digest = temporal_set_definition_digest(
        temporal_set_ref=temporal_set_ref,
        boundary_timezone=temporal_set.boundary_timezone,
        coverage=temporal_set.coverage,
        occurrence_id=temporal_set.occurrence_id,
        start=temporal_set.start,
        end=temporal_set.end,
        category=temporal_set.category,
        dependency_digest=dependency_digest,
    )
    TemporalSetSnapshotStore(project_root).publish(snapshot, definition_digest=definition_digest)
    rows: tuple[dict[str, object], ...] = tuple(
        {
            "key": item.key,
            "start": item.start.isoformat(),
            "end": item.end.isoformat(),
            "category": item.category,
        }
        for item in snapshot.occurrences[:preview_limit]
    )
    return PreviewResult(
        kind="semantic_dataset",
        ref=temporal_set_ref.path,
        columns=("key", "start", "end", "category"),
        types={
            "key": "json_scalar",
            "start": snapshot.encoding,
            "end": snapshot.encoding,
            "category": "string|null",
        },
        rows=rows,
        requested_limit=preview_limit,
        returned_row_count=len(rows),
        is_truncated=len(snapshot.occurrences) > preview_limit,
        status="passed",
        coverage=PreviewCoverage(
            scopes=((occurrence_field.entity, using.scope),),
            rows_observed=using.coverage.observed_row_count,
            scope_exhaustion=using.coverage.scope_exhaustion,
            scope_exactness=using.coverage.scope_exactness,
            snapshot_ids=(using.id,),
            cache_status="fresh" if using.cache_status == "mismatched" else using.cache_status,
        ),
        sample_policy=PreviewSamplePolicy(method="bounded_limit", limit=preview_limit),
    )


def _preview_work_schedule(
    *,
    work_schedule_ref: Ref[WorkScheduleKind],
    registry: Registry,
    project_root: Path,
    using: PreviewUsing,
    limit: int,
    dependency_digest: str,
) -> PreviewResult:
    """Certify a work schedule locally from one exhaustive persisted snapshot."""
    preview_limit = validate_preview_limit(limit)
    if not isinstance(using, DiscoverySnapshot):
        _raise(
            ErrorKind.MATERIALIZE_FAILED,
            "Work-schedule preview requires exactly one DiscoverySnapshot in using=.",
            cls=SemanticRuntimeError,
            refs=(work_schedule_ref.key,),
            details={"query_executed": False},
        )
    if using._project_root.resolve() != project_root.resolve():
        _raise(
            ErrorKind.MATERIALIZE_FAILED,
            "Work-schedule snapshot belongs to a different semantic project.",
            cls=SemanticRuntimeError,
            refs=(work_schedule_ref.key,),
            details={"query_executed": False},
        )
    if using.cache_status in {"stale", "mismatched"} or using.expires_at <= datetime.now(UTC):
        _raise(
            ErrorKind.MATERIALIZE_FAILED,
            "Work-schedule preview cannot certify stale or expired datasource evidence.",
            cls=SemanticRuntimeError,
            refs=(work_schedule_ref.key,),
            details={"query_executed": False, "cache_status": using.cache_status},
        )
    if using.value_evidence_state != "available" or not using.retained_values:
        _raise(
            ErrorKind.MATERIALIZE_FAILED,
            "Work-schedule preview requires retained persisted value evidence.",
            cls=SemanticRuntimeError,
            refs=(work_schedule_ref.key,),
            details={
                "query_executed": False,
                "value_evidence_state": using.value_evidence_state,
                "retained_row_count": len(using.retained_values),
            },
        )
    schedule = registry.work_schedules.get(work_schedule_ref.path)
    if schedule is None:
        raise RuntimeError(f"missing compiled work schedule {work_schedule_ref.path!r}")
    date_field = registry.dimensions.get(schedule.date)
    working_field = registry.dimensions.get(schedule.is_working)
    if (
        date_field is None
        or working_field is None
        or date_field.source_column is None
        or working_field.source_column is None
    ):
        _raise(
            ErrorKind.MATERIALIZE_FAILED,
            "Work-schedule fields must be declared with physical source columns.",
            cls=SemanticRuntimeError,
            refs=(work_schedule_ref.key,),
            details={"query_executed": False},
        )
    source_entity = registry.entities.get(date_field.entity)
    if (
        source_entity is None
        or using.datasource.path != source_entity.datasource
        or using.source != source_entity.source
        or using.coverage.scope_exhaustion != "exhaustive"
        or not using.persist_values
    ):
        _raise(
            ErrorKind.MATERIALIZE_FAILED,
            "Work-schedule preview requires an exhaustive snapshot from the date entity source with persist_values=True.",
            cls=SemanticRuntimeError,
            refs=(work_schedule_ref.key,),
            details={
                "query_executed": False,
                "scope_exhaustion": using.coverage.scope_exhaustion,
                "persist_values": using.persist_values,
            },
        )
    from marivo._temporal import (
        WorkScheduleSnapshotStore,
        certify_work_schedule_rows,
        work_schedule_definition_digest,
    )

    try:
        snapshot = certify_work_schedule_rows(
            work_schedule_ref=work_schedule_ref,
            boundary_timezone=schedule.boundary_timezone,
            coverage=(
                date.fromisoformat(schedule.coverage[0]),
                date.fromisoformat(schedule.coverage[1]),
            ),
            columns=using.columns,
            retained_values=using.retained_values,
            date_column=date_field.source_column,
            is_working=working_field.source_column,
            date_parse=date_field.parse,
        )
    except (KeyError, TypeError, ValueError) as exc:
        _raise(
            ErrorKind.MATERIALIZE_FAILED,
            f"Work-schedule certification failed: {exc}",
            cls=SemanticRuntimeError,
            refs=(work_schedule_ref.key,),
            details={"query_executed": False, "certification_error": type(exc).__name__},
        )
    definition_digest = work_schedule_definition_digest(
        work_schedule_ref=work_schedule_ref,
        boundary_timezone=schedule.boundary_timezone,
        coverage=schedule.coverage,
        date=schedule.date,
        is_working=schedule.is_working,
        dependency_digest=dependency_digest,
    )
    WorkScheduleSnapshotStore(project_root).publish(snapshot, definition_digest=definition_digest)
    rows: tuple[dict[str, object], ...] = tuple(
        {"date": item.date.isoformat(), "is_working": item.is_working}
        for item in snapshot.days[:preview_limit]
    )
    return PreviewResult(
        kind="semantic_dataset",
        ref=work_schedule_ref.path,
        columns=("date", "is_working"),
        types={"date": "date", "is_working": "boolean"},
        rows=rows,
        requested_limit=preview_limit,
        returned_row_count=len(rows),
        is_truncated=len(snapshot.days) > preview_limit,
        status="passed",
        coverage=PreviewCoverage(
            scopes=((date_field.entity, using.scope),),
            rows_observed=using.coverage.observed_row_count,
            scope_exhaustion=using.coverage.scope_exhaustion,
            scope_exactness=using.coverage.scope_exactness,
            snapshot_ids=(using.id,),
            cache_status="fresh" if using.cache_status == "mismatched" else using.cache_status,
        ),
        sample_policy=PreviewSamplePolicy(method="bounded_limit", limit=preview_limit),
    )


def _preview_period_calendar(
    *,
    calendar_ref: Ref[PeriodCalendarKind],
    registry: Registry,
    project_root: Path,
    using: PreviewUsing,
    limit: int,
    dependency_digest: str,
) -> PreviewResult:
    """Certify a calendar locally from one exhaustive persisted datasource snapshot."""
    preview_limit = validate_preview_limit(limit)
    if not isinstance(using, DiscoverySnapshot):
        _raise(
            ErrorKind.MATERIALIZE_FAILED,
            "Period-calendar preview requires exactly one DiscoverySnapshot in using=.",
            cls=SemanticRuntimeError,
            refs=(calendar_ref.key,),
            details={"query_executed": False},
        )
    if using._project_root.resolve() != project_root.resolve():
        _raise(
            ErrorKind.MATERIALIZE_FAILED,
            "Period-calendar snapshot belongs to a different semantic project.",
            cls=SemanticRuntimeError,
            refs=(calendar_ref.key,),
            details={
                "query_executed": False,
                "expected_project_root": str(project_root.resolve()),
                "received_project_root": str(using._project_root.resolve()),
            },
            repair_value=repair(
                kind="reacquire",
                canonical_id="SourceInspection.sample",
                action=(
                    "Acquire the exhaustive calendar snapshot from this project and pass "
                    "that exact immutable value to catalog.preview(..., using=...)."
                ),
                preserves_evidence=False,
            ),
        )
    if using.cache_status in {"stale", "mismatched"} or using.expires_at <= datetime.now(UTC):
        _raise(
            ErrorKind.MATERIALIZE_FAILED,
            "Period-calendar preview cannot certify stale or expired datasource evidence.",
            cls=SemanticRuntimeError,
            refs=(calendar_ref.key,),
            details={
                "query_executed": False,
                "cache_status": using.cache_status,
                "expires_at": using.expires_at.isoformat(),
            },
            repair_value=repair(
                kind="reacquire",
                canonical_id="SourceInspection.sample",
                action=(
                    "Reacquire a fresh exhaustive snapshot with persist_values=True, then "
                    "retry the calendar preview."
                ),
                preserves_evidence=False,
            ),
        )
    if using.value_evidence_state != "available" or not using.retained_values:
        _raise(
            ErrorKind.MATERIALIZE_FAILED,
            "Period-calendar preview requires retained persisted value evidence.",
            cls=SemanticRuntimeError,
            refs=(calendar_ref.key,),
            details={
                "query_executed": False,
                "value_evidence_state": using.value_evidence_state,
                "retained_row_count": len(using.retained_values),
            },
            repair_value=repair(
                kind="reacquire",
                canonical_id="SourceInspection.sample",
                action="Acquire the same calendar columns with persist_values=True and retry.",
                preserves_evidence=False,
            ),
        )
    calendar = registry.period_calendars.get(calendar_ref.path)
    if calendar is None:
        raise RuntimeError(f"missing compiled period calendar {calendar_ref.path!r}")
    date_dimension = registry.dimensions.get(calendar.date)
    if date_dimension is None or date_dimension.source_column is None:
        _raise(
            ErrorKind.MATERIALIZE_FAILED,
            "Period-calendar date must be declared with ms.time_dimension_column(...).",
            cls=SemanticRuntimeError,
            refs=(calendar_ref.key, calendar.date),
            details={"query_executed": False},
        )
    source_entity = registry.entities.get(date_dimension.entity)
    if (
        source_entity is None
        or using.datasource.path != source_entity.datasource
        or using.source != source_entity.source
    ):
        _raise(
            ErrorKind.MATERIALIZE_FAILED,
            "Period-calendar snapshot must belong to the calendar date entity's datasource and source.",
            cls=SemanticRuntimeError,
            refs=(calendar_ref.key,),
            details={"query_executed": False},
        )
    if using.coverage.scope_exhaustion != "exhaustive" or not using.persist_values:
        _raise(
            ErrorKind.MATERIALIZE_FAILED,
            "Period-calendar preview requires an exhaustive snapshot acquired with persist_values=True.",
            cls=SemanticRuntimeError,
            refs=(calendar_ref.key,),
            details={
                "query_executed": False,
                "scope_exhaustion": using.coverage.scope_exhaustion,
                "persist_values": using.persist_values,
            },
        )
    level_columns: dict[str, str] = {}
    for level, field_ref in calendar.levels:
        field = registry.dimensions.get(field_ref)
        if field is None or field.source_column is None:
            _raise(
                ErrorKind.MATERIALIZE_FAILED,
                f"Period-calendar level {level!r} must be declared with ms.dimension_column(...).",
                cls=SemanticRuntimeError,
                refs=(calendar_ref.key, field_ref),
                details={"query_executed": False},
            )
        level_columns[level] = field.source_column
    correspondence_columns: dict[str, tuple[str, str]] = {}
    for name, level, field_ref in calendar.correspondences:
        field = registry.dimensions.get(field_ref)
        if field is None or field.source_column is None:
            _raise(
                ErrorKind.MATERIALIZE_FAILED,
                f"Period-calendar correspondence {name!r} must use ms.dimension_column(...).",
                cls=SemanticRuntimeError,
                refs=(calendar_ref.key, field_ref),
                details={"query_executed": False},
            )
        correspondence_columns[name] = (level, field.source_column)
    from marivo._temporal import (
        TemporalSnapshotStore,
        certify_period_calendar_rows,
        period_calendar_definition_digest,
    )

    try:
        snapshot = certify_period_calendar_rows(
            calendar_ref=calendar_ref,
            boundary_timezone=calendar.boundary_timezone,
            coverage=(
                date.fromisoformat(calendar.coverage[0]),
                date.fromisoformat(calendar.coverage[1]),
            ),
            columns=using.columns,
            retained_values=using.retained_values,
            date_column=date_dimension.source_column,
            levels=level_columns,
            correspondences=correspondence_columns,
        )
    except (KeyError, TypeError, ValueError) as exc:
        _raise(
            ErrorKind.MATERIALIZE_FAILED,
            f"Period-calendar certification failed: {exc}",
            cls=SemanticRuntimeError,
            refs=(calendar_ref.key,),
            details={
                "query_executed": False,
                "certification_error": type(exc).__name__,
            },
            repair_value=repair(
                kind="reacquire",
                canonical_id="SourceInspection.sample",
                action=(
                    "Correct the calendar source values or coverage, reacquire the same "
                    "exhaustive columns with persist_values=True, and retry preview."
                ),
                preserves_evidence=False,
            ),
        )
    definition_digest = period_calendar_definition_digest(
        calendar_ref=calendar_ref,
        boundary_timezone=calendar.boundary_timezone,
        coverage=calendar.coverage,
        levels=calendar.levels,
        correspondences=calendar.correspondences,
        dependency_digest=dependency_digest,
    )
    TemporalSnapshotStore(project_root).publish(snapshot, definition_digest=definition_digest)
    rows: tuple[dict[str, object], ...] = tuple(
        {
            "level": period.level_name,
            "key": period.key,
            "start": period.start_date.isoformat(),
            "end": period.end_date.isoformat(),
        }
        for period in snapshot.periods[:preview_limit]
    )
    return PreviewResult(
        kind="semantic_dataset",
        ref=calendar_ref.path,
        columns=("level", "key", "start", "end"),
        types={"level": "string", "key": "json_scalar", "start": "date", "end": "date"},
        rows=rows,
        requested_limit=preview_limit,
        returned_row_count=len(rows),
        is_truncated=len(snapshot.periods) > preview_limit,
        status="passed",
        coverage=PreviewCoverage(
            scopes=((date_dimension.entity, using.scope),),
            rows_observed=using.coverage.observed_row_count,
            scope_exhaustion=using.coverage.scope_exhaustion,
            scope_exactness=using.coverage.scope_exactness,
            snapshot_ids=(using.id,),
            cache_status="fresh" if using.cache_status == "mismatched" else using.cache_status,
        ),
        sample_policy=PreviewSamplePolicy(method="bounded_limit", limit=preview_limit),
    )


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
        result.extend(_build_event_object(item, reg, self.catalog) for item in reg.events.values())
        result.extend(
            _build_state_model_object(item, reg, self.catalog) for item in reg.state_models.values()
        )
        result.extend(
            _build_period_calendar_object(item, reg, self.catalog)
            for item in reg.period_calendars.values()
        )
        result.extend(
            _build_temporal_set_object(item, reg, self.catalog)
            for item in reg.temporal_sets.values()
        )
        result.extend(
            _build_work_schedule_object(item, reg, self.catalog)
            for item in reg.work_schedules.values()
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
            if isinstance(details, EventDetails):
                return scope.ref == details.source_entity or any(
                    endpoint == scope.ref
                    for _name, endpoint, _cardinality, _path in details.participants
                )
            if isinstance(details, StateModelDetails):
                return scope.ref == details.subject
            if isinstance(details, TemporalSetDetails):
                return any(parent == scope.ref for parent in details.parents)
            if isinstance(details, WorkScheduleDetails):
                return any(parent == scope.ref for parent in details.parents)
        return False


# ---------------------------------------------------------------------------
# SemanticCatalog
# ---------------------------------------------------------------------------


class SemanticCatalog(RenderableResult):
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
        >>> revenue = catalog.require(ms.ref.metric("sales.revenue"))
        >>> revenue.details().additivity

    Constraints:
        catalog is obtained via ms.load(), not constructed directly.
        Typed collection properties return CatalogCollection[CatalogEntry].
        SemanticCatalog objects do not expose internal IR instances.
    """

    __slots__ = ("_index", "_ontology_index", "_project", "_reg", "_state")

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
        object.__setattr__(self, "_ontology_index", self._build_ontology_indexes())

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
    _COLLECTION_PROPERTIES = frozenset(CATALOG_COLLECTION_PROPERTIES)

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

    def _build_ontology_indexes(self) -> _OntologySemanticIndexes:
        anchors_by_metric: dict[Ref[MetricKind], tuple[Ref[SemanticKindTag], ...]] = {}
        metrics_by_endpoint_mutable: dict[Ref[SemanticKindTag], list[Ref[MetricKind]]] = {}
        for entry in self.metrics:
            details = entry.details()
            assert isinstance(details, (SimpleMetricDetails, DerivedMetricDetails))
            anchors: list[Ref[SemanticKindTag]] = [cast("Ref[SemanticKindTag]", entry.ref)]
            if details.root_entity is not None:
                anchors.append(details.root_entity)
            for entity_ref in details.effective_entities:
                metrics_by_endpoint_mutable.setdefault(entity_ref, []).append(entry.ref)
            seen_measures: set[Ref[SemanticKindTag]] = set()
            for _role, measure_ref in details.measure_lineage:
                if measure_ref not in seen_measures:
                    anchors.append(measure_ref)
                    seen_measures.add(measure_ref)
                metrics_by_endpoint_mutable.setdefault(measure_ref, []).append(entry.ref)
            anchors_by_metric[entry.ref] = tuple(anchors)
        metrics_by_endpoint = {
            endpoint: tuple(sorted(set(metric_refs), key=lambda ref: ref.key))
            for endpoint, metric_refs in metrics_by_endpoint_mutable.items()
        }
        return _OntologySemanticIndexes(
            anchors_by_metric=MappingProxyType(anchors_by_metric),
            metrics_by_endpoint=MappingProxyType(metrics_by_endpoint),
        )

    def _ontology_anchors_for_metric(
        self, metric_ref: Ref[MetricKind]
    ) -> tuple[Ref[SemanticKindTag], ...]:
        """Return the closed ontology anchor set for one current metric ref."""
        self.require(metric_ref)
        return self._ontology_index.anchors_by_metric[metric_ref]

    def _ontology_metrics_for_endpoint(
        self, endpoint: Ref[EntityKind | MeasureKind | MetricKind]
    ) -> tuple[Ref[MetricKind], ...]:
        """Resolve one ontology endpoint through semantic-owned dependency indexes."""
        self.require(endpoint)
        if endpoint.kind is SemanticKind.METRIC:
            return (cast("Ref[MetricKind]", endpoint),)
        return self._ontology_index.metrics_by_endpoint.get(
            cast("Ref[SemanticKindTag]", endpoint), ()
        )

    def _repr_identity(self) -> str:
        fingerprint = self.definition_fingerprint[:12]
        return f"SemanticCatalog fingerprint={fingerprint} objects={len(self._index._by_ref)}"

    def _card(self) -> Card:
        rows: list[tuple[str, str, str, str]] = []
        for member in CATALOG_MEMBER_CONTRACTS:
            collection = getattr(self, member.property_name)
            rows.append(
                (
                    member.property_name,
                    member.kind.value,
                    member.entry_type_name,
                    str(len(collection)),
                )
            )
        card = Card(
            identity=self._repr_identity(),
            available=(
                *(f".{name}" for name in CATALOG_COLLECTION_PROPERTIES),
                ".require(...)",
                ".readiness(...)",
                ".verify(...)",
                ".preview(...)",
                ".preview_many(...)",
                ".contract()",
                ".render()",
                ".show()",
            ),
        )
        card.field("definition_fingerprint", self.definition_fingerprint)
        card.field("semantic_root", str(self.semantic_root))
        card.field("workspace_dir", str(self.workspace_dir))
        return card.table(
            label="collections",
            columns=("property", "kind", "entry_type", "count"),
            rows=rows,
            row_count=len(rows),
        )

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
        key: str | Ref[KindT],
    ) -> CatalogEntry[KindT]:
        items = collection.items
        if type(key) is Ref:
            ref_key = key
            if ref_key.kind is not collection._kind:
                correct_collection = _COLLECTION_PROPERTY_BY_KIND[ref_key.kind]
                _raise(
                    ErrorKind.INVALID_REF,
                    "CatalogCollection.get(...) received the wrong ref kind. "
                    f"Expected {collection._kind.value}, received {ref_key.kind.value}. "
                    f"Inspect catalog.{correct_collection} for this ref kind.",
                    cls=SemanticRuntimeError,
                    refs=(ref_key.key,),
                    expected=f"Ref[{collection._kind.value}]",
                    received=f"Ref[{ref_key.kind.value}]",
                    repair_value=repair(
                        kind="inspect",
                        canonical_id="load",
                        action=(
                            f"Inspect catalog.{correct_collection}; do not substitute it "
                            f"for the {collection._kind.value} input."
                        ),
                    ),
                )
            match = next((item for item in items if item.ref == ref_key), None)
            if match is not None:
                return match
            global_match = self._require_index().require(cast("Ref[SemanticKindTag]", ref_key))
            if global_match is not None:
                scope = (
                    collection._scope_ref.key if collection._scope_ref is not None else "catalog"
                )
                _raise(
                    ErrorKind.NOT_FOUND,
                    f"Ref {ref_key.key!r} exists but is outside collection scope {scope}. "
                    "The collection scope is a hard visibility boundary. Use "
                    f"catalog.require(ms.ref.{ref_key.kind.value}({ref_key.path!r})) "
                    "only for an intentional global lookup.",
                    cls=SemanticRuntimeError,
                    refs=(ref_key.key,),
                    repair_value=repair(
                        kind="inspect",
                        canonical_id="SemanticCatalog.require",
                        action="Inspect the exact global object without widening this collection.",
                        candidates=(ref_key.key,),
                    ),
                )
            available = tuple(item.key for item in items[:_SEMANTIC_INPUT_CANDIDATE_LIMIT])
            _raise(
                ErrorKind.NOT_FOUND,
                f"Ref {ref_key.key!r} was not found in this "
                f"{collection._kind.value} collection. Current candidates: {available!r}.",
                cls=SemanticRuntimeError,
                refs=(ref_key.key,),
                repair_value=repair(
                    kind="inspect",
                    canonical_id="load",
                    action="Inspect current same-kind collection members.",
                    candidates=available,
                ),
            )

        if type(key) is not str:
            _raise(
                ErrorKind.INVALID_REF,
                "CatalogCollection.get(...) expected a local/full path string or an "
                f"exact same-kind Ref; received {type(key).__name__}.",
                cls=SemanticRuntimeError,
            )
        if ":" in key:
            _raise(
                ErrorKind.INVALID_REF,
                "CatalogCollection.get(...) string inputs use local names or full paths, "
                "not typed ref keys containing ':'. Pass the exact Ref instead.",
                cls=SemanticRuntimeError,
                refs=(key,),
            )
        if "." in key:
            try:
                full_ref = _make_ref(key, collection._kind)
            except (TypeError, ValueError) as exc:
                _raise(
                    ErrorKind.INVALID_REF,
                    str(exc),
                    cls=SemanticRuntimeError,
                    refs=(key,),
                )
            match = next((item for item in items if item.ref == full_ref), None)
            if match is not None:
                return match
            global_match = self._require_index().require(full_ref)
            if global_match is not None:
                scope = (
                    collection._scope_ref.key if collection._scope_ref is not None else "catalog"
                )
                _raise(
                    ErrorKind.NOT_FOUND,
                    f"Path {key!r} exists but is outside collection scope {scope}. "
                    "The collection scope is a hard visibility boundary. Use "
                    f"catalog.require(ms.ref.{collection._kind.value}({key!r})) "
                    "only for an intentional global lookup.",
                    cls=SemanticRuntimeError,
                    refs=(full_ref.key,),
                    repair_value=repair(
                        kind="inspect",
                        canonical_id="SemanticCatalog.require",
                        action="Inspect the exact global object without widening this collection.",
                        candidates=(full_ref.key,),
                    ),
                )
            available = tuple(item.key for item in items[:_SEMANTIC_INPUT_CANDIDATE_LIMIT])
            _raise(
                ErrorKind.NOT_FOUND,
                f"Full path {key!r} was not found in this "
                f"{collection._kind.value} collection. Current candidates: {available!r}.",
                cls=SemanticRuntimeError,
                refs=(key,),
                repair_value=repair(
                    kind="inspect",
                    canonical_id="load",
                    action="Inspect current same-kind collection members.",
                    candidates=available,
                ),
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
        matches = tuple(item for item in items if item.name == key)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            collection_property = _COLLECTION_PROPERTY_BY_KIND[collection._kind]
            calls = tuple(f"catalog.{collection_property}.get({item.path!r})" for item in matches)
            _raise(
                ErrorKind.AMBIGUOUS_REFERENCE,
                f"Local name {key!r} matched {len(matches)} objects in this collection. "
                f"Use one exact call: {', '.join(calls)}.",
                cls=SemanticRuntimeError,
                refs=tuple(item.key for item in matches),
                repair_value=repair(
                    kind="inspect",
                    canonical_id="load",
                    action="Choose one exact full path from the current collection.",
                    candidates=tuple(item.key for item in matches),
                ),
            )
        available = tuple(item.name for item in items[:_SEMANTIC_INPUT_CANDIDATE_LIMIT])
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

    @property
    def events(self) -> CatalogCollection[EventKind]:
        return self._collection(EventEntry, SemanticKind.EVENT)

    @property
    def state_models(self) -> CatalogCollection[StateModelKind]:
        return self._collection(StateModelEntry, SemanticKind.STATE_MODEL)

    @property
    def period_calendars(self) -> CatalogCollection[PeriodCalendarKind]:
        return self._collection(PeriodCalendarEntry, SemanticKind.PERIOD_CALENDAR)

    @property
    def temporal_sets(self) -> CatalogCollection[TemporalSetKind]:
        return self._collection(TemporalSetEntry, SemanticKind.TEMPORAL_SET)

    @property
    def work_schedules(self) -> CatalogCollection[WorkScheduleKind]:
        return self._collection(WorkScheduleEntry, SemanticKind.WORK_SCHEDULE)

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
            f"ms.ref.{candidate.kind.value}({candidate.path!r})" for candidate in candidates
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
        refs: Sequence[_SemanticInput[SemanticKindTag] | RuntimeMetricExpr] | None = None,
    ) -> ReadinessReport:
        """Return explicit certification and diagnostics for the given semantic refs.

        Reads loaded state plus persisted row-free preview evidence without
        acquiring, refreshing, or querying. Missing evidence produces exact
        next calls for the caller to execute explicitly.

        ``analysis_ready_inputs`` preserves directly selected refs and runtime
        expressions whose full dependency closures have no blocker.
        ``analysis_ready_refs`` remains the refs-only compatibility projection.

        Args:
            refs: Current catalog entries, semantic refs, or closed runtime
                metric expressions to check. Resolves the full governed
                dependency closure for each input. None checks all loaded
                semantic objects.

        Returns:
            ReadinessReport indicating whether the selected refs satisfy the
            current certification contract.

        Example:
            >>> report = catalog.readiness(refs=[revenue, runtime_revenue])
            >>> if report.status == "blocked":
            ...     report.show()
            ...     raise SystemExit

        Constraints:
            Use after authoring or changing semantic objects, or when a workflow
            requests technical certification. Analysis APIs do not invoke
            readiness automatically.
        """
        self._require_ready()
        if refs is None:
            return self._project.readiness(refs=None)
        inputs = tuple(_require_readiness_input(self, value) for value in refs)
        if not inputs:
            _raise(
                ErrorKind.INVALID_REF,
                "catalog.readiness(refs=...) requires a non-empty sequence.",
                cls=SemanticRuntimeError,
            )
        if all(type(value) is Ref for value in inputs):
            exact_inputs = cast("tuple[Ref[SemanticKindTag], ...]", inputs)
            duplicate_refs = tuple(
                dict.fromkeys(
                    value
                    for index, value in enumerate(exact_inputs)
                    if value in exact_inputs[:index]
                )
            )
            if duplicate_refs:
                _raise(
                    ErrorKind.INVALID_REF,
                    "catalog.readiness(refs=...) requires unique exact refs; received "
                    f"{[ref.key for ref in duplicate_refs]}.",
                    cls=SemanticRuntimeError,
                    refs=tuple(ref.key for ref in duplicate_refs),
                )

        from marivo.semantic.metric_graph_canonical import fingerprint
        from marivo.semantic.readiness import ReadinessIssue
        from marivo.semantic.runtime_metric_lowering import lower_metric_inputs

        registry = self._require_index().registry
        sidecar = self._project._expression_sidecar
        graph_blocked: set[int] = set()
        graph_issues: list[ReadinessIssue] = []

        def runtime_key(value: RuntimeMetricExpr, *, index: int) -> str:
            try:
                digest = fingerprint(replay_payload(value))
            except (RecursionError, TypeError, ValueError):
                return f"runtime:root[{index}]"
            return f"runtime:{digest}"

        def graph_issue(
            *,
            root_keys: tuple[str, ...],
            exc: ValueError | TypeError,
        ) -> ReadinessIssue:
            return ReadinessIssue(
                kind="metric_graph_invalid",
                severity="blocker",
                refs=root_keys,
                message=f"Runtime metric input cannot lower to the bounded metric graph: {exc}",
                repair=repair(
                    kind="reauthor",
                    canonical_id="readiness",
                    action=(
                        "Repair the governed leaf refs or rebuild the closed expression with "
                        "mv.runtime_metric.* within the registered graph constraints."
                    ),
                ),
                details={
                    "max_depth": 10,
                    "max_occurrences": 256,
                    "lowering_error_kind": getattr(
                        exc, "code", getattr(exc, "kind", "graph_contract")
                    ),
                    "observed_count": getattr(exc, "observed_count", None),
                    "limit": getattr(exc, "limit", None),
                    "occurrence_path": getattr(exc, "path", None),
                    "candidates": getattr(exc, "candidates", {}),
                    "repairs": getattr(exc, "repairs", ()),
                },
                catalog_definition_fingerprint=self.definition_fingerprint,
            )

        normalized_inputs: list[Ref[SemanticKindTag] | RuntimeMetricExpr] = []
        check_refs: list[Ref[SemanticKindTag]] = []
        root_dependencies: list[tuple[Ref[SemanticKindTag], ...]] = []
        runtime_indices: list[int] = []
        for index, value in enumerate(inputs):
            if type(value) is Ref:
                exact_ref = self.require(value).ref
                normalized_inputs.append(exact_ref)
                root_dependencies.append((exact_ref,))
                if exact_ref not in check_refs:
                    check_refs.append(exact_ref)
                continue
            normalized_inputs.append(value)
            runtime_indices.append(index)
            try:
                dependencies = runtime_metric_leaf_refs(value)
            except (TypeError, ValueError) as exc:
                graph_blocked.add(index)
                graph_issues.append(
                    graph_issue(root_keys=(runtime_key(value, index=index),), exc=exc)
                )
                root_dependencies.append(())
                continue
            root_dependencies.append(dependencies)
            for dependency in dependencies:
                if dependency not in check_refs:
                    check_refs.append(dependency)

        duplicate_candidates = tuple(
            value for index, value in enumerate(normalized_inputs) if index not in graph_blocked
        )
        duplicates = tuple(
            value
            for index, value in enumerate(duplicate_candidates)
            if value in duplicate_candidates[:index]
        )
        if duplicates:
            _raise(
                ErrorKind.INVALID_REF,
                "catalog.readiness(refs=...) requires unique direct inputs; duplicate runtime "
                "metric roots are not allowed.",
                cls=SemanticRuntimeError,
            )

        valid_forest_inputs: list[Ref[MetricKind] | RuntimeMetricExpr] = []
        valid_forest_indices: list[int] = []
        for index in runtime_indices:
            if index in graph_blocked:
                continue
            expression = cast("RuntimeMetricExpr", normalized_inputs[index])
            try:
                lower_metric_inputs(registry, (expression,), sidecar=sidecar)
            except (ValueError, TypeError) as exc:
                graph_blocked.add(index)
                graph_issues.append(
                    graph_issue(root_keys=(runtime_key(expression, index=index),), exc=exc)
                )
            else:
                valid_forest_inputs.append(expression)
                valid_forest_indices.append(index)

        for index, value in enumerate(normalized_inputs):
            if type(value) is Ref and value.kind is SemanticKind.METRIC:
                valid_forest_inputs.append(cast("Ref[MetricKind]", value))
                valid_forest_indices.append(index)

        if runtime_indices and valid_forest_inputs:
            ordered = sorted(
                zip(valid_forest_indices, valid_forest_inputs, strict=True),
                key=lambda item: item[0],
            )
            try:
                lower_metric_inputs(
                    registry,
                    tuple(item for _index, item in ordered),
                    sidecar=sidecar,
                )
            except (ValueError, TypeError) as exc:
                affected = tuple(index for index, _item in ordered)
                graph_blocked.update(affected)
                root_keys = tuple(
                    value.key if type(value) is Ref else runtime_key(value, index=index)
                    for index in affected
                    for value in (normalized_inputs[index],)
                )
                graph_issues.append(graph_issue(root_keys=root_keys, exc=exc))

        base_report = self._project.readiness(refs=check_refs)
        ready_leaf_refs = set(base_report.analysis_ready_refs)
        ready_inputs = tuple(
            value
            for index, value in enumerate(normalized_inputs)
            if index not in graph_blocked
            and all(dependency in ready_leaf_refs for dependency in root_dependencies[index])
        )
        ready_refs = tuple(value for value in ready_inputs if type(value) is Ref)
        blockers = (*base_report.blockers, *graph_issues)
        status = "blocked" if blockers else base_report.status
        return replace(
            base_report,
            status=status,
            blockers=blockers,
            analysis_ready_refs=ready_refs,
            analysis_ready_inputs=ready_inputs,
        )

    def verify(
        self,
        ref: _SemanticInput[SemanticKindTag],
        /,
    ) -> VerifyResult:
        """Statically verify one current catalog entry or exact ref.

        Args:
            ref: A current entry owned by this catalog or an exact member ref.

        Returns:
            A static VerifyResult for the normalized canonical ref.

        Example:
            >>> revenue = catalog.metrics.get("sales.revenue")
            >>> result = catalog.verify(revenue)

        Constraints:
            Verification does not query a datasource. Entries from another or
            earlier catalog instance are rejected before verification.
        """
        normalized = _normalize_semantic_input(
            self,
            ref,
            allowed_kinds=_ALL_SEMANTIC_KINDS,
            location="catalog.verify(ref)",
        )
        return self._project._verify(normalized)

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
        ref: _SemanticInput[SemanticKindTag],
        /,
        *,
        using: PreviewUsing,
        limit: int = PREVIEW_DEFAULT_LIMIT,
        include_types: bool = True,
        context_columns: Iterable[str] | None = None,
    ) -> PreviewResult:
        """Return one bounded runtime preview for a current entry or exact ref.

        Args:
            ref: A current catalog entry or exact member ref.
            using: Persisted datasource discovery snapshot bindings.
            limit: Positive bounded preview row limit.
            include_types: Include physical type facts in the result.
            context_columns: Optional context columns for field previews.

        Returns:
            A bounded PreviewResult for the normalized canonical ref.

        Example:
            >>> revenue = catalog.metrics.get("sales.revenue")
            >>> preview = catalog.preview(revenue, using=orders_snapshot)

        Constraints:
            Input ownership and membership are checked before connection
            acquisition or materialization.
        """
        return self._preview_one(
            _normalize_semantic_input(
                self,
                ref,
                allowed_kinds=_ALL_SEMANTIC_KINDS,
                location="catalog.preview(ref)",
            ),
            using=using,
            limit=limit,
            include_types=include_types,
            context_columns=context_columns,
        )

    def preview_many(
        self,
        refs: Sequence[_SemanticInput[SemanticKindTag]],
        /,
        *,
        using: PreviewUsing,
        limit: int = PREVIEW_DEFAULT_LIMIT,
        include_types: bool = True,
    ) -> PreviewBatchResult:
        """Return a bounded batch preview for ordered current entries or refs.

        Args:
            refs: A non-empty ordered sequence of current entries or exact refs.
            using: One snapshot or exact entity-to-snapshot bindings.
            limit: Positive bounded preview row limit per result.
            include_types: Include physical type facts in each result.

        Returns:
            PreviewBatchResult in the same order as the normalized inputs.

        Example:
            >>> revenue = catalog.metrics.get("sales.revenue")
            >>> region = catalog.dimensions.get("sales.orders.region")
            >>> batch = catalog.preview_many([region, revenue], using=orders_snapshot)

        Constraints:
            The complete input sequence is normalized before any preview
            begins. Duplicate canonical refs are rejected.
        """
        normalized_refs = tuple(
            _normalize_semantic_input(
                self,
                value,
                allowed_kinds=_ALL_SEMANTIC_KINDS,
                location=f"catalog.preview_many(refs)[{index}]",
            )
            for index, value in enumerate(refs)
        )
        return self._preview_batch(
            normalized_refs,
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
        if kind is SemanticKind.PERIOD_CALENDAR:
            from marivo.semantic._definition_identity import scoped_definition_fingerprint

            return _preview_period_calendar(
                calendar_ref=cast("Ref[PeriodCalendarKind]", ref_obj),
                registry=reg,
                project_root=self.workspace_dir,
                using=using,
                limit=limit,
                dependency_digest=scoped_definition_fingerprint(
                    root=ref_obj,
                    definitions=self._state.definitions,
                    dependencies=self._state.dependencies,
                    sidecar=self._state.sidecar,
                ),
            )
        if kind is SemanticKind.TEMPORAL_SET:
            from marivo.semantic._definition_identity import scoped_definition_fingerprint

            return _preview_temporal_set(
                temporal_set_ref=cast("Ref[TemporalSetKind]", ref_obj),
                registry=reg,
                project_root=self.workspace_dir,
                using=using,
                limit=limit,
                dependency_digest=scoped_definition_fingerprint(
                    root=ref_obj,
                    definitions=self._state.definitions,
                    dependencies=self._state.dependencies,
                    sidecar=self._state.sidecar,
                ),
            )
        if kind is SemanticKind.WORK_SCHEDULE:
            from marivo.semantic._definition_identity import scoped_definition_fingerprint

            return _preview_work_schedule(
                work_schedule_ref=cast("Ref[WorkScheduleKind]", ref_obj),
                registry=reg,
                project_root=self.workspace_dir,
                using=using,
                limit=limit,
                dependency_digest=scoped_definition_fingerprint(
                    root=ref_obj,
                    definitions=self._state.definitions,
                    dependencies=self._state.dependencies,
                    sidecar=self._state.sidecar,
                ),
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
            if kind == SemanticKind.EVENT:
                event_ir = reg.events[ref_str]
                table = resolver.event(
                    ref_factory.event(ref_str),
                    participants=tuple(participant.name for participant in event_ir.participants),
                )
                return _validate_event_preview(
                    preview_ibis_table(
                        table,
                        kind="semantic_event",
                        ref=ref_str,
                        limit=preview_limit,
                        sample_policy=PreviewSamplePolicy(
                            method="bounded_limit",
                            limit=preview_limit,
                        ),
                        include_types=include_types,
                        report_tz=system_timezone_name(),
                    ),
                    event_ir=event_ir,
                    participants=event_ir.participants,
                )
            if kind == SemanticKind.STATE_MODEL:
                import pandas as pd

                model_ir = reg.state_models[ref_str]
                event_refs = tuple(
                    dict.fromkeys(
                        (
                            *(item.trigger.event_ref for item in model_ir.inceptions),
                            *(item.trigger.event_ref for item in model_ir.transitions),
                        )
                    )
                )
                rows: list[dict[str, object]] = []
                for event_ref in event_refs:
                    event_ir = reg.events[event_ref]
                    event_preview = _validate_event_preview(
                        preview_ibis_table(
                            resolver.event(
                                ref_factory.event(event_ref),
                                participants=tuple(
                                    participant.name for participant in event_ir.participants
                                ),
                            ),
                            kind="semantic_event",
                            ref=event_ref,
                            limit=preview_limit,
                            sample_policy=PreviewSamplePolicy(
                                method="bounded_limit",
                                limit=preview_limit,
                            ),
                            include_types=include_types,
                            report_tz=system_timezone_name(),
                        ),
                        event_ir=event_ir,
                        participants=event_ir.participants,
                    )
                    rows.append(
                        {
                            "event_ref": event_ref,
                            "status": event_preview.status,
                            "observed_rows": event_preview.returned_row_count,
                            "truncated": event_preview.is_truncated,
                        }
                    )
                return preview_from_pandas(
                    pd.DataFrame(
                        rows,
                        columns=(
                            "event_ref",
                            "status",
                            "observed_rows",
                            "truncated",
                        ),
                    ),
                    kind="semantic_state_model",
                    ref=ref_str,
                    requested_limit=preview_limit,
                    sample_policy=PreviewSamplePolicy(
                        method="bounded_limit",
                        limit=preview_limit,
                    ),
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
            SemanticKind.EVENT,
            SemanticKind.STATE_MODEL,
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


def _validate_catalog_member_contract() -> None:
    """Keep the closed catalog member table aligned with live properties."""

    if len(_OBJECT_TYPE_BY_KIND) != len(CATALOG_MEMBER_CONTRACTS):
        raise RuntimeError("catalog member contract contains duplicate semantic kinds")
    if len(_COLLECTION_PROPERTY_BY_KIND) != len(CATALOG_MEMBER_CONTRACTS):
        raise RuntimeError("catalog member contract contains duplicate property names")
    for member in CATALOG_MEMBER_CONTRACTS:
        attribute = inspect.getattr_static(SemanticCatalog, member.property_name, None)
        if not isinstance(attribute, property):
            raise RuntimeError(
                f"catalog member contract does not resolve to a property: {member.property_name}"
            )


_validate_catalog_member_contract()


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
