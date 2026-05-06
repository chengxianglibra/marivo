from __future__ import annotations

from typing import Protocol

from app.contracts.ids import ModelId, RevisionId, UserId
from app.contracts.semantic import ModelSummary, SemanticModel


class ModelSelector(Protocol):
    model_id: ModelId | None
    name: str | None
    revision: RevisionId | None


class ModelListQuery(Protocol):
    owner: UserId | None
    visibility: str | None
    include_public: bool
    include_private: bool


class ModelStore(Protocol):
    def get(self, selector: ModelSelector) -> SemanticModel | None: ...
    def save(
        self,
        model: SemanticModel,
        *,
        actor: UserId,
        expected_revision: RevisionId | None,
    ) -> ModelId: ...
    def list(self, query: ModelListQuery) -> list[ModelSummary]: ...
