from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ResolvedMetric:
    name: str
    definition_sql: str | None = None
    dimensions: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ResolvedEntity:
    name: str
    keys: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
