"""Declaration-order exact-ref and scoped navigation index."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from functools import cached_property
from types import MappingProxyType
from typing import (
    TYPE_CHECKING,
    TypeAlias,
    cast,
)

from marivo._kernel.refs import (
    Ref,
    SemanticKind,
    SemanticKindTag,
)
from marivo._kernel.refs import (
    ref as ref_factory,
)
from marivo.datasource.spi import (
    DatasourceIR,
    DatasourceSourceLocation,
)
from marivo.semantic.catalog_model.details import (
    DatasourceDetails,
    DomainDetails,
)
from marivo.semantic.catalog_model.objects import (
    _ENTRY_TYPE_BY_KIND,
    CatalogLookup,
    Datasource,
    Domain,
    SemanticObject,
    _SemanticObject,
)
from marivo.semantic.definition_resolution import (
    CatalogValues,
    ResolvedValue,
)
from marivo.semantic.ir import (
    DomainIR,
    SourceLocation,
)

if TYPE_CHECKING:
    from marivo.semantic.static_check.registry import Registry


CatalogItem: TypeAlias = _SemanticObject[SemanticKindTag] | ResolvedValue


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
    ds_ir: DatasourceIR, build_index: _CatalogBuildIndex, catalog: CatalogLookup
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
    return Datasource(_ref=ref, _details=details, _catalog=catalog)


def _build_domain_object(
    model_ir: DomainIR, build_index: _CatalogBuildIndex, catalog: CatalogLookup
) -> Domain:
    ref = ref_factory.domain(model_ir.name)
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
    return Domain(_ref=ref, _details=details, _catalog=catalog)


class _CatalogIndex:
    """Immutable exact-ref and scoped-navigation index for one catalog.

    Entities, fields, published Metrics and Relationships are expression
    values resolved lazily from compiled state; the remaining kinds are entries.
    """

    def __init__(
        self,
        catalog: CatalogLookup,
        datasource_irs: tuple[DatasourceIR, ...],
        registry: Registry,
    ) -> None:
        self.values = CatalogValues(catalog._state)
        self._dependencies = catalog._state.dependencies
        build_index = _CatalogBuildIndex(registry)
        datasource_irs = datasource_irs or tuple(registry.datasources.values())
        entries: tuple[Domain | Datasource, ...] = (
            *(
                _build_domain_object(item, build_index, catalog)
                for item in registry.domains.values()
            ),
            *(_build_datasource_object(item, build_index, catalog) for item in datasource_irs),
        )
        self._entries: Mapping[Ref[SemanticKindTag], Domain | Datasource] = MappingProxyType(
            {entry._ref: entry for entry in entries}
        )
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
                ref: self._entries[ref]
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

    def entries(self) -> Iterable[SemanticObject]:
        return self._public_entries.values()

    def __contains__(self, ref: Ref[SemanticKindTag]) -> bool:
        return ref in self._public_entries

    @cached_property
    def dependents(self) -> Mapping[Ref[SemanticKindTag], tuple[Ref[SemanticKindTag], ...]]:
        reverse: dict[Ref[SemanticKindTag], list[Ref[SemanticKindTag]]] = {}
        for ref, dependencies in self._dependencies.items():
            for dependency in dependencies:
                reverse.setdefault(dependency, []).append(ref)
        return MappingProxyType({ref: tuple(items) for ref, items in reverse.items()})

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
