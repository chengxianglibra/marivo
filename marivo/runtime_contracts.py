from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ExecutionFeedback:
    code: str
    category: str
    message: str
    retryable: bool = False
    replan_candidate: bool = False
    fallback_candidates: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
