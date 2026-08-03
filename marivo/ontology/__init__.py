"""Optional typed ontology guidance layered over Marivo semantic identities."""

from marivo.ontology import errors as errors
from marivo.ontology._authoring import influences, related_to
from marivo.ontology.catalog import OntologyCatalog, load
from marivo.ontology.types import SemanticEdgeRef

__all__ = [
    "OntologyCatalog",
    "SemanticEdgeRef",
    "errors",
    "influences",
    "load",
    "related_to",
]
