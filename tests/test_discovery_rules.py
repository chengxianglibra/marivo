"""Tests for discovery judgment-target templates and the rule engine."""

from __future__ import annotations

from typing import Literal

from marivo.datasource.authoring import ref
from marivo.datasource.discovery import (
    DimensionDiscoveryResult,
    DiscoveryIssue,
    DiscoverySignal,
    MeasureDiscoveryResult,
)
from marivo.datasource.discovery_rules import (
    build_dimension_result,
    build_measure_result,
    dimension_column_rules,
    dimension_value_rules,
    measure_column_rules,
    scan_rules,
    time_column_rules,
)
from marivo.datasource.scan import ColumnProfile, ScanReport, ScanScope, table


def _profile(
    name: str,
    data_type: str,
    distinct: int = 5,
    null_count: int = 0,
    empty_count: int = 0,
    read_status: Literal["readable", "not_found", "unreadable"] = "readable",
) -> ColumnProfile:
    return ColumnProfile(
        name=name,
        data_type=data_type,
        nullable=True,
        comment=None,
        null_count=null_count,
        empty_count=empty_count,
        distinct_count=distinct,
        top_values=(),
        sample_values=(),
        min_value=None,
        max_value=None,
        read_status=read_status,
    )


def _scan(truncated: bool = False, rows: int = 10) -> ScanReport:
    return ScanReport(
        partition_used=None,
        partition_resolution="none",
        rows_scanned=rows,
        columns_scanned=("a",),
        truncated=truncated,
        elapsed_seconds=0.1,
        warnings=(),
    )


def test_scan_truncated_emits_warning() -> None:
    issues = scan_rules(_scan(truncated=True), ScanScope())
    ids = [i.rule_id for i in issues]
    assert "discovery_scan_truncated" in ids
    assert all(i.severity == "warning" for i in issues)


def test_unpruned_scope_emits_info_issue() -> None:
    issues = scan_rules(_scan(), ScanScope(partition=None))
    ids = [i.rule_id for i in issues]
    assert "discovery_unpruned_scan" in ids
    info = next(i for i in issues if i.rule_id == "discovery_unpruned_scan")
    assert info.severity == "info"


def test_low_cardinality_signal() -> None:
    out = dimension_column_rules(_profile("status", "VARCHAR", distinct=2))
    ids = [s.rule_id for s in out if isinstance(s, DiscoverySignal)]
    assert "dimension_low_cardinality" in ids


def test_nullable_info_issue() -> None:
    out = dimension_column_rules(_profile("status", "VARCHAR", null_count=3))
    issues = [i for i in out if isinstance(i, DiscoveryIssue)]
    assert any(i.rule_id == "dimension_nullable" and i.severity == "info" for i in issues)


def test_empty_values_present_warning() -> None:
    out = dimension_column_rules(_profile("status", "VARCHAR", empty_count=2))
    issues = [i for i in out if isinstance(i, DiscoveryIssue)]
    assert any(i.rule_id == "dimension_empty_values_present" for i in issues)


def test_measure_numeric_signal_and_unsupported_type_blocker() -> None:
    numeric = measure_column_rules(_profile("amount", "DOUBLE"))
    assert "measure_numeric_type" in [s.rule_id for s in numeric if isinstance(s, DiscoverySignal)]
    text = measure_column_rules(_profile("label", "VARCHAR"))
    blockers = [i for i in text if isinstance(i, DiscoveryIssue) and i.severity == "blocker"]
    assert any(i.rule_id == "unsupported_type" for i in blockers)
    assert not any(i.rule_id == "measure_non_numeric_type" for i in blockers)


def test_measure_missing_column_emits_column_not_found_blocker() -> None:
    out = measure_column_rules(_profile("elapsed_time_millis", "UNKNOWN", read_status="not_found"))
    blockers = [i for i in out if isinstance(i, DiscoveryIssue) and i.severity == "blocker"]

    assert [i.rule_id for i in blockers] == ["column_not_found"]
    assert not any(i.rule_id == "unsupported_type" for i in blockers)
    assert not any(
        isinstance(i, DiscoverySignal) and i.rule_id == "measure_numeric_type" for i in out
    )


def test_measure_unreadable_column_emits_unreadable_column_blocker() -> None:
    out = measure_column_rules(_profile("amount", "DOUBLE", read_status="unreadable"))
    blockers = [i for i in out if isinstance(i, DiscoveryIssue) and i.severity == "blocker"]

    assert [i.rule_id for i in blockers] == ["unreadable_column"]
    assert not any(i.rule_id == "unsupported_type" for i in blockers)
    assert not any(
        isinstance(i, DiscoverySignal) and i.rule_id == "measure_numeric_type" for i in out
    )


def test_dimension_values_truncated_when_not_complete() -> None:
    from marivo.datasource.discovery import DimensionValueFact

    values = (DimensionValueFact(value="open", count=3),)
    out = dimension_value_rules(values, complete=False)
    ids = [i.rule_id for i in out if isinstance(i, DiscoveryIssue)]
    assert "dimension_values_truncated" in ids


