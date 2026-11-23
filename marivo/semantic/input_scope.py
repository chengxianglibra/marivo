"""Check ownership and project roots before rebinding."""

from __future__ import annotations

from pathlib import Path

from marivo._definition.types import DefinitionGraphError, InputContext, Owner, merge_input_contexts
from marivo.semantic.errors import SemanticExpressionError


def validate_context(
    context: InputContext, *, project_root: Path, owner: Owner | None = None
) -> None:
    """Check captured project and existing owner constraints before normalization."""
    try:
        merge_input_contexts(context, InputContext(project_root.resolve(), owner))
        if context.owner is not None and owner is None:
            raise DefinitionGraphError(
                "A Session-owned input requires its owning Session receiver.",
                kind="ownership_conflict",
            )
    except DefinitionGraphError as error:
        raise SemanticExpressionError.from_definition(
            error, operation="semantic input binding"
        ) from error
