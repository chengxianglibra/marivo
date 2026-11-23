"""Captured project and session ownership constraints."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from marivo._kernel.errors import invalid


@dataclass(frozen=True, slots=True)
class Owner:
    """Existing Session ownership carried by a lazy Population or Query definition."""

    session_id: str
    owner_id: str


@dataclass(frozen=True, slots=True)
class InputContext:
    """Immutable project and existing Session constraints of a captured input."""

    project_root: Path | None = None
    owner: Owner | None = None


def merge_input_contexts(*contexts: InputContext) -> InputContext:
    """Combine constraints before discarding any outer expression carrier."""
    project: Path | None = None
    owner: Owner | None = None
    for context in contexts:
        if context.project_root is not None:
            normalized = context.project_root.resolve()
            if project is not None and project != normalized:
                invalid(
                    f"Semantic inputs belong to different projects ({project} and {normalized}); obtain inputs from the receiving Catalog.",
                    kind="ownership_conflict",
                )
            project = normalized
        if context.owner is not None:
            if owner is not None and owner != context.owner:
                invalid(
                    f"Semantic inputs belong to different Sessions ({owner.session_id}/{owner.owner_id} and {context.owner.session_id}/{context.owner.owner_id}); use inputs owned by the receiving Session.",
                    kind="ownership_conflict",
                )
            owner = context.owner
    return InputContext(project, owner)
