"""Typed request models for intent reference DTOs.

HTTP intent routes use generated AOI request models directly.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ObservationRef(BaseModel):
    """Typed reference to an upstream `observe` step artifact."""

    session_id: str | None = Field(
        default=None,
        description="Session containing the upstream observe step. Defaults to the path session when omitted.",
    )
    step_id: str
    step_type: Literal["observe"]


class CorrelateObservationRef(ObservationRef):
    """Full typed artifact reference for `correlate` — adds artifact identity fields
    required by the correlate.md Reference Contract."""

    artifact_id: str | None = Field(
        default=None,
        description="Artifact ID of the upstream observe artifact (optional; resolved from step_id if omitted).",
    )
    shape: Literal["scalar", "time_series", "segmented", "panel"] = Field(
        default="time_series",
        description="Metric frame shape. Both correlate inputs must use the same shape.",
    )


class ArtifactRef(BaseModel):
    """Typed reference to any upstream intent step artifact."""

    session_id: str | None = Field(
        default=None,
        description="Session containing the upstream step. Defaults to the path session when omitted.",
    )
    step_id: str
    step_type: str
