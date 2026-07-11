"""Shared bounded plain-text card formatter for agent-facing result types."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

_DEFAULT_MAX_OUTPUT_BYTES = 8192
_OMISSION_RECOVERY = "pass max_output_bytes=None for full output"


@runtime_checkable
class AgentResult(Protocol):
    """Structural protocol every terminal result type satisfies.

    A terminal result is an object an agent stops to read or ``print()``s.
    Conformance is verified by the contract test, not by inheritance.
    """

    def render(self, *, max_output_bytes: int | None = _DEFAULT_MAX_OUTPUT_BYTES) -> str: ...

    def show(self, *, max_output_bytes: int | None = _DEFAULT_MAX_OUTPUT_BYTES) -> None: ...

    def __repr__(self) -> str: ...


@dataclass(frozen=True)
class TableSection:
    """Tabular section rendered as columns plus preview rows."""

    label: str
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...] | None
    rows_provider: Callable[[], Iterable[Sequence[str]]] | None
    row_count: int | None = None
    show_omission_counts: bool = False


@dataclass(frozen=True)
class ListSection:
    """Bullet-list section."""

    label: str
    items: tuple[str, ...]


@dataclass(frozen=True)
class FieldSection:
    """Single ``label: value`` section."""

    label: str
    value: str


Section = TableSection | ListSection | FieldSection


@dataclass(frozen=True)
class _Line:
    text: str
    section_index: int
    is_table_row: bool = False


def result_repr(identity: str) -> str:
    """Return the single-line bounded repr for a terminal result.

    Args:
        identity: The type-and-id identity line.

    Returns:
        A single-line string of the form
        ``"<{identity}; call .show() to inspect>"``.

    Example:
        >>> result_repr("MetricFrame ref=frame_ab12 rows=7")
        '<MetricFrame ref=frame_ab12 rows=7; call .show() to inspect>'

    Constraints:
        identity must not contain a newline.
    """
    return f"<{identity}; call .show() to inspect>"


class RenderableResult:
    """Mixin for terminal results backed by a ``Card``."""

    def _card(self) -> Card:
        raise NotImplementedError

    def _repr_identity(self) -> str:
        raise NotImplementedError

    def render(self, *, max_output_bytes: int | None = _DEFAULT_MAX_OUTPUT_BYTES) -> str:
        return self._card().render(max_output_bytes=max_output_bytes)

    def show(self, *, max_output_bytes: int | None = _DEFAULT_MAX_OUTPUT_BYTES) -> None:
        print(self.render(max_output_bytes=max_output_bytes))

    def __repr__(self) -> str:
        return result_repr(self._repr_identity())


class Card:
    """Builder for bounded plain-text terminal result cards."""

    def __init__(self, *, identity: str, available: Sequence[str]) -> None:
        self._identity = identity
        self._status: str | None = None
        self._sections: list[Section] = []
        self._available = tuple(available)

    def status(self, value: str) -> Card:
        self._status = value
        return self

    def table(
        self,
        columns: Sequence[str],
        rows: Iterable[Sequence[str]],
        *,
        row_count: int | None = None,
        label: str = "preview",
        show_omission_counts: bool = False,
    ) -> Card:
        materialized_rows = tuple(tuple(str(value) for value in row) for row in rows)
        self._sections.append(
            TableSection(
                label=label,
                columns=tuple(str(column) for column in columns),
                rows=materialized_rows,
                rows_provider=None,
                row_count=row_count,
                show_omission_counts=show_omission_counts,
            )
        )
        return self

    def lazy_table(
        self,
        columns: Sequence[str],
        rows_provider: Callable[[], Iterable[Sequence[str]]],
        row_count: int,
        *,
        label: str = "preview",
    ) -> Card:
        self._sections.append(
            TableSection(
                label=label,
                columns=tuple(str(column) for column in columns),
                rows=None,
                rows_provider=rows_provider,
                row_count=row_count,
                show_omission_counts=False,
            )
        )
        return self

    def listing(self, label: str, items: Iterable[str]) -> Card:
        self._sections.append(ListSection(label=label, items=tuple(str(item) for item in items)))
        return self

    def field(self, label: str, value: str) -> Card:
        self._sections.append(FieldSection(label=label, value=str(value)))
        return self

    def section(self, section: Section) -> Card:
        self._sections.append(section)
        return self

    def render(self, *, max_output_bytes: int | None = _DEFAULT_MAX_OUTPUT_BYTES) -> str:
        if max_output_bytes is None:
            return _join_lines(
                self._head_lines() + list(self._section_lines_full()) + self._tail_lines()
            )
        return self._render_bounded(max_output_bytes)

    def _head_lines(self) -> list[str]:
        lines = [self._identity]
        if self._status is not None:
            lines.append(f"status: {self._status}")
        return lines

    def _tail_lines(self) -> list[str]:
        lines = ["available:"]
        lines.extend(f"- {entry}" for entry in self._available)
        return lines

    def _section_lines_full(self) -> Iterator[str]:
        for section in self._sections:
            yield from _section_text_lines(section)

    def _section_line_items(self) -> Iterator[_Line]:
        for index, section in enumerate(self._sections):
            for text, is_table_row in _section_text_line_items(section):
                yield _Line(text=text, section_index=index, is_table_row=is_table_row)

    def _render_bounded(self, max_output_bytes: int) -> str:
        head = self._head_lines()
        tail = self._tail_lines()
        if not self._sections:
            rendered = _join_lines([*head, *tail])
            if _encoded_len(rendered) <= max_output_bytes:
                return rendered
            minimum = _encoded_len(rendered)
            raise ValueError(
                "max_output_bytes is too small to preserve identity and available output; "
                f"minimum is {minimum} bytes; {_OMISSION_RECOVERY}"
            )

        body: list[_Line] = []
        truncated_at: int | None = None
        current_line: _Line | None = None

        for line in self._section_line_items():
            current_line = line
            candidate = [*body, line]
            candidate_text = _join_lines([*head, *_line_texts(candidate), *tail])
            if _encoded_len(candidate_text) > max_output_bytes:
                truncated_at = line.section_index
                break
            body.append(line)
        else:
            return _join_lines([*head, *_line_texts(body), *tail])

        min_marker = _truncation_marker(max_output_bytes=max_output_bytes, tokens=())
        minimum = _encoded_len(_join_lines([*head, min_marker, *tail]))
        if max_output_bytes < minimum:
            raise ValueError(
                "max_output_bytes is too small to preserve identity, truncation marker, "
                f"and available output; minimum is {minimum} bytes; {_OMISSION_RECOVERY}"
            )

        while True:
            body_text = _line_texts(body)
            omitted_tokens = self._omission_tokens(
                truncated_at=truncated_at,
                current_line=current_line,
                rows_shown_by_section=_rows_shown_by_section(body),
            )
            marker = _best_fit_marker(
                max_output_bytes=max_output_bytes,
                head=head,
                body=body_text,
                tail=tail,
                omitted_tokens=omitted_tokens,
                require_all_tokens=any(
                    isinstance(section, TableSection) and section.show_omission_counts
                    for section in self._sections[truncated_at:]
                ),
            )
            rendered = _join_lines([*head, *body_text, marker, *tail])
            if _encoded_len(rendered) <= max_output_bytes:
                return rendered
            if not body:
                minimum = _encoded_len(_join_lines([*head, marker, *tail]))
                raise ValueError(
                    "max_output_bytes is too small to preserve identity, truncation detail, "
                    f"and available output; minimum is {minimum} bytes; {_OMISSION_RECOVERY}"
                )
            removed = body.pop()
            truncated_at = min(truncated_at, removed.section_index)

    def _omission_tokens(
        self,
        *,
        truncated_at: int,
        current_line: _Line | None,
        rows_shown_by_section: dict[int, int],
    ) -> tuple[str, ...]:
        tokens: list[str] = []
        for index in range(truncated_at, len(self._sections)):
            token = _section_omission_token(
                self._sections[index],
                rows_shown=rows_shown_by_section.get(index, 0),
                current_line_is_table_row=current_line is not None
                and current_line.section_index == index
                and current_line.is_table_row,
            )
            if token is not None:
                tokens.append(token)
        return tuple(tokens)


def format_bounded_card(
    *,
    identity: str,
    status: str | None = None,
    columns: Sequence[str] | None = None,
    rows: Iterable[Sequence[str]] | None = None,
    row_count: int | None = None,
    available: Sequence[str],
    max_output_bytes: int | None = _DEFAULT_MAX_OUTPUT_BYTES,
) -> str:
    """Return a bounded plain-text result card without a trailing newline."""
    card = Card(identity=identity, available=available)
    if status is not None:
        card.status(status)
    if columns is not None or rows is not None:
        card.table(columns or (), rows or (), row_count=row_count)
    return card.render(max_output_bytes=max_output_bytes)


def _section_text_lines(section: Section) -> Iterator[str]:
    for text, _is_table_row in _section_text_line_items(section):
        yield text


def _line_texts(lines: Sequence[_Line]) -> list[str]:
    return [line.text for line in lines]


def _rows_shown_by_section(lines: Sequence[_Line]) -> dict[int, int]:
    rows_shown: dict[int, int] = {}
    for line in lines:
        if line.is_table_row:
            rows_shown[line.section_index] = rows_shown.get(line.section_index, 0) + 1
    return rows_shown


def _section_text_line_items(section: Section) -> Iterator[tuple[str, bool]]:
    if isinstance(section, TableSection):
        yield f"columns: {' | '.join(section.columns)}", False
        iterator = _table_rows(section)
        first = next(iterator, None)
        if first is None:
            yield f"{section.label}: none", False
            return
        yield f"{section.label}:", False
        yield _format_row(first), True
        for row in iterator:
            yield _format_row(row), True
        return
    if isinstance(section, ListSection):
        if not section.items:
            yield f"{section.label}: none", False
            return
        yield f"{section.label}:", False
        for item in section.items:
            yield f"- {item}", False
        return
    yield f"{section.label}: {section.value}", False


def _table_rows(section: TableSection) -> Iterator[tuple[str, ...]]:
    if section.rows is not None:
        yield from section.rows
        return
    if section.rows_provider is None:
        return
    for row in section.rows_provider():
        yield tuple(str(value) for value in row)


def _format_row(row: Sequence[str]) -> str:
    return " | ".join(str(value) for value in row)


def _section_omission_token(
    section: Section,
    *,
    rows_shown: int,
    current_line_is_table_row: bool,
) -> str | None:
    if isinstance(section, TableSection):
        omitted_rows = _omitted_table_rows(section, rows_shown, current_line_is_table_row)
        if omitted_rows is None:
            return f"{section.label} rows"
        if not section.show_omission_counts:
            row_word = "row" if omitted_rows == 1 else "rows"
            return f"{section.label} ({omitted_rows} {row_word})"
        total_rows = rows_shown + omitted_rows
        return f"{section.label} (displayed={rows_shown} total={total_rows} omitted={omitted_rows})"
    if isinstance(section, ListSection):
        return section.label
    if isinstance(section, FieldSection):
        return section.label
    return None


def _omitted_table_rows(
    section: TableSection,
    rows_shown: int,
    current_line_is_table_row: bool,
) -> int | None:
    if section.row_count is not None:
        omitted = section.row_count - rows_shown
        return max(omitted, 0)
    if section.rows is not None:
        omitted = len(section.rows) - rows_shown
        if current_line_is_table_row:
            omitted = max(omitted, 1)
        return max(omitted, 0)
    return None


def _truncation_marker(*, max_output_bytes: int, tokens: Sequence[str]) -> str:
    omitted = ", ".join(tokens) if tokens else "output"
    return f"output truncated at {max_output_bytes} bytes; omitted: {omitted}; {_OMISSION_RECOVERY}"


def _best_fit_marker(
    *,
    max_output_bytes: int,
    head: Sequence[str],
    body: Sequence[str],
    tail: Sequence[str],
    omitted_tokens: Sequence[str],
    require_all_tokens: bool,
) -> str:
    if require_all_tokens:
        return _truncation_marker(max_output_bytes=max_output_bytes, tokens=omitted_tokens)
    accepted_tokens: list[str] = []
    for token in omitted_tokens:
        candidate_tokens = [*accepted_tokens, token]
        marker = _truncation_marker(max_output_bytes=max_output_bytes, tokens=candidate_tokens)
        if _encoded_len(_join_lines([*head, *body, marker, *tail])) <= max_output_bytes:
            accepted_tokens.append(token)
            continue
        break
    marker = _truncation_marker(max_output_bytes=max_output_bytes, tokens=accepted_tokens)
    if _encoded_len(_join_lines([*head, *body, marker, *tail])) <= max_output_bytes:
        return marker
    return _truncation_marker(max_output_bytes=max_output_bytes, tokens=())


def _join_lines(lines: Sequence[str]) -> str:
    return "\n".join(lines)


def _encoded_len(value: str) -> int:
    return len(value.encode())
