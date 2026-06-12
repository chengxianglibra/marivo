"""Render canonical introspection descriptors and bounded plain-text cards."""

from __future__ import annotations

from typing import Any

from marivo.introspection.schema import SCHEMA_VERSION, Descriptor

# --- bounded card formatting constants ---

_MAX_PREVIEW_COLUMNS = 8
_MAX_PREVIEW_ROWS = 5


# --- bounded card formatting ---


def format_bounded_card(
    *,
    identity: str,
    status: str | None = None,
    columns: list[str] | None = None,
    rows: list[list[str]] | None = None,
    row_count: int | None = None,
    preview_truncation_hint: str | None = None,
    available: tuple[str, ...],
) -> str:
    """Return a bounded plain-text result card without a trailing newline.

    Args:
        identity: First line; identifies result type, ref/id, scale/status.
        status: Optional status segment appended as ``status: <value>``.
        columns: Column names for tabular results (capped at 8).
        rows: Preview row values as pre-serialized strings (capped at 5 rows).
        row_count: Total row count used to compute the truncation message.
        preview_truncation_hint: Suffix for the truncation line when rows are
            truncated (e.g. ``"call .preview(limit=...) or .to_pandas()"``)
        available: Tuple of method strings listed in the ``available:``
            section (e.g. ``(".summary()", ".render()")``)

    Returns:
        Bounded plain-text card without a trailing newline.

    Constraints:
        Columns are capped at 8. Preview rows are capped at 5. When rows is
        provided and row_count exceeds the shown row count, a truncation line
        is appended using preview_truncation_hint.
    """
    lines: list[str] = [identity]

    if status is not None:
        lines.append(f"status: {status}")

    if columns is not None:
        visible = columns[:_MAX_PREVIEW_COLUMNS]
        lines.append(f"columns: {' | '.join(visible)}")

    if rows is not None:
        lines.append("preview:")
        shown_rows = rows[:_MAX_PREVIEW_ROWS]
        for row in shown_rows:
            lines.append(" | ".join(row))
        if row_count is not None and row_count > len(shown_rows):
            remaining = row_count - len(shown_rows)
            row_word = "row" if remaining == 1 else "rows"
            hint = preview_truncation_hint or "call .preview(limit=...) for more"
            lines.append(f"... {remaining} more {row_word}; {hint}")
        elif row_count is None and len(rows) > len(shown_rows):
            remaining = len(rows) - len(shown_rows)
            row_word = "row" if remaining == 1 else "rows"
            hint = preview_truncation_hint or "call .preview(limit=...) for more"
            lines.append(f"... {remaining} more {row_word}; {hint}")

    lines.append("available:")
    for entry in available:
        lines.append(f"- {entry}")

    return "\n".join(lines)


# --- descriptor rendering ---


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
        if descriptor.next_intents:
            data["next_intents"] = list(descriptor.next_intents)
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
    if descriptor.next_intents:
        lines.append("")
        lines.append(f"Next intents: {', '.join(descriptor.next_intents)}")
    if descriptor.constructed_by is not None:
        lines.append(f"Constructed by: {descriptor.constructed_by}")
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
