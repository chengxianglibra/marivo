from __future__ import annotations

from collections.abc import Callable
from typing import Any

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


def _require_string(value: Any, *, error_factory: Callable[[], ValueError]) -> str:
    if not isinstance(value, str):
        raise error_factory()
    return value


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
