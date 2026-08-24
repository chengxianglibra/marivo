"""Structured repair errors for Phase 1 observe planning."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, NoReturn

from marivo._compat import StrEnum
from marivo.analysis.errors import (
    AnalysisRepair,
    MetricShapeUnsupportedError,
    _DerivedFields,
)
from marivo.introspection.live.model import LiveHelpTarget

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
    "metric-graph-metric-missing",
    "metric-graph-physical-leaf-missing",
    "metric-graph-source-domain-mismatch",
    "metric-graph-slice-not-leaf",
    "metric-graph-slice-conflict",
    "runtime-metric-target-kind",
    "runtime-metric-measure-missing",
    "runtime-weighted-mean-measure-missing",
    "runtime-weighted-mean-grain-mismatch",
    "runtime-weighted-mean-weight-non-additive",
    "sampled-grain-floor-unsupported-unit",
    "grain-finer-than-sampled-floor",
    "status-time-dimension-unresolved",
    "status-time-dimension-mismatch",
    "status-time-dimension-missing-metadata",
    "status-time-dimension-unsupported-type",
    "unsampled-time-fold-unsupported",
    "snapshot-fold-identity-missing",
    "snapshot-fold-deadlock",
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

    def _derive_fields(self) -> _DerivedFields:
        code = self._context.get("code")
        if code not in {"component-axis-unreachable", "component-filter-unreachable"}:
            return _DerivedFields()

        candidates = self._context.get("candidates")
        if not isinstance(candidates, Mapping):
            return _DerivedFields()
        is_filter = code == "component-filter-unreachable"
        scope_name = "filter" if is_filter else "dimension"
        scope_key = "filter_key" if is_filter else "dimension"
        requested = candidates.get(scope_key)
        missing = candidates.get("missing_components")
        if not isinstance(requested, str) or not isinstance(missing, list):
            return _DerivedFields()

        missing_ids = tuple(str(item) for item in missing)
        missing_preview = missing_ids[:10]
        raw_causes = candidates.get("failure_causes")
        cause_items = raw_causes if isinstance(raw_causes, list) else []
        failure_codes = {
            cause.get("code")
            for cause in cause_items
            if isinstance(cause, Mapping) and isinstance(cause.get("code"), str)
        }
        if failure_codes & {"field-ref-ambiguous", "path-ambiguous"}:
            action = (
                "Disambiguate the existing field or relationship paths for each listed "
                "metric leaf; author a new relationship only for a leaf explicitly marked "
                "path-missing, reload the semantic catalog, then retry the same "
                "session.observe call."
            )
        elif "field-ref-not-found" in failure_codes:
            action = (
                f"Correct or author the requested field {requested!r} for every listed "
                "metric leaf, add governed relationships only where the retained cause is "
                "path-missing, reload the semantic catalog, then retry the same "
                "session.observe call."
            )
        else:
            action = (
                f"Author a governed relationship path that makes {requested!r} reachable "
                "from every listed metric leaf, reload the semantic catalog, then retry "
                "the same session.observe call."
            )
        received = f"{requested!r} is unreachable from metric leaves {list(missing_preview)!r}"
        if len(missing_ids) > len(missing_preview):
            received += f" (+{len(missing_ids) - len(missing_preview)} more)"
        return _DerivedFields(
            expected=(
                f"observation {scope_name} {requested!r} reachable from every metric leaf "
                "through governed semantic relationships"
            ),
            received=received,
            location="session.observe slice_by" if is_filter else "session.observe dimensions",
            repair=AnalysisRepair(
                kind="semantic_authoring",
                action=action,
                help_target=LiveHelpTarget(surface="analysis", canonical_id="observe"),
                candidates=missing_preview,
            ),
        )


def raise_observe_planning_error(
    *,
    code: ObserveErrorCode,
    message: str,
    candidates: dict[str, Any] | None = None,
    repair: list[RepairAction] | None = None,
) -> NoReturn:
    raise ObservePlanningError(
        message=message,
        context={
            "schema_version": "observe-error/v1",
            "code": code,
            "candidates": candidates or {},
            "repair": [action.model_dump() for action in repair or []],
        },
    )
