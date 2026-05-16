from __future__ import annotations

from datetime import UTC, datetime

from marivo.contracts.generated import aoi
from marivo.runtime.aoi_lowering import lower_aoi_derived_request, lower_aoi_request


def test_lowers_observe_request_to_runner_params() -> None:
    request = aoi.Observe2(
        metric="view_time",
        time_scope=aoi.TimeScope(
            field="event_time",
            start=datetime(2026, 5, 1, tzinfo=UTC),
            end=datetime(2026, 5, 8, tzinfo=UTC),
        ),
        granularity="day",
    )

    assert lower_aoi_request("observe", request) == {
        "metric": "view_time",
        "time_scope": {
            "field": "event_time",
            "start": "2026-05-01T00:00:00Z",
            "end": "2026-05-08T00:00:00Z",
        },
        "filter": None,
        "granularity": "day",
    }


def test_lowers_compare_request_to_runner_params() -> None:
    request = aoi.Compare(
        left_artifact_id="artifact-left",
        right_artifact_id="artifact-right",
        compare_type="yoy",
    )

    assert lower_aoi_request("compare", request) == {
        "left_artifact_id": "artifact-left",
        "right_artifact_id": "artifact-right",
        "compare_type": "yoy",
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


def test_lowers_validate_request_to_runner_params() -> None:
    request = aoi.Validate(
        metric="view_time",
        left=aoi.Slice(
            time_scope=aoi.TimeScope(
                field="event_time",
                start=datetime(2026, 5, 1, tzinfo=UTC),
                end=datetime(2026, 5, 8, tzinfo=UTC),
            ),
            filter=aoi.Expression(
                dialects=[aoi.Dialect(dialect="ANSI_SQL", expression="region = 'US'")]
            ),
        ),
        right=aoi.Slice(
            time_scope=aoi.TimeScope(
                field="event_time",
                start=datetime(2026, 4, 24, tzinfo=UTC),
                end=datetime(2026, 5, 1, tzinfo=UTC),
            ),
        ),
        hypothesis=aoi.Hypothesis(
            family="two_sample_mean",
            alternative="greater",
            significance="balanced",
        ),
    )

    assert lower_aoi_derived_request("validate", request) == {
        "metric": "view_time",
        "left": {
            "time_scope": {
                "field": "event_time",
                "start": "2026-05-01T00:00:00Z",
                "end": "2026-05-08T00:00:00Z",
            },
            "filter": {"dialects": [{"dialect": "ANSI_SQL", "expression": "region = 'US'"}]},
        },
        "right": {
            "time_scope": {
                "field": "event_time",
                "start": "2026-04-24T00:00:00Z",
                "end": "2026-05-01T00:00:00Z",
            }
        },
        "hypothesis": {
            "family": "two_sample_mean",
            "alternative": "greater",
            "significance": "balanced",
        },
    }
