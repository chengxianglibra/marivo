"""Tests for the current AOI test intent runner contract."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from marivo.runtime.intents._helpers import (
    SampleSummary,
    build_scoped_query_for_window,
    compute_numeric_sample_summary,
)
from marivo.runtime.intents.test import _betai, _p_value_from_t, _t_sf, run_test_intent


def _valid_params() -> dict[str, Any]:
    return {
        "metric": "metric.test_metric",
        "current": {
            "time_scope": {
                "field": "event_time",
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-01-08T00:00:00Z",
            }
        },
        "baseline": {
            "time_scope": {
                "field": "event_time",
                "start": "2026-01-08T00:00:00Z",
                "end": "2026-01-15T00:00:00Z",
            }
        },
        "grain": "day",
        "kind": "numeric",
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


def _sample(
    *,
    n: int | None = 30,
    mean: float | None = 100.0,
    standard_deviation: float | None = 15.0,
    predicate_filter_lineage: dict[str, Any] | None = None,
) -> SampleSummary:
    return SampleSummary(
        n=n,
        mean=mean,
        standard_deviation=standard_deviation,
        predicate_filter_lineage=predicate_filter_lineage,
    )


def _run_with_mock_data(
    params: dict[str, Any] | None = None,
    *,
    left_summary: SampleSummary | None = None,
    right_summary: SampleSummary | None = None,
) -> tuple[dict[str, Any], MagicMock]:
    runtime = _runtime()
    params = deepcopy(params if params is not None else _valid_params())

    with patch("marivo.runtime.intents.test.compute_numeric_sample_summary") as mock_compute:
        mock_compute.side_effect = [
            left_summary or _sample(),
            right_summary or _sample(n=25, mean=90.0, standard_deviation=12.0),
        ]
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
                return artifact, mock_compute


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


def test_passes_filters_to_sample_summaries_and_source_lineage() -> None:
    params = _valid_params()
    left_filter = {"dialects": [{"dialect": "ANSI_SQL", "expression": "region = 'US'"}]}
    right_filter = {"dialects": [{"dialect": "ANSI_SQL", "expression": "region = 'CA'"}]}
    left_scope = {"predicate": "region = 'US'"}
    right_scope = {"predicate": "region = 'CA'"}
    params["current"]["filter"] = left_filter
    params["baseline"]["filter"] = right_filter

    artifact, mock_compute = _run_with_mock_data(params)

    assert mock_compute.call_args_list[0].kwargs["scope_raw"] == left_scope
    assert mock_compute.call_args_list[1].kwargs["scope_raw"] == right_scope
    assert mock_compute.call_args_list[0].kwargs["grain"] == "day"
    assert mock_compute.call_args_list[1].kwargs["grain"] == "day"
    assert artifact["source_lineage"]["grain"] == "day"
    assert artifact["source_lineage"]["current"]["filter"] == left_filter
    assert artifact["source_lineage"]["baseline"]["filter"] == right_filter


def test_query_hash_includes_normalized_slice_filters() -> None:
    params = _valid_params()
    params["current"]["filter"] = {
        "dialects": [{"dialect": "ANSI_SQL", "expression": "region = 'US'"}]
    }
    params["baseline"]["filter"] = {
        "dialects": [{"dialect": "ANSI_SQL", "expression": "region = 'CA'"}]
    }
    filtered_artifact, _ = _run_with_mock_data(params)

    other_params = deepcopy(params)
    other_params["current"]["filter"] = {
        "dialects": [{"dialect": "ANSI_SQL", "expression": "region = 'MX'"}]
    }
    other_artifact, _ = _run_with_mock_data(other_params)

    assert (
        filtered_artifact["execution_metadata"]["query_hash"]
        != other_artifact["execution_metadata"]["query_hash"]
    )


@pytest.mark.parametrize("grain", ["quarter", "year"])
def test_accepts_time_granularity_grain(grain: str) -> None:
    params = _valid_params()
    params["grain"] = grain

    artifact, mock_compute = _run_with_mock_data(params)

    assert mock_compute.call_args_list[0].kwargs["grain"] == grain
    assert artifact["source_lineage"]["grain"] == grain


def test_zero_variance_slice_adds_assumption_note() -> None:
    artifact, _ = _run_with_mock_data(left_summary=_sample(standard_deviation=0.0))

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
    ("left_summary", "right_summary", "message"),
    [
        (_sample(n=1), _sample(n=25, mean=90.0, standard_deviation=12.0), "n >= 2"),
        (_sample(mean=None), _sample(n=25, mean=90.0, standard_deviation=12.0), "missing"),
        (_sample(standard_deviation=0.0), _sample(standard_deviation=0.0), "standard error"),
    ],
)
def test_rejects_insufficient_data(
    left_summary: SampleSummary,
    right_summary: SampleSummary,
    message: str,
) -> None:
    runtime = _runtime()

    with (
        patch("marivo.runtime.intents.test.compute_numeric_sample_summary") as mock_compute,
        patch(
            "marivo.runtime.intents.test.resolve_predicate_lineage_reuse_for_intent",
            return_value={"issues": [], "fatal_message": None, "reuse_summary": None},
        ),
    ):
        mock_compute.side_effect = [left_summary, right_summary]
        with pytest.raises(ValueError, match=message):
            run_test_intent(runtime, "session-1", _valid_params())


def test_time_derived_slice_filter_fails_fast_instead_of_running_unfiltered() -> None:
    params = _valid_params()
    params["current"]["filter"] = {
        "dialects": [
            {
                "dialect": "ANSI_SQL",
                "expression": "EXTRACT(DAY_OF_WEEK FROM event_time) BETWEEN 1 AND 5",
            }
        ]
    }
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

    def _resolve_time_axis(resolved: Any, **_: Any) -> None:
        resolved.resolved_time_axis.analysis_time_expr = "event_time"

    runtime.resolve_windowed_query_time_axis.side_effect = _resolve_time_axis

    with (
        patch(
            "marivo.runtime.intents.test.resolve_predicate_lineage_reuse_for_intent",
            return_value={"issues": [], "fatal_message": None, "reuse_summary": None},
        ),
        pytest.raises(
            ValueError,
            match=(
                r"test: INVALID_ARGUMENT - current\.filter "
                r"scope\.predicate must not contain time-axis predicates"
            ),
        ),
    ):
        run_test_intent(runtime, "session-1", params)


@pytest.mark.parametrize(
    ("payload_patch", "message"),
    [
        (None, "params"),
        ({"__remove__": "metric"}, "metric"),
        ({"__remove__": "kind"}, "kind"),
        ({"__remove__": "grain"}, "grain"),
        ({"__remove__": "hypothesis"}, "hypothesis"),
        ({"method": "welch_t"}, "method"),
        ({"kind": "Numeric"}, "kind"),
        ({"kind": "rate"}, "kind"),
        ({"grain": "minute"}, "grain"),
        ({"grain": None}, "grain"),
        ({"current": {"scope": {"predicate": "region = 'US'"}}}, "scope"),
        ({"current": {"filter": None}}, "filter"),
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
