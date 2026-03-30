from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


class JobExecutor(Protocol):
    def run_step(
        self, session_id: str, step_type: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]: ...


@dataclass
class PolicyDecision:
    code: str
    message: str
    decision: str
    effect: str
    policy_id: str | None = None
    policy_name: str | None = None
    policy_type: str | None = None
    scope: dict[str, Any] = field(default_factory=dict)
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_violation(self) -> dict[str, str]:
        payload = {"message": self.message}
        if self.policy_id is not None:
            payload["policy_id"] = self.policy_id
        if self.policy_name is not None:
            payload["name"] = self.policy_name
        return payload


@dataclass
class PolicyApplicationResult:
    decisions: list[PolicyDecision] = field(default_factory=list)
    transforms: dict[str, Any] = field(default_factory=dict)

    @property
    def violations(self) -> list[dict[str, str]]:
        return [
            decision.to_violation() for decision in self.decisions if decision.effect == "block"
        ]

    @property
    def warnings(self) -> list[dict[str, str]]:
        return [decision.to_violation() for decision in self.decisions if decision.effect == "warn"]

    @property
    def hard_constraints(self) -> list[dict[str, Any]]:
        return [decision.to_dict() for decision in self.decisions if decision.effect == "block"]

    @property
    def soft_signals(self) -> list[dict[str, Any]]:
        return [decision.to_dict() for decision in self.decisions if decision.effect != "block"]

    @property
    def passed(self) -> bool:
        return not self.violations

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "violations": self.violations,
            "warnings": self.warnings,
            "decisions": [decision.to_dict() for decision in self.decisions],
            "transforms": dict(self.transforms),
            "hard_constraints": self.hard_constraints,
            "soft_signals": self.soft_signals,
        }


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



