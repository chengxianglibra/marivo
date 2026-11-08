"""SemanticCatalog — unified agent-facing read surface for marivo.semantic.

Public entrypoint: ms.load() -> SemanticCatalog
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager, nullcontext
from dataclasses import dataclass, replace
from datetime import date
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

import ibis.expr.datatypes as dt
from ibis.common.deferred import Deferred, Resolver

from marivo._definition.deferred import RelationshipParameter, _RelationshipValue, root_declaration
from marivo._definition.types import InputContext
from marivo._symbolic import (
    DatasourceSymbol,
    DomainSymbol,
    LookupSymbol,
    _DatasourceIdentity,
    _DatasourceInput,
    native_symbol_ref,
    symbol_factory,
    symbol_from_ref,
)
from marivo.datasource import credentials as cr
from marivo.datasource._builtin import DEFAULT_DATASOURCE_DESCRIPTION, DEFAULT_DATASOURCE_NAME
from marivo.datasource.credential_inputs import credential_labels
from marivo.datasource.engines import require_profile_for_backend_type
from marivo.datasource.errors import DatasourceError
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
    PreviewExecutionLimits,
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
from marivo.semantic._expression_values import (
    _OMITTED_TIME,
    MetricExpression,
    NumericMetric,
    RowExpression,
    TimeExpression,
    _OmittedTime,
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
    RelationshipDefinition,
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
    SemanticExpressionError,
    SemanticLoadFailed,
    SemanticRuntimeError,
    _raise,
    repair,
)
from marivo.semantic.expressions import (
    CatalogValues,
    ExpressionContract,
    ResolvedValue,
    TimeField,
    _Entity,
    _Relationship,
    author_value,
)
from marivo.semantic.expressions import (
    Dimension as _Dimension,
)
from marivo.semantic.expressions import (
    Measure as _Measure,
)
from marivo.semantic.ir import (
    DimensionIR,
    DomainIR,
    EntityVersioningIR,
    SnapshotVersioningIR,
    SourceLocation,
    ValidityVersioningIR,
)
from marivo.semantic.preview_scope import (
    NormalizedPreviewScope,
    PreviewScope,
    PreviewSourceBindings,
    normalize_preview_batch_scopes,
    normalize_preview_limit,
    normalize_preview_scope,
    preflight_preview_scope,
)

CalendarDate = date

if TYPE_CHECKING:
    from ibis.backends import BaseBackend

    from marivo._authoring.model import AuthoringRepair
    from marivo.semantic._compiled_state import CompiledSemanticState
    from marivo.semantic.reader import SemanticProject
    from marivo.semantic.readiness import ReadinessReport
    from marivo.semantic.resolver import SemanticResolver
    from marivo.semantic.validation import SourceCheck, ValidationReport
    from marivo.semantic.validator import Registry

__all__ = [
    "AiContextView",
    "CatalogFilters",
    "CatalogPage",
    "CatalogScope",
    "Datasource",
    "DatasourceDefinition",
    "Dimension",
    "Domain",
    "DomainDefinition",
    "Entity",
    "EntityDefinition",
    "EntityVersioning",
    "EntryProvenance",
    "FieldDefinition",
    "Measure",
    "Metric",
    "MetricDefinition",
    "MetricTimeBinding",
    "Relationship",
    "RelationshipDefinition",
    "SemanticCatalog",
    "SemanticKind",
    "SnapshotVersioning",
    "TimeDimension",
    "ValidityVersioning",
    "_SemanticObject",
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
            "output rows per Entity, also bounded by scope.max_rows; unordered input "
            "is not a representative sample or an estimate of the full Metric"
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
        SemanticKind.DOMAIN: ref_factory.domain,
        SemanticKind.DATASOURCE: ref_factory.datasource,
        SemanticKind.ENTITY: ref_factory.entity,
        SemanticKind.DIMENSION: ref_factory.dimension,
        SemanticKind.TIME_DIMENSION: ref_factory.time_dimension,
        SemanticKind.MEASURE: ref_factory.measure,
        SemanticKind.METRIC: ref_factory.metric,
        SemanticKind.RELATIONSHIP: ref_factory.relationship,
    }[kind]
    return factory(path)


CatalogObjectT = TypeVar("CatalogObjectT", bound="_SemanticObject[SemanticKindTag]")
CatalogEntryKindT = TypeVar("CatalogEntryKindT", bound=SemanticKindTag, covariant=True)
KindT = TypeVar("KindT", bound=SemanticKindTag)


@dataclass(frozen=True)
class _BatchPreviewItem:
    order: int
    ref: Ref[SemanticKindTag]
    kind: SemanticKind
    bindings: NormalizedPreviewScope


@contextmanager
def _preview_reader(
    bindings: NormalizedPreviewScope, connections: DatasourceConnectionService
) -> Iterator[BaseBackend]:
    """Apply native query limits before metadata or business-data resolution."""
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
            timeout(backend, bindings.timeout_seconds) if timeout is not None else nullcontext(),
        ):
            yield backend


def _preview_execution_limits(bindings: NormalizedPreviewScope) -> PreviewExecutionLimits:
    return PreviewExecutionLimits(
        backend=bindings.backend,
        timeout_seconds=bindings.timeout_seconds,
        timeout_enforced=(
            require_profile_for_backend_type(bindings.backend).native_query_timeout is not None
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
    wall_clock_timezone: str | None
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
                FieldSection(
                    label="wall_clock_timezone", value=self.wall_clock_timezone or "(none)"
                ),
                FieldSection(label="fields", value=_format_mapping(self.fields)),
                FieldSection(
                    label="credentials",
                    value=" | ".join(credential_labels(self.fields, self.env_refs)),
                ),
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


_CatalogObjectDetails = DatasourceDetails | DomainDetails


@dataclass(frozen=True, repr=False, eq=False)
class _SemanticObject(RenderableResult, Generic[CatalogEntryKindT]):
    """One immutable browsable object in one compiled semantic catalog."""

    _ref: Ref[CatalogEntryKindT]
    _details: _CatalogObjectDetails | None
    _catalog: SemanticCatalog

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
            from marivo.semantic._metric_time_disclosure import add_metric_time

            card.field("formula", definition.formula)
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
                from marivo.semantic._source_timezone import (
                    effective_source_timezone,
                    source_timezone_axis,
                )

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


CatalogItem: TypeAlias = _SemanticObject[SemanticKindTag] | ResolvedValue
_SemanticInput: TypeAlias = "SemanticObject"


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
class Datasource(_SemanticObject[DatasourceKind], _DatasourceInput):
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
        from marivo._definition.deferred import NamedResolver
        from marivo.semantic._expression_values import _merge, _parameter, _source_arguments

        value = self._catalog._index.values.get(self._ref)
        assert isinstance(value, _Entity)
        # Keep the Entity boundary inside count, so receiver scope owns identity facts.
        from ibis.common.deferred import Attr, Call

        from marivo._definition.deferred import SemanticInput, SemanticResolver
        from marivo._definition.types import SumNone

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
        from marivo._definition.deferred import NamedResolver, resolver_of

        value = author_value(self._catalog._index.values.get(self._ref))
        assert isinstance(value, Deferred)
        return NamedResolver(
            self._catalog._index.values._resolver.exact(self._ref), resolver_of(value)
        )


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
        from marivo._definition.deferred import NamedResolver, resolver_of

        value = author_value(self._catalog._index.values.get(self._ref))
        assert isinstance(value, Deferred)
        return NamedResolver(
            self._catalog._index.values._resolver.exact(self._ref), resolver_of(value)
        )


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
        from marivo._definition.deferred import NamedResolver, resolver_of

        value = author_value(self._catalog._index.values.get(self._ref))
        assert isinstance(value, Deferred)
        return NamedResolver(
            self._catalog._index.values._resolver.exact(self._ref), resolver_of(value)
        )


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
        from marivo._definition.deferred import NamedResolver, resolver_of

        value = author_value(self._catalog._index.values.get(self._ref))
        assert isinstance(value, Deferred)
        return NamedResolver(
            self._catalog._index.values._resolver.exact(self._ref), resolver_of(value)
        )


@dataclass(frozen=True, repr=False, eq=False)
class Relationship(_SemanticObject[RelationshipKind], _RelationshipValue):
    """A named directed Relationship with captured metadata and endpoint navigation."""

    @property
    def kind(self) -> Literal["relationship"]:
        return "relationship"

    @property
    def definition(self) -> RelationshipDefinition:
        return cast("RelationshipDefinition", super().definition)

    @property
    def _parameter(self) -> RelationshipParameter:
        value = self._catalog._index.values.get(self._ref)
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


SemanticObject: TypeAlias = (
    Domain | Datasource | Entity | Dimension | TimeDimension | Measure | Metric | Relationship
)


def _encode_related_cursor(snapshot_digest: str, level: str, offset: int) -> str:
    payload = json.dumps([snapshot_digest, level, offset], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_related_cursor(cursor: str | None, *, snapshot_digest: str, level: str) -> int:
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
        raise ValueError("related cursor does not match the current Catalog snapshot and relation")
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
    return f"a named semantic object with kind in {{{kinds}}}"


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
    if isinstance(value, _Entity):
        return value._exact_ref.ref
    if isinstance(value, _Relationship):
        return value._parameter.ref.ref
    if isinstance(value, (_Dimension, TimeField, _Measure)):
        return value.ref.ref
    return None


def _semantic_input_ref(value: object) -> Ref[SemanticKindTag] | None:
    symbolic = native_symbol_ref(value)
    if symbolic is not None:
        return symbolic
    if isinstance(value, _SemanticObject):
        return value._ref
    return _value_lookup_ref(value)


def _semantic_input_received(value: object) -> str:
    exact_ref = _semantic_input_ref(value)
    if exact_ref is not None:
        if type(value) is Ref:
            return f"Ref[{exact_ref.kind.value}]({exact_ref.key})"
        return f"{type(value).__name__}({exact_ref.key})"
    if isinstance(value, _SemanticObject):
        return f"{type(value).__name__}(invalid identity)"
    return type(value).__name__


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
    operation = location.split(".", 1)[1].split("(", 1)[0]
    _raise(
        kind,
        f"{location}: {message}",
        cls=SemanticRuntimeError,
        refs=(received_ref.key,) if received_ref is not None else (),
        expected=_semantic_input_expected(allowed_kinds),
        received=_semantic_input_received(value),
        constraint_id=ConstraintId.REF_SHAPE,
        hint=f'Use a named object from catalog.get(id) or search(); run marivo.help("semantic.{operation}") for the accepted kinds.',
        repair_value=repair_value
        or repair(
            kind="inspect",
            canonical_id=operation,
            action="Choose an allowed named object directly from the receiving project's Catalog.",
            snippet=f"subject = catalog.get({received_ref.key!r})"
            if received_ref is not None
            and received_ref in catalog._require_index()._refs
            and received_ref.kind in allowed_kinds
            else None,
            candidates=tuple(candidate.key for candidate in candidates),
        ),
        details={
            "operation": location,
            "parameter": location,
            "query_executed": False,
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
    declaration_closure: bool = False,
) -> Ref[SemanticKindTag]:
    """Resolve a named object's identity in the explicit receiving project scope."""
    index = catalog._require_index()
    if not isinstance(value, _SemanticObject):
        _raise_invalid_semantic_input(
            catalog=catalog,
            value=value,
            allowed_kinds=allowed_kinds,
            location=location,
            message="expected a named semantic object obtained from this project's Catalog.get(id) or search().",
        )
    exact_ref = value._ref
    if type(value) is not _ENTRY_TYPE_BY_KIND.get(exact_ref.kind):
        _raise_invalid_semantic_input(
            catalog=catalog,
            value=value,
            allowed_kinds=allowed_kinds,
            location=location,
            message="expected a concrete named semantic object produced by a Catalog.",
        )
    if value._catalog.workspace_dir.resolve() != catalog.workspace_dir.resolve():
        _raise_invalid_semantic_input(
            catalog=catalog,
            value=value,
            allowed_kinds=allowed_kinds,
            location=location,
            message=(
                f"input belongs to project {value._catalog.workspace_dir}; "
                f"the receiving Catalog belongs to {catalog.workspace_dir}."
            ),
        )
    if exact_ref.kind not in allowed_kinds:
        _raise_invalid_semantic_input(
            catalog=catalog,
            value=value,
            allowed_kinds=allowed_kinds,
            location=location,
            message=f"received semantic kind {exact_ref.kind.value}; select an allowed named object.",
        )
    if exact_ref not in index._refs:
        _raise_invalid_semantic_input(
            catalog=catalog,
            value=value,
            allowed_kinds=allowed_kinds,
            location=location,
            kind=ErrorKind.NOT_FOUND,
            message="the named identity is outside the receiving Catalog scope.",
            candidate_kinds=frozenset({exact_ref.kind}),
        )
    registry = catalog._state.registry
    if not declaration_closure and (
        (exact_ref.kind is SemanticKind.METRIC and not metric_preview)
        or (exact_ref.kind is SemanticKind.MEASURE and exact_ref.path in registry.declared_measures)
    ):
        physical = _physical_dependencies(catalog, exact_ref)
        shown = physical[:_SEMANTIC_INPUT_CANDIDATE_LIMIT]
        physical_snippet: str | None = None
        if location == "catalog.readiness(refs=...)":
            physical_snippet = (
                "catalog.readiness(refs=["
                + ", ".join(f"catalog.get({ref.key!r})" for ref in shown)
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


def _require_readiness_input(
    catalog: SemanticCatalog,
    value: _SemanticInput,
) -> Ref[SemanticKindTag]:
    return _normalize_semantic_input(
        catalog,
        value,
        allowed_kinds=_ALL_SEMANTIC_KINDS,
        location="catalog.readiness(refs)",
        declaration_closure=True,
    )


def _freeze_semantic_inputs(
    values: Sequence[_SemanticInput],
    *,
    operation: str,
) -> tuple[_SemanticInput, ...]:
    if not isinstance(values, Sequence) or isinstance(values, str | bytes) or not values:
        _raise(
            ErrorKind.INVALID_REF,
            f"catalog.{operation}(refs) requires a non-empty sequence of named semantic objects.",
            cls=SemanticRuntimeError,
            expected="a non-empty sequence of named semantic objects",
            received=(
                f"an empty {type(values).__name__}"
                if isinstance(values, Sequence) and not values
                else type(values).__name__
            ),
            repair_value=repair(
                kind="inspect",
                canonical_id=operation,
                action=f"Select at least one object and call catalog.{operation}([object]).",
            ),
            details={"parameter": "refs", "query_executed": False},
        )
    return tuple(values)


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
        self.domain_children = MappingProxyType(
            {key: tuple(values) for key, values in domain_children.items()}
        )
        self.datasource_entities = MappingProxyType(
            {key: tuple(values) for key, values in datasource_entities.items()}
        )


def _build_datasource_object(
    ds_ir: DatasourceIR, build_index: _CatalogBuildIndex, catalog: SemanticCatalog
) -> Datasource:
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
        wall_clock_timezone=ds_ir.wall_clock_timezone,
        fields=dict(ds_ir.fields),
        env_refs=dict(ds_ir.env_refs),
    )
    return _object_from_details(Datasource, details, catalog)


