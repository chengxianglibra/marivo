"""Tests for app.core.semantic.metric_resolution pure functions."""

from __future__ import annotations

import pytest

from app.core.semantic.metric_resolution import (
    MetricBindingResolution,
    MetricCarrierRoutePreflight,
    MetricExecutionContext,
    build_metric_query_extractor_context,
    comparison_slice_label,
    compile_typed_metric_sql,
    compile_typed_metric_value_sql,
    dataset_native_metric_input_field_map,
    metric_query_debug_payload,
    metric_query_mode_contract,
    metric_query_quality_builder,
    metric_query_summary,
    normalize_metric_query_order,
    normalize_metric_rows,
    window_length,
)

# ── metric_query_mode_contract ───────────────────────────────────────


def test_metric_query_mode_contract_compare() -> None:
    result = metric_query_mode_contract("compare")
    assert result["mode"] == "compare"
    assert "current_value" in result["payload_fields"]
    assert "delta_pct" in result["required_payload_keys"]
    assert "required_row_fields" in result


def test_metric_query_mode_contract_single_window() -> None:
    result = metric_query_mode_contract("single_window")
    assert result["mode"] == "single_window"
    assert "baseline_value" not in result["payload_fields"]


def test_metric_query_mode_contract_case_insensitive() -> None:
    result = metric_query_mode_contract("Compare")
    assert result["mode"] == "compare"


def test_metric_query_mode_contract_unknown_raises() -> None:
    with pytest.raises(ValueError, match="Unsupported metric_query mode"):
        metric_query_mode_contract("unknown")


# ── build_metric_query_extractor_context ─────────────────────────────


def test_build_metric_query_extractor_context() -> None:
    result = build_metric_query_extractor_context(
        mode="compare",
        metric_name="watch_time",
        observation_type="metric_observation",
        dimensions=["platform"],
        quality_builder=lambda row: {},
    )
    assert result["metric"] == "watch_time"
    assert result["observation_type"] == "metric_observation"
    assert result["dimensions"] == ["platform"]
    assert "payload_fields" in result


# ── metric_query_quality_builder ──────────────────────────────────────


def test_metric_query_quality_builder_compare_ok() -> None:
    builder = metric_query_quality_builder("compare")
    result = builder({"current_sessions": 200, "baseline_sessions": 300})
    assert result["freshness_ok"] is True
    assert result["sample_size_ok"] is True


def test_metric_query_quality_builder_compare_small() -> None:
    builder = metric_query_quality_builder("compare")
    result = builder({"current_sessions": 10, "baseline_sessions": 20})
    assert result["sample_size_ok"] is False


def test_metric_query_quality_builder_single_window() -> None:
    builder = metric_query_quality_builder("single_window")
    result = builder({"current_sessions": 200})
    assert result["sample_size_ok"] is True


# ── normalize_metric_rows ────────────────────────────────────────────


def test_normalize_metric_rows_valid_compare() -> None:
    rows = [
        {
            "current_value": 1,
            "baseline_value": 2,
            "delta_pct": -50,
            "current_sessions": 100,
            "baseline_sessions": 200,
        },
    ]
    result = normalize_metric_rows(rows, mode="compare")
    assert len(result) == 1


def test_normalize_metric_rows_missing_column_raises() -> None:
    rows = [{"current_value": 1}]
    with pytest.raises(ValueError, match="missing required columns"):
        normalize_metric_rows(rows, mode="compare")


# ── comparison_slice_label ───────────────────────────────────────────


def test_comparison_slice_label_no_dimensions() -> None:
    assert comparison_slice_label({}, []) == "overall"


def test_comparison_slice_label_with_dimensions() -> None:
    row = {"platform": "ios", "region": "us"}
    assert comparison_slice_label(row, ["platform", "region"]) == "platform=ios, region=us"


def test_comparison_slice_label_missing_values() -> None:
    row = {"platform": "ios"}
    result = comparison_slice_label(row, ["platform", "region"])
    assert result == "platform=ios"


def test_comparison_slice_label_all_none() -> None:
    row = {"platform": None, "region": None}
    assert comparison_slice_label(row, ["platform", "region"]) == "overall"


# ── metric_query_debug_payload ───────────────────────────────────────


