from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypedDict

_VALID_COMPARISON_BASES: frozenset[str] = frozenset({"yoy", "mom", "wow"})
_BASELINE_RULE_FIELDS: tuple[str, ...] = (
    "strategy",
    "offset_value",
    "offset_unit",
    "fixed_start",
    "fixed_end",
    "named_window_ref",
)
_BUCKET_PAIRING_FIELDS: tuple[str, ...] = (
    "current_bucket_start",
    "baseline_bucket_start",
    "pairing_reason",
    "shift_days",
    "issues",
)
_COVERAGE_SUMMARY_FIELDS: tuple[str, ...] = (
    "aligned_bucket_count",
    "unpaired_bucket_count",
    "aligned_ratio",
)


class _CalendarAlignmentIssuePolicy(TypedDict):
    gate_family: str
    severity: str
    blocking: bool
    message_template: str


_CALENDAR_ALIGNMENT_ISSUE_POLICIES: dict[str, _CalendarAlignmentIssuePolicy] = {
    "calendar_alignment_metadata_mismatch": {
        "gate_family": "comparability_gate",
        "severity": "error",
        "blocking": True,
        "message_template": (
            "calendar alignment metadata must be present on both observations when either side "
            "freezes a resolved policy summary"
        ),
    },
    "calendar_policy_mismatch": {
        "gate_family": "comparability_gate",
        "severity": "error",
        "blocking": True,
        "message_template": "",
    },
    "calendar_comparison_basis_mismatch": {
        "gate_family": "comparability_gate",
        "severity": "error",
        "blocking": True,
        "message_template": "",
    },
    "calendar_source_mismatch": {
        "gate_family": "comparability_gate",
        "severity": "error",
        "blocking": True,
        "message_template": "",
    },
    "calendar_version_mismatch": {
        "gate_family": "comparability_gate",
        "severity": "error",
        "blocking": True,
        "message_template": "",
    },
    "holiday_cluster_unmapped": {
        "gate_family": "comparability_gate",
        "severity": "warning",
        "blocking": False,
        "message_template": "upstream observation froze holiday alignment mapping gaps",
    },
    "event_cluster_unmapped": {
        "gate_family": "comparability_gate",
        "severity": "warning",
        "blocking": False,
        "message_template": "upstream observation froze event alignment mapping gaps",
    },
    "fallback_applied": {
        "gate_family": "comparability_gate",
        "severity": "warning",
        "blocking": False,
        "message_template": "upstream observation froze a calendar alignment fallback path",
    },
    "alignment_coverage_insufficient": {
        "gate_family": "comparability_gate",
        "severity": "warning",
        "blocking": False,
        "message_template": "upstream observation froze incomplete calendar bucket alignment coverage",
    },
    "weekday_pairing_tie": {
        "gate_family": "comparability_gate",
        "severity": "error",
        "blocking": True,
        "message_template": "upstream observation froze unresolved calendar weekday pairing ambiguity",
    },
}


