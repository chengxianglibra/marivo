"""Tests for MCP-friendly DTO conversion into generated AOI request models."""

from __future__ import annotations

import pytest

from marivo.contracts.generated import aoi
from marivo.transports.mcp.tools.intents import (
    to_aoi_attribute_request,
    to_aoi_compare_request,
    to_aoi_decompose_request,
    to_aoi_detect_request,
    to_aoi_diagnose_request,
    to_aoi_forecast_request,
    to_aoi_observe_request,
    to_aoi_test_request,
    to_aoi_validate_request,
)
from marivo.transports.mcp.tools.schemas import (
    McpAoiSliceRef,
    McpExpression,
    McpTestHypothesis,
    McpTimeScope,
    McpValidateHypothesis,
)


def test_to_aoi_observe_request_builds_scalar_model() -> None:
    request = to_aoi_observe_request(
        metric="view_time",
        time_scope=McpTimeScope(
            field="log_time",
            start="2026-05-01T00:00:00Z",
            end="2026-05-08T00:00:00Z",
        ),
    )

    assert isinstance(request, aoi.Observe1)
    assert request.metric == "view_time"
    assert request.time_scope.field == "log_time"


def test_to_aoi_observe_request_builds_time_series_model() -> None:
    request = to_aoi_observe_request(
        metric="view_time",
        time_scope=McpTimeScope(
            field="log_time",
            start="2026-05-01T00:00:00Z",
            end="2026-05-08T00:00:00Z",
        ),
        granularity="year",
    )

    assert isinstance(request, aoi.Observe2)
    assert request.granularity == "year"


def test_to_aoi_observe_request_builds_segmented_model() -> None:
    request = to_aoi_observe_request(
        metric="view_time",
        time_scope=McpTimeScope(
            field="log_time",
            start="2026-05-01T00:00:00Z",
            end="2026-05-08T00:00:00Z",
        ),
        dimensions=["region", "platform"],
        filter_expression={"dialects": [{"dialect": "ANSI_SQL", "expression": "region = 'US'"}]},
    )

    assert isinstance(request, aoi.Observe3)
    assert request.metric == "view_time"
    assert request.time_scope.field == "log_time"
    assert request.dimensions is not None
    assert [dimension.root for dimension in request.dimensions] == ["region", "platform"]
    assert request.filter is not None
    assert request.filter.model_dump(exclude_none=True) == {
        "dialects": [{"dialect": "ANSI_SQL", "expression": "region = 'US'"}]
    }


def test_to_aoi_observe_request_rejects_mixed_mode_selectors() -> None:
    try:
        to_aoi_observe_request(
            metric="view_time",
            time_scope=McpTimeScope(
                field="log_time",
                start="2026-05-01T00:00:00Z",
                end="2026-05-08T00:00:00Z",
            ),
            granularity="day",
            dimensions=["region"],
        )
    except ValueError as error:
        assert "omit granularity" in str(error)
    else:
        raise AssertionError("expected mixed observe mode selectors to be rejected")


def test_to_aoi_compare_request_builds_compare_model() -> None:
    request = to_aoi_compare_request(
        left_artifact_id="artifact_obs_left",
        right_artifact_id="artifact_obs_right",
        compare_type="holiday_aligned_yoy",
    )

    assert isinstance(request, aoi.Compare)
    assert request.left_artifact_id == "artifact_obs_left"
    assert request.right_artifact_id == "artifact_obs_right"
    assert request.compare_type == "holiday_aligned_yoy"


def test_to_aoi_compare_request_defaults_compare_type() -> None:
    request = to_aoi_compare_request(
        left_artifact_id="artifact_obs_left",
        right_artifact_id="artifact_obs_right",
    )

    assert isinstance(request, aoi.Compare)
    assert request.compare_type == "normal"


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


def test_to_aoi_decompose_request_omits_absent_optional_fields() -> None:
    request = to_aoi_decompose_request(
        compare_artifact_id="artifact_compare_1",
        dimension="region",
    )

    dumped = request.model_dump(exclude_none=True)
    assert dumped == {
        "compare_artifact_id": "artifact_compare_1",
        "dimension": "region",
    }


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
    assert request.filter.model_dump(exclude_none=True) == {
        "dialects": [{"dialect": "ANSI_SQL", "expression": "region = 'US'"}]
    }
    assert request.dimension == "region"
    assert request.strategy == "period_shift"
    assert request.sensitivity == "balanced"
    assert request.limit == 5


