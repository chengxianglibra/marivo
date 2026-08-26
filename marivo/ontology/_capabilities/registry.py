"""Closed ontology help descriptors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from marivo._authoring.model import AuthoringEffects


@dataclass(frozen=True)
class OntologyDescriptor:
    canonical_id: str
    public_entrypoint: str | None
    callable_path: str | None
    summary: str
    body: tuple[str, ...]
    effects: AuthoringEffects


_LOCAL = AuthoringEffects(data_access="local_metadata_read", connection="none")
_AUTHOR = AuthoringEffects(
    data_access="none",
    connection="none",
    mutations=("semantic_source",),
)


_DESCRIPTORS = (
    OntologyDescriptor(
        canonical_id="authoring",
        public_entrypoint="mo.load(semantic=...)",
        callable_path="marivo.ontology.load",
        summary="Load the one optional project ontology and validate every endpoint.",
        body=(
            "Source: models/ontology.py",
            "Construct edges only with mo.influences(...) or mo.related_to(...).",
            "All endpoints are exact EntityRef, MeasureRef, or MetricRef values.",
            "ontology = mo.load(semantic=session.catalog)",
            "ontology.show()",
            "Ontology supplies discovery context only; it cannot execute semantic meaning.",
        ),
        effects=_LOCAL,
    ),
    OntologyDescriptor(
        canonical_id="influences",
        public_entrypoint="mo.influences(name=..., driver=..., outcome=..., ai_context=...)",
        callable_path="marivo.ontology.influences",
        summary="Author a directional driver hypothesis without asserting causality.",
        body=(
            "driver: EntityRef | MeasureRef | MetricRef",
            "outcome: EntityRef | MetricRef",
            "ai_context: ms.ai_context(...) with a non-empty business_definition",
            "Discovery matches the outcome and proposes the driver.",
            "The relation is a hypothesis and does not assert causality.",
            "It does not encode effect size, confidence, evidence, joins, filters, or SQL.",
        ),
        effects=_AUTHOR,
    ),
    OntologyDescriptor(
        canonical_id="related_to",
        public_entrypoint="mo.related_to(name=..., left=..., right=..., ai_context=...)",
        callable_path="marivo.ontology.related_to",
        summary="Author a symmetric contextual relation between two semantic refs.",
        body=(
            "left/right: EntityRef | MeasureRef | MetricRef",
            "ai_context: ms.ai_context(...) with a non-empty business_definition",
            "Discovery proposes the endpoint opposite exactly one matching source anchor.",
            "Swapped endpoints are the same canonical pair; identical endpoints are rejected.",
            "It does not imply joinability or statistical association.",
        ),
        effects=_AUTHOR,
    ),
)


class OntologyRegistry:
    surface: Literal["ontology"] = "ontology"

    def __init__(self) -> None:
        self._by_id = MappingProxyType({item.canonical_id: item for item in _DESCRIPTORS})

    def canonical_ids(self) -> tuple[str, ...]:
        return tuple(self._by_id)

    def discovery_ids(self) -> tuple[str, ...]:
        """Return every ontology capability because the surface is already bounded."""
        return self.canonical_ids()

    def by_canonical_id(self, canonical_id: str) -> OntologyDescriptor:
        try:
            return self._by_id[canonical_id]
        except KeyError as error:
            raise KeyError(canonical_id) from error

    def by_callable(self, value: object) -> OntologyDescriptor:
        from marivo.ontology import influences, load, related_to

        candidates: dict[Callable[..., object], str] = {
            load: "authoring",
            influences: "influences",
            related_to: "related_to",
        }
        if not callable(value):
            raise KeyError(value)
        try:
            return self.by_canonical_id(candidates[value])
        except (KeyError, TypeError) as error:
            raise KeyError(value) from error


REGISTRY = OntologyRegistry()


__all__ = ["REGISTRY", "OntologyDescriptor", "OntologyRegistry"]
