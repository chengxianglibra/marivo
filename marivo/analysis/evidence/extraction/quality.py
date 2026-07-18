"""Extract exact predicate findings from a QualityReport."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
import json
from datetime import datetime
from typing import Any

import pandas as pd

from marivo.analysis.evidence.identity import make_finding_id
from marivo.analysis.evidence.types import (
    AnalysisScope,
    DerivationRule,
    Finding,
    JsonScalar,
    QualityCheckFindingValue,
    Subject,
)


def _predicate(
    check_kind: str, details: dict[str, Any]
) -> tuple[JsonScalar, str, dict[str, JsonScalar], bool]:
    if check_kind == "row_count":
        row_count = int(details.get("row_count", 0))
        blocking_count = int(details.get("threshold_blocking", 0))
        return (
            row_count,
            "row_count_above_blocking_threshold",
            {"threshold": blocking_count},
            row_count > blocking_count,
        )
    if check_kind == "null_ratio":
        null_ratio = float(details.get("null_ratio", 0.0))
        blocking_ratio = float(details.get("threshold_blocking", 0.5))
        return (
            null_ratio,
            "null_ratio_at_or_below_blocking_threshold",
            {"threshold": blocking_ratio},
            null_ratio <= blocking_ratio,
        )
    if check_kind == "time_coverage":
        coverage_ratio = float(details.get("coverage_ratio", 0.0))
        blocking_coverage = 0.8
        return (
            coverage_ratio,
            "time_coverage_at_or_above_blocking_threshold",
            {"threshold": blocking_coverage},
            coverage_ratio >= blocking_coverage,
        )
    if check_kind == "duplicate_keys":
        duplicate_count = int(details.get("duplicate_count", 0))
        return (
            duplicate_count,
            "duplicate_key_count_equals_zero",
            {"expected": 0},
            duplicate_count == 0,
        )
    raise ValueError(f"unsupported quality check kind: {check_kind}")


def extract_quality_check_findings(
    *,
    df: pd.DataFrame,
    artifact_id: str,
    session_id: str,
    subject: Subject,
    committed_at: datetime,
    evaluated_scope: AnalysisScope,
    source_refs: tuple[str, ...],
) -> list[Finding]:
    """Extract one typed finding for every executed quality predicate."""
    findings: list[Finding] = []
    for _, row in df.sort_values("check_id", kind="stable").iterrows():
        check_id = str(row["check_id"])
        check_kind = str(row["check_kind"])
        details = json.loads(str(row["details_json"]))
        measured, predicate, parameters, passed = _predicate(check_kind, details)
        findings.append(
            Finding(
                finding_id=make_finding_id(artifact_id, "quality_check", check_id),
                finding_type="quality_check",
                epistemic_kind="tested",
                artifact_id=artifact_id,
                session_id=session_id,
                subject=subject,
                canonical_item_key=check_id,
                value=QualityCheckFindingValue(
                    check_id=check_id,
                    measured_value=measured,
                    expectation_predicate=predicate,
                    expectation_parameters=parameters,
                    expectation_condition_passed=passed,
                    evaluated_scope=evaluated_scope,
                    source_refs=source_refs,
                ),
                derivation=DerivationRule(
                    rule_id="extract.quality_check",
                    rule_version="v2",
                    operator="assess_quality",
                    source_fields=("check_id", "check_kind", "details_json"),
                    source_finding_refs=(),
                ),
                source_refs=source_refs,
                committed_at=committed_at,
            )
        )
    return findings


__all__ = ["extract_quality_check_findings"]