def test_metric_query_debug_payload_single_window() -> None:
    result = metric_query_debug_payload(
        current_start="2024-01-01",
        current_end="2024-01-08",
        scope_mode="single_window",
        all_rows=[],
    )
    assert result["current_window"] == ["2024-01-01", "2024-01-08"]
    assert "baseline_window" not in result


def test_metric_query_debug_payload_compare_with_baseline() -> None:
    result = metric_query_debug_payload(
        current_start="2024-01-01",
        current_end="2024-01-08",
        baseline_start="2023-12-25",
        baseline_end="2024-01-01",
        scope_mode="compare",
        all_rows=[],
        window_length_match=True,
    )
    assert result["baseline_window"] == ["2023-12-25", "2024-01-01"]
    assert result["window_length_match"] is True


def test_metric_query_debug_payload_compare_without_baseline_raises() -> None:
    with pytest.raises(ValueError, match="requires baseline window"):
        metric_query_debug_payload(
            current_start="2024-01-01",
            current_end="2024-01-08",
            scope_mode="compare",
            all_rows=[],
        )


# ── metric_query_summary ─────────────────────────────────────────────


def test_metric_query_summary_single_window_with_rows() -> None:
    rows = [{"current_value": 100, "current_sessions": 50}]
    debug = {"current_has_data": True, "current_window": ["2024-01-01", "2024-01-08"]}
    result = metric_query_summary(
        "watch_time", rows, mode="single_window", debug=debug, dimensions=[], grain="day"
    )
    assert "100" in result
    assert "watch_time" in result


def test_metric_query_summary_single_window_no_data() -> None:
    debug = {"current_has_data": False, "current_window": ["2024-01-01", "2024-01-08"]}
    result = metric_query_summary(
        "watch_time", [], mode="single_window", debug=debug, dimensions=[], grain="day"
    )
    assert "no data" in result


def test_metric_query_summary_compare_with_rows() -> None:
    rows = [{"delta_pct": -15.0, "current_value": 85, "baseline_value": 100}]
    debug = {
        "current_has_data": True,
        "baseline_has_data": True,
        "window_length_match": True,
        "current_window": ["2024-01-01", "2024-01-08"],
        "baseline_window": ["2023-12-25", "2024-01-01"],
    }
    result = metric_query_summary(
        "watch_time", rows, mode="compare", debug=debug, dimensions=[], grain="day"
    )
    assert "decline" in result
    assert "-15.0%" in result


# ── normalize_metric_query_order ─────────────────────────────────────


def test_normalize_metric_query_order_compare_default() -> None:
    assert normalize_metric_query_order(None, mode="compare") is None


def test_normalize_metric_query_order_compare_asc() -> None:
    assert normalize_metric_query_order("ASC", mode="compare") == "DELTA_PCT ASC"


def test_normalize_metric_query_order_compare_desc() -> None:
    assert normalize_metric_query_order("DESC", mode="compare") == "DELTA_PCT DESC"


def test_normalize_metric_query_order_compare_invalid_raises() -> None:
    with pytest.raises(ValueError, match="compare mode supports only delta_pct"):
        normalize_metric_query_order("CURRENT_VALUE DESC", mode="compare")


def test_normalize_metric_query_order_single_window_default() -> None:
    assert normalize_metric_query_order(None, mode="single_window") == "CURRENT_VALUE DESC"


def test_normalize_metric_query_order_single_window_valid() -> None:
    assert (
        normalize_metric_query_order("CURRENT_SESSIONS ASC", mode="single_window")
        == "CURRENT_SESSIONS ASC"
    )


def test_normalize_metric_query_order_single_window_invalid_raises() -> None:
    with pytest.raises(ValueError, match="single_window mode supports only"):
        normalize_metric_query_order("DELTA_PCT ASC", mode="single_window")


# ── window_length ────────────────────────────────────────────────────


def test_window_length_day_grain() -> None:
    result = window_length(window_start="2024-01-01", window_end="2024-01-08", grain="day")
    assert result == 7


def test_window_length_hour_grain() -> None:
    result = window_length(
        window_start="2024-01-01T00:00:00", window_end="2024-01-01T12:00:00", grain="hour"
    )
    assert result == 12


