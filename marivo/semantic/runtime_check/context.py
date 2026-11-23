"""Local authority and exact identities for online validation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, Protocol, cast

import ibis.expr.types as ir

from marivo._kernel.refs import (
    FieldKind,
    MeasureKind,
    Ref,
    SemanticKind,
    SemanticKindTag,
)
from marivo.datasource.spi import (
    AuthoringScope,
    DatasourceConnectionService,
)
from marivo.semantic.catalog_model.inputs import CatalogInputLookup
from marivo.semantic.catalog_model.objects import lookup_state

if TYPE_CHECKING:
    from marivo.semantic.runtime_check.resolver import SemanticResolver
    from marivo.semantic.static_check.registry import Registry


def _reverse_affected(
    catalog: ValidationLookup,
    roots: Sequence[Ref[SemanticKindTag]],
) -> tuple[Ref[SemanticKindTag], ...]:
    affected = set(roots)
    changed = True
    while changed:
        changed = False
        for ref, dependencies in lookup_state(catalog).dependencies.items():
            if ref not in affected and any(dependency in affected for dependency in dependencies):
                affected.add(ref)
                changed = True
    return tuple(sorted(affected, key=lambda ref: ref.key))


def _field_owner(registry: Registry, ref: Ref[SemanticKindTag]) -> str:
    if ref.kind in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION}:
        return registry.dimensions[ref.path].entity
    if ref.kind is SemanticKind.MEASURE:
        return registry.measures[ref.path].entity
    raise AssertionError(f"unsupported online validation field kind: {ref.kind}")


def _materialized_field(
    resolver: SemanticResolver,
    ref: Ref[FieldKind],
    table: ir.Table,
) -> ir.Value:
    if ref.kind in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION}:
        return resolver.dimension_on(ref, table)
    return resolver.measure_on(cast("Ref[MeasureKind]", ref), table)


class ValidationLookup(CatalogInputLookup, Protocol):
    def _connection_operation(self) -> AbstractContextManager[DatasourceConnectionService]: ...

    def _semantic_resolver(
        self,
        *,
        connections: DatasourceConnectionService,
        sample_size: int | None = None,
        entity_scopes: Mapping[str, AuthoringScope] | None = None,
    ) -> SemanticResolver: ...


def validation_connections(
    catalog: ValidationLookup,
) -> AbstractContextManager[DatasourceConnectionService]:
    return catalog._connection_operation()  # noqa: SLF001 - Keep online checks in the owning Catalog's connection scope.


def validation_resolver(
    catalog: ValidationLookup,
    *,
    connections: DatasourceConnectionService,
    entity_scopes: Mapping[str, AuthoringScope],
) -> SemanticResolver:
    return catalog._semantic_resolver(  # noqa: SLF001 - The Catalog owns the validation scope.
        connections=connections, entity_scopes=entity_scopes
    )
