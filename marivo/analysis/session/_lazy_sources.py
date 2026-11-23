"""Source-free assembly of current Session semantic authority."""

from __future__ import annotations

from marivo.analysis.binding.observation import ObservationOwner, construction_error
from marivo.analysis.binding.source_bindings import SourceParameterScopes
from marivo.analysis.model.time_authority import ReportTimeAuthority
from marivo.semantic import SemanticCatalog
from marivo.semantic.spi import catalog_state


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
