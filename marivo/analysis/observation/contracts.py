"""Observation-owned immutable arguments, row contracts and private family assembly."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from marivo._temporal import PeriodCalendarSnapshotV2
from marivo.analysis.core.time_authority import ReportTimeAuthority
from marivo.analysis.observation.errors import ObservationConstructionError
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.catalog import (
    SemanticCatalog,
)
from marivo.semantic.validator import Registry

if TYPE_CHECKING:
    from marivo.analysis.observation.source_bindings import (
        SourceBindingScopes,
    )


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class ObservationOwner:
    """Current Session identity and frozen authored source authority."""

    session_id: str
    store_id: str
    catalog_identity: SemanticCatalog | None = None
    report_time: ReportTimeAuthority = field(default_factory=ReportTimeAuthority, kw_only=True)
    semantic_registry: Registry = field(kw_only=True)
    sidecar: CompiledExpressionSidecar = field(kw_only=True)
    binding_scopes: SourceBindingScopes = field(kw_only=True)
    period_calendar_snapshots: tuple[PeriodCalendarSnapshotV2, ...] = field(
        default=(), kw_only=True
    )


def construction_error(
    expected: str,
    received: str,
    *,
    repair: str = "Reconstruct the observation with current exact semantic inputs and supported coordinates.",
) -> ObservationConstructionError:
    return ObservationConstructionError(
        expected=expected, received=received, repair=repair, location="observation.construction"
    )
