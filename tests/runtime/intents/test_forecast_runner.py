from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from marivo.runtime.intents.forecast import run_forecast_intent
from marivo.runtime.intents.metric_frame import build_metric_frame_artifact
from tests.runtime.intents._runner_fixtures import _FAKE_ARTIFACT_ID, _SESSION

_SOURCE_ARTIFACT_ID = "art_source_ts"
_SOURCE_STEP_ID = "step_source_obs"


def _bucket(start: str, end: str, value: Any) -> dict[str, Any]:
    return {"window": {"start": start, "end": end}, "value": value}


def _daily_points(values: list[Any]) -> list[dict[str, Any]]:
    return [
        _bucket(f"2026-01-{idx:02d}", f"2026-01-{idx + 1:02d}", value)
        for idx, value in enumerate(values, start=1)
    ]


def _time_series_artifact(
    *,
    values: list[Any] | None = None,
    points: list[dict[str, Any]] | None = None,
    granularity: str = "day",
    shape: str = "time_series",
) -> dict[str, Any]:
    pts = points if points is not None else _daily_points(values or [100.0, 110.0, 120.0])
    return build_metric_frame_artifact(
        artifact_id="art_metric_forecast_dau_time_series",
        shape=shape,
        metric_ref="metric.forecast_dau",
        time_scope={
            "field": "event_time",
            "start": pts[0]["window"]["start"] if pts else "2026-01-01",
            "end": pts[-1]["window"]["end"] if pts else "2026-01-01",
        },
        scope={},
        axes=[{"kind": "time", "grain": granularity}] if shape == "time_series" else [],
        series=[{"keys": {}, "points": pts}],
        unit=None,
    )


def _panel_artifact(
    *,
    values_by_region: dict[str, list[Any]] | None = None,
    granularity: str = "day",
) -> dict[str, Any]:
    resolved_values = values_by_region or {
        "US": [100.0, 110.0, 120.0],
        "EU": [200.0, 210.0, 220.0],
    }
    return build_metric_frame_artifact(
        artifact_id="art_metric_forecast_dau_panel",
        shape="panel",
        metric_ref="metric.forecast_dau",
        time_scope={"field": "event_time", "start": "2026-01-01", "end": "2026-01-04"},
        scope={},
        axes=[{"kind": "time", "grain": granularity}, {"kind": "dimension", "name": "region"}],
        series=[
            {"keys": {"region": region}, "points": _daily_points(values)}
            for region, values in resolved_values.items()
        ],
        unit=None,
    )


def _make_runtime(
    artifact: dict[str, Any] | None = None,
    *,
    resolved: tuple[str, dict[str, Any]] | None = None,
) -> MagicMock:
    runtime = MagicMock()
    runtime.core = MagicMock()
    if resolved is not None:
        runtime.resolve_artifact_with_step_by_id.return_value = resolved
    else:
        runtime.resolve_artifact_with_step_by_id.return_value = (
            _SOURCE_STEP_ID,
            artifact or _time_series_artifact(),
        )
    runtime.commit_artifact_with_extraction.return_value = _FAKE_ARTIFACT_ID
    runtime.insert_step.return_value = None
    return runtime


def _run_forecast(runtime: MagicMock, **overrides: Any) -> dict[str, Any]:
    params: dict[str, Any] = {"source_artifact_id": _SOURCE_ARTIFACT_ID, "horizon": 3}
    params.update(overrides)
    return run_forecast_intent(runtime, _SESSION, params)


