"""Tests for MCP-friendly DTO conversion into generated AOI request models."""

from __future__ import annotations

from datetime import datetime

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


def _local_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone()


def test_to_aoi_observe_request_builds_scalar_model() -> None:
    request = to_aoi_observe_request(
        metric="view_time",
        time_scope=McpTimeScope(
            field="log_time",
            start="2026-05-01T00:00:00Z",
            end="2026-05-08T00:00:00Z",
        ),
    )

    assert isinstance(request, aoi.Observe)
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

    assert isinstance(request, aoi.Observe)
    assert request.granularity == "year"


def test_to_aoi_observe_request_accepts_naive_mcp_time_scope() -> None:
    request = to_aoi_observe_request(
        metric="view_time",
        time_scope=McpTimeScope(
            field="log_time",
            start="2026-05-01T00:00:00",
            end="2026-05-08 00:00:00",
        ),
        granularity="day",
    )

    assert isinstance(request, aoi.Observe)
    assert request.time_scope.start == _local_datetime("2026-05-01T00:00:00")
    assert request.time_scope.end == _local_datetime("2026-05-08T00:00:00")


def test_to_aoi_observe_request_accepts_date_only_mcp_time_scope() -> None:
    request = to_aoi_observe_request(
        metric="view_time",
        time_scope=McpTimeScope(
            field="log_time",
            start="2026-05-01",
            end="2026-05-08",
        ),
        granularity="day",
    )

    assert isinstance(request, aoi.Observe)
    assert request.time_scope.start == _local_datetime("2026-05-01T00:00:00")
    assert request.time_scope.end == _local_datetime("2026-05-08T00:00:00")


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

    assert isinstance(request, aoi.Observe)
    assert request.metric == "view_time"
    assert request.time_scope.field == "log_time"
    assert request.dimensions is not None
    assert [dimension.root for dimension in request.dimensions] == ["region", "platform"]
    assert request.filter is not None
    assert request.filter.model_dump(exclude_none=True) == {
        "dialects": [{"dialect": "ANSI_SQL", "expression": "region = 'US'"}]
    }


def test_to_aoi_observe_request_accepts_granularity_and_dimensions() -> None:
    request = to_aoi_observe_request(
        metric="view_time",
        time_scope=McpTimeScope(
            field="log_time",
            start="2026-05-01T00:00:00Z",
            end="2026-05-08T00:00:00Z",
        ),
        granularity="day",
        dimensions=["region"],
    )

    assert isinstance(request, aoi.Observe)
    assert request.granularity == "day"
    assert [dimension.root for dimension in request.dimensions] == ["region"]


def test_to_aoi_compare_request_builds_compare_model() -> None:
    request = to_aoi_compare_request(
        current_artifact_id="artifact_obs_left",
        baseline_artifact_id="artifact_obs_right",
        compare_type="holiday_aligned",
    )

    assert isinstance(request, aoi.Compare)
    assert request.current_artifact_id == "artifact_obs_left"
    assert request.baseline_artifact_id == "artifact_obs_right"
    assert request.compare_type == "holiday_aligned"


