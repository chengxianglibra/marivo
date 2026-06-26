"""Deterministic discovery rules and judgment-target templates.

Rules describe datasource evidence shape only. They never infer business
meaning, normalization policy, additivity, unit, or timezone policy.
Judgment targets are deterministic templates per discover kind, not
conclusions.
"""

from __future__ import annotations

import datetime
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from marivo.datasource.authoring import DatasourceRef
from marivo.datasource.discovery import (
    ColumnDiscoveryCandidate,
    DimensionDiscoveryResult,
    DimensionValueFact,
    DiscoveryEvidenceEntry,
    DiscoveryIssue,
    DiscoveryObjectKind,
    DiscoverySeverity,
    DiscoverySignal,
    EntityDiscoveryCandidate,
    EntityDiscoveryResult,
    EvidenceValue,
    FormatCandidate,
    JudgmentOwner,
    KeyTypeEvidence,
    MeasureDiscoveryResult,
    PrimaryKeyCandidate,
    RelationshipDiscoveryEvidence,
    RelationshipDiscoveryResult,
    SemanticJudgmentTarget,
    TableSource,
    TimeColumnDiscoveryCandidate,
    TimeDimensionDiscoveryResult,
    TimeValueRange,
)
from marivo.datasource.ir import source_name
from marivo.datasource.metadata import TableMetadata
from marivo.datasource.scan import (
    ColumnProfile,
    JoinSide,
    PartitionResolution,
    ScanReport,
    ScanScope,
)


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
        _target(
            "time_dimension",
            "time_dimension.name",
            "choose the semantic time dimension label",
            "agent",
        ),
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
        _target(
            "measure",
            "measure.column",
            "decide whether the candidate column is a row-level quantitative fact",
            "agent",
        ),
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
        _target(
            "relationship", "relationship.name", "choose the semantic relationship label", "agent"
        ),
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
_IDENTIFIER_SUFFIXES = ("_id",)
_UNIT_TOKENS = (
    "usd",
    "dollars",
    "cents",
    "percent",
    "seconds",
    "milliseconds",
    "bytes",
    "meters",
    "kilograms",
)


def _ev(*pairs: tuple[str, EvidenceValue]) -> tuple[DiscoveryEvidenceEntry, ...]:
    return tuple(DiscoveryEvidenceEntry(key=k, value=v) for k, v in pairs)


def _is_numeric(data_type: str) -> bool:
    upper = data_type.upper()
    return any(token in upper for token in _NUMERIC_TYPE_TOKENS)


def _detect_unit_token(comment: str | None) -> str | None:
    if not comment:
        return None
    lower = comment.lower()
    for token in _UNIT_TOKENS:
        if token in lower:
            return token
    return None


def _is_identifier_name(name: str) -> bool:
    lower = name.lower()
    return lower == "id" or any(lower.endswith(suffix) for suffix in _IDENTIFIER_SUFFIXES)


@dataclass(frozen=True)
class PartitionResolutionOutcome:
    """Outcome of resolving a ScanScope partition against table metadata.

    Attributes:
        resolution: How the partition was resolved.
        partition_used: The concrete partition mapping used, or ``None``.
        unresolved: True when ``"latest"`` was requested but no concrete value
            could be resolved within the available backend capability.
        reason: Optional explanation when ``unresolved`` is True.
    """

    resolution: PartitionResolution
    partition_used: Mapping[str, str] | None
    unresolved: bool = False
    reason: str | None = None


def resolve_partition(
    metadata: TableMetadata | None,
    scope: ScanScope,
) -> PartitionResolutionOutcome:
    """Classify how a scan scope's partition resolves against table metadata.

    This is a pure classification for rule emission; it does not execute a scan
    or look up the latest partition value. When ``"latest"`` is requested and the
    source has partition metadata, the outcome is marked unresolved because the
    concrete latest value is not derivable without a backend query.

    Args:
        metadata: Table metadata, or ``None`` when no metadata is available.
        scope: The scan scope whose partition is being classified.

    Returns:
        A ``PartitionResolutionOutcome``.
    """
    if scope.partition is None:
        return PartitionResolutionOutcome(resolution="unpruned", partition_used=None)
    if scope.partition == "latest":
        if metadata is None or not metadata.partitions:
            return PartitionResolutionOutcome(resolution="unpruned", partition_used=None)
        return PartitionResolutionOutcome(
            resolution="latest",
            partition_used=None,
            unresolved=True,
            reason="latest partition value not resolvable within scan budget",
        )
    return PartitionResolutionOutcome(
        resolution="explicit",
        partition_used=dict(scope.partition),
    )