# ── compile_typed_metric_sql ─────────────────────────────────────────


def test_compile_typed_metric_sql_count() -> None:
    result = compile_typed_metric_sql(
        "count_metric",
        {},
        "metric.sessions",
        input_field_map={"count_target": "session_id"},
    )
    assert result == "COUNT(session_id)"


def test_compile_typed_metric_sql_count_distinct() -> None:
    payload = {"count_target": {"aggregation": "count_distinct"}}
    result = compile_typed_metric_sql(
        "count_metric",
        payload,
        "metric.users",
        input_field_map={"count_target": "user_id"},
    )
    assert result == "COUNT(DISTINCT user_id)"


def test_compile_typed_metric_sql_sum() -> None:
    result = compile_typed_metric_sql(
        "sum_metric",
        {},
        "metric.revenue",
        input_field_map={"measure": "amount"},
    )
    assert result == "SUM(amount)"


def test_compile_typed_metric_sql_average() -> None:
    result = compile_typed_metric_sql(
        "average_metric",
        {},
        "metric.duration",
        input_field_map={"numerator": "duration", "denominator": "session_id"},
    )
    assert result == "SUM(duration) / NULLIF(COUNT(session_id), 0)"


def test_compile_typed_metric_sql_rate() -> None:
    result = compile_typed_metric_sql(
        "rate_metric",
        {},
        "metric.crash_rate",
        input_field_map={"numerator": "crashes", "denominator": "sessions"},
    )
    assert result == "SUM(crashes) / NULLIF(SUM(sessions), 0)"


def test_compile_typed_metric_sql_distribution_trino() -> None:
    payload = {"distribution_spec": {"kind": "percentile", "percentile": 0.95}}
    result = compile_typed_metric_sql(
        "distribution_metric",
        payload,
        "metric.p95_latency",
        input_field_map={"value_component": "latency_ms"},
        engine_type="trino",
    )
    assert result == "APPROX_PERCENTILE(latency_ms, 0.95)"


def test_compile_typed_metric_sql_distribution_duckdb() -> None:
    payload = {"distribution_spec": {"kind": "quantile", "percentile": 0.5}}
    result = compile_typed_metric_sql(
        "distribution_metric",
        payload,
        "metric.p50_latency",
        input_field_map={"value_component": "latency_ms"},
        engine_type="duckdb",
    )
    assert result == "QUANTILE_CONT(latency_ms, 0.5)"


def test_compile_typed_metric_sql_distribution_unsupported_engine_raises() -> None:
    payload = {"distribution_spec": {"kind": "percentile", "percentile": 0.5}}
    with pytest.raises(ValueError, match="unsupported engine_type"):
        compile_typed_metric_sql(
            "distribution_metric",
            payload,
            "metric.p50_latency",
            input_field_map={"value_component": "latency_ms"},
            engine_type="spark",
        )


def test_compile_typed_metric_sql_distribution_missing_percentile_raises() -> None:
    payload = {"distribution_spec": {"kind": "percentile"}}
    with pytest.raises(ValueError, match=r"missing.*percentile"):
        compile_typed_metric_sql(
            "distribution_metric",
            payload,
            "metric.p50_latency",
            input_field_map={"value_component": "latency_ms"},
        )


def test_compile_typed_metric_sql_distribution_histogram_ready_raises() -> None:
    payload = {"distribution_spec": {"kind": "histogram_ready"}}
    with pytest.raises(ValueError, match="histogram_ready"):
        compile_typed_metric_sql(
            "distribution_metric",
            payload,
            "metric.hist",
            input_field_map={"value_component": "value"},
        )


def test_compile_typed_metric_sql_none_input_field_map() -> None:
    result = compile_typed_metric_sql("count_metric", {}, "metric.x", input_field_map=None)
    assert result is None


def test_compile_typed_metric_sql_unknown_family() -> None:
    result = compile_typed_metric_sql("unknown_metric", {}, "metric.x", input_field_map={})
    assert result is None


# ── compile_typed_metric_value_sql ───────────────────────────────────


def test_compile_typed_metric_value_sql_sum() -> None:
    result = compile_typed_metric_value_sql(
        "sum_metric",
        {},
        "metric.revenue",
        input_field_map={"measure": "amount"},
    )
    assert result == "amount"


