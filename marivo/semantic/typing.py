"""Core protocols and typed dicts for marivo.semantic v1.1."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from marivo.datasource.typing import AiContextValue as AiContextValue

if TYPE_CHECKING:
    import ibis

__all__ = [
    "AiContextValue",
    "IbisBackend",
]


class IbisBackend(Protocol):
    """Protocol for ibis backend objects used by entity functions."""

    def table(
        self,
        name: str,
        /,
        *,
        database: str | tuple[str, ...] | None = None,
    ) -> ibis.Table: ...
    def read_parquet(self, path: str, /, **options: object) -> ibis.Table: ...
    def read_csv(self, path: str, /, **options: object) -> ibis.Table: ...
    def sql(self, query: str, /) -> ibis.Table: ...
