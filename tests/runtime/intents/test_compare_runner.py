from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from tests.runtime.intents._runner_fixtures import (
    _FAKE_ARTIFACT_ID,
    _SESSION,
    _FakeCalendarDataReader,
    _panel_observation_v2,
    _scalar_observation_v2,
    _segmented_observation_v2,
    _time_series_observation_v2,
)

_LEFT_ARTIFACT_ID = "art_left_obs"
_RIGHT_ARTIFACT_ID = "art_right_obs"
_LEFT_STEP_ID = "step_left"
_RIGHT_STEP_ID = "step_right"


def _make_runtime(
    left_artifact: dict[str, Any] | None = None,
    right_artifact: dict[str, Any] | None = None,
) -> MagicMock:
    runtime = MagicMock()
    runtime.core = MagicMock()
    runtime.commit_artifact_with_extraction.return_value = _FAKE_ARTIFACT_ID
    runtime.insert_step.return_value = None
    runtime.resolve_artifact_with_step_by_id.side_effect = [
        (_LEFT_STEP_ID, left_artifact or _scalar_observation_v2("m1")),
        (_RIGHT_STEP_ID, right_artifact or _scalar_observation_v2("m1")),
    ]
    return runtime


def _compare_params(compare_type: str | None = None) -> dict[str, Any]:
    params = {
        "current_artifact_id": _LEFT_ARTIFACT_ID,
        "baseline_artifact_id": _RIGHT_ARTIFACT_ID,
    }
    if compare_type is not None:
        params["compare_type"] = compare_type
    return params


