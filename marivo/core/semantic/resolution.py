"""Resolved semantic objects — pure data classes for runtime resolution results."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ResolvedSemanticObject:
    object_kind: str
    object_id: str
    ref: str
    semantic_object: dict[str, Any]
    status: str
    revision: int
    created_at: str
    updated_at: str


@dataclass(slots=True)
class RuntimeSemanticAvailability:
    resolved: ResolvedSemanticObject
    lifecycle_status: str
    readiness_status: str
    blocking_requirements: list[dict[str, Any]] = field(default_factory=list)
    capabilities: dict[str, Any] = field(default_factory=dict)
    dependency_refs: list[str] = field(default_factory=list)

    @property
    def is_active(self) -> bool:
        return self.lifecycle_status == "active"

    @property
    def is_ready(self) -> bool:
        return self.readiness_status == "ready"
