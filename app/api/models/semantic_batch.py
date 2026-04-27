from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class SemanticBatchItem(BaseModel):
    op_key: str = Field(description="Caller-provided stable key for this batch operation.")
    kind: Literal["time", "dimension", "entity", "metric", "binding"] = Field(
        description="Semantic object kind handled by this operation."
    )
    action: Literal["create", "validate", "activate", "publish"] = Field(
        description="Operation to run. publish is treated as an activate alias in batch v1."
    )
    payload: dict[str, Any] = Field(default_factory=dict, description="Operation payload.")


class SemanticBatchDefaults(BaseModel):
    carrier_bindings: dict[str, dict[str, Any]] = Field(default_factory=dict)
    time_bindings: dict[str, dict[str, Any]] = Field(default_factory=dict)


class SemanticBatchRequest(BaseModel):
    mode: Literal["dry_run", "apply"] = "dry_run"
    lifecycle: Literal["create_only", "create_and_validate", "create_validate_activate"] = (
        "create_only"
    )
    continue_on_error: bool = True
    defaults: SemanticBatchDefaults | None = None
    items: list[SemanticBatchItem] = Field(default_factory=list)


class SemanticBatchItemResponse(BaseModel):
    op_key: str
    kind: str
    action: str
    status: Literal["succeeded", "failed", "skipped"]
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    guidance: dict[str, Any] | None = None
    coverage: dict[str, Any] | None = None


class SemanticBatchResponse(BaseModel):
    ok: bool
    mode: Literal["dry_run", "apply"]
    summary: dict[str, int]
    items: list[SemanticBatchItemResponse]
