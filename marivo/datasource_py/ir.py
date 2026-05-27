"""Intermediate representation for project-level datasources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = [
    "DatasourceAiContextIR",
    "DatasourceIR",
    "DatasourceSourceLocation",
]


@dataclass(frozen=True)
class DatasourceSourceLocation:
    """Absolute source location for datasource error reporting."""

    file: str
    line: int


@dataclass(frozen=True)
class DatasourceAiContextIR:
    """Immutable AI-facing context stored on datasource declarations."""

    business_definition: str | None = None
    guardrails: tuple[str, ...] = ()
    synonyms: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()
    instructions: str | None = None
    owner_notes: str | None = None


@dataclass(frozen=True)
class DatasourceIR:
    """Project-level datasource configuration."""

    semantic_id: str
    name: str
    backend_type: str
    fields: dict[str, Any]
    env_refs: dict[str, str]
    description: str | None
    ai_context: DatasourceAiContextIR
    python_symbol: str
    location: DatasourceSourceLocation
