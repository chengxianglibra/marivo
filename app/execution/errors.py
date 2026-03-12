from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.runtime_contracts import ExecutionFeedback


@dataclass
class ExecutionFailure(ValueError):
    code: str
    category: str
    message: str
    retryable: bool = False
    replan_candidate: bool = False
    fallback_candidates: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__init__(self.message)

    def to_feedback(self) -> ExecutionFeedback:
        return ExecutionFeedback(
            code=self.code,
            category=self.category,
            message=self.message,
            retryable=self.retryable,
            replan_candidate=self.replan_candidate,
            fallback_candidates=list(self.fallback_candidates),
            detail=dict(self.detail),
        )