def _assert_forecast_fails_without_commit(
    runtime: MagicMock,
    params: dict[str, Any],
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        run_forecast_intent(runtime, _SESSION, params)
    runtime.commit_artifact_with_extraction.assert_not_called()
    runtime.insert_step.assert_not_called()


def test_forecast_resolves_source_by_artifact_id_and_records_source_lineage() -> None:
    runtime = _make_runtime()

    result = _run_forecast(runtime, horizon=1)

    runtime.resolve_artifact_with_step_by_id.assert_called_once_with(_SESSION, _SOURCE_ARTIFACT_ID)
    assert result["artifact_id"] == _FAKE_ARTIFACT_ID
    assert result["source_ref"] == {
        "step_type": "observe",
        "session_id": _SESSION,
        "step_id": _SOURCE_STEP_ID,
        "artifact_id": _SOURCE_ARTIFACT_ID,
        "source_shape": "time_series",
    }
    assert result["source_lineage"]["source_artifact_ref"] == result["source_ref"]
    assert result["source_lineage"]["source_metric_contract_version"] is None


def test_forecast_commits_forecast_series_artifact_and_step() -> None:
    runtime = _make_runtime()

    result = _run_forecast(runtime, horizon=2)

    runtime.commit_artifact_with_extraction.assert_called_once()
    args, kwargs = runtime.commit_artifact_with_extraction.call_args
    assert args[0] == _SESSION
    assert args[2] == "forecast_series"
    assert kwargs["step_type"] == "forecast"
    runtime.insert_step.assert_called_once()
    assert result["intent_type"] == "forecast"
    assert result["step_type"] == "forecast"
    assert result["observation_type"] == "forecast_series"


def test_forecast_outputs_display_metric_name() -> None:
    runtime = _make_runtime()

    result = _run_forecast(runtime, horizon=1)

    assert result["metric"] == "forecast_dau"


def test_forecast_auto_selects_trend_with_sufficient_history() -> None:
    runtime = _make_runtime(_time_series_artifact(values=[100.0, 110.0, 121.0, 133.0]))

    result = _run_forecast(runtime, horizon=3)

    assert result["profile"] == "trend"
    assert result["analytical_metadata"]["trend_assumption"] == "included"
    assert result["analytical_metadata"]["seasonality_assumption"] == "none"
    assert result["execution_metadata"]["model_family"] == "ols_linear"
    assert [bucket["bucket_index"] for bucket in result["forecast"]] == [1, 2, 3]
    assert result["forecast"][1]["point_forecast"] > result["forecast"][0]["point_forecast"]


def test_forecast_auto_falls_back_to_level_for_minimal_history() -> None:
    runtime = _make_runtime(_time_series_artifact(values=[42.0]))

    result = _run_forecast(runtime, horizon=2)

    assert result["profile"] == "level"
    assert result["analytical_metadata"]["trend_assumption"] == "none"
    assert result["analytical_metadata"]["seasonality_assumption"] == "not_applicable"
    assert result["execution_metadata"]["model_family"] == "level"
    assert [bucket["point_forecast"] for bucket in result["forecast"]] == [42.0, 42.0]
    assert [bucket["prediction_interval"] for bucket in result["forecast"]] == [None, None]


def test_forecast_uses_fixed_internal_interval_level() -> None:
    runtime = _make_runtime(_time_series_artifact(values=[10.0, 13.0, 18.0, 30.0]))

    result = _run_forecast(runtime, horizon=2)

    assert result["interval_level"] == 0.95
    intervals = [bucket["prediction_interval"] for bucket in result["forecast"]]
    assert all(interval is not None for interval in intervals)
    assert {interval["level"] for interval in intervals if interval is not None} == {0.95}


def test_forecast_history_summary_counts_dropped_non_numeric_points() -> None:
    runtime = _make_runtime(_time_series_artifact(values=[100.0, None, "bad", 130.0, 140.0]))

    result = _run_forecast(runtime, horizon=2)

    assert result["history_summary"]["observed_points"] == 5
    assert result["history_summary"]["usable_points"] == 3
    assert result["history_summary"]["dropped_points"] == 2
    assert result["profile"] == "trend"
    assert result["history_summary"]["last_observed_window"] == {
        "start": "2026-01-05",
        "end": "2026-01-06",
    }


def test_forecast_long_horizon_returns_needs_attention_warning() -> None:
    runtime = _make_runtime(_time_series_artifact(values=[10.0, 20.0, 30.0]))

    result = _run_forecast(runtime, horizon=7)

    assert result["forecastability"]["status"] == "needs_attention"
    assert [issue["code"] for issue in result["forecastability"]["issues"]] == [
        "long_horizon_warning"
    ]


def test_forecast_moderate_horizon_is_forecastable() -> None:
    runtime = _make_runtime()

    result = _run_forecast(runtime, horizon=3)

    assert result["forecastability"] == {"status": "forecastable", "issues": []}


def test_forecast_accepts_panel_metric_frame_and_preserves_series_keys() -> None:
    runtime = _make_runtime(
        _panel_artifact(values_by_region={"US": [100.0, 110.0, 120.0], "EU": [40.0, 45.0, 50.0]})
    )

    result = _run_forecast(runtime, horizon=2)

    assert result["source_ref"]["source_shape"] == "panel"
    assert result["history_summary"]["series_count"] == 2
    assert result["forecast"][0]["keys"] == {"region": "US"}
    assert result["forecast"][1]["keys"] == {"region": "EU"}
    assert [len(series["points"]) for series in result["forecast"]] == [2, 2]
    assert [point["bucket_index"] for point in result["forecast"][0]["points"]] == [1, 2]
    assert result["forecast"][0]["points"][0]["window"] == {
        "start": "2026-01-04",
        "end": "2026-01-05",
    }


@pytest.mark.parametrize(
    ("granularity", "points", "expected_windows"),
    [
        (
            "hour",
            [_bucket("2026-01-01T00:00:00+00:00", "2026-01-01T01:00:00+00:00", 10.0)],
            [
                {"start": "2026-01-01T01:00:00+00:00", "end": "2026-01-01T02:00:00+00:00"},
                {"start": "2026-01-01T02:00:00+00:00", "end": "2026-01-01T03:00:00+00:00"},
            ],
        ),
        (
            "day",
            [_bucket("2026-01-01", "2026-01-02", 10.0)],
            [
                {"start": "2026-01-02", "end": "2026-01-03"},
                {"start": "2026-01-03", "end": "2026-01-04"},
            ],
        ),
        (
            "week",
            [_bucket("2026-01-05", "2026-01-12", 10.0)],
            [
                {"start": "2026-01-12", "end": "2026-01-19"},
                {"start": "2026-01-19", "end": "2026-01-26"},
            ],
        ),
        (
            "month",
            [_bucket("2026-01-01", "2026-02-01", 10.0)],
            [
                {"start": "2026-02-01", "end": "2026-03-01"},
                {"start": "2026-03-01", "end": "2026-04-01"},
            ],
        ),
    ],
)
def test_forecast_future_windows_follow_source_granularity(
    granularity: str,
    points: list[dict[str, Any]],
    expected_windows: list[dict[str, str]],
) -> None:
    runtime = _make_runtime(_time_series_artifact(points=points, granularity=granularity))

    result = _run_forecast(runtime, horizon=2)

    assert [bucket["window"] for bucket in result["forecast"]] == expected_windows


@pytest.mark.parametrize("horizon", [1, 90])
def test_forecast_accepts_horizon_bounds(horizon: int) -> None:
    runtime = _make_runtime()

    result = _run_forecast(runtime, horizon=horizon)

    assert len(result["forecast"]) == horizon


@pytest.mark.parametrize(
    ("params", "match"),
    [
        ({}, "'horizon' is required"),
        ({"source_artifact_id": _SOURCE_ARTIFACT_ID}, "'horizon' is required"),
        ({"source_artifact_id": _SOURCE_ARTIFACT_ID, "horizon": 0}, "horizon must be"),
        ({"source_artifact_id": _SOURCE_ARTIFACT_ID, "horizon": 91}, "horizon must be"),
        (
            {"source_artifact_id": _SOURCE_ARTIFACT_ID, "horizon": "not_int"},
            "'horizon' must be an integer",
        ),
        ({"horizon": 1}, "source_artifact_id is required"),
        ({"source_artifact_id": " ", "horizon": 1}, "source_artifact_id is required"),
    ],
)
def test_forecast_rejects_invalid_required_parameters(
    params: dict[str, Any],
    match: str,
) -> None:
    runtime = _make_runtime()

    _assert_forecast_fails_without_commit(runtime, params, match)


def test_forecast_reports_missing_source_artifact() -> None:
    runtime = _make_runtime(resolved=None)
    runtime.resolve_artifact_with_step_by_id.return_value = None

    _assert_forecast_fails_without_commit(
        runtime,
        {"source_artifact_id": _SOURCE_ARTIFACT_ID, "horizon": 1},
        "ARTIFACT_NOT_FOUND",
    )


def test_forecast_rejects_non_metric_frame_artifact_before_shape_read() -> None:
    runtime = _make_runtime({"shape": "time_series", "series": []})

    _assert_forecast_fails_without_commit(
        runtime,
        {"source_artifact_id": _SOURCE_ARTIFACT_ID, "horizon": 1},
        "artifact_family='metric_frame'",
    )


@pytest.mark.parametrize("shape", ["scalar", "segmented"])
def test_forecast_rejects_non_forecastable_metric_frame_shapes(shape: str) -> None:
    runtime = _make_runtime(_time_series_artifact(shape=shape))

    _assert_forecast_fails_without_commit(
        runtime,
        {"source_artifact_id": _SOURCE_ARTIFACT_ID, "horizon": 1},
        r"metric_frame\(time_series\|panel\)",
    )


def test_forecast_rejects_unsupported_source_granularity() -> None:
    runtime = _make_runtime(_time_series_artifact(granularity="quarter"))

    _assert_forecast_fails_without_commit(
        runtime,
        {"source_artifact_id": _SOURCE_ARTIFACT_ID, "horizon": 1},
        "UNSUPPORTED_OPERATION",
    )


def test_forecast_rejects_zero_usable_history_points() -> None:
    runtime = _make_runtime(_time_series_artifact(values=[None, "bad"]))

    _assert_forecast_fails_without_commit(
        runtime,
        {"source_artifact_id": _SOURCE_ARTIFACT_ID, "horizon": 1},
        "INSUFFICIENT_HISTORY",
    )
