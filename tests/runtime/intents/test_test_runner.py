"""Tests for the current AOI test intent runner contract."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from marivo.runtime.intents._helpers import (
    build_scoped_query_for_window,
    compute_numeric_sample_summary,
)
from marivo.runtime.intents.test import _betai, _p_value_from_t, _t_sf, run_test_intent


def _sample_frame(
    *,
    artifact_id: str,
    metric_ref: str | None = "metric.test_metric",
    subject_kind: Any = "sample_summary",
    source_artifact_id: Any = None,
    lineage_operation: Any = "sample_summary",
    lineage_source_artifact_ids: Any = None,
    source_axis: str = "time",
    grain: str = "day",
    n: Any = 30,
    mean: Any = 100.0,
    standard_deviation: Any = 15.0,
    quality_status: Any = "test_ready",
) -> dict[str, Any]:
    subject: dict[str, Any] = {
        "kind": subject_kind,
    }
    if source_artifact_id is None:
        source_artifact_id = f"{artifact_id}_source"
    if lineage_source_artifact_ids is None:
        lineage_source_artifact_ids = [source_artifact_id]
    if source_artifact_id != "__missing__":
        subject["source_artifact_id"] = source_artifact_id
    if metric_ref is not None:
        subject["metric_ref"] = metric_ref
    lineage: dict[str, Any] = {"operation": lineage_operation}
    if lineage_source_artifact_ids != "__missing__":
        lineage["source_artifact_ids"] = lineage_source_artifact_ids
    return {
        "artifact_id": artifact_id,
        "artifact_family": "sample_frame",
        "shape": "numeric_summary",
        "subject": subject,
        "axes": [{"kind": "sample", "source_axis": source_axis, "grain": grain}],
        "measures": [
            {"id": "n", "value_type": "integer", "nullable": False},
            {"id": "mean", "value_type": "number", "nullable": True},
            {"id": "standard_deviation", "value_type": "number", "nullable": True},
        ],
        "lineage": lineage,
        "payload": {
            "summary": {
                "n": n,
                "mean": mean,
                "standard_deviation": standard_deviation,
            },
            "quality": {"status": quality_status, "issues": []},
        },
    }


def _valid_params() -> dict[str, Any]:
    return {
        "current_sample_artifact_id": "art_sample_current",
        "baseline_sample_artifact_id": "art_sample_baseline",
        "hypothesis": {
            "family": "two_sample_mean",
            "alternative": "two_sided",
            "significance": "balanced",
        },
    }


def _runtime() -> MagicMock:
    runtime = MagicMock()
    runtime.core.normalize_intent_metric_ref = MagicMock(return_value="metric.test_metric")
    runtime.core.metric_name_from_ref = MagicMock(return_value="test_metric")
    return runtime


def _run_with_mock_data(
    params: dict[str, Any] | None = None,
    *,
    current_sample: dict[str, Any] | None = None,
    baseline_sample: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], MagicMock]:
    runtime = _runtime()
    params = deepcopy(params if params is not None else _valid_params())
    current = current_sample or _sample_frame(artifact_id="art_sample_current")
    baseline = baseline_sample or _sample_frame(
        artifact_id="art_sample_baseline",
        n=25,
        mean=90.0,
        standard_deviation=12.0,
    )
    runtime.resolve_artifact_by_id.side_effect = [current, baseline]

    with patch(
        "marivo.runtime.intents.test.resolve_predicate_lineage_reuse_for_intent"
    ) as mock_lineage:
        mock_lineage.return_value = {
            "issues": [],
            "fatal_message": None,
            "reuse_summary": None,
        }
        with patch("marivo.runtime.intents.test.commit_step_result") as mock_commit:
            mock_commit.return_value = {
                "intent_type": "test",
                "step_type": "test",
                "step_ref": {"session_id": "s1", "step_id": "step-1", "step_type": "test"},
                "artifact_id": "art-1",
            }
            run_test_intent(runtime, "session-1", params)
            artifact = mock_commit.call_args[0][6]
            return artifact, runtime.resolve_artifact_by_id


def test_t_sf_symmetry() -> None:
    for t in [-3.0, -1.0, 0.0, 1.0, 3.0]:
        for df in [5, 10, 30, 100]:
            assert _t_sf(t, df) + _t_sf(-t, df) == pytest.approx(1.0)


def test_p_value_two_sided_zero_t() -> None:
    assert _p_value_from_t(0.0, 10, "two_sided") == pytest.approx(1.0)


def test_p_value_decreases_with_larger_t() -> None:
    assert _p_value_from_t(1.0, 30, "two_sided") > _p_value_from_t(5.0, 30, "two_sided")


def test_betai_boundary_values() -> None:
    assert _betai(1, 1, 0.0) == pytest.approx(0.0)
    assert _betai(1, 1, 1.0) == pytest.approx(1.0)


@pytest.mark.parametrize("alternative", ["two_sided", "greater", "less"])
def test_records_supported_alternatives(alternative: str) -> None:
    params = _valid_params()
    params["hypothesis"]["alternative"] = alternative

    artifact, _ = _run_with_mock_data(params)

    assert artifact["hypothesis"]["alternative"] == alternative
    assert artifact["p_value"] is not None


@pytest.mark.parametrize(
    ("significance", "alpha"),
    [("conservative", 0.01), ("balanced", 0.05), ("aggressive", 0.10)],
)
def test_records_supported_significance_presets(significance: str, alpha: float) -> None:
    params = _valid_params()
    params["hypothesis"]["significance"] = significance

    artifact, _ = _run_with_mock_data(params)

    assert artifact["hypothesis"]["significance"] == significance
    assert artifact["hypothesis"]["alpha"] == alpha


def test_artifact_shape_is_current_hypothesis_test_result() -> None:
    artifact, _ = _run_with_mock_data()

    assert artifact["result_type"] == "hypothesis_test"
    assert artifact["kind"] == "numeric"
    assert artifact["hypothesis"] == {
        "family": "two_sample_mean",
        "alternative": "two_sided",
        "significance": "balanced",
        "alpha": 0.05,
    }
    assert isinstance(artifact["statistic"], float)
    assert isinstance(artifact["assumption_notes"], list)
    assert all(isinstance(note, str) for note in artifact["assumption_notes"])
    assert artifact["method"] == "welch_t"
    assert artifact["estimate"]["estimand"] == "mean_diff"
    assert "label" not in artifact["hypothesis"]
    assert "assumptions" not in artifact
    assert "left_ref" not in artifact
    assert "right_ref" not in artifact
    assert "sample_kind" not in artifact


def test_reads_sample_frames_by_artifact_id() -> None:
    artifact, resolver = _run_with_mock_data()

    assert resolver.call_args_list[0].args == ("session-1", "art_sample_current")
    assert resolver.call_args_list[1].args == ("session-1", "art_sample_baseline")
    assert artifact["source_lineage"]["current_sample_artifact_id"] == "art_sample_current"
    assert artifact["source_lineage"]["baseline_sample_artifact_id"] == "art_sample_baseline"
    assert artifact["source_lineage"]["sample_axis"] == {"source_axis": "time", "grain": "day"}


def test_does_not_resolve_metric_or_source_execution() -> None:
    runtime = _runtime()
    runtime.resolve_artifact_by_id.side_effect = [
        _sample_frame(artifact_id="art_sample_current"),
        _sample_frame(artifact_id="art_sample_baseline", mean=90.0),
    ]

    with (
        patch("marivo.runtime.intents.test.commit_step_result") as mock_commit,
        patch(
            "marivo.runtime.intents.test.resolve_predicate_lineage_reuse_for_intent",
            return_value={"issues": [], "fatal_message": None, "reuse_summary": None},
        ),
    ):
        mock_commit.return_value = {"artifact_id": "art_test"}
        run_test_intent(runtime, "session-1", _valid_params())

    assert not runtime.core.normalize_intent_metric_ref.called
    assert not runtime.core.metric_name_from_ref.called
    assert not runtime.resolve_metric_execution_context.called
    assert not runtime.resolve_metric.called
    assert not runtime.resolve_metric_dimensions.called
    assert not runtime.resolve_engine_for_session.called
    assert not runtime.resolve_metric_sql_for_execution.called
    assert not runtime.compile_step.called


def test_zero_variance_slice_adds_assumption_note() -> None:
    artifact, _ = _run_with_mock_data(
        current_sample=_sample_frame(
            artifact_id="art_sample_current",
            standard_deviation=0.0,
        )
    )

    assert any("zero variance" in note for note in artifact["assumption_notes"])


@pytest.mark.parametrize(
    ("grain", "start", "end", "bucket_expr"),
    [
        ("week", "2026-01-05T00:00:00Z", "2026-01-26T00:00:00Z", "week"),
        ("quarter", "2026-01-01T00:00:00Z", "2026-10-01T00:00:00Z", "quarter"),
        ("year", "2024-01-01T00:00:00Z", "2027-01-01T00:00:00Z", "year"),
    ],
)
def test_sample_summary_query_uses_required_grain(
    grain: str,
    start: str,
    end: str,
    bucket_expr: str,
) -> None:
    runtime = _runtime()
    runtime.resolve_metric_execution_context.return_value = SimpleNamespace(table_name="orders")
    runtime.resolve_metric.return_value = SimpleNamespace(
        semantic_object={"header": {"decomposition_semantics": "sum"}}
    )
    runtime.resolve_metric_dimensions.return_value = ["event_time"]
    runtime.resolve_engine_for_session.return_value = (
        MagicMock(),
        "duckdb",
        {"orders": "q_orders"},
    )
    runtime.resolve_metric_sql_for_execution.return_value = "SUM(revenue)"
    runtime.compile_step.return_value = SimpleNamespace(
        params=[],
        metadata={"engine_type": "duckdb"},
        ir_bundle={"plan": {"nodes": []}},
    )

    def _resolve_time_axis(resolved: Any, **_: Any) -> None:
        resolved.resolved_time_axis.analysis_time_expr = "event_time"

    runtime.resolve_windowed_query_time_axis.side_effect = _resolve_time_axis

    with (
        patch("marivo.runtime.intents._helpers.build_scoped_query_for_window") as mock_scoped,
        patch("marivo.runtime.intents._helpers.execute_compiled") as mock_execute,
    ):
        mock_scoped.return_value = {
            "mode": "single_window",
            "analysis_time_expr": "event_time",
            "analysis_time_kind": "timestamp",
            "engine_type": "duckdb",
            "current": {"start": start, "end": end},
        }
        mock_execute.return_value = SimpleNamespace(
            rows=[{"n": 3, "mean": 16.0, "standard_deviation": 6.0}]
        )

        summary = compute_numeric_sample_summary(
            runtime,
            "session-1",
            "metric.test_metric",
            {
                "field": "event_time",
                "start": start,
                "end": end,
            },
            grain=grain,  # type: ignore[arg-type]
        )

    assert summary.n == 3
    assert summary.mean == pytest.approx(16.0)
    assert summary.standard_deviation == pytest.approx(6.0)
    assert mock_scoped.call_args.kwargs["grain"] == grain
    compiled_step = runtime.compile_step.call_args.args[0]
    assert compiled_step.params["time_scope"]["grain"] == grain
    assert compiled_step.params["measures"] == [{"expr": "SUM(revenue)", "as": "value"}]
    assert compiled_step.params["group_by"] == [
        f"DATE_TRUNC('{bucket_expr}', event_time) AS bucket_start"
    ]
    assert compiled_step.params["order"] == "bucket_start"
    executed_query = mock_execute.call_args.args[1]
    assert "bucket_values AS" in executed_query.sql
    assert "SUM(revenue) AS value" in executed_query.sql
    assert "COUNT(value) AS n" in executed_query.sql
    assert "AVG(value) AS mean" in executed_query.sql
    assert "STDDEV_SAMP(value) AS standard_deviation" in executed_query.sql


def test_sample_summary_preserves_aoi_time_scope_field_for_bucket_axis() -> None:
    runtime = _runtime()
    runtime.resolve_metric_execution_context.return_value = SimpleNamespace(table_name="orders")
    runtime.resolve_metric.return_value = SimpleNamespace(
        semantic_object={"header": {"decomposition_semantics": "sum"}}
    )
    runtime.resolve_metric_dimensions.return_value = ["log_date_ts", "log_hour"]
    runtime.resolve_engine_for_session.return_value = (
        MagicMock(),
        "trino",
        {"orders": "q_orders"},
    )
    runtime.resolve_metric_sql_for_execution.return_value = "COUNT(*)"
    runtime.compile_step.return_value = SimpleNamespace(
        params=[],
        metadata={"engine_type": "trino"},
        ir_bundle={"plan": {"nodes": []}},
    )

    def _resolve_time_axis(resolved: Any, **_: Any) -> None:
        override = resolved.resolved_time_axis.override_analysis_time_column
        resolved.resolved_time_axis.analysis_time_expr = override

    runtime.resolve_windowed_query_time_axis.side_effect = _resolve_time_axis

    with (
        patch("marivo.runtime.intents._helpers.build_scoped_query_for_window") as mock_scoped,
        patch("marivo.runtime.intents._helpers.execute_compiled") as mock_execute,
    ):
        mock_scoped.return_value = {
            "mode": "single_window",
            "analysis_time_expr": "log_date_ts",
            "analysis_time_kind": "timestamp",
            "engine_type": "trino",
            "current": {"start": "2026-04-18", "end": "2026-05-18"},
        }
        mock_execute.return_value = SimpleNamespace(
            rows=[{"n": 30, "mean": 16.0, "standard_deviation": 6.0}]
        )

        compute_numeric_sample_summary(
            runtime,
            "session-1",
            "metric.test_metric",
            {
                "field": "log_date_ts",
                "start": "2026-04-18",
                "end": "2026-05-18",
            },
            grain="day",
        )

    first_resolved = runtime.resolve_windowed_query_time_axis.call_args_list[0].args[0]
    assert first_resolved.resolved_time_axis.override_analysis_time_column == "log_date_ts"
    scoped_kwargs = mock_scoped.call_args.kwargs
    assert scoped_kwargs["time_scope_field"] == "log_date_ts"
    compiled_step = runtime.compile_step.call_args.args[0]
    assert compiled_step.params["group_by"] == ["DATE_TRUNC('day', log_date_ts) AS bucket_start"]


def test_scoped_query_for_window_lowers_normalized_scope_to_predicate_filter() -> None:
    runtime = _runtime()

    def _resolve_time_axis(resolved: Any, **_: Any) -> None:
        resolved.resolved_time_axis.analysis_time_expr = "event_time"
        resolved.resolved_time_axis.analysis_time_kind = "timestamp"

    def _build_scoped_query(_session_id: str, request: Any, **_: Any) -> dict[str, Any]:
        return {"scope_predicate_filter": request.scope.predicate}

    runtime.resolve_windowed_query_time_axis.side_effect = _resolve_time_axis
    runtime.build_scoped_query.side_effect = _build_scoped_query

    scoped_query = build_scoped_query_for_window(
        runtime,
        session_id="session-1",
        engine_type="duckdb",
        metric_ref="metric.test_metric",
        table="orders",
        start="2026-01-01T00:00:00Z",
        end="2026-01-05T00:00:00Z",
        grain="day",
        scope_raw={"predicate": "region = 'US'"},
        all_dimensions=["event_time", "region"],
        time_scope_field="event_time",
    )

    assert scoped_query["scope_predicate_filter"] == "region = 'US'"


def test_sample_summary_accepts_count_metric_for_sum_semantics() -> None:
    runtime = _runtime()
    runtime.resolve_metric_execution_context.return_value = SimpleNamespace(table_name="orders")
    runtime.resolve_metric.return_value = SimpleNamespace(
        semantic_object={"header": {"decomposition_semantics": "sum"}}
    )
    runtime.resolve_metric_dimensions.return_value = ["event_time"]
    runtime.resolve_engine_for_session.return_value = (
        MagicMock(),
        "duckdb",
        {"orders": "q_orders"},
    )
    runtime.resolve_metric_sql_for_execution.return_value = "COUNT(*)"
    runtime.compile_step.return_value = SimpleNamespace(
        params=[],
        metadata={"engine_type": "duckdb"},
        ir_bundle={"plan": {"nodes": []}},
    )

    def _resolve_time_axis(resolved: Any, **_: Any) -> None:
        resolved.resolved_time_axis.analysis_time_expr = "event_time"

    runtime.resolve_windowed_query_time_axis.side_effect = _resolve_time_axis

    with (
        patch("marivo.runtime.intents._helpers.build_scoped_query_for_window") as mock_scoped,
        patch("marivo.runtime.intents._helpers.execute_compiled") as mock_execute,
    ):
        mock_scoped.return_value = {
            "mode": "single_window",
            "analysis_time_expr": "event_time",
            "analysis_time_kind": "timestamp",
            "engine_type": "duckdb",
            "current": {
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-01-05T00:00:00Z",
            },
        }
        mock_execute.return_value = SimpleNamespace(
            rows=[{"n": "4", "mean": "12.5", "standard_deviation": "3.5"}]
        )

        summary = compute_numeric_sample_summary(
            runtime,
            "session-1",
            "metric.test_metric",
            {
                "field": "event_time",
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-01-05T00:00:00Z",
            },
            grain="day",
        )

    assert summary.n == 4
    assert summary.mean == pytest.approx(12.5)
    assert summary.standard_deviation == pytest.approx(3.5)
    executed_query = mock_execute.call_args.args[1]
    assert "COUNT(*) AS value" in executed_query.sql
    assert "SUM(" not in executed_query.sql


@pytest.mark.parametrize("decomposition_semantics", ["ratio", "weighted_average"])
def test_sample_summary_rejects_non_sum_semantics_without_sum_shape_requirement(
    decomposition_semantics: str,
) -> None:
    runtime = _runtime()
    runtime.resolve_metric_execution_context.return_value = SimpleNamespace(table_name="orders")
    runtime.resolve_metric.return_value = SimpleNamespace(
        semantic_object={"header": {"decomposition_semantics": decomposition_semantics}}
    )
    runtime.resolve_metric_dimensions.return_value = ["event_time"]
    runtime.resolve_engine_for_session.return_value = (
        MagicMock(),
        "duckdb",
        {"orders": "q_orders"},
    )
    runtime.resolve_metric_sql_for_execution.return_value = "SUM(revenue)"

    def _resolve_time_axis(resolved: Any, **_: Any) -> None:
        resolved.resolved_time_axis.analysis_time_expr = "event_time"

    runtime.resolve_windowed_query_time_axis.side_effect = _resolve_time_axis

    with pytest.raises(ValueError, match="decomposition_semantics='sum'") as exc_info:
        compute_numeric_sample_summary(
            runtime,
            "session-1",
            "metric.test_metric",
            {
                "field": "event_time",
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-01-05T00:00:00Z",
            },
            grain="day",
        )

    assert "SUM(expr)" not in str(exc_info.value)


@pytest.mark.parametrize(
    ("current_sample", "baseline_sample", "message"),
    [
        (
            _sample_frame(artifact_id="art_sample_current", n=1),
            _sample_frame(artifact_id="art_sample_baseline", n=25, mean=90.0),
            "n >= 2",
        ),
        (
            _sample_frame(artifact_id="art_sample_current", mean=None),
            _sample_frame(artifact_id="art_sample_baseline", n=25, mean=90.0),
            "missing",
        ),
        (
            _sample_frame(artifact_id="art_sample_current", standard_deviation=0.0),
            _sample_frame(artifact_id="art_sample_baseline", standard_deviation=0.0),
            "standard error",
        ),
    ],
)
def test_rejects_insufficient_data(
    current_sample: dict[str, Any],
    baseline_sample: dict[str, Any],
    message: str,
) -> None:
    runtime = _runtime()
    runtime.resolve_artifact_by_id.side_effect = [current_sample, baseline_sample]

    with (
        patch(
            "marivo.runtime.intents.test.resolve_predicate_lineage_reuse_for_intent",
            return_value={"issues": [], "fatal_message": None, "reuse_summary": None},
        ),
        pytest.raises(ValueError, match=message),
    ):
        run_test_intent(runtime, "session-1", _valid_params())


@pytest.mark.parametrize(
    ("payload_patch", "message"),
    [
        (None, "params"),
        ({"__remove__": "current_sample_artifact_id"}, "current_sample_artifact_id"),
        ({"__remove__": "baseline_sample_artifact_id"}, "baseline_sample_artifact_id"),
        ({"__remove__": "hypothesis"}, "hypothesis"),
        ({"method": "welch_t"}, "method"),
        ({"current_sample_artifact_id": ""}, "current_sample_artifact_id"),
        ({"baseline_sample_artifact_id": None}, "baseline_sample_artifact_id"),
        ({"hypothesis": {"family": "two_sample_proportion"}}, "family"),
        ({"hypothesis": {"alternative": "not_equal"}}, "alternative"),
        ({"hypothesis": {"significance": "loose"}}, "significance"),
        ({"hypothesis": {"__remove__": "family"}}, "family"),
        ({"hypothesis": {"__remove__": "alternative"}}, "alternative"),
        ({"hypothesis": {"__remove__": "significance"}}, "significance"),
        ({"hypothesis": {"alpha": 0.05}}, "alpha"),
    ],
)
def test_rejects_non_current_request_shapes(
    payload_patch: dict[str, Any] | None,
    message: str,
) -> None:
    runtime = _runtime()
    params: dict[str, Any] | None = _valid_params()
    if payload_patch is None:
        params = None
    else:
        params = deepcopy(params)
        _merge_patch(params, payload_patch)

    with pytest.raises(ValueError, match=message):
        run_test_intent(runtime, "session-1", params)


@pytest.mark.parametrize(
    ("payload_patch", "message"),
    [
        ({"metric": "metric.test_metric"}, "unsupported"),
        ({"grain": "day"}, "unsupported"),
        ({"kind": "numeric"}, "unsupported"),
        (
            {
                "current": {
                    "time_scope": {
                        "field": "event_time",
                        "start": "2026-01-01",
                        "end": "2026-01-02",
                    }
                }
            },
            "unsupported",
        ),
        (
            {
                "baseline": {
                    "time_scope": {
                        "field": "event_time",
                        "start": "2026-01-01",
                        "end": "2026-01-02",
                    }
                }
            },
            "unsupported",
        ),
    ],
)
def test_rejects_removed_source_request_fields(
    payload_patch: dict[str, Any],
    message: str,
) -> None:
    params = _valid_params()
    params.update(payload_patch)

    with pytest.raises(ValueError, match=message):
        run_test_intent(_runtime(), "session-1", params)


def test_rejects_non_sample_frame_artifacts() -> None:
    runtime = _runtime()
    runtime.resolve_artifact_by_id.side_effect = [
        {"artifact_id": "art_metric", "artifact_family": "metric_frame"},
        _sample_frame(artifact_id="art_sample_baseline"),
    ]

    with pytest.raises(ValueError, match="sample_frame"):
        run_test_intent(runtime, "session-1", _valid_params())


@pytest.mark.parametrize(
    ("artifact_patch", "message"),
    [
        ({"axes": [{"kind": "sample", "source_axis": "time", "grain": "day"}] * 2}, "sample_frame"),
        (
            {
                "measures": [
                    {"id": "n", "value_type": "integer", "nullable": False},
                    {"id": "mean", "value_type": "number", "nullable": True},
                ]
            },
            "sample_frame",
        ),
        (
            {
                "measures": [
                    {"id": "n", "value_type": "number", "nullable": False},
                    {"id": "mean", "value_type": "number", "nullable": True},
                    {"id": "standard_deviation", "value_type": "number", "nullable": True},
                ]
            },
            "sample_frame",
        ),
    ],
)
def test_rejects_generated_sample_frame_contract_violations(
    artifact_patch: dict[str, Any],
    message: str,
) -> None:
    runtime = _runtime()
    current = _sample_frame(artifact_id="art_sample_current")
    current.update(artifact_patch)
    runtime.resolve_artifact_by_id.side_effect = [
        current,
        _sample_frame(artifact_id="art_sample_baseline"),
    ]

    with pytest.raises(ValueError, match=message):
        run_test_intent(runtime, "session-1", _valid_params())


def test_rejects_mismatched_sample_axes() -> None:
    runtime = _runtime()
    runtime.resolve_artifact_by_id.side_effect = [
        _sample_frame(artifact_id="art_sample_current", grain="day"),
        _sample_frame(artifact_id="art_sample_baseline", grain="week"),
    ]

    with pytest.raises(ValueError, match="sample axis"):
        run_test_intent(runtime, "session-1", _valid_params())


@pytest.mark.parametrize(
    ("axis_patch", "message"),
    [
        ({"kind": "bucket", "source_axis": "time", "grain": "day"}, "sample axis"),
        ({"kind": "sample", "source_axis": "region", "grain": "day"}, "sample axis"),
        ({"kind": "sample", "source_axis": "time", "grain": "minute"}, "grain"),
    ],
)
def test_rejects_malformed_sample_axis(
    axis_patch: dict[str, Any],
    message: str,
) -> None:
    runtime = _runtime()
    current = _sample_frame(artifact_id="art_sample_current")
    current["axes"] = [axis_patch]
    runtime.resolve_artifact_by_id.side_effect = [
        current,
        _sample_frame(artifact_id="art_sample_baseline"),
    ]

    with pytest.raises(ValueError, match=message):
        run_test_intent(runtime, "session-1", _valid_params())


def test_rejects_mismatched_sample_metric_refs() -> None:
    runtime = _runtime()
    runtime.resolve_artifact_by_id.side_effect = [
        _sample_frame(artifact_id="art_sample_current", metric_ref="metric.revenue"),
        _sample_frame(artifact_id="art_sample_baseline", metric_ref="metric.orders"),
    ]

    with pytest.raises(ValueError, match="metric_ref"):
        run_test_intent(runtime, "session-1", _valid_params())


@pytest.mark.parametrize(
    ("current_metric_ref", "baseline_metric_ref"),
    [
        (None, "metric.test_metric"),
        ("metric.test_metric", None),
        (None, None),
    ],
)
def test_rejects_missing_sample_metric_ref(
    current_metric_ref: str | None,
    baseline_metric_ref: str | None,
) -> None:
    runtime = _runtime()
    runtime.resolve_artifact_by_id.side_effect = [
        _sample_frame(artifact_id="art_sample_current", metric_ref=current_metric_ref),
        _sample_frame(artifact_id="art_sample_baseline", metric_ref=baseline_metric_ref),
    ]

    with pytest.raises(ValueError, match="metric_ref"):
        run_test_intent(runtime, "session-1", _valid_params())


@pytest.mark.parametrize("source_artifact_id", ["__missing__", "", 123])
def test_rejects_missing_or_invalid_source_artifact_id(source_artifact_id: Any) -> None:
    runtime = _runtime()
    runtime.resolve_artifact_by_id.side_effect = [
        _sample_frame(
            artifact_id="art_sample_current",
            source_artifact_id=source_artifact_id,
        ),
        _sample_frame(artifact_id="art_sample_baseline"),
    ]

    with pytest.raises(ValueError, match="source_artifact_id"):
        run_test_intent(runtime, "session-1", _valid_params())


@pytest.mark.parametrize(
    ("sample_patch", "message"),
    [
        ({"subject_kind": "metric"}, "subject.kind"),
        ({"lineage_operation": "observe"}, "lineage.operation"),
        ({"lineage_source_artifact_ids": "__missing__"}, "source_artifact_ids"),
        ({"lineage_source_artifact_ids": []}, "source_artifact_ids"),
        (
            {
                "source_artifact_id": "art_sample_current_other_source",
                "lineage_source_artifact_ids": ["art_sample_current_source"],
            },
            "source_artifact_id",
        ),
    ],
)
def test_rejects_malformed_sample_subject_or_lineage(
    sample_patch: dict[str, Any],
    message: str,
) -> None:
    runtime = _runtime()
    runtime.resolve_artifact_by_id.side_effect = [
        _sample_frame(artifact_id="art_sample_current", **sample_patch),
        _sample_frame(artifact_id="art_sample_baseline"),
    ]

    with pytest.raises(ValueError, match=message):
        run_test_intent(runtime, "session-1", _valid_params())


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("n", "30", "n"),
        ("mean", "100.0", "mean"),
        ("standard_deviation", "15.0", "standard_deviation"),
        ("n", True, "n"),
        ("mean", False, "mean"),
        ("standard_deviation", True, "standard_deviation"),
        ("n", -1, "n"),
        ("standard_deviation", -0.1, "standard_deviation"),
    ],
)
def test_rejects_malformed_sample_summary_stats(
    field: str,
    value: Any,
    message: str,
) -> None:
    runtime = _runtime()
    current = _sample_frame(artifact_id="art_sample_current")
    current["payload"]["summary"][field] = value
    runtime.resolve_artifact_by_id.side_effect = [
        current,
        _sample_frame(artifact_id="art_sample_baseline"),
    ]

    with pytest.raises(ValueError, match=message):
        run_test_intent(runtime, "session-1", _valid_params())


@pytest.mark.parametrize(
    ("current_quality", "baseline_quality"),
    [
        ("unsupported_source", "test_ready"),
        ("test_ready", "insufficient_data"),
    ],
)
def test_rejects_sample_frame_quality_not_test_ready(
    current_quality: str,
    baseline_quality: str,
) -> None:
    runtime = _runtime()
    runtime.resolve_artifact_by_id.side_effect = [
        _sample_frame(
            artifact_id="art_sample_current",
            quality_status=current_quality,
        ),
        _sample_frame(
            artifact_id="art_sample_baseline",
            n=25,
            mean=90.0,
            standard_deviation=12.0,
            quality_status=baseline_quality,
        ),
    ]

    with pytest.raises(ValueError, match=r"quality|test_ready"):
        run_test_intent(runtime, "session-1", _valid_params())


def _merge_patch(target: dict[str, Any], patch_value: dict[str, Any]) -> None:
    for key, value in patch_value.items():
        if key == "__remove__":
            target.pop(str(value))
            continue
        nested = target.get(key)
        if isinstance(value, dict) and isinstance(nested, dict):
            _merge_patch(nested, value)
        else:
            target[key] = value