def test_dimension_values_complete_emits_no_truncated_issue() -> None:
    from marivo.datasource.discovery import DimensionValueFact

    values = (DimensionValueFact(value="open", count=3),)
    out = dimension_value_rules(values, complete=True)
    ids = [i.rule_id for i in out if isinstance(i, DiscoveryIssue)]
    assert "dimension_values_truncated" not in ids


def test_build_dimension_result_wires_scan_rules_and_columns() -> None:
    profile = _profile("status", "VARCHAR", distinct=2, null_count=1)
    result = build_dimension_result(
        datasource=ref("warehouse"),
        source=table("orders"),
        table_metadata=None,
        scan=_scan(truncated=True),
        scope=ScanScope(),
        column_profiles=(profile,),
    )
    assert isinstance(result, DimensionDiscoveryResult)
    assert any(i.rule_id == "discovery_scan_truncated" for i in result.issues)
    assert any(s.rule_id == "dimension_low_cardinality" for s in result.columns[0].signals)
    assert not hasattr(result, "judgment_targets")
    assert not hasattr(result, "candidates")
    result_signal_ids = {s.rule_id for s in result.signals}
    column_signal_ids = {s.rule_id for s in result.columns[0].signals}
    assert result_signal_ids.isdisjoint(column_signal_ids)


def test_build_measure_result_marks_non_numeric_blocker() -> None:
    profile = _profile("label", "VARCHAR")
    result = build_measure_result(
        datasource=ref("warehouse"),
        source=table("orders"),
        table_metadata=None,
        scan=_scan(),
        scope=ScanScope(),
        column_profiles=(profile,),
    )
    assert isinstance(result, MeasureDiscoveryResult)
    blocker = [i for i in result.columns[0].issues if i.severity == "blocker"]
    assert any(i.rule_id == "unsupported_type" for i in blocker)
    assert not hasattr(result, "judgment_targets")


def test_resolve_partition_explicit_unpruned_and_unresolved() -> None:
    from marivo.datasource.discovery_rules import resolve_partition
    from marivo.datasource.metadata import PartitionMetadata, TableMetadata

    assert resolve_partition(None, ScanScope(partition=None)).resolution == "unpruned"
    assert resolve_partition(None, ScanScope(partition=None)).unresolved is False
    # latest with no partition metadata falls back to unpruned, not unresolved
    assert resolve_partition(None, ScanScope()).resolution == "unpruned"
    assert resolve_partition(None, ScanScope()).unresolved is False
    explicit = resolve_partition(None, ScanScope(partition={"dt": "20260101"}))
    assert explicit.resolution == "explicit"
    assert explicit.partition_used == {"dt": "20260101"}
    # latest with partition metadata that cannot be resolved is unresolved
    metadata = TableMetadata(
        datasource="wh",
        table="events",
        database=None,
        backend_type="clickhouse",
        comment=None,
        columns=(),
        partitions=(PartitionMetadata(name="dt"),),
        warnings=(),
    )
    outcome = resolve_partition(metadata, ScanScope())
    assert outcome.resolution == "latest"
    assert outcome.unresolved is True


def test_scan_rules_emit_latest_partition_unresolved() -> None:
    from marivo.datasource.discovery_rules import resolve_partition
    from marivo.datasource.metadata import PartitionMetadata, TableMetadata

    metadata = TableMetadata(
        datasource="wh",
        table="events",
        database=None,
        backend_type="clickhouse",
        comment=None,
        columns=(),
        partitions=(PartitionMetadata(name="dt"),),
        warnings=(),
    )
    outcome = resolve_partition(metadata, ScanScope())
    issues = scan_rules(_scan(), ScanScope(), outcome=outcome)
    ids = [i.rule_id for i in issues]
    assert "discovery_latest_partition_unresolved" in ids
    unresolved = next(i for i in issues if i.rule_id == "discovery_latest_partition_unresolved")
    assert unresolved.severity == "warning"


def test_scan_rules_without_outcome_preserves_phase1_behavior() -> None:
    # No outcome passed: only scan-truncated and unpruned-scan fire as in Plan 1.
    truncated = scan_rules(_scan(truncated=True), ScanScope())
    assert [i.rule_id for i in truncated] == ["discovery_scan_truncated"]
    assert all(i.severity == "warning" for i in truncated)
    unpruned = scan_rules(_scan(), ScanScope(partition=None))
    assert "discovery_unpruned_scan" in [i.rule_id for i in unpruned]


def test_metadata_rules_forward_warnings() -> None:
    from marivo.datasource.discovery_rules import metadata_rules
    from marivo.datasource.metadata import (
        ColumnMetadata,
        MetadataWarning,
        TableMetadata,
    )

    metadata = TableMetadata(
        datasource="wh",
        table="orders",
        database=None,
        backend_type="duckdb",
        comment=None,
        columns=(
            ColumnMetadata(
                name="a", type="INTEGER", nullable=False, comment=None, ordinal_position=1
            ),
        ),
        partitions=(),
        warnings=(
            MetadataWarning(kind="partitions_unavailable", message="no partitions"),
            MetadataWarning(kind="metadata_query_failed", message="boom"),
        ),
    )
    issues = metadata_rules(metadata)
    assert [i.rule_id for i in issues] == [
        "discovery_metadata_warning",
        "discovery_metadata_warning",
    ]
    severities = {i.message: i.severity for i in issues}
    assert severities["no partitions"] == "info"
    assert severities["boom"] == "warning"


