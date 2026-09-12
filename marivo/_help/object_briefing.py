"""No-I/O semantic object briefings for unified help."""

from __future__ import annotations


def is_semantic_object(target: object) -> bool:
    """Return whether target is an exact Ref or registered CatalogEntry."""
    from marivo.refs import Ref

    if type(target) is Ref:
        return True
    if type(target).__module__ != "marivo.semantic.catalog":
        return False
    from marivo.semantic.catalog import CatalogEntry

    return isinstance(target, CatalogEntry)


def semantic_object_path(target: object) -> str:
    """Return the exact identity path after semantic-object validation."""
    from marivo.refs import Ref
    from marivo.semantic.catalog import CatalogEntry

    if type(target) is Ref:
        return target.path
    if isinstance(target, CatalogEntry):
        return target.ref.path
    raise RuntimeError(f"unsupported semantic object: {type(target).__name__}")


def _analysis_handoff_lines(kind: str) -> tuple[str, ...]:
    from marivo.analysis._capabilities.dataset_model import CallableInput
    from marivo.analysis._capabilities.registry import REGISTRY
    from marivo.refs import SemanticKind

    try:
        semantic_kind = SemanticKind(kind)
    except ValueError:
        return ()
    return tuple(
        f"{descriptor.public_entrypoint}(...) -> {descriptor.output}; "
        f'help: marivo.help("analysis.{descriptor.canonical_id}")'
        for descriptor in REGISTRY.descriptors
        if isinstance(descriptor, CallableInput) and semantic_kind in descriptor.semantic_kinds
    )


def render_semantic_object(target: object) -> str:
    """Render identity or loaded catalog facts without loading or querying."""
    from marivo.refs import Ref
    from marivo.semantic._capabilities.render import render_reference_briefing
    from marivo.semantic.catalog import CatalogEntry

    if type(target) is Ref:
        return render_reference_briefing(target)
    if not isinstance(target, CatalogEntry):
        raise RuntimeError(f"unsupported semantic object: {type(target).__name__}")

    return render_reference_briefing(
        target,
        analysis_handoff=_analysis_handoff_lines(target.ref.kind.value),
    )
