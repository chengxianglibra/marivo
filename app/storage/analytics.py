from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class AnalyticsEngine(ABC):
    """Pluggable backend for analytical query execution."""

    @abstractmethod
    def initialize(self) -> None: ...

    @abstractmethod
    def query_rows(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]: ...

    @abstractmethod
    def table_exists(self, table_name: str) -> bool: ...

    @abstractmethod
    def table_row_count(self, table_name: str) -> int: ...
