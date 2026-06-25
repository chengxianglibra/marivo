"""Deterministic discovery rules and judgment-target templates.

Rules describe datasource evidence shape only. They never infer business
meaning, normalization policy, additivity, unit, or timezone policy.
Judgment targets are deterministic templates per discover kind, not
conclusions.
"""

from __future__ import annotations

from marivo.datasource.authoring import DatasourceRef
from marivo.datasource.discovery import (
    ColumnDiscoveryCandidate,
    DimensionDiscoveryResult,
    DimensionValueFact,
    DiscoveryEvidenceEntry,
    DiscoveryIssue,
    DiscoveryObjectKind,
    DiscoverySignal,
    EvidenceValue,
    JudgmentOwner,
    MeasureDiscoveryResult,
    SemanticJudgmentTarget,
    TableSource,
)
from marivo.datasource.metadata import TableMetadata
from marivo.datasource.scan import ColumnProfile, ScanReport, ScanScope


def _target(
    object_kind: DiscoveryObjectKind,
    field_path: str,
    question: str,
    owner: JudgmentOwner,
) -> SemanticJudgmentTarget:
    return SemanticJudgmentTarget(
        object_kind=object_kind,
        field_path=field_path,
        question=question,
        owner=owner,
    )


def entity_judgment_targets() -> tuple[SemanticJudgmentTarget, ...]:
    return (
        _target("entity", "entity.name", "choose the semantic entity label", "agent"),
        _target(
            "entity",
            "entity.primary_key",
            "decide the authoritative primary key from declared or sampled evidence",
            "user_or_project_context",
        ),
        _target(
            "entity",
            "entity.ai_context.business_definition",
            "write the entity's business meaning",
            "user_or_project_context",
        ),
    )


def dimension_judgment_targets() -> tuple[SemanticJudgmentTarget, ...]:
    return (
        _target("dimension", "dimension.column", "select the candidate column", "agent"),
        _target("dimension", "dimension.name", "choose the semantic dimension label", "agent"),
        _target(
            "dimension",
            "dimension.ai_context.business_definition",
            "write the dimension's business meaning",
            "user_or_project_context",
        ),
    )


def time_dimension_judgment_targets() -> tuple[SemanticJudgmentTarget, ...]:
    return (
        _target("time_dimension", "time_dimension.column", "select the candidate column", "agent"),
        _target("time_dimension", "time_dimension.name", "choose the semantic time dimension label", "agent"),
        _target(
            "time_dimension",
            "time_dimension.granularity",
            "decide the authoritative grain",
            "user_or_project_context",
        ),
        _target(
            "time_dimension",
            "time_dimension.parse",
            "decide the parse policy for string or integer encodings",
            "user_or_project_context",
        ),
        _target(
            "time_dimension",
            "time_dimension.is_default",
            "decide whether this is the default business time dimension",
            "user_or_project_context",
        ),
        _target(
            "time_dimension",
            "time_dimension.ai_context.business_definition",
            "write the time dimension's business meaning",
            "user_or_project_context",
        ),
    )


def measure_judgment_targets() -> tuple[SemanticJudgmentTarget, ...]:
    return (
        _target("measure", "measure.column", "decide whether the candidate column is a row-level quantitative fact", "agent"),
        _target("measure", "measure.name", "choose the semantic measure label", "agent"),
        _target(
            "measure",
            "measure.unit",
            "decide the authoritative unit, if any",
            "user_or_project_context",
        ),
        _target(
            "measure",
            "measure.additivity",
            "decide additive, semi-additive, or non-additive policy",
            "user_or_project_context",
        ),
        _target(
            "measure",
            "measure.ai_context.business_definition",
            "write the measure's business meaning",
            "user_or_project_context",
        ),
    )