def _build_domain_object(
    model_ir: DomainIR, build_index: _CatalogBuildIndex, catalog: SemanticCatalog
) -> Domain:
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
    return _object_from_details(Domain, details, catalog)


def _preview_timezones_for_field(
    *,
    column_name: str,
    field_ir: DimensionIR,
    registry: Registry,
    physical_type: dt.DataType,
    engine: str,
) -> dict[str, dict[str, str | None]]:
    from marivo.semantic._source_timezone import effective_source_timezone, source_timezone_axis

    if not field_ir.is_time_dimension:
        return {}
    if isinstance(physical_type, dt.Timestamp) and (
        physical_type.timezone is not None or engine == "clickhouse"
    ):
        return {column_name: {"kind": "instant"}}
    source = effective_source_timezone(
        registry, source_timezone_axis(registry, field_ir.semantic_id)
    )
    from marivo.semantic.ir import StrptimeParse, is_time_bearing_format

    civil_date = physical_type.is_date() or (
        isinstance(field_ir.parse, StrptimeParse)
        and not is_time_bearing_format(field_ir.parse.format)
    )
    return {
        column_name: {
            "kind": "civil_date" if civil_date else "wall_clock",
            "timezone": source.timezone if source is not None else None,
            "origin": source.origin if source is not None else None,
            "inherited_from": source.inherited_from if source is not None else None,
        }
    }


