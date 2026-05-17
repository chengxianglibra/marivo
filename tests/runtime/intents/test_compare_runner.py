from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from tests.runtime.intents._runner_fixtures import (
    _FAKE_ARTIFACT_ID,
    _SESSION,
    _FakeCalendarDataReader,
    _scalar_observation,
    _time_series_observation,
)

_LEFT_ARTIFACT_ID = "art_left_obs"
_RIGHT_ARTIFACT_ID = "art_right_obs"
_LEFT_STEP_ID = "step_left"
_RIGHT_STEP_ID = "step_right"


def _segmented_observation(metric: str = "m1") -> dict[str, Any]:
    return {
        "observation_type": "segmented",
        "metric": metric,
        "schema_version": "1.0",
        "unit": None,
        "dimensions": ["country"],
        "segments": [
            {"keys": {"country": "US"}, "value": 100.0},
            {"keys": {"country": "CA"}, "value": 50.0},
        ],
        "scope_value": 150.0,
        "analytical_metadata": {
            "aggregation_semantics": "sum",
            "additive_dimensions": ["country", "device", "date"],
            "row_count": 2,
        },
        "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
        "scope": {},
    }


def _make_runtime(
    left_artifact: dict[str, Any] | None = None,
    right_artifact: dict[str, Any] | None = None,
) -> MagicMock:
    runtime = MagicMock()
    runtime.core = MagicMock()
    runtime.commit_artifact_with_extraction.return_value = _FAKE_ARTIFACT_ID
    runtime.insert_step.return_value = None
    runtime.resolve_artifact_with_step_by_id.side_effect = [
        (_LEFT_STEP_ID, left_artifact or _scalar_observation("m1")),
        (_RIGHT_STEP_ID, right_artifact or _scalar_observation("m1")),
    ]
    return runtime


def _compare_params(compare_type: str | None = None) -> dict[str, Any]:
    params = {
        "left_artifact_id": _LEFT_ARTIFACT_ID,
        "right_artifact_id": _RIGHT_ARTIFACT_ID,
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

    assert result["left_ref"]["step_id"] == _LEFT_STEP_ID
    assert result["left_ref"]["artifact_id"] == _LEFT_ARTIFACT_ID
    assert result["right_ref"]["step_id"] == _RIGHT_STEP_ID
    assert result["right_ref"]["artifact_id"] == _RIGHT_ARTIFACT_ID
    assert result["lineage"]["left_source_ref"]["artifact_id"] == _LEFT_ARTIFACT_ID
    assert result["lineage"]["right_source_ref"]["artifact_id"] == _RIGHT_ARTIFACT_ID
    assert result["resolved_input_summary"]["left_time_scope"] == {
        "kind": "range",
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
        {"left_artifact_id": _LEFT_ARTIFACT_ID},
        {"right_artifact_id": _RIGHT_ARTIFACT_ID},
        {"left_artifact_id": " ", "right_artifact_id": _RIGHT_ARTIFACT_ID},
        {"left_artifact_id": _LEFT_ARTIFACT_ID, "right_artifact_id": " "},
    ],
)
def test_compare_requires_both_artifact_ids(payload: dict[str, Any]) -> None:
    runtime = _make_runtime()

    with pytest.raises(ValueError, match="both left_artifact_id and right_artifact_id"):
        _run_compare(runtime, payload)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "left_ref": {"step_id": _LEFT_STEP_ID, "session_id": _SESSION},
            "right_ref": {"step_id": _RIGHT_STEP_ID, "session_id": _SESSION},
        },
        {**_compare_params(), "mode": "scalar"},
    ],
)
def test_compare_rejects_legacy_or_unknown_request_fields(payload: dict[str, Any]) -> None:
    runtime = _make_runtime()

    with pytest.raises(ValueError, match="unsupported parameter"):
        _run_compare(runtime, payload)


def test_compare_rejects_unknown_compare_type() -> None:
    runtime = _make_runtime()

    with pytest.raises(ValueError, match="Unknown compare_type 'not_real'"):
        _run_compare(runtime, _compare_params("not_real"))


@pytest.mark.parametrize(
    "compare_type",
    [
        "yoy",
        "mom",
        "wow",
        "holiday_aligned_yoy",
        "weekday_aligned_yoy",
        "weekday_aligned_mom",
    ],
)
def test_compare_rejects_legacy_compare_types(compare_type: str) -> None:
    runtime = _make_runtime()

    with pytest.raises(ValueError, match=f"Unknown compare_type '{compare_type}'"):
        _run_compare(runtime, _compare_params(compare_type))


def test_compare_reports_missing_left_artifact_id() -> None:
    runtime = MagicMock()
    runtime.resolve_artifact_with_step_by_id.return_value = None

    with pytest.raises(ValueError, match="left_artifact_id 'art_left_obs'"):
        _run_compare(runtime)


