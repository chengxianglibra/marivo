from __future__ import annotations

from datetime import UTC, datetime

import pytest

from marivo.contracts.generated import aoi
from marivo.runtime.aoi_lowering import (
    lower_aoi_derived_request,
    lower_aoi_request,
)


def _time_scope() -> aoi.TimeScope:
    return aoi.TimeScope(
        field="event_time",
        start=datetime(2026, 5, 1, tzinfo=UTC),
        end=datetime(2026, 5, 8, tzinfo=UTC),
    )


def test_lowers_scalar_observe_request_to_runner_params() -> None:
    request = aoi.Observe(
        metric="view_time",
        time_scope=_time_scope(),
    )

    assert lower_aoi_request("observe", request) == {
        "metric": "view_time",
        "time_scope": {
            "field": "event_time",
            "start": "2026-05-01T00:00:00Z",
            "end": "2026-05-08T00:00:00Z",
        },
        "filter": None,
    }


@pytest.mark.parametrize("granularity", ["hour", "day", "week", "month", "quarter", "year"])
def test_lowers_time_series_observe_request_to_runner_params(granularity: str) -> None:
    request = aoi.Observe(
        metric="view_time",
        time_scope=_time_scope(),
        granularity=granularity,
    )

    assert lower_aoi_request("observe", request) == {
        "metric": "view_time",
        "time_scope": {
            "field": "event_time",
            "start": "2026-05-01T00:00:00Z",
            "end": "2026-05-08T00:00:00Z",
        },
        "filter": None,
        "granularity": granularity,
    }


def test_lowers_segmented_observe_request_with_filter_to_runner_params() -> None:
    request = aoi.Observe(
        metric="view_time",
        time_scope=_time_scope(),
        filter=aoi.Expression(
            dialects=[aoi.Dialect(dialect="ANSI_SQL", expression="region = 'US'")]
        ),
        dimensions=["region", "platform"],
    )

    assert lower_aoi_request("observe", request) == {
        "metric": "view_time",
        "time_scope": {
            "field": "event_time",
            "start": "2026-05-01T00:00:00Z",
            "end": "2026-05-08T00:00:00Z",
        },
        "filter": {"dialects": [{"dialect": "ANSI_SQL", "expression": "region = 'US'"}]},
        "dimensions": ["region", "platform"],
    }


def test_lowers_compare_request_to_runner_params() -> None:
    request = aoi.Compare(
        current_artifact_id="artifact-current",
        baseline_artifact_id="artifact-baseline",
        compare_type="holiday_aligned",
    )

    assert lower_aoi_request("compare", request) == {
        "current_artifact_id": "artifact-current",
        "baseline_artifact_id": "artifact-baseline",
        "compare_type": "holiday_aligned",
    }


def test_lowers_compare_default_compare_type_to_runner_params() -> None:
    request = aoi.Compare(
        current_artifact_id="artifact-current",
        baseline_artifact_id="artifact-baseline",
    )

    assert lower_aoi_request("compare", request) == {
        "current_artifact_id": "artifact-current",
        "baseline_artifact_id": "artifact-baseline",
        "compare_type": "normal",
    }


def test_lowers_decompose_request_with_all_options_to_runner_params() -> None:
    request = aoi.Decompose(
        compare_artifact_id="artifact-compare",
        dimension="region",
        limit=5,
    )

    assert lower_aoi_request("decompose", request) == {
        "compare_artifact_id": "artifact-compare",
        "dimension": "region",
        "limit": 5,
    }


def test_lowers_correlate_request_with_all_options_to_runner_params() -> None:
    request = aoi.Correlate(
        left_artifact_id="artifact-left",
        right_artifact_id="artifact-right",
        method="pearson",
        min_pairs=7,
    )

    assert lower_aoi_request("correlate", request) == {
        "left_artifact_id": "artifact-left",
        "right_artifact_id": "artifact-right",
        "method": "pearson",
        "min_pairs": 7,
    }


def test_lowers_correlate_omitted_options_to_none() -> None:
    request = aoi.Correlate(
        left_artifact_id="artifact-left",
        right_artifact_id="artifact-right",
    )

    assert lower_aoi_request("correlate", request) == {
        "left_artifact_id": "artifact-left",
        "right_artifact_id": "artifact-right",
        "method": None,
        "min_pairs": None,
    }


def test_lowers_forecast_request_to_runner_params() -> None:
    request = aoi.Forecast(
        source_artifact_id="artifact-source",
        horizon=14,
    )

    assert lower_aoi_request("forecast", request) == {
        "source_artifact_id": "artifact-source",
        "horizon": 14,
    }


def test_lowers_detect_request_to_artifact_input_runner_params() -> None:
    request = aoi.Detect.model_validate(
        {
            "source_artifact_id": "artifact_source",
            "sensitivity": "balanced",
            "limit": 5,
        }
    )

    assert lower_aoi_request("detect", request) == {
        "source_artifact_id": "artifact_source",
        "sensitivity": "balanced",
        "limit": 5,
    }


def test_lowers_sample_summary_transform_to_runner_params() -> None:
    from marivo.runtime.aoi_lowering import lower_aoi_transform_request

    request = aoi.SampleSummary(
        source_artifact_id="art_metric_frame_current",
        sample_kind="numeric",
    )

    assert lower_aoi_transform_request("sample_summary", request) == {
        "source_artifact_id": "art_metric_frame_current",
        "sample_kind": "numeric",
    }