def test_column_limit_rules_emit_only_when_truncated() -> None:
    from marivo.datasource.discovery_rules import column_limit_rules

    truncated = column_limit_rules(ScanScope(max_columns=2), 5)
    assert len(truncated) == 1
    assert truncated[0].rule_id == "discovery_column_limit_truncated"
    assert truncated[0].severity == "warning"
    assert column_limit_rules(ScanScope(max_columns=10), 5) == ()


def _enriched_profile(
    name: str,
    data_type: str = "VARCHAR",
    *,
    type_family: str = "unknown",
    distinct: int = 5,
    null_count: int = 0,
    distinct_ratio: float | None = None,
    min_length: int | None = None,
    comment: str | None = None,
    negative_count: int = 0,
    zero_count: int = 0,
    non_null_count: int = 5,
) -> ColumnProfile:
    return ColumnProfile(
        name=name,
        data_type=data_type,
        nullable=True,
        comment=comment,
        null_count=null_count,
        empty_count=0,
        distinct_count=distinct,
        top_values=(),
        sample_values=(),
        min_value=None,
        max_value=None,
        non_null_count=non_null_count,
        distinct_ratio=distinct_ratio,
        type_family=type_family,
        min_length=min_length,
        negative_count=negative_count,
        zero_count=zero_count,
    )


def test_dimension_high_cardinality_signal() -> None:
    out = dimension_column_rules(
        _enriched_profile(
            "user_id",
            "VARCHAR",
            type_family="string",
            distinct=100,
            distinct_ratio=0.95,
            min_length=8,
        )
    )
    ids = [s.rule_id for s in out if isinstance(s, DiscoverySignal)]
    assert "dimension_high_cardinality" in ids
    assert "dimension_text_shape" in ids


def test_dimension_boolean_like_for_two_valued_column() -> None:
    out = dimension_column_rules(_enriched_profile("is_active", "VARCHAR", distinct=2))
    ids = [s.rule_id for s in out if isinstance(s, DiscoverySignal)]
    assert "dimension_boolean_like" in ids


def test_dimension_identifier_shape_for_id_column() -> None:
    out = dimension_column_rules(
        _enriched_profile(
            "customer_id", "BIGINT", type_family="integer", distinct=100, distinct_ratio=1.0
        )
    )
    ids = [s.rule_id for s in out if isinstance(s, DiscoverySignal)]
    assert "dimension_identifier_shape" in ids


def test_dimension_shadowing_column_emits_authoring_warning() -> None:
    out = dimension_column_rules(_enriched_profile("schema", "VARCHAR", type_family="string"))
    issues = [i for i in out if isinstance(i, DiscoveryIssue)]

    warning = next(i for i in issues if i.rule_id == "authoring_ibis_attribute_shadowing")
    assert warning.severity == "warning"
    assert warning.subject == "schema"
    assert 'table["schema"]' in warning.message


def test_time_dimension_shadowing_column_emits_authoring_warning() -> None:
    out = time_column_rules(_enriched_profile("count", "BIGINT", type_family="integer"), (), False)
    issues = [i for i in out if isinstance(i, DiscoveryIssue)]

    warning = next(i for i in issues if i.rule_id == "authoring_ibis_attribute_shadowing")
    assert warning.severity == "warning"
    assert warning.subject == "count"
    assert 'table["count"]' in warning.message


def test_measure_shadowing_column_emits_authoring_warning() -> None:
    out = measure_column_rules(_enriched_profile("info", "DOUBLE", type_family="numeric"))
    issues = [i for i in out if isinstance(i, DiscoveryIssue)]

    warning = next(i for i in issues if i.rule_id == "authoring_ibis_attribute_shadowing")
    assert warning.severity == "warning"
    assert warning.subject == "info"
    assert 'table["info"]' in warning.message


def test_measure_negative_zero_and_unit_token_signals() -> None:
    out = measure_column_rules(
        _enriched_profile(
            "amount",
            "DOUBLE",
            type_family="numeric",
            negative_count=2,
            zero_count=1,
            null_count=1,
            comment="Gross order amount in USD",
        )
    )
    ids = [s.rule_id for s in out if isinstance(s, DiscoverySignal)]
    assert "measure_numeric_type" in ids
    assert "measure_negative_values_present" in ids
    assert "measure_zero_values_present" in ids
    assert "measure_unit_token_observed" in ids
    issues = [i for i in out if isinstance(i, DiscoveryIssue)]
    assert any(i.rule_id == "measure_nullable" and i.severity == "info" for i in issues)


def test_measure_unit_token_absent_when_no_token() -> None:
    out = measure_column_rules(
        _enriched_profile("amount", "DOUBLE", type_family="numeric", comment="some value")
    )
    ids = [s.rule_id for s in out if isinstance(s, DiscoverySignal)]
    assert "measure_unit_token_observed" not in ids
