from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from .ids import ModelId, RevisionId, UserId


class SemanticModel(BaseModel):
    """Domain-level semantic model, aligned with OSI but not coupled to HTTP shapes."""

    model_id: ModelId | None = None
    name: str
    revision: RevisionId | None = None
    description: str | None = None
    osi_document: dict[str, Any] = {}
    visibility: str = "private"
    owner: UserId | None = None


class ModelSummary(BaseModel):
    model_id: ModelId
    name: str
    revision: RevisionId | None = None
    description: str | None = None
    visibility: str = "private"
    owner: UserId | None = None
    updated_at: str | None = None
