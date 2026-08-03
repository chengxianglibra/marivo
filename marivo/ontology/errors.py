"""Structured ontology authoring, loading, and help errors."""

from __future__ import annotations

from collections.abc import Iterable

from marivo._authoring.model import AuthoringRepair
from marivo.introspection.live.errors import HelpTargetErrorPayload
from marivo.introspection.live.model import LiveHelpTarget
from marivo.semantic.errors import SemanticError


class OntologyError(SemanticError):
    """Base class for ontology-owned semantic errors."""


class InvalidOntologyRefError(OntologyError):
    """An edge endpoint is not an allowed current-catalog semantic ref."""


class InvalidSemanticEdgeError(OntologyError):
    """An authored edge violates its closed relation contract."""


class OntologyLoadError(OntologyError):
    """The authored ontology source could not be loaded as one valid catalog."""

    issues: tuple[SemanticError, ...]

    def __init__(self, issues: Iterable[SemanticError]) -> None:
        self.issues = tuple(issues)
        super().__init__(
            kind="invalid_semantic_edge",
            message=f"ontology validation failed with {len(self.issues)} issue(s)",
            details={"issues": tuple(str(issue) for issue in self.issues)},
            expected="one fully valid project ontology",
            received=f"{len(self.issues)} validation issue(s)",
            repair=AuthoringRepair(
                kind="reauthor",
                help_target=LiveHelpTarget(surface="ontology", canonical_id="authoring"),
                action="Inspect every reported edge and repair the authored ontology source.",
            ),
        )


class OntologyHelpTargetError(OntologyError):
    """Ontology-native rejection of an unknown live-help target."""

    def __init__(self, payload: HelpTargetErrorPayload) -> None:
        super().__init__(
            kind="ontology_help_target_not_found",
            message=payload.message,
            expected=f"accepted ontology help target ({', '.join(payload.accepted_kinds)})",
            received=payload.received,
            location_label="ontology help surface",
            repair=AuthoringRepair(
                kind="retry",
                help_target=LiveHelpTarget(surface="ontology"),
                action="Retry with a registered ontology help target.",
                candidates=payload.candidates,
            ),
        )


__all__ = [
    "InvalidOntologyRefError",
    "InvalidSemanticEdgeError",
    "OntologyError",
    "OntologyHelpTargetError",
    "OntologyLoadError",
]
