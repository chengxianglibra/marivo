"""Whole-row, budgeted rendering for retained business data only."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from inspect import getdoc, signature
from itertools import islice

from marivo.render import _DEFAULT_MAX_OUTPUT_BYTES, RenderableResult, _format_row, _join_lines


def _validate_display(n: int | None, max_output_bytes: int | None) -> None:
    for name, value, minimum in (("n", n, 0), ("max_output_bytes", max_output_bytes, 1)):
        if value is not None and (type(value) is not int or value < minimum):
            raise ValueError(f"{name} must be None or an integer >= {minimum}; received {value!r}")


def _text(value: object) -> str:
    # Escape controls and separators without losing values or creating fake rows.
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
        .replace("|", "\\|")
    )


@dataclass(frozen=True)
class _DataCard:
    identity: str
    columns: Sequence[str]
    rows: Callable[[], Iterable[Sequence[object]]]
    row_count: int
    row_scope: str = "retained result"
    facts: tuple[tuple[str, str], ...] = ()
    boundaries: tuple[tuple[str, str], ...] = ()
    continuations: tuple[str, ...] = ()

    def render(self, *, n: int | None, max_output_bytes: int | None) -> str:
        _validate_display(n, max_output_bytes)
        requested = self.row_count if n is None else min(n, self.row_count)
        fixed_head = [f"{_text(k)}: {_text(v)}" for k, v in self.facts]
        fixed_head += [f"columns: {_format_row(tuple(_text(c) for c in self.columns))}", "data:"]
        fixed_tail = [f"{_text(k)}: {_text(v)}" for k, v in self.boundaries]
        continuations = [f"read: {_text(call)}" for call in self.continuations]

        def head(shown: int) -> list[str]:
            return [
                _text(self.identity),
                f"Rows: {self.row_count} total; {shown} shown; "
                f"{self.row_count - shown} omitted; scope={_text(self.row_scope)}",
                *fixed_head,
            ]

        def tail(shown: int) -> list[str]:
            if shown == self.row_count:
                return [*fixed_tail, *continuations]
            reasons = []
            arguments = []
            if requested < self.row_count:
                reasons.append("row_limit")
                arguments.append("n=None")
            if shown < requested:
                reasons.append("output_budget")
                arguments.append("max_output_bytes=None")
            elif max_output_bytes != _DEFAULT_MAX_OUTPUT_BYTES:
                arguments.append(f"max_output_bytes={max_output_bytes}")
            return [
                *fixed_tail,
                f"Omitted: {self.row_count - shown} rows; reason={'+'.join(reasons)}",
                f"read: .show({', '.join(arguments)})",
                *continuations,
            ]

        lines: list[str] = []
        line_bytes = 0
        # Budget the full mandatory metadata first. Omission text is added below;
        # delaying it lets an exactly fitting complete result avoid a false cut.
        for row in islice(self.rows(), requested):
            line = _format_row(tuple(_text(cell) for cell in row))
            size = len(line.encode("utf-8")) + 1
            minimum = (
                len(
                    _join_lines([*head(len(lines) + 1), *fixed_tail, *continuations]).encode(
                        "utf-8"
                    )
                )
                + 1
            )
            if max_output_bytes is not None and minimum + line_bytes + size > max_output_bytes:
                break
            lines.append(line)
            line_bytes += size

        while True:
            rendered = _join_lines([*head(len(lines)), *lines, *tail(len(lines))])
            size = len(rendered.encode("utf-8")) + 1
            if max_output_bytes is None or size <= max_output_bytes:
                return rendered
            if not lines:
                raise ValueError(
                    "max_output_bytes is too small to preserve identity, meaning, columns, "
                    f"and omission detail; minimum is {size} bytes; "
                    "pass max_output_bytes=None for full output"
                )
            lines.pop()


class _DataResult(RenderableResult):
    """Private result mixin; non-data Card users keep their existing protocol."""

    def _data_card(self) -> _DataCard:
        raise NotImplementedError

    def render(
        self, *, n: int | None = None, max_output_bytes: int | None = _DEFAULT_MAX_OUTPUT_BYTES
    ) -> str:
        """Render retained rows and their interpretation within an output budget.

        Args:
            n: Maximum displayed rows; None means all and zero means metadata only.
            max_output_bytes: UTF-8 budget including show's newline; None removes the budget.
        Returns: Text without a trailing newline, with exact display omission counts.
        Example: ``text = result.render(n=20)``.
        Constraints: Does not fetch more source rows. Query limits remain independent.
        """
        _validate_display(n, max_output_bytes)
        return self._data_card().render(n=n, max_output_bytes=max_output_bytes)

    def show(
        self, *, n: int | None = None, max_output_bytes: int | None = _DEFAULT_MAX_OUTPUT_BYTES
    ) -> None:
        """Print retained rows, meaning, and explicit display omissions.

        Args:
            n: Maximum displayed rows; None means all and zero means metadata only.
            max_output_bytes: UTF-8 budget including the newline; None removes the budget.
        Returns: None; prints the current result without querying the source again.
        Example: ``result.show(n=20)``.
        Constraints: Row and byte limits both apply; source limits and coverage are unchanged.
        """
        print(self.render(n=n, max_output_bytes=max_output_bytes))


def _display_help() -> tuple[str, ...]:
    """Project the owning callable's signature and documentation into native Help."""
    installed = signature(_DataResult.show)
    installed = installed.replace(parameters=tuple(installed.parameters.values())[1:])
    return (
        f"Display signature: .show{installed}",
        *(line.strip() for line in (getdoc(_DataResult.show) or "").splitlines() if line.strip()),
    )
