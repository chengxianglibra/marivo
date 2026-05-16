"""Tests for MCP-friendly DTO conversion into generated AOI request models."""

from __future__ import annotations

from marivo.contracts.generated import aoi
from marivo.transports.mcp.tools.intents import (
    to_aoi_compare_request,
    to_aoi_decompose_request,
    to_aoi_detect_request,
    to_aoi_forecast_request,
    to_aoi_observe_request,
    to_aoi_test_request,
)
from marivo.transports.mcp.tools.schemas import McpSliceRef, McpTestHypothesis, McpTimeScope


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


def _slice(start: str, end: str) -> McpSliceRef:
    return McpSliceRef(
        time_scope=McpTimeScope(
            field="log_time",
            start=start,
            end=end,
        )
    )


def test_to_aoi_test_request_builds_test_model() -> None:
    request = to_aoi_test_request(
        metric="view_time",
        left=_slice("2026-05-01T00:00:00Z", "2026-05-08T00:00:00Z"),
        right=_slice("2026-04-24T00:00:00Z", "2026-05-01T00:00:00Z"),
        hypothesis=McpTestHypothesis(
            alternative="greater",
            significance="balanced",
        ),
    )

    assert isinstance(request, aoi.Test)
    assert request.kind == "numeric"
    assert request.hypothesis.family == "two_sample_mean"
    assert request.hypothesis.alternative == "greater"


def test_to_aoi_test_request_rejects_hypothesis_label() -> None:
    try:
        to_aoi_test_request(
            metric="view_time",
            left=_slice("2026-05-01T00:00:00Z", "2026-05-08T00:00:00Z"),
            right=_slice("2026-04-24T00:00:00Z", "2026-05-01T00:00:00Z"),
            hypothesis={
                "alternative": "greater",
                "significance": "balanced",
                "label": "legacy label",
            },
        )
    except ValueError as error:
        assert "label" in str(error)
    else:
        raise AssertionError("expected label to be rejected")


def test_to_aoi_test_request_rejects_alpha() -> None:
    try:
        to_aoi_test_request(
            metric="view_time",
            left=_slice("2026-05-01T00:00:00Z", "2026-05-08T00:00:00Z"),
            right=_slice("2026-04-24T00:00:00Z", "2026-05-01T00:00:00Z"),
            hypothesis={
                "alternative": "greater",
                "alpha": 0.05,
            },
        )
    except ValueError as error:
        assert "alpha" in str(error)
    else:
        raise AssertionError("expected alpha to be rejected")


def test_to_aoi_test_request_rejects_fixed_family_in_mcp_dto() -> None:
    try:
        to_aoi_test_request(
            metric="view_time",
            left=_slice("2026-05-01T00:00:00Z", "2026-05-08T00:00:00Z"),
            right=_slice("2026-04-24T00:00:00Z", "2026-05-01T00:00:00Z"),
            hypothesis={
                "family": "two_sample_mean",
                "alternative": "greater",
                "significance": "balanced",
            },
        )
    except ValueError as error:
        assert "family" in str(error)
    else:
        raise AssertionError("expected fixed AOI family to be rejected in MCP DTO")