def relationship_judgment_targets() -> tuple[SemanticJudgmentTarget, ...]:
    return (
        _target("relationship", "relationship.name", "choose the semantic relationship label", "agent"),
        _target(
            "relationship",
            "relationship.from_entity",
            "confirm the from-side entity",
            "user_or_project_context",
        ),
        _target(
            "relationship",
            "relationship.to_entity",
            "confirm the to-side entity",
            "user_or_project_context",
        ),
        _target(
            "relationship",
            "relationship.keys",
            "confirm the join key columns",
            "user_or_project_context",
        ),
        _target(
            "relationship",
            "relationship.ai_context.business_definition",
            "write the relationship's business meaning",
            "user_or_project_context",
        ),
    )


def dimension_value_judgment_targets() -> tuple[SemanticJudgmentTarget, ...]:
    return (
        _target(
            "dimension",
            "dimension_value.filter_selection",
            "decide current filter values from runtime evidence",
            "agent",
        ),
    )


_NUMERIC_TYPE_TOKENS = ("INT", "DECIMAL", "FLOAT", "DOUBLE", "NUMERIC", "REAL")
_LOW_CARDINALITY_THRESHOLD = 20


def _ev(*pairs: tuple[str, EvidenceValue]) -> tuple[DiscoveryEvidenceEntry, ...]:
    return tuple(DiscoveryEvidenceEntry(key=k, value=v) for k, v in pairs)


def _is_numeric(data_type: str) -> bool:
    upper = data_type.upper()
    return any(token in upper for token in _NUMERIC_TYPE_TOKENS)


def scan_rules(
    scan: ScanReport,
    scope: ScanScope,
) -> tuple[DiscoveryIssue, ...]:
    """Result-scope rules over a scan report and the scope that produced it.

    Emits exactly the result-scope issues; candidate-scope rules live in the
    per-column rule functions. A rule emits on exactly one scope.
    """
    issues: list[DiscoveryIssue] = []
    if scan.truncated:
        issues.append(
            DiscoveryIssue(
                rule_id="discovery_scan_truncated",
                kind="entity",
                severity="warning",
                subject="scan",
                message="bounded scan hit max_rows; evidence is from a truncated sample",
                evidence=_ev(("rows_scanned", scan.rows_scanned), ("max_rows", scope.max_rows)),
            )
        )
    if scope.partition is None:
        issues.append(
            DiscoveryIssue(
                rule_id="discovery_unpruned_scan",
                kind="entity",
                severity="info",
                subject="scan",
                message="scan ran without partition pruning",
                evidence=_ev(("partition", "none")),
            )
        )
    return tuple(issues)


def dimension_column_rules(
    profile: ColumnProfile,
) -> tuple[DiscoverySignal | DiscoveryIssue, ...]:
    """Candidate-scope dimension rules for one column profile."""
    out: list[DiscoverySignal | DiscoveryIssue] = []
    if profile.distinct_count <= _LOW_CARDINALITY_THRESHOLD and profile.distinct_count > 0:
        out.append(
            DiscoverySignal(
                rule_id="dimension_low_cardinality",
                kind="dimension",
                subject=profile.name,
                evidence=_ev(("distinct_count", profile.distinct_count)),
            )
        )
    if profile.null_count > 0:
        out.append(
            DiscoveryIssue(
                rule_id="dimension_nullable",
                kind="dimension",
                severity="info",
                subject=profile.name,
                message="column contains sampled nulls",
                evidence=_ev(("null_count", profile.null_count)),
            )
        )
    if profile.empty_count > 0:
        out.append(
            DiscoveryIssue(
                rule_id="dimension_empty_values_present",
                kind="dimension",
                severity="warning",
                subject=profile.name,
                message="column contains empty string values",
                evidence=_ev(("empty_count", profile.empty_count)),
            )
        )
    return tuple(out)