def scan_rules(
    scan: ScanReport,
    scope: ScanScope,
    outcome: PartitionResolutionOutcome | None = None,
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
    if outcome is not None and outcome.unresolved:
        issues.append(
            DiscoveryIssue(
                rule_id="discovery_latest_partition_unresolved",
                kind="entity",
                severity="warning",
                subject="scan",
                message="latest partition requested but could not be resolved to a concrete value",
                evidence=_ev(
                    ("resolution", outcome.resolution),
                    ("reason", outcome.reason),
                ),
            )
        )
    return tuple(issues)


def metadata_rules(metadata: TableMetadata) -> tuple[DiscoveryIssue, ...]:
    """Forward table metadata warnings as ``discovery_metadata_warning`` issues."""
    out: list[DiscoveryIssue] = []
    for warning in metadata.warnings:
        severity: DiscoverySeverity = (
            "warning" if warning.kind == "metadata_query_failed" else "info"
        )
        out.append(
            DiscoveryIssue(
                rule_id="discovery_metadata_warning",
                kind="entity",
                severity=severity,
                subject=metadata.table,
                message=warning.message,
                evidence=_ev(
                    ("warning_kind", warning.kind),
                    ("column_count", len(warning.columns)),
                ),
            )
        )
    return tuple(out)


def column_limit_rules(
    scope: ScanScope,
    requested_count: int,
) -> tuple[DiscoveryIssue, ...]:
    """Emit ``discovery_column_limit_truncated`` when columns were omitted."""
    if requested_count > scope.max_columns:
        return (
            DiscoveryIssue(
                rule_id="discovery_column_limit_truncated",
                kind="entity",
                severity="warning",
                subject="scan",
                message="column list truncated by max_columns; some columns not profiled",
                evidence=_ev(
                    ("requested", requested_count),
                    ("max_columns", scope.max_columns),
                    ("omitted", requested_count - scope.max_columns),
                ),
            ),
        )
    return ()


def dimension_column_rules(
    profile: ColumnProfile,
) -> tuple[DiscoverySignal | DiscoveryIssue, ...]:
    """Candidate-scope dimension rules for one column profile."""
    out: list[DiscoverySignal | DiscoveryIssue] = []
    if 0 < profile.distinct_count <= _LOW_CARDINALITY_THRESHOLD:
        out.append(
            DiscoverySignal(
                rule_id="dimension_low_cardinality",
                kind="dimension",
                subject=profile.name,
                evidence=_ev(("distinct_count", profile.distinct_count)),
            )
        )
    if (
        profile.distinct_ratio is not None
        and profile.distinct_ratio >= 0.9
        and profile.distinct_count > _LOW_CARDINALITY_THRESHOLD
    ):
        out.append(
            DiscoverySignal(
                rule_id="dimension_high_cardinality",
                kind="dimension",
                subject=profile.name,
                evidence=_ev(
                    ("distinct_ratio", profile.distinct_ratio),
                    ("distinct_count", profile.distinct_count),
                ),
            )
        )
    if profile.type_family == "boolean" or profile.distinct_count == 2:
        out.append(
            DiscoverySignal(
                rule_id="dimension_boolean_like",
                kind="dimension",
                subject=profile.name,
                evidence=_ev(
                    ("type_family", profile.type_family),
                    ("distinct_count", profile.distinct_count),
                ),
            )
        )
    if profile.type_family == "integer" and _is_identifier_name(profile.name):
        out.append(
            DiscoverySignal(
                rule_id="dimension_identifier_shape",
                kind="dimension",
                subject=profile.name,
                evidence=_ev(("type_family", profile.type_family), ("name", profile.name)),
            )
        )
    if profile.type_family == "string" and profile.min_length is not None:
        out.append(
            DiscoverySignal(
                rule_id="dimension_text_shape",
                kind="dimension",
                subject=profile.name,
                evidence=_ev(
                    ("min_length", profile.min_length),
                    ("distinct_ratio", profile.distinct_ratio),
                ),
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
    if not _is_numeric(profile.data_type):
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
    out: list[DiscoverySignal | DiscoveryIssue] = [
        DiscoverySignal(
            rule_id="measure_numeric_type",
            kind="measure",
            subject=profile.name,
            evidence=_ev(("data_type", profile.data_type)),
        )
    ]
    if profile.negative_count > 0:
        out.append(
            DiscoverySignal(
                rule_id="measure_negative_values_present",
                kind="measure",
                subject=profile.name,
                evidence=_ev(("negative_count", profile.negative_count)),
            )
        )
    if profile.zero_count > 0:
        out.append(
            DiscoverySignal(
                rule_id="measure_zero_values_present",
                kind="measure",
                subject=profile.name,
                evidence=_ev(("zero_count", profile.zero_count)),
            )
        )
    if profile.null_count > 0:
        out.append(
            DiscoveryIssue(
                rule_id="measure_nullable",
                kind="measure",
                severity="info",
                subject=profile.name,
                message="column contains sampled nulls",
                evidence=_ev(("null_count", profile.null_count)),
            )
        )
    token = _detect_unit_token(profile.comment)
    if token is not None:
        out.append(
            DiscoverySignal(
                rule_id="measure_unit_token_observed",
                kind="measure",
                subject=profile.name,
                evidence=_ev(("token", token), ("comment", profile.comment)),
            )
        )
    return tuple(out)


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
    result_issues = scan_rules(scan, scope, resolve_partition(table_metadata, scope))
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
    result_issues = scan_rules(scan, scope, resolve_partition(table_metadata, scope))
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


def _entity_primary_key_candidates(
    metadata: TableMetadata | None,
    scan: ScanReport,
    profiles: tuple[ColumnProfile, ...],
) -> tuple[PrimaryKeyCandidate, ...]:
    """Build typed primary-key candidates from declared and sampled evidence."""
    out: list[PrimaryKeyCandidate] = []
    if metadata is not None:
        for column in metadata.primary_keys:
            out.append(
                PrimaryKeyCandidate(
                    column=column,
                    source="declared_primary",
                    evidence=_ev(("source", "declared_primary")),
                )
            )
        for constraint in metadata.unique_constraints:
            for column in constraint.columns:
                out.append(
                    PrimaryKeyCandidate(
                        column=column,
                        source="declared_unique",
                        evidence=_ev(("kind", constraint.kind)),
                    )
                )
    rows = scan.rows_scanned
    for profile in profiles:
        if rows > 0 and profile.null_count == 0 and profile.distinct_count == rows:
            out.append(
                PrimaryKeyCandidate(
                    column=profile.name,
                    source="sampled_unique",
                    evidence=_ev(
                        ("distinct_count", profile.distinct_count),
                        ("rows_scanned", rows),
                    ),
                )
            )
    return tuple(out)


def entity_rules(
    metadata: TableMetadata | None,
    scan: ScanReport,
    profiles: tuple[ColumnProfile, ...],
    scope: ScanScope,
) -> tuple[DiscoverySignal | DiscoveryIssue, ...]:
    """Candidate-scope entity rules for one table."""
    out: list[DiscoverySignal | DiscoveryIssue] = []
    table_name = metadata.table if metadata is not None else "table"

    if metadata is not None:
        for column in metadata.primary_keys:
            out.append(
                DiscoverySignal(
                    rule_id="entity_declared_primary_key",
                    kind="entity",
                    subject=column,
                    evidence=_ev(("column", column), ("source", "declared_primary")),
                )
            )
        for constraint in metadata.unique_constraints:
            for column in constraint.columns:
                out.append(
                    DiscoverySignal(
                        rule_id="entity_declared_unique_key",
                        kind="entity",
                        subject=column,
                        evidence=_ev(("column", column), ("kind", constraint.kind)),
                    )
                )

    rows = scan.rows_scanned
    for profile in profiles:
        if rows > 0 and profile.null_count == 0 and profile.distinct_count == rows:
            out.append(
                DiscoverySignal(
                    rule_id="entity_sampled_unique_column",
                    kind="entity",
                    subject=profile.name,
                    evidence=_ev(
                        ("distinct_count", profile.distinct_count),
                        ("rows_scanned", rows),
                    ),
                )
            )

    has_pk_evidence = any(
        isinstance(item, DiscoverySignal)
        and item.rule_id
        in (
            "entity_declared_primary_key",
            "entity_declared_unique_key",
            "entity_sampled_unique_column",
        )
        for item in out
    )
    if not has_pk_evidence:
        out.append(
            DiscoveryIssue(
                rule_id="entity_no_primary_key_evidence",
                kind="entity",
                severity="warning",
                subject=table_name,
                message=(
                    "no declared primary key and no sampled unique column within the scan budget"
                ),
                evidence=_ev(("rows_scanned", rows)),
            )
        )

    for profile in profiles:
        if profile.type_family in ("date", "timestamp"):
            out.append(
                DiscoverySignal(
                    rule_id="entity_temporal_column_detected",
                    kind="entity",
                    subject=profile.name,
                    evidence=_ev(("column", profile.name), ("type_family", profile.type_family)),
                )
            )

    if metadata is not None:
        for partition in metadata.partitions:
            out.append(
                DiscoverySignal(
                    rule_id="entity_partition_column_detected",
                    kind="entity",
                    subject=partition.name,
                    evidence=_ev(("partition", partition.name)),
                )
            )

    if metadata is not None and len(metadata.columns) > scope.max_columns:
        out.append(
            DiscoveryIssue(
                rule_id="entity_many_columns",
                kind="entity",
                severity="info",
                subject=table_name,
                message="column count exceeds the bounded scan limit",
                evidence=_ev(
                    ("column_count", len(metadata.columns)),
                    ("max_columns", scope.max_columns),
                ),
            )
        )

    return tuple(out)


def build_entity_result(
    *,
    datasource: DatasourceRef,
    source: TableSource,
    table_metadata: TableMetadata | None,
    scan: ScanReport,
    scope: ScanScope,
    candidate_profiles: tuple[ColumnProfile, ...],
) -> EntityDiscoveryResult:
    """Build an EntityDiscoveryResult from metadata, scan, and column profiles.

    Result-scope issues come from ``scan_rules`` (with the partition outcome),
    ``metadata_rules``, and ``column_limit_rules``; the single entity candidate
    carries ``entity_rules`` signals/issues plus typed primary-key candidates.
    """
    outcome = resolve_partition(table_metadata, scope)
    result_issues: tuple[DiscoveryIssue, ...] = scan_rules(scan, scope, outcome)
    if table_metadata is not None:
        result_issues = result_issues + metadata_rules(table_metadata)
    requested = (
        len(table_metadata.columns) if table_metadata is not None else len(candidate_profiles)
    )
    result_issues = result_issues + column_limit_rules(scope, requested)

    pk_candidates = _entity_primary_key_candidates(table_metadata, scan, candidate_profiles)
    time_like_columns = tuple(
        profile.name
        for profile in candidate_profiles
        if profile.type_family in ("date", "timestamp")
    )
    partition_columns = (
        tuple(partition.name for partition in table_metadata.partitions)
        if table_metadata is not None
        else ()
    )
    signals, issues = _split(entity_rules(table_metadata, scan, candidate_profiles, scope))
    candidate = EntityDiscoveryCandidate(
        table=source_name(source),
        primary_key_candidates=pk_candidates,
        time_like_columns=time_like_columns,
        partition_columns=partition_columns,
        column_profiles=candidate_profiles,
        signals=signals,
        issues=issues,
    )
    return EntityDiscoveryResult(
        datasource=datasource,
        source=source,
        table_metadata=table_metadata,
        scan=scan,
        signals=(),
        issues=result_issues,
        judgment_targets=entity_judgment_targets(),
        candidates=(candidate,),
    )


_STRING_TIME_FORMATS: tuple[tuple[str, str], ...] = (
    ("%Y-%m-%d", "date"),
    ("%Y/%m/%d", "date"),
    ("%Y-%m-%d %H:%M:%S", "datetime"),
    ("%Y%m%d", "date"),
    ("%H:%M:%S", "hour_only"),
    ("%H:%M", "hour_only"),
)


def _count_strptime_matches(samples: tuple[object, ...], fmt: str) -> int:
    count = 0
    for value in samples:
        try:
            datetime.datetime.strptime(str(value), fmt)
            count += 1
        except ValueError:
            pass
    return count


def _is_yyyymmdd(value: object) -> bool:
    text = str(value)
    if len(text) != 8 or not text.isdigit():
        return False
    try:
        datetime.datetime.strptime(text, "%Y%m%d")
    except ValueError:
        return False
    return True


def _is_epoch_millis(value: object) -> bool:
    text = str(value)
    return len(text) == 13 and text.isdigit()


def _is_ten_digit_integer(value: object) -> bool:
    text = str(value)
    return len(text) == 10 and text.isdigit()


def _detect_integer_time_formats(samples: tuple[object, ...]) -> tuple[FormatCandidate, ...]:
    if not samples:
        return ()
    out: list[FormatCandidate] = []
    if all(_is_yyyymmdd(value) for value in samples):
        out.append(
            FormatCandidate(
                format="%Y%m%d", kind="integer", matched_count=len(samples), ambiguous=False
            )
        )
    if all(_is_epoch_millis(value) for value in samples):
        out.append(
            FormatCandidate(
                format="epoch_millis", kind="integer", matched_count=len(samples), ambiguous=False
            )
        )
    if all(_is_ten_digit_integer(value) for value in samples):
        out.append(
            FormatCandidate(
                format="epoch_seconds_or_hour_bucket",
                kind="integer",
                matched_count=len(samples),
                ambiguous=True,
            )
        )
    return tuple(out)


def detect_time_formats(profile: ColumnProfile) -> tuple[FormatCandidate, ...]:
    """Detect supported time-parse candidates from a bounded column profile.

    Native date/timestamp types produce no FormatCandidate (they are native).
    String columns try a fixed set of strptime formats; integer columns try
    YYYYMMDD, epoch-millis, and an ambiguous 10-digit encoding.

    Args:
        profile: Bounded column profile with sample values.

    Returns:
        A tuple of ``FormatCandidate`` records.
    """
    samples = tuple(value for value in profile.sample_values if value is not None)
    if not samples:
        return ()
    if profile.type_family == "string":
        out: list[FormatCandidate] = []
        for fmt, _category in _STRING_TIME_FORMATS:
            matched = _count_strptime_matches(samples, fmt)
            if matched == len(samples):
                out.append(
                    FormatCandidate(
                        format=fmt, kind="string", matched_count=matched, ambiguous=False
                    )
                )
        return tuple(out)
    if profile.type_family == "integer":
        return _detect_integer_time_formats(samples)
    return ()


def _coerce_time_bound(value: object) -> str | int | datetime.datetime | None:
    if value is None:
        return None
    if isinstance(value, (str, int, datetime.datetime)):
        return value
    return str(value)


def time_column_rules(
    profile: ColumnProfile,
    formats: tuple[FormatCandidate, ...],
    partition_aligned: bool,
) -> tuple[DiscoverySignal | DiscoveryIssue, ...]:
    """Candidate-scope time-dimension rules for one column profile."""
    out: list[DiscoverySignal | DiscoveryIssue] = []
    if profile.type_family == "date":
        out.append(
            DiscoverySignal(
                rule_id="time_native_date",
                kind="time_dimension",
                subject=profile.name,
                evidence=_ev(("type_family", "date")),
            )
        )
    elif profile.type_family == "timestamp":
        out.append(
            DiscoverySignal(
                rule_id="time_native_timestamp",
                kind="time_dimension",
                subject=profile.name,
                evidence=_ev(("type_family", "timestamp")),
            )
        )

    date_bearing_string = False
    hour_only = False
    for candidate in formats:
        if candidate.kind == "string":
            if "%Y" in candidate.format:
                date_bearing_string = True
                out.append(
                    DiscoverySignal(
                        rule_id="time_string_parse_candidate",
                        kind="time_dimension",
                        subject=profile.name,
                        evidence=_ev(
                            ("format", candidate.format),
                            ("matched_count", candidate.matched_count),
                        ),
                    )
                )
            elif "%H" in candidate.format:
                hour_only = True
        elif candidate.kind == "integer":
            if candidate.ambiguous:
                out.append(
                    DiscoveryIssue(
                        rule_id="time_integer_parse_ambiguous",
                        kind="time_dimension",
                        severity="warning",
                        subject=profile.name,
                        message="sampled integer values match multiple plausible time encodings",
                        evidence=_ev(
                            ("format", candidate.format),
                            ("matched_count", candidate.matched_count),
                        ),
                    )
                )
            else:
                out.append(
                    DiscoverySignal(
                        rule_id="time_integer_parse_candidate",
                        kind="time_dimension",
                        subject=profile.name,
                        evidence=_ev(
                            ("format", candidate.format),
                            ("matched_count", candidate.matched_count),
                        ),
                    )
                )

    if partition_aligned:
        out.append(
            DiscoverySignal(
                rule_id="time_partition_aligned",
                kind="time_dimension",
                subject=profile.name,
                evidence=_ev(("partition_column", profile.name)),
            )
        )

    native_temporal = profile.type_family in ("date", "timestamp")
    has_candidate = (
        native_temporal
        or date_bearing_string
        or any(candidate.kind == "integer" for candidate in formats)
    )
    if hour_only and not date_bearing_string and not native_temporal:
        matched = ",".join(
            candidate.format
            for candidate in formats
            if candidate.kind == "string" and "%H" in candidate.format
        )
        out.append(
            DiscoveryIssue(
                rule_id="time_ambiguous_hour_only",
                kind="time_dimension",
                severity="blocker",
                subject=profile.name,
                message="sampled values match an hour-only format that cannot identify a date",
                evidence=_ev(("matched_formats", matched)),
            )
        )
    if not native_temporal and not has_candidate and not hour_only:
        out.append(
            DiscoveryIssue(
                rule_id="time_no_parse_candidate",
                kind="time_dimension",
                severity="warning",
                subject=profile.name,
                message="no native temporal type and no supported sampled format",
                evidence=_ev(("data_type", profile.data_type)),
            )
        )
    return tuple(out)


def build_time_dimension_result(
    *,
    datasource: DatasourceRef,
    source: TableSource,
    table_metadata: TableMetadata | None,
    scan: ScanReport,
    scope: ScanScope,
    candidate_profiles: tuple[ColumnProfile, ...],
) -> TimeDimensionDiscoveryResult:
    """Build a TimeDimensionDiscoveryResult from scan + column profiles.

    Each candidate carries its detected formats, typed value range, partition
    alignment, and ``time_column_rules`` signals/issues. Result-scope issues
    come from ``scan_rules`` (with outcome), ``metadata_rules``, and
    ``column_limit_rules``.
    """
    outcome = resolve_partition(table_metadata, scope)
    result_issues: tuple[DiscoveryIssue, ...] = scan_rules(scan, scope, outcome)
    if table_metadata is not None:
        result_issues = result_issues + metadata_rules(table_metadata)
    result_issues = result_issues + column_limit_rules(scope, len(candidate_profiles))

    partition_names = (
        {partition.name for partition in table_metadata.partitions}
        if table_metadata is not None
        else set()
    )
    candidates: list[TimeColumnDiscoveryCandidate] = []
    for profile in candidate_profiles:
        formats = detect_time_formats(profile)
        aligned = profile.name in partition_names
        signals, issues = _split(time_column_rules(profile, formats, aligned))
        candidates.append(
            TimeColumnDiscoveryCandidate(
                column=profile.name,
                profile=profile,
                detected_formats=formats,
                value_range=TimeValueRange(
                    lower=_coerce_time_bound(profile.min_value),
                    upper=_coerce_time_bound(profile.max_value),
                ),
                partition_aligned=aligned,
                signals=signals,
                issues=issues,
            )
        )
    return TimeDimensionDiscoveryResult(
        datasource=datasource,
        source=source,
        table_metadata=table_metadata,
        scan=scan,
        signals=(),
        issues=result_issues,
        judgment_targets=time_dimension_judgment_targets(),
        candidates=tuple(candidates),
    )


def relationship_rules(
    key_type_evidence: tuple[KeyTypeEvidence, ...],
    sampled_key_count: int,
    matched_key_count: int,
    max_rows_per_key: int,
) -> tuple[DiscoverySignal | DiscoveryIssue, ...]:
    """Candidate-scope relationship rules over key-type and match evidence."""
    out: list[DiscoverySignal | DiscoveryIssue] = []
    if key_type_evidence:
        from_family = next(
            (entry.type_family for entry in key_type_evidence if entry.side == "from"),
            "",
        )
        to_family = next(
            (entry.type_family for entry in key_type_evidence if entry.side == "to"),
            "",
        )
        out.append(
            DiscoverySignal(
                rule_id="relationship_key_type_evidence",
                kind="relationship",
                subject="keys",
                evidence=_ev(("from_family", from_family), ("to_family", to_family)),
            )
        )
        families = {entry.type_family for entry in key_type_evidence}
        if len(families) > 1:
            out.append(
                DiscoveryIssue(
                    rule_id="relationship_key_type_mismatch_observed",
                    kind="relationship",
                    severity="warning",
                    subject="keys",
                    message="left and right key type families differ",
                    evidence=_ev(("families", ",".join(sorted(families)))),
                )
            )
    if sampled_key_count > 0:
        rate = matched_key_count / sampled_key_count
        out.append(
            DiscoverySignal(
                rule_id="relationship_match_rate",
                kind="relationship",
                subject="keys",
                evidence=_ev(
                    ("sampled", sampled_key_count),
                    ("matched", matched_key_count),
                    ("rate", rate),
                ),
            )
        )
        if matched_key_count == 0:
            out.append(
                DiscoveryIssue(
                    rule_id="relationship_no_matches_sampled",
                    kind="relationship",
                    severity="warning",
                    subject="keys",
                    message="sampled keys produced no matches on the to-side",
                    evidence=_ev(("sampled", sampled_key_count)),
                )
            )
    if max_rows_per_key > 1:
        out.append(
            DiscoveryIssue(
                rule_id="relationship_fanout_observed",
                kind="relationship",
                severity="warning",
                subject="keys",
                message="sampled key maps to more than one right-side row",
                evidence=_ev(("max_rows_per_key", max_rows_per_key)),
            )
        )
    return tuple(out)


def build_relationship_result(
    *,
    from_side: JoinSide,
    to_side: JoinSide,
    key_type_evidence: tuple[KeyTypeEvidence, ...],
    sampled_key_count: int,
    matched_key_count: int,
    max_rows_per_key: int,
    avg_rows_per_key: float,
    cardinality_evidence: Literal["one_to_one", "many_to_one", "indeterminate"],
    from_scan: ScanReport,
    to_scan: ScanReport,
) -> RelationshipDiscoveryResult:
    """Build a RelationshipDiscoveryResult from join-key probe evidence.

    Candidate-scope rules (key type, match rate, no-matches, fanout) live on
    the evidence object. The result-scope ``relationship_probe_truncated``
    issue is computed here from the two scan reports.
    """
    signals, issues = _split(
        relationship_rules(
            key_type_evidence,
            sampled_key_count,
            matched_key_count,
            max_rows_per_key,
        )
    )
    match_rate = matched_key_count / sampled_key_count if sampled_key_count > 0 else 0.0
    evidence = RelationshipDiscoveryEvidence(
        from_side=from_side,
        to_side=to_side,
        key_type_evidence=key_type_evidence,
        sampled_key_count=sampled_key_count,
        matched_key_count=matched_key_count,
        match_rate=match_rate,
        max_rows_per_key=max_rows_per_key,
        avg_rows_per_key=avg_rows_per_key,
        cardinality_evidence=cardinality_evidence,
        from_scan=from_scan,
        to_scan=to_scan,
        signals=signals,
        issues=issues,
    )
    result_issues: list[DiscoveryIssue] = []
    if from_scan.truncated or to_scan.truncated:
        result_issues.append(
            DiscoveryIssue(
                rule_id="relationship_probe_truncated",
                kind="relationship",
                severity="warning",
                subject="scan",
                message="join-key probe scan was truncated",
                evidence=_ev(
                    ("from_truncated", from_scan.truncated),
                    ("to_truncated", to_scan.truncated),
                ),
            )
        )
    return RelationshipDiscoveryResult(
        evidence=evidence,
        judgment_targets=relationship_judgment_targets(),
        signals=(),
        issues=tuple(result_issues),
    )
