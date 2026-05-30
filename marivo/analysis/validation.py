"""Non-raising validation result type for pre-submit checks."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ValidationIssue(BaseModel):
    """One pre-submit incompatibility, mirroring the exception it stands in for.

    Carries the originating exception's class name and its structured ``details``
    (which already include a ``kind`` code such as ``"AlignmentPolicyNotApplicable"``)
    so the raising and non-raising validation paths cannot drift.
    """

    model_config = ConfigDict(extra="forbid")

    intent: str
    error_type: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
