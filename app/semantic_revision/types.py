from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Compatibility = Literal["compatible", "breaking"]


@dataclass(frozen=True)
class RevisionDiffEntry:
    path: str
    change_type: str
    compatibility: Compatibility
    reason: str

    def to_json(self) -> dict[str, object]:
        return {
            "path": self.path,
            "change_type": self.change_type,
            "compatibility": self.compatibility,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class RequiredAction:
    action_id: str
    action: str
    target_ref: str
    target_revision: int | None
    depends_on: list[str]
    blocking: bool
    action_status: Literal["pending", "satisfied", "failed"]
    completion_criteria: dict[str, object]
    validation_evidence: dict[str, object] | None = None
    reason: str = ""

    def to_json(self) -> dict[str, object]:
        return {
            "action_id": self.action_id,
            "action": self.action,
            "target_ref": self.target_ref,
            "target_revision": self.target_revision,
            "depends_on": self.depends_on,
            "blocking": self.blocking,
            "action_status": self.action_status,
            "completion_criteria": self.completion_criteria,
            "validation_evidence": self.validation_evidence,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class RevisionClassificationResult:
    classified_compatibility: Compatibility
    diff_summary: list[RevisionDiffEntry] = field(default_factory=list)
    affected_dependents: list[dict[str, object]] = field(default_factory=list)
    required_actions: list[RequiredAction] = field(default_factory=list)

    @property
    def can_activate_now(self) -> bool:
        return not any(
            action.blocking and action.action_status != "satisfied"
            for action in self.required_actions
        )
