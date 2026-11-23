"""Observation-owned immutable arguments, row contracts and private family assembly."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from marivo.analysis.model.errors import ObservationConstructionError
from marivo.analysis.model.time_authority import ReportTimeAuthority
from marivo.semantic.catalog import (
    SemanticCatalog,
)
from marivo.semantic.validator import Registry

if TYPE_CHECKING:
    from marivo.analysis.binding.source_bindings import (
        SourceParameterScopes,
    )
    from marivo.semantic.validator import CompiledExpressionSidecar


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class ObservationOwner:
    """Current Session identity and frozen authored source authority."""

    session_id: str
    store_id: str
    catalog_identity: SemanticCatalog | None = None
    report_time: ReportTimeAuthority = field(default_factory=ReportTimeAuthority, kw_only=True)
    semantic_registry: Registry = field(kw_only=True)
    sidecar: CompiledExpressionSidecar = field(kw_only=True)
    binding_scopes: SourceParameterScopes = field(kw_only=True)


def construction_error(
    expected: str,
    received: str,
    *,
    repair: str = "Reconstruct the observation with current exact semantic inputs and supported coordinates.",
) -> ObservationConstructionError:
    return ObservationConstructionError(
        expected=expected, received=received, repair=repair, location="observation.construction"
    )