def normalize_resolved_policy_summary(
    value: Any,
    *,
    error_factory: Callable[[], ValueError],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise error_factory()

    policy_ref = _require_string(value.get("policy_ref"), error_factory=error_factory)
    comparison_basis = _require_string(value.get("comparison_basis"), error_factory=error_factory)
    if comparison_basis not in _VALID_COMPARISON_BASES:
        raise error_factory()
    comparability_warnings = value.get("comparability_warnings")
    if not isinstance(comparability_warnings, list) or not all(
        isinstance(warning, str) for warning in comparability_warnings
    ):
        raise error_factory()

    return {
        "policy_ref": policy_ref,
        "comparison_basis": comparison_basis,
        "resolved_calendar_source": _require_string(
            value.get("resolved_calendar_source"),
            error_factory=error_factory,
        ),
        "resolved_calendar_version": _require_string(
            value.get("resolved_calendar_version"),
            error_factory=error_factory,
        ),
        "resolved_baseline_generation_rule": _normalize_baseline_rule(
            value.get("resolved_baseline_generation_rule"),
            error_factory=error_factory,
        ),
        "current_window": _normalize_window(
            value.get("current_window"),
            error_factory=error_factory,
        ),
        "baseline_window": _normalize_window(
            value.get("baseline_window"),
            error_factory=error_factory,
        ),
        "bucket_pairing": _normalize_bucket_pairing(
            value.get("bucket_pairing"),
            error_factory=error_factory,
        ),
        "coverage_summary": _normalize_coverage_summary(
            value.get("coverage_summary"),
            error_factory=error_factory,
        ),
        "comparability_warnings": list(comparability_warnings),
    }


def resolve_calendar_alignment_reuse(
    *,
    left_resolved_policy_summary: Any,
    right_resolved_policy_summary: Any,
    error_factory: Callable[[], ValueError],
) -> dict[str, Any]:
    left_summary = _normalize_optional_resolved_policy_summary(
        left_resolved_policy_summary,
        error_factory=error_factory,
    )
    right_summary = _normalize_optional_resolved_policy_summary(
        right_resolved_policy_summary,
        error_factory=error_factory,
    )
    if left_summary is None and right_summary is None:
        return {"issues": [], "fatal_message": None, "reuse_summary": None}
    if left_summary is None or right_summary is None:
        issue = _build_issue("calendar_alignment_metadata_mismatch")
        return {
            "issues": [issue],
            "fatal_message": issue["message"],
            "reuse_summary": None,
        }

    mismatch = _calendar_alignment_mismatch(left_summary=left_summary, right_summary=right_summary)
    if mismatch is not None:
        return {
            "issues": [mismatch],
            "fatal_message": str(mismatch["message"]),
            "reuse_summary": None,
        }

    issues: list[dict[str, Any]] = []
    fatal_message: str | None = None
    warnings = sorted(
        {
            *left_summary["comparability_warnings"],
            *right_summary["comparability_warnings"],
        }
    )
    for warning_code in warnings:
        issue = _build_issue(warning_code)
        issues.append(issue)
        if issue["blocking"] and fatal_message is None:
            fatal_message = issue["message"]

    min_aligned_ratio = min(
        left_summary["coverage_summary"]["aligned_ratio"],
        right_summary["coverage_summary"]["aligned_ratio"],
    )
    max_unpaired_bucket_count = max(
        left_summary["coverage_summary"]["unpaired_bucket_count"],
        right_summary["coverage_summary"]["unpaired_bucket_count"],
    )
    if min_aligned_ratio < 1.0 or max_unpaired_bucket_count > 0:
        issues.append(
            _build_issue(
                "alignment_coverage_insufficient",
                details={
                    "left_coverage_summary": left_summary["coverage_summary"],
                    "right_coverage_summary": right_summary["coverage_summary"],
                },
            )
        )

    return {
        "issues": issues,
        "fatal_message": fatal_message,
        "reuse_summary": {
            "reuse_source": "observation_resolved_policy_summary",
            "policy_ref": left_summary["policy_ref"],
            "comparison_basis": left_summary["comparison_basis"],
            "resolved_calendar_source": left_summary["resolved_calendar_source"],
            "resolved_calendar_version": left_summary["resolved_calendar_version"],
            "comparability_warnings": warnings,
            "left_coverage_summary": left_summary["coverage_summary"],
            "right_coverage_summary": right_summary["coverage_summary"],
            "effective_coverage_summary": {
                "aligned_bucket_count": min(
                    left_summary["coverage_summary"]["aligned_bucket_count"],
                    right_summary["coverage_summary"]["aligned_bucket_count"],
                ),
                "unpaired_bucket_count": max_unpaired_bucket_count,
                "aligned_ratio": min_aligned_ratio,
            },
        },
    }


def resolve_calendar_alignment_reuse_for_intent(
    *,
    intent_name: str,
    left_resolved_policy_summary: Any,
    right_resolved_policy_summary: Any,
) -> dict[str, Any]:
    return resolve_calendar_alignment_reuse(
        left_resolved_policy_summary=left_resolved_policy_summary,
        right_resolved_policy_summary=right_resolved_policy_summary,
        error_factory=lambda: ValueError(
            f"{intent_name}: INVALID_ARGUMENT - malformed resolved calendar alignment metadata"
        ),
    )


def _require_string(value: Any, *, error_factory: Callable[[], ValueError]) -> str:
    if not isinstance(value, str):
        raise error_factory()
    return value


def _normalize_optional_resolved_policy_summary(
    value: Any,
    *,
    error_factory: Callable[[], ValueError],
) -> dict[str, Any] | None:
    if value is None:
        return None
    return normalize_resolved_policy_summary(value, error_factory=error_factory)


def _calendar_alignment_mismatch(
    *,
    left_summary: dict[str, Any],
    right_summary: dict[str, Any],
) -> dict[str, Any] | None:
    mismatch_fields = (
        ("policy_ref", "calendar_policy_mismatch"),
        ("comparison_basis", "calendar_comparison_basis_mismatch"),
        ("resolved_calendar_source", "calendar_source_mismatch"),
        ("resolved_calendar_version", "calendar_version_mismatch"),
    )
    for field_name, code in mismatch_fields:
        left_value = left_summary[field_name]
        right_value = right_summary[field_name]
        if left_value != right_value:
            return _build_issue(
                code,
                message=f"left {field_name} '{left_value}' != right {field_name} '{right_value}'",
            )
    return None


def _build_issue(
    code: str,
    *,
    message: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy = _CALENDAR_ALIGNMENT_ISSUE_POLICIES.get(code)
    if policy is None:
        policy = {
            "gate_family": "comparability_gate",
            "severity": "warning",
            "blocking": False,
            "message_template": f"upstream observation froze calendar alignment warning '{code}'",
        }

    issue: dict[str, Any] = {
        "code": code,
        "severity": policy["severity"],
        "message": message or policy["message_template"],
        "gate_family": policy["gate_family"],
        "blocking": policy["blocking"],
    }
    if details is not None:
        issue["details"] = details
    return issue


def _normalize_window(value: Any, *, error_factory: Callable[[], ValueError]) -> dict[str, str]:
    if not isinstance(value, dict):
        raise error_factory()
    return {
        "start": _require_string(value.get("start"), error_factory=error_factory),
        "end": _require_string(value.get("end"), error_factory=error_factory),
    }


def _normalize_baseline_rule(
    value: Any,
    *,
    error_factory: Callable[[], ValueError],
) -> dict[str, Any]:
    if not isinstance(value, dict) or any(field not in value for field in _BASELINE_RULE_FIELDS):
        raise error_factory()
    return {field: value[field] for field in _BASELINE_RULE_FIELDS}


def _normalize_bucket_pairing(
    value: Any,
    *,
    error_factory: Callable[[], ValueError],
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise error_factory()
    pairings: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict) or any(field not in item for field in _BUCKET_PAIRING_FIELDS):
            raise error_factory()
        current_bucket_start = item["current_bucket_start"]
        baseline_bucket_start = item["baseline_bucket_start"]
        pairing_reason = item["pairing_reason"]
        shift_days = item["shift_days"]
        issues = item["issues"]
        if not isinstance(current_bucket_start, str):
            raise error_factory()
        if baseline_bucket_start is not None and not isinstance(baseline_bucket_start, str):
            raise error_factory()
        if not isinstance(pairing_reason, str):
            raise error_factory()
        if shift_days is not None and not isinstance(shift_days, int):
            raise error_factory()
        if not isinstance(issues, list) or not all(isinstance(issue, str) for issue in issues):
            raise error_factory()
        pairings.append({field: item[field] for field in _BUCKET_PAIRING_FIELDS})
    return pairings


def _normalize_coverage_summary(
    value: Any,
    *,
    error_factory: Callable[[], ValueError],
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(_COVERAGE_SUMMARY_FIELDS):
        raise error_factory()

    aligned_bucket_count = value["aligned_bucket_count"]
    unpaired_bucket_count = value["unpaired_bucket_count"]
    aligned_ratio = value["aligned_ratio"]
    if not isinstance(aligned_bucket_count, int):
        raise error_factory()
    if not isinstance(unpaired_bucket_count, int):
        raise error_factory()
    if not isinstance(aligned_ratio, int | float):
        raise error_factory()
    if aligned_bucket_count < 0 or unpaired_bucket_count < 0:
        raise error_factory()

    aligned_ratio_float = float(aligned_ratio)
    if not 0.0 <= aligned_ratio_float <= 1.0:
        raise error_factory()

    total_bucket_count = aligned_bucket_count + unpaired_bucket_count
    if total_bucket_count == 0:
        if aligned_ratio_float != 0.0:
            raise error_factory()
    else:
        expected_ratio = aligned_bucket_count / total_bucket_count
        if abs(aligned_ratio_float - expected_ratio) > 1e-9:
            raise error_factory()

    return {
        "aligned_bucket_count": aligned_bucket_count,
        "unpaired_bucket_count": unpaired_bucket_count,
        "aligned_ratio": aligned_ratio_float,
    }
