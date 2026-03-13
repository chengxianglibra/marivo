from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class CompositeStepTemplate:
    step_type: str
    params: dict[str, Any] = field(default_factory=dict)
    dependencies: list[int] = field(default_factory=list)
    execution_hints: dict[str, Any] = field(default_factory=dict)
    evidence_hints: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CompositeWorkflowSpec:
    name: str
    steps: list[CompositeStepTemplate] = field(default_factory=list)
    description: str = ""
