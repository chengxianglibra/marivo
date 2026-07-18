"""Private bounded-page mechanics shared by public analysis page results."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from dataclasses import dataclass

from marivo.render import Card, RenderableResult


def encode_keyset_cursor(committed_at: str | int, identity: str) -> str:
    payload = json.dumps([committed_at, identity], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def decode_keyset_cursor(cursor: str) -> tuple[str | int, str]:
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(cursor + padding))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("cursor is not a valid analysis keyset cursor") from exc
    if (
        not isinstance(payload, list)
        or len(payload) != 2
        or not isinstance(payload[0], (str, int))
        or not isinstance(payload[1], str)
    ):
        raise ValueError("cursor is not a valid analysis keyset cursor")
    return payload[0], payload[1]


@dataclass(frozen=True, repr=False)
class _BoundedPage[T](RenderableResult):
    items: tuple[T, ...]
    limit: int
    has_more: bool
    next_cursor: str | None

    def __post_init__(self) -> None:
        if not 1 <= self.limit <= 100:
            raise ValueError("page limit must be within [1, 100]")
        if self.has_more != (self.next_cursor is not None):
            raise ValueError("has_more and next_cursor must describe the same page state")
        if len(self.items) > self.limit:
            raise ValueError("page items cannot exceed limit")

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self) -> Iterator[T]:
        return iter(self.items)

    def __getitem__(self, index: int) -> T:
        return self.items[index]

    def _repr_identity(self) -> str:
        return (
            f"{type(self).__name__} items={len(self.items)} "
            f"limit={self.limit} has_more={self.has_more}"
        )

    def _card(self) -> Card:
        card = Card(
            identity=self._repr_identity(),
            available=(".items", ".next_cursor", ".render()", ".show()"),
        ).listing("items", (repr(item) for item in self.items))
        if self.next_cursor is not None:
            card.field("next_cursor", self.next_cursor)
        return card


__all__ = ["_BoundedPage", "decode_keyset_cursor", "encode_keyset_cursor"]
