from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from .context import ReadinessEvaluationContext

ObjectKind = Literal[
    "entity",
    "metric",
    "process",
    "dimension",
    "time",
    "enum",
    "binding",
    "compiler_profile",
]


def derive_lifecycle_status(status: str) -> str:
    if status == "published":
        return "active"
    if status == "draft":
        return "draft"
    if status == "deprecated":
        return "deprecated"
    raise ValueError(
        f"Unknown storage status: {status!r}. Expected one of ['deprecated', 'draft', 'published']."
    )


def derive_readiness_status(status: str) -> str:
    if status == "published":
        return "ready"
    if status in {"draft", "deprecated"}:
        return "not_ready"
    raise ValueError(
        f"Unknown storage status: {status!r}. Expected one of ['deprecated', 'draft', 'published']."
    )


@dataclass(slots=True)
class BlockingRequirementPayload:
    """A blocking requirement preventing an object from being ready.

    Each blocker has a stable code (e.g., METRIC_BINDING_MISSING),
    a human-readable message, and optional refs to the affected object
    and the dependency causing the block.
    """

    code: str
    message: str
    subject_ref: str | None = None
    dependency_ref: str | None = None
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for API response."""
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
        }
        if self.subject_ref is not None:
            payload["subject_ref"] = self.subject_ref
        if self.dependency_ref is not None:
            payload["dependency_ref"] = self.dependency_ref
        if self.details:
            payload["details"] = dict(self.details)
        return payload


@dataclass(slots=True)
class ReadinessTraceItem:
    """A trace entry for debugging readiness computation.

    Records each stage of evaluation with detail message and source
    (e.g., "metric_placeholder_evaluator"). Used for debugging and testing.
    """

    stage: str
    detail: str
    source: str
    subject_ref: str | None = None
    dependency_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for debugging output."""
        payload: dict[str, Any] = {
            "stage": self.stage,
            "detail": self.detail,
            "source": self.source,
        }
        if self.subject_ref is not None:
            payload["subject_ref"] = self.subject_ref
        if self.dependency_ref is not None:
            payload["dependency_ref"] = self.dependency_ref
        return payload


@dataclass(slots=True)
class ReadinessResult:
    """Result of readiness evaluation for a semantic object.

    Contains lifecycle_status (draft/active/deprecated), readiness_status
    (not_ready/ready/stale), blockers preventing readiness, capabilities
    the object provides, and trace for debugging.

    had_ready_predecessor tracks whether this object was previously ready,
    used for stale detection in Phase B.
    """

    lifecycle_status: str
    readiness_status: str
    blocking_requirements: list[BlockingRequirementPayload] = field(default_factory=list)
    capabilities: dict[str, Any] = field(default_factory=dict)
    trace: list[ReadinessTraceItem] = field(default_factory=list)
    had_ready_predecessor: bool = False

    def contract_payload(self) -> dict[str, Any]:
        return {
            "lifecycle_status": self.lifecycle_status,
            "readiness_status": self.readiness_status,
            "blocking_requirements": [item.to_dict() for item in self.blocking_requirements],
            "capabilities": dict(self.capabilities),
        }


class SemanticReadinessEvaluator(Protocol):
    """Protocol for semantic object readiness evaluators.

    Each evaluator computes lifecycle_status, readiness_status, blocking_requirements,
    and capabilities for a specific object kind (entity, metric, process, etc).
    """

    def evaluate(self, context: ReadinessEvaluationContext) -> ReadinessResult: ...
