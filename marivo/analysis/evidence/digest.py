"""Private operator-local rules for building bounded artifact digests."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TypedDict, cast

from marivo.analysis.evidence.identity import (
    make_digest_fingerprint,
    make_digest_item_id,
)
from marivo.analysis.evidence.types import (
    AnalysisScope,
    AnomalyCandidate,
    AnomalyCandidateFindingValue,
    ArtifactDigest,
    AssociationFact,
    AssociationFindingValue,
    ChangeFact,
    ContributionFact,
    ContributionFindingValue,
    DeltaFindingValue,
    DerivationRule,
    DigestItem,
    DigestItemKind,
    FallbackReason,
    Finding,
    ForecastOutput,
    ForecastPointFindingValue,
    InferenceBoundary,
    MetricValueFindingValue,
    ObservationFact,
    ObservationFindingValue,
    OmissionSummary,
    OperatorSemantics,
    QualityCheckFindingValue,
    QualityCheckResult,
    QualitySummary,
    RawFallback,
    Subject,
    TestDecision,
    TestFindingValue,
)

_ITEM_LIMIT = 5
_BOUNDARY_LIMIT = 3


@dataclass(frozen=True)
class _RuleEntry:
    rule_id: str
    rule_version: str
    accepted_finding_kinds: tuple[str, ...]
    produced_item_kinds: tuple[DigestItemKind, ...]
    source_fields: tuple[str, ...]
    sort_key: Callable[[Finding], tuple[int, str, str]]


def _default_sort_key(finding: Finding) -> tuple[int, str, str]:
    rank = getattr(finding.value, "rank", None)
    return (
        rank if isinstance(rank, int) else 2**31 - 1,
        finding.canonical_item_key,
        finding.finding_id,
    )


_RULES: dict[str, _RuleEntry] = {
    "observe": _RuleEntry(
        rule_id="digest.observe",
        rule_version="v1",
        accepted_finding_kinds=("observation", "metric_value"),
        produced_item_kinds=("observation",),
        source_fields=("value.row_count", "value.value"),
        sort_key=_default_sort_key,
    ),
    "compare": _RuleEntry(
        rule_id="digest.compare",
        rule_version="v1",
        accepted_finding_kinds=("delta",),
        produced_item_kinds=("change",),
        source_fields=(
            "value.current",
            "value.baseline",
            "value.magnitude",
            "value.relative_delta",
            "value.relative_delta_undefined_reason",
            "value.direction",
            "value.presence",
            "value.unit",
            "value.dimension_keys",
        ),
        sort_key=_default_sort_key,
    ),
    "attribute": _RuleEntry(
        rule_id="digest.contribution",
        rule_version="v1",
        accepted_finding_kinds=("decomposition_item",),
        produced_item_kinds=("contribution",),
        source_fields=(
            "value.dimension",
            "value.dimension_keys",
            "value.contribution_value",
            "value.contribution_share",
            "value.contribution_rank",
            "value.decomposition_method",
            "value.reconciliation_residual",
        ),
        sort_key=_default_sort_key,
    ),
    "correlate": _RuleEntry(
        rule_id="digest.association",
        rule_version="v1",
        accepted_finding_kinds=("correlation_result",),
        produced_item_kinds=("association",),
        source_fields=(
            "value.left_ref",
            "value.right_ref",
            "value.method",
            "value.coefficient",
            "value.p_value",
            "value.confidence_interval",
            "value.sample_size",
            "value.join_basis",
            "value.lag",
        ),
        sort_key=_default_sort_key,
    ),
    "hypothesis_test": _RuleEntry(
        rule_id="digest.test_decision",
        rule_version="v1",
        accepted_finding_kinds=("test_result",),
        produced_item_kinds=("test_decision",),
        source_fields=(
            "value.null_predicate",
            "value.alternative",
            "value.method",
            "value.alpha",
            "value.statistic",
            "value.p_value",
            "value.effect_estimate",
            "value.confidence_interval",
            "value.reject_null",
            "value.sample_size",
        ),
        sort_key=_default_sort_key,
    ),
    "forecast": _RuleEntry(
        rule_id="digest.forecast",
        rule_version="v1",
        accepted_finding_kinds=("forecast_point",),
        produced_item_kinds=("forecast_output",),
        source_fields=(
            "value.bucket_start",
            "value.bucket_end",
            "value.predicted_value",
            "value.prediction_interval",
            "value.horizon_index",
            "value.model",
            "value.training_scope",
            "value.evaluation_scope",
        ),
        sort_key=_default_sort_key,
    ),
    "discover": _RuleEntry(
        rule_id="digest.anomaly_candidate",
        rule_version="v1",
        accepted_finding_kinds=("anomaly_candidate",),
        produced_item_kinds=("anomaly_candidate",),
        source_fields=(
            "value.candidate_ref",
            "value.score",
            "value.detector",
            "value.threshold",
            "value.rank",
            "value.reason_codes",
            "value.flag_level",
            "value.current_value",
            "value.baseline_value",
            "value.deviation_absolute",
            "value.deviation_relative",
        ),
        sort_key=_default_sort_key,
    ),
    "assess_quality": _RuleEntry(
        rule_id="digest.quality_check",
        rule_version="v1",
        accepted_finding_kinds=("quality_check",),
        produced_item_kinds=("quality_check",),
        source_fields=(
            "value.check_id",
            "value.measured_value",
            "value.expectation_predicate",
            "value.expectation_parameters",
            "value.expectation_condition_passed",
        ),
        sort_key=_default_sort_key,
    ),
    "transform": _RuleEntry(
        rule_id="digest.transform",
        rule_version="v1",
        accepted_finding_kinds=(),
        produced_item_kinds=(),
        source_fields=(),
        sort_key=_default_sort_key,
    ),
    "select_metric": _RuleEntry(
        rule_id="digest.select_metric",
        rule_version="v1",
        accepted_finding_kinds=(),
        produced_item_kinds=(),
        source_fields=(),
        sort_key=_default_sort_key,
    ),
}
_OPERATOR_ALIASES = {"decompose": "attribute", "test": "hypothesis_test"}


def _item_derivation(entry: _RuleEntry, finding: Finding) -> DerivationRule:
    return DerivationRule(
        rule_id=entry.rule_id,
        rule_version=entry.rule_version,
        operator=entry.rule_id.removeprefix("digest."),
        source_fields=entry.source_fields,
        source_finding_refs=(finding.finding_id,),
    )


class _CommonItemArgs(TypedDict):
    item_id: str
    artifact_ref: str
    subject: Subject
    scope: AnalysisScope
    derivation: DerivationRule


def _common(entry: _RuleEntry, finding: Finding, scope: AnalysisScope) -> _CommonItemArgs:
    item_kind = cast(
        "DigestItemKind",
        {
            "observation": "observation",
            "delta": "change",
            "decomposition_item": "contribution",
            "correlation_result": "association",
            "test_result": "test_decision",
            "forecast_point": "forecast_output",
            "anomaly_candidate": "anomaly_candidate",
            "quality_check": "quality_check",
            "metric_value": "observation",
        }[finding.finding_type],
    )
    refs = (finding.finding_id,)
    return {
        "item_id": make_digest_item_id(
            artifact_ref=finding.artifact_id,
            item_kind=item_kind,
            source_finding_refs=refs,
        ),
        "artifact_ref": finding.artifact_id,
        "subject": finding.subject,
        "scope": scope,
        "derivation": _item_derivation(entry, finding),
    }


def _build_item(entry: _RuleEntry, finding: Finding, scope: AnalysisScope) -> DigestItem | None:
    value = finding.value
    common = _common(entry, finding, scope)
    if isinstance(value, ObservationFindingValue):
        return ObservationFact(**common, row_count=value.row_count, value=value.value)
    if isinstance(value, MetricValueFindingValue):
        return None
    if isinstance(value, DeltaFindingValue):
        return ChangeFact(
            **common,
            current=value.current,
            baseline=value.baseline,
            delta=value.magnitude,
            relative_delta=value.relative_delta,
            relative_delta_undefined_reason=value.relative_delta_undefined_reason,
            direction=value.direction,
            presence=value.presence,
            unit=value.unit,
            dimension_keys=value.dimension_keys,
        )
    if isinstance(value, ContributionFindingValue):
        return ContributionFact(
            **common,
            dimension=value.dimension,
            dimension_keys=value.dimension_keys,
            contribution_value=value.contribution_value,
            contribution_share=value.contribution_share,
            contribution_rank=value.contribution_rank,
            decomposition_method=value.decomposition_method,
            reconciliation_residual=value.reconciliation_residual,
        )
    if isinstance(value, AssociationFindingValue):
        return AssociationFact(
            **common,
            left_ref=value.left_ref,
            right_ref=value.right_ref,
            method=value.method,
            coefficient=value.coefficient,
            p_value=value.p_value,
            confidence_interval=value.confidence_interval,
            sample_size=value.sample_size,
            join_basis=value.join_basis,
            lag=value.lag,
        )
    if isinstance(value, TestFindingValue):
        return TestDecision(
            **common,
            null_predicate=value.null_predicate,
            alternative=value.alternative,
            method=value.method,
            alpha=value.alpha,
            statistic=value.statistic,
            p_value=value.p_value,
            effect_estimate=value.effect_estimate,
            confidence_interval=value.confidence_interval,
            reject_null=value.reject_null,
            sample_size=value.sample_size,
        )
    if isinstance(value, ForecastPointFindingValue):
        return ForecastOutput(
            **common,
            bucket_start=value.bucket_start,
            bucket_end=value.bucket_end,
            predicted_value=value.predicted_value,
            prediction_interval=value.prediction_interval,
            horizon_index=value.horizon_index,
            model=value.model,
            training_scope=value.training_scope,
            evaluation_scope=value.evaluation_scope,
        )
    if isinstance(value, AnomalyCandidateFindingValue):
        return AnomalyCandidate(
            **common,
            candidate_ref=value.candidate_ref,
            score=value.score,
            detector=value.detector,
            threshold=value.threshold,
            rank=value.rank,
            reason_codes=value.reason_codes,
            flag_level=value.flag_level,
            current_value=value.current_value,
            baseline_value=value.baseline_value,
            deviation_absolute=value.deviation_absolute,
            deviation_relative=value.deviation_relative,
        )
    if isinstance(value, QualityCheckFindingValue):
        return QualityCheckResult(
            **common,
            check_id=value.check_id,
            measured_value=value.measured_value,
            expectation_predicate=value.expectation_predicate,
            expectation_parameters=value.expectation_parameters,
            expectation_condition_passed=value.expectation_condition_passed,
        )
    raise TypeError(f"unsupported finding value: {type(value).__name__}")


def _boundaries(
    operator: str, items: tuple[DigestItem, ...], omitted: int
) -> tuple[InferenceBoundary, ...]:
    result: list[InferenceBoundary] = []
    if omitted:
        result.append(
            InferenceBoundary(
                kind="full_distribution_not_in_digest",
                reason="digest_bound_exceeded",
                required_evidence=("full_distribution",),
            )
        )
    if operator == "correlate":
        associations = [item for item in items if isinstance(item, AssociationFact)]
        if any(item.p_value is None for item in associations):
            result.append(
                InferenceBoundary(
                    kind="significance_not_computed",
                    reason="operator_did_not_compute",
                    required_evidence=("significance_statistic",),
                )
            )
        if any(item.confidence_interval is None for item in associations):
            result.append(
                InferenceBoundary(
                    kind="interval_not_computed",
                    reason="operator_did_not_compute",
                    required_evidence=("uncertainty_interval",),
                )
            )
        result.append(
            InferenceBoundary(
                kind="causal_effect_not_estimated",
                reason="requires_independent_evidence",
                required_evidence=("causal_design",),
            )
        )
    elif operator == "attribute":
        result.append(
            InferenceBoundary(
                kind="causal_effect_not_estimated",
                reason="requires_independent_evidence",
                required_evidence=("causal_design",),
            )
        )
    elif operator == "hypothesis_test" and any(
        isinstance(item, TestDecision) and item.confidence_interval is None for item in items
    ):
        result.append(
            InferenceBoundary(
                kind="interval_not_computed",
                reason="operator_did_not_compute",
                required_evidence=("uncertainty_interval",),
            )
        )
    elif operator == "forecast":
        result.extend(
            (
                InferenceBoundary(
                    kind="forecast_actual_not_observed",
                    reason="artifact_does_not_contain",
                    required_evidence=("observed_forecast_actual",),
                ),
                InferenceBoundary(
                    kind="forecast_accuracy_not_evaluated",
                    reason="artifact_does_not_contain",
                    required_evidence=("forecast_error_metric",),
                ),
            )
        )
    elif operator == "discover":
        result.append(
            InferenceBoundary(
                kind="candidate_not_reviewed",
                reason="requires_independent_evidence",
                required_evidence=("independent_review",),
            )
        )
    elif operator == "assess_quality":
        result.append(
            InferenceBoundary(
                kind="quality_dimensions_not_tested",
                reason="outside_library_contract",
                required_evidence=("additional_quality_check",),
            )
        )
    return tuple(result[:_BOUNDARY_LIMIT])


def build_artifact_digest(
    *,
    artifact_ref: str,
    operator: OperatorSemantics,
    subject: Subject,
    scope: AnalysisScope,
    findings: Iterable[Finding],
    quality: QualitySummary | None,
    rows_available: bool,
) -> ArtifactDigest:
    """Build one bounded digest from validated in-memory typed findings."""
    operator_name = _OPERATOR_ALIASES.get(operator.operator, operator.operator)
    entry = _RULES.get(operator_name)
    supplied_findings = tuple(findings)
    if entry is None:
        raise ValueError(f"no digest rule registered for operator {operator.operator!r}")
    ordered = sorted(supplied_findings, key=entry.sort_key)
    for finding in ordered:
        if finding.artifact_id != artifact_ref:
            raise ValueError("every finding must belong to the digest artifact")
        if finding.finding_type not in entry.accepted_finding_kinds:
            raise ValueError(
                f"{operator.operator} digest does not accept {finding.finding_type} findings"
            )
    all_items = tuple(
        item for finding in ordered if (item := _build_item(entry, finding, scope)) is not None
    )
    retained = all_items[:_ITEM_LIMIT]
    omitted_items = all_items[_ITEM_LIMIT:]
    omitted_kinds = tuple(dict.fromkeys(item.kind for item in omitted_items))
    boundaries = _boundaries(operator_name, retained, len(omitted_items))
    fallback_reasons: list[FallbackReason] = ["unregistered_question"]
    if omitted_items:
        fallback_reasons.append("omitted_item_detail")
    if rows_available:
        fallback_reasons.append("row_level_validation")
    payload = {
        "digest_version": "v1",
        "artifact_ref": artifact_ref,
        "operator": operator,
        "subject": subject,
        "scope": scope,
        "items": retained,
        "boundaries": boundaries,
        "omissions": OmissionSummary(
            retained_items=len(retained),
            omitted_items=len(omitted_items),
            omitted_kinds=omitted_kinds,
            bounded=bool(omitted_items),
        ),
        "quality": quality,
        "fallback": RawFallback(
            artifact_ref=artifact_ref,
            findings_available=bool(ordered),
            rows_available=rows_available,
            recommended_when=tuple(fallback_reasons),
        ),
        "fingerprint": "",
    }
    digest = ArtifactDigest.model_validate(payload)
    return digest.model_copy(update={"fingerprint": make_digest_fingerprint(digest)})


__all__ = ["build_artifact_digest"]
