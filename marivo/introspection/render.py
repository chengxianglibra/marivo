"""Render canonical introspection descriptors."""

from __future__ import annotations

from typing import Any

from marivo.introspection.schema import SCHEMA_VERSION, Descriptor, FamilyFold


def format_family_block(families: tuple[FamilyFold, ...], *, help_call: str) -> list[str]:
    """Render folded families as a compact, one-line-per-family text block."""

    if not families:
        return []
    lines = ["", f"Families (call {help_call}('<name>') for any member):"]
    for fold in families:
        lines.append(f"  {fold.label} ({len(fold.members)}): {', '.join(fold.members)}")
    return lines


def render_json(descriptor: Descriptor) -> dict[str, Any]:
    """Render a descriptor as the canonical JSON-compatible shape."""

    data: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "surface": descriptor.surface,
        "kind": descriptor.kind,
        "symbol": descriptor.symbol,
        "summary": descriptor.summary,
        "constraints": [constraint.to_summary_dict() for constraint in descriptor.constraints],
        "examples": list(descriptor.examples),
        "see_also": list(descriptor.see_also),
    }
    if descriptor.doc:
        data["doc"] = descriptor.doc
    if descriptor.signature is not None and descriptor.kind != "frame":
        data["signature"] = descriptor.signature
    if descriptor.methods:
        data["methods"] = [
            {"name": method.name, "summary": method.summary} for method in descriptor.methods
        ]
    if descriptor.fields:
        data["fields"] = [
            {
                "name": f.name,
                "annotation": f.annotation,
                "required": f.required,
                **({"default": f.default} if f.default is not None else {}),
                **({"description": f.description} if f.description is not None else {}),
            }
            for f in descriptor.fields
        ]
    if descriptor.kind == "frame":
        data["affordance_note"] = (
            "Use artifact.contract().affordances for mechanical compatibility. Affordances are not ranked and are not recommendations."
        )
        if descriptor.constructed_by is not None:
            data["constructed_by"] = descriptor.constructed_by
    if descriptor.kind == "surface":
        data["entries"] = [
            {
                "name": entry.name,
                "kind": entry.kind,
                "summary": entry.summary,
            }
            for entry in descriptor.entries
        ]
        if descriptor.families:
            data["families"] = [
                {"label": fold.label, "members": list(fold.members)} for fold in descriptor.families
            ]
    if descriptor.kind == "topic":
        data["content"] = descriptor.content
    if descriptor.kind == "unknown":
        data["did_you_mean"] = list(descriptor.did_you_mean)
    return data


def render_text(descriptor: Descriptor) -> str:
    """Render a compact text view for humans and agents."""

    symbol = descriptor.symbol if descriptor.symbol is not None else "help()"
    lines = [f"{descriptor.surface}: {symbol}", descriptor.summary]
    if descriptor.signature is not None and descriptor.kind != "frame":
        lines.append(f"Signature: {descriptor.signature}")
    if descriptor.doc:
        lines.append("")
        lines.append(descriptor.doc)
    if descriptor.entries:
        lines.append("")
        lines.append("Entries:")
        for entry in descriptor.entries:
            lines.append(f"- {entry.name} ({entry.kind}): {entry.summary}")
    lines.extend(format_family_block(descriptor.families, help_call=f"{descriptor.surface}.help"))
    if descriptor.methods:
        lines.append("")
        lines.append("Methods:")
        for method in descriptor.methods:
            lines.append(f"- {method.name}: {method.summary}")
    if descriptor.fields:
        lines.append("")
        lines.append("Fields:")
        for f in descriptor.fields:
            tag = "required" if f.required else "optional"
            parts = [f.name, f"[{f.annotation}]", tag]
            if f.default is not None:
                parts.append(f"default={f.default}")
            if f.description:
                parts.append(f"-- {f.description}")
            lines.append(f"- {' '.join(parts)}")
    if descriptor.constructed_by is not None:
        lines.append(f"Constructed by: {descriptor.constructed_by}")
    if descriptor.kind == "frame":
        lines.append("")
        lines.append(
            "Use artifact.contract().affordances for mechanical compatibility. Affordances are not ranked and are not recommendations."
        )
    if descriptor.constraints:
        lines.append("")
        lines.append("Constraints:")
        for constraint in descriptor.constraints:
            lines.append(f"- {constraint.id}: {constraint.title}")
    if descriptor.examples:
        lines.append("")
        lines.append("Examples:")
        for example in descriptor.examples:
            lines.append(f"- {example}")
    if descriptor.see_also:
        lines.append("")
        lines.append("See also:")
        for item in descriptor.see_also:
            lines.append(f"- {item}")
    if descriptor.did_you_mean:
        lines.append("")
        lines.append(f"Did you mean: {', '.join(descriptor.did_you_mean)}")
    return "\n".join(lines)
