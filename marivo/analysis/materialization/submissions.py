"""Action-local observations of actual driver submissions, never execution authority."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ExecutionDomain = Literal["source", "local"]


@dataclass(slots=True)
class Submission:
    domain: ExecutionDomain
    role: str
    sql: str
    state: Literal["submitted", "succeeded", "failed"] = "submitted"
    error_type: str | None = None

    def fail(self, error: BaseException) -> None:
        self.state = "failed"
        self.error_type = type(error).__name__
