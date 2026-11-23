"""Project bounded related pages, search documents and exact definition facts."""

from __future__ import annotations

from typing import (
    TYPE_CHECKING,
)

from ibis.common.deferred import Deferred

from marivo._definition.deferred import root_declaration
from marivo._definition.expressions import MetricNode, RowNode
from marivo._definition.graph import computation_fingerprint
from marivo._definition.types import ExactRef, SumPermission
from marivo._kernel.refs import (
    Ref,
    SemanticKind,
    SemanticKindTag,
)
from marivo._kernel.refs import (
    ref as ref_factory,
)
from marivo.semantic import _catalog_definition
from marivo.semantic._deferred_semantics import _rescale_disclosure, consume_metric, consume_row
from marivo.semantic._metric_time_disclosure import metric_time_binding_text
from marivo.semantic.catalog_entries import (
    CatalogFilters,
    CatalogPage,
    CatalogScope,
    EntityDefinition,
    FieldDefinition,
    MetricDefinition,
    _decode_related_cursor,
    _encode_related_cursor,
)
from marivo.semantic.catalog_model.index import (
    _CatalogIndex,
)
from marivo.semantic.catalog_model.inputs import (
    _physical_dependencies,
)
from marivo.semantic.catalog_model.objects import (
    RelatedRelation,
    SemanticObject,
    _SemanticObject,
)
from marivo.semantic.definition_resolution import _Resolver
from marivo.semantic.errors import (
    ErrorKind,
    SemanticRuntimeError,
    _raise,
)
from marivo.semantic.expressions.objects import Dimension as _Dimension
from marivo.semantic.expressions.objects import (
    ExpressionContract,
    TimeField,
    _Entity,
    _Relationship,
)
from marivo.semantic.expressions.objects import Measure as _Measure
from marivo.semantic.search.models import (
    RelatedRetrieval,
    SearchDocument,
)

if TYPE_CHECKING:
    from marivo.semantic.compiled_types import CompiledSemanticState


from marivo.semantic._catalog_definition import DefinitionLookup
from marivo.semantic.catalog_model.index import CatalogItem
from marivo.semantic.catalog_model.inputs import CatalogInputLookup


def _related_refs(
    catalog: CatalogInputLookup,
    index: _CatalogIndex,
    ref: Ref[SemanticKindTag],
    relation: RelatedRelation,
) -> set[Ref[SemanticKindTag]]:
    dependencies = catalog._state.dependencies
    selected: set[Ref[SemanticKindTag]] = set()
    if relation == "dependencies":
        selected.update(catalog._entry_dependencies(ref))
    elif relation == "dependents":
        selected.update(index.dependents.get(ref, ()))
    else:
        owners = (
            {ref.path}
            if ref.kind is SemanticKind.ENTITY
            else {
                item.path
                for item in _physical_dependencies(catalog, ref)
                if item.kind is SemanticKind.ENTITY
            }
        )
        owners.update(
            item.path.rsplit(".", 1)[0]
            for item in _physical_dependencies(catalog, ref)
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
                for item in index._refs
                if item.kind
                in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION, SemanticKind.MEASURE}
                and item.path.rsplit(".", 1)[0] in owners
            )
        elif relation == "metrics":
            selected.update(
                item
                for item in index._refs
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
                        for dependency in _physical_dependencies(catalog, item)
                    )
                )
            )
        elif relation == "relationships":
            selected.update(
                ref_factory.relationship(item.semantic_id)
                for item in catalog._reg.relationships.values()
                if item.from_entity in owners or item.to_entity in owners
            )
        elif ref.kind is SemanticKind.DOMAIN:
            selected.update(
                item
                for item in index._refs
                if item.kind not in {SemanticKind.DOMAIN, SemanticKind.DATASOURCE}
                and item.path.split(".")[0] == ref.path
            )
        elif ref.kind is SemanticKind.DATASOURCE:
            selected.update(
                ref_factory.entity(item.semantic_id)
                for item in catalog._reg.entities.values()
                if item.datasource == ref.path
            )
    return selected


def related_page(
    catalog: CatalogInputLookup,
    entry: _SemanticObject[SemanticKindTag],
    relation: RelatedRelation,
    scope: CatalogScope,
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
    index = catalog._require_index()
    selected = _related_refs(catalog, index, entry._ref, relation)
    loaded = tuple(sorted((item for item in selected if item in index), key=lambda item: item.key))
    outside = tuple(sorted(item.key for item in selected if item not in index))
    token = f"{entry.id}:{relation}"
    try:
        offset = _decode_related_cursor(
            cursor, snapshot_digest=catalog.definition_fingerprint, level=token
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
        _encode_related_cursor(catalog.definition_fingerprint, token, next_offset)
        if next_offset < len(loaded)
        else None
    )
    return CatalogPage(
        tuple(catalog.get(item.key) for item in page_refs),
        len(loaded),
        scope,
        CatalogFilters("", origin=entry.id, relation=relation),
        RelatedRetrieval(candidate_window=len(loaded)),
        {},
        next_cursor,
        outside,
    )


def build_search_documents(
    catalog: DefinitionLookup, index: _CatalogIndex
) -> tuple[SearchDocument, ...]:
    documents: list[SearchDocument] = []
    for ref in index._refs:
        entry = index.entry(ref)
        assert entry is not None
        definition = entry.definition
        context = definition.context
        fields: list[tuple[str, str]] = [("business_definition", context.business_definition or "")]
        fields.extend(("alias", alias) for alias in context.aliases)
        fields.extend(("guardrail", item) for item in context.guardrails)
        if entry.domain is not None:
            domain_ref = ref_factory.domain(entry.domain)
            fields.append(
                (
                    "domain.business_definition",
                    _catalog_definition.context_for(catalog, domain_ref).business_definition or "",
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
            fields.extend((("expression", definition.expression), ("unit", definition.unit or "")))
        for dependency in catalog._state.dependencies.get(ref, ()):
            if dependency in index:
                inherited = _catalog_definition.context_for(catalog, dependency)
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


def describe_entry(
    target: Ref[SemanticKindTag], value: CatalogItem | None, state: CompiledSemanticState
) -> ExpressionContract:
    if isinstance(value, (_Entity, _Dimension, TimeField, _Measure, _Relationship)):
        return value.contract()
    if isinstance(value, Deferred):
        declaration = root_declaration(value)
        assert declaration is not None

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
            original = state.registry.declared_metrics[target.path]
            metric = consume_metric(original)
            roots, unit, permission = metric.roots, metric.unit, None
            node = metric.node
        context = declaration.ai_context
        location = declaration.location
        return ExpressionContract(
            stage="declared_measure" if declaration.kind == "measure" else "published_metric",
            _ref=_Resolver(state).exact(target),
            _roots=roots,
            unit=unit,
            sum_permission=permission,
            computation=computation_fingerprint(node),
            _reads=state.dependencies.get(target, ()),
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
