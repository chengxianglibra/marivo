"""SemanticCatalog — unified agent-facing read surface for marivo.semantic.

Public entrypoint: ms.load() -> SemanticCatalog
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Iterable, Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from types import MappingProxyType
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Generic,
    Literal,
    NoReturn,
    TypeAlias,
    TypeVar,
    cast,
    overload,
)

from ibis.common.deferred import Deferred

from marivo._definition.deferred import root_declaration
from marivo._symbolic import (
    DatasourceSymbol,
    DimensionSymbol,
    DomainSymbol,
    LookupSymbol,
    PeriodCalendarSymbol,
    TemporalSetSymbol,
    TimeDimensionSymbol,
    native_symbol_ref,
    symbol_factory,
    symbol_from_ref,
)
from marivo._temporal import (
    Grain,
    PeriodCalendarSnapshotV2,
    TemporalOccurrenceRecord,
    TemporalResolver,
    TemporalSetSnapshotStore,
    TemporalSetSnapshotV1,
    TimeScope,
)
from marivo.datasource import credentials as cr
from marivo.datasource._builtin import DEFAULT_DATASOURCE_DESCRIPTION, DEFAULT_DATASOURCE_NAME
from marivo.datasource.engines import require_profile_for_backend_type
from marivo.datasource.errors import DatasourceError, _backend_failure_summary
from marivo.datasource.ir import (
    AiContextIR,
    DatasourceIR,
    DatasourceSourceLocation,
    QueryParamScalar,
    QueryParamScalarList,
)
from marivo.datasource.runtime import DatasourceConnectionService
from marivo.datasource.source import AuthoringScope
from marivo.preview import (
    METRIC_PREVIEW_SAMPLE_SIZE,
    PREVIEW_DEFAULT_LIMIT,
    PreviewCoverage,
    PreviewResult,
    PreviewSamplePolicy,
    PreviewWarning,
    normalize_preview_cell,
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
    PeriodCalendarKind,
    Ref,
    RelationshipKind,
    SemanticKind,
    SemanticKindTag,
    TemporalSetKind,
    TimeDimensionKind,
)
from marivo.refs import (
    ref as ref_factory,
)
from marivo.render import (
    Card,
    FieldSection,
    ListSection,
    RenderableResult,
    Section,
)
from marivo.semantic.catalog_entries import (
    CatalogFilters,
    CatalogPage,
    CatalogScope,
    DatasourceDefinition,
    DomainDefinition,
    EntityDefinition,
    EntryDefinition,
    EntryProvenance,
    FieldDefinition,
    JsonValue,
    MetricDefinition,
    MetricTimeBinding,
    PeriodCalendarDefinition,
    RelationshipDefinition,
    TemporalSetDefinition,
    _json_value,
)
from marivo.semantic.catalog_search import (
    RelatedRetrieval,
    SearchConfig,
    SearchDocument,
    load_search_config,
    search_documents,
)
from marivo.semantic.constraints import ConstraintId
from marivo.semantic.dtos import PreviewBatchResult
from marivo.semantic.errors import (
    ErrorKind,
    SemanticError,
    SemanticExpressionError,
    SemanticLoadFailed,
    SemanticRuntimeError,
    _raise,
    repair,
)
from marivo.semantic.expressions import (
    CatalogValues,
    Dimension,
    Entity,
    ExpressionContract,
    Measure,
    Relationship,
    ResolvedValue,
    TimeField,
    author_value,
)
from marivo.semantic.ir import (
    DimensionIR,
    DomainIR,
    EntityVersioningIR,
    PeriodCalendarIR,
    SnapshotVersioningIR,
    SourceLocation,
    TemporalSetIR,
    ValidityVersioningIR,
)
from marivo.semantic.preview_scope import (
    NormalizedPreviewScope,
    PreviewScope,
    PreviewSourceBindings,
    normalize_preview_batch_scopes,
    normalize_preview_limit,
    normalize_preview_scope,
)

CalendarDate = date

if TYPE_CHECKING:
    from marivo._authoring.model import AuthoringRepair
    from marivo.semantic._compiled_state import CompiledSemanticState
    from marivo.semantic.reader import SemanticProject
    from marivo.semantic.readiness import ReadinessReport
    from marivo.semantic.resolver import SemanticResolver
    from marivo.semantic.source_health import SourceCheck, SourceHealthReport
    from marivo.semantic.validator import Registry

__all__ = [
    "AiContextView",
    "CalendarPeriodPage",
    "CatalogEntry",
    "CatalogFilters",
    "CatalogPage",
    "CatalogScope",
    "DatasourceDefinition",
    "DatasourceEntry",
    "DimensionEntry",
    "DomainDefinition",
    "DomainEntry",
    "EntityDefinition",
    "EntityEntry",
    "EntityVersioning",
    "EntryProvenance",
    "FieldDefinition",
    "MeasureEntry",
    "MetricDefinition",
    "MetricEntry",
    "MetricTimeBinding",
    "PeriodCalendarDefinition",
    "PeriodCalendarEntry",
    "RelationshipDefinition",
    "RelationshipEntry",
    "SemanticCatalog",
    "SemanticKind",
    "SnapshotVersioning",
    "TemporalOccurrencePage",
    "TemporalSetDefinition",
    "TemporalSetEntry",
    "TimeDimensionEntry",
    "ValidityVersioning",
    "load",
]

AiContextView = AiContextIR
SnapshotVersioning = SnapshotVersioningIR
ValidityVersioning = ValidityVersioningIR
EntityVersioning = EntityVersioningIR


def _metric_preview_warning() -> PreviewWarning:
    return PreviewWarning(
        kind="approximate_preview",
        message=(
            f"metric preview aggregates at most {METRIC_PREVIEW_SAMPLE_SIZE:,} Entity "
            "output rows; treat the result as approximate"
        ),
    )


def _attach_preview_scope(
    result: PreviewResult,
    *,
    bindings: NormalizedPreviewScope,
    approximate_input: bool = False,
) -> PreviewResult:
    """Attach only current execution scope facts to one preview result."""
    coverage = replace(
        result.coverage,
        scopes=bindings.scopes,
        scope_exactness=("sample_only" if approximate_input else result.coverage.scope_exactness),
    )
    return replace(result, coverage=coverage)


def _is_naive_timestamp_type(data_type: str | None) -> bool:
    if data_type is None:
        return False
    normalized = data_type.strip().lower().replace(" ", "")
    return normalized.startswith("timestamp") and "'" not in normalized and '"' not in normalized


def _time_parse_risk_warning(ref: str, data_type: str) -> PreviewWarning:
    return PreviewWarning(
        kind="time_parse_risk",
        message=(
            f"{ref} has no declared parse/timezone and preview observed native naive "
            f"type {data_type!r}; declare the source timezone before relying on "
            "report-local day or hour boundaries"
        ),
    )


def _with_time_parse_risk(
    result: PreviewResult,
    *,
    ref: str,
    field: DimensionIR,
) -> PreviewResult:
    """Attach the native naive-timestamp warning when current types prove it."""
    observed_type = result.types.get(result.columns[-1])
    if field.parse is not None or not _is_naive_timestamp_type(observed_type):
        return result
    return replace(
        result,
        warnings=(
            *result.warnings,
            _time_parse_risk_warning(ref, cast("str", observed_type)),
        ),
    )


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
def _make_ref(
    path: str, kind: Literal[SemanticKind.PERIOD_CALENDAR]
) -> Ref[PeriodCalendarKind]: ...


@overload
def _make_ref(path: str, kind: Literal[SemanticKind.TEMPORAL_SET]) -> Ref[TemporalSetKind]: ...


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
        SemanticKind.PERIOD_CALENDAR: ref_factory.period_calendar,
        SemanticKind.TEMPORAL_SET: ref_factory.temporal_set,
    }[kind]
    return factory(path)


CatalogObjectT = TypeVar("CatalogObjectT", bound="CatalogEntry[SemanticKindTag]")
CatalogEntryKindT = TypeVar("CatalogEntryKindT", bound=SemanticKindTag, covariant=True)
KindT = TypeVar("KindT", bound=SemanticKindTag)


@dataclass(frozen=True)
class _BatchPreviewItem:
    order: int
    ref: Ref[SemanticKindTag]
    kind: SemanticKind
    bindings: NormalizedPreviewScope


def _require_metric_preview_backend(
    ref: Ref[SemanticKindTag], bindings: NormalizedPreviewScope
) -> None:
    """Reject unavailable Metric routes before any single or batch source access."""
    if ref.kind is not SemanticKind.METRIC or bindings.backend == "duckdb":
        return
    from marivo._authoring.model import AuthoringRepair
    from marivo.introspection.live.model import LiveHelpTarget

    action = (
        "Evaluate this Metric with session.observe(...).compute(), supplying the "
        "time scope or Population required by the question. This performs actual "
        "analysis over the selected inputs."
    )
    _raise(
        ErrorKind.MATERIALIZE_FAILED,
        "Ordinary Metric preview is available only on DuckDB.",
        cls=SemanticRuntimeError,
        refs=(ref.key,),
        expected="ordinary Metric preview on a DuckDB datasource",
        received=f"backend={bindings.backend}; datasource={bindings.datasource_id}",
        details={
            "query_executed": False,
            "backend": bindings.backend,
            "datasource_id": bindings.datasource_id,
        },
        hint=action,
        repair_value=AuthoringRepair(
            kind="user_choice",
            help_target=LiveHelpTarget(surface="analysis", canonical_id="session.observe"),
            action=action,
        ),
    )


# ---------------------------------------------------------------------------
# Kind-specific details
# ---------------------------------------------------------------------------


def _source_location_text(source_location: SourceLocation) -> str:
    return f"{source_location.file}:{source_location.line}"


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


def _format_mapping(mapping: Mapping[str, object]) -> str:
    if not mapping:
        return "(none)"
    return ", ".join(f"{key}: {value}" for key, value in sorted(mapping.items()))


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

    _ref: Ref[SemanticKindTag]
    kind: SemanticKind
    name: str
    domain: str | None
    context: AiContextView
    source_location: SourceLocation
    _parents: tuple[Ref[SemanticKindTag], ...]
    _children: tuple[Ref[SemanticKindTag], ...]
    _dependents: tuple[Ref[SemanticKindTag], ...]
    python_symbol: str

    @property
    def ref(self) -> LookupSymbol:
        return symbol_from_ref(self._ref)

    @property
    def parents(self) -> tuple[LookupSymbol, ...]:
        return tuple(symbol_from_ref(ref) for ref in self._parents)

    @property
    def children(self) -> tuple[LookupSymbol, ...]:
        return tuple(symbol_from_ref(ref) for ref in self._children)

    @property
    def dependents(self) -> tuple[LookupSymbol, ...]:
        return tuple(symbol_from_ref(ref) for ref in self._dependents)

    def _repr_identity(self) -> str:
        return f"{self.__class__.__name__} ref={self._ref.key}"

    def _detail_sections(self) -> list[Section]:
        return _common_detail_sections(
            context=self.context,
            python_symbol=self.python_symbol,
            source_location=self.source_location,
            parents=self._parents,
            children=self._children,
            dependents=self._dependents,
        )

    def _card(self) -> Card:
        card = Card(identity=self._repr_identity(), available=(".show()",))
        for section in self._detail_sections():
            card = card.section(section)
        return card


@dataclass(frozen=True, repr=False)
class DatasourceDetails(_DetailsBase):
    """Details for a datasource object."""

    backend_type: str
    fields: Mapping[str, object]
    env_refs: Mapping[str, str]

    @property
    def ref(self) -> DatasourceSymbol:
        return symbol_factory.datasource(self._ref.path)

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", MappingProxyType(dict(self.fields)))
        object.__setattr__(self, "env_refs", MappingProxyType(dict(self.env_refs)))

    def _detail_sections(self) -> list[Section]:
        sections = super()._detail_sections()
        sections.extend(
            (
                FieldSection(label="backend_type", value=self.backend_type),
                FieldSection(label="fields", value=_format_mapping(self.fields)),
                FieldSection(label="env_refs", value=_format_mapping(self.env_refs)),
            )
        )
        if self.name == DEFAULT_DATASOURCE_NAME:
            sections.append(FieldSection(label="source", value=DEFAULT_DATASOURCE_DESCRIPTION))
        return sections


@dataclass(frozen=True, repr=False)
class DomainDetails(_DetailsBase):
    """Details for a domain object."""

    owner: str
    default: bool

    @property
    def ref(self) -> DomainSymbol:
        return symbol_factory.domain(self._ref.path)

    def _detail_sections(self) -> list[Section]:
        sections = super()._detail_sections()
        sections.extend(
            (
                FieldSection(label="owner", value=self.owner),
                FieldSection(label="default", value=str(self.default)),
            )
        )
        return sections


@dataclass(frozen=True, repr=False)
class CalendarLevelDetails(RenderableResult):
    """Certification-derived facts for one owned period-calendar level."""

    name: str
    _key_ref: Ref[DimensionKind] | Ref[TimeDimensionKind]
    period_count: int | None
    direct_finer_levels: tuple[str, ...] | None
    direct_coarser_levels: tuple[str, ...] | None
    rollup_targets: tuple[str, ...] | None

    @property
    def key_ref(self) -> DimensionSymbol | TimeDimensionSymbol:
        if self._key_ref.kind is SemanticKind.TIME_DIMENSION:
            return symbol_factory.time_dimension(self._key_ref.path)
        return symbol_factory.dimension(self._key_ref.path)

    def _repr_identity(self) -> str:
        return f"CalendarLevelDetails name={self.name}"

    def _card(self) -> Card:
        return (
            Card(identity=self._repr_identity(), available=(".show()",))
            .field("key_ref", self._key_ref.key)
            .field("period_count", str(self.period_count))
            .field("direct_finer_levels", repr(self.direct_finer_levels))
            .field("direct_coarser_levels", repr(self.direct_coarser_levels))
            .field("rollup_targets", repr(self.rollup_targets))
        )


@dataclass(frozen=True, repr=False)
class PeriodCalendarDetails(_DetailsBase):
    """Static source contract for one governed period calendar."""

    _date: Ref[TimeDimensionKind]
    boundary_timezone: str
    coverage: tuple[CalendarDate, CalendarDate]
    levels: tuple[CalendarLevelDetails, ...]
    _level_bindings: tuple[tuple[str, Ref[DimensionKind]], ...]
    snapshot_status: Literal["missing", "current", "stale", "invalid"]
    snapshot_digest: str | None = None

    @property
    def ref(self) -> PeriodCalendarSymbol:
        return symbol_factory.period_calendar(self._ref.path)

    @property
    def source_date(self) -> TimeDimensionSymbol:
        """The calendar's civil-date source field."""
        return symbol_factory.time_dimension(self._date.path)

    def _detail_sections(self) -> list[Section]:
        sections = super()._detail_sections()
        sections.extend(
            (
                FieldSection(label="calendar_ref", value=self._ref.key),
                FieldSection(label="source_date", value=self._date.key),
                FieldSection(label="boundary_timezone", value=self.boundary_timezone),
                FieldSection(
                    label="coverage",
                    value=f"[{self.coverage[0].isoformat()}, {self.coverage[1].isoformat()})",
                ),
                FieldSection(label="levels", value=", ".join(item.name for item in self.levels)),
                FieldSection(label="snapshot_status", value=self.snapshot_status),
                FieldSection(
                    label="snapshot_digest",
                    value=self.snapshot_digest or "(unavailable)",
                ),
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
    _occurrence_id: Ref[DimensionKind]
    _start: Ref[TimeDimensionKind]
    _end: Ref[TimeDimensionKind]
    _category: Ref[DimensionKind] | None
    occurrence_count: int | None
    snapshot_status: Literal["missing", "current", "stale", "invalid"]

    @property
    def ref(self) -> TemporalSetSymbol:
        return symbol_factory.temporal_set(self._ref.path)

    @property
    def occurrence_id(self) -> DimensionSymbol:
        return symbol_factory.dimension(self._occurrence_id.path)

    @property
    def start(self) -> TimeDimensionSymbol:
        return symbol_factory.time_dimension(self._start.path)

    @property
    def end(self) -> TimeDimensionSymbol:
        return symbol_factory.time_dimension(self._end.path)

    @property
    def category(self) -> DimensionSymbol | None:
        return symbol_factory.dimension(self._category.path) if self._category is not None else None

    def _detail_sections(self) -> list[Section]:
        sections = super()._detail_sections()
        sections.extend(
            (
                FieldSection(label="boundary_timezone", value=self.boundary_timezone),
                FieldSection(
                    label="coverage",
                    value=f"[{self.coverage[0].isoformat()}, {self.coverage[1].isoformat()})",
                ),
                FieldSection(label="occurrence_id", value=self._occurrence_id.key),
                FieldSection(label="start", value=self._start.key),
                FieldSection(label="end", value=self._end.key),
                FieldSection(
                    label="category",
                    value=self._category.key if self._category is not None else "(none)",
                ),
                FieldSection(label="occurrence_count", value=str(self.occurrence_count)),
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
        return (
            Card(
                identity=self._repr_identity(),
                available=(".items", ".next_cursor", ".show()"),
            )
            .field(label="has_more", value=str(self.next_cursor is not None))
            .table(
                columns=("temporal_set", "key", "start", "end", "category", "boundary_timezone"),
                rows=(
                    (
                        item._temporal_set.key,
                        repr(item.key),
                        item.start.isoformat(),
                        item.end.isoformat(),
                        repr(item.occurrence_category),
                        item.boundary_timezone,
                    )
                    for item in self.items
                ),
                row_count=len(self.items),
            )
        )


_CatalogObjectDetails = (
    DatasourceDetails | DomainDetails | PeriodCalendarDetails | TemporalSetDetails
)


@dataclass(frozen=True, repr=False, eq=False)
class CatalogEntry(RenderableResult, Generic[CatalogEntryKindT]):
    """One immutable browsable object in one compiled semantic catalog."""

    _ref: Ref[CatalogEntryKindT]
    _details: _CatalogObjectDetails | None
    _catalog: SemanticCatalog

    _navigation_names: ClassVar[tuple[str, ...]] = ()

    @property
    def ref(self) -> LookupSymbol:
        return symbol_from_ref(self._ref)

    def __eq__(self, other: object) -> bool:
        return (
            type(self) is type(other)
            and isinstance(other, CatalogEntry)
            and self._ref == other._ref
            and self._catalog is other._catalog
        )

    def __hash__(self) -> int:
        return hash((type(self), id(self._catalog), self._ref))

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
        from marivo.semantic._catalog_definition import definition_for

        return definition_for(self._catalog, self._ref)

    @property
    def provenance(self) -> EntryProvenance:
        """Captured declaration source and compiled snapshot identity."""
        captured = self._catalog._state.definitions.get(self._ref)
        location = getattr(captured, "location", None)
        if location is None and self._ref.kind in {SemanticKind.METRIC, SemanticKind.MEASURE}:
            value = self._catalog._index.values.get(self._ref)
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

    def related(
        self, relation: RelatedRelation, /, *, limit: int = 10, cursor: str | None = None
    ) -> CatalogPage[SemanticEntry]:
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
                f"Unknown Entry display section {section!r}.",
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
            available.append(".value -> native closed Analysis input")
        elif isinstance(definition, RelationshipDefinition):
            available.append(
                ".value -> native closed Relationship"
                if definition.cardinality != "outside_scope"
                else "ms.load(domains=...) -> include outside_scope endpoints"
            )
        elif isinstance(definition, (DomainDefinition, DatasourceDefinition)):
            available.append('.related("members")')
        elif definition.snapshot_status == "current":
            available.extend(
                (".grain(level)", ".periods(level)")
                if isinstance(definition, PeriodCalendarDefinition)
                else (".occurrence(key)", ".occurrences()")
            )
        else:
            available.append("catalog.preview(entry, scope=...) -> certify temporal authority")
        card = Card(identity=self._repr_identity(), available=tuple(available)).field(
            "business_definition", self.summary
        )
        if definition.context.guardrails:
            card.listing("guardrails", definition.context.guardrails)
        if isinstance(definition, MetricDefinition):
            card.field("formula", definition.formula)
            card.field("unit", definition.unit or "(none)")
            for binding in definition.time_bindings:
                card.field(
                    "time_binding",
                    f"{binding.field} ({binding.source}); Entity default={binding.entity_default}",
                )
            for dependency in definition.dependency_context:
                if dependency.guardrails:
                    card.listing(f"guardrails from {dependency.id}", dependency.guardrails)
        elif isinstance(definition, FieldDefinition):
            card.field("expression", definition.expression)
            card.field("unit", definition.unit or "(none)")
            card.field("default_time", definition.default_time or "(none)")
        elif isinstance(definition, EntityDefinition):
            card.field("expression", definition.expression)
            card.field("primary_key", ", ".join(definition.primary_key))
            card.field("default_time", definition.default_time or "(none)")
            card.field("datasource", definition.datasource)
        elif isinstance(definition, (PeriodCalendarDefinition, TemporalSetDefinition)):
            card.field("boundary_timezone", definition.boundary_timezone)
            card.field("coverage", str(definition.coverage))
            card.field("snapshot_status", definition.snapshot_status)
        elif isinstance(definition, DomainDefinition):
            card.field("owner", definition.owner)
        elif isinstance(definition, DatasourceDefinition):
            card.field("backend_type", definition.backend_type)
        elif isinstance(definition, RelationshipDefinition):
            card.field("direction", f"{definition.from_entity} -> {definition.to_entity}")
            card.field("keys", str(definition.keys))
            card.field("cardinality", definition.cardinality)
        return card


CatalogItem: TypeAlias = CatalogEntry[SemanticKindTag] | ResolvedValue
_SemanticInput: TypeAlias = Deferred | CatalogEntry[SemanticKindTag] | ResolvedValue


@dataclass(frozen=True, repr=False, eq=False)
class DomainEntry(CatalogEntry[DomainKind]):
    """Immutable domain catalog entry."""

    @property
    def kind(self) -> Literal["domain"]:
        return "domain"

    @property
    def definition(self) -> DomainDefinition:
        return cast("DomainDefinition", super().definition)


@dataclass(frozen=True, repr=False, eq=False)
class DatasourceEntry(CatalogEntry[DatasourceKind]):
    """Immutable datasource catalog entry."""

    @property
    def kind(self) -> Literal["datasource"]:
        return "datasource"

    @property
    def definition(self) -> DatasourceDefinition:
        return cast("DatasourceDefinition", super().definition)


@dataclass(frozen=True, repr=False, eq=False)
class PeriodCalendarEntry(CatalogEntry[PeriodCalendarKind]):
    """Loaded period-calendar declaration with direct semantic-grain lookup."""

    _ref: Ref[PeriodCalendarKind]

    @property
    def kind(self) -> Literal["period_calendar"]:
        return "period_calendar"

    @property
    def definition(self) -> PeriodCalendarDefinition:
        return cast("PeriodCalendarDefinition", super().definition)

    @property
    def ref(self) -> PeriodCalendarSymbol:
        return symbol_factory.period_calendar(self._ref.path)

    def _temporal_details(self) -> PeriodCalendarDetails:
        details = cast("PeriodCalendarDetails", self._details)
        status, snapshot = self._snapshot_with_status()
        if status != "current" or snapshot is None:
            return replace(
                details,
                snapshot_status=status,
                snapshot_digest=None,
                levels=_calendar_level_details(
                    details,
                    snapshot=None,
                ),
            )
        return replace(
            details,
            snapshot_status="current",
            snapshot_digest=snapshot.snapshot_digest,
            levels=_calendar_level_details(details, snapshot=snapshot),
        )

    def grain(self, level: str, /) -> Grain:
        """Return the common semantic Grain for one declared calendar level.

        Args:
            level: Exact declared calendar level name; unknown levels are rejected.

        Example:
            >>> calendar = catalog.get("period_calendar:sales.fiscal")
            >>> fiscal_week = calendar.grain("fiscal_week")
        """
        details = self._temporal_details()
        if level not in {item.name for item in details.levels}:
            _raise_period_lookup(
                self._ref,
                "grain",
                f"Calendar level {level!r} is not declared by {self._ref.key}.",
                details={"level": level},
            )
        from marivo._temporal import semantic_grain

        status, snapshot = self._snapshot_with_status()
        return semantic_grain(
            calendar=self._ref,
            level=level,
            snapshot=snapshot if status == "current" else None,
        )

    def period(self, level: str, key: str | int | float | bool, /) -> TimeScope:
        """Return the exact certified scope for one named period.

        Args:
            level: Declared calendar level name, such as ``"fiscal_week"``.
            key: Exact certified period key at that level.

        Returns:
            Snapshot-bound half-open ``TimeScope`` accepted by
            ``session.observe``.

        Example:
            >>> session = mv.session.get_or_create(
            ...     "analysis", question="Compare one certified fiscal week"
            ... )
            >>> calendar = session.catalog.get("period_calendar:sales.fiscal")
            >>> scope = calendar.period("fiscal_week", "FY2026-W01")

        Guidance:
            The calendar must have a current certified snapshot, and ``level``
            and ``key`` must identify one exact record in that snapshot.
        """
        try:
            return self._snapshot().period_scope(level, key)
        except (KeyError, TypeError, ValueError):
            _raise_period_lookup(
                self._ref,
                "period",
                f"No certified {level!r} period matches key {key!r}.",
                details={"level": level, "key": key},
            )

    def period_on(self, level: str, value: date, /) -> TimeScope:
        """Return the exact certified scope containing one civil date.

        Args:
            level: Exact declared calendar level name.
            value: Civil date contained in one certified period; missing coverage is rejected.

        Example:
            >>> from datetime import date
            >>> calendar = catalog.get("period_calendar:sales.fiscal")
            >>> scope = calendar.period_on("fiscal_week", date(2026, 1, 15))
        """
        try:
            snapshot = self._snapshot()
            period = TemporalResolver(snapshot).period_on(level, value)
            return snapshot.period_scope(level, period.key)
        except (KeyError, TypeError, ValueError):
            _raise_period_lookup(
                self._ref,
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
        """Return a bounded ordinal page of certified periods for one level.

        Args:
            level: Exact declared calendar level name.
            limit: Integer page size in [1, 100].
            cursor: Previous page.next_cursor for this same snapshot and level; omit for the first page.

        Example:
            >>> calendar = catalog.get("period_calendar:sales.fiscal")
            >>> page = calendar.periods("fiscal_week", limit=20)
            >>> page.show()
        """
        if type(limit) is not int or isinstance(limit, bool) or not 1 <= limit <= 100:
            _raise_period_lookup(
                self._ref,
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
                self._ref,
                "periods",
                "The cursor is invalid or belongs to a different certified snapshot/level.",
                details={"level": level, "cursor": "provided"},
            )
        if level not in snapshot.levels:
            _raise_period_lookup(
                self._ref,
                "periods",
                f"Calendar level {level!r} is not certified.",
                details={"level": level},
            )
        if level == "day":
            start, end = snapshot.coverage
            total = (end - start).days
            if offset >= total and total:
                _raise_period_lookup(
                    self._ref,
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
                self._ref,
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

    def _snapshot(self) -> PeriodCalendarSnapshotV2:
        status, snapshot = self._snapshot_with_status()
        if status != "current" or snapshot is None:
            _raise_period_lookup(
                self._ref,
                "snapshot",
                f"{self._ref.key} has no current certified period-calendar snapshot ({status}).",
                details={"snapshot_status": status},
            )
        return snapshot

    def _snapshot_with_status(
        self,
    ) -> tuple[Literal["missing", "current", "stale", "invalid"], PeriodCalendarSnapshotV2 | None]:
        from marivo._temporal import TemporalSnapshotStore, period_calendar_definition_digest
        from marivo.semantic._definition_identity import scoped_definition_fingerprint

        details = cast("PeriodCalendarDetails", self._details)
        definition_digest = period_calendar_definition_digest(
            calendar_ref=self._ref,
            boundary_timezone=details.boundary_timezone,
            coverage=(details.coverage[0], details.coverage[1]),
            levels=tuple((name, value.path) for name, value in details._level_bindings),
            dependency_digest=scoped_definition_fingerprint(
                root=self._ref,
                definitions=self._catalog._state.definitions,
                dependencies=self._catalog._state.dependencies,
                sidecar=self._catalog._state.sidecar,
            ),
        )
        return TemporalSnapshotStore(self._catalog.workspace_dir).inspect_current(
            self._ref,
            definition_digest=definition_digest,
        )


@dataclass(frozen=True, repr=False, eq=False)
class TemporalSetEntry(CatalogEntry[TemporalSetKind]):
    """Loaded temporal-set declaration with exact occurrence navigation."""

    _ref: Ref[TemporalSetKind]

    @property
    def kind(self) -> Literal["temporal_set"]:
        return "temporal_set"

    @property
    def definition(self) -> TemporalSetDefinition:
        return cast("TemporalSetDefinition", super().definition)

    @property
    def ref(self) -> TemporalSetSymbol:
        return symbol_factory.temporal_set(self._ref.path)

    def _temporal_details(self) -> TemporalSetDetails:
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
        """Return the exact certified scope for one named occurrence.

        Args:
            key: Exact occurrence key in the current certified snapshot; unknown keys are rejected.

        Returns:
            The original half-open scope with category, boundary timezone and snapshot identity.

        Example:
            >>> temporal_set = catalog.get("temporal_set:sales.campaigns")
            >>> scope = temporal_set.occurrence("spring_launch")

        Constraints:
            Reads certified local facts only; missing, stale or invalid snapshots reject.
        """
        try:
            return self._snapshot().occurrence_scope(key)
        except (KeyError, TypeError, ValueError):
            _raise_temporal_set_lookup(
                self._ref,
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
        """Return deterministic, bounded certified occurrence scopes.

        Args:
            start: Optional inclusive overlap filter inside certified coverage; date for
                civil-date sets or datetime for timestamp sets.
            end: Optional exclusive overlap filter inside certified coverage, compatible
                with start. Timestamp coverage uses local midnights in boundary_timezone.
            category: Exact non-empty category filter; None accepts every category.
            limit: Integer page size in [1, 100].
            cursor: Previous page.next_cursor for the same snapshot and filters; omit for the first page.

        Returns:
            Original occurrence scopes ordered by start, end and key, plus next_cursor.
            Covered filters with no matches return an empty page.

        Example:
            >>> temporal_set = catalog.get("temporal_set:sales.campaigns")
            >>> page = temporal_set.occurrences(category="promotion", limit=20)
            >>> page.show()

        Constraints:
            Overlap filters never clip returned scopes. Omitted bounds leave that side
            unfiltered. Read next_cursor until None before treating results as complete.
            Explicit bounds outside coverage reject rather than implying no occurrences.
            Reads certified local facts only; unavailable snapshots reject.
        """
        if type(limit) is not int or isinstance(limit, bool) or not 1 <= limit <= 100:
            _raise_temporal_set_lookup(
                self._ref,
                "occurrences",
                "occurrence page limit must be an integer in [1, 100].",
                details={"limit": limit},
            )
        if category is not None and (type(category) is not str or not category):
            _raise_temporal_set_lookup(
                self._ref,
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
                self._ref,
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
                self._ref,
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
                self._ref,
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
                self._ref,
                "snapshot",
                f"{self._ref.key} has no current certified temporal-set snapshot ({status}).",
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
            temporal_set_ref=self._ref,
            boundary_timezone=details.boundary_timezone,
            coverage=(details.coverage[0], details.coverage[1]),
            occurrence_id=details._occurrence_id.path,
            start=details._start.path,
            end=details._end.path,
            category=details._category.path if details._category is not None else None,
            dependency_digest=scoped_definition_fingerprint(
                root=self._ref,
                definitions=self._catalog._state.definitions,
                dependencies=self._catalog._state.dependencies,
                sidecar=self._catalog._state.sidecar,
            ),
        )
        return TemporalSetSnapshotStore(self._catalog.workspace_dir).inspect_current(
            self._ref,
            definition_digest=definition_digest,
        )


RelatedRelation: TypeAlias = Literal[
    "dependencies", "dependents", "fields", "metrics", "relationships", "members"
]


@dataclass(frozen=True, repr=False, eq=False)
class EntityEntry(CatalogEntry[EntityKind]):
    """Immutable entity metadata and its native closed execution value."""

    @property
    def kind(self) -> Literal["entity"]:
        return "entity"

    @property
    def definition(self) -> EntityDefinition:
        return cast("EntityDefinition", super().definition)

    @property
    def value(self) -> Entity:
        """Return this snapshot's native immutable closed execution value."""
        return cast("Entity", author_value(self._catalog._index.values.get(self._ref)))


@dataclass(frozen=True, repr=False, eq=False)
class DimensionEntry(CatalogEntry[DimensionKind]):
    """Immutable dimension metadata and its native closed execution value."""

    @property
    def kind(self) -> Literal["dimension"]:
        return "dimension"

    @property
    def definition(self) -> FieldDefinition:
        return cast("FieldDefinition", super().definition)

    @property
    def value(self) -> Deferred:
        """Return this snapshot's native immutable closed execution value."""
        return cast("Deferred", author_value(self._catalog._index.values.get(self._ref)))


@dataclass(frozen=True, repr=False, eq=False)
class TimeDimensionEntry(CatalogEntry[TimeDimensionKind]):
    """Immutable time_dimension metadata and its native closed execution value."""

    @property
    def kind(self) -> Literal["time_dimension"]:
        return "time_dimension"

    @property
    def definition(self) -> FieldDefinition:
        return cast("FieldDefinition", super().definition)

    @property
    def value(self) -> Deferred:
        """Return this snapshot's native immutable closed execution value."""
        return cast("Deferred", author_value(self._catalog._index.values.get(self._ref)))


@dataclass(frozen=True, repr=False, eq=False)
class MeasureEntry(CatalogEntry[MeasureKind]):
    """Immutable measure metadata and its native closed execution value."""

    @property
    def kind(self) -> Literal["measure"]:
        return "measure"

    @property
    def definition(self) -> FieldDefinition:
        return cast("FieldDefinition", super().definition)

    @property
    def value(self) -> Deferred:
        """Return this snapshot's native immutable closed execution value."""
        return cast("Deferred", author_value(self._catalog._index.values.get(self._ref)))


@dataclass(frozen=True, repr=False, eq=False)
class MetricEntry(CatalogEntry[MetricKind]):
    """Immutable metric metadata and its native closed execution value."""

    @property
    def kind(self) -> Literal["metric"]:
        return "metric"

    @property
    def definition(self) -> MetricDefinition:
        return cast("MetricDefinition", super().definition)

    @property
    def value(self) -> Deferred:
        """Return this snapshot's native immutable closed execution value."""
        return cast("Deferred", author_value(self._catalog._index.values.get(self._ref)))


@dataclass(frozen=True, repr=False, eq=False)
class RelationshipEntry(CatalogEntry[RelationshipKind]):
    """Immutable relationship metadata and its native closed execution value."""

    @property
    def kind(self) -> Literal["relationship"]:
        return "relationship"

    @property
    def definition(self) -> RelationshipDefinition:
        return cast("RelationshipDefinition", super().definition)

    @property
    def value(self) -> Relationship:
        """Return this snapshot's native immutable closed execution value."""
        outside = tuple(
            item.key
            for item in self._catalog._entry_dependencies(self._ref)
            if item not in self._catalog._index._refs
        )
        if outside:
            _raise(
                ErrorKind.NOT_FOUND,
                "Relationship execution requires endpoints outside this loaded Catalog.",
                cls=SemanticRuntimeError,
                refs=(self.id, *outside),
                expected="all Relationship endpoints in the loaded scope",
                received=", ".join(outside),
                hint="Call ms.load(domains=...) with every required domain to create a new Catalog.",
                details={"reason": "outside_scope", "outside_scope": outside},
            )
        return cast("Relationship", author_value(self._catalog._index.values.get(self._ref)))


SemanticEntry: TypeAlias = (
    DomainEntry
    | DatasourceEntry
    | EntityEntry
    | DimensionEntry
    | TimeDimensionEntry
    | MeasureEntry
    | MetricEntry
    | RelationshipEntry
    | PeriodCalendarEntry
    | TemporalSetEntry
)


def _calendar_level_details(
    details: PeriodCalendarDetails,
    *,
    snapshot: PeriodCalendarSnapshotV2 | None,
) -> tuple[CalendarLevelDetails, ...]:
    """Project static level bindings plus certified graph facts."""
    bindings: tuple[tuple[str, Ref[DimensionKind] | Ref[TimeDimensionKind]], ...] = (
        ("day", details._date),
        *details._level_bindings,
    )
    if snapshot is None:
        return tuple(
            CalendarLevelDetails(
                name=name,
                _key_ref=key_ref,
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
            _key_ref=key_ref,
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
    from marivo._authoring.model import AuthoringRepair
    from marivo.introspection.live.model import LiveHelpTarget

    routes = {
        "grain": (
            LiveHelpTarget(surface="analysis", canonical_id="calendar.grain"),
            (
                f"Inspect catalog.get({calendar_ref.key!r}).definition for "
                "the exact declared levels, then retry calendar.grain(level) with one of them."
            ),
        ),
        "period": (
            LiveHelpTarget(surface="analysis", canonical_id="calendar.period"),
            (
                f"Inspect catalog.get({calendar_ref.key!r}).definition, enumerate "
                "the level with calendar.periods(level), then retry calendar.period(level, key) "
                "with one exact certified key."
            ),
        ),
        "period_on": (
            LiveHelpTarget(surface="analysis", canonical_id="calendar.period_on"),
            (
                f"Inspect catalog.get({calendar_ref.key!r}).definition for "
                "declared levels and certified coverage, then retry calendar.period_on(level, "
                "value) with one covered civil date."
            ),
        ),
        "periods": (
            LiveHelpTarget(surface="analysis", canonical_id="calendar.periods"),
            (
                f"Inspect catalog.get({calendar_ref.key!r}).definition, then retry "
                "calendar.periods(level, limit=..., cursor=...) with a declared level, a limit in "
                "[1, 100], and either no cursor or the cursor returned by the current snapshot."
            ),
        ),
        "snapshot": (
            LiveHelpTarget(surface="semantic", canonical_id="preview"),
            (
                f"Certify the current period-calendar snapshot with catalog.preview("
                f"catalog.get({calendar_ref.key!r}), scope=...), then retry. "
                "If certification is blocked, inspect scoped catalog.readiness(...)."
            ),
        ),
    }
    route = routes.get(operation)
    if route is None:
        raise RuntimeError(f"unsupported period-calendar lookup operation: {operation}")
    help_target, action = route
    _raise(
        ErrorKind.NOT_FOUND,
        message,
        cls=SemanticRuntimeError,
        refs=(calendar_ref.key,),
        details={"operation": operation, **dict(details)},
        hint=action,
        repair_value=AuthoringRepair(
            kind="retry",
            help_target=help_target,
            action=action,
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
    from marivo._authoring.model import AuthoringRepair
    from marivo.introspection.live.model import LiveHelpTarget

    routes = {
        "occurrence": (
            LiveHelpTarget(surface="analysis", canonical_id="temporal_set.occurrence"),
            (
                "Browse the current certified keys with temporal_set.occurrences(...), then retry "
                "temporal_set.occurrence(key) with one exact returned key."
            ),
        ),
        "occurrences": (
            LiveHelpTarget(surface="analysis", canonical_id="temporal_set.occurrences"),
            (
                "Inspect the temporal-set details, then retry temporal_set.occurrences(...) with "
                "valid bounds inside certified coverage, a non-empty category or None, a limit in [1, 100], and either no "
                "cursor or the cursor returned for the same current snapshot and filters."
            ),
        ),
        "snapshot": (
            LiveHelpTarget(surface="semantic", canonical_id="preview"),
            (
                f"Certify the current temporal-set snapshot with catalog.preview("
                f"catalog.get({temporal_set_ref.key!r}), scope=...), then retry. "
                "If certification is blocked, inspect scoped catalog.readiness(...)."
            ),
        ),
    }
    route = routes.get(operation)
    if route is None:
        raise RuntimeError(f"unsupported temporal-set lookup operation: {operation}")
    help_target, action = route
    _raise(
        ErrorKind.NOT_FOUND,
        message,
        cls=SemanticRuntimeError,
        refs=(temporal_set_ref.key,),
        details={"operation": operation, **dict(details)},
        hint=action,
        repair_value=AuthoringRepair(
            kind="retry",
            help_target=help_target,
            action=action,
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
    coverage_start: date | datetime = snapshot.coverage[0]
    coverage_end: date | datetime = snapshot.coverage[1]
    if snapshot.encoding == "timestamp":
        from marivo._temporal import _normalize_temporal_timestamp

        coverage_start = _normalize_temporal_timestamp(
            datetime.combine(snapshot.coverage[0], datetime.min.time()),
            boundary_timezone=snapshot.boundary_timezone,
        )
        coverage_end = _normalize_temporal_timestamp(
            datetime.combine(snapshot.coverage[1], datetime.min.time()),
            boundary_timezone=snapshot.boundary_timezone,
        )
    if any(
        bound is not None and not coverage_start <= bound <= coverage_end for bound in (start, end)
    ):
        raise ValueError(
            "occurrence filters must lie within certified coverage "
            f"[{coverage_start.isoformat()}, {coverage_end.isoformat()}) "
            f"in {snapshot.boundary_timezone}"
        )
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


def _object_from_details(
    object_type: type[CatalogObjectT],
    details: _CatalogObjectDetails,
    catalog: SemanticCatalog,
) -> CatalogObjectT:
    return cast(
        "CatalogObjectT",
        cast("Any", object_type)(
            _ref=details._ref,
            _details=details,
            _catalog=catalog,
        ),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


_ENTRY_TYPE_BY_KIND: dict[SemanticKind, type[CatalogEntry[SemanticKindTag]]] = {
    SemanticKind.ENTITY: EntityEntry,
    SemanticKind.DIMENSION: DimensionEntry,
    SemanticKind.TIME_DIMENSION: TimeDimensionEntry,
    SemanticKind.MEASURE: MeasureEntry,
    SemanticKind.METRIC: MetricEntry,
    SemanticKind.RELATIONSHIP: RelationshipEntry,
    SemanticKind.DOMAIN: DomainEntry,
    SemanticKind.DATASOURCE: DatasourceEntry,
    SemanticKind.PERIOD_CALENDAR: PeriodCalendarEntry,
    SemanticKind.TEMPORAL_SET: TemporalSetEntry,
}


_SEMANTIC_INPUT_CANDIDATE_LIMIT = 12
_ALL_SEMANTIC_KINDS = frozenset(SemanticKind)


def _same_kind_candidates(
    catalog: SemanticCatalog,
    allowed_kinds: frozenset[SemanticKind],
) -> tuple[Ref[SemanticKindTag], ...]:
    return tuple(ref for ref in catalog._require_index()._refs if ref.kind in allowed_kinds)[
        :_SEMANTIC_INPUT_CANDIDATE_LIMIT
    ]


def _semantic_input_expected(allowed_kinds: frozenset[SemanticKind]) -> str:
    kinds = ", ".join(sorted(kind.value for kind in allowed_kinds))
    return f"native lookup symbol, current catalog entry, or catalog value with kind in {{{kinds}}}"


def _physical_dependencies(
    catalog: SemanticCatalog, ref: Ref[SemanticKindTag]
) -> tuple[Ref[SemanticKindTag], ...]:
    """Find physical check inputs in the already compiled dependency closure."""
    pending = [ref]
    seen: set[Ref[SemanticKindTag]] = set()
    physical: set[Ref[SemanticKindTag]] = set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        declared = current.kind is SemanticKind.METRIC or (
            current.kind is SemanticKind.MEASURE
            and current.path in catalog._state.registry.declared_measures
        )
        if declared:
            pending.extend(catalog._state.dependencies.get(current, ()))
        else:
            physical.add(current)
    return tuple(sorted(physical, key=lambda item: item.key))


def _value_lookup_ref(value: object) -> Ref[SemanticKindTag] | None:
    """The catalog ref a resolved expression value names; None for anything else."""
    if isinstance(value, Deferred):
        from marivo.semantic._deferred_semantics import consume_field, consume_row

        declaration = root_declaration(value)
        if declaration is not None:
            if declaration.kind == "measure":
                return ref_factory.measure(f"{consume_row(value).root.ref.path}.{declaration.name}")
            if declaration.domain is not None:
                return ref_factory.metric(f"{declaration.domain.path}.{declaration.name}")
            return None
        try:
            field = consume_field(value)
        except SemanticExpressionError:
            return None
        return field.reference.ref
    if isinstance(value, TimeField) and value.path:
        return None
    if isinstance(value, Entity):
        return value._exact_ref.ref
    if isinstance(value, Relationship):
        return value._parameter.ref.ref
    if isinstance(value, (Dimension, TimeField, Measure)):
        return value.ref.ref
    return None


def _semantic_input_ref(value: object) -> Ref[SemanticKindTag] | None:
    symbolic = native_symbol_ref(value)
    if symbolic is not None:
        return symbolic
    if isinstance(value, CatalogEntry):
        return value._ref
    return _value_lookup_ref(value)


def _semantic_input_received(value: object) -> str:
    exact_ref = _semantic_input_ref(value)
    if exact_ref is not None:
        if type(value) is Ref:
            return f"Ref[{exact_ref.kind.value}]({exact_ref.key})"
        return f"{type(value).__name__}({exact_ref.key})"
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


def _normalize_semantic_input(
    catalog: SemanticCatalog,
    value: _SemanticInput,
    *,
    allowed_kinds: frozenset[SemanticKind],
    location: str,
    metric_preview: bool = False,
    declaration_readiness: bool = False,
) -> Ref[SemanticKindTag]:
    """Normalize one ref, current-catalog entry, or catalog value to its canonical ref.

    Entries must belong to this catalog instance. Expression values are lookup
    keys resolved by kind and path in this catalog. Metric previews opt into
    ordinary numeric lowering; certification and source checks remain physical.
    """
    if not allowed_kinds:
        raise ValueError("allowed_kinds must not be empty")

    index = catalog._require_index()
    entry: CatalogEntry[SemanticKindTag] | None = None
    symbolic = native_symbol_ref(value)
    if symbolic is not None:
        exact_ref = symbolic
    elif isinstance(value, CatalogEntry):
        entry = value
        if type(entry._ref) is not Ref:
            _raise_invalid_semantic_input(
                catalog=catalog,
                value=value,
                allowed_kinds=allowed_kinds,
                location=location,
                message="the entry does not carry an exact sealed Ref value.",
            )
        exact_ref = entry._ref
        expected_entry_type = _ENTRY_TYPE_BY_KIND.get(exact_ref.kind)
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
            current = index.entry(exact_ref)
            if (
                same_semantic_root
                and current is not None
                and current._ref.kind in allowed_kinds
                and type(current) is expected_entry_type
            ):
                snippet = f"entry = catalog.get({exact_ref.key!r})"
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
        lookup = _value_lookup_ref(value)
        if lookup is None:
            _raise_invalid_semantic_input(
                catalog=catalog,
                value=value,
                allowed_kinds=allowed_kinds,
                location=location,
                message=(
                    "expected a native lookup symbol, a current catalog entry, or a catalog value; "
                    "bare strings, anonymous expressions, via-bound time fields, and "
                    "duck-typed objects are not accepted."
                ),
            )
        exact_ref = lookup

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

    if exact_ref not in index._refs:
        _raise_invalid_semantic_input(
            catalog=catalog,
            value=value,
            allowed_kinds=allowed_kinds,
            location=location,
            kind=ErrorKind.NOT_FOUND,
            message="the semantic ref is not a member of the current compiled catalog.",
            candidate_kinds=frozenset({exact_ref.kind}),
        )
    if entry is not None and index.entry(exact_ref) is not entry:
        _raise_invalid_semantic_input(
            catalog=catalog,
            value=value,
            allowed_kinds=allowed_kinds,
            location=location,
            message="the entry was not produced by the current compiled catalog.",
            candidate_kinds=frozenset({exact_ref.kind}),
        )
    registry = catalog._state.registry
    if not declaration_readiness and (
        (exact_ref.kind is SemanticKind.METRIC and not metric_preview)
        or (exact_ref.kind is SemanticKind.MEASURE and exact_ref.path in registry.declared_measures)
    ):
        physical = _physical_dependencies(catalog, exact_ref)
        shown = physical[:_SEMANTIC_INPUT_CANDIDATE_LIMIT]
        physical_snippet: str | None = None
        if location == "catalog.readiness(refs=...)":
            physical_snippet = (
                "catalog.readiness(refs=["
                + ", ".join(f"ms.ref.{ref.kind.value}({ref.path!r})" for ref in shown)
                + "])"
                if shown and len(shown) == len(physical)
                else f"catalog.get({exact_ref.key!r}).show()"
            )
        _raise(
            ErrorKind.INVALID_EXPRESSION_STAGE,
            f"{location} requires physical inputs; {exact_ref.key} is a published "
            "expression declaration.",
            cls=SemanticRuntimeError,
            refs=(exact_ref.key,),
            expected="a physical Entity, field, Relationship, or authored temporal object",
            received=f"published {exact_ref.kind.value} {exact_ref.key}",
            hint='Run marivo.help("semantic.readiness") to inspect physical input requirements.',
            repair_value=repair(
                kind="inspect",
                canonical_id="readiness",
                action=(
                    "Inspect its definition with catalog.get(identity).show(), then "
                    "check the physical fields and Entities it reads instead."
                ),
                snippet=physical_snippet,
                candidates=tuple(ref.key for ref in shown),
            ),
        )
    return exact_ref


def _require_semantic_ref(value: object, *, parameter: str) -> Ref[SemanticKindTag]:
    symbolic = native_symbol_ref(value)
    if symbolic is not None:
        return symbolic
    _raise(
        ErrorKind.INVALID_REF,
        f"catalog.{parameter} requires a native lookup symbol; received {type(value).__name__}. "
        "Pass entry.ref when starting from catalog navigation, or construct one "
        "with the exact ms.ref.<kind>(path) factory.",
        cls=SemanticRuntimeError,
        constraint_id=ConstraintId.REF_SHAPE,
        details={
            "operation": f"catalog.{parameter}",
            "expected": "native lookup symbol",
            "received_type": type(value).__name__,
        },
    )


def _require_readiness_input(
    catalog: SemanticCatalog,
    value: object,
) -> Ref[SemanticKindTag]:
    if (
        native_symbol_ref(value) is not None
        or isinstance(value, CatalogEntry)
        or _value_lookup_ref(value)
    ):
        return _normalize_semantic_input(
            catalog,
            cast("_SemanticInput", value),
            allowed_kinds=_ALL_SEMANTIC_KINDS,
            location="catalog.readiness(refs=...)",
            declaration_readiness=True,
        )
    candidates: tuple[Ref[SemanticKindTag], ...] = ()
    repair_snippet: str | None = None
    repair_action = (
        "Inspect the relevant catalog collection, then pass its current value or entry, or "
        "a native lookup symbol."
    )
    if isinstance(value, str):
        candidates = tuple(
            ref for ref in catalog._require_index()._refs if value in {ref.path, ref.key}
        )[:_SEMANTIC_INPUT_CANDIDATE_LIMIT]
        if len(candidates) == 1:
            candidate = candidates[0]

            repair_action = "Replace the string with this exact current catalog member."
            repair_snippet = (
                f"member = catalog.get({candidate.key!r})\n"
                "report = catalog.readiness(refs=[member])"
            )
        elif candidates:
            repair_action = (
                "Choose the intended exact current catalog member; this path exists in "
                "multiple semantic kinds."
            )
    message = (
        "catalog.readiness(refs=...) requires a current catalog value or entry, "
        f"native lookup symbol; received {type(value).__name__}"
    )
    if isinstance(value, str):
        message += "; bare strings are not accepted."
    else:
        message += "."
    _raise(
        ErrorKind.INVALID_REF,
        message,
        cls=SemanticRuntimeError,
        constraint_id=ConstraintId.REF_SHAPE,
        hint=("Pass a current physical catalog value or entry, or exact ms.ref.<kind>(path)."),
        expected="current catalog value or entry, native lookup symbol",
        received=type(value).__name__,
        repair_value=repair(
            kind="inspect",
            canonical_id="readiness",
            action=repair_action,
            snippet=repair_snippet,
            candidates=tuple(candidate.key for candidate in candidates),
        ),
        details={
            "operation": "catalog.readiness(refs=...)",
            "expected": "current catalog value or entry, native lookup symbol",
            "received_type": type(value).__name__,
            "candidates": tuple(candidate.key for candidate in candidates),
        },
    )


def _normalize_location(loc: SourceLocation | DatasourceSourceLocation) -> SourceLocation:
    return SourceLocation(file=loc.file, line=loc.line)


class _CatalogBuildIndex:
    """Declaration-order navigation projections for one catalog construction."""

    def __init__(self, registry: Registry) -> None:
        domain_children: dict[str, list[Ref[SemanticKindTag]]] = {}
        datasource_entities: dict[str, list[Ref[SemanticKindTag]]] = {}
        for entity in registry.entities.values():
            entity_ref = ref_factory.entity(entity.semantic_id)
            domain_children.setdefault(entity.domain, []).append(entity_ref)
            datasource_entities.setdefault(entity.datasource, []).append(entity_ref)
        for path in registry.declared_metrics:
            domain_children.setdefault(path.split(".", 1)[0], []).append(ref_factory.metric(path))
        for calendar in registry.period_calendars.values():
            domain_children.setdefault(calendar.domain, []).append(
                ref_factory.period_calendar(calendar.semantic_id)
            )
        for temporal_set in registry.temporal_sets.values():
            domain_children.setdefault(temporal_set.domain, []).append(
                ref_factory.temporal_set(temporal_set.semantic_id)
            )
        self.domain_children = MappingProxyType(
            {key: tuple(values) for key, values in domain_children.items()}
        )
        self.datasource_entities = MappingProxyType(
            {key: tuple(values) for key, values in datasource_entities.items()}
        )


def _build_datasource_object(
    ds_ir: DatasourceIR, build_index: _CatalogBuildIndex, catalog: SemanticCatalog
) -> DatasourceEntry:
    ref = ref_factory.datasource(ds_ir.semantic_id)
    details = DatasourceDetails(
        _ref=ref,
        kind=SemanticKind.DATASOURCE,
        name=ds_ir.name,
        domain=None,
        context=ds_ir.ai_context,
        source_location=_normalize_location(ds_ir.location),
        _parents=(),
        _children=(),
        _dependents=build_index.datasource_entities.get(ds_ir.semantic_id, ()),
        python_symbol=ds_ir.python_symbol,
        backend_type=ds_ir.backend_type,
        fields=dict(ds_ir.fields),
        env_refs=dict(ds_ir.env_refs),
    )
    return _object_from_details(DatasourceEntry, details, catalog)


def _build_domain_object(
    model_ir: DomainIR, build_index: _CatalogBuildIndex, catalog: SemanticCatalog
) -> DomainEntry:
    ref = _make_ref(model_ir.name, SemanticKind.DOMAIN)
    details = DomainDetails(
        _ref=ref,
        kind=SemanticKind.DOMAIN,
        name=model_ir.name,
        domain=model_ir.name,
        context=model_ir.ai_context,
        source_location=model_ir.location,
        _parents=(),
        _children=build_index.domain_children.get(model_ir.name, ()),
        _dependents=(),
        python_symbol="",
        owner=model_ir.owner,
        default=model_ir.default,
    )
    return _object_from_details(DomainEntry, details, catalog)


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


def _build_period_calendar_object(
    calendar_ir: PeriodCalendarIR,
    catalog: SemanticCatalog,
) -> PeriodCalendarEntry:
    ref = ref_factory.period_calendar(calendar_ir.semantic_id)
    date_ref = ref_factory.time_dimension(calendar_ir.date)
    levels = tuple((name, ref_factory.dimension(path)) for name, path in calendar_ir.levels)
    parents = (date_ref, *(value for _name, value in levels))
    # Slice 1 has no metric IR edge that can reference a semantic calendar.
    # Do not turn unrelated cumulative anchors into fabricated dependents;
    # reverse edges will be added when Slice 2 persists calendar Grain refs.
    dependents: tuple[Ref[SemanticKindTag], ...] = ()
    declared_levels = tuple((name, value) for name, value in levels)
    details = PeriodCalendarDetails(
        _ref=ref,
        kind=SemanticKind.PERIOD_CALENDAR,
        name=calendar_ir.name,
        domain=calendar_ir.domain,
        context=calendar_ir.ai_context,
        source_location=calendar_ir.location,
        _parents=parents,
        _children=(),
        _dependents=dependents,
        python_symbol=calendar_ir.python_symbol,
        _date=date_ref,
        boundary_timezone=calendar_ir.boundary_timezone,
        coverage=(
            date.fromisoformat(calendar_ir.coverage[0]),
            date.fromisoformat(calendar_ir.coverage[1]),
        ),
        levels=tuple(
            CalendarLevelDetails(
                name=name,
                _key_ref=cast("Ref[DimensionKind] | Ref[TimeDimensionKind]", value),
                period_count=None,
                direct_finer_levels=None,
                direct_coarser_levels=None,
                rollup_targets=None,
            )
            for name, value in (("day", date_ref), *declared_levels)
        ),
        _level_bindings=declared_levels,
        snapshot_status="missing",
    )
    return _object_from_details(PeriodCalendarEntry, details, catalog)


def _build_temporal_set_object(
    temporal_set_ir: TemporalSetIR,
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
        _ref=ref,
        kind=SemanticKind.TEMPORAL_SET,
        name=temporal_set_ir.name,
        domain=temporal_set_ir.domain,
        context=temporal_set_ir.ai_context,
        source_location=temporal_set_ir.location,
        _parents=parents,
        _children=(),
        _dependents=(),
        python_symbol=temporal_set_ir.python_symbol,
        boundary_timezone=temporal_set_ir.boundary_timezone,
        coverage=(
            date.fromisoformat(temporal_set_ir.coverage[0]),
            date.fromisoformat(temporal_set_ir.coverage[1]),
        ),
        _occurrence_id=occurrence_ref,
        _start=start_ref,
        _end=end_ref,
        _category=category_ref,
        occurrence_count=None,
        snapshot_status="missing",
    )
    return _object_from_details(TemporalSetEntry, details, catalog)


_PreviewCell: TypeAlias = str | int | float | bool | None


@dataclass(frozen=True)
class _CertificationCapture:
    """Exact current-source rows used only for one temporal certification."""

    scope: AuthoringScope
    columns: tuple[str, ...]
    rows_observed: int
    retained_values: tuple[dict[str, _PreviewCell], ...]

    def coverage(self, entity_id: str) -> PreviewCoverage:
        return PreviewCoverage(
            scopes=((entity_id, self.scope),),
            rows_observed=self.rows_observed,
            scope_exhaustion="exhaustive",
            scope_exactness="scope_exact",
        )


def _certification_capture(
    *,
    ref: Ref[SemanticKindTag],
    kind: SemanticKind,
    registry: Registry,
    bindings: NormalizedPreviewScope,
    resolver: SemanticResolver,
) -> _CertificationCapture:
    """Read one exhaustive bounded value capture for a certified artifact."""
    if len(bindings.entity_ids) != 1:
        _raise(
            ErrorKind.MATERIALIZE_FAILED,
            "Certified semantic artifacts require exactly one dependency entity.",
            cls=SemanticRuntimeError,
            refs=(ref.key,),
            details={"query_executed": False, "entities": bindings.entity_ids},
        )
    entity_id = bindings.entity_ids[0]
    scope = bindings.scopes[0][1]
    if kind is SemanticKind.PERIOD_CALENDAR:
        calendar = registry.period_calendars[ref.path]
        field_ids = (
            calendar.date,
            *(field for _name, field in calendar.levels),
        )
    elif kind is SemanticKind.TEMPORAL_SET:
        temporal_set = registry.temporal_sets[ref.path]
        field_ids = tuple(
            field
            for field in (
                temporal_set.occurrence_id,
                temporal_set.start,
                temporal_set.end,
                temporal_set.category,
            )
            if field is not None
        )
    else:
        raise AssertionError(f"unsupported certified artifact kind: {kind}")
    fields = tuple(registry.dimensions[field_id] for field_id in field_ids)
    if any(field.source_column is None for field in fields):
        _raise(
            ErrorKind.MATERIALIZE_FAILED,
            "Certified artifact fields must all use direct physical source columns.",
            cls=SemanticRuntimeError,
            refs=(ref.key,),
            details={"query_executed": False},
        )
    columns = tuple(dict.fromkeys(cast("str", field.source_column) for field in fields))
    connections = resolver.connections
    profile = require_profile_for_backend_type(bindings.backend)
    timeout_guard = profile.native_query_timeout
    with connections.terminal_scope(bindings.timeout_seconds):
        backend = connections.session_backend(bindings.datasource_id)
        with (
            cr.backend_errors(backend),
            (
                timeout_guard(backend, bindings.timeout_seconds)
                if timeout_guard is not None
                else nullcontext()
            ),
        ):
            table = resolver.entity(ref_factory.entity(entity_id)).select(*columns)
            if kind is SemanticKind.PERIOD_CALENDAR:
                date_field = registry.dimensions[registry.period_calendars[ref.path].date]
                date_column = date_field.source_column
                assert date_column is not None
                if date_field.parse is None and not table.schema()[date_column].is_date():
                    _raise(
                        ErrorKind.MATERIALIZE_FAILED,
                        "Period-calendar certification requires a native civil DATE spine.",
                        cls=SemanticRuntimeError,
                        refs=(ref.key, date_field.semantic_id),
                        expected="a native DATE field or an explicitly encoded civil-date field",
                        received=str(table.schema()[date_column]),
                        details={"query_executed": False},
                        repair_value=repair(
                            kind="reauthor",
                            canonical_id="time_dimension_column",
                            action=(
                                "Declare a native DATE source field, or explicitly derive the "
                                "business DATE in the authored source view, then retry "
                                "catalog.preview(..., scope=...) with complete coverage."
                            ),
                        ),
                    )
            source_owner = connections.source_session(
                bindings.datasource_id, registry.datasources[bindings.datasource_id]
            )
            try:
                frame = connections.collect_source(
                    bindings.datasource_id,
                    table,
                    purpose="semantic.certified_preview",
                    max_rows=scope.max_rows + 1,
                )
            except (DatasourceError, SemanticError):
                raise
            except Exception as error:
                failure = _backend_failure_summary(error)
                _raise(
                    ErrorKind.MATERIALIZE_FAILED,
                    "Certified artifact source capture failed before snapshot publication.",
                    cls=SemanticRuntimeError,
                    refs=(ref.key,),
                    expected="an exhaustive source capture within the scoped deadline",
                    received=failure.identity,
                    details={
                        "backend": bindings.backend,
                        "backend_code": failure.backend_code,
                        "backend_exception": failure.exception_type,
                        "query_executed": any(
                            item.purpose == "semantic.certified_preview"
                            for item in source_owner.submissions
                        ),
                        "timeout_seconds": bindings.timeout_seconds,
                    },
                    repair_value=repair(
                        kind="rescope",
                        canonical_id="preview",
                        action="Restore the selected source and retry certification with an exhaustive bounded scope and a sufficient timeout.",
                    ),
                )
    observed = len(frame)
    if observed > scope.max_rows:
        _raise(
            ErrorKind.MATERIALIZE_FAILED,
            "Certified artifact preview requires an exhaustive explicit scope.",
            cls=SemanticRuntimeError,
            refs=(ref.key,),
            details={
                "query_executed": True,
                "rows_observed": observed,
                "max_rows": scope.max_rows,
                "scope_exhaustion": "truncated",
            },
            repair_value=repair(
                kind="rescope",
                canonical_id="preview",
                action=(
                    "Retry catalog.preview(..., scope=...) with a complete bounded "
                    "source range and a row budget that admits every required row."
                ),
                preserves_evidence=False,
            ),
        )
    retained = frame.iloc[: scope.max_rows]
    schema = table.schema()
    return _CertificationCapture(
        scope=scope,
        columns=columns,
        rows_observed=observed,
        retained_values=tuple(
            {
                column: cast(
                    "str | int | float | bool | None",
                    (
                        value.date().isoformat()
                        if schema[column].is_date() and isinstance(value, datetime)
                        else normalize_preview_cell(value)
                    ),
                )
                for column, value in zip(columns, row, strict=True)
            }
            for row in retained.itertuples(index=False, name=None)
        ),
    )


def _preview_temporal_set(
    *,
    temporal_set_ref: Ref[TemporalSetKind],
    registry: Registry,
    project_root: Path,
    capture: _CertificationCapture,
    limit: int,
    dependency_digest: str,
) -> PreviewResult:
    """Certify a temporal-set artifact from one live exhaustive capture."""
    preview_limit = validate_preview_limit(limit)
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
            columns=capture.columns,
            retained_values=capture.retained_values,
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
        coverage=capture.coverage(occurrence_field.entity),
        sample_policy=PreviewSamplePolicy(method="bounded_limit", limit=preview_limit),
    )


def _preview_period_calendar(
    *,
    calendar_ref: Ref[PeriodCalendarKind],
    registry: Registry,
    project_root: Path,
    capture: _CertificationCapture,
    limit: int,
    dependency_digest: str,
) -> PreviewResult:
    """Certify a period-calendar artifact from one live exhaustive capture."""
    preview_limit = validate_preview_limit(limit)
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
            columns=capture.columns,
            retained_values=capture.retained_values,
            date_column=date_dimension.source_column,
            levels=level_columns,
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
                kind="rescope",
                canonical_id="preview",
                action=(
                    "Correct the calendar source values or declared coverage, then retry "
                    "catalog.preview(..., scope=...) with complete bounded coverage."
                ),
                preserves_evidence=False,
            ),
        )
    definition_digest = period_calendar_definition_digest(
        calendar_ref=calendar_ref,
        boundary_timezone=calendar.boundary_timezone,
        coverage=calendar.coverage,
        levels=calendar.levels,
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
        coverage=capture.coverage(date_dimension.entity),
        sample_policy=PreviewSamplePolicy(method="bounded_limit", limit=preview_limit),
    )


# ---------------------------------------------------------------------------
# _CatalogIndex — private query layer for typed catalog objects
# ---------------------------------------------------------------------------


class _CatalogIndex:
    """Immutable exact-ref and scoped-navigation index for one catalog.

    Entities, fields, published Metrics and Relationships are expression
    values resolved lazily from compiled state; the remaining kinds are entries.
    """

    def __init__(
        self,
        catalog: SemanticCatalog,
        project: SemanticProject,
        registry: Registry,
    ) -> None:
        self.values = CatalogValues(catalog._state)
        build_index = _CatalogBuildIndex(registry)
        datasource_irs = project._datasource_irs or tuple(registry.datasources.values())
        entries: tuple[CatalogEntry[SemanticKindTag], ...] = (
            *(
                _build_domain_object(item, build_index, catalog)
                for item in registry.domains.values()
            ),
            *(_build_datasource_object(item, build_index, catalog) for item in datasource_irs),
            *(
                _build_period_calendar_object(item, catalog)
                for item in registry.period_calendars.values()
            ),
            *(
                _build_temporal_set_object(item, catalog)
                for item in registry.temporal_sets.values()
            ),
        )
        self._entries = MappingProxyType({entry._ref: entry for entry in entries})
        self._refs: tuple[Ref[SemanticKindTag], ...] = tuple(
            sorted((*self._entries, *self.values.refs), key=lambda ref: ref.key)
        )
        known = frozenset(self._refs)
        scopes: dict[Ref[SemanticKindTag], frozenset[Ref[SemanticKindTag]]] = {}
        for ref in self._refs:
            owners: list[Ref[SemanticKindTag]] = []
            if ref.kind not in {SemanticKind.DOMAIN, SemanticKind.DATASOURCE}:
                owners.append(ref_factory.domain(ref.path.split(".", 1)[0]))
            if ref.kind is SemanticKind.ENTITY:
                owners.append(ref_factory.datasource(registry.entities[ref.path].datasource))
            scopes[ref] = frozenset(owner for owner in owners if owner in known)
        self._scopes = MappingProxyType(scopes)
        self._public_entries: Mapping[Ref[SemanticKindTag], SemanticEntry] = MappingProxyType(
            {
                ref: cast("SemanticEntry", self._entries[ref])
                if ref in self._entries
                else cast(
                    "SemanticEntry",
                    _ENTRY_TYPE_BY_KIND[ref.kind](_ref=ref, _details=None, _catalog=catalog),
                )
                for ref in self._refs
            }
        )

    def require(self, ref: Ref[SemanticKindTag]) -> CatalogItem | None:
        entry = self._entries.get(ref)
        if entry is not None:
            return entry
        if ref in self._scopes:
            return self.values.get(ref)
        return None

    def item(self, ref: Ref[SemanticKindTag]) -> CatalogItem:
        found = self.require(ref)
        if found is None:
            raise KeyError(ref.key)
        return found

    def entry(self, ref: Ref[SemanticKindTag]) -> CatalogEntry[SemanticKindTag] | None:
        return self._public_entries.get(ref)

    def members(
        self,
        kinds: tuple[SemanticKind, ...],
        *,
        scope_ref: Ref[SemanticKindTag] | None = None,
    ) -> tuple[Ref[SemanticKindTag], ...]:
        return tuple(
            ref
            for ref in self._refs
            if ref.kind in kinds and (scope_ref is None or scope_ref in self._scopes[ref])
        )

    def contains(
        self, ref: Ref[SemanticKindTag], *, scope_ref: Ref[SemanticKindTag] | None
    ) -> bool:
        return scope_ref is None or scope_ref in self._scopes[ref]


# ---------------------------------------------------------------------------
# SemanticCatalog
# ---------------------------------------------------------------------------


class SemanticCatalog(RenderableResult):
    """Immutable loaded business definitions with canonical discovery and execution handoff.

    Args: project: A successfully loaded SemanticProject.
    Returns: A Catalog with search, exact get, readiness and explicit preview operations.
    Example:
        >>> catalog = ms.load()
        >>> catalog.search("revenue", kind="metric").show()
        >>> revenue = catalog.get("metric:sales.revenue")
        >>> if revenue.kind == "metric":
        ...     value = revenue.value
    Constraints: Use ms.load() to construct a fresh snapshot. Search/get/show never
        read business datasources; native closed values are available through .value.
    """

    __slots__ = ("_documents", "_index", "_project", "_reg", "_scope", "_search_config", "_state")

    _project: SemanticProject
    _state: CompiledSemanticState
    _reg: Registry
    _index: _CatalogIndex
    _search_config: SearchConfig
    _documents: tuple[SearchDocument, ...]
    _scope: CatalogScope

    def __init__(self, project: SemanticProject) -> None:
        if project._compiled_state is None:
            raise SemanticLoadFailed(project.errors())
        object.__setattr__(self, "_project", project)
        object.__setattr__(self, "_state", project._compiled_state)
        object.__setattr__(self, "_reg", self._state.registry)
        object.__setattr__(self, "_index", _CatalogIndex(self, project, self._reg))
        from marivo.config import AUTHORED_DIR, load_semantic_layer_paths

        object.__setattr__(self, "_search_config", load_search_config(self.workspace_dir))
        object.__setattr__(
            self,
            "_scope",
            CatalogScope(
                str(self.workspace_dir),
                tuple(
                    str(item)
                    for item in (
                        self.workspace_dir / AUTHORED_DIR,
                        *load_semantic_layer_paths(self.workspace_dir),
                    )
                ),
                tuple(sorted(self._reg.domains)),
                self.definition_fingerprint,
            ),
        )
        object.__setattr__(self, "_documents", self._search_documents())

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

    def _repr_identity(self) -> str:
        fingerprint = self.definition_fingerprint[:12]
        return f"SemanticCatalog fingerprint={fingerprint} objects={len(self._index._refs)}"

    def _card(self) -> Card:
        counts = tuple(
            (kind.value, str(sum(ref.kind is kind for ref in self._index._refs)))
            for kind in SemanticKind
        )
        return (
            Card(
                identity=self._repr_identity(),
                available=(
                    ".search()",
                    '.get("kind:path")',
                    ".readiness()",
                    ".show()",
                ),
            )
            .field("workspace_dir", str(self.workspace_dir))
            .field("model_roots", ", ".join(self._scope.model_roots))
            .field("loaded_domains", ", ".join(self._scope.domains))
            .field("definition_fingerprint", self.definition_fingerprint)
            .table(columns=("kind", "count"), rows=counts, row_count=len(counts))
        )

    @staticmethod
    def _entry_ref_symbol(ref: Ref[SemanticKindTag]) -> LookupSymbol:
        return symbol_from_ref(ref)

    @staticmethod
    def _ref_from_id(id: str) -> Ref[SemanticKindTag]:
        if type(id) is not str or id.count(":") != 1:
            _raise(
                ErrorKind.INVALID_REF,
                "catalog.get(id) requires a canonical kind:path identity.",
                cls=SemanticRuntimeError,
                expected="canonical kind:path string",
                received=id if isinstance(id, str) else type(id).__name__,
                hint="Choose an id from catalog.search().items.",
            )
        kind, path = id.split(":", 1)
        try:
            return _make_ref(path, SemanticKind(kind))
        except (ValueError, TypeError) as exc:
            _raise(
                ErrorKind.INVALID_REF,
                str(exc),
                cls=SemanticRuntimeError,
                expected="canonical kind:path string",
                received=id,
                hint="Choose an id from catalog.search().items.",
            )

    def get(self, id: str, /) -> SemanticEntry:
        """Get one complete entry by its exact canonical identity.

        Args: id: Canonical kind:path string returned by search or Entry.id.
        Returns: A closed kind-discriminated immutable entry. Check kind before value.
        Example: ``entry = catalog.get("metric:sales.revenue")``.
        Constraints: Exact lookup never uses fuzzy replacement or calls a model/source.
        """
        ref = self._ref_from_id(id)
        entry = self._index.entry(ref)
        if entry is not None:
            return cast("SemanticEntry", entry)
        known_outside = any(ref in self._entry_dependencies(item) for item in self._index._refs)
        if known_outside:
            _raise(
                ErrorKind.NOT_FOUND,
                f"Canonical identity {id!r} is referenced but outside this Catalog's loaded scope.",
                cls=SemanticRuntimeError,
                refs=(id,),
                expected="a required identity included in the explicit load scope",
                received=id,
                hint="Create a new Catalog with ms.load(domains=...) including the required domain and its dependencies.",
                repair_value=repair(
                    kind="reload",
                    canonical_id="load",
                    action="Explicitly reload with the referenced domain and its required dependency domains; the current Catalog is unchanged.",
                    candidates=(id,),
                ),
                details={"reason": "outside_scope", "loaded_domains": self._scope.domains},
            )
        same_path = tuple(item.key for item in self._index._refs if item.path == ref.path)
        candidates = tuple(
            item.key
            for item in sorted(
                (item for item in self._index._refs if item.kind is ref.kind),
                key=lambda item: (-SequenceMatcher(None, ref.path, item.path).ratio(), item.key),
            )[:_SEMANTIC_INPUT_CANDIDATE_LIMIT]
        )
        _raise(
            ErrorKind.NOT_FOUND,
            f"Canonical identity {id!r} was not found in this loaded Catalog.",
            cls=SemanticRuntimeError,
            refs=(id,),
            expected="an exact loaded kind:path identity",
            received=id,
            hint="Use catalog.search(query, kind=...) to discover current identities.",
            repair_value=repair(
                kind="inspect",
                canonical_id="semantic_catalog.get",
                action="Choose an existing canonical identity; no replacement was applied.",
                candidates=same_path or candidates,
            ),
            details={
                "reason": "wrong_kind" if same_path else "not_found",
                "actual_identities": same_path,
                "loaded_domains": self._scope.domains,
                "candidates": candidates,
            },
        )

    def _entry_dependencies(self, ref: Ref[SemanticKindTag]) -> tuple[Ref[SemanticKindTag], ...]:
        dependencies = set(self._state.dependencies.get(ref, ()))
        if ref.kind is SemanticKind.RELATIONSHIP:
            relationship = self._reg.relationships[ref.path]
            dependencies.update(
                (
                    ref_factory.entity(relationship.from_entity),
                    ref_factory.entity(relationship.to_entity),
                )
            )
        return tuple(sorted(dependencies, key=lambda item: item.key))

    def _search_documents(self) -> tuple[SearchDocument, ...]:
        from marivo.semantic._catalog_definition import context_for

        documents: list[SearchDocument] = []
        for ref in self._index._refs:
            entry = self._index.entry(ref)
            assert entry is not None
            definition = entry.definition
            context = definition.context
            fields: list[tuple[str, str]] = [
                ("business_definition", context.business_definition or "")
            ]
            fields.extend(("alias", alias) for alias in context.aliases)
            fields.extend(("guardrail", item) for item in context.guardrails)
            if entry.domain is not None:
                domain_ref = ref_factory.domain(entry.domain)
                fields.append(
                    (
                        "domain.business_definition",
                        context_for(self, domain_ref).business_definition or "",
                    )
                )
            if isinstance(definition, MetricDefinition):
                fields.extend((("formula", definition.formula), ("unit", definition.unit or "")))
                fields.extend(("condition", item) for item in definition.conditions)
                fields.extend(
                    ("time_binding", f"{item.field} {item.source} default={item.entity_default}")
                    for item in definition.time_bindings
                )
            elif isinstance(definition, EntityDefinition):
                fields.append(("expression", definition.expression))
            elif isinstance(definition, FieldDefinition):
                fields.extend(
                    (("expression", definition.expression), ("unit", definition.unit or ""))
                )
            for dependency in self._state.dependencies.get(ref, ()):
                if dependency in self._index._refs:
                    inherited = context_for(self, dependency)
                    fields.append(
                        (
                            f"dependency.{dependency.key}",
                            f"{dependency.name} {inherited.business_definition or ''}",
                        )
                    )
                    fields.extend(
                        (f"guardrail.{dependency.key}", item) for item in inherited.guardrails
                    )
            entity = (
                definition.roots[0].removeprefix("entity:")
                if isinstance(definition, MetricDefinition) and len(definition.roots) == 1
                else definition.entity.removeprefix("entity:")
                if isinstance(definition, FieldDefinition)
                else ref.path
                if ref.kind is SemanticKind.ENTITY
                else None
            )
            documents.append(
                SearchDocument(
                    entry.id,
                    entry.kind,
                    entry.path,
                    entry.name,
                    entry.domain,
                    entity,
                    tuple(fields),
                )
            )
        return tuple(documents)

    @overload
    def search(
        self,
        query: str = "",
        *,
        kind: Literal["domain"],
        domain: str | None = None,
        entity: str | None = None,
        limit: int = 10,
        cursor: str | None = None,
    ) -> CatalogPage[DomainEntry]: ...

    @overload
    def search(
        self,
        query: str = "",
        *,
        kind: Literal["datasource"],
        domain: str | None = None,
        entity: str | None = None,
        limit: int = 10,
        cursor: str | None = None,
    ) -> CatalogPage[DatasourceEntry]: ...

    @overload
    def search(
        self,
        query: str = "",
        *,
        kind: Literal["entity"],
        domain: str | None = None,
        entity: str | None = None,
        limit: int = 10,
        cursor: str | None = None,
    ) -> CatalogPage[EntityEntry]: ...

    @overload
    def search(
        self,
        query: str = "",
        *,
        kind: Literal["dimension"],
        domain: str | None = None,
        entity: str | None = None,
        limit: int = 10,
        cursor: str | None = None,
    ) -> CatalogPage[DimensionEntry]: ...

    @overload
    def search(
        self,
        query: str = "",
        *,
        kind: Literal["time_dimension"],
        domain: str | None = None,
        entity: str | None = None,
        limit: int = 10,
        cursor: str | None = None,
    ) -> CatalogPage[TimeDimensionEntry]: ...

    @overload
    def search(
        self,
        query: str = "",
        *,
        kind: Literal["measure"],
        domain: str | None = None,
        entity: str | None = None,
        limit: int = 10,
        cursor: str | None = None,
    ) -> CatalogPage[MeasureEntry]: ...

    @overload
    def search(
        self,
        query: str = "",
        *,
        kind: Literal["metric"],
        domain: str | None = None,
        entity: str | None = None,
        limit: int = 10,
        cursor: str | None = None,
    ) -> CatalogPage[MetricEntry]: ...

    @overload
    def search(
        self,
        query: str = "",
        *,
        kind: Literal["relationship"],
        domain: str | None = None,
        entity: str | None = None,
        limit: int = 10,
        cursor: str | None = None,
    ) -> CatalogPage[RelationshipEntry]: ...

    @overload
    def search(
        self,
        query: str = "",
        *,
        kind: Literal["period_calendar"],
        domain: str | None = None,
        entity: str | None = None,
        limit: int = 10,
        cursor: str | None = None,
    ) -> CatalogPage[PeriodCalendarEntry]: ...

    @overload
    def search(
        self,
        query: str = "",
        *,
        kind: Literal["temporal_set"],
        domain: str | None = None,
        entity: str | None = None,
        limit: int = 10,
        cursor: str | None = None,
    ) -> CatalogPage[TemporalSetEntry]: ...

    @overload
    def search(
        self,
        query: str = "",
        *,
        kind: str | None = None,
        domain: str | None = None,
        entity: str | None = None,
        limit: int = 10,
        cursor: str | None = None,
    ) -> CatalogPage[SemanticEntry]: ...

    def search(
        self,
        query: str = "",
        *,
        kind: str | None = None,
        domain: str | None = None,
        entity: str | None = None,
        limit: int = 10,
        cursor: str | None = None,
    ) -> CatalogPage[SemanticEntry]:
        """Search captured business definitions, or browse with an empty query.

        Args:
            query: Business intent, alias, name or identity.
            kind: Exact semantic kind.
            domain: Owning domain filter.
            entity: Full owning Entity path.
            limit: Page size from 1 to 100.
            cursor: Previous page continuation.

        Returns:
            Complete typed page with scope, filters, query matches and retrieval facts.

        Example:
            catalog.search("revenue", kind="metric", limit=5).show()

        Constraints:
            Empty query invokes no model. Text search uses frozen configuration.
            Search never accesses business datasources or changes the loaded scope.
        """
        if kind is not None and kind not in {item.value for item in SemanticKind}:
            _raise(
                ErrorKind.INVALID_REF,
                f"Unknown catalog search kind {kind!r}.",
                cls=SemanticRuntimeError,
                expected=", ".join(item.value for item in SemanticKind),
                received=kind,
                hint="Use one exact semantic kind, such as metric or dimension.",
            )
        result = search_documents(
            self._documents,
            workspace_dir=self.workspace_dir,
            snapshot=self.definition_fingerprint,
            query=query,
            kind=kind,
            domain=domain,
            entity=entity,
            limit=limit,
            cursor=cursor,
            config=self._search_config,
        )
        return CatalogPage(
            tuple(self.get(hit.id) for hit in result.items),
            result.candidate_count,
            self._scope,
            CatalogFilters(query, kind, domain, entity),
            result.retrieval,
            {hit.id: hit for hit in result.items} if query.strip() else {},
            result.next_cursor,
        )

    def _related(
        self,
        entry: CatalogEntry[SemanticKindTag],
        relation: RelatedRelation,
        *,
        limit: int,
        cursor: str | None,
    ) -> CatalogPage[SemanticEntry]:
        relations = {"dependencies", "dependents", "fields", "metrics", "relationships", "members"}
        if relation not in relations:
            _raise(
                ErrorKind.INVALID_REF,
                f"Unknown catalog relation {relation!r}.",
                cls=SemanticRuntimeError,
                expected=", ".join(sorted(relations)),
                received=relation,
                hint="Choose an explicit related relation.",
            )
        if type(limit) is not int or not 1 <= limit <= 100:
            _raise(
                ErrorKind.INVALID_REF,
                "Related page limit must be an integer from 1 to 100.",
                cls=SemanticRuntimeError,
                expected="integer 1..100",
                received=str(limit),
                hint="Pass limit=10.",
            )
        ref = entry._ref
        dependencies = self._state.dependencies
        selected: set[Ref[SemanticKindTag]] = set()
        if relation == "dependencies":
            selected.update(self._entry_dependencies(ref))
        elif relation == "dependents":
            selected.update(item for item, reads in dependencies.items() if ref in reads)
        else:
            owners = (
                {ref.path}
                if ref.kind is SemanticKind.ENTITY
                else {
                    item.path
                    for item in _physical_dependencies(self, ref)
                    if item.kind is SemanticKind.ENTITY
                }
            )
            owners.update(
                item.path.rsplit(".", 1)[0]
                for item in _physical_dependencies(self, ref)
                if item.kind
                in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION, SemanticKind.MEASURE}
            )
            if ref.kind in {
                SemanticKind.DIMENSION,
                SemanticKind.TIME_DIMENSION,
                SemanticKind.MEASURE,
            }:
                owners.add(ref.path.rsplit(".", 1)[0])
            if relation == "fields":
                selected.update(
                    item
                    for item in self._index._refs
                    if item.kind
                    in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION, SemanticKind.MEASURE}
                    and item.path.rsplit(".", 1)[0] in owners
                )
            elif relation == "metrics":
                selected.update(
                    item
                    for item in self._index._refs
                    if item.kind is SemanticKind.METRIC
                    and (
                        ref in dependencies.get(item, ())
                        or any(
                            (
                                dependency.path
                                if dependency.kind is SemanticKind.ENTITY
                                else dependency.path.rsplit(".", 1)[0]
                            )
                            in owners
                            for dependency in _physical_dependencies(self, item)
                        )
                    )
                )
            elif relation == "relationships":
                selected.update(
                    ref_factory.relationship(item.semantic_id)
                    for item in self._reg.relationships.values()
                    if item.from_entity in owners or item.to_entity in owners
                )
            elif ref.kind is SemanticKind.DOMAIN:
                selected.update(
                    item
                    for item in self._index._refs
                    if item.kind not in {SemanticKind.DOMAIN, SemanticKind.DATASOURCE}
                    and item.path.split(".")[0] == ref.path
                )
            elif ref.kind is SemanticKind.DATASOURCE:
                selected.update(
                    ref_factory.entity(item.semantic_id)
                    for item in self._reg.entities.values()
                    if item.datasource == ref.path
                )
        loaded = tuple(
            sorted(
                (item for item in selected if item in self._index._refs), key=lambda item: item.key
            )
        )
        outside = tuple(sorted(item.key for item in selected if item not in self._index._refs))
        token = f"{entry.id}:{relation}"
        try:
            offset = _decode_period_cursor(
                cursor, snapshot_digest=self.definition_fingerprint, level=token
            )
        except ValueError as exc:
            _raise(
                ErrorKind.INVALID_REF,
                str(exc),
                cls=SemanticRuntimeError,
                expected="cursor from this entry/relation/snapshot",
                received="cursor",
                hint="Restart related() without cursor after loading a new Catalog.",
            )
        page_refs = loaded[offset : offset + limit]
        next_offset = offset + len(page_refs)
        next_cursor = (
            _encode_period_cursor(self.definition_fingerprint, token, next_offset)
            if next_offset < len(loaded)
            else None
        )
        return CatalogPage(
            tuple(self.get(item.key) for item in page_refs),
            len(loaded),
            self._scope,
            CatalogFilters("", origin=entry.id, relation=relation),
            RelatedRetrieval(candidate_window=len(loaded)),
            {},
            next_cursor,
            outside,
        )

    def _require_ref(self, ref: Ref[SemanticKindTag]) -> None:
        """Validate exact internal identity in this compiled scope."""
        exact_ref = ref
        index = self._require_index()
        if exact_ref in index._refs:
            return

        candidates = tuple(item.key for item in index._refs if item.kind is exact_ref.kind)[
            :_SEMANTIC_INPUT_CANDIDATE_LIMIT
        ]
        _raise(
            ErrorKind.NOT_FOUND,
            f"Ref {exact_ref.key!r} is not present in this compiled catalog.",
            cls=SemanticRuntimeError,
            refs=(exact_ref.key,),
            expected=f"an existing {exact_ref.kind.value} in this compiled catalog",
            received=exact_ref.key,
            hint=f'Browse catalog.search(kind="{exact_ref.kind.value}").show() for current identities.',
            repair_value=repair(
                kind="inspect",
                canonical_id="semantic_catalog.get",
                action="Choose an existing canonical identity from catalog.search().",
                candidates=candidates,
            ),
            details={
                "catalog_definition_fingerprint": self.definition_fingerprint,
                "filtered_domains": self._project._filtered_domains,
                "candidates": candidates,
            },
        )

    def _describe(self, ref: LookupSymbol, /) -> ExpressionContract:
        """Describe one Entity, field, Metric or Relationship without source access.

        Args:
            ref: A native semantic lookup symbol present in this Catalog.
        Returns:
            Bounded definition facts, dependencies and available expression operations.
        Example:
            >>> catalog.describe(ms.ref.metric("sales.revenue")).show()
        Constraints:
            Physical dtypes and execution qualification belong to Query consumption.
            The returned facts do not mutate or add attributes to native Deferred values.
        """
        from marivo._definition.graph import computation_fingerprint
        from marivo.semantic._deferred_semantics import (
            _rescale_disclosure,
            consume_metric,
            consume_row,
        )
        from marivo.semantic.expressions import _Resolver

        target = _require_semantic_ref(ref, parameter="describe(ref)")
        value = self._require_index().require(target)
        if isinstance(value, (Entity, Dimension, TimeField, Measure, Relationship)):
            return value.contract()
        if isinstance(value, Deferred):
            declaration = root_declaration(value)
            assert declaration is not None
            from marivo._definition.expressions import MetricNode, RowNode
            from marivo._definition.types import ExactRef, SumPermission

            roots: tuple[ExactRef, ...]
            permission: SumPermission | None
            node: RowNode | MetricNode
            if declaration.kind == "measure":
                row = consume_row(value)
                roots, unit, permission, default_time = (
                    (row.root,),
                    row.unit,
                    row.permission,
                    row.default_time,
                )
                node = row.node
            else:
                original = self._reg.declared_metrics[target.path]
                metric = consume_metric(original)
                roots, unit, permission, default_time = metric.roots, metric.unit, None, None
                node = metric.node
            context = declaration.ai_context
            location = declaration.location
            return ExpressionContract(
                stage="declared_measure" if declaration.kind == "measure" else "published_metric",
                _ref=_Resolver(self._state).exact(target),
                _roots=roots,
                unit=unit,
                sum_permission=permission,
                _default_time=default_time,
                computation=computation_fingerprint(node),
                _reads=self._state.dependencies.get(target, ()),
                name=declaration.name,
                business_definition=context.business_definition if context else None,
                guardrails=tuple(context.guardrails or ()) if context else (),
                location=f"{location.path}:{location.line}" if location else None,
                facts=_rescale_disclosure(value),
                operations=(
                    "native Ibis numerical composition",
                    "sum/mean" if declaration.kind == "measure" else "where/per/window",
                ),
            )
        _raise(
            ErrorKind.INVALID_REF,
            "Describe requires an Entity, field, Metric or Relationship in this Catalog.",
            cls=SemanticRuntimeError,
            refs=(target.key,),
        )

    def readiness(
        self,
        refs: Sequence[_SemanticInput] | None = None,
    ) -> ReadinessReport:
        """Check physical semantic inputs and certified temporal artifacts without querying.

        Args:
            refs: Current physical catalog values or entries, or exact typed refs.
                None checks all loaded physical objects. Explicit selections must
                be nonempty and unique; their full dependency closures are checked.

        Returns:
            A ReadinessReport with the directly selected physical inputs whose
            dependency closures have no blocker.

        Example:
            >>> amount = catalog.get("measure:sales.orders.amount")
            >>> catalog.readiness(refs=[amount]).show()

        Constraints:
            No datasource connection or query is opened. Selected Measure/Metric
            entries expand their loaded physical dependencies. Requested and checked
            identities remain separate; Query preparation owns execution support.
        """
        self._require_ready()
        if refs is None:
            return self._project._readiness(refs=None)
        inputs = tuple(_require_readiness_input(self, value) for value in refs)
        if not inputs:
            _raise(
                ErrorKind.INVALID_REF,
                "catalog.readiness(refs=...) requires a non-empty sequence.",
                cls=SemanticRuntimeError,
            )
        duplicates = tuple(
            dict.fromkeys(value for index, value in enumerate(inputs) if value in inputs[:index])
        )
        if duplicates:
            _raise(
                ErrorKind.INVALID_REF,
                "catalog.readiness(refs=...) requires unique exact refs; received "
                f"{[ref.key for ref in duplicates]}.",
                cls=SemanticRuntimeError,
                refs=tuple(ref.key for ref in duplicates),
            )
        physical = tuple(
            sorted(
                {
                    dependency
                    for item in inputs
                    for dependency in _physical_dependencies(self, item)
                },
                key=lambda item: item.key,
            )
        )
        outside = {
            dependency
            for item in physical
            for dependency in self._entry_dependencies(item)
            if dependency not in self._index._refs
        }
        report = self._project._readiness(
            refs=(*physical, *sorted(outside, key=lambda item: item.key))
        )
        checked: set[Ref[SemanticKindTag]] = set()
        pending = list(physical)
        while pending:
            current = pending.pop()
            if current in checked:
                continue
            checked.add(current)
            pending.extend(
                self._entry_dependencies(current) if current in self._index._refs else ()
            )
            if current.kind in {SemanticKind.DOMAIN, SemanticKind.DATASOURCE}:
                pending.extend(
                    item for item, scopes in self._index._scopes.items() if current in scopes
                )
        return replace(
            report,
            # Expanded dependencies are diagnostics, not directly requested inputs.
            _analysis_ready_inputs=tuple(
                ref for ref in report._analysis_ready_inputs if ref in inputs
            ),
            requested_ids=tuple(item.key for item in inputs),
            checked_ids=tuple(sorted(item.key for item in checked)),
        )

    def source_health(
        self,
        refs: Sequence[_SemanticInput],
        /,
        *,
        checks: Sequence[SourceCheck] = (),
        scope: PreviewScope | None = None,
    ) -> SourceHealthReport:
        """Check current source access, schema, and explicit data expectations.

        Connectivity and metadata checks run for every selected source. Data
        expectations run only when supplied through ``ms.source_check`` and
        require an explicit bounded ``scope``. The result is independent of
        semantic readiness and is not persisted.

        Args:
            refs: Non-empty unique current semantic entries or exact refs whose
                physical Entity sources should be checked.
            checks: Optional explicit expectations built by ``ms.source_check``.
                No null, enum, uniqueness, freshness, relationship, or
                cardinality expectation is inferred.
            scope: Required for data-reading checks and forbidden otherwise.
                Use one ``AuthoringScope`` for one checked Entity or an exact
                Entity symbol/scope pair sequence for multiple checked Entities.

        Returns:
            SourceHealthReport with per-check status, observed identities,
            affected refs, repair direction, and query/scope disclosure.
        """
        self._require_ready()
        values = tuple(refs)
        if not values:
            _raise(
                ErrorKind.INVALID_REF,
                "catalog.source_health(refs=...) requires a non-empty sequence.",
                cls=SemanticRuntimeError,
                details={"query_executed": False},
            )
        exact_refs = tuple(
            _normalize_semantic_input(
                self,
                value,
                allowed_kinds=_ALL_SEMANTIC_KINDS,
                location="catalog.source_health(refs=...)",
            )
            for value in values
        )
        duplicates = tuple(
            dict.fromkeys(
                value for index, value in enumerate(exact_refs) if value in exact_refs[:index]
            )
        )
        if duplicates:
            _raise(
                ErrorKind.INVALID_REF,
                "catalog.source_health(refs=...) requires unique exact refs.",
                cls=SemanticRuntimeError,
                refs=tuple(ref.key for ref in duplicates),
                details={"query_executed": False},
            )
        from marivo.semantic.source_health import run_source_health

        return run_source_health(
            self,
            refs=exact_refs,
            checks=checks,
            scope=scope,
        )

    def _semantic_resolver(
        self,
        *,
        connections: object,
        sample_size: int | None = None,
        entity_scopes: Mapping[str, AuthoringScope] | None = None,
        source_bindings: (
            Mapping[str, Mapping[str, QueryParamScalar | QueryParamScalarList]] | None
        ) = None,
    ) -> SemanticResolver:
        """Return an internal resolver backed by Materializer."""
        self._require_ready()
        if source_bindings is None:
            binding_provider = getattr(connections, "source_bindings", None)
            if callable(binding_provider):
                source_bindings = binding_provider()
        from marivo.semantic.resolver import SemanticResolver

        return SemanticResolver(
            self,
            connections=connections,
            sample_size=sample_size,
            entity_scopes=entity_scopes,
            source_bindings=source_bindings,
        )

    def preview(
        self,
        ref: _SemanticInput,
        /,
        *,
        scope: PreviewScope,
        source_bindings: PreviewSourceBindings | None = None,
        limit: int = PREVIEW_DEFAULT_LIMIT,
        include_types: bool = True,
        context_columns: Iterable[str] | None = None,
    ) -> PreviewResult:
        """Return one bounded runtime preview for a current entry or exact ref.

        Args:
            ref: A current catalog entry or exact member ref.
            scope: One explicit authoring scope, or exact entity-to-scope
                bindings covering all dependency Entities when the ref spans
                multiple entities. Use ``md.unpruned`` for no pruning,
                ``md.time_range`` for a physical time range, or ``md.partition``
                for physical partition values; each requires row/time guards.
            source_bindings: Optional exact entity-to-parameter bindings for
                parameterized JSON sources.
            limit: Integer from 1 to 100 inclusive bounding retained preview rows.
            include_types: Include physical type facts in the result.
            context_columns: Optional context columns for field previews.

        Returns:
            A bounded PreviewResult for the normalized canonical ref.

        Example:
            >>> revenue = catalog.get("metric:sales.revenue")
            >>> preview = catalog.preview(
            ...     revenue,
            ...     scope=md.unpruned(max_rows=1000, timeout_seconds=30),
            ... )

        Constraints:
            Input ownership and membership are checked before connection
            acquisition or materialization. ``limit`` is independent from the
            ``scope.max_rows`` acquisition guard and does not reduce temporal
            certification coverage. Ordinary Metric previews require DuckDB,
            aggregate at most 10,000 scoped input rows and are approximate.
            For other backends, use session.observe(...).compute() with explicit
            analysis inputs to evaluate the Metric.
        """
        limit = normalize_preview_limit(limit, operation="preview")
        target = _normalize_semantic_input(
            self,
            ref,
            allowed_kinds=_ALL_SEMANTIC_KINDS,
            location="catalog.preview(ref)",
            metric_preview=True,
        )
        bindings = normalize_preview_scope(
            ref=target.path,
            kind=target.kind,
            scope=scope,
            source_bindings=source_bindings,
            registry=self._require_ready(),
        )
        with self._project._connection_operation() as connections:
            return self._preview_one(
                target,
                bindings=bindings,
                connections=connections,
                limit=limit,
                include_types=include_types,
                context_columns=context_columns,
            )

    def preview_many(
        self,
        refs: Sequence[_SemanticInput],
        /,
        *,
        scope: PreviewScope,
        source_bindings: PreviewSourceBindings | None = None,
        limit: int = PREVIEW_DEFAULT_LIMIT,
        include_types: bool = True,
    ) -> PreviewBatchResult:
        """Return a bounded batch preview for ordered current entries or refs.

        Args:
            refs: A non-empty ordered sequence of current entries or exact refs.
            scope: One explicit authoring scope when all refs share one entity,
                or exact entity-to-scope bindings otherwise.
            source_bindings: Optional exact entity-to-parameter bindings for
                parameterized JSON sources.
            limit: Integer from 1 to 100 inclusive bounding retained rows per result.
            include_types: Include physical type facts in each result.

        Returns:
            PreviewBatchResult in the same order as the normalized inputs.

        Example:
            >>> amount = catalog.get("measure:sales.orders.amount")
            >>> region = catalog.get("dimension:sales.orders.region")
            >>> batch = catalog.preview_many(
            ...     [region, amount],
            ...     scope=md.unpruned(max_rows=1000, timeout_seconds=30),
            ... )

        Constraints:
            The complete input sequence is normalized before any preview
            begins. Duplicate canonical refs are rejected. ``limit`` is
            independent from the ``scope.max_rows`` acquisition guard. Ordinary
            Metric previews require DuckDB, aggregate at most 10,000 Entity output
            rows and are approximate. For other backends, evaluate Metrics with
            session.observe(...).compute() and explicit analysis inputs.
        """
        limit = normalize_preview_limit(limit, operation="preview_many")
        normalized_refs = tuple(
            _normalize_semantic_input(
                self,
                value,
                allowed_kinds=_ALL_SEMANTIC_KINDS,
                location=f"catalog.preview_many(refs)[{index}]",
                metric_preview=True,
            )
            for index, value in enumerate(refs)
        )
        with self._project._connection_operation() as connections:
            return self._preview_batch(
                normalized_refs,
                scope=scope,
                connections=connections,
                source_bindings=source_bindings,
                limit=limit,
                include_types=include_types,
            )

    def _preview_one(
        self,
        ref: Ref[SemanticKindTag],
        *,
        bindings: NormalizedPreviewScope,
        connections: DatasourceConnectionService,
        limit: int = PREVIEW_DEFAULT_LIMIT,
        include_types: bool = True,
        context_columns: Iterable[str] | None = None,
    ) -> PreviewResult:
        """Execute the existing one-object preview contract."""
        reg = self._require_ready()
        ref_obj = ref
        self._require_ref(ref_obj)
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
        if kind is SemanticKind.METRIC:
            from marivo.semantic._numeric_preview import validate_preview_metric

            metric = self._index.values.get(ref_obj)
            assert isinstance(metric, Deferred)
            validate_preview_metric(metric)
        _require_metric_preview_backend(ref_obj, bindings)
        if kind in {
            SemanticKind.PERIOD_CALENDAR,
            SemanticKind.TEMPORAL_SET,
        }:
            validate_preview_limit(limit)
        if kind is SemanticKind.PERIOD_CALENDAR:
            from marivo.semantic._definition_identity import scoped_definition_fingerprint

            resolver = self._semantic_resolver(
                connections=connections,
                entity_scopes=bindings.entity_scopes,
                source_bindings=bindings.source_bindings,
            )

            return _preview_period_calendar(
                calendar_ref=cast("Ref[PeriodCalendarKind]", ref_obj),
                registry=reg,
                project_root=self.workspace_dir,
                capture=_certification_capture(
                    ref=ref_obj,
                    kind=kind,
                    registry=reg,
                    bindings=bindings,
                    resolver=resolver,
                ),
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

            resolver = self._semantic_resolver(
                connections=connections,
                entity_scopes=bindings.entity_scopes,
                source_bindings=bindings.source_bindings,
            )

            return _preview_temporal_set(
                temporal_set_ref=cast("Ref[TemporalSetKind]", ref_obj),
                registry=reg,
                project_root=self.workspace_dir,
                capture=_certification_capture(
                    ref=ref_obj,
                    kind=kind,
                    registry=reg,
                    bindings=bindings,
                    resolver=resolver,
                ),
                limit=limit,
                dependency_digest=scoped_definition_fingerprint(
                    root=ref_obj,
                    definitions=self._state.definitions,
                    dependencies=self._state.dependencies,
                    sidecar=self._state.sidecar,
                ),
            )
        preview_limit = validate_preview_limit(limit)
        row_preview_limit = min(
            preview_limit,
            *(bound_scope.max_rows for _entity_id, bound_scope in bindings.scopes),
        )
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
        timeout = profile.native_query_timeout

        def execute_preview() -> PreviewResult:
            from functools import partial

            preview_with_source = partial(
                preview_ibis_table,
                read_table=lambda expression, bound: connections.collect_source(
                    bindings.datasource_id,
                    expression,
                    purpose="semantic.preview",
                    max_rows=bound,
                ),
            )
            resolver = self._semantic_resolver(
                connections=connections,
                sample_size=(METRIC_PREVIEW_SAMPLE_SIZE if kind == SemanticKind.METRIC else None),
                entity_scopes=bindings.entity_scopes,
                source_bindings=bindings.source_bindings,
            )
            if kind is SemanticKind.METRIC:
                from marivo.semantic._numeric_lowering import numeric_failure
                from marivo.semantic._numeric_preview import lower_preview_metric

                metric = self._index.values.get(ref_obj)
                assert isinstance(metric, Deferred)
                expression, checks = lower_preview_metric(metric, resolver)
                frame = connections.collect_source(
                    bindings.datasource_id, expression, purpose="semantic.preview", max_rows=1
                )
                for name, check in checks:
                    if frame[name].fillna(False).any():
                        numeric_failure(check.expected, f"{ref_str}: {check.received}")
                return preview_from_pandas(
                    frame.loc[:, ["value"]],
                    kind="semantic_metric",
                    ref=ref_str,
                    requested_limit=preview_limit,
                    sample_policy=PreviewSamplePolicy(
                        method="pre_aggregate_limit", limit=METRIC_PREVIEW_SAMPLE_SIZE
                    ),
                    types={"value": str(expression.value.type())} if include_types else {},
                    warnings=(_metric_preview_warning(),),
                )
            if kind == SemanticKind.ENTITY:
                table = resolver.entity(_make_ref(ref_str, SemanticKind.ENTITY))
                return preview_with_source(
                    table,
                    kind="semantic_dataset",
                    ref=ref_str,
                    limit=row_preview_limit,
                    sample_policy=PreviewSamplePolicy(
                        method="bounded_limit", limit=row_preview_limit
                    ),
                    include_types=include_types,
                    report_tz=system_timezone_name(),
                )
            if kind == SemanticKind.MEASURE:
                measure_ir = reg.measures[ref_str]
                parent_table = resolver.entity(_make_ref(measure_ir.entity, SemanticKind.ENTITY))
                measure_value = resolver.measure(_make_ref(ref_str, SemanticKind.MEASURE))
                measure_column_name = ref_str.rsplit(".", 1)[-1]
                preview_table = parent_table.select(measure_value.name(measure_column_name))
                return preview_with_source(
                    preview_table,
                    kind="semantic_measure",
                    ref=ref_str,
                    limit=row_preview_limit,
                    sample_policy=PreviewSamplePolicy(
                        method="bounded_limit", limit=row_preview_limit
                    ),
                    include_types=include_types,
                    report_tz=system_timezone_name(),
                )
            if is_field_preview:
                field_ir = reg.dimensions[ref_str]
                parent_table = resolver.entity(_make_ref(field_ir.entity, SemanticKind.ENTITY))
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
                result = preview_with_source(
                    preview_table,
                    kind="semantic_field",
                    ref=ref_str,
                    limit=row_preview_limit,
                    sample_policy=PreviewSamplePolicy(
                        method="bounded_limit", limit=row_preview_limit
                    ),
                    include_types=include_types,
                    timezones=_preview_timezones_for_field(
                        column_name=field_column_name,
                        field_ir=field_ir,
                        datasource_timezone=datasource_timezone,
                        report_tz=report_tz,
                    ),
                    report_tz=report_tz,
                )
                if kind is SemanticKind.TIME_DIMENSION:
                    result = _with_time_parse_risk(
                        result,
                        ref=ref_str,
                        field=field_ir,
                    )
                return result
            if kind == SemanticKind.RELATIONSHIP:
                relationship = reg.relationships[ref_str]
                left = resolver.entity(_make_ref(relationship.from_entity, SemanticKind.ENTITY))
                right = resolver.entity(_make_ref(relationship.to_entity, SemanticKind.ENTITY))
                left_names: list[str] = []
                right_names: list[str] = []
                left_values = []
                right_values = []
                for index, key in enumerate(relationship.keys, start=1):
                    from_key, to_key = key.from_key, key.to_key
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
                return preview_with_source(
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

        connection_scope = (
            connections.terminal_scope(bindings.timeout_seconds)
            if bindings.backend in {"trino", "postgres", "mysql"}
            else nullcontext()
        )
        with connection_scope:
            backend = connections.session_backend(bindings.datasource_id)
            with (
                cr.backend_errors(backend),
                (
                    timeout(backend, bindings.timeout_seconds)
                    if timeout is not None
                    else nullcontext()
                ),
            ):
                result = execute_preview()
        return _attach_preview_scope(
            result,
            bindings=bindings,
            approximate_input=kind is SemanticKind.METRIC,
        )

    def _preview_batch(
        self,
        refs: Sequence[Ref[SemanticKindTag]],
        *,
        scope: PreviewScope,
        connections: DatasourceConnectionService,
        source_bindings: PreviewSourceBindings | None,
        limit: int,
        include_types: bool,
    ) -> PreviewBatchResult:
        reg = self._require_ready()
        if not refs:
            _raise(
                ErrorKind.INVALID_REF,
                "catalog.preview_many(refs, scope=...) requires a non-empty sequence.",
                cls=SemanticRuntimeError,
                details={"query_executed": False},
            )
        ref_objects = tuple(refs)
        for ref_obj in ref_objects:
            self._require_ref(ref_obj)
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
                "catalog.preview_many(refs, scope=...) received duplicate refs: "
                f"{[ref.key for ref in duplicate_refs]}.",
                cls=SemanticRuntimeError,
                refs=tuple(ref.key for ref in duplicate_refs),
                details={"query_executed": False},
            )

        supported_kinds = {
            SemanticKind.METRIC,
            SemanticKind.ENTITY,
            SemanticKind.DIMENSION,
            SemanticKind.TIME_DIMENSION,
            SemanticKind.MEASURE,
            SemanticKind.RELATIONSHIP,
        }
        resolved: list[tuple[str, SemanticKind]] = []
        for ref_obj in ref_objects:
            kind = ref_obj.kind
            if kind not in supported_kinds:
                _raise(
                    ErrorKind.MATERIALIZE_FAILED,
                    f"catalog.preview_many(refs, scope=...) does not support {kind} refs.",
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
        normalized = normalize_preview_batch_scopes(
            refs=resolved,
            scope=scope,
            source_bindings=source_bindings,
            registry=reg,
        )
        for ref_obj, bindings in zip(ref_objects, normalized, strict=True):
            _require_metric_preview_backend(ref_obj, bindings)
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
                item.bindings.scopes,
                item.bindings.timeout_seconds,
            )
            key = ("row", *identity) if item.kind in row_kinds else ("relationship", item.order)
            groups.setdefault(key, []).append(item)

        by_order: dict[int, PreviewResult] = {}
        for group_key, group_items in groups.items():
            try:
                if group_key[0] == "row":
                    bindings = group_items[0].bindings
                    profile = require_profile_for_backend_type(bindings.backend)
                    timeout = profile.native_query_timeout
                    connection_scope = (
                        connections.terminal_scope(bindings.timeout_seconds)
                        if bindings.backend in {"trino", "postgres", "mysql"}
                        else nullcontext()
                    )
                    with connection_scope:
                        backend = connections.session_backend(bindings.datasource_id)
                        with (
                            cr.backend_errors(backend),
                            (
                                timeout(backend, bindings.timeout_seconds)
                                if timeout is not None
                                else nullcontext()
                            ),
                        ):
                            raw_results = self._preview_row_group(
                                tuple(group_items),
                                connections=connections,
                                limit=preview_limit,
                                include_types=include_types,
                            )
                    results = tuple(
                        _attach_preview_scope(result, bindings=item.bindings)
                        for item, result in zip(group_items, raw_results, strict=True)
                    )
                else:
                    item = group_items[0]
                    results = (
                        self._preview_one(
                            item.ref,
                            connections=connections,
                            bindings=item.bindings,
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
        row_limit = min(limit, bindings.scopes[0][1].max_rows)
        resolver = self._semantic_resolver(
            connections=connections,
            entity_scopes=bindings.entity_scopes,
            source_bindings=bindings.source_bindings,
        )
        parent_table = resolver.entity(_make_ref(entity_id, SemanticKind.ENTITY))
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
        dataframe = connections.collect_source(
            bindings.datasource_id,
            preview_table,
            purpose="semantic.preview_batch",
            max_rows=row_limit + 1,
        )
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
            result = preview_from_pandas(
                frame,
                kind=kind,
                ref=item.ref.path,
                requested_limit=row_limit,
                sample_policy=PreviewSamplePolicy(method="bounded_limit", limit=row_limit),
                types=result_types,
                timezones=timezones,
                report_tz=report_tz,
            )
            if item.kind is SemanticKind.TIME_DIMENSION:
                result = _with_time_parse_risk(
                    result,
                    ref=item.ref.path,
                    field=reg.dimensions[item.ref.path],
                )
            results.append(result)
        return tuple(results)


def load(
    *,
    workspace_dir: str | Path | None = None,
    domains: str | Sequence[str] | None = None,
) -> SemanticCatalog:
    """Load a semantic project and return a browseable SemanticCatalog.

    This is the single public entry point for loading the semantic layer.  The
    lower layers (``SemanticProject.load()`` and
    ``marivo.semantic.loader.load_project()``) return a raw ``LoadResult``
    rather than a catalog; they are for inspecting load status/errors or for
    re-using an already-loaded project, not for agent-facing browsing.

    Args:
        workspace_dir: Optional exact project root. When omitted, resolves
            from ``MARIVO_PROJECT_ROOT``, the nearest ancestor manifest, or
            the current directory. The local ``models/`` root is always
            loaded; external roots can be added with
            ``marivo.toml [semantic].layer_paths``.
        domains: None loads all domain directories. A name or sequence selects
            only those directories, without automatic dependency expansion.
            Include every domain required by the selected declarations.
            Filtered Relationship references may warn; unresolved required
            Metric dependencies fail loading.

    Returns:
        SemanticCatalog on success.

    Example:
        >>> import marivo.semantic as ms
        >>> catalog = ms.load()
        >>> catalog.search(kind="domain").show()
        >>> catalog = ms.load(domains=["sales"])
        >>> catalog.search(kind="domain").show()

    Constraints:
        Raises a typed load error on failure. Does not return a partial catalog.
        Does not query a datasource or print to stdout. Source validation is
        separate from this project-level static validation event.
        Configured layer paths must point at authored ``models/`` roots that
        contain both ``datasources/`` and ``semantic/``.
        Restart Python after package upgrades or model edits/deletions;
        ordinary imported dependencies are not guaranteed to hot-reload.
        Execution failures expose their original exception type and traceback.
    """
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject(workspace_dir=workspace_dir)
    result = project.load(domains=domains)
    if result.status != "ready":
        from marivo.semantic.errors import SemanticLoadFailed

        raise SemanticLoadFailed(result.errors)
    return project.catalog()
