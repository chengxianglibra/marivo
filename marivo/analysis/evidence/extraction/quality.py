"""Extract exact predicate findings from a QualityReport."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
import json
from datetime import datetime
from typing import Any

import pandas as pd

from marivo.analysis.evidence.identity import make_finding_id, make_scope_fingerprint
from marivo.analysis.evidence.types import (
    AnalysisScope,
    DerivationRule,
    EvidenceScope,
    EvidenceSubject,
    Finding,
    JsonScalar,
    QualityCheckFindingValue,
    Subject,
)
from marivo.semantic.metric_graph import (
    CatalogMetricIdentity,
    CatalogMetricSubjectV1,
    RuntimeExpressionIdentity,
    RuntimeExpressionSubjectV1,
)


def _metric_id(identity: CatalogMetricIdentity | RuntimeExpressionIdentity) -> str:
    if isinstance(identity, CatalogMetricIdentity):
        return identity.metric_ref.path
    return f"runtime:{identity.expression_fingerprint}"


def _metric_context(
    *,
    metric_id: str | None,
    subject: EvidenceSubject,
    scope: EvidenceScope,
    artifact_id: str,
    session_id: str,
) -> tuple[EvidenceSubject, EvidenceScope]:
    if metric_id is None:
        return subject, scope
    if not isinstance(subject, Subject) or not isinstance(scope, AnalysisScope):
        raise TypeError("metric-specific quality checks require a metric subject and scope")
    matches = tuple(
        identity for identity in scope.metric_identities if _metric_id(identity) == metric_id
    )
    if len(matches) != 1:
        raise ValueError(
            f"quality check metric_id {metric_id!r} must match exactly one assessed MetricIdentity"
        )
    identity = matches[0]
    metric_scope = scope.model_copy(update={"metric_identities": (identity,)})
    scope_fingerprint = make_scope_fingerprint(metric_scope)
    typed_subject: CatalogMetricSubjectV1 | RuntimeExpressionSubjectV1
    if isinstance(identity, CatalogMetricIdentity):
        typed_subject = CatalogMetricSubjectV1(
            kind="catalog_metric",
            session_id=session_id,
            metric_ref=identity.metric_ref,
            artifact_id=artifact_id,
            scope_fingerprint=scope_fingerprint,
        )
    else:
        typed_subject = RuntimeExpressionSubjectV1(
            kind="runtime_expression",
            session_id=session_id,
            expression_fingerprint=identity.expression_fingerprint,
            artifact_id=artifact_id,
            scope_fingerprint=scope_fingerprint,
        )
    return subject.model_copy(update={"typed_metric_subject": typed_subject}), metric_scope


def _predicate(
    check_kind: str, details: dict[str, Any]
) -> tuple[JsonScalar, str, dict[str, JsonScalar], bool]:
    if check_kind == "row_count":
        row_count = int(details.get("row_count", 0))
        row_warning_threshold = int(details.get("threshold_warning", 1))
        return (
            row_count,
            "row_count_at_or_above_warning_threshold",
            {"threshold": row_warning_threshold},
            row_count >= row_warning_threshold,
        )
    if check_kind == "null_ratio":
        null_ratio = float(details.get("null_ratio", 0.0))
        warning_ratio = float(details.get("threshold_warning", 0.1))
        return (
            null_ratio,
            "null_ratio_at_or_below_warning_threshold",
            {"threshold": warning_ratio},
            null_ratio <= warning_ratio,
        )
    if check_kind == "time_coverage":
        coverage_ratio = float(details.get("coverage_ratio", 0.0))
        return (
            coverage_ratio,
            "time_coverage_complete_within_data_extent",
            {"threshold": 1.0},
            coverage_ratio == 1.0,
        )
    if check_kind == "value_density":
        value_density = float(details.get("value_density", 0.0))
        warning_threshold = float(details.get("threshold_warning", 0.1))
        return (
            value_density,
            "value_density_at_or_above_warning_threshold",
            {"threshold": warning_threshold},
            value_density >= warning_threshold,
        )
    if check_kind == "duplicate_keys":
        duplicate_count = int(details.get("duplicate_count", 0))
        return (
            duplicate_count,
            "duplicate_key_count_equals_zero",
            {"expected": 0},
            duplicate_count == 0,
        )
    if check_kind == "delta_row_contract":
        invalid_count = int(details.get("invalid_count", 0))
        return (
            invalid_count,
            "invalid_count_equals_zero",
            {"expected": 0},
            invalid_count == 0,
        )
    if check_kind == "cumulative_pairing":
        caveat_count = int(details.get("caveat_count", 0))
        return (
            caveat_count,
            "cumulative_pairing_caveat_count_equals_zero",
            {"expected": 0},
            caveat_count == 0,
        )
    invalid_count_checks = {
        "metric_row_contract",
        "delta_math",
        "attribution_row_contract",
        "attribution_contribution_values",
        "attribution_reconciliation",
        "event_funnel_row_contract",
        "event_funnel_math",
        "event_funnel_axes",
        "event_funnel_reconciliation",
        "event_time_to_event_row_contract",
        "event_time_to_event_identity",
        "event_time_to_event_duration",
        "event_time_to_event_axes",
        "funnel_delta_alignment",
        "funnel_delta_components",
        "funnel_delta_coverage",
        "funnel_delta_row_contract",
        "funnel_attribution_components",
        "funnel_attribution_pools",
        "funnel_attribution_residual",
        "funnel_attribution_reconciliation",
    }
    if check_kind in invalid_count_checks:
        count = int(details.get("invalid_count", 0))
        return (
            count,
            "invalid_count_equals_zero",
            {"expected": 0},
            count == 0,
        )
    event_detail_fields = {
        "event_row_contract": "invalid_count",
        "event_identity": "invalid_count",
        "event_participant": "invalid_count",
        "event_ordering": "invalid_count",
        "event_coverage": "unknown_count",
        "declared_completeness_used": "declared_input_count",
        "event_censoring": "coverage_censored_count",
    }
    if check_kind in event_detail_fields:
        detail_field = event_detail_fields[check_kind]
        count = int(details.get(detail_field, 0))
        return (
            count,
            f"{detail_field}_equals_zero",
            {"expected": 0},
            count == 0,
        )
    lifecycle_invalid_count_checks = {
        "lifecycle_history_row_contract",
        "lifecycle_history_state",
        "lifecycle_history_intervals",
        "lifecycle_history_counts",
        "lifecycle_trace",
        "lifecycle_distribution_row_contract",
        "lifecycle_distribution_math",
        "lifecycle_distribution_reconciliation",
        "lifecycle_transitions_row_contract",
        "lifecycle_transitions_math",
        "lifecycle_dwell_row_contract",
        "lifecycle_dwell_math",
        "lifecycle_violations_row_contract",
        "lifecycle_violations_math",
        "lifecycle_source_history",
    }
    if check_kind in lifecycle_invalid_count_checks:
        count = int(details.get("invalid_count", 0))
        return (
            count,
            "invalid_count_equals_zero",
            {"expected": 0},
            count == 0,
        )
    if check_kind == "lifecycle_coverage":
        count = int(details.get("unknown_count", 0)) + int(details.get("invalid_count", 0))
        return (
            count,
            "unknown_or_invalid_coverage_count_equals_zero",
            {"expected": 0},
            count == 0,
        )
    if check_kind == "lifecycle_censoring":
        count = int(details.get("coverage_censored_interval_count", 0)) + int(
            details.get("coverage_censored_subject_count", 0)
        )
        return (
            count,
            "coverage_censored_count_equals_zero",
            {"expected": 0},
            count == 0,
        )
    raise ValueError(f"unsupported quality check kind: {check_kind}")


def extract_quality_check_findings(
    *,
    df: pd.DataFrame,
    artifact_id: str,
    session_id: str,
    subject: EvidenceSubject,
    committed_at: datetime,
    evaluated_scope: EvidenceScope,
    source_refs: tuple[str, ...],
) -> list[Finding]:
    """Extract one typed finding for every executed quality predicate."""
    findings: list[Finding] = []
    for _, row in df.sort_values("check_id", kind="stable").iterrows():
        check_id = str(row["check_id"])
        check_kind = str(row["check_kind"])
        raw_metric_id = row.get("metric_id")
        metric_id = raw_metric_id if isinstance(raw_metric_id, str) and raw_metric_id else None
        finding_subject, finding_scope = _metric_context(
            metric_id=metric_id,
            subject=subject,
            scope=evaluated_scope,
            artifact_id=artifact_id,
            session_id=session_id,
        )
        details = json.loads(str(row["details_json"]))
        measured, predicate, parameters, passed = _predicate(check_kind, details)
        findings.append(
            Finding(
                finding_id=make_finding_id(artifact_id, "quality_check", check_id),
                finding_type="quality_check",
                epistemic_kind="tested",
                artifact_id=artifact_id,
                session_id=session_id,
                subject=finding_subject,
                canonical_item_key=check_id,
                value=QualityCheckFindingValue(
                    check_id=check_id,
                    measured_value=measured,
                    expectation_predicate=predicate,
                    expectation_parameters=parameters,
                    expectation_condition_passed=passed,
                    evaluated_scope=finding_scope,
                    source_refs=source_refs,
                ),
                derivation=DerivationRule(
                    rule_id="extract.quality_check",
                    rule_version="v3",
                    operator="assess_quality",
                    source_fields=("check_id", "check_kind", "metric_id", "details_json"),
                    source_finding_refs=(),
                ),
                source_refs=source_refs,
                committed_at=committed_at,
            )
        )
    return findings


__all__ = ["extract_quality_check_findings"]
