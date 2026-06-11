"""DiscoveryResult[T] — list-like typed container for semantic discovery results."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from typing import Generic, TypeVar

from marivo.render import format_bounded_card
from marivo.semantic.errors import ErrorKind, SemanticError

T = TypeVar("T")

_PREVIEW_DEFAULT_LIMIT = 10
_PREVIEW_MAX_LIMIT = 100


class SelectionError(SemanticError):
    """Raised by require_one() when the discovery result has 0 or >1 items."""


@dataclasses.dataclass(frozen=True)
class DiscoveryResultSummary:
    """Typed summary returned by DiscoveryResult.summary()."""

    item_type: str
    item_count: int
    columns: tuple[str, ...]


class DiscoveryResult(Generic[T]):  # noqa: UP046
    """List-like typed container for semantic discovery results.

    Args:
        items: The discovered items.
        item_type_name: Human-readable type name for display (e.g. "MetricSummary").
        has_ids: Whether items expose a ``semantic_id`` attribute.
            When True, ``.ids()`` is advertised in the ``available:`` section.

    Example:
        >>> metrics = DiscoveryResult(metric_items, item_type_name="MetricSummary")
        >>> metrics
        <DiscoveryResult[MetricSummary] items=12; call .show() to inspect>
        >>> metrics.show()
        DiscoveryResult[MetricSummary] items=12
        ...
        >>> metrics.ids()
        ['sales.revenue', 'sales.orders', ...]
        >>> metrics.require_one()  # raises if 0 or >1
    """

    def __init__(
        self,
        items: list[T],
        item_type_name: str,
        *,
        has_ids: bool = True,
    ) -> None:
        self._items: list[T] = list(items)
        self._item_type_name = item_type_name
        self._has_ids = has_ids

    # --- list-like protocol ---

    @property
    def items(self) -> list[T]:
        """Return a copy of the full typed item list."""
        return list(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[T]:
        return iter(self._items)

    def __getitem__(self, index: int) -> T:
        return self._items[index]

    # --- agent helpers ---

    def ids(self) -> list[str]:
        """Return the semantic_id of each item.

        Returns:
            List of semantic_id strings from each item.

        Raises:
            SelectionError: When items do not expose ``semantic_id``.
                This occurs when the DiscoveryResult was created with
                ``has_ids=False``.

        Constraints:
            Only advertised in render() when has_ids=True.
        """
        if not self._has_ids:
            raise SelectionError(
                kind=ErrorKind.NOT_FOUND,
                message="items do not expose semantic_id; this DiscoveryResult was created with has_ids=False",
                details={"has_ids": False},
            )
        return [item.semantic_id for item in self._items]  # type: ignore[attr-defined]

    def first(self) -> T | None:
        """Return the first item, or None when the result is empty.

        Returns:
            The first item, or None if no items exist.
        """
        return self._items[0] if self._items else None

    def require_one(self) -> T:
        """Return the item when exactly one result matches.

        Returns:
            The single item when exactly one result exists.

        Raises:
            SelectionError: When there are zero items ("no results matched …")
                or more than one item (message includes the actual count).
        """
        if len(self._items) == 0:
            raise SelectionError(
                kind=ErrorKind.NOT_FOUND,
                message="no results matched; inspect with .show() or broaden the query",
                details={"count": 0},
            )
        if len(self._items) > 1:
            raise SelectionError(
                kind=ErrorKind.AMBIGUOUS_REFERENCE,
                message=(
                    f"{len(self._items)} results found; "
                    "narrow the query, use .first(), or iterate .items"
                ),
                details={"count": len(self._items)},
            )
        return self._items[0]

    # --- inspection surface ---

    def summary(self) -> DiscoveryResultSummary:
        """Return a typed summary of this discovery result.

        Returns:
            DiscoveryResultSummary with item_type, item_count, and columns.
        """
        columns: tuple[str, ...] = ()
        if self._items:
            first = self._items[0]
            if dataclasses.is_dataclass(first) and not isinstance(first, type):
                columns = tuple(f.name for f in dataclasses.fields(first))
        return DiscoveryResultSummary(
            item_type=self._item_type_name,
            item_count=len(self._items),
            columns=columns,
        )

    def preview(self, limit: int = _PREVIEW_DEFAULT_LIMIT) -> list[T]:
        """Return a bounded sub-list of items without writing stdout.

        Args:
            limit: Maximum items to return (1–100, default 10).

        Returns:
            Bounded list of at most ``limit`` items.

        Raises:
            ValueError: When limit is outside 1–100.
        """
        if limit < 1 or limit > _PREVIEW_MAX_LIMIT:
            raise ValueError(f"preview limit must be between 1 and 100, got {limit!r}")
        return self._items[:limit]

    @property
    def _available_entries(self) -> tuple[str, ...]:
        if self._has_ids:
            return (".ids()", ".first()", ".require_one()", ".preview(limit=...)", ".render()")
        return (".first()", ".require_one()", ".preview(limit=...)", ".render()")

    def _render_preview_rows(self) -> tuple[list[list[str]], list[str]]:
        """Return (rows_as_strings, column_names) for the first 5 items."""
        if not self._items:
            return [], []
        first = self._items[0]
        if dataclasses.is_dataclass(first) and not isinstance(first, type):
            col_names = [f.name for f in dataclasses.fields(first)]
        else:
            col_names = []
        rows: list[list[str]] = []
        for item in self._items[:5]:
            if col_names:
                row = [str(getattr(item, col, "")) for col in col_names]
            else:
                rows.append([str(item)])
                continue
            rows.append(row)
        return rows, col_names

    def render(self) -> str:
        """Return bounded plain-text result card without a trailing newline.

        Returns:
            Bounded plain text suitable for terminal/agent inspection.
        """
        rows, col_names = self._render_preview_rows()
        return format_bounded_card(
            identity=f"DiscoveryResult[{self._item_type_name}] items={len(self._items)}",
            columns=col_names if col_names else None,
            rows=rows if rows else None,
            row_count=len(self._items),
            preview_truncation_hint="call .preview(limit=...) or iterate .items",
            available=self._available_entries,
        )

    def show(self) -> None:
        """Print render() output followed by a trailing newline and return None."""
        print(self.render())

    def __repr__(self) -> str:
        return (
            f"<DiscoveryResult[{self._item_type_name}] items={len(self._items)}; "
            "call .show() to inspect>"
        )
