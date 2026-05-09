"""Session management request models for the Marivo HTTP API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from marivo.transports.http.models.json_contract import ScalarMap


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


class SessionStateSlice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: str | None = None
    entity: str | None = None
    grain: str | None = None
    constraints: ScalarMap | None = None


class SessionStateQueryRequest(BaseModel):
    """Request body for ``POST /sessions/{session_id}/state/query`` (Phase 5b).

    All fields are optional.  Omitted fields apply no filter.
    Mirrors the ``SessionStateQuery`` canonical contract from
    ``docs/analysis/evidence-engine/schemas/state-surface-schema.md``.

    ``page_token`` is intentionally absent: it is a transport concern, not part
    of the canonical query contract.  When cursor pagination is implemented it
    will be a separate HTTP query parameter on both GET and POST endpoints.
    """

    metric: str | None = None
    entity: str | None = None
    slice: SessionStateSlice | ScalarMap | None = None
    proposition_types: list[str] | None = None
    origin_kinds: list[str] | None = None
    assessment_presence: Literal["assessed", "unassessed"] | None = None
    assessment_statuses: list[str] | None = None
    has_blocking_gaps: bool | None = None
    limit: int | None = None
