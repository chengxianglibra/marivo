"""Core protocols and typed dicts for marivo.semantic v1.1."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from marivo._kernel.ai_context import AiContextValue as AiContextValue
from marivo.datasource.table_source import TableLookupBackend

if TYPE_CHECKING:
    import ibis

__all__ = [
    "AiContextValue",
    "IbisBackend",
]


class IbisBackend(TableLookupBackend, Protocol):
    """Protocol for ibis backend objects used by entity functions."""

    def read_parquet(self, path: str, /, **options: object) -> ibis.Table: ...
    def read_csv(self, path: str, /, **options: object) -> ibis.Table: ...
    def read_json(self, path: str, /, **options: object) -> ibis.Table: ...
