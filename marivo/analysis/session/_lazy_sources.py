"""Source-free assembly of current Session semantic authority."""

from __future__ import annotations

from marivo._temporal import PeriodCalendarSnapshotV1
from marivo.analysis.core.time_authority import ReportTimeAuthority
from marivo.analysis.observation.contracts import ObservationOwner, construction_error
from marivo.analysis.observation.source_bindings import SourceBindingScopes
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.validator import Registry


def make_source_owner(
    *,
    semantic_registry: Registry,
    sidecar: CompiledExpressionSidecar,
    session_id: str,
    store_id: str,
    catalog: SemanticCatalog | None,
    report_time: ReportTimeAuthority,
    period_calendar_snapshots: tuple[PeriodCalendarSnapshotV1, ...] = (),
) -> ObservationOwner:
    """Bind immutable semantic authority without a Dataset family or action port."""
    if not semantic_registry._frozen:
        raise construction_error("frozen in-memory semantic Registry", "mutable Registry")
    if catalog is not None and catalog._reg is not semantic_registry:
        raise construction_error("catalog and Registry with shared authority", "mismatched catalog")
    return ObservationOwner(
        session_id=session_id,
        store_id=store_id,
        catalog_identity=catalog,
        report_time=report_time,
        semantic_registry=semantic_registry,
        sidecar=sidecar,
        binding_scopes=SourceBindingScopes.from_registry(semantic_registry),
        period_calendar_snapshots=period_calendar_snapshots,
    )
