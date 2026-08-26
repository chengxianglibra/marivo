"""Project-local ontology loading and bounded catalog inspection."""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass
from importlib import util as importlib_util
from pathlib import Path
from typing import cast

from marivo.ontology._authoring import _CONTEXT, _OntologyAuthoringContext, _repair
from marivo.ontology.errors import (
    InvalidOntologyRefError,
    InvalidSemanticEdgeError,
    OntologyError,
    OntologyLoadError,
)
from marivo.ontology.types import SemanticEdgeIR
from marivo.refs import Ref, SemanticKindTag
from marivo.render import _DEFAULT_MAX_OUTPUT_BYTES, Card, RenderableResult
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.errors import SemanticError
from marivo.semantic.ir import SourceLocation
from marivo.semantic.metric_graph_canonical import fingerprint

_ONTOLOGY_SOURCE = Path("models") / "ontology.py"
_EDGE_PREVIEW_LIMIT = 8


def _validation_error(
    *, edge: SemanticEdgeIR, message: str, expected: str, received: str
) -> InvalidOntologyRefError:
    return InvalidOntologyRefError(
        kind="invalid_ontology_ref",
        message=message,
        refs=(edge.ref.key,),
        location=edge.location,
        expected=expected,
        received=received,
        repair=_repair(
            "Replace the endpoint with the exact .ref from the supplied current semantic catalog."
        ),
    )


def _validate_edges(
    edges: tuple[SemanticEdgeIR, ...], semantic: SemanticCatalog
) -> tuple[SemanticError, ...]:
    issues: list[SemanticError] = []
    for edge in edges:
        for role, endpoint in (("source", edge.source), ("target", edge.target)):
            try:
                semantic.require(cast("Ref[SemanticKindTag]", endpoint))
            except SemanticError:
                issues.append(
                    _validation_error(
                        edge=edge,
                        message=f"edge {edge.ref.path!r} {role} is absent from this semantic catalog",
                        expected=f"a current catalog {endpoint.kind.value} ref",
                        received=endpoint.key,
                    )
                )
    return tuple(issues)


def _execution_error(path: Path, error: Exception) -> OntologyError:
    if isinstance(error, OntologyError):
        return error
    return InvalidSemanticEdgeError(
        kind="invalid_semantic_edge",
        message=f"error executing authored ontology: {error}",
        expected="a valid models/ontology.py module",
        received=type(error).__name__,
        location=SourceLocation(file=str(path), line=0),
        repair=_repair(
            "Fix models/ontology.py so it imports and declares every edge successfully."
        ),
    )


def _load_edges(path: Path) -> tuple[SemanticEdgeIR, ...]:
    context = _OntologyAuthoringContext()
    token = _CONTEXT.set(context)
    module_name = f"_marivo_ontology_{fingerprint(str(path.resolve()))[:12]}"
    try:
        sys.modules.pop(module_name, None)
        spec = importlib_util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"could not load ontology source {path}")
        module = types.ModuleType(module_name)
        module.__file__ = str(path)
        module.__package__ = ""
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    except Exception as error:
        raise OntologyLoadError((_execution_error(path, error),)) from error
    finally:
        _CONTEXT.reset(token)
        sys.modules.pop(module_name, None)
    return tuple(context.edges)


@dataclass(frozen=True, repr=False)
class OntologyCatalog(RenderableResult):
    """Immutable, bounded view of one optional project ontology."""

    configured: bool
    definition_fingerprint: str
    semantic_catalog_fingerprint: str
    source_location: str | None
    _edges: tuple[SemanticEdgeIR, ...]

    @property
    def edge_count(self) -> int:
        """Return the number of validated authored edges."""
        return len(self._edges)

    def _repr_identity(self) -> str:
        return (
            "OntologyCatalog "
            f"configured={str(self.configured).lower()} "
            f"fingerprint={self.definition_fingerprint[:12]} edges={len(self._edges)}"
        )

    def _card(self) -> Card:
        card = (
            Card(
                identity=self._repr_identity(),
                available=(".configured", ".edge_count", ".show()"),
            )
            .field("definition_fingerprint", self.definition_fingerprint)
            .field("semantic_catalog_fingerprint", self.semantic_catalog_fingerprint)
            .field("source", self.source_location or "not configured")
        )
        visible = self._edges[:_EDGE_PREVIEW_LIMIT]
        if visible:
            card = card.listing(
                "edges",
                (
                    f"{edge.ref.key}: {edge.relation} "
                    f"{edge.source.key} -> {edge.target.key}; "
                    f"business_definition={edge.context.business_definition}"
                    for edge in visible
                ),
            )
        omitted = len(self._edges) - len(visible)
        if omitted:
            card = card.field("edges_omitted", str(omitted))
        return card

    def render(self, *, max_output_bytes: int | None = _DEFAULT_MAX_OUTPUT_BYTES) -> str:
        """Render bounded deterministic configuration and edge summaries."""
        return self._card().render(max_output_bytes=max_output_bytes)

    def show(self, *, max_output_bytes: int | None = _DEFAULT_MAX_OUTPUT_BYTES) -> None:
        """Print the bounded ontology catalog summary."""
        print(self.render(max_output_bytes=max_output_bytes))

    def _edges_for_discovery(self) -> tuple[SemanticEdgeIR, ...]:
        """Return the immutable internal edge view for the analysis bridge."""
        return self._edges


def load(*, semantic: SemanticCatalog) -> OntologyCatalog:
    """Load and validate the optional project ontology against a semantic catalog.

    Args:
        semantic: The exact loaded SemanticCatalog that owns endpoint identities.

    Returns:
        An immutable OntologyCatalog; ``configured`` is false when no source exists.

    Example:
        ontology = mo.load(semantic=session.catalog)
        ontology.show()

    Constraints:
        Reads only ``models/ontology.py`` under the semantic catalog workspace.
        Invalid sources raise OntologyLoadError; no partial catalog is returned.
    """
    if type(semantic) is not SemanticCatalog:
        raise InvalidOntologyRefError(
            kind="invalid_ontology_ref",
            message="mo.load requires an exact SemanticCatalog",
            expected="SemanticCatalog from ms.load() or session.catalog",
            received=type(semantic).__name__,
            repair=_repair(
                "Pass the exact SemanticCatalog returned by ms.load() or session.catalog."
            ),
        )
    source = semantic.workspace_dir / _ONTOLOGY_SOURCE
    if not source.is_file():
        empty_fingerprint = fingerprint(
            ["marivo.ontology/v1", semantic.definition_fingerprint, "absent"]
        )
        return OntologyCatalog(
            configured=False,
            definition_fingerprint=empty_fingerprint,
            semantic_catalog_fingerprint=semantic.definition_fingerprint,
            source_location=None,
            _edges=(),
        )
    edges = _load_edges(source)
    issues = _validate_edges(edges, semantic)
    if issues:
        raise OntologyLoadError(issues)
    ordered = tuple(sorted(edges, key=lambda edge: edge.ref.path))
    definition_fingerprint = fingerprint(
        ["marivo.ontology/v1", *(edge.canonical_payload() for edge in ordered)]
    )
    return OntologyCatalog(
        configured=True,
        definition_fingerprint=definition_fingerprint,
        semantic_catalog_fingerprint=semantic.definition_fingerprint,
        source_location=str(source.resolve()),
        _edges=ordered,
    )


__all__ = ["OntologyCatalog", "load"]