def test_compare_reports_missing_right_artifact_id() -> None:
    runtime = MagicMock()
    runtime.resolve_artifact_with_step_by_id.side_effect = [
        (_LEFT_STEP_ID, _scalar_observation("m1")),
        None,
    ]

    with pytest.raises(ValueError, match="right_artifact_id 'art_right_obs'"):
        _run_compare(runtime)


@pytest.mark.parametrize(
    "left_artifact",
    [_scalar_observation("m1"), _segmented_observation("m1")],
)
def test_compare_type_non_normal_rejects_non_time_series_observations(
    left_artifact: dict[str, Any],
) -> None:
    runtime = _make_runtime(left_artifact, left_artifact)

    with pytest.raises(ValueError, match="compare_type 'weekday_aligned' requires time_series"):
        _run_compare(runtime, _compare_params("weekday_aligned"))


def test_compare_time_series_commits_time_series_delta() -> None:
    runtime = _make_runtime(
        _time_series_observation("m1"),
        _time_series_observation(
            "m1",
            series=[
                {"window": {"start": "2024-01-01", "end": "2024-01-02"}, "value": 8.0},
                {"window": {"start": "2024-01-02", "end": "2024-01-03"}, "value": 15.0},
            ],
        ),
    )

    result = _run_compare(runtime)

    assert result["comparison_type"] == "time_series_delta"
    assert result["granularity"] == "day"
    assert len(result["rows"]) == 2
    assert result["summary_left_value"] == 30.0
    assert result["summary_right_value"] == 23.0
    assert result["analytical_metadata"]["pairing_basis"] == "input_artifact_window_position"
    assert result["analytical_metadata"]["pairing_rule"] == "relative_bucket_position"


def test_compare_segmented_commits_segmented_delta() -> None:
    right = _segmented_observation("m1")
    right["segments"] = [
        {"keys": {"country": "US"}, "value": 80.0},
        {"keys": {"country": "MX"}, "value": 30.0},
    ]
    right["scope_value"] = 110.0
    runtime = _make_runtime(_segmented_observation("m1"), right)

    result = _run_compare(runtime)

    assert result["comparison_type"] == "segmented_delta"
    assert result["scope_absolute_delta"] == 40.0
    assert {row["presence"] for row in result["rows"]} == {"both", "left_only", "right_only"}


def test_compare_metric_mismatch_is_not_comparable() -> None:
    runtime = _make_runtime(_scalar_observation("m1"), _scalar_observation("m2"))

    with pytest.raises(ValueError, match="compare: NOT_COMPARABLE"):
        _run_compare(runtime)


def test_compare_type_normal_aligns_non_overlapping_windows_by_relative_position() -> None:
    left = _time_series_observation(
        "m1",
        series=[
            {"window": {"start": "2026-02-14", "end": "2026-02-15"}, "value": 10.0},
            {"window": {"start": "2026-02-15", "end": "2026-02-16"}, "value": 12.0},
        ],
    )
    left["time_scope"] = {"kind": "range", "start": "2026-02-14", "end": "2026-02-16"}
    right = _time_series_observation(
        "m1",
        series=[
            {"window": {"start": "2025-02-14", "end": "2025-02-15"}, "value": 9.0},
            {"window": {"start": "2025-02-15", "end": "2025-02-16"}, "value": 11.0},
        ],
    )
    right["time_scope"] = {"kind": "range", "start": "2025-02-14", "end": "2025-02-16"}
    runtime = _make_runtime(left, right)

    result = _run_compare(runtime)

    assert result["analytical_metadata"]["pairing_basis"] == "input_artifact_window_position"
    assert result["analytical_metadata"]["pairing_rule"] == "relative_bucket_position"
    assert result["analytical_metadata"]["compare_type"] == "normal"
    assert result["summary_left_value"] == 22.0
    assert result["summary_right_value"] == 20.0
    assert result["summary_absolute_delta"] == 2.0
    assert result["analytical_metadata"]["matched_left_time_scope"] == {
        "kind": "range",
        "start": "2026-02-14",
        "end": "2026-02-16",
    }
    assert result["analytical_metadata"]["matched_right_time_scope"] == {
        "kind": "range",
        "start": "2025-02-14",
        "end": "2025-02-16",
    }
    assert result["rows"][0]["left_value"] == 10.0
    assert result["rows"][0]["right_value"] == 9.0