_PreviewCell: TypeAlias = str | int | float | bool | None


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
        entries: tuple[_SemanticObject[SemanticKindTag], ...] = (
            *(
                _build_domain_object(item, build_index, catalog)
                for item in registry.domains.values()
            ),
            *(_build_datasource_object(item, build_index, catalog) for item in datasource_irs),
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
        self._public_entries: Mapping[Ref[SemanticKindTag], SemanticObject] = MappingProxyType(
            {
                ref: cast("SemanticObject", self._entries[ref])
                if ref in self._entries
                else cast(
                    "SemanticObject",
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

    def entry(self, ref: Ref[SemanticKindTag]) -> _SemanticObject[SemanticKindTag] | None:
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
        ...     ratio = revenue / 2
    Constraints: Use ms.load() to construct a fresh snapshot. Search/get/show never
        read business datasources. Named objects compose and enter consumers directly.
    """

    __slots__ = (
        "_definitions",
        "_documents",
        "_index",
        "_project",
        "_reg",
        "_scope",
        "_search_config",
        "_state",
    )

    _project: SemanticProject
    _state: CompiledSemanticState
    _reg: Registry
    _index: _CatalogIndex
    _search_config: SearchConfig
    _documents: tuple[SearchDocument, ...]
    _definitions: dict[Ref[SemanticKindTag], EntryDefinition]
    _scope: CatalogScope

    def __init__(self, project: SemanticProject) -> None:
        if project._compiled_state is None:
            raise SemanticLoadFailed(project.errors())
        object.__setattr__(self, "_project", project)
        object.__setattr__(self, "_state", project._compiled_state)
        object.__setattr__(self, "_reg", self._state.registry)
        object.__setattr__(self, "_definitions", {})
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
    def _ref_from_id(id: object) -> Ref[SemanticKindTag]:
        if isinstance(id, _SemanticObject):
            _raise(
                ErrorKind.INVALID_REF,
                "catalog.get(id) requires an identity or lookup symbol; the supplied object is already directly usable.",
                cls=SemanticRuntimeError,
                refs=(id.id,),
                expected="canonical kind:path string or native lookup symbol",
                received=f"{type(id).__name__}({id.id})",
                hint="Use the object directly; pass object.id only to acquire the receiving Catalog's definition.",
                repair_value=repair(
                    kind="inspect",
                    canonical_id="semantic_catalog.get",
                    action="Use the semantic object directly, or pass object.id to get the receiving Catalog's definition.",
                    snippet="subject = catalog.get(object.id)",
                    candidates=(id.id,),
                ),
            )
        symbolic = native_symbol_ref(id)
        if symbolic is not None:
            return symbolic
        if not isinstance(id, str) or id.count(":") != 1:
            _raise(
                ErrorKind.INVALID_REF,
                "catalog.get(id) requires a canonical kind:path identity or native lookup symbol.",
                cls=SemanticRuntimeError,
                expected="canonical kind:path string or native lookup symbol",
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

    def get(self, id: str | LookupSymbol, /) -> SemanticObject:
        """Get one directly usable named object by its exact canonical identity.

        Args: id: Canonical kind:path string or an uncomposed native ms.ref.* symbol.
        Returns: A closed kind-discriminated immutable semantic object; narrow kind before composing.
        Example: ``entry = catalog.get(ms.ref.metric("sales.revenue"))``.
        Constraints: Lookup symbols capture declaration references; get resolves one
        symbol against this Catalog, returning a closed object usable in analysis.
        Composite Deferred expressions are not identities. Exact lookup never uses
        fuzzy replacement or calls a model/source.
        """
        ref = self._ref_from_id(id)
        identity = ref.key
        entry = self._index.entry(ref)
        if entry is not None:
            return cast("SemanticObject", entry)
        known_outside = any(ref in self._entry_dependencies(item) for item in self._index._refs)
        if known_outside:
            _raise(
                ErrorKind.NOT_FOUND,
                f"Canonical identity {identity!r} is referenced but outside this Catalog's loaded scope.",
                cls=SemanticRuntimeError,
                refs=(identity,),
                expected="a required identity included in the explicit load scope",
                received=identity,
                hint="Create a new Catalog with ms.load(domains=...) including the required domain and its dependencies.",
                repair_value=repair(
                    kind="reload",
                    canonical_id="load",
                    action="Explicitly reload with the referenced domain and its required dependency domains; the current Catalog is unchanged.",
                    candidates=(identity,),
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
            f"Canonical identity {identity!r} was not found in this loaded Catalog.",
            cls=SemanticRuntimeError,
            refs=(identity,),
            expected="an exact loaded kind:path identity",
            received=identity,
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

    def _definition(self, ref: Ref[SemanticKindTag]) -> EntryDefinition:
        from marivo.semantic._catalog_definition import definition_for

        # Definition facts belong to this immutable scope and reuse their projection.
        definition = self._definitions.get(ref)
        if definition is None:
            definition = self._definitions[ref] = definition_for(self, ref)
        return definition

    def _search_documents(self) -> tuple[SearchDocument, ...]:
        from marivo.semantic._catalog_definition import context_for
        from marivo.semantic._metric_time_disclosure import metric_time_binding_text

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
                    ("time_binding", metric_time_binding_text(item))
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
    ) -> CatalogPage[Domain]: ...

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
    ) -> CatalogPage[Datasource]: ...

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
    ) -> CatalogPage[Entity]: ...

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
    ) -> CatalogPage[Dimension]: ...

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
    ) -> CatalogPage[TimeDimension]: ...

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
    ) -> CatalogPage[Measure]: ...

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
    ) -> CatalogPage[Metric]: ...

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
    ) -> CatalogPage[Relationship]: ...

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
    ) -> CatalogPage[SemanticObject]: ...

    def search(
        self,
        query: str = "",
        *,
        kind: str | None = None,
        domain: str | None = None,
        entity: str | None = None,
        limit: int = 10,
        cursor: str | None = None,
    ) -> CatalogPage[SemanticObject]:
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
        entry: _SemanticObject[SemanticKindTag],
        relation: RelatedRelation,
        *,
        limit: int,
        cursor: str | None,
    ) -> CatalogPage[SemanticObject]:
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
            offset = _decode_related_cursor(
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
            _encode_related_cursor(self.definition_fingerprint, token, next_offset)
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

    def _describe(self, ref: Ref[SemanticKindTag], /) -> ExpressionContract:
        """Capture immutable metadata for an exact internal scope identity."""
        from marivo._definition.graph import computation_fingerprint
        from marivo.semantic._deferred_semantics import (
            _rescale_disclosure,
            consume_metric,
            consume_row,
        )
        from marivo.semantic.expressions import _Resolver

        target = ref
        self._require_ref(target)
        value = self._require_index().require(target)
        if isinstance(value, (_Entity, _Dimension, TimeField, _Measure, _Relationship)):
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
                roots, unit, permission = (
                    (row.root,),
                    row.unit,
                    row.permission,
                )
                node = row.node
            else:
                original = self._reg.declared_metrics[target.path]
                metric = consume_metric(original)
                roots, unit, permission = metric.roots, metric.unit, None
                node = metric.node
            context = declaration.ai_context
            location = declaration.location
            return ExpressionContract(
                stage="declared_measure" if declaration.kind == "measure" else "published_metric",
                _ref=_Resolver(self._state).exact(target),
                _roots=roots,
                unit=unit,
                sum_permission=permission,
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
        """Check selected definitions' physical dependency closures offline.

        Args:
            refs: Named objects from the same project,
                including declared Measures and Metrics.
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
            report = self._project._readiness(refs=None)
            return replace(
                report,
                _resolved_inputs=tuple(self.get(ref.key) for ref in report._analysis_ready_inputs),
            )
        values = _freeze_semantic_inputs(refs, operation="readiness")
        inputs = tuple(_require_readiness_input(self, value) for value in values)
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
            _resolved_inputs=tuple(
                self.get(ref.key) for ref in report._analysis_ready_inputs if ref in inputs
            ),
            requested_ids=tuple(item.key for item in inputs),
            checked_ids=tuple(sorted(item.key for item in checked)),
        )

    def validate(
        self,
        refs: Sequence[_SemanticInput],
        /,
        *,
        checks: Sequence[SourceCheck] = (),
        scope: PreviewScope | None = None,
    ) -> ValidationReport:
        """Validate semantic contracts against current physical source types.

        Connect to the selected dependency closure, establish Entity output
        schemas, and check field bindings, Measure types and temporal parser
        compatibility. Preview and analysis reuse the same field contracts. Data
        expectations run only when supplied through ``ms.source_check`` and
        require an explicit bounded ``scope``. The result is independent of
        semantic readiness and is not persisted.

        Args:
            refs: Non-empty unique named semantic objects whose
                physical Entity sources should be checked.
            checks: Optional explicit expectations built by ``ms.source_check``.
                No null, enum, uniqueness, freshness, relationship, or
                cardinality expectation is inferred.
            scope: Required for data-reading checks and forbidden otherwise.
                Use one ``AuthoringScope`` for one checked Entity or an exact
                Entity symbol/scope pair sequence for multiple checked Entities.

        Returns:
            ValidationReport with per-check status, observed identities,
            affected refs, repair direction, and query/scope disclosure.

        Example:
            ``catalog.validate([catalog.get("metric:sales.revenue")]).show()``.

        Constraints:
            This online check does not change readiness or certify arbitrary
            analysis methods. Unavailable physical types cannot pass validation.
            Schema checks retain their column names and raw types in
            ``observed["unavailable_columns"]`` and skipped fields in
            ``observed["unavailable_field_refs"]``. Direct Entities still check
            independent fields; expression Entities require complete input types.
        """
        self._require_ready()
        values = _freeze_semantic_inputs(refs, operation="validate")
        exact_refs = tuple(
            _normalize_semantic_input(
                self,
                value,
                allowed_kinds=_ALL_SEMANTIC_KINDS - {SemanticKind.DOMAIN, SemanticKind.DATASOURCE},
                location=f"catalog.validate(refs[{index}])",
                declaration_closure=True,
            )
            for index, value in enumerate(values)
        )
        duplicates = tuple(
            dict.fromkeys(
                value for index, value in enumerate(exact_refs) if value in exact_refs[:index]
            )
        )
        if duplicates:
            _raise(
                ErrorKind.INVALID_REF,
                "catalog.validate(refs=...) requires unique exact refs.",
                cls=SemanticRuntimeError,
                refs=tuple(ref.key for ref in duplicates),
                details={"query_executed": False},
            )
        from marivo.semantic.validation import run_validation

        return run_validation(
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
        """Return one bounded runtime preview for a named semantic object.

        Args:
            ref: A named object from the same project.
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
            ``scope.max_rows`` acquisition guard. Ordinary Metric previews support DuckDB,
            PostgreSQL, MySQL, Trino and ClickHouse and reuse datasource physical
            partition preflight. Each Entity contributes at most
            min(scope.max_rows, 10,000) output rows. Coverage reports actual
            Entity input counts; reaching a cap leaves exhaustion unknown.
            execution_limits reports requested native limits, not a total
            deadline. No scan row/byte cap is requested; scanning is unknown.
            Unordered samples are not full-source estimates.
        """
        limit = normalize_preview_limit(limit, operation="preview")
        target = _normalize_semantic_input(
            self,
            ref,
            allowed_kinds=_ALL_SEMANTIC_KINDS
            - {SemanticKind.DOMAIN, SemanticKind.DATASOURCE, SemanticKind.RELATIONSHIP},
            location="catalog.preview(ref)",
            metric_preview=True,
        )
        bindings = normalize_preview_scope(
            ref=target.path,
            kind=target.kind,
            scope=scope,
            source_bindings=source_bindings,
            registry=self._require_ready(),
            catalog=self,
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
        """Return a bounded batch preview for ordered named semantic objects.

        Args:
            refs: A non-empty ordered sequence of named semantic objects.
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
            Metric previews support all five SQL backends and aggregate at most
            min(scope.max_rows, 10,000) output rows per Entity. All physical
            scopes pass datasource partition preflight before any business read.
            Coverage and execution_limits disclose sampled inputs and native
            limits; output limits do not bound physical scanning.
        """
        limit = normalize_preview_limit(limit, operation="preview_many")
        values = _freeze_semantic_inputs(refs, operation="preview_many")
        normalized_refs = tuple(
            _normalize_semantic_input(
                self,
                value,
                allowed_kinds=_ALL_SEMANTIC_KINDS
                - {SemanticKind.DOMAIN, SemanticKind.DATASOURCE, SemanticKind.RELATIONSHIP},
                location=f"catalog.preview_many(refs)[{index}]",
                metric_preview=True,
            )
            for index, value in enumerate(values)
        )
        reg = self._require_ready()
        if not normalized_refs:
            _raise(
                ErrorKind.INVALID_REF,
                "catalog.preview_many(refs, scope=...) requires a non-empty sequence.",
                cls=SemanticRuntimeError,
                details={"query_executed": False},
            )
        ref_objects = tuple(normalized_refs)
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
            catalog=self,
        )
        with self._project._connection_operation() as connections, ExitStack() as stack:
            return self._preview_batch(
                normalized_refs,
                normalized=normalized,
                connections=connections,
                stack=stack,
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
        preflighted: bool = False,
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
                expression, checks, samples = lower_preview_metric(metric, resolver)
                frame = connections.collect_source(
                    bindings.datasource_id, expression, purpose="semantic.preview", max_rows=1
                )
                for name, check in checks:
                    if frame[name].fillna(False).any():
                        numeric_failure(check.expected, f"{ref_str}: {check.received}")
                result = preview_from_pandas(
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
                entity_rows = tuple(
                    (entity_id, int(frame[name].iloc[0])) for entity_id, name in samples
                )
                entity_limits = tuple(
                    (entity_id, min(scope.max_rows, METRIC_PREVIEW_SAMPLE_SIZE))
                    for entity_id, scope in bindings.scopes
                )
                limits = dict(entity_limits)
                return replace(
                    result,
                    coverage=replace(
                        result.coverage,
                        entity_rows=entity_rows,
                        entity_row_limits=entity_limits,
                        scope_exhaustion=(
                            "exhaustive"
                            if all(count < limits[entity_id] for entity_id, count in entity_rows)
                            else "unknown"
                        ),
                    ),
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
                    report_tz=None,
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
                    report_tz=None,
                )
            if is_field_preview:
                field_ir = reg.dimensions[ref_str]
                parent_table = resolver.entity(_make_ref(field_ir.entity, SemanticKind.ENTITY))
                field_value = resolver.dimension(cast("Ref[FieldKind]", _make_ref(ref_str, kind)))
                field_column_name = ref_str.rsplit(".", 1)[-1]
                report_tz = None
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
                        registry=reg,
                        physical_type=field_value.type(),
                        engine=bindings.backend,
                    ),
                    report_tz=report_tz,
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
                    report_tz=None,
                )
            _raise(
                ErrorKind.MATERIALIZE_FAILED,
                f"catalog.preview() does not support {kind} refs.",
                cls=SemanticRuntimeError,
                refs=(ref_str,),
                details={"kind": str(kind)},
            )

        with _preview_reader(bindings, connections):
            if not preflighted:
                preflight_preview_scope(
                    bindings, registry=reg, connections=connections, project_root=self.workspace_dir
                )
            result = replace(
                execute_preview(), execution_limits=_preview_execution_limits(bindings)
            )
        return _attach_preview_scope(
            result,
            bindings=bindings,
            approximate_input=kind is SemanticKind.METRIC,
        )

    def _preview_batch(
        self,
        refs: Sequence[Ref[SemanticKindTag]],
        *,
        normalized: tuple[NormalizedPreviewScope, ...],
        connections: DatasourceConnectionService,
        stack: ExitStack,
        source_bindings: PreviewSourceBindings | None,
        limit: int,
        include_types: bool,
    ) -> PreviewBatchResult:
        reg = self._require_ready()
        ref_objects = tuple(refs)
        resolved = tuple((ref.path, ref.kind) for ref in ref_objects)
        preview_limit = validate_preview_limit(limit)
        sidecar = self._project._expression_sidecar
        assert sidecar is not None
        readers: dict[int, DatasourceConnectionService] = {}

        def reader_for(bindings: NormalizedPreviewScope) -> DatasourceConnectionService:
            timeout = bindings.timeout_seconds
            reader = readers.get(timeout)
            if reader is None:
                reader = stack.enter_context(
                    connections.operation(terminal_timeout_seconds=timeout)
                )
                readers[timeout] = reader
            return reader

        checked_scopes: set[tuple[tuple[str, AuthoringScope], ...]] = set()
        for bindings in normalized:
            if bindings.scopes in checked_scopes:
                continue
            reader = reader_for(bindings)
            with _preview_reader(bindings, reader):
                preflight_preview_scope(
                    bindings, registry=reg, connections=reader, project_root=self.workspace_dir
                )
            checked_scopes.add(bindings.scopes)
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
            bindings = group_items[0].bindings
            reader = reader_for(bindings)
            try:
                if group_key[0] == "row":
                    with _preview_reader(bindings, reader):
                        raw_results = self._preview_row_group(
                            tuple(group_items),
                            connections=reader,
                            limit=preview_limit,
                            include_types=include_types,
                        )
                        raw_results = tuple(
                            replace(
                                result,
                                execution_limits=_preview_execution_limits(bindings),
                            )
                            for result in raw_results
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
                            connections=reader,
                            bindings=item.bindings,
                            limit=preview_limit,
                            include_types=include_types,
                            preflighted=True,
                        ),
                    )
            except (SemanticRuntimeError, DatasourceError):
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

        report_tz = None
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
                    timezones = _preview_timezones_for_field(
                        column_name=semantic_name,
                        field_ir=field_ir,
                        registry=reg,
                        physical_type=preview_table.schema()[alias],
                        engine=bindings.backend,
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