def _run_compare(
    runtime: MagicMock,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from marivo.runtime.intents.compare import run_compare_intent

    return run_compare_intent(runtime, _SESSION, params or _compare_params())


def _set_metric_frame_time_scope(artifact: dict[str, Any], time_scope: dict[str, Any]) -> None:
    artifact["subject"]["time_scope"] = time_scope


def test_compare_calls_commit_artifact_with_extraction() -> None:
    runtime = _make_runtime()

    _run_compare(runtime)

    runtime.commit_artifact_with_extraction.assert_called_once()


def test_compare_passes_step_type_compare() -> None:
    runtime = _make_runtime()

    _run_compare(runtime)

    _, kwargs = runtime.commit_artifact_with_extraction.call_args
    assert kwargs.get("step_type") == "compare"


def test_compare_artifact_type_is_compare_artifact() -> None:
    runtime = _make_runtime()

    _run_compare(runtime)

    args, _ = runtime.commit_artifact_with_extraction.call_args
    assert args[2] == "compare_artifact"


def test_compare_resolves_inputs_by_artifact_id_and_records_lineage() -> None:
    runtime = _make_runtime()

    result = _run_compare(runtime)

    assert result["current_ref"]["step_id"] == _LEFT_STEP_ID
    assert result["current_ref"]["artifact_id"] == _LEFT_ARTIFACT_ID
    assert result["baseline_ref"]["step_id"] == _RIGHT_STEP_ID
    assert result["baseline_ref"]["artifact_id"] == _RIGHT_ARTIFACT_ID
    assert result["lineage"]["current_source_ref"]["artifact_id"] == _LEFT_ARTIFACT_ID
    assert result["lineage"]["baseline_source_ref"]["artifact_id"] == _RIGHT_ARTIFACT_ID
    assert result["resolved_input_summary"]["current_time_scope"] == {
        "field": "time",
        "start": "2024-01-01",
        "end": "2024-01-08",
    }


def test_compare_omitted_compare_type_defaults_to_normal() -> None:
    runtime = _make_runtime()

    result = _run_compare(runtime, _compare_params(compare_type=None))

    assert result["lineage"]["compare_type"] == "normal"
    assert result["analytical_metadata"]["compare_type"] == "normal"


@pytest.mark.parametrize(
    "payload",
    [
        {"current_artifact_id": _LEFT_ARTIFACT_ID},
        {"baseline_artifact_id": _RIGHT_ARTIFACT_ID},
        {"current_artifact_id": " ", "baseline_artifact_id": _RIGHT_ARTIFACT_ID},
        {"current_artifact_id": _LEFT_ARTIFACT_ID, "baseline_artifact_id": " "},
    ],
)
def test_compare_requires_both_artifact_ids(payload: dict[str, Any]) -> None:
    runtime = _make_runtime()

    with pytest.raises(ValueError, match="both current_artifact_id and baseline_artifact_id"):
        _run_compare(runtime, payload)


def test_compare_rejects_unknown_compare_type() -> None:
    runtime = _make_runtime()

    with pytest.raises(ValueError, match="Unknown compare_type 'not_real'"):
        _run_compare(runtime, _compare_params("not_real"))


def test_compare_reports_missing_current_artifact_id() -> None:
    runtime = MagicMock()
    runtime.resolve_artifact_with_step_by_id.return_value = None

    with pytest.raises(ValueError, match="current_artifact_id 'art_left_obs'"):
        _run_compare(runtime)


def test_compare_reports_missing_baseline_artifact_id() -> None:
    runtime = MagicMock()
    runtime.resolve_artifact_with_step_by_id.side_effect = [
        (_LEFT_STEP_ID, _scalar_observation_v2("m1")),
        None,
    ]

    with pytest.raises(ValueError, match="baseline_artifact_id 'art_right_obs'"):
        _run_compare(runtime)


@pytest.mark.parametrize(
    "left_artifact",
    [_scalar_observation_v2("m1"), _segmented_observation_v2("m1")],
)
def test_compare_type_non_normal_rejects_non_time_series_observations(
    left_artifact: dict[str, Any],
) -> None:
    runtime = _make_runtime(left_artifact, left_artifact)

    with pytest.raises(ValueError, match="compare_type 'weekday_aligned' requires time_series"):
        _run_compare(runtime, _compare_params("weekday_aligned"))


def test_compare_time_series_commits_time_series_delta() -> None:
    runtime = _make_runtime(
        _time_series_observation_v2("m1"),
        _time_series_observation_v2(
            "m1",
            points=[
                {"window": {"start": "2024-01-01", "end": "2024-01-02"}, "value": 8.0},
                {"window": {"start": "2024-01-02", "end": "2024-01-03"}, "value": 15.0},
            ],
        ),
    )

    result = _run_compare(runtime)

    assert result["comparison_type"] == "time_series_delta"
    assert result["axes"] == [{"kind": "time", "grain": "day"}]
    points = result["series"][0]["points"]
    assert len(points) == 2
    assert result["summary_current_value"] == 30.0
    assert result["summary_baseline_value"] == 23.0
    assert result["analytical_metadata"]["pairing_basis"] == "input_artifact_window_position"
    assert result["analytical_metadata"]["pairing_rule"] == "relative_bucket_position"


def test_compare_time_series_derives_coverage_from_source_series() -> None:
    current = _time_series_observation_v2(
        "m1",
        points=[
            {"window": {"start": "2026-05-12", "end": "2026-05-13"}, "value": 10.0},
            {"window": {"start": "2026-05-13", "end": "2026-05-14"}, "value": 0.0},
            {"window": {"start": "2026-05-14", "end": "2026-05-15"}, "value": None},
        ],
    )
    baseline = _time_series_observation_v2(
        "m1",
        points=[
            {"window": {"start": "2026-05-05", "end": "2026-05-06"}, "value": 8.0},
            {"window": {"start": "2026-05-06", "end": "2026-05-07"}, "value": 1.0},
            {"window": {"start": "2026-05-07", "end": "2026-05-08"}, "value": 2.0},
        ],
    )
    runtime = _make_runtime(current, baseline)

    result = _run_compare(runtime)

    assert result["coverage"] == {
        "current": {
            "grain": "day",
            "requested_units": 3,
            "covered_units": 2,
            "missing_units": ["2026-05-14"],
        },
        "baseline": {
            "grain": "day",
            "requested_units": 3,
            "covered_units": 3,
            "missing_units": [],
        },
    }
    assert result["comparability"]["status"] == "needs_attention"
    assert {issue["code"]: issue for issue in result["comparability"]["issues"]}[
        "coverage_mismatch"
    ] == {
        "code": "coverage_mismatch",
        "severity": "warning",
        "message": "current and baseline time-series coverage differ",
        "details": result["coverage"],
    }


def test_compare_time_series_matching_coverage_has_no_coverage_warning() -> None:
    runtime = _make_runtime(
        _time_series_observation_v2("m1"),
        _time_series_observation_v2("m1"),
    )

    result = _run_compare(runtime)

    assert result["coverage"] == {
        "current": {
            "grain": "day",
            "requested_units": 2,
            "covered_units": 2,
            "missing_units": [],
        },
        "baseline": {
            "grain": "day",
            "requested_units": 2,
            "covered_units": 2,
            "missing_units": [],
        },
    }
    assert result["comparability"] == {"status": "comparable", "issues": []}


def test_compare_time_series_matching_relative_coverage_ignores_absolute_missing_dates() -> None:
    current = _time_series_observation_v2(
        "m1",
        points=[
            {"window": {"start": "2026-05-12", "end": "2026-05-13"}, "value": 10.0},
            {"window": {"start": "2026-05-13", "end": "2026-05-14"}, "value": None},
        ],
    )
    baseline = _time_series_observation_v2(
        "m1",
        points=[
            {"window": {"start": "2026-05-05", "end": "2026-05-06"}, "value": 8.0},
            {"window": {"start": "2026-05-06", "end": "2026-05-07"}, "value": None},
        ],
    )
    runtime = _make_runtime(current, baseline)

    result = _run_compare(runtime)

    assert result["coverage"] == {
        "current": {
            "grain": "day",
            "requested_units": 2,
            "covered_units": 1,
            "missing_units": ["2026-05-13"],
        },
        "baseline": {
            "grain": "day",
            "requested_units": 2,
            "covered_units": 1,
            "missing_units": ["2026-05-06"],
        },
    }
    assert result["comparability"] == {"status": "comparable", "issues": []}


def test_compare_segmented_commits_segmented_delta() -> None:
    left = _segmented_observation_v2(
        "m1",
        dimensions=["country"],
        series=[
            {"keys": {"country": "US"}, "points": [{"value": 100.0}]},
            {"keys": {"country": "CA"}, "points": [{"value": 50.0}]},
        ],
    )
    right = _segmented_observation_v2(
        "m1",
        dimensions=["country"],
        series=[
            {"keys": {"country": "US"}, "points": [{"value": 80.0}]},
            {"keys": {"country": "MX"}, "points": [{"value": 30.0}]},
        ],
    )
    runtime = _make_runtime(left, right)

    result = _run_compare(runtime)

    assert result["comparison_type"] == "segmented_delta"
    assert "coverage" not in result
    assert result["scope_absolute_delta"] == 40.0
    assert result["lineage"]["compare_type"] == "normal"
    series_entries = result["series"]
    assert {entry["points"][0]["presence"] for entry in series_entries} == {
        "both",
        "current_only",
        "baseline_only",
    }


def test_compare_segmented_log_hour_commits_segmented_delta() -> None:
    left = _segmented_observation_v2(
        "m1",
        dimensions=["log_hour"],
        series=[
            {"keys": {"log_hour": "09"}, "points": [{"value": 100.0}]},
            {"keys": {"log_hour": "10"}, "points": [{"value": 60.0}]},
        ],
    )
    right = _segmented_observation_v2(
        "m1",
        dimensions=["log_hour"],
        series=[
            {"keys": {"log_hour": "09"}, "points": [{"value": 70.0}]},
            {"keys": {"log_hour": "11"}, "points": [{"value": 20.0}]},
        ],
    )
    runtime = _make_runtime(left, right)

    result = _run_compare(runtime)

    assert result["comparison_type"] == "segmented_delta"
    assert result["axes"] == [{"kind": "dimension", "name": "log_hour"}]
    assert result["scope_absolute_delta"] == 70.0
    series_entries = result["series"]
    assert {next(iter(entry["keys"].items())) for entry in series_entries} == {
        ("log_hour", "09"),
        ("log_hour", "10"),
        ("log_hour", "11"),
    }
    assert {entry["points"][0]["presence"] for entry in series_entries} == {
        "both",
        "current_only",
        "baseline_only",
    }


def test_compare_panel_metric_frame_is_explicitly_unsupported() -> None:
    runtime = _make_runtime(_panel_observation_v2("m1"), _panel_observation_v2("m1"))

    with pytest.raises(
        ValueError,
        match=(
            "compare: UNSUPPORTED_OPERATION - panel metric_frame comparison is not supported yet"
        ),
    ):
        _run_compare(runtime)

    runtime.commit_artifact_with_extraction.assert_not_called()


def test_compare_rejects_legacy_observation_artifacts_without_commit() -> None:
    legacy_artifact = {
        "artifact_id": "art_legacy_obs",
        "observation_type": "time_series",
        "metric": "m1",
        "series": [{"window": {"start": "2024-01-01", "end": "2024-01-02"}, "value": 10.0}],
    }
    runtime = _make_runtime(legacy_artifact, _time_series_observation_v2("m1"))

    with pytest.raises(
        ValueError,
        match="current artifact must be metric_frame with top-level artifact_family='metric_frame'",
    ):
        _run_compare(runtime)

    runtime.commit_artifact_with_extraction.assert_not_called()


def test_compare_panel_shape_rejects_as_unsupported_even_with_malformed_axes() -> None:
    left = _panel_observation_v2("m1")
    right = _panel_observation_v2("m1")
    left["axes"] = [{"kind": "dimension"}]
    right["axes"] = []
    runtime = _make_runtime(left, right)

    with pytest.raises(
        ValueError,
        match=(
            "compare: UNSUPPORTED_OPERATION - panel metric_frame comparison is not supported yet"
        ),
    ):
        _run_compare(runtime)

    runtime.commit_artifact_with_extraction.assert_not_called()


def test_compare_rejects_shape_axes_invariant_violation_without_commit() -> None:
    current = _time_series_observation_v2("m1")
    current["axes"] = []
    runtime = _make_runtime(current, _time_series_observation_v2("m1"))

    with pytest.raises(
        ValueError,
        match="time_series metric_frame requires one time axis with grain",
    ):
        _run_compare(runtime)

    runtime.commit_artifact_with_extraction.assert_not_called()


def test_compare_segmented_dimension_mismatch_is_not_comparable() -> None:
    left = _segmented_observation_v2("m1")
    right = _segmented_observation_v2(
        "m1",
        dimensions=["device"],
        series=[{"keys": {"device": "ios"}, "points": [{"value": 100.0}]}],
    )
    runtime = _make_runtime(left, right)

    with pytest.raises(ValueError, match="compare: NOT_COMPARABLE - left dimensions"):
        _run_compare(runtime)


def test_compare_metric_mismatch_is_not_comparable() -> None:
    runtime = _make_runtime(_scalar_observation_v2("m1"), _scalar_observation_v2("m2"))

    with pytest.raises(ValueError, match="compare: NOT_COMPARABLE"):
        _run_compare(runtime)


def test_compare_type_normal_aligns_non_overlapping_windows_by_relative_position() -> None:
    left = _time_series_observation_v2(
        "m1",
        points=[
            {"window": {"start": "2026-02-14", "end": "2026-02-15"}, "value": 10.0},
            {"window": {"start": "2026-02-15", "end": "2026-02-16"}, "value": 12.0},
        ],
    )
    _set_metric_frame_time_scope(
        left, {"field": "time", "start": "2026-02-14", "end": "2026-02-16"}
    )
    right = _time_series_observation_v2(
        "m1",
        points=[
            {"window": {"start": "2025-02-14", "end": "2025-02-15"}, "value": 9.0},
            {"window": {"start": "2025-02-15", "end": "2025-02-16"}, "value": 11.0},
        ],
    )
    _set_metric_frame_time_scope(
        right, {"field": "time", "start": "2025-02-14", "end": "2025-02-16"}
    )
    runtime = _make_runtime(left, right)

    result = _run_compare(runtime)

    assert result["analytical_metadata"]["pairing_basis"] == "input_artifact_window_position"
    assert result["analytical_metadata"]["pairing_rule"] == "relative_bucket_position"
    assert result["analytical_metadata"]["compare_type"] == "normal"
    assert result["summary_current_value"] == 22.0
    assert result["summary_baseline_value"] == 20.0
    assert result["summary_absolute_delta"] == 2.0
    assert result["analytical_metadata"]["matched_current_time_scope"] == {
        "field": "time",
        "start": "2026-02-14",
        "end": "2026-02-16",
    }
    assert result["analytical_metadata"]["matched_baseline_time_scope"] == {
        "field": "time",
        "start": "2025-02-14",
        "end": "2025-02-16",
    }
    points = result["series"][0]["points"]
    assert points[0]["current_value"] == 10.0
    assert points[0]["baseline_value"] == 9.0


def test_compare_type_weekday_aligned_uses_nearest_weekday() -> None:
    left = _time_series_observation_v2(
        "m1",
        points=[{"window": {"start": "2026-04-02", "end": "2026-04-03"}, "value": 120.0}],
    )
    _set_metric_frame_time_scope(
        left, {"field": "time", "start": "2026-04-02", "end": "2026-04-04"}
    )
    right = _time_series_observation_v2(
        "m1",
        points=[{"window": {"start": "2025-04-03", "end": "2025-04-04"}, "value": 100.0}],
    )
    _set_metric_frame_time_scope(
        right, {"field": "time", "start": "2025-04-01", "end": "2025-04-05"}
    )
    runtime = _make_runtime(left, right)

    result = _run_compare(runtime, _compare_params("weekday_aligned"))

    assert result["analytical_metadata"]["pairing_rule"] == "same_weekday"
    points = result["series"][0]["points"]
    assert points[0]["baseline_value"] == 100.0
    assert (
        result["resolved_input_summary"]["calendar_alignment"]["bucket_pairing"][0][
            "pairing_reason"
        ]
        == "same_weekday_nearest"
    )


def test_compare_type_weekday_aligned_falls_back_to_relative_position() -> None:
    left = _time_series_observation_v2(
        "m1",
        points=[{"window": {"start": "2026-04-08", "end": "2026-04-09"}, "value": 120.0}],
    )
    _set_metric_frame_time_scope(
        left, {"field": "time", "start": "2026-04-08", "end": "2026-04-09"}
    )
    right = _time_series_observation_v2(
        "m1",
        points=[{"window": {"start": "2026-04-07", "end": "2026-04-08"}, "value": 100.0}],
    )
    _set_metric_frame_time_scope(
        right, {"field": "time", "start": "2026-04-07", "end": "2026-04-08"}
    )
    runtime = _make_runtime(left, right)

    result = _run_compare(runtime, _compare_params("weekday_aligned"))

    points = result["series"][0]["points"]
    assert points[0]["baseline_value"] == 100.0
    assert (
        result["resolved_input_summary"]["calendar_alignment"]["bucket_pairing"][0][
            "pairing_reason"
        ]
        == "natural_date_shift"
    )


def test_compare_type_holiday_aligned_reads_calendar_data() -> None:
    left = _time_series_observation_v2(
        "m1",
        points=[{"window": {"start": "2026-02-20", "end": "2026-02-21"}, "value": 120.0}],
    )
    _set_metric_frame_time_scope(
        left, {"field": "time", "start": "2026-02-20", "end": "2026-02-21"}
    )
    right = _time_series_observation_v2(
        "m1",
        points=[{"window": {"start": "2025-02-20", "end": "2025-02-21"}, "value": 100.0}],
    )
    _set_metric_frame_time_scope(
        right, {"field": "time", "start": "2025-02-20", "end": "2025-02-21"}
    )
    runtime = _make_runtime(left, right)
    runtime.calendar_data_reader = _FakeCalendarDataReader()

    result = _run_compare(runtime, _compare_params("holiday_aligned"))

    points = result["series"][0]["points"]
    assert points[0]["baseline_value"] == 100.0
    assert (
        result["resolved_input_summary"]["calendar_alignment"]["bucket_pairing"][0][
            "pairing_reason"
        ]
        == "holiday_cluster"
    )


def test_compare_type_holiday_and_weekday_aligned_falls_back_to_weekday() -> None:
    left = _time_series_observation_v2(
        "m1",
        points=[{"window": {"start": "2026-04-02", "end": "2026-04-03"}, "value": 120.0}],
    )
    _set_metric_frame_time_scope(
        left, {"field": "time", "start": "2026-04-02", "end": "2026-04-03"}
    )
    right = _time_series_observation_v2(
        "m1",
        points=[{"window": {"start": "2025-04-03", "end": "2025-04-04"}, "value": 100.0}],
    )
    _set_metric_frame_time_scope(
        right, {"field": "time", "start": "2025-04-01", "end": "2025-04-05"}
    )
    runtime = _make_runtime(left, right)
    runtime.calendar_data_reader = _FakeCalendarDataReader()

    result = _run_compare(runtime, _compare_params("holiday_and_weekday_aligned"))

    points = result["series"][0]["points"]
    assert points[0]["baseline_value"] == 100.0
    assert (
        result["resolved_input_summary"]["calendar_alignment"]["bucket_pairing"][0][
            "pairing_reason"
        ]
        == "same_weekday_nearest"
    )


def test_compare_type_holiday_aligned_requires_calendar_reader() -> None:
    runtime = _make_runtime(_time_series_observation_v2("m1"), _time_series_observation_v2("m1"))
    runtime.calendar_data_reader = None

    with pytest.raises(ValueError, match="requires configured calendar data"):
        _run_compare(runtime, _compare_params("holiday_aligned"))


def test_compare_time_series_missing_granularity_fails() -> None:
    left = _time_series_observation_v2("m1")
    right = _time_series_observation_v2("m1")
    # Set grain to None in the time axis to simulate missing granularity
    left["axes"] = [{"kind": "time", "grain": None}]
    runtime = _make_runtime(left, right)

    with pytest.raises(
        ValueError,
        match="time_series metric_frame requires one time axis with grain",
    ):
        _run_compare(runtime)
    runtime.commit_artifact_with_extraction.assert_not_called()


def test_compare_time_series_empty_series_fails_before_commit() -> None:
    runtime = _make_runtime(
        _time_series_observation_v2("m1", points=[]),
        _time_series_observation_v2("m1", points=[]),
    )

    with pytest.raises(ValueError, match="compare: NOT_COMPARABLE - no time-series buckets"):
        _run_compare(runtime)
    runtime.commit_artifact_with_extraction.assert_not_called()
