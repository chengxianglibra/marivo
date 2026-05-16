"""Tests for MCP-friendly DTO conversion into generated AOI request models."""

from __future__ import annotations

from marivo.contracts.generated import aoi
from marivo.transports.mcp.tools.intents import (
    to_aoi_compare_request,
    to_aoi_decompose_request,
    to_aoi_detect_request,
    to_aoi_forecast_request,
    to_aoi_observe_request,
)
from marivo.transports.mcp.tools.schemas import McpTimeScope


def test_to_aoi_observe_request_builds_observe_model() -> None:
    request = to_aoi_observe_request(
        metric="view_time",
        time_scope=McpTimeScope(
            field="log_time",
            start="2026-05-01T00:00:00Z",
            end="2026-05-08T00:00:00Z",
        ),
        granularity="day",
        dimensions=["region", "platform"],
        filter_expression={"dialects": [{"dialect": "ANSI_SQL", "expression": "region = 'US'"}]},
    )

    assert isinstance(request, aoi.Observe1)
    assert request.metric == "view_time"
    assert request.time_scope.field == "log_time"
    assert request.granularity == "day"
    assert request.dimensions is not None
    assert request.dimensions.model_dump() == ["region", "platform"]
    assert request.filter is not None
    assert request.filter.model_dump(exclude_none=True) == {
        "dialects": [{"dialect": "ANSI_SQL", "expression": "region = 'US'"}]
    }


def test_to_aoi_compare_request_builds_compare_model() -> None:
    request = to_aoi_compare_request(
        left_artifact_id="artifact_obs_left",
        right_artifact_id="artifact_obs_right",
        compare_type="yoy",
    )

    assert isinstance(request, aoi.Compare)
    assert request.left_artifact_id == "artifact_obs_left"
    assert request.right_artifact_id == "artifact_obs_right"
    assert request.compare_type == "yoy"


def test_to_aoi_decompose_request_builds_decompose_model() -> None:
    request = to_aoi_decompose_request(
        compare_artifact_id="artifact_compare_1",
        dimension="region",
        limit=10,
    )

    assert isinstance(request, aoi.Decompose)
    assert request.compare_artifact_id == "artifact_compare_1"
    assert request.dimension == "region"
    assert request.limit == 10


def test_to_aoi_detect_request_builds_detect_model() -> None:
    request = to_aoi_detect_request(
        metric="view_time",
        time_scope=McpTimeScope(
            field="log_time",
            start="2026-05-01T00:00:00Z",
            end="2026-05-08T00:00:00Z",
        ),
        granularity="day",
        filter_expression={"dialects": [{"dialect": "ANSI_SQL", "expression": "region = 'US'"}]},
        dimension="region",
        strategy="period_shift",
        sensitivity="balanced",
        limit=5,
    )

    assert isinstance(request, aoi.Detect)
    assert request.metric == "view_time"
    assert request.time_scope.field == "log_time"
    assert request.granularity == "day"
    assert request.filter is not None
    assert request.dimension is not None
    assert request.dimension.root == "region"
    assert request.strategy == "period_shift"
    assert request.sensitivity == "balanced"
    assert request.limit is not None
    assert request.limit.root == 5


def test_to_aoi_forecast_request_builds_forecast_model() -> None:
    request = to_aoi_forecast_request(
        source_artifact_id="artifact_obs_1",
        horizon=7,
        profile="auto",
    )

    assert isinstance(request, aoi.Forecast)
    assert request.source_artifact_id == "artifact_obs_1"
    assert request.horizon == 7
    assert request.profile == "auto"
