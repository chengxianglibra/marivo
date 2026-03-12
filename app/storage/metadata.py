from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import Any, Iterator


class MetadataStore(ABC):
    """Pluggable backend for OmniDB control-plane tables (sessions, steps,
    artifacts, observations, claims, edges, recommendations, sources,
    semantic objects, etc.)."""

    @abstractmethod
    def initialize(self) -> None: ...

    @abstractmethod
    @contextmanager
    def connect(self) -> Iterator[Any]: ...

    @abstractmethod
    def execute(self, sql: str, params: list[Any] | None = None) -> None: ...

    @abstractmethod
    def execute_many(self, sql: str, rows: list[tuple]) -> None: ...

    @abstractmethod
    def query_rows(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]: ...

    @abstractmethod
    def query_one(self, sql: str, params: list[Any] | None = None) -> dict[str, Any] | None: ...
