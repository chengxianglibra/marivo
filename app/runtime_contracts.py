from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


class JobExecutor(Protocol):
    def run_step(
        self, session_id: str, step_type: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]: ...


class PlanExecutor(Protocol):
    def execute_plan(self, plan_id: str, service: JobExecutor) -> dict[str, Any]: ...


@dataclass
class PlanValidationIssue:
    code: str
    message: str
    severity: str = "error"
    category: str = "validation"
    step_index: int | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PlanValidationResult:
    plan_id: str
    issues: list[PlanValidationIssue] = field(default_factory=list)
    cost_estimates: list[CostEstimate] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "valid": self.valid,
            "errors": [issue.message for issue in self.issues if issue.severity == "error"],
            "issues": [issue.to_dict() for issue in self.issues],
            "cost_estimates": [estimate.to_dict() for estimate in self.cost_estimates],
        }


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
class CostEstimate:
    subject: str
    estimated_rows: int | None = None
    estimated_bytes: int | None = None
    confidence: str = "unknown"
    engine_id: str | None = None
    engine_locality: str = "unknown"
    join_fanout_risk: str = "unknown"
    cache_signals: list[str] = field(default_factory=list)
    suggested_fallbacks: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BudgetCheckResult:
    plan_id: str
    total_estimated_rows: int = 0
    total_estimated_bytes: int = 0
    budget_max_rows: float | int = float("inf")
    within_budget: bool = True
    confidence: str = "unknown"
    risk_level: str = "low"
    unknown_subjects: list[str] = field(default_factory=list)
    suggested_fallbacks: list[str] = field(default_factory=list)
    cost_estimates: list[CostEstimate] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "total_estimated_cost": self.total_estimated_rows,
            "total_estimated_rows": self.total_estimated_rows,
            "total_estimated_bytes": self.total_estimated_bytes,
            "budget_max_rows": self.budget_max_rows,
            "within_budget": self.within_budget,
            "confidence": self.confidence,
            "risk_level": self.risk_level,
            "unknown_subjects": list(self.unknown_subjects),
            "suggested_fallbacks": list(self.suggested_fallbacks),
            "cost_estimates": [estimate.to_dict() for estimate in self.cost_estimates],
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


@dataclass
class ReplanTrigger:
    code: str
    source: str
    message: str
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReplanDecision:
    action: str
    reason: str
    triggers: list[ReplanTrigger] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "reason": self.reason,
            "triggers": [trigger.to_dict() for trigger in self.triggers],
            "detail": dict(self.detail),
        }