def test_to_aoi_compare_request_defaults_compare_type() -> None:
    request = to_aoi_compare_request(
        current_artifact_id="artifact_obs_left",
        baseline_artifact_id="artifact_obs_right",
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


def test_to_aoi_detect_request_builds_artifact_input_model() -> None:
    request = to_aoi_detect_request(
        source_artifact_id="artifact_source",
        sensitivity="balanced",
        limit=5,
    )

    assert isinstance(request, aoi.Detect)
    assert request.source_artifact_id == "artifact_source"
    assert request.sensitivity == "balanced"
    assert request.limit == 5


def test_to_aoi_detect_request_omits_absent_optional_fields() -> None:
    request = to_aoi_detect_request(
        source_artifact_id="artifact_source",
    )

    dumped = request.model_dump(exclude_none=True)
    assert dumped == {
        "source_artifact_id": "artifact_source",
        "sensitivity": "aggressive",
    }
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


def _slice(start: str, end: str, slice_filter: McpExpression | None = None) -> McpAoiSliceRef:
    return McpAoiSliceRef(
        time_scope=McpTimeScope(
            field="log_time",
            start=start,
            end=end,
        ),
        filter=slice_filter,
    )


def test_to_aoi_test_request_builds_test_model() -> None:
    request = to_aoi_test_request(
        metric="view_time",
        current=_slice("2026-05-01T00:00:00Z", "2026-05-08T00:00:00Z"),
        baseline=_slice("2026-04-24T00:00:00Z", "2026-05-01T00:00:00Z"),
        grain="day",
        hypothesis=McpTestHypothesis(
            alternative="greater",
            significance="balanced",
        ),
    )

    assert isinstance(request, aoi.Test)
    assert request.kind == "numeric"
    assert request.grain == "day"
    assert request.hypothesis.family == "two_sample_mean"
    assert "filter" not in request.model_dump(exclude_none=True)["current"]
    assert request.hypothesis.alternative == "greater"
    assert request.hypothesis.significance == "balanced"


@pytest.mark.parametrize("grain", ["quarter", "year"])
def test_to_aoi_test_request_accepts_time_granularity_grain(grain: str) -> None:
    request = to_aoi_test_request(
        metric="view_time",
        current=_slice("2026-01-01T00:00:00Z", "2027-01-01T00:00:00Z"),
        baseline=_slice("2025-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        grain=grain,  # type: ignore[arg-type]
        hypothesis=McpTestHypothesis(
            alternative="two_sided",
            significance="balanced",
        ),
    )

    assert request.grain == grain


def test_to_aoi_test_request_accepts_naive_mcp_slice_time_scope() -> None:
    request = to_aoi_test_request(
        metric="view_time",
        current=_slice("2026-05-01T00:00:00", "2026-05-08T00:00:00"),
        baseline=_slice("2026-04-24 00:00:00", "2026-05-01 00:00:00"),
        grain="day",
        hypothesis=McpTestHypothesis(
            alternative="greater",
            significance="balanced",
        ),
    )

    assert isinstance(request, aoi.Test)
    assert request.current.time_scope.start == _local_datetime("2026-05-01T00:00:00")
    assert request.baseline.time_scope.end == _local_datetime("2026-05-01T00:00:00")


def test_to_aoi_test_request_accepts_date_only_mcp_slice_time_scope() -> None:
    request = to_aoi_test_request(
        metric="view_time",
        current=_slice("2026-05-01", "2026-05-08"),
        baseline=_slice("2026-04-24", "2026-05-01"),
        grain="day",
        hypothesis=McpTestHypothesis(
            alternative="greater",
            significance="balanced",
        ),
    )

    assert isinstance(request, aoi.Test)
    assert request.current.time_scope.start == _local_datetime("2026-05-01T00:00:00")
    assert request.baseline.time_scope.end == _local_datetime("2026-05-01T00:00:00")


@pytest.mark.parametrize("alternative", ["two_sided", "greater", "less"])
@pytest.mark.parametrize("significance", ["conservative", "balanced", "aggressive"])
def test_to_aoi_test_request_passes_supported_hypothesis_options(
    alternative: str,
    significance: str,
) -> None:
    request = to_aoi_test_request(
        metric="view_time",
        current=_slice("2026-05-01T00:00:00Z", "2026-05-08T00:00:00Z"),
        baseline=_slice("2026-04-24T00:00:00Z", "2026-05-01T00:00:00Z"),
        grain="week",
        hypothesis=McpTestHypothesis(
            alternative=alternative,
            significance=significance,
        ),
    )

    assert request.kind == "numeric"
    assert request.grain == "week"
    assert request.hypothesis.family == "two_sample_mean"
    assert request.hypothesis.alternative == alternative
    assert request.hypothesis.significance == significance


def test_to_aoi_validate_request_builds_validate_model_with_defaults() -> None:
    request = to_aoi_validate_request(
        metric="view_time",
        current=_slice("2026-05-01T00:00:00Z", "2026-05-08T00:00:00Z"),
        baseline=_slice("2026-04-24T00:00:00Z", "2026-05-01T00:00:00Z"),
        grain="day",
    )

    assert isinstance(request, aoi.Validate)
    assert request.grain == "day"
    assert request.hypothesis.family == "two_sample_mean"
    assert request.hypothesis.alternative == "two_sided"
    assert request.hypothesis.significance == "balanced"
    assert "filter" not in request.model_dump(exclude_none=True)["current"]


@pytest.mark.parametrize("grain", ["quarter", "year"])
def test_to_aoi_validate_request_accepts_time_granularity_grain(grain: str) -> None:
    request = to_aoi_validate_request(
        metric="view_time",
        current=_slice("2026-01-01T00:00:00Z", "2027-01-01T00:00:00Z"),
        baseline=_slice("2025-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        grain=grain,  # type: ignore[arg-type]
    )

    assert request.grain == grain


def test_to_aoi_validate_request_preserves_aoi_slice_filter() -> None:
    request = to_aoi_validate_request(
        metric="view_time",
        current=_slice(
            "2026-05-01T00:00:00Z",
            "2026-05-08T00:00:00Z",
            McpExpression(dialects=[{"dialect": "ANSI_SQL", "expression": "region = 'US'"}]),
        ),
        baseline=_slice("2026-04-24T00:00:00Z", "2026-05-01T00:00:00Z"),
        grain="day",
        hypothesis=McpValidateHypothesis(alternative="greater"),
    )

    assert isinstance(request, aoi.Validate)
    assert request.current.filter is not None
    assert request.current.filter.model_dump(exclude_none=True) == {
        "dialects": [{"dialect": "ANSI_SQL", "expression": "region = 'US'"}]
    }
    assert request.hypothesis.alternative == "greater"
    assert request.hypothesis.significance == "balanced"


def test_to_aoi_attribute_request_preserves_aoi_slice_filter() -> None:
    request = to_aoi_attribute_request(
        metric="view_time",
        current=_slice(
            "2026-05-01T00:00:00Z",
            "2026-05-08T00:00:00Z",
            McpExpression(dialects=[{"dialect": "ANSI_SQL", "expression": "region = 'US'"}]),
        ),
        baseline=_slice("2026-04-24T00:00:00Z", "2026-05-01T00:00:00Z"),
        dimensions=["region"],
        decomposition_limit=10,
    )

    assert isinstance(request, aoi.Attribute)
    assert request.current.filter is not None
    assert request.current.filter.model_dump(exclude_none=True) == {
        "dialects": [{"dialect": "ANSI_SQL", "expression": "region = 'US'"}]
    }
    assert [dimension.root for dimension in request.dimensions] == ["region"]
    assert request.decomposition_method == "delta_share"
    assert request.decomposition_limit == 10


def test_to_aoi_attribute_request_uses_contract_defaults() -> None:
    request = to_aoi_attribute_request(
        metric="view_time",
        current=_slice("2026-05-01T00:00:00Z", "2026-05-08T00:00:00Z"),
        baseline=_slice("2026-04-24T00:00:00Z", "2026-05-01T00:00:00Z"),
        dimensions=["region"],
    )

    assert isinstance(request, aoi.Attribute)
    assert request.decomposition_method == "delta_share"
    assert request.decomposition_limit == 5


def test_to_aoi_diagnose_request_builds_auto_detect_model() -> None:
    request = to_aoi_diagnose_request(
        metric="view_time",
        time_scope=McpTimeScope(
            field="log_time",
            start="2026-05-01T00:00:00Z",
            end="2026-05-08T00:00:00Z",
        ),
        granularity="year",
        filter_expression=McpExpression(
            dialects=[{"dialect": "ANSI_SQL", "expression": "region = 'US'"}]
        ),
        scan_dimension="region",
        dimensions=["region"],
        strategy="point_anomaly",
        candidate_limit=5,
    )

    assert isinstance(request, aoi.Diagnose)
    assert request.granularity == "year"
    assert request.time_scope.field == "log_time"
    assert request.filter is not None
    assert request.filter.model_dump(exclude_none=True) == {
        "dialects": [{"dialect": "ANSI_SQL", "expression": "region = 'US'"}]
    }
    assert [dimension.root for dimension in request.dimensions] == ["region"]
    assert request.scan_dimension == "region"
    assert request.candidate_limit == 5


def test_to_aoi_test_request_preserves_aoi_slice_filter() -> None:
    request = to_aoi_test_request(
        metric="view_time",
        current=_slice(
            "2026-05-01T00:00:00Z",
            "2026-05-08T00:00:00Z",
            McpExpression(dialects=[{"dialect": "ANSI_SQL", "expression": "region = 'US'"}]),
        ),
        baseline=_slice("2026-04-24T00:00:00Z", "2026-05-01T00:00:00Z"),
        grain="day",
        hypothesis=McpTestHypothesis(
            alternative="greater",
            significance="balanced",
        ),
    )

    assert request.current.filter is not None
    assert request.current.filter.model_dump(exclude_none=True) == {
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


def test_aoi_slice_ref_rejects_filter_expression_alias() -> None:
    try:
        McpAoiSliceRef.model_validate(
            {
                "time_scope": {
                    "field": "log_time",
                    "start": "2026-05-01T00:00:00Z",
                    "end": "2026-05-08T00:00:00Z",
                },
                "filter_expression": {
                    "dialects": [
                        {"dialect": "ANSI_SQL", "expression": "region = 'US'"},
                    ],
                },
            }
        )
    except ValueError as error:
        assert "filter_expression" in str(error)
        assert "Extra inputs are not permitted" in str(error)
    else:
        raise AssertionError("expected AOI slice DTO to reject filter_expression")


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
        ("family", "two_sample_mean"),
        ("alpha", 0.05),
    ],
)
def test_to_aoi_validate_request_rejects_non_mcp_hypothesis_fields(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValueError, match=field):
        to_aoi_validate_request(
            metric="view_time",
            current=_slice("2026-05-01T00:00:00Z", "2026-05-08T00:00:00Z"),
            baseline=_slice("2026-04-24T00:00:00Z", "2026-05-01T00:00:00Z"),
            grain="day",
            hypothesis={
                "alternative": "greater",
                "significance": "balanced",
                field: value,
            },
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
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
            current=_slice("2026-05-01T00:00:00Z", "2026-05-08T00:00:00Z"),
            baseline=_slice("2026-04-24T00:00:00Z", "2026-05-01T00:00:00Z"),
            grain="day",
            hypothesis={
                "alternative": "greater",
                "significance": "balanced",
                field: value,
            },
        )
