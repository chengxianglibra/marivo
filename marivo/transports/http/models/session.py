"""Session management request models for the Marivo HTTP API."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SessionTerminateRequest(BaseModel):
    terminal_reason: str = "user_closed"


class SessionBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_scan_bytes: int = Field(default=500_000_000_000, ge=0)
    max_latency_sec: int = Field(default=120, ge=0)


class SessionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str
    budget: SessionBudget = Field(
        default_factory=SessionBudget,
        description=(
            "Hard resource limits enforced by Marivo. Steps that would exceed "
            "max_scan_bytes or max_latency_sec are blocked before execution. "
            "This is a system decision constraint, not a suggestion."
        ),
    )
