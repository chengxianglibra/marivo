"""Session management request models for the Marivo HTTP API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SessionTerminateRequest(BaseModel):
    terminal_reason: str = "user_closed"


class SessionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str
    budget: dict[str, Any] = Field(
        default_factory=lambda: {
            "max_scan_bytes": 500_000_000_000,
            "max_latency_sec": 120,
        },
        description=(
            "Hard resource limits enforced by Marivo. Steps that would exceed "
            "max_scan_bytes or max_latency_sec are blocked before execution. "
            "This is a system decision constraint, not a suggestion."
        ),
    )
    policy: dict[str, Any] = Field(
        default_factory=lambda: {
            "aggregate_only": True,
            "min_group_size": 100,
        },
        description=(
            "Governance rules enforced by Marivo (e.g. aggregate_only blocks raw row access, "
            "min_group_size enforces k-anonymity). System-enforced decision constraints — "
            "violations block step execution regardless of agent intent."
        ),
    )


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
    slice: dict[str, Any] | None = None
    proposition_types: list[str] | None = None
    origin_kinds: list[str] | None = None
    assessment_presence: Literal["assessed", "unassessed"] | None = None
    assessment_statuses: list[str] | None = None
    has_blocking_gaps: bool | None = None
    limit: int | None = None
