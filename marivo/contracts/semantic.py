from __future__ import annotations

from pydantic import BaseModel

from marivo.contracts.generated import SemanticModel as OSISemanticModel

from .ids import ModelId, UserId


class SemanticModel(BaseModel):
    """Domain-level semantic model, aligned with OSI but not coupled to HTTP shapes."""

    model_id: ModelId | None = None
    name: str
    description: str | None = None
    osi_model: OSISemanticModel | None = None
    visibility: str = "private"
    owner: UserId | None = None

    @property
    def osi_document(self) -> dict[str, object]:
        if self.osi_model is None:
            return {}
        return self.osi_model.model_dump(by_alias=True, exclude_none=True)


class ModelSummary(BaseModel):
    model_id: ModelId
    name: str
    description: str | None = None
    visibility: str = "private"
    owner: UserId | None = None
    updated_at: str | None = None
