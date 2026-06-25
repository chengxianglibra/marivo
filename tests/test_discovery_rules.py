"""Tests for discovery judgment-target templates and the rule engine."""

from __future__ import annotations

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
    dimension_value_judgment_targets,
    dimension_value_rules,
    entity_judgment_targets,
    measure_column_rules,
    measure_judgment_targets,
    relationship_judgment_targets,
    scan_rules,
    time_dimension_judgment_targets,
)
from marivo.datasource.scan import ColumnProfile, ScanReport, ScanScope, table


def test_entity_targets_use_real_field_paths_and_owners() -> None:
    paths = {t.field_path for t in entity_judgment_targets()}
    assert paths == {
        "entity.name",
        "entity.primary_key",
        "entity.ai_context.business_definition",
    }
    owners = {t.field_path: t.owner for t in entity_judgment_targets()}
    assert owners["entity.name"] == "agent"
    assert owners["entity.primary_key"] == "user_or_project_context"
    assert owners["entity.ai_context.business_definition"] == "user_or_project_context"


def test_measure_targets_include_additivity_unit_business_definition() -> None:
    paths = {t.field_path for t in measure_judgment_targets()}
    assert "measure.column" in paths
    assert "measure.name" in paths
    assert "measure.unit" in paths
    assert "measure.additivity" in paths
    assert "measure.ai_context.business_definition" in paths
    # Metric-layer fields must NOT appear in measure discovery.
    assert "metric.aggregation" not in paths
    assert "metric.measure" not in paths


def test_time_dimension_targets_include_granularity_parse_is_default() -> None:
    paths = {t.field_path for t in time_dimension_judgment_targets()}
    assert "time_dimension.granularity" in paths
    assert "time_dimension.parse" in paths
    assert "time_dimension.is_default" in paths


def test_dimension_value_targets_are_non_authoring() -> None:
    targets = dimension_value_judgment_targets()
    assert len(targets) == 1
    target = targets[0]
    assert target.owner == "agent"
    assert "ai_context" not in target.field_path
    assert "enum" not in target.field_path


def test_relationship_targets_include_keys_and_entities() -> None:
    paths = {t.field_path for t in relationship_judgment_targets()}
    assert "relationship.keys" in paths
    assert "relationship.from_entity" in paths
    assert "relationship.to_entity" in paths


def _profile(name: str, data_type: str, distinct: int = 5, null_count: int = 0, empty_count: int = 0) -> ColumnProfile:
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


def test_measure_numeric_signal_and_non_numeric_blocker() -> None:
    numeric = measure_column_rules(_profile("amount", "DOUBLE"))
    assert "measure_numeric_type" in [s.rule_id for s in numeric if isinstance(s, DiscoverySignal)]
    text = measure_column_rules(_profile("label", "VARCHAR"))
    blockers = [i for i in text if isinstance(i, DiscoveryIssue) and i.severity == "blocker"]
    assert any(i.rule_id == "measure_non_numeric_type" for i in blockers)


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


def test_build_dimension_result_wires_scan_rules_candidates_and_targets() -> None:
    profile = _profile("status", "VARCHAR", distinct=2, null_count=1)
    result = build_dimension_result(
        datasource=ref("warehouse"),
        source=table("orders"),
        table_metadata=None,
        scan=_scan(truncated=True),
        scope=ScanScope(),
        candidate_profiles=(profile,),
    )
    assert isinstance(result, DimensionDiscoveryResult)
    # result-scope truncated issue present
    assert any(i.rule_id == "discovery_scan_truncated" for i in result.issues)
    # candidate-scope low-cardinality signal present on the candidate
    assert any(s.rule_id == "dimension_low_cardinality" for s in result.candidates[0].signals)
    # judgment targets are the dimension template
    paths = {t.field_path for t in result.judgment_targets}
    assert "dimension.ai_context.business_definition" in paths
    # result-level and candidate-level do not overlap
    result_signal_ids = {s.rule_id for s in result.signals}
    cand_signal_ids = {s.rule_id for s in result.candidates[0].signals}
    assert result_signal_ids.isdisjoint(cand_signal_ids)


def test_build_measure_result_marks_non_numeric_blocker() -> None:
    profile = _profile("label", "VARCHAR")
    result = build_measure_result(
        datasource=ref("warehouse"),
        source=table("orders"),
        table_metadata=None,
        scan=_scan(),
        scope=ScanScope(),
        candidate_profiles=(profile,),
    )
    assert isinstance(result, MeasureDiscoveryResult)
    blocker = [i for i in result.candidates[0].issues if i.severity == "blocker"]
    assert any(i.rule_id == "measure_non_numeric_type" for i in blocker)
    assert "measure.additivity" in {t.field_path for t in result.judgment_targets}
