"""Tests for the discovery evidence model, scope helpers, and rule engine."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from marivo.datasource.authoring import ref
from marivo.datasource.discovery import (
    ColumnDiscovery,
    DimensionValueDiscoveryResult,
    DimensionValueFact,
    DiscoveryEvidenceEntry,
    DiscoveryIssue,
    DiscoverySignal,
    EntityDiscoveryResult,
    FormatCandidate,
    KeyTypeEvidence,
    MeasureDiscoveryResult,
    PartitionInspectionResult,
    PrimaryKeyCandidate,
    RawSqlResult,
    RelationshipDiscoveryEvidence,
    RelationshipDiscoveryResult,
    TimeColumnDiscovery,
    TimeDimensionDiscoveryResult,
    TimeValueRange,
)
from marivo.datasource.scan import (
    ColumnProfile,
    JoinSide,
    ScanReport,
    TableSource,
    partition,
    table,
    unpruned,
)
from marivo.render import AgentResult, RenderableResult


def test_table_source_is_entity_source_union() -> None:
    from marivo.datasource.ir import CsvSourceIR, JsonSourceIR, ParquetSourceIR, TableSourceIR

    # TableSource is the union alias; each concrete IR is an instance of it
    # at runtime (isinstance checks against the alias are not valid, but the
    # alias must equal the union type object).
    assert TableSource == TableSourceIR | ParquetSourceIR | CsvSourceIR | JsonSourceIR


def test_partition_records_explicit_values() -> None:
    scope = partition({"dt": "20260612"}, max_rows=50)
    assert scope.partition == {"dt": "20260612"}
    assert scope.max_rows == 50


def test_unpruned_sets_partition_none() -> None:
    scope = unpruned()
    assert scope.partition is None


def _scan_report() -> ScanReport:
    return ScanReport(
        partition_used=None,
        partition_resolution="none",
        rows_scanned=10,
        columns_scanned=("a",),
        truncated=False,
        elapsed_seconds=0.1,
        warnings=(),
    )


def test_evidence_entry_is_frozen_scalar() -> None:
    entry = DiscoveryEvidenceEntry(key="distinct_count", value=8)
    assert entry.key == "distinct_count"
    assert entry.value == 8
    with pytest.raises(FrozenInstanceError):
        setattr(entry, "key", "other")  # noqa: B010 - exercising frozen guard


def test_signal_and_issue_carry_evidence_tuples() -> None:
    sig = DiscoverySignal(
        rule_id="dimension_low_cardinality",
        kind="dimension",
        subject="status",
        evidence=(DiscoveryEvidenceEntry(key="distinct_count", value=2),),
    )
    assert sig.evidence[0].value == 2
    issue = DiscoveryIssue(
        rule_id="dimension_nullable",
        kind="dimension",
        severity="info",
        subject="status",
        message="column contains sampled nulls",
        evidence=(DiscoveryEvidenceEntry(key="null_count", value=3),),
    )
    assert issue.severity == "info"


def test_discovery_module_no_longer_exports_judgment_targets() -> None:
    import marivo.datasource as md
    import marivo.datasource.discovery as discovery

    assert not hasattr(discovery, "SemanticJudgmentTarget")
    assert "SemanticJudgmentTarget" not in md.__all__


def test_time_value_range_typed_bounds() -> None:
    rng = TimeValueRange(lower="2026-01-01", upper=None)
    assert rng.lower == "2026-01-01"
    assert rng.upper is None


def test_dimension_value_fact_value_is_scalar_union() -> None:
    fact = DimensionValueFact(value="open", count=7)
    assert fact.value == "open"
    assert fact.count == 7


def _profile(name: str, data_type: str = "VARCHAR") -> ColumnProfile:
    return ColumnProfile(
        name=name,
        data_type=data_type,
        nullable=True,
        comment=None,
        null_count=0,
        empty_count=0,
        distinct_count=4,
        top_values=(),
        sample_values=(),
        min_value=None,
        max_value=None,
    )


def _rich_profile(name: str, data_type: str = "VARCHAR") -> ColumnProfile:
    return ColumnProfile(
        name=name,
        data_type=data_type,
        nullable=False,
        comment="business key",
        null_count=1,
        empty_count=0,
        distinct_count=4,
        top_values=(("A", 2),),
        sample_values=("A", "B"),
        min_value="A",
        max_value="Z",
        non_null_count=4,
        distinct_ratio=1.0,
        top_value_concentration=0.5,
        negative_count=0,
        zero_count=0,
        min_length=1,
        max_length=4,
        avg_length=2.5,
        type_family="string",
    )


def _measure_result() -> MeasureDiscoveryResult:
    column = ColumnDiscovery(
        column="amount",
        profile=_profile("amount", "DOUBLE"),
        signals=(
            DiscoverySignal(
                rule_id="measure_numeric_type",
                kind="measure",
                subject="amount",
                evidence=(DiscoveryEvidenceEntry(key="type_family", value="numeric"),),
            ),
        ),
        issues=(),
    )
    return MeasureDiscoveryResult(
        datasource=ref("warehouse"),
        source=table("orders"),
        table_metadata=None,
        scan=_scan_report(),
        signals=(),
        issues=(),
        columns=(column,),
    )


@pytest.mark.parametrize(
    "result_factory",
    [
        lambda: EntityDiscoveryResult(
            datasource=ref("warehouse"),
            source=table("orders"),
            table_metadata=None,
            scan=_scan_report(),
            table="orders",
            primary_key_evidence=(),
            time_like_columns=(),
            partition_columns=(),
            column_profiles=(),
            signals=(),
            issues=(),
        ),
        _measure_result,
        lambda: DimensionValueDiscoveryResult(
            datasource=ref("warehouse"),
            source=table("orders"),
            column="status",
            values=(DimensionValueFact(value="open", count=3),),
            complete=True,
            scan=_scan_report(),
            signals=(),
            issues=(),
        ),
    ],
)
def test_result_conforms_to_agent_result(result_factory) -> None:
    result = result_factory()
    assert isinstance(result, AgentResult)
    r = repr(result)
    assert "\n" not in r
    assert len(r) <= 200
    assert type(result).__name__ in r
    rendered = result.render()
    assert isinstance(rendered, str)
    assert not rendered.endswith("\n")
    assert result.show() is None


def test_nested_discovery_evidence_conforms_to_agent_result() -> None:
    profile = _rich_profile("order_id", "VARCHAR")
    pk = PrimaryKeyCandidate(
        column="order_id",
        source="sampled_unique",
        evidence=(DiscoveryEvidenceEntry(key="distinct_count", value=4),),
    )
    fmt = FormatCandidate(format="%Y-%m-%d", kind="string", matched_count=4, ambiguous=False)
    column = ColumnDiscovery(column="order_id", profile=profile, signals=(), issues=())
    time_column = TimeColumnDiscovery(
        column="created_at",
        profile=_rich_profile("created_at", "VARCHAR"),
        detected_formats=(fmt,),
        value_range=TimeValueRange(lower="2026-01-01", upper="2026-01-04"),
        partition_aligned=True,
        signals=(),
        issues=(),
    )
    relationship = RelationshipDiscoveryEvidence(
        from_side=JoinSide(ref("warehouse"), table("orders"), columns=("customer_id",)),
        to_side=JoinSide(ref("warehouse"), table("customers"), columns=("customer_id",)),
        key_type_evidence=(
            KeyTypeEvidence(
                side="from",
                column="customer_id",
                type_family="integer",
                data_type="BIGINT",
            ),
        ),
        sampled_key_count=4,
        matched_key_count=3,
        match_rate=0.75,
        max_rows_per_key=2,
        avg_rows_per_key=1.25,
        cardinality_evidence="many_to_one",
        from_scan=_scan_report(),
        to_scan=_scan_report(),
        signals=(),
        issues=(),
    )

    for obj in (profile, pk, fmt, column, time_column, relationship):
        assert isinstance(obj, AgentResult)
        rendered = obj.render()
        assert isinstance(rendered, str)
        assert not rendered.endswith("\n")
        assert obj.show() is None
        assert "\n" not in repr(obj)


def test_entity_render_includes_bounded_evidence_not_only_affordances() -> None:
    result = EntityDiscoveryResult(
        datasource=ref("warehouse"),
        source=table("orders"),
        table_metadata=None,
        scan=_scan_report(),
        table="orders",
        primary_key_evidence=(
            PrimaryKeyCandidate(
                column="order_id",
                source="sampled_unique",
                evidence=(DiscoveryEvidenceEntry(key="distinct_count", value=4),),
            ),
        ),
        time_like_columns=("created_at",),
        partition_columns=("dt",),
        column_profiles=(_rich_profile("order_id", "VARCHAR"), _rich_profile("created_at")),
        signals=(),
        issues=(),
    )

    rendered = result.render()

    assert "primary key evidence:" in rendered
    assert "order_id" in rendered
    assert "sampled_unique" in rendered
    assert "time-like columns: created_at" in rendered
    assert "partition columns: dt" in rendered
    assert "column profiles:" in rendered
    assert "distinct=4" in rendered
    assert "nulls=1" in rendered
    assert "available:" in rendered
    assert rendered.index("primary key evidence:") < rendered.index("available:")
    assert "judgment targets:" not in rendered


def test_time_dimension_render_includes_formats_range_and_partition_evidence() -> None:
    result = TimeDimensionDiscoveryResult(
        datasource=ref("warehouse"),
        source=table("orders"),
        table_metadata=None,
        scan=_scan_report(),
        signals=(),
        issues=(),
        columns=(
            TimeColumnDiscovery(
                column="created_at",
                profile=_rich_profile("created_at", "VARCHAR"),
                detected_formats=(
                    FormatCandidate(
                        format="%Y-%m-%d",
                        kind="string",
                        matched_count=4,
                        ambiguous=False,
                    ),
                ),
                value_range=TimeValueRange(lower="2026-01-01", upper="2026-01-04"),
                partition_aligned=True,
                signals=(),
                issues=(),
            ),
        ),
    )

    rendered = result.render()

    assert "time column evidence:" in rendered
    assert "created_at" in rendered
    assert "%Y-%m-%d" in rendered
    assert "range=2026-01-01..2026-01-04" in rendered
    assert "partition_aligned=True" in rendered
    assert rendered.index("time column evidence:") < rendered.index("available:")


def test_time_dimension_full_render_lists_all_columns() -> None:
    result = TimeDimensionDiscoveryResult(
        datasource=ref("warehouse"),
        source=table("orders"),
        table_metadata=None,
        scan=_scan_report(),
        signals=(),
        issues=(),
        columns=tuple(
            TimeColumnDiscovery(
                column=f"time_col_{i:02d}",
                profile=_rich_profile(f"time_col_{i:02d}", "VARCHAR"),
                detected_formats=(),
                value_range=TimeValueRange(lower="2026-01-01", upper="2026-01-04"),
                partition_aligned=False,
                signals=(),
                issues=(),
            )
            for i in range(1, 13)
        ),
    )

    rendered = result.render(max_output_bytes=None)

    assert "time_col_01" in rendered
    assert "time_col_12" in rendered
    assert "... 4 more" not in rendered


def test_measure_render_lists_columns_without_judgment_targets() -> None:
    result = _measure_result()
    rendered = result.render()

    assert "MeasureDiscoveryResult" in rendered
    assert "evidence_only" in rendered
    assert "amount" in rendered
    assert "distinct=" in rendered
    assert "nulls=" in rendered
    assert "measure_numeric_type" in rendered
    assert ".columns" not in rendered
    assert ".profile" not in rendered
    assert ".issues" not in rendered
    assert "judgment targets:" not in rendered
    assert ".judgment_targets" not in rendered
    assert ".candidates" not in rendered


def test_measure_render_includes_result_scope_issue_details() -> None:
    result = MeasureDiscoveryResult(
        datasource=ref("warehouse"),
        source=table("orders"),
        table_metadata=None,
        scan=_scan_report(),
        signals=(),
        issues=(
            DiscoveryIssue(
                rule_id="discovery_unpruned_scan",
                kind="measure",
                severity="warning",
                subject="orders",
                message="scan was explicitly unpruned",
                evidence=(DiscoveryEvidenceEntry(key="partition", value="none"),),
            ),
        ),
        columns=(),
    )

    rendered = result.render()

    assert "result issues:" in rendered
    assert "discovery_unpruned_scan" in rendered
    assert "scan was explicitly unpruned" in rendered
    assert "partition=none" in rendered


def test_dimension_values_full_render_lists_all_values() -> None:
    result = DimensionValueDiscoveryResult(
        datasource=ref("warehouse"),
        source=table("orders"),
        column="status",
        values=tuple(DimensionValueFact(value=f"value_{i:02d}", count=i) for i in range(1, 13)),
        complete=True,
        scan=_scan_report(),
        signals=(),
        issues=(),
    )

    rendered = result.render(max_output_bytes=None)

    assert "value_01 | 1" in rendered
    assert "value_12 | 12" in rendered
    assert "... 4 more" not in rendered


def test_relationship_result_full_render_lists_all_key_type_evidence() -> None:
    evidence = RelationshipDiscoveryEvidence(
        from_side=JoinSide(ref("warehouse"), table("orders"), columns=("customer_id",)),
        to_side=JoinSide(ref("warehouse"), table("customers"), columns=("customer_id",)),
        key_type_evidence=tuple(
            KeyTypeEvidence(
                side="from",
                column=f"key_col_{i:02d}",
                type_family="integer",
                data_type="BIGINT",
            )
            for i in range(1, 13)
        ),
        sampled_key_count=12,
        matched_key_count=12,
        match_rate=1.0,
        max_rows_per_key=1,
        avg_rows_per_key=1.0,
        cardinality_evidence="many_to_one",
        from_scan=_scan_report(),
        to_scan=_scan_report(),
        signals=(),
        issues=(),
    )
    result = RelationshipDiscoveryResult(evidence=evidence, signals=(), issues=())

    rendered = result.render(max_output_bytes=None)

    assert "key_col_01" in rendered
    assert "key_col_12" in rendered
    assert "... 4 more" not in rendered


def test_dimension_value_complete_invariant_when_no_truncated_issue() -> None:
    result = DimensionValueDiscoveryResult(
        datasource=ref("warehouse"),
        source=table("orders"),
        column="status",
        values=(),
        complete=True,
        scan=_scan_report(),
        signals=(),
        issues=(),
    )
    assert result.complete is True


def test_primary_key_candidate_is_frozen_typed() -> None:
    from marivo.datasource.discovery import PrimaryKeyCandidate

    cand = PrimaryKeyCandidate(
        column="order_id",
        source="declared_primary",
        evidence=(DiscoveryEvidenceEntry(key="source", value="declared_primary"),),
    )
    assert cand.column == "order_id"
    assert cand.source == "declared_primary"
    with pytest.raises(FrozenInstanceError):
        setattr(cand, "column", "other")  # noqa: B010 - exercising frozen guard


def test_format_candidate_carries_match_metadata() -> None:
    from marivo.datasource.discovery import FormatCandidate

    fmt = FormatCandidate(format="%Y-%m-%d", kind="string", matched_count=8, ambiguous=False)
    assert fmt.format == "%Y-%m-%d"
    assert fmt.kind == "string"
    assert fmt.matched_count == 8
    assert fmt.ambiguous is False


def test_discovery_results_byte_capped_and_uncapped() -> None:
    fc = FormatCandidate(format="%Y-%m-%d", kind="string", matched_count=3, ambiguous=False)

    assert isinstance(fc, RenderableResult)
    capped = fc.render()
    assert len(capped.encode("utf-8")) <= 8192
    full = fc.render(max_output_bytes=None)
    assert "truncated" not in full
    assert "FormatCandidate" in repr(fc)


def test_raw_sql_result_full_render_lists_all_returned_rows() -> None:
    result = RawSqlResult(
        datasource=ref("warehouse"),
        backend_type="duckdb",
        sql="SELECT n FROM numbers",
        reason="inspect sample",
        columns=("n",),
        types={"n": "INTEGER"},
        rows=tuple({"n": i} for i in range(1, 13)),
        requested_limit=12,
        returned_row_count=12,
        is_truncated=False,
        warnings=(),
    )

    rendered = result.render(max_output_bytes=None)

    assert "RawSqlResult" in rendered
    assert "9" in rendered.splitlines()
    assert "12" in rendered.splitlines()


def test_long_format_candidate_default_render_stays_bounded() -> None:
    result = FormatCandidate(
        format="%" + ("Y" * 20_000), kind="string", matched_count=3, ambiguous=False
    )

    rendered = result.render()

    assert len(rendered.encode("utf-8")) <= 8192
    assert "FormatCandidate" in rendered


def test_long_partition_columns_default_render_stays_bounded() -> None:
    long_column = "partition_" + ("x" * 20_000)
    result = PartitionInspectionResult(
        datasource=ref("warehouse"),
        source=table("orders"),
        partition_columns=(long_column,),
        rows=(),
        requested_limit=10,
        is_truncated=False,
        warnings=(),
    )

    rendered = result.render()
    full = result.render(max_output_bytes=None)

    assert len(rendered.encode("utf-8")) <= 8192
    assert "PartitionInspectionResult" in rendered
    assert long_column in full


def test_long_raw_sql_reason_default_render_stays_bounded() -> None:
    long_reason = "diagnose " + ("join path " * 3000)
    result = RawSqlResult(
        datasource=ref("warehouse"),
        backend_type="duckdb",
        sql="SELECT 1 AS ok",
        reason=long_reason,
        columns=("ok",),
        types={"ok": "INTEGER"},
        rows=({"ok": 1},),
        requested_limit=1,
        returned_row_count=1,
        is_truncated=False,
        warnings=(),
    )

    rendered = result.render()
    full = result.render(max_output_bytes=None)

    assert len(rendered.encode("utf-8")) <= 8192
    assert "RawSqlResult" in rendered
    assert long_reason in full


def test_key_type_evidence_is_frozen_typed() -> None:
    from marivo.datasource.discovery import KeyTypeEvidence

    ev = KeyTypeEvidence(
        side="from", column="customer_id", type_family="integer", data_type="BIGINT"
    )
    assert ev.side == "from"
    assert ev.type_family == "integer"
    with pytest.raises(FrozenInstanceError):
        setattr(ev, "side", "to")  # noqa: B010 - exercising frozen guard


def test_entity_result_accepts_typed_primary_key_evidence() -> None:
    from marivo.datasource.discovery import PrimaryKeyCandidate

    result = EntityDiscoveryResult(
        datasource=ref("warehouse"),
        source=table("orders"),
        table_metadata=None,
        scan=_scan_report(),
        table="orders",
        primary_key_evidence=(
            PrimaryKeyCandidate(
                column="order_id",
                source="sampled_unique",
                evidence=(DiscoveryEvidenceEntry(key="distinct_count", value=4),),
            ),
        ),
        time_like_columns=(),
        partition_columns=(),
        column_profiles=(),
        signals=(),
        issues=(),
    )
    assert result.primary_key_evidence[0].source == "sampled_unique"


def test_time_column_discovery_accepts_format_evidence() -> None:
    from marivo.datasource.discovery import FormatCandidate

    column = TimeColumnDiscovery(
        column="created_at",
        profile=_profile("created_at", "VARCHAR"),
        detected_formats=(
            FormatCandidate(format="%Y-%m-%d", kind="string", matched_count=4, ambiguous=False),
        ),
        value_range=TimeValueRange(lower="2026-01-01", upper="2026-01-04"),
        partition_aligned=False,
        signals=(),
        issues=(),
    )
    assert column.detected_formats[0].format == "%Y-%m-%d"
