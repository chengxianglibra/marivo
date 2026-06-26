"""Tests for time-dimension discovery rules and build_time_dimension_result."""

from __future__ import annotations

from marivo.datasource.authoring import ref
from marivo.datasource.discovery import (
    TimeColumnDiscovery,
    TimeDimensionDiscoveryResult,
)
from marivo.datasource.discovery_rules import (
    build_time_dimension_result,
    detect_time_formats,
    time_column_rules,
)
from marivo.datasource.metadata import PartitionMetadata, TableMetadata
from marivo.datasource.scan import ColumnProfile, ScanReport, ScanScope, table


def _profile(
    name: str,
    data_type: str,
    *,
    type_family: str,
    samples=(),
) -> ColumnProfile:
    return ColumnProfile(
        name=name,
        data_type=data_type,
        nullable=True,
        comment=None,
        null_count=0,
        empty_count=0,
        distinct_count=len(samples),
        top_values=(),
        sample_values=tuple(samples),
        min_value=None,
        max_value=None,
        non_null_count=len(samples),
        type_family=type_family,
    )


def _scan(rows: int = 3) -> ScanReport:
    return ScanReport(
        partition_used=None,
        partition_resolution="none",
        rows_scanned=rows,
        columns_scanned=("a",),
        truncated=False,
        elapsed_seconds=0.1,
        warnings=(),
    )


def test_detect_string_date_format() -> None:
    profile = _profile(
        "dt", "VARCHAR", type_family="string", samples=("2026-01-01", "2026-01-02", "2026-01-03")
    )
    formats = detect_time_formats(profile)
    assert any(f.format == "%Y-%m-%d" and f.kind == "string" for f in formats)


def test_detect_integer_yyyymmdd_and_epoch_millis() -> None:
    yyyymmdd = _profile("dt", "BIGINT", type_family="integer", samples=(20260101, 20260102))
    assert any(f.format == "%Y%m%d" and not f.ambiguous for f in detect_time_formats(yyyymmdd))
    epoch_ms = _profile(
        "ts", "BIGINT", type_family="integer", samples=(1700000000000, 1700000001000)
    )
    assert any(
        f.format == "epoch_millis" and not f.ambiguous for f in detect_time_formats(epoch_ms)
    )


def test_integer_10_digit_is_ambiguous() -> None:
    profile = _profile("ts", "BIGINT", type_family="integer", samples=(1700000000, 1700000001))
    formats = detect_time_formats(profile)
    assert any(f.ambiguous for f in formats)


def test_time_native_date_and_timestamp_signals() -> None:
    date_out = time_column_rules(_profile("d", "DATE", type_family="date"), (), False)
    assert any(getattr(item, "rule_id", None) == "time_native_date" for item in date_out)
    ts_out = time_column_rules(_profile("ts", "TIMESTAMP", type_family="timestamp"), (), False)
    assert any(getattr(item, "rule_id", None) == "time_native_timestamp" for item in ts_out)


def test_time_string_parse_candidate_signal() -> None:
    profile = _profile("dt", "VARCHAR", type_family="string", samples=("2026-01-01", "2026-01-02"))
    formats = detect_time_formats(profile)
    out = time_column_rules(profile, formats, False)
    assert any(getattr(item, "rule_id", None) == "time_string_parse_candidate" for item in out)


def test_time_integer_ambiguous_warning() -> None:
    profile = _profile("ts", "BIGINT", type_family="integer", samples=(1700000000, 1700000001))
    formats = detect_time_formats(profile)
    out = time_column_rules(profile, formats, False)
    issues = [i for i in out if hasattr(i, "severity")]
    assert any(getattr(i, "rule_id", None) == "time_integer_parse_ambiguous" for i in issues)


def test_time_no_parse_candidate_warning() -> None:
    profile = _profile("label", "VARCHAR", type_family="string", samples=("alpha", "beta"))
    formats = detect_time_formats(profile)
    out = time_column_rules(profile, formats, False)
    issues = [i for i in out if hasattr(i, "severity")]
    assert any(getattr(i, "rule_id", None) == "time_no_parse_candidate" for i in issues)


def test_time_partition_aligned_signal() -> None:
    profile = _profile("dt", "DATE", type_family="date")
    out = time_column_rules(profile, (), True)
    assert any(getattr(item, "rule_id", None) == "time_partition_aligned" for item in out)


def test_time_ambiguous_hour_only_blocker() -> None:
    profile = _profile("hh", "VARCHAR", type_family="string", samples=("12:00:00", "13:00:00"))
    formats = detect_time_formats(profile)
    out = time_column_rules(profile, formats, False)
    blockers = [
        i for i in out if hasattr(i, "severity") and getattr(i, "severity", None) == "blocker"
    ]
    assert any(getattr(i, "rule_id", None) == "time_ambiguous_hour_only" for i in blockers)


def test_build_time_dimension_result_wires_columns() -> None:
    metadata = TableMetadata(
        datasource="wh",
        table="events",
        database=None,
        backend_type="duckdb",
        comment=None,
        columns=(),
        partitions=(PartitionMetadata(name="dt", type="date"),),
        warnings=(),
    )
    profile = _profile("dt", "DATE", type_family="date")
    result = build_time_dimension_result(
        datasource=ref("warehouse"),
        source=table("events"),
        table_metadata=metadata,
        scan=_scan(),
        scope=ScanScope(),
        column_profiles=(profile,),
    )
    assert isinstance(result, TimeDimensionDiscoveryResult)
    column = result.columns[0]
    assert isinstance(column, TimeColumnDiscovery)
    assert column.partition_aligned is True
    assert any(getattr(s, "rule_id", None) == "time_partition_aligned" for s in column.signals)
    assert not hasattr(result, "judgment_targets")
    assert not hasattr(result, "candidates")


def test_build_time_dimension_result_value_range_typed() -> None:
    profile = ColumnProfile(
        name="dt",
        data_type="VARCHAR",
        nullable=True,
        comment=None,
        null_count=0,
        empty_count=0,
        distinct_count=2,
        top_values=(),
        sample_values=("2026-01-01", "2026-01-02"),
        min_value="2026-01-01",
        max_value="2026-01-02",
        non_null_count=2,
        type_family="string",
    )
    result = build_time_dimension_result(
        datasource=ref("warehouse"),
        source=table("events"),
        table_metadata=None,
        scan=_scan(),
        scope=ScanScope(partition=None),
        column_profiles=(profile,),
    )
    column = result.columns[0]
    assert column.value_range.lower == "2026-01-01"
    assert column.value_range.upper == "2026-01-02"
