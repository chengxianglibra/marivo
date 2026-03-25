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
    """Conservative relation discovery used for the pure refactor phase.

    Current behaviour is preserved by only translating existing claim-to-claim
    edges into explicit relation objects. No new claim pair mining happens here.
    """

    name = "default"

    def discover(
        self,
        claims: list[Claim],
        observations: list[dict[str, Any]],
        existing_edges: list[dict[str, Any]],
    ) -> list[ClaimRelation]:
        claim_ids = {claim["claim_id"] for claim in claims}
        relations: list[ClaimRelation] = []
        seen: set[tuple[str, str, str]] = set()

        for edge in existing_edges:
            if edge.get("from_node_type") != "claim" or edge.get("to_node_type") != "claim":
                continue
            from_id = edge.get("from_node_id")
            to_id = edge.get("to_node_id")
            relation_type = edge.get("edge_type")
            if from_id not in claim_ids or to_id not in claim_ids or not relation_type:
                continue
            key = (str(from_id), str(to_id), str(relation_type))
            if key in seen:
                continue
            seen.add(key)
            relations.append(
                {
                    "from_claim_id": str(from_id),
                    "to_claim_id": str(to_id),
                    "relation_type": str(relation_type),
                    "weight": float(edge.get("weight", 0.0)),
                    "match_basis": {"source": "existing_edge"},
                    "supporting_observation_ids": [],
                    "explanation": str(
                        edge.get("explanation")
                        or "Claim relation derived from synthesized graph edge."
                    ),
                }
            )
        return relations


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