def test_compile_typed_metric_value_sql_average_sum_count() -> None:
    payload = {"numerator": {"aggregation": "sum"}, "denominator": {"aggregation": "count"}}
    result = compile_typed_metric_value_sql(
        "average_metric",
        payload,
        "metric.duration",
        input_field_map={"numerator": "duration", "denominator": "session_id"},
    )
    assert result == "duration"


def test_compile_typed_metric_value_sql_none_input_field_map() -> None:
    result = compile_typed_metric_value_sql("sum_metric", {}, "metric.x", input_field_map=None)
    assert result is None


# ── dataset_native_metric_input_field_map ────────────────────────────


def test_dataset_native_metric_input_field_map_count_with_id() -> None:
    result = dataset_native_metric_input_field_map(
        "count_metric", {"dataset_fields": {"id": "int"}}
    )
    assert result == {"count_target": "id"}


def test_dataset_native_metric_input_field_map_count_without_id() -> None:
    result = dataset_native_metric_input_field_map(
        "count_metric", {"dataset_fields": {"name": "string"}}
    )
    assert result == {"count_target": "*"}


def test_dataset_native_metric_input_field_map_sum() -> None:
    result = dataset_native_metric_input_field_map(
        "sum_metric", {"dataset_fields": {"value": "float"}}
    )
    assert result == {"measure": "value"}


def test_dataset_native_metric_input_field_map_average_average_type() -> None:
    payload = {
        "dataset_fields": {"play_duration_seconds": "float", "session_id": "int"},
        "measure_type": "average",
    }
    result = dataset_native_metric_input_field_map("average_metric", payload)
    assert result["numerator"] == "play_duration_seconds"
    assert result["denominator"] == "session_id"


def test_dataset_native_metric_input_field_map_average_ratio_type() -> None:
    payload = {"dataset_fields": {"numerator": "float", "denominator": "int"}}
    result = dataset_native_metric_input_field_map("average_metric", payload)
    assert result["numerator"] == "numerator"
    assert result["denominator"] == "denominator"


def test_dataset_native_metric_input_field_map_rate() -> None:
    result = dataset_native_metric_input_field_map(
        "rate_metric", {"dataset_fields": {"numerator": "int", "denominator": "int"}}
    )
    assert result["numerator"] == "numerator"


def test_dataset_native_metric_input_field_map_distribution() -> None:
    result = dataset_native_metric_input_field_map(
        "distribution_metric", {"dataset_fields": {"value": "float"}}
    )
    assert result == {"value_component": "value"}


def test_dataset_native_metric_input_field_map_score() -> None:
    result = dataset_native_metric_input_field_map(
        "score_metric", {"dataset_fields": {"value": "float"}}
    )
    assert result == {"score_source": "value"}


def test_dataset_native_metric_input_field_map_unknown() -> None:
    result = dataset_native_metric_input_field_map("unknown_metric", {"dataset_fields": {}})
    assert result == {}


# ── Data classes ─────────────────────────────────────────────────────


def test_metric_execution_context() -> None:
    ctx = MetricExecutionContext(
        metric_ref="metric.sessions",
        table_name="events",
        binding_ref="metric.sessions",
    )
    assert ctx.metric_ref == "metric.sessions"
    assert ctx.carrier_binding_key is None
    assert ctx.input_field_map is None


def test_metric_binding_resolution() -> None:
    res = MetricBindingResolution(
        metric_ref="metric.sessions",
        binding_ref="metric.sessions",
        carrier_binding_key=None,
        source_object_ref=None,
        carrier_locator=None,
        authority_locator=None,
        mapping_id=None,
        execution_locator=None,
        routing_detail=None,
        table_name="events",
        input_field_map={"count_target": "session_id"},
    )
    assert res.table_name == "events"
    assert res.input_field_map["count_target"] == "session_id"


def test_metric_carrier_route_preflight() -> None:
    pf = MetricCarrierRoutePreflight(
        table_name="events",
        mapping_id="map1",
        execution_locator=None,
        routing_detail={"status": "ok"},
        readiness_blockers=[],
    )
    assert pf.readiness_blockers == []
