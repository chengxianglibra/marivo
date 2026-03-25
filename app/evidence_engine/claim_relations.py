"""Claim relation discovery for the layered evidence engine.

This module intentionally separates two concerns:
- discovering stable in-memory relations between claims
- materializing those relations into graph edges later in the pipeline

Phase 1.4 keeps discovery conservative. The default implementation only
returns relations that can be derived from already-synthesized graph edges,
which preserves current behaviour while making room for richer relation mining
in later phases.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.evidence_engine.schemas import Claim, ClaimRelation


class ClaimRelationDiscovery(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def discover(
        self,
        claims: list[Claim],
        observations: list[dict[str, Any]],
        existing_edges: list[dict[str, Any]],
    ) -> list[ClaimRelation]:
        raise NotImplementedError


class DefaultClaimRelationDiscovery(ClaimRelationDiscovery):
    """Default relation discovery for the strict five-layer pipeline.

    Relation discovery must not depend on previously materialized edges,
    otherwise the pipeline becomes cyclical: edges create relations and
    relations create edges again. Until Factum has observation-first relation
    mining rules, the default implementation returns no derived relations.
    """

    name = "default"

    def discover(
        self,
        claims: list[Claim],
        observations: list[dict[str, Any]],
        existing_edges: list[dict[str, Any]],
    ) -> list[ClaimRelation]:
        return []


def materialize_relations_as_edges(relations: list[ClaimRelation]) -> list[dict[str, Any]]:
    """Convert claim relations into evidence-edge rows without persisting them."""

    edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for relation in relations:
        key = (
            relation["from_claim_id"],
            relation["to_claim_id"],
            relation["relation_type"],
        )
        if key in seen:
            continue
        seen.add(key)
        edges.append(
            {
                "from_node_id": relation["from_claim_id"],
                "from_node_type": "claim",
                "to_node_id": relation["to_claim_id"],
                "to_node_type": "claim",
                "edge_type": relation["relation_type"],
                "weight": relation["weight"],
                "explanation": relation["explanation"],
            }
        )
    return edges
