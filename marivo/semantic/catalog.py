"""SemanticCatalog — unified agent-facing read surface for marivo.semantic.

Public entrypoint: ms.load() -> SemanticCatalog"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from contextlib import AbstractContextManager, ExitStack
from dataclasses import replace
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from threading import Lock
from typing import (
    TYPE_CHECKING,
    Literal,
    NoReturn,
    TypeAlias,
    TypeVar,
    cast,
    overload,
)

from ibis.common.deferred import Resolver
from typing_extensions import override

from marivo._kernel.config import AUTHORED_DIR, load_semantic_layer_paths
from marivo._kernel.refs import (
    Ref,
    SemanticKind,
    SemanticKindTag,
)
from marivo._kernel.refs import (
    ref as ref_factory,
)
from marivo._kernel.render import (
    Card,
    RenderableResult,
)
from marivo._kernel.symbolic import (
    LookupSymbol,
    native_symbol_ref,
    symbol_from_ref,
)
from marivo.datasource.spi import (
    PREVIEW_DEFAULT_LIMIT,
    AiContextIR,
    AuthoringScope,
    DatasourceConnectionService,
    PreviewResult,
    QueryParamScalar,
    QueryParamScalarList,
)
from marivo.semantic import _catalog_definition
from marivo.semantic._catalog_navigation import build_search_documents, describe_entry, related_page
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
    MetricContributionTimeRole,
    MetricDefinition,
    RelationshipDefinition,
)
from marivo.semantic.catalog_model.index import (
    _CatalogIndex,
)
from marivo.semantic.catalog_model.inputs import (
    _ALL_SEMANTIC_KINDS,
    _SEMANTIC_INPUT_CANDIDATE_LIMIT,
    _freeze_semantic_inputs,
    _normalize_semantic_input,
    _physical_dependencies,
    _require_readiness_input,
    _SemanticInput,
)
from marivo.semantic.catalog_model.objects import (
    Datasource,
    Dimension,
    Domain,
    Entity,
    Measure,
    Metric,
    RelatedRelation,
    Relationship,
    SemanticObject,
    TimeDimension,
    _SemanticObject,
)
from marivo.semantic.catalog_model.refs import (
    _make_ref,
)
from marivo.semantic.catalog_previews import CatalogPreviewer
from marivo.semantic.catalog_project import CatalogProject as CatalogProject
from marivo.semantic.catalog_project import (
    project_connections,
    project_excluded_refs,
    project_filtered_domains,
    project_readiness,
)
from marivo.semantic.definition_resolution import ResolvedValue
from marivo.semantic.dtos import PreviewBatchResult
from marivo.semantic.errors import (
    ErrorKind,
    SemanticError,
    SemanticLoadFailed,
    SemanticRuntimeError,
    _raise,
    repair,
)
from marivo.semantic.expressions.objects import (
    ExpressionContract,
)
from marivo.semantic.ir import (
    EntityVersioningIR,
    SnapshotVersioningIR,
    ValidityVersioningIR,
)
from marivo.semantic.project_view import (
    project_compiled_state,
    project_datasources,
    project_expression_sidecar,
)
from marivo.semantic.readiness import ready_input_refs
from marivo.semantic.runtime_check.preview_scope import (
    PreviewScope,
    PreviewSourceParameters,
    normalize_preview_batch_scopes,
    normalize_preview_limit,
    normalize_preview_scope,
)
from marivo.semantic.runtime_check.resolver import SemanticResolver
from marivo.semantic.runtime_check.validation import run_validation
from marivo.semantic.search.catalog_search import search_documents
from marivo.semantic.search.models import (
    SearchConfig,
    SearchDocument,
)
from marivo.semantic.search.settings import load_search_config
from marivo.telemetry import traced

if TYPE_CHECKING:
    from _thread import LockType

    from marivo.semantic.compiled_types import CompiledSemanticState
    from marivo.semantic.readiness import ReadinessReport
    from marivo.semantic.runtime_check.results import ValidationReport
    from marivo.semantic.runtime_check.source_checks import SourceCheck
    from marivo.semantic.static_check.registry import Registry


CalendarDate = date

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
    "MetricContributionTimeRole",
    "MetricDefinition",
    "Relationship",
    "RelationshipDefinition",
    "SemanticCatalog",
    "SemanticKind",
    "SnapshotVersioning",
    "TimeDimension",
    "ValidityVersioning",
]

AiContextView = AiContextIR

SnapshotVersioning = SnapshotVersioningIR

ValidityVersioning = ValidityVersioningIR

EntityVersioning = EntityVersioningIR


KindT = TypeVar("KindT", bound=SemanticKindTag)


_PreviewCell: TypeAlias = str | int | float | bool | None


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
        "_documents_lock",
        "_excluded_refs",
        "_index",
        "_load_errors",
        "_project",
        "_reg",
        "_scope",
        "_search_config",
        "_state",
    )

    _project: CatalogProject
    _state: CompiledSemanticState
    _reg: Registry
    _index: _CatalogIndex
    _search_config: SearchConfig
    _documents: tuple[SearchDocument, ...] | None
    _documents_lock: LockType
    _definitions: dict[Ref[SemanticKindTag], EntryDefinition]
    _scope: CatalogScope
    _load_errors: tuple[SemanticError, ...]
    _excluded_refs: tuple[str, ...]

    def __init__(self, project: CatalogProject) -> None:
        state = project_compiled_state(project)
        if state is None:
            raise SemanticLoadFailed(project.errors())
        object.__setattr__(self, "_project", project)
        object.__setattr__(self, "_state", state)
        object.__setattr__(self, "_reg", self._state.registry)
        object.__setattr__(self, "_load_errors", project.errors())
        object.__setattr__(self, "_excluded_refs", project_excluded_refs(project))
        object.__setattr__(self, "_definitions", {})
        object.__setattr__(self, "_documents", None)
        object.__setattr__(self, "_documents_lock", Lock())
        object.__setattr__(
            self, "_index", _CatalogIndex(self, project_datasources(project), self._reg)
        )

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
                complete=not self._load_errors,
                load_error_count=len(self._load_errors),
                excluded_count=len(self._excluded_refs),
            ),
        )

    @override
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

    @property
    def load_errors(self) -> tuple[SemanticError, ...]:
        """Root diagnostics for a partial Catalog, empty for a complete load."""
        return self._load_errors

    @property
    def excluded_refs(self) -> tuple[str, ...]:
        """Known semantic identities excluded due to unavailable sources."""
        return self._excluded_refs

    def _require_ready(self) -> Registry:
        reg = self._reg
        if not self._load_errors and self._project.is_ready():
            return reg
        errors = self._load_errors or self._project.errors()
        if errors:
            raise SemanticLoadFailed(errors)
        _raise(
            ErrorKind.PROJECT_NOT_LOADED,
            "Semantic catalog is not loaded. Construct a fresh catalog with ms.load().",
            cls=SemanticRuntimeError,
        )

    def _resolved_value(self, ref: Ref[SemanticKindTag]) -> ResolvedValue:
        return self._index.values.get(ref)

    def _named_recipe(self, ref: Ref[SemanticKindTag]) -> Resolver:
        return self._index.values.named_recipe(ref)

    def _require_index(self) -> _CatalogIndex:
        return self._index

    @override
    def _repr_identity(self) -> str:
        fingerprint = self.definition_fingerprint[:12]
        partial = " partial" if self._load_errors else ""
        return f"SemanticCatalog{partial} fingerprint={fingerprint} objects={len(self._index.refs)}"

    @override
    def _card(self) -> Card:
        counts = tuple(
            (kind.value, str(sum(ref.kind is kind for ref in self._index.refs)))
            for kind in SemanticKind
        )
        card = (
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
        )
        if self._load_errors:
            card.field(
                "partial_catalog",
                f"{len(self._load_errors)} load errors; {len(self._excluded_refs)} "
                "semantic objects excluded. Inspect .load_errors and .excluded_refs.",
            )
        return card.table(columns=("kind", "count"), rows=counts, row_count=len(counts))

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

    @traced(surface="semantic", operation="semantic_catalog.get", kind="method")
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
        if self._load_errors:
            _raise(
                ErrorKind.NOT_FOUND,
                f"Canonical identity {identity!r} is unavailable in this partial Catalog.",
                cls=SemanticRuntimeError,
                refs=(identity,),
                repair_value=repair(
                    kind="reload",
                    canonical_id="load",
                    action="Inspect catalog.load_errors, fix the failed declarations, and reload.",
                ),
            )
        known_outside = any(ref in self._entry_dependencies(item) for item in self._index.refs)
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
        same_path = tuple(item.key for item in self._index.refs if item.path == ref.path)
        candidates = tuple(
            item.key
            for item in sorted(
                (item for item in self._index.refs if item.kind is ref.kind),
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

        # Definition facts belong to this immutable scope and reuse their projection.
        definition = self._definitions.get(ref)
        if definition is None:
            definition = self._definitions.setdefault(
                ref, _catalog_definition.definition_for(self, ref)
            )
        return definition

    def _search_documents(self) -> tuple[SearchDocument, ...]:
        with self._documents_lock:
            documents = self._documents
            if documents is None:
                documents = self._build_search_documents()
                object.__setattr__(self, "_documents", documents)
            return documents

    def _build_search_documents(self) -> tuple[SearchDocument, ...]:

        return build_search_documents(self, self._index)

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

    @traced(surface="semantic", operation="semantic_catalog.search", kind="method")
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
            query: Business intent, alias, name or identity. Omit or pass an empty
                string to list all objects matching the filters in the loaded scope.
            kind: Exact semantic kind.
            domain: Owning domain filter.
            entity: Full owning Entity path.
            limit: Positive integer page size; defaults to 10.
            cursor: Previous page continuation. Keep the same query and filters.

        Returns:
            Complete typed page with scope, filters, query matches and retrieval facts.

        Example:
            >>> print(catalog.search())  # First page of all loaded kinds
            >>> print(catalog.search(kind="entity", domain="sales"))  # Datasource bindings
            >>> page = catalog.search(kind="metric", limit=100)
            >>> while True:
            ...     for metric in page.items:
            ...         print(metric.id, metric.name)
            ...     if page.next_cursor is None:
            ...         break
            ...     page = catalog.search(kind="metric", limit=100, cursor=page.next_cursor)
            >>> print(catalog.search("revenue", kind="metric", limit=5))

        Constraints:
            Empty query lists every filtered object in canonical identity order
            across pages without model calls. candidate_count is the filtered total
            for browsing, but only the candidate pool size for text search.
            print(page) and show() display a bounded card with Entity datasource
            bindings. items contains the full current page even when display truncates it;
            follow next_cursor until None to enumerate all pages.
            Text search uses frozen configuration.
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
            self._search_documents(),
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
        return related_page(self, entry, relation, self._scope, limit=limit, cursor=cursor)

    def _require_ref(self, ref: Ref[SemanticKindTag]) -> None:
        """Validate exact internal identity in this compiled scope."""
        exact_ref = ref
        index = self._require_index()
        if exact_ref in index:
            return

        candidates = tuple(item.key for item in index.refs if item.kind is exact_ref.kind)[
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
                "filtered_domains": project_filtered_domains(self._project),
                "candidates": candidates,
            },
        )

    def _describe(self, ref: Ref[SemanticKindTag], /) -> ExpressionContract:
        """Capture immutable metadata for an exact internal scope identity."""

        self._require_ref(ref)
        return describe_entry(ref, self._require_index().require(ref), self._state)

    @traced(surface="semantic", operation="readiness", kind="method")
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
            report = project_readiness(self._project, refs=None)
            return replace(
                report,
                _resolved_inputs=tuple(self.get(ref.key) for ref in ready_input_refs(report)),
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
            if dependency not in self._index
        }
        report = project_readiness(
            self._project, refs=(*physical, *sorted(outside, key=lambda item: item.key))
        )
        checked: set[Ref[SemanticKindTag]] = set()
        pending = list(physical)
        while pending:
            current = pending.pop()
            if current in checked:
                continue
            checked.add(current)
            pending.extend(self._entry_dependencies(current) if current in self._index else ())
            if current.kind in {SemanticKind.DOMAIN, SemanticKind.DATASOURCE}:
                pending.extend(
                    item for item, scopes in self._index.scopes.items() if current in scopes
                )
        return replace(
            report,
            # Expanded dependencies are diagnostics, not directly requested inputs.
            _analysis_ready_inputs=tuple(ref for ref in ready_input_refs(report) if ref in inputs),
            _resolved_inputs=tuple(
                self.get(ref.key) for ref in ready_input_refs(report) if ref in inputs
            ),
            requested_ids=tuple(item.key for item in inputs),
            checked_ids=tuple(sorted(item.key for item in checked)),
        )

    @traced(surface="semantic", operation="validate", kind="method", stage="connect")
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

        return run_validation(
            self,
            refs=exact_refs,
            checks=checks,
            scope=scope,
        )

    def _connection_operation(self) -> AbstractContextManager[DatasourceConnectionService]:
        return project_connections(self._project)

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

        return SemanticResolver(
            project=self._project,
            require_ref=self._require_ref,
            connections=connections,
            sample_size=sample_size,
            entity_scopes=entity_scopes,
            source_bindings=source_bindings,
        )

    @traced(surface="semantic", operation="preview", kind="method", stage="connect")
    def preview(
        self,
        ref: _SemanticInput,
        /,
        *,
        scope: PreviewScope,
        source_parameters: PreviewSourceParameters | None = None,
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
            source_parameters: Optional exact entity-to-parameter mappings for
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
            source_parameters=source_parameters,
            registry=self._require_ready(),
            catalog=self,
        )
        with project_connections(self._project) as connections:
            return CatalogPreviewer(self).one(
                target,
                bindings=bindings,
                connections=connections,
                limit=limit,
                include_types=include_types,
                context_columns=context_columns,
            )

    @traced(surface="semantic", operation="preview_many", kind="method", stage="connect")
    def preview_many(
        self,
        refs: Sequence[_SemanticInput],
        /,
        *,
        scope: PreviewScope,
        source_parameters: PreviewSourceParameters | None = None,
        limit: int = PREVIEW_DEFAULT_LIMIT,
        include_types: bool = True,
    ) -> PreviewBatchResult:
        """Return a bounded batch preview for ordered named semantic objects.

        Args:
            refs: A non-empty ordered sequence of named semantic objects.
            scope: One explicit authoring scope when all refs share one entity,
                or exact entity-to-scope bindings otherwise.
            source_parameters: Optional exact entity-to-parameter mappings for
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

        sidecar = project_expression_sidecar(self._project)
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
            source_parameters=source_parameters,
            registry=reg,
            catalog=self,
        )
        with project_connections(self._project) as connections, ExitStack() as stack:
            return CatalogPreviewer(self).batch(
                normalized_refs,
                normalized=normalized,
                connections=connections,
                stack=stack,
                limit=limit,
                include_types=include_types,
            )
