"""Observation-owned immutable arguments, row contracts and private family assembly."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from marivo._temporal import PeriodCalendarSnapshotV1
from marivo.analysis.core.time_authority import ReportTimeAuthority
from marivo.analysis.datasets.descriptors import (
    _canonical_digest,
)
from marivo.analysis.observation.errors import ObservationConstructionError
from marivo.refs import (
    SemanticKind,
    _create_ref,
)
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.catalog import (
    SemanticCatalog,
)
from marivo.semantic.metric_graph_lowering import dependency_digest
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
    period_calendar_snapshots: tuple[PeriodCalendarSnapshotV1, ...] = field(
        default=(), kw_only=True
    )


@dataclass(slots=True, repr=False)
class ObservationSourceContext:
    """Explicit current Session semantics, resolved only for authored enrichment."""

    current: ObservationOwner | None = None


def construction_error(
    expected: str,
    received: str,
    *,
    repair: str = "Reconstruct the observation with current exact semantic inputs and supported coordinates.",
) -> ObservationConstructionError:
    return ObservationConstructionError(
        expected=expected, received=received, repair=repair, location="observation.construction"
    )


def path_dependency_fingerprint(
    owner: ObservationOwner, source: str, paths: tuple[tuple[str, ...], ...]
) -> str:
    """Bind ordered paths to their owning semantic source and join definitions."""
    registry = owner.semantic_registry
    if source not in registry.entities:
        raise construction_error("current semantic source Entity", "unknown path source")
    entity_ids = {source}
    for path in paths:
        for relationship_id in path:
            relationship = registry.relationships.get(relationship_id)
            if relationship is None:
                raise construction_error(
                    "current governed relationship path", "unknown path relationship"
                )
            entity_ids.update((relationship.from_entity, relationship.to_entity))
    # The semantic owner includes relationships joining reached Entities in its
    # canonical dependency closure, together with source and datasource facts.
    # Retain authored path order separately; no second input graph is created.
    dependencies = dependency_digest(
        registry,
        sidecar=owner.sidecar,
        semantic_refs=tuple(_create_ref(SemanticKind.ENTITY, name) for name in sorted(entity_ids)),
    )
    return _canonical_digest(
        ("observation.path-dependencies/v1", source, paths, dependencies.digest)
    )
