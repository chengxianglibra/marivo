"""Tests for app.core.semantic.compiler pure functions."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from marivo.core.semantic.compiler import (
    SemanticCompilerError,
    SemanticRequestCompatibilityError,
    build_aggregate_comparison_query,
    build_calendar_alignment_coverage,
    build_intent_node,
    build_lowering_requirements,
    build_metric_query,
    build_process_node,
    build_profile_usage_trace,
    build_validation_summary,
    build_validation_trace,
    build_windowed_aggregate_query,
    date_window_from_time_scope,
    entity_field_snapshot,
    metric_snapshot,
    process_snapshot,
    relationship_snapshot,
    requests_imported_dimensions,
    serialize_calendar_window,
)

# ── build_metric_query ─────────────────────────────────────────────────


def test_build_metric_query_compare_scoped() -> None:
    scoped_query = {
        "mode": "compare",
        "analysis_time_expr": "event_date",
        "analysis_time_kind": "date",
        "engine_type": "duckdb",
        "current": {"start": "2024-01-01", "end": "2024-01-08"},
        "baseline": {"start": "2023-12-25", "end": "2024-01-01"},
    }
    result = build_metric_query(
        metric_name="revenue",
        table_name="events",
        metric_sql="SUM(amount)",
        dimensions=["platform"],
        order="DELTA_PCT ASC",
        limit=5,
        scoped_query=scoped_query,
    )
    assert "scoped" in result
    assert "by_period" in result
    assert "pivoted" in result
    assert "delta_pct" in result
    assert "platform" in result


def test_build_metric_query_single_window_scoped() -> None:
    scoped_query = {
        "mode": "single_window",
        "analysis_time_expr": "event_date",
        "analysis_time_kind": "date",
        "engine_type": "duckdb",
        "current": {"start": "2024-01-01", "end": "2024-01-08"},
    }
    result = build_metric_query(
        metric_name="revenue",
        table_name="events",
        metric_sql="SUM(amount)",
        dimensions=[],
        order="CURRENT_VALUE DESC",
        limit=10,
        scoped_query=scoped_query,
    )
    assert "current_value" in result
    assert "current_sessions" in result
    assert "baseline" not in result


def test_build_metric_query_legacy_no_scoped() -> None:
    result = build_metric_query(
        metric_name="revenue",
        table_name="events",
        metric_sql="SUM(amount)",
        dimensions=["platform"],
        date_column="event_date",
        order="DELTA_PCT ASC",
        limit=10,
    )
    assert "periodized" in result
    assert "event_date BETWEEN ? AND ?" in result
    assert "platform" in result


def test_build_metric_query_no_dimensions() -> None:
    result = build_metric_query(
        metric_name="revenue",
        table_name="events",
        metric_sql="SUM(amount)",
        dimensions=[],
        date_column="event_date",
    )
    assert "GROUP BY period" in result
    # No dimension columns in SELECT
    assert "metric_value" in result


# ── build_windowed_aggregate_query ─────────────────────────────────────


def test_build_windowed_aggregate_query_compare() -> None:
    scoped_query = {
        "mode": "compare",
        "analysis_time_expr": "event_date",
        "analysis_time_kind": "date",
        "engine_type": "duckdb",
        "current": {"start": "2024-01-01", "end": "2024-01-08"},
        "baseline": {"start": "2023-12-25", "end": "2024-01-01"},
    }
    result = build_windowed_aggregate_query(
        table_name="events",
        measures=[{"expr": "COUNT(*)", "as": "cnt"}, {"expr": "SUM(amount)", "as": "total"}],
        group_by=["platform"],
        limit=10,
        scoped_query=scoped_query,
    )
    assert "cnt_current" in result
    assert "cnt_baseline" in result
    assert "cnt_delta_pct" in result
    assert "total_current" in result
    assert "platform" in result


def test_build_windowed_aggregate_query_single_window() -> None:
    scoped_query = {
        "mode": "single_window",
        "analysis_time_expr": "event_date",
        "analysis_time_kind": "date",
        "engine_type": "duckdb",
        "current": {"start": "2024-01-01", "end": "2024-01-08"},
    }
    result = build_windowed_aggregate_query(
        table_name="events",
        measures=[{"expr": "COUNT(*)", "as": "cnt"}],
        group_by=["platform"],
        scoped_query=scoped_query,
    )
    assert "cnt" in result
    assert "FROM scoped" in result


def test_build_windowed_aggregate_query_no_scoped() -> None:
    result = build_windowed_aggregate_query(
        table_name="events",
        measures=[{"expr": "COUNT(*)", "as": "cnt"}],
        group_by=["platform"],
    )
    assert "SELECT platform, COUNT(*) AS cnt FROM events" in result


def test_build_windowed_aggregate_query_invalid_measures() -> None:
    with pytest.raises(ValueError, match="requires 'measures'"):
        build_windowed_aggregate_query(
            table_name="events",
            measures=[],
            group_by=[],
        )


def test_build_windowed_aggregate_query_missing_alias() -> None:
    with pytest.raises(ValueError, match="non-empty 'expr' and 'as'"):
        build_windowed_aggregate_query(
            table_name="events",
            measures=[{"expr": "COUNT(*)"}],
            group_by=[],
        )


# ── build_aggregate_comparison_query ───────────────────────────────────


def test_build_aggregate_comparison_query_scoped() -> None:
    scoped_query = {
        "mode": "compare",
        "analysis_time_expr": "event_date",
        "analysis_time_kind": "date",
        "engine_type": "duckdb",
        "current": {"start": "2024-01-01", "end": "2024-01-08"},
        "baseline": {"start": "2023-12-25", "end": "2024-01-01"},
    }
    result = build_aggregate_comparison_query(
        table_name="events",
        select_exprs=["COUNT(*) AS cnt", "platform"],
        group_by=["platform"],
        date_column="event_date",
        scoped_query=scoped_query,
    )
    assert "cnt_current" in result
    assert "cnt_baseline" in result
    assert "cnt_delta_pct" in result


def test_build_aggregate_comparison_query_legacy() -> None:
    result = build_aggregate_comparison_query(
        table_name="events",
        select_exprs=["COUNT(*) AS cnt", "platform"],
        group_by=["platform"],
        date_column="event_date",
    )
    assert "periodized" in result
    assert "event_date BETWEEN ? AND ?" in result


def test_build_aggregate_comparison_query_no_agg_raises() -> None:
    with pytest.raises(ValueError, match="compare_period requires"):
        build_aggregate_comparison_query(
            table_name="events",
            select_exprs=["platform"],
            group_by=["platform"],
            date_column="event_date",
        )


def test_build_aggregate_comparison_query_missing_alias_raises() -> None:
    with pytest.raises(ValueError, match="requires aliases"):
        build_aggregate_comparison_query(
            table_name="events",
            select_exprs=["COUNT(*)", "platform"],
            group_by=["platform"],
            date_column="event_date",
        )


# ── Calendar alignment helpers ─────────────────────────────────────────


def test_build_calendar_alignment_coverage_full() -> None:
    pairing = [
        {"baseline_bucket_start": "2024-01-01"},
        {"baseline_bucket_start": "2024-01-02"},
        {"baseline_bucket_start": None},
    ]
    result = build_calendar_alignment_coverage(pairing)
    assert result["aligned_bucket_count"] == 2
    assert result["unpaired_bucket_count"] == 1
    assert abs(result["aligned_ratio"] - 2 / 3) < 1e-6


def test_build_calendar_alignment_coverage_empty() -> None:
    result = build_calendar_alignment_coverage([])
    assert result["aligned_bucket_count"] == 0
    assert result["aligned_ratio"] == 0.0


def test_serialize_calendar_window() -> None:
    result = serialize_calendar_window((date(2024, 1, 1), date(2024, 1, 8)))
    assert result == {"start": "2024-01-01", "end": "2024-01-08"}


def test_serialize_calendar_window_none() -> None:
    assert serialize_calendar_window(None) is None


def test_date_window_from_time_scope() -> None:
    time_scope = {"current": {"start": "2024-01-01", "end": "2024-01-08"}}
    start, end = date_window_from_time_scope(time_scope)
    assert start == date(2024, 1, 1)
    assert end == date(2024, 1, 8)


def test_date_window_from_time_scope_inverted_raises() -> None:
    time_scope = {"current": {"start": "2024-01-08", "end": "2024-01-01"}}
    with pytest.raises(ValueError, match="start < end"):
        date_window_from_time_scope(time_scope)


def test_date_window_from_time_scope_missing_raises() -> None:
    time_scope = {"current": {"start": "", "end": ""}}
    with pytest.raises(ValueError, match="date window boundaries"):
        date_window_from_time_scope(time_scope)


# ── Validation trace/summary builders ──────────────────────────────────


class _FakeValidationResult:
    def __init__(
        self, issues: list[Any], validated_dimension_refs: list[str] | None = None
    ) -> None:
        self.issues = issues
        self.validated_dimension_refs = validated_dimension_refs or []
        self.resolved_filter_time_ref = None


class _FakeIssue:
    def __init__(self, gate: str, severity: str) -> None:
        self.gate = gate
        self.severity = severity


def test_build_validation_trace_all_passed() -> None:
    result = _FakeValidationResult([])
    trace = build_validation_trace(result)
    assert len(trace) == 14  # All gates in _VALIDATION_GATE_ORDER
    assert all(record["status"] == "passed" for record in trace)


def test_build_validation_trace_with_failure() -> None:
    issues = [_FakeIssue(gate="request_shape", severity="error")]
    result = _FakeValidationResult(issues)
    trace = build_validation_trace(result)
    gate_kinds = [record["validation_kind"] for record in trace]
    assert "request_shape" not in gate_kinds


def test_build_validation_summary_basic() -> None:
    result = _FakeValidationResult([], validated_dimension_refs=["dimension.platform"])
    trace = build_validation_trace(result)
    summary = build_validation_summary(result, trace)
    assert summary["passed_gate_count"] == 14
    assert summary["warning_count"] == 0
    assert summary["validated_dimension_refs"] == ["dimension.platform"]


# ── IR snapshot builders ───────────────────────────────────────────────


class _FakeResolvedObject:
    def __init__(
        self,
        ref: str,
        revision: int = 1,
        object_id: str = "oid",
        semantic_object: dict | None = None,
    ) -> None:
        self.ref = ref
        self.revision = revision
        self.object_id = object_id
        self.semantic_object = semantic_object or {}


def test_metric_snapshot() -> None:
    metric = _FakeResolvedObject(
        "metric.revenue",
        semantic_object={"header": {"primary_time_ref": "time.event_date"}},
    )
    snap = metric_snapshot(metric)
    assert snap["metric_ref"] == "metric.revenue"
    assert snap["resolved_primary_time_ref"] == "time.event_date"


def test_process_snapshot() -> None:
    process = _FakeResolvedObject(
        "process.session",
        semantic_object={"interface_contract": {"anchor_time_ref": "time.event_date"}},
    )
    snap = process_snapshot(process)
    assert snap["process_ref"] == "process.session"
    assert snap["resolved_anchor_time_ref"] == "time.event_date"


def test_entity_field_snapshot() -> None:
    class FakeField:
        field_ref = "entity.user.field.age"
        entity_ref = "entity.user"
        local_field_ref = "field.age"
        entity_revision = 3
        source_object_ref = "dataset.users"
        source_object_fqn = "public.users"
        carrier_kind = "column"
        physical_column = "age"
        physical_expression_locator = None

    snap = entity_field_snapshot(FakeField())
    assert snap["field_ref"] == "entity.user.field.age"
    assert snap["physical_column"] == "age"


class _FakeRelationship:
    relationship_ref = "rel.user_event"
    left_entity_ref = "entity.user"
    right_entity_ref = "entity.event"
    revision = 1
    key_alignment = {}
    time_alignment = None
    cardinality = "one_to_many"
    grain_compatibility = {}
    snapshot_effective_window_alignment = {}


def test_relationship_snapshot() -> None:
    snap = relationship_snapshot(_FakeRelationship())
    assert snap["relationship_ref"] == "rel.user_event"
    assert snap["cardinality"] == "one_to_many"


# ── build_process_node ─────────────────────────────────────────────────


def test_build_process_node() -> None:
    process = _FakeResolvedObject(
        "process.session",
        semantic_object={
            "process_type": "entity_stream",
            "interface_contract": {
                "contract_mode": "entity_stream",
                "population_subject_ref": "entity.user",
                "context_kind": "session",
            },
        },
    )
    node = build_process_node(0, process)
    assert node["node_id"] == "process:0:process.session"
    assert node["process_type"] == "entity_stream"
    assert node["contract_mode"] == "entity_stream"
    assert node["context_kind"] == "session"


# ── build_intent_node ──────────────────────────────────────────────────


class _FakeRequest:
    request_dimensions = ["dimension.platform"]
    request_result_mode = "standard"


def test_build_intent_node() -> None:
    node = build_intent_node(
        step_index=0,
        step_type="metric_query",
        normalized_request=_FakeRequest(),
        output_binding={"artifact_id": "artifact:test", "artifact_kind": "table"},
        depends_on=["measurement:0"],
    )
    assert node["node_id"] == "intent:0"
    assert node["intent_kind"] == "metric_query"
    assert node["requested_dimensions"] == ["dimension.platform"]


# ── build_lowering_requirements ────────────────────────────────────────


class _FakeRequestWithTime:
    request_time_scope = {"mode": "single_window"}


class _FakeRequestNoTime:
    request_time_scope = None


class _FakeInputs:
    resolved_entity_fields = {"field.age": object()}
    resolved_metric = _FakeResolvedObject("metric.revenue")


class _FakeInputsNoMetric:
    resolved_entity_fields = {"field.age": object()}
    resolved_metric = None


def test_build_lowering_requirements_full() -> None:
    reqs = build_lowering_requirements(
        step_index=0,
        step_type="metric_query",
        normalized_request=_FakeRequestWithTime(),
        resolved_inputs=_FakeInputs(),
        intent_node_id="intent:0",
    )
    assert len(reqs) == 3
    kinds = [r["requirement_kind"] for r in reqs]
    assert "engine_sql_execution" in kinds
    assert "time_window_filter" in kinds
    assert "entity_field_grounding" in kinds


def test_build_lowering_requirements_no_time() -> None:
    reqs = build_lowering_requirements(
        step_index=0,
        step_type="sample_rows",
        normalized_request=_FakeRequestNoTime(),
        resolved_inputs=_FakeInputs(),
        intent_node_id="intent:0",
    )
    kinds = [r["requirement_kind"] for r in reqs]
    assert "time_window_filter" not in kinds


# ── build_profile_usage_trace ──────────────────────────────────────────


class _FakeTrace:
    subject_ref = "metric.revenue"
    applied = True
    reason = "default"
    profile_ref = "profile.default"
    subject_revision = None
    resolved_subject_revision = 5


def test_build_profile_usage_trace() -> None:
    traces = build_profile_usage_trace([_FakeTrace()])
    assert len(traces) == 1
    assert traces[0]["subject_ref"] == "metric.revenue"
    assert traces[0]["applied"] is True
    assert traces[0]["resolved_subject_revision"] == 5


# ── requests_imported_dimensions ───────────────────────────────────────


class _FakeBridge:
    dimension_ref = "dimension.platform"


class _FakeReq:
    request_dimensions = ["dimension.platform", "dimension.region"]


class _FakeResolved:
    normalized_request = _FakeReq()
    resolved_imported_dimensions = [_FakeBridge()]


def test_requests_imported_dimensions_true() -> None:
    assert requests_imported_dimensions(_FakeResolved()) is True


class _FakeResolvedNoImport:
    normalized_request = _FakeReq()
    resolved_imported_dimensions = []


def test_requests_imported_dimensions_false() -> None:
    assert requests_imported_dimensions(_FakeResolvedNoImport()) is False


# ── Error classes ──────────────────────────────────────────────────────


def test_semantic_compiler_error() -> None:
    error_dict = {"error_code": "TEST", "failed_gate": "test", "message": "test error"}
    error = SemanticCompilerError(error_dict)  # type: ignore[arg-type]
    assert str(error) == "test error"
    assert error.compile_error["error_code"] == "TEST"


def test_semantic_request_compatibility_error() -> None:
    detail = {"message": "incompatible", "code": "test"}
    error = SemanticRequestCompatibilityError(detail)
    assert "incompatible" in str(error)
