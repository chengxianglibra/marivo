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
    from marivo.analysis._capabilities.model import OperatorCapability, SameAsInputFamily
    from marivo.analysis._capabilities.registry import REGISTRY as ANALYSIS_REGISTRY

    handoff = ANALYSIS_REGISTRY.semantic_handoff(kind)
    if handoff is None:
        return ()

    lines: list[str] = []
    for target in handoff.handoff_targets:
        if target.canonical_id is None:
            continue
        help_call = f'marivo.help("{target.surface}.{target.canonical_id}")'
        if target.surface != "analysis":
            lines.append(help_call)
            continue
        output = ""
        analysis_descriptor = ANALYSIS_REGISTRY.by_id(target.canonical_id)
        entrypoint = analysis_descriptor.public_entrypoint
        if isinstance(analysis_descriptor, OperatorCapability):
            family = analysis_descriptor.output_contract.family
            if not isinstance(family, SameAsInputFamily):
                output = f" -> {family}"
        if "(" not in entrypoint:
            entrypoint = f"{entrypoint}(...)"
        lines.append(f"{entrypoint}{output}; help: {help_call}")
    return tuple(lines)


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