def measure_column_rules(
    profile: ColumnProfile,
) -> tuple[DiscoverySignal | DiscoveryIssue, ...]:
    """Candidate-scope measure rules for one column profile."""
    if _is_numeric(profile.data_type):
        return (
            DiscoverySignal(
                rule_id="measure_numeric_type",
                kind="measure",
                subject=profile.name,
                evidence=_ev(("data_type", profile.data_type)),
            ),
        )
    return (
        DiscoveryIssue(
            rule_id="measure_non_numeric_type",
            kind="measure",
            severity="blocker",
            subject=profile.name,
            message="requested measure column is not a numeric type",
            evidence=_ev(("data_type", profile.data_type)),
        ),
    )


def dimension_value_rules(
    values: tuple[DimensionValueFact, ...],
    complete: bool,
) -> tuple[DiscoverySignal | DiscoveryIssue, ...]:
    """Candidate-scope dimension-value rules for one column's bounded values."""
    out: list[DiscoverySignal | DiscoveryIssue] = [
        DiscoverySignal(
            rule_id="dimension_values_top_values",
            kind="dimension",
            subject="dimension_values",
            evidence=_ev(("value_count", len(values))),
        )
    ]
    if not complete:
        out.append(
            DiscoveryIssue(
                rule_id="dimension_values_truncated",
                kind="dimension",
                severity="warning",
                subject="dimension_values",
                message="returned values hit the limit or scan truncation; result is not exhaustive",
                evidence=_ev(("complete", False)),
            )
        )
    return tuple(out)


def _split(
    items: tuple[DiscoverySignal | DiscoveryIssue, ...],
) -> tuple[tuple[DiscoverySignal, ...], tuple[DiscoveryIssue, ...]]:
    signals = tuple(i for i in items if isinstance(i, DiscoverySignal))
    issues = tuple(i for i in items if isinstance(i, DiscoveryIssue))
    return signals, issues


def build_dimension_result(
    *,
    datasource: DatasourceRef,
    source: TableSource,
    table_metadata: TableMetadata | None,
    scan: ScanReport,
    scope: ScanScope,
    candidate_profiles: tuple[ColumnProfile, ...],
) -> DimensionDiscoveryResult:
    """Build a DimensionDiscoveryResult from scan + column profiles.

    Result-scope issues come from ``scan_rules``; each candidate carries its
    own ``dimension_column_rules`` signals/issues. The two scopes never
    overlap.
    """
    result_issues = scan_rules(scan, scope)
    candidates: list[ColumnDiscoveryCandidate] = []
    for profile in candidate_profiles:
        sig, iss = _split(dimension_column_rules(profile))
        candidates.append(
            ColumnDiscoveryCandidate(
                column=profile.name,
                profile=profile,
                signals=sig,
                issues=iss,
            )
        )
    return DimensionDiscoveryResult(
        datasource=datasource,
        source=source,
        table_metadata=table_metadata,
        scan=scan,
        signals=(),
        issues=result_issues,
        judgment_targets=dimension_judgment_targets(),
        candidates=tuple(candidates),
    )


def build_measure_result(
    *,
    datasource: DatasourceRef,
    source: TableSource,
    table_metadata: TableMetadata | None,
    scan: ScanReport,
    scope: ScanScope,
    candidate_profiles: tuple[ColumnProfile, ...],
) -> MeasureDiscoveryResult:
    """Build a MeasureDiscoveryResult from scan + column profiles."""
    result_issues = scan_rules(scan, scope)
    candidates: list[ColumnDiscoveryCandidate] = []
    for profile in candidate_profiles:
        sig, iss = _split(measure_column_rules(profile))
        candidates.append(
            ColumnDiscoveryCandidate(
                column=profile.name,
                profile=profile,
                signals=sig,
                issues=iss,
            )
        )
    return MeasureDiscoveryResult(
        datasource=datasource,
        source=source,
        table_metadata=table_metadata,
        scan=scan,
        signals=(),
        issues=result_issues,
        judgment_targets=measure_judgment_targets(),
        candidates=tuple(candidates),
    )
