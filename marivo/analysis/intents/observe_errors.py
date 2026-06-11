"""Structured repair errors for Phase 1 observe planning."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, NoReturn

from marivo.analysis.errors import MetricShapeUnsupportedError

ObserveErrorCode = Literal[
    "missing-additivity",
    "missing-root",
    "invalid-root",
    "empty-base-entities",
    "root-only-measure-violation",
    "field-ref-not-found",
    "field-ref-ambiguous",
    "field-expr-type-error",
    "non-root-time-dimension",
    "path-missing",
    "path-ambiguous",
    "unsafe-fanout",
    "unknown-join-safety",
    "cross-datasource-plan",
    "snapshot-metadata-invalid",
    "snapshot-partition-missing",
    "unsupported-as-of-root-time",
    "derived-shared-planner-unsupported",
    "component-axis-unreachable",
    "component-axis-field-mismatch",
    "component-filter-unreachable",
    "component-filter-field-mismatch",
    "component-version-mismatch",
    "nested-derived-unsupported",
    "sampled-grain-floor-unsupported-unit",
    "grain-finer-than-sampled-floor",
    "fold-time-dimension-unresolved",
    "fold-time-dimension-mismatch",
    "fold-time-dimension-missing-metadata",
    "fold-time-dimension-unsupported-type",
]


class RepairSafety(StrEnum):
    AUTO_SAFE = "auto_safe"
    MODELING_DECISION = "modeling_decision"
    UNSAFE_WITHOUT_APPROVAL = "unsafe_without_approval"


@dataclass(frozen=True)
class RepairAction:
    action: str
    target: str
    arg: str | None
    value: Any
    safety: RepairSafety
    why: str

    def model_dump(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "target": self.target,
            "arg": self.arg,
            "value": self.value,
            "safety": self.safety.value,
            "why": self.why,
        }


class ObservePlanningError(MetricShapeUnsupportedError):
    """Machine-readable observe planner rejection."""


def raise_observe_planning_error(
    *,
    code: ObserveErrorCode,
    message: str,
    candidates: dict[str, Any] | None = None,
    repair: list[RepairAction] | None = None,
) -> NoReturn:
    raise ObservePlanningError(
        message=message,
        details={
            "schema_version": "observe-error/v1",
            "code": code,
            "candidates": candidates or {},
            "repair": [action.model_dump() for action in repair or []],
        },
    )
