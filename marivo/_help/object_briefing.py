"""No-I/O semantic object briefings for unified help."""

from __future__ import annotations

from marivo.introspection.live.model import SURFACE_LIMITS
from marivo.introspection.live.render import enforce_budget


def _bounded(text: str) -> str:
    return enforce_budget(
        text,
        max_lines=SURFACE_LIMITS.focused_help_max_lines,
        max_codepoints=SURFACE_LIMITS.focused_help_max_codepoints,
    )


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
    from marivo.semantic.catalog import CatalogEntry

    if type(target) is Ref:
        ref = target
        return _bounded(
            "\n".join(
                (
                    f"{ref.kind.value}: {ref.path}",
                    "  Object: Ref",
                    "  Authority: typed identity only; project membership and readiness are unknown.",
                    "",
                    "  Inspect in an explicitly loaded project:",
                    "    import marivo.semantic as ms",
                    "    catalog = ms.load()",
                    "    entry = catalog.require(ref)",
                    "    entry.details().show()",
                    "    catalog.readiness(refs=[entry]).show()",
                    "",
                    '  Capability help: marivo.help("semantic.Ref")',
                )
            )
        )
    if not isinstance(target, CatalogEntry):
        raise RuntimeError(f"unsupported semantic object: {type(target).__name__}")

    ref = target.ref
    details_text = target.details().render()
    lines = [
        f"{ref.kind.value}: {ref.path}",
        f"  Object: {type(target).__name__}",
        "  Authority: current compiled catalog entry.",
        "",
        "  Details:",
        *(f"    {line}" for line in details_text.splitlines()),
    ]
    handoff_lines = _analysis_handoff_lines(ref.kind.value)
    if handoff_lines:
        lines.extend(
            (
                "",
                "  Analysis handoff (kind-level; readiness and companion inputs still apply):",
                *(f"    {line}" for line in handoff_lines),
                "  After an analysis operator returns an artifact:",
                "    result.contract().show()",
            )
        )
    lines.extend(
        (
            "",
            "  Readiness is not inferred here; use catalog.readiness(refs=[entry]).",
            "  No datasource connectivity or inspection evidence was queried.",
        )
    )
    return _bounded("\n".join(lines))
