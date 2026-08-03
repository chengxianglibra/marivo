"""Bounded ontology help rendering."""

from __future__ import annotations

from marivo.introspection.live.model import SURFACE_LIMITS
from marivo.introspection.live.render import enforce_budget
from marivo.introspection.live.resolve import ResolvedLiveTarget
from marivo.ontology._capabilities.registry import REGISTRY, OntologyDescriptor


def _bounded(text: str, *, root: bool = False) -> str:
    return enforce_budget(
        text,
        max_lines=SURFACE_LIMITS.root_help_max_lines
        if root
        else SURFACE_LIMITS.focused_help_max_lines,
        max_codepoints=(
            SURFACE_LIMITS.root_help_max_codepoints
            if root
            else SURFACE_LIMITS.focused_help_max_codepoints
        ),
    )


def render_root_help() -> str:
    lines = [
        "marivo.ontology",
        "  Python imports:",
        "    import marivo",
        "    import marivo.ontology as mo",
        "    import marivo.semantic as ms",
        "",
        "  Optional knowledge context over exact semantic refs:",
    ]
    lines.extend(
        f"    {descriptor.canonical_id}: {descriptor.summary}"
        for descriptor in (REGISTRY.by_canonical_id(value) for value in REGISTRY.canonical_ids())
    )
    lines.extend(
        (
            "",
            "  Ontology is not executable semantic authority and is never required for ordinary analysis.",
            '  Call marivo.help("ontology.<target>") for one exact contract.',
        )
    )
    return _bounded("\n".join(lines), root=True)


def render_help_target(
    resolved: ResolvedLiveTarget[OntologyDescriptor], *, original_target: object
) -> str:
    del original_target
    if resolved.kind == "descriptor":
        descriptor = resolved.descriptor
        assert descriptor is not None
        lines = [
            f"ontology.{descriptor.canonical_id}",
            "  Python imports:",
            "    import marivo",
            "    import marivo.ontology as mo",
            "    import marivo.semantic as ms",
            "",
            f"  {descriptor.summary}",
        ]
        lines.extend(f"  {line}" for line in descriptor.body)
        return _bounded("\n".join(lines))
    if resolved.kind == "type_contract":
        return _bounded(
            f"ontology.{resolved.type_name}\n"
            "  Public immutable ontology value. Use repr() for identity and marivo.help() for its owner."
        )
    if resolved.kind in {"error_contract", "error_briefing"}:
        error = resolved.original if resolved.original is not None else resolved.error_name
        return _bounded(f"ontology error {resolved.error_name}\n  {error}")
    raise AssertionError(f"unsupported ontology help resolution {resolved.kind!r}")


__all__ = ["render_help_target", "render_root_help"]
