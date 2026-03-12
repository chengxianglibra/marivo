from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(slots=True)
class AnalysisRequest:
    """Normalized request context for analysis execution."""

    goal: str = ""
    session_id: str | None = None
    constraints: dict[str, Any] = field(default_factory=dict)
    budget: dict[str, Any] = field(default_factory=dict)
    policy: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AnalysisStepIR:
    """Minimal, execution-oriented representation of a typed analysis step."""

    index: int
    step_type: str
    params: dict[str, Any] = field(default_factory=dict)
    dependencies: list[int] = field(default_factory=list)


@dataclass(slots=True)
class ExecutionPlanIR:
    """Shared step container for planning and execution paths."""

    steps: list[AnalysisStepIR] = field(default_factory=list)


def from_legacy_step(index: int, step: Mapping[str, Any]) -> AnalysisStepIR:
    """Build IR from the current plan/step payload structure."""

    return AnalysisStepIR(
        index=index,
        step_type=str(step["step_type"]),
        params=dict(step.get("params", {})),
        dependencies=list(step.get("dependencies", [])),
    )
