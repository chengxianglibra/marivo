"""Shared bounded plain-text card formatter for agent-facing result types."""

from __future__ import annotations

_MAX_PREVIEW_COLUMNS = 8
_MAX_PREVIEW_ROWS = 5


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
