"""Closed ontology help descriptors."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from marivo._authoring.model import AuthoringEffects
from marivo.introspection.live.model import LiveHelpTarget


@dataclass(frozen=True)
class OntologyDescriptor:
    canonical_id: str
    public_entrypoint: str | None
    callable_path: str | None
    summary: str
    body: tuple[str, ...]
    output_family: str
    constraints: tuple[str, ...]
    minimal_example: str
    effects: AuthoringEffects


@dataclass(frozen=True)
class OntologyTypeContract:
    """Stable public fields and flow edges for one ontology runtime type."""

    name: str
    producers: tuple[LiveHelpTarget, ...]
    public_properties: tuple[str, ...] = ()
    public_methods: tuple[str, ...] = ()


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
            "Ontology supplies discovery context only; it cannot execute semantic meaning.",
        ),
        output_family="OntologyCatalog",
        constraints=(
            "semantic must be an exact current SemanticCatalog.",
            "Only models/ontology.py is read; an absent source returns configured=False.",
            "Every endpoint must resolve in the supplied catalog; invalid sources return no partial catalog.",
        ),
        minimal_example=(
            "semantic_catalog = ms.load()\n"
            "ontology = mo.load(semantic=semantic_catalog)\n"
            "ontology.show()"
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
        output_family="SemanticEdgeRef",
        constraints=(
            "name is a unique lowercase dotted snake_case identity.",
            "driver is an EntityRef, MeasureRef, or MetricRef; outcome is an EntityRef or MetricRef.",
            "ai_context requires a non-empty business_definition.",
            "The declaration is valid only while mo.load(...) evaluates models/ontology.py.",
        ),
        minimal_example=(
            "driver = ms.ref.metric('sales.refund_rate')\n"
            "outcome = ms.ref.metric('sales.healthy_order_rate')\n"
            "edge = mo.influences(\n"
            "    name='refund_pressure',\n"
            "    driver=driver,\n"
            "    outcome=outcome,\n"
            "    ai_context=ms.ai_context(\n"
            "        business_definition='Refunds may degrade order health.'\n"
            "    ),\n"
            ")"
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
        output_family="SemanticEdgeRef",
        constraints=(
            "name is a unique lowercase dotted snake_case identity.",
            "left and right are distinct EntityRef, MeasureRef, or MetricRef values.",
            "ai_context requires a non-empty business_definition.",
            "The declaration is valid only while mo.load(...) evaluates models/ontology.py.",
        ),
        minimal_example=(
            "left = ms.ref.metric('sales.refund_rate')\n"
            "right = ms.ref.metric('sales.support_ticket_rate')\n"
            "edge = mo.related_to(\n"
            "    name='refund_and_support_pressure',\n"
            "    left=left,\n"
            "    right=right,\n"
            "    ai_context=ms.ai_context(\n"
            "        business_definition='Both describe order friction.'\n"
            "    ),\n"
            ")"
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


def _type_contracts() -> Mapping[type, OntologyTypeContract]:
    from marivo.ontology.catalog import OntologyCatalog
    from marivo.ontology.types import SemanticEdgeRef

    return MappingProxyType(
        {
            OntologyCatalog: OntologyTypeContract(
                name="OntologyCatalog",
                producers=(LiveHelpTarget(surface="ontology", canonical_id="authoring"),),
                public_properties=(
                    "configured",
                    "definition_fingerprint",
                    "semantic_catalog_fingerprint",
                    "source_location",
                    "edge_count",
                ),
                public_methods=("render", "show"),
            ),
            SemanticEdgeRef: OntologyTypeContract(
                name="SemanticEdgeRef",
                producers=(
                    LiveHelpTarget(surface="ontology", canonical_id="influences"),
                    LiveHelpTarget(surface="ontology", canonical_id="related_to"),
                ),
                public_properties=("kind", "path", "key"),
                public_methods=("to_dict",),
            ),
        }
    )


TYPE_CONTRACTS = _type_contracts()


def _error_types() -> Mapping[str, type]:
    from marivo.ontology import errors

    return MappingProxyType(
        {
            name: value
            for name, value in vars(errors).items()
            if isinstance(value, type) and issubclass(value, errors.OntologyError)
        }
    )


ERROR_TYPES = _error_types()


__all__ = [
    "ERROR_TYPES",
    "REGISTRY",
    "TYPE_CONTRACTS",
    "OntologyDescriptor",
    "OntologyRegistry",
    "OntologyTypeContract",
]