def test_lowers_test_request_to_sample_frame_ref_runner_params() -> None:
    request = aoi.Test(
        current_sample_artifact_id="art_sample_current",
        baseline_sample_artifact_id="art_sample_baseline",
        hypothesis=aoi.Hypothesis(
            family="two_sample_mean",
            alternative="greater",
            significance="balanced",
        ),
    )

    assert lower_aoi_request("test", request) == {
        "current_sample_artifact_id": "art_sample_current",
        "baseline_sample_artifact_id": "art_sample_baseline",
        "hypothesis": {
            "family": "two_sample_mean",
            "alternative": "greater",
            "significance": "balanced",
        },
    }


def test_lowers_validate_request_to_runner_params() -> None:
    request = aoi.Validate(
        metric="view_time",
        current=aoi.Slice(
            time_scope=aoi.TimeScope(
                field="event_time",
                start=datetime(2026, 5, 1, tzinfo=UTC),
                end=datetime(2026, 5, 8, tzinfo=UTC),
            ),
            filter=aoi.Expression(
                dialects=[aoi.Dialect(dialect="ANSI_SQL", expression="region = 'US'")]
            ),
        ),
        baseline=aoi.Slice(
            time_scope=aoi.TimeScope(
                field="event_time",
                start=datetime(2026, 4, 24, tzinfo=UTC),
                end=datetime(2026, 5, 1, tzinfo=UTC),
            ),
        ),
        granularity="day",
        hypothesis=aoi.Hypothesis(
            family="two_sample_mean",
            alternative="greater",
            significance="balanced",
        ),
    )

    assert lower_aoi_derived_request("validate", request) == {
        "metric": "view_time",
        "current": {
            "time_scope": {
                "field": "event_time",
                "start": "2026-05-01T00:00:00Z",
                "end": "2026-05-08T00:00:00Z",
            },
            "filter": {"dialects": [{"dialect": "ANSI_SQL", "expression": "region = 'US'"}]},
        },
        "baseline": {
            "time_scope": {
                "field": "event_time",
                "start": "2026-04-24T00:00:00Z",
                "end": "2026-05-01T00:00:00Z",
            }
        },
        "granularity": "day",
        "hypothesis": {
            "family": "two_sample_mean",
            "alternative": "greater",
            "significance": "balanced",
        },
    }


def test_lowers_attribute_request_to_runner_params() -> None:
    request = aoi.Attribute(
        metric="view_time",
        current=aoi.Slice(
            time_scope=aoi.TimeScope(
                field="event_time",
                start=datetime(2026, 5, 1, tzinfo=UTC),
                end=datetime(2026, 5, 8, tzinfo=UTC),
            ),
            filter=aoi.Expression(
                dialects=[aoi.Dialect(dialect="ANSI_SQL", expression="region = 'US'")]
            ),
        ),
        baseline=aoi.Slice(
            time_scope=aoi.TimeScope(
                field="event_time",
                start=datetime(2026, 4, 24, tzinfo=UTC),
                end=datetime(2026, 5, 1, tzinfo=UTC),
            ),
        ),
        dimensions=["region"],
        decomposition_limit=10,
    )

    assert lower_aoi_derived_request("attribute", request) == {
        "metric": "view_time",
        "current": {
            "time_scope": {
                "field": "event_time",
                "start": "2026-05-01T00:00:00Z",
                "end": "2026-05-08T00:00:00Z",
            },
            "filter": {"dialects": [{"dialect": "ANSI_SQL", "expression": "region = 'US'"}]},
        },
        "baseline": {
            "time_scope": {
                "field": "event_time",
                "start": "2026-04-24T00:00:00Z",
                "end": "2026-05-01T00:00:00Z",
            }
        },
        "dimensions": ["region"],
        "decomposition_method": "delta_share",
        "decomposition_limit": 10,
    }


def test_lowers_diagnose_auto_detect_request_to_runner_params() -> None:
    request = aoi.Diagnose(
        metric="view_time",
        time_scope=aoi.TimeScope(
            field="event_time",
            start=datetime(2026, 5, 1, tzinfo=UTC),
            end=datetime(2026, 5, 8, tzinfo=UTC),
        ),
        granularity="day",
        filter=aoi.Expression(
            dialects=[aoi.Dialect(dialect="ANSI_SQL", expression="region = 'US'")]
        ),
        scan_dimension="region",
        dimensions=["region"],
        strategy="point_anomaly",
        sensitivity="balanced",
        candidate_limit=2,
        decomposition_limit=7,
    )

    assert lower_aoi_derived_request("diagnose", request) == {
        "metric": "view_time",
        "time_scope": {
            "field": "event_time",
            "start": "2026-05-01T00:00:00Z",
            "end": "2026-05-08T00:00:00Z",
        },
        "granularity": "day",
        "filter": {"dialects": [{"dialect": "ANSI_SQL", "expression": "region = 'US'"}]},
        "scan_dimension": "region",
        "dimensions": ["region"],
        "strategy": "point_anomaly",
        "sensitivity": "balanced",
        "candidate_limit": 2,
        "decomposition_limit": 7,
    }
