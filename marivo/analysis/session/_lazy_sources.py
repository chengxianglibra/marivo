"""Source-free assembly of current Session semantic authority."""

from __future__ import annotations

from ibis.common.deferred import Deferred

from marivo._definition.types import Owner
from marivo.analysis.binding.observation import ObservationOwner, construction_error
from marivo.analysis.binding.source_bindings import SourceParameterScopes
from marivo.analysis.model.errors import reject
from marivo.analysis.model.time_authority import ReportTimeAuthority
from marivo.semantic import RowExpression, SemanticCatalog
from marivo.semantic.spi import (
    FieldExpression,
    RowPredicate,
    bind_field,
    bind_predicate,
    catalog_state,
)


def make_source_owner(
    catalog: SemanticCatalog,
    /,
    *,
    session_id: str,
    store_id: str,
    report_time: ReportTimeAuthority,
) -> ObservationOwner:
    """Bind immutable semantic authority without a Dataset family or action port."""
    state = catalog_state(catalog)
    semantic_registry = state.registry
    if not semantic_registry.is_frozen:
        raise construction_error("frozen in-memory semantic Registry", "mutable Registry")
    return ObservationOwner(
        session_id=session_id,
        store_id=store_id,
        catalog_identity=catalog,
        report_time=report_time,
        semantic_registry=semantic_registry,
        sidecar=state.sidecar,
        binding_scopes=SourceParameterScopes.from_registry(semantic_registry),
    )


def _predicate_input(
    value: RowPredicate | None, catalog: SemanticCatalog, owner: Owner, *, location: str
) -> Deferred | None:
    if value is None:
        return None
    if not isinstance(value, RowExpression) or value._role != "boolean":  # type: ignore[redundant-expr]  # Preserve validation for untyped inputs.
        reject(
            "a row Predicate",
            type(value).__name__,
            "Use a condition built from loaded fields or in_population(...).",
            location,
        )
    if value._context.owner not in (None, owner):
        reject(
            "a Predicate owned by this Session",
            "another Store or Session",
            "Rebuild the predicate in its owning Session.",
            location,
        )
    return bind_predicate(value, catalog, owner=owner)


def _field_input(
    value: FieldExpression, catalog: SemanticCatalog, owner: Owner, *, location: str
) -> Deferred:
    if not isinstance(value, RowExpression) or value._role not in ("dimension", "time", "numeric"):  # type: ignore[redundant-expr]  # Preserve validation for untyped inputs.
        reject(
            "a semantic field expression",
            type(value).__name__,
            "Obtain a field from the receiving Catalog.",
            location,
        )
    return bind_field(value, catalog, owner=owner)
