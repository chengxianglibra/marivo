"""Closed ontology help descriptors."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from marivo._authoring.model import AuthoringEffects
from marivo.introspection.capabilities.registry import SurfaceRegistry, TypeContractSpec
from marivo.introspection.live.model import LiveHelpTarget
from marivo.ontology import errors
from marivo.ontology.catalog import OntologyCatalog
from marivo.ontology.types import SemanticEdgeRef


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
        callable_path="marivo.ontology.catalog.load",
        summary="Load the one optional project ontology and validate every endpoint.",
        body=(
            "Author models/ontology.py with mo.influences(...) or mo.related_to(...).",
            "The source module and its imports execute as Python; their effects are not limited by Marivo.",
            "There is no public analysis-side hypothesis discovery entrypoint.",
            "Ontology and semantic catalog fingerprints jointly identify this association.",
        ),
        output_family="OntologyCatalog",
        constraints=(
            "semantic must be the exact SemanticCatalog from ms.load() or session.catalog; it is not refreshed from disk.",
            "The source is under that catalog's workspace; an absent source returns configured=False.",
            "Every endpoint must resolve in the supplied catalog; invalid sources raise OntologyLoadError without a partial catalog. Inspect error.issues for each repair.",
            "Edges grant no causal, analysis-admission, or Artifact authority.",
        ),
        minimal_example=(
            "semantic_catalog = ms.load()\n"
            "ontology = mo.load(semantic=semantic_catalog)\n"
            "ontology.show()\n"
            "marivo.help(type(ontology))"
        ),
        effects=_LOCAL,
    ),
    OntologyDescriptor(
        canonical_id="influences",
        public_entrypoint="mo.influences(name=..., driver=..., outcome=..., ai_context=...)",
        callable_path="marivo.ontology._authoring.influences",
        summary="Author a directional driver hypothesis without asserting causality.",
        body=(
            "The driver is a hypothesis about the outcome, not an assertion of causality.",
            "It does not encode effect size, confidence, evidence, joins, filters, or SQL.",
        ),
        output_family="SemanticEdgeRef",
        constraints=(
            "name is a unique lowercase dotted snake_case identity.",
            "Endpoints must be native ms.ref lookup symbols, not catalog values or internal refs.",
            "driver accepts Entity, Measure, or Metric; outcome accepts Entity or Metric.",
            'Construct endpoints with marivo.help("semantic.ref").',
            'ai_context requires a non-empty business_definition; see marivo.help("semantic.ai_context").',
            'Declare in models/ontology.py and load with marivo.help("ontology.authoring").',
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
        callable_path="marivo.ontology._authoring.related_to",
        summary="Author a symmetric contextual relation between two semantic refs.",
        body=(
            "Swapped endpoints form the same pair; only one declaration per pair is allowed.",
            "It does not imply joinability or statistical association.",
        ),
        output_family="SemanticEdgeRef",
        constraints=(
            "name is a unique lowercase dotted snake_case identity.",
            "Endpoints must be distinct native ms.ref lookup symbols, not catalog values or internal refs.",
            "Both endpoints accept Entity, Measure, or Metric.",
            'Construct endpoints with marivo.help("semantic.ref").',
            'ai_context requires a non-empty business_definition; see marivo.help("semantic.ai_context").',
            'Declare in models/ontology.py and load with marivo.help("ontology.authoring").',
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


REGISTRY = SurfaceRegistry("ontology", _DESCRIPTORS)


def _type_contracts() -> Mapping[type, TypeContractSpec]:

    return MappingProxyType(
        {
            OntologyCatalog: TypeContractSpec(
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
                type_page_methods=("render", "show"),
            ),
            SemanticEdgeRef: TypeContractSpec(
                name="SemanticEdgeRef",
                producers=(
                    LiveHelpTarget(surface="ontology", canonical_id="influences"),
                    LiveHelpTarget(surface="ontology", canonical_id="related_to"),
                ),
                public_properties=("kind", "path", "key"),
                public_methods=("to_dict",),
                type_page_methods=("to_dict",),
            ),
        }
    )


TYPE_CONTRACTS = _type_contracts()


def _error_types() -> Mapping[str, type]:

    return MappingProxyType(
        {
            name: value
            for name, value in vars(errors).items()
            if isinstance(value, type) and issubclass(value, errors.OntologyError)
        }
    )


ERROR_TYPES = _error_types()