def test_compare_type_weekday_aligned_uses_nearest_weekday() -> None:
    left = _time_series_observation(
        "m1",
        series=[{"window": {"start": "2026-04-02", "end": "2026-04-03"}, "value": 120.0}],
    )
    left["time_scope"] = {"kind": "range", "start": "2026-04-02", "end": "2026-04-04"}
    right = _time_series_observation(
        "m1",
        series=[{"window": {"start": "2025-04-03", "end": "2025-04-04"}, "value": 100.0}],
    )
    right["time_scope"] = {"kind": "range", "start": "2025-04-01", "end": "2025-04-05"}
    runtime = _make_runtime(left, right)

    result = _run_compare(runtime, _compare_params("weekday_aligned"))

    assert result["analytical_metadata"]["pairing_rule"] == "same_weekday"
    assert result["rows"][0]["right_value"] == 100.0
    assert (
        result["resolved_input_summary"]["calendar_alignment"]["bucket_pairing"][0][
            "pairing_reason"
        ]
        == "same_weekday_nearest"
    )


def test_compare_type_weekday_aligned_falls_back_to_relative_position() -> None:
    left = _time_series_observation(
        "m1",
        series=[{"window": {"start": "2026-04-08", "end": "2026-04-09"}, "value": 120.0}],
    )
    left["time_scope"] = {"kind": "range", "start": "2026-04-08", "end": "2026-04-09"}
    right = _time_series_observation(
        "m1",
        series=[{"window": {"start": "2026-04-07", "end": "2026-04-08"}, "value": 100.0}],
    )
    right["time_scope"] = {"kind": "range", "start": "2026-04-07", "end": "2026-04-08"}
    runtime = _make_runtime(left, right)

    result = _run_compare(runtime, _compare_params("weekday_aligned"))

    assert result["rows"][0]["right_value"] == 100.0
    assert (
        result["resolved_input_summary"]["calendar_alignment"]["bucket_pairing"][0][
            "pairing_reason"
        ]
        == "natural_date_shift"
    )


def test_compare_type_holiday_aligned_reads_calendar_data() -> None:
    left = _time_series_observation(
        "m1",
        series=[{"window": {"start": "2026-02-20", "end": "2026-02-21"}, "value": 120.0}],
    )
    left["time_scope"] = {"kind": "range", "start": "2026-02-20", "end": "2026-02-21"}
    right = _time_series_observation(
        "m1",
        series=[{"window": {"start": "2025-02-20", "end": "2025-02-21"}, "value": 100.0}],
    )
    right["time_scope"] = {"kind": "range", "start": "2025-02-20", "end": "2025-02-21"}
    runtime = _make_runtime(left, right)
    runtime.calendar_data_reader = _FakeCalendarDataReader()

    result = _run_compare(runtime, _compare_params("holiday_aligned"))

    assert result["rows"][0]["right_value"] == 100.0
    assert (
        result["resolved_input_summary"]["calendar_alignment"]["bucket_pairing"][0][
            "pairing_reason"
        ]
        == "holiday_cluster"
    )


def test_compare_type_holiday_and_weekday_aligned_falls_back_to_weekday() -> None:
    left = _time_series_observation(
        "m1",
        series=[{"window": {"start": "2026-04-02", "end": "2026-04-03"}, "value": 120.0}],
    )
    left["time_scope"] = {"kind": "range", "start": "2026-04-02", "end": "2026-04-03"}
    right = _time_series_observation(
        "m1",
        series=[{"window": {"start": "2025-04-03", "end": "2025-04-04"}, "value": 100.0}],
    )
    right["time_scope"] = {"kind": "range", "start": "2025-04-01", "end": "2025-04-05"}
    runtime = _make_runtime(left, right)
    runtime.calendar_data_reader = _FakeCalendarDataReader()

    result = _run_compare(runtime, _compare_params("holiday_and_weekday_aligned"))

    assert result["rows"][0]["right_value"] == 100.0
    assert (
        result["resolved_input_summary"]["calendar_alignment"]["bucket_pairing"][0][
            "pairing_reason"
        ]
        == "same_weekday_nearest"
    )


def test_compare_type_holiday_aligned_requires_calendar_reader() -> None:
    runtime = _make_runtime(_time_series_observation("m1"), _time_series_observation("m1"))
    runtime.calendar_data_reader = None

    with pytest.raises(ValueError, match="requires configured calendar data"):
        _run_compare(runtime, _compare_params("holiday_aligned"))


def test_compare_time_series_missing_granularity_fails() -> None:
    left = _time_series_observation("m1")
    right = _time_series_observation("m1")
    left["granularity"] = None
    runtime = _make_runtime(left, right)

    with pytest.raises(ValueError, match="compare: NOT_COMPARABLE - time_series observations"):
        _run_compare(runtime)


def test_compare_time_series_empty_series_fails_before_commit() -> None:
    runtime = _make_runtime(
        _time_series_observation("m1", series=[]),
        _time_series_observation("m1", series=[]),
    )

    with pytest.raises(ValueError, match="compare: NOT_COMPARABLE - no time-series buckets"):
        _run_compare(runtime)
    runtime.commit_artifact_with_extraction.assert_not_called()
