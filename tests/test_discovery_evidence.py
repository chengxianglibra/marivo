"""Tests for the discovery evidence model, scope helpers, and rule engine."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from marivo.datasource.authoring import ref
from marivo.datasource.discovery import (
    ColumnDiscoveryCandidate,
    DimensionValueDiscoveryResult,
    DimensionValueFact,
    DiscoveryEvidenceEntry,
    DiscoveryIssue,
    DiscoverySignal,
    EntityDiscoveryResult,
    MeasureDiscoveryResult,
    SemanticJudgmentTarget,
    TimeValueRange,
)
from marivo.datasource.scan import (
    ColumnProfile,
    ScanReport,
    ScanScope,
    TableSource,
    latest_partition,
    partition,
    table,
    unpruned,
)
from marivo.render import AgentResult


def test_table_source_is_entity_source_union() -> None:
    from marivo.datasource.ir import CsvSourceIR, ParquetSourceIR, TableSourceIR

    # TableSource is the union alias; each concrete IR is an instance of it
    # at runtime (isinstance checks against the alias are not valid, but the
    # alias must equal the union type object).
    assert TableSource == TableSourceIR | ParquetSourceIR | CsvSourceIR


def test_latest_partition_defaults_to_latest_partition() -> None:
    scope = latest_partition()
    assert isinstance(scope, ScanScope)
    assert scope.partition == "latest"
    assert scope.max_rows == 1000
    assert scope.max_columns == 100
    assert scope.timeout_seconds == 30


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


def test_judgment_target_fields() -> None:
    target = SemanticJudgmentTarget(
        object_kind="measure",
        field_path="measure.additivity",
        question="decide additive, semi-additive, or non-additive policy",
        owner="user_or_project_context",
    )
    assert target.owner == "user_or_project_context"


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


def _measure_result() -> MeasureDiscoveryResult:
    cand = ColumnDiscoveryCandidate(
        column="amount",
        profile=_profile("amount", "DOUBLE"),
        signals=(),
        issues=(),
    )
    return MeasureDiscoveryResult(
        datasource=ref("warehouse"),
        source=table("orders"),
        table_metadata=None,
        scan=_scan_report(),
        signals=(),
        issues=(),
        judgment_targets=(
            SemanticJudgmentTarget(
                object_kind="measure",
                field_path="measure.additivity",
                question="decide additive, semi-additive, or non-additive policy",
                owner="user_or_project_context",
            ),
        ),
        candidates=(cand,),
    )


@pytest.mark.parametrize(
    "result_factory",
    [
        lambda: EntityDiscoveryResult(
            datasource=ref("warehouse"),
            source=table("orders"),
            table_metadata=None,
            scan=_scan_report(),
            signals=(),
            issues=(),
            judgment_targets=(),
            candidates=(),
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
            judgment_targets=(),
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


def test_measure_render_lists_judgment_targets_and_columns() -> None:
    result = _measure_result()
    rendered = result.render()
    assert "MeasureDiscoveryResult" in rendered
    assert "evidence_only" in rendered
    assert "judgment targets:" in rendered
    assert "measure.additivity" in rendered
    assert "amount" in rendered
    assert "available:" in rendered


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
        judgment_targets=(),
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


def test_key_type_evidence_is_frozen_typed() -> None:
    from marivo.datasource.discovery import KeyTypeEvidence

    ev = KeyTypeEvidence(
        side="from", column="customer_id", type_family="integer", data_type="BIGINT"
    )
    assert ev.side == "from"
    assert ev.type_family == "integer"
    with pytest.raises(FrozenInstanceError):
        setattr(ev, "side", "to")  # noqa: B010 - exercising frozen guard


def test_entity_candidate_accepts_typed_primary_key_candidates() -> None:
    from marivo.datasource.discovery import (
        EntityDiscoveryCandidate,
        PrimaryKeyCandidate,
    )

    cand = EntityDiscoveryCandidate(
        table="orders",
        primary_key_candidates=(
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
    assert cand.primary_key_candidates[0].source == "sampled_unique"


def test_time_candidate_accepts_format_candidates() -> None:
    from marivo.datasource.discovery import (
        FormatCandidate,
        TimeColumnDiscoveryCandidate,
    )

    cand = TimeColumnDiscoveryCandidate(
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
    assert cand.detected_formats[0].format == "%Y-%m-%d"