def test_to_aoi_detect_request_omits_absent_optional_fields() -> None:
    request = to_aoi_detect_request(
        metric="view_time",
        time_scope=McpTimeScope(
            field="log_time",
            start="2026-05-01T00:00:00Z",
            end="2026-05-08T00:00:00Z",
        ),
        granularity="day",
        strategy="point_anomaly",
    )

    dumped = request.model_dump(exclude_none=True)
    assert "filter" not in dumped
    assert "dimension" not in dumped
    assert "limit" not in dumped


def test_to_aoi_forecast_request_builds_forecast_model() -> None:
    request = to_aoi_forecast_request(
        source_artifact_id="artifact_obs_1",
        horizon=7,
    )

    assert isinstance(request, aoi.Forecast)
    assert request.source_artifact_id == "artifact_obs_1"
    assert request.horizon == 7
    assert "profile" not in request.model_dump()


def _slice(start: str, end: str, filter_expression: McpExpression | None = None) -> McpAoiSliceRef:
    return McpAoiSliceRef(
        time_scope=McpTimeScope(
            field="log_time",
            start=start,
            end=end,
        ),
        filter=filter_expression,
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
    assert "filter" not in request.model_dump(exclude_none=True)["left"]
    assert request.hypothesis.alternative == "greater"
    assert request.hypothesis.significance == "balanced"


@pytest.mark.parametrize("alternative", ["two_sided", "greater", "less"])
@pytest.mark.parametrize("significance", ["conservative", "balanced", "aggressive"])
def test_to_aoi_test_request_passes_supported_hypothesis_options(
    alternative: str,
    significance: str,
) -> None:
    request = to_aoi_test_request(
        metric="view_time",
        left=_slice("2026-05-01T00:00:00Z", "2026-05-08T00:00:00Z"),
        right=_slice("2026-04-24T00:00:00Z", "2026-05-01T00:00:00Z"),
        hypothesis=McpTestHypothesis(
            alternative=alternative,
            significance=significance,
        ),
    )

    assert request.kind == "numeric"
    assert request.hypothesis.family == "two_sample_mean"
    assert request.hypothesis.alternative == alternative
    assert request.hypothesis.significance == significance


def test_to_aoi_validate_request_builds_validate_model_with_defaults() -> None:
    request = to_aoi_validate_request(
        metric="view_time",
        left=_slice("2026-05-01T00:00:00Z", "2026-05-08T00:00:00Z"),
        right=_slice("2026-04-24T00:00:00Z", "2026-05-01T00:00:00Z"),
    )

    assert isinstance(request, aoi.Validate)
    assert request.hypothesis.family == "two_sample_mean"
    assert request.hypothesis.alternative == "two_sided"
    assert request.hypothesis.significance == "balanced"
    assert "filter" not in request.model_dump(exclude_none=True)["left"]


def test_to_aoi_validate_request_preserves_aoi_slice_filter() -> None:
    request = to_aoi_validate_request(
        metric="view_time",
        left=_slice(
            "2026-05-01T00:00:00Z",
            "2026-05-08T00:00:00Z",
            McpExpression(dialects=[{"dialect": "ANSI_SQL", "expression": "region = 'US'"}]),
        ),
        right=_slice("2026-04-24T00:00:00Z", "2026-05-01T00:00:00Z"),
        hypothesis=McpValidateHypothesis(alternative="greater"),
    )

    assert isinstance(request, aoi.Validate)
    assert request.left.filter is not None
    assert request.left.filter.model_dump(exclude_none=True) == {
        "dialects": [{"dialect": "ANSI_SQL", "expression": "region = 'US'"}]
    }
    assert request.hypothesis.alternative == "greater"
    assert request.hypothesis.significance == "balanced"


def test_to_aoi_attribute_request_preserves_aoi_slice_filter() -> None:
    request = to_aoi_attribute_request(
        metric="view_time",
        left=_slice(
            "2026-05-01T00:00:00Z",
            "2026-05-08T00:00:00Z",
            McpExpression(dialects=[{"dialect": "ANSI_SQL", "expression": "region = 'US'"}]),
        ),
        right=_slice("2026-04-24T00:00:00Z", "2026-05-01T00:00:00Z"),
        dimensions=["region"],
        decomposition_limit=10,
    )

    assert isinstance(request, aoi.Attribute)
    assert request.left.filter is not None
    assert request.left.filter.model_dump(exclude_none=True) == {
        "dialects": [{"dialect": "ANSI_SQL", "expression": "region = 'US'"}]
    }
    assert [dimension.root for dimension in request.dimensions] == ["region"]
    assert request.decomposition_method == "delta_share"
    assert request.decomposition_limit == 10


def test_to_aoi_diagnose_request_builds_auto_detect_model() -> None:
    request = to_aoi_diagnose_request(
        metric="view_time",
        time_scope=McpTimeScope(
            field="log_time",
            start="2026-05-01T00:00:00Z",
            end="2026-05-08T00:00:00Z",
        ),
        granularity="day",
        filter_expression=McpExpression(
            dialects=[{"dialect": "ANSI_SQL", "expression": "region = 'US'"}]
        ),
        detect_dimension="region",
        candidate_dimensions=["region"],
        strategy="point_anomaly",
        candidate_limit=5,
    )

    assert isinstance(request, aoi.Diagnose)
    assert request.mode == "auto_detect"
    assert request.time_scope is not None
    assert request.time_scope.field == "log_time"
    assert request.filter is not None
    assert request.filter.model_dump(exclude_none=True) == {
        "dialects": [{"dialect": "ANSI_SQL", "expression": "region = 'US'"}]
    }
    assert [dimension.root for dimension in request.candidate_dimensions] == ["region"]
    assert request.candidate_limit == 5


def test_to_aoi_diagnose_request_builds_explicit_compare_model() -> None:
    request = to_aoi_diagnose_request(
        metric="view_time",
        mode="explicit_compare",
        current=_slice("2026-05-01T00:00:00Z", "2026-05-08T00:00:00Z"),
        baseline=_slice("2026-04-24T00:00:00Z", "2026-05-01T00:00:00Z"),
        candidate_dimensions=["region"],
        strategy="period_shift",
    )

    assert isinstance(request, aoi.Diagnose)
    assert request.mode == "explicit_compare"
    assert request.current is not None
    assert request.baseline is not None
    assert "time_scope" not in request.model_dump(exclude_none=True)


def test_to_aoi_test_request_preserves_aoi_slice_filter() -> None:
    request = to_aoi_test_request(
        metric="view_time",
        left=_slice(
            "2026-05-01T00:00:00Z",
            "2026-05-08T00:00:00Z",
            McpExpression(dialects=[{"dialect": "ANSI_SQL", "expression": "region = 'US'"}]),
        ),
        right=_slice("2026-04-24T00:00:00Z", "2026-05-01T00:00:00Z"),
        hypothesis=McpTestHypothesis(
            alternative="greater",
            significance="balanced",
        ),
    )

    assert request.left.filter is not None
    assert request.left.filter.model_dump(exclude_none=True) == {
        "dialects": [{"dialect": "ANSI_SQL", "expression": "region = 'US'"}]
    }


def test_aoi_slice_ref_rejects_derived_scope() -> None:
    try:
        McpAoiSliceRef.model_validate(
            {
                "time_scope": {
                    "field": "log_time",
                    "start": "2026-05-01T00:00:00Z",
                    "end": "2026-05-08T00:00:00Z",
                },
                "scope": {"constraints": {"region": "US"}},
            }
        )
    except ValueError as error:
        assert "scope" in str(error)
    else:
        raise AssertionError("expected AOI slice DTO to reject derived scope")


def test_validate_hypothesis_rejects_non_mcp_fields() -> None:
    for field in ("family", "alpha", "label"):
        try:
            McpValidateHypothesis.model_validate(
                {
                    "alternative": "greater",
                    "significance": "balanced",
                    field: "two_sample_mean" if field == "family" else "extra",
                }
            )
        except ValueError as error:
            assert field in str(error)
        else:
            raise AssertionError(f"expected validate hypothesis DTO to reject {field}")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("label", "legacy label"),
        ("alpha", 0.05),
        ("family", "two_sample_mean"),
    ],
)
def test_to_aoi_test_request_rejects_non_mcp_hypothesis_fields(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValueError, match=field):
        to_aoi_test_request(
            metric="view_time",
            left=_slice("2026-05-01T00:00:00Z", "2026-05-08T00:00:00Z"),
            right=_slice("2026-04-24T00:00:00Z", "2026-05-01T00:00:00Z"),
            hypothesis={
                "alternative": "greater",
                "significance": "balanced",
                field: value,
            },
        )
