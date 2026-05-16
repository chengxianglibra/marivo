"""Registration functions for MCP intent tools."""

from __future__ import annotations

from typing import Any, Literal

from marivo.contracts.generated import aoi
from marivo.transports.mcp.tools._async_bridge import call_runtime
from marivo.transports.mcp.tools.schemas import (
    McpSliceRef,
    McpStructuredObject,
    McpTestHypothesis,
    McpTimeScope,
    McpTimeScopeValidated,
    ObserveScope,
)


def _omit_none(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}


def to_aoi_observe_request(
    metric: str,
    time_scope: McpTimeScope,
    granularity: Literal["hour", "day", "week", "month", "quarter", "year"] = None,  # type: ignore[assignment]  # noqa: RUF013
    dimensions: list[str] = None,  # type: ignore[assignment]  # noqa: RUF013
    filter_expression: dict[str, Any] = None,  # type: ignore[assignment]  # noqa: RUF013
) -> aoi.Observe1 | aoi.Observe2 | aoi.Observe3:
    payload = _omit_none(
        {
            "metric": metric,
            "time_scope": time_scope.model_dump(),
            "filter": filter_expression,
            "granularity": granularity,
            "dimensions": dimensions,
        }
    )
    if dimensions is not None:
        return aoi.Observe3.model_validate(payload)
    if granularity is not None:
        return aoi.Observe2.model_validate(payload)
    return aoi.Observe1.model_validate(payload)


def to_aoi_compare_request(
    left_artifact_id: str,
    right_artifact_id: str,
    compare_type: Literal[
        "normal",
        "yoy",
        "mom",
        "wow",
        "holiday_aligned_yoy",
        "weekday_aligned_yoy",
        "weekday_aligned_mom",
    ] = "normal",
) -> aoi.Compare:
    return aoi.Compare.model_validate(
        _omit_none(
            {
                "left_artifact_id": left_artifact_id,
                "right_artifact_id": right_artifact_id,
                "compare_type": compare_type,
            }
        )
    )


def to_aoi_decompose_request(
    compare_artifact_id: str,
    dimension: str,
    limit: int = None,  # type: ignore[assignment]  # noqa: RUF013
) -> aoi.Decompose:
    return aoi.Decompose.model_validate(
        _omit_none(
            {
                "compare_artifact_id": compare_artifact_id,
                "dimension": dimension,
                "limit": limit,
            }
        )
    )


def to_aoi_forecast_request(
    source_artifact_id: str,
    horizon: int,
    profile: str = None,  # type: ignore[assignment]  # noqa: RUF013
) -> aoi.Forecast:
    return aoi.Forecast.model_validate(
        _omit_none(
            {
                "source_artifact_id": source_artifact_id,
                "horizon": horizon,
                "profile": profile,
            }
        )
    )


def to_aoi_correlate_request(
    left_artifact_id: str,
    right_artifact_id: str,
    method: Literal["pearson", "spearman"] = None,  # type: ignore[assignment]  # noqa: RUF013
) -> aoi.Correlate:
    return aoi.Correlate.model_validate(
        _omit_none(
            {
                "left_artifact_id": left_artifact_id,
                "right_artifact_id": right_artifact_id,
                "method": method,
            }
        )
    )


def to_aoi_detect_request(
    metric: str,
    time_scope: McpTimeScope,
    granularity: Literal["hour", "day", "week", "month", "quarter", "year"],
    strategy: Literal["point_anomaly", "period_shift"],
    filter_expression: dict[str, Any] = None,  # type: ignore[assignment]  # noqa: RUF013
    dimension: str = None,  # type: ignore[assignment]  # noqa: RUF013
    sensitivity: Literal["conservative", "balanced", "aggressive"] = "aggressive",
    limit: int = None,  # type: ignore[assignment]  # noqa: RUF013
) -> aoi.Detect:
    return aoi.Detect.model_validate(
        _omit_none(
            {
                "metric": metric,
                "time_scope": time_scope.model_dump(),
                "granularity": granularity,
                "filter": filter_expression,
                "dimension": dimension,
                "strategy": strategy,
                "sensitivity": sensitivity,
                "limit": limit,
            }
        )
    )


def _to_aoi_slice(slice_ref: McpSliceRef) -> dict[str, Any]:
    return {
        "time_scope": slice_ref.time_scope.model_dump(),
    }


def to_aoi_test_request(
    metric: str,
    left: McpSliceRef,
    right: McpSliceRef,
    hypothesis: McpTestHypothesis | dict[str, Any],
) -> aoi.Test:
    hypothesis_model = (
        hypothesis
        if isinstance(hypothesis, McpTestHypothesis)
        else McpTestHypothesis.model_validate(hypothesis)
    )
    return aoi.Test.model_validate(
        {
            "metric": metric,
            "left": _to_aoi_slice(left),
            "right": _to_aoi_slice(right),
            "kind": "numeric",
            "hypothesis": {
                "family": "two_sample_mean",
                "alternative": hypothesis_model.alternative,
                "significance": hypothesis_model.significance,
            },
        }
    )


def register_observe(server: Any, runtime: Any) -> None:
    @server.tool()  # type: ignore
    async def observe(
        session_id: str,
        metric: str,
        time_scope: McpTimeScopeValidated,
        granularity: Literal["hour", "day", "week", "month", "quarter", "year"] = None,  # type: ignore[assignment]  # noqa: RUF013
        dimensions: list[str] = None,  # type: ignore[assignment]  # noqa: RUF013
        filter_expression: McpStructuredObject = None,  # type: ignore[assignment]
    ) -> dict[str, Any]:
        request = to_aoi_observe_request(
            metric=metric,
            time_scope=time_scope,
            granularity=granularity,
            dimensions=dimensions,
            filter_expression=filter_expression,
        )
        return await call_runtime(runtime.observe, session_id=session_id, request=request)


def register_compare(server: Any, runtime: Any) -> None:
    @server.tool()  # type: ignore
    async def compare(
        session_id: str,
        left_artifact_id: str,
        right_artifact_id: str,
        compare_type: Literal[
            "normal",
            "yoy",
            "mom",
            "wow",
            "holiday_aligned_yoy",
            "weekday_aligned_yoy",
            "weekday_aligned_mom",
        ] = "normal",
    ) -> dict[str, Any]:
        request = to_aoi_compare_request(
            left_artifact_id=left_artifact_id,
            right_artifact_id=right_artifact_id,
            compare_type=compare_type,
        )
        return await call_runtime(runtime.compare, session_id=session_id, request=request)


def register_decompose(server: Any, runtime: Any) -> None:
    @server.tool()  # type: ignore
    async def decompose(
        session_id: str,
        compare_artifact_id: str,
        dimension: str,
        limit: int = None,  # type: ignore[assignment]  # noqa: RUF013
    ) -> dict[str, Any]:
        request = to_aoi_decompose_request(
            compare_artifact_id=compare_artifact_id,
            dimension=dimension,
            limit=limit,
        )
        return await call_runtime(runtime.decompose, session_id=session_id, request=request)


def register_detect(server: Any, runtime: Any) -> None:
    @server.tool()  # type: ignore
    async def detect(
        session_id: str,
        metric: str,
        time_scope: McpTimeScope,
        granularity: Literal["hour", "day", "week", "month", "quarter", "year"],
        strategy: Literal["point_anomaly", "period_shift"],
        filter_expression: McpStructuredObject = None,  # type: ignore[assignment]
        dimension: str = None,  # type: ignore[assignment]  # noqa: RUF013
        sensitivity: Literal["conservative", "balanced", "aggressive"] = "aggressive",
        limit: int = None,  # type: ignore[assignment]  # noqa: RUF013
    ) -> dict[str, Any]:
        request = to_aoi_detect_request(
            metric=metric,
            time_scope=time_scope,
            granularity=granularity,
            filter_expression=filter_expression,
            dimension=dimension,
            strategy=strategy,
            sensitivity=sensitivity,
            limit=limit,
        )
        return await call_runtime(runtime.detect, session_id=session_id, request=request)


def register_correlate(server: Any, runtime: Any) -> None:
    @server.tool()  # type: ignore
    async def correlate(
        session_id: str,
        left_artifact_id: str,
        right_artifact_id: str,
        method: Literal["pearson", "spearman"] = None,  # type: ignore[assignment]  # noqa: RUF013
    ) -> dict[str, Any]:
        request = to_aoi_correlate_request(
            left_artifact_id=left_artifact_id,
            right_artifact_id=right_artifact_id,
            method=method,
        )
        return await call_runtime(runtime.correlate, session_id=session_id, request=request)


def register_forecast(server: Any, runtime: Any) -> None:
    @server.tool()  # type: ignore
    async def forecast(
        session_id: str,
        source_artifact_id: str,
        horizon: int,
        profile: str = None,  # type: ignore[assignment]  # noqa: RUF013
    ) -> dict[str, Any]:
        request = to_aoi_forecast_request(
            source_artifact_id=source_artifact_id,
            horizon=horizon,
            profile=profile,
        )
        return await call_runtime(runtime.forecast, session_id=session_id, request=request)


def register_attribute(server: Any, runtime: Any) -> None:
    @server.tool()  # type: ignore
    async def attribute(
        session_id: str,
        metric: str,
        left: McpSliceRef,
        right: McpSliceRef,
        dimensions: list[str],
        decomposition_method: str = "delta_share",
        decomposition_limit: int = 5,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "metric": metric,
            "left": left.model_dump(),
            "right": right.model_dump(),
            "dimensions": dimensions,
            "decomposition_method": decomposition_method,
            "decomposition_limit": decomposition_limit,
        }
        return await call_runtime(runtime.attribute, session_id=session_id, params=params)


def register_diagnose(server: Any, runtime: Any) -> None:
    @server.tool()  # type: ignore
    async def diagnose(
        session_id: str,
        metric: str,
        candidate_dimensions: list[str],
        strategy: Literal["point_anomaly", "period_shift"],
        mode: Literal["auto_detect", "explicit_compare"] = "auto_detect",
        time_scope: McpTimeScope | None = None,
        granularity: Literal["hour", "day", "week", "month"] | None = None,
        current: McpSliceRef | None = None,
        baseline: McpSliceRef | None = None,
        scope: ObserveScope | None = None,
        detect_dimension: str | None = None,
        sensitivity: Literal["conservative", "balanced", "aggressive"] = "aggressive",
        candidate_limit: int | None = None,
        followup_limit: int | None = 3,
        decomposition_limit: int | None = 5,
        baseline_policy: Literal[
            "previous_adjacent_equal_length"
        ] = "previous_adjacent_equal_length",
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "metric": metric,
            "candidate_dimensions": candidate_dimensions,
            "mode": mode,
            "strategy": strategy,
            "sensitivity": sensitivity,
            "baseline_policy": baseline_policy,
        }
        if time_scope is not None:
            params["time_scope"] = time_scope.model_dump()
        if granularity is not None:
            params["granularity"] = granularity
        if current is not None:
            params["current"] = current.model_dump()
        if baseline is not None:
            params["baseline"] = baseline.model_dump()
        if scope is not None:
            params["scope"] = scope.model_dump()
        if detect_dimension is not None:
            params["detect_dimension"] = detect_dimension
        if candidate_limit is not None:
            params["candidate_limit"] = candidate_limit
        if followup_limit is not None:
            params["followup_limit"] = followup_limit
        if decomposition_limit is not None:
            params["decomposition_limit"] = decomposition_limit
        return await call_runtime(runtime.diagnose, session_id=session_id, params=params)


def register_test_intent(server: Any, runtime: Any) -> None:
    @server.tool()  # type: ignore
    async def test_intent(
        session_id: str,
        metric: str,
        left: McpSliceRef,
        right: McpSliceRef,
        hypothesis: McpTestHypothesis,
    ) -> dict[str, Any]:
        request = to_aoi_test_request(
            metric=metric,
            left=left,
            right=right,
            hypothesis=hypothesis,
        )
        return await call_runtime(runtime.test, session_id=session_id, request=request)


def register_validate(server: Any, runtime: Any) -> None:
    @server.tool()  # type: ignore
    async def validate(
        session_id: str,
        metric: str,
        left: McpSliceRef,
        right: McpSliceRef,
        hypothesis: McpStructuredObject | None = None,
        method: Literal["auto", "welch_t"] | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "metric": metric,
            "left": left.model_dump(),
            "right": right.model_dump(),
        }
        if hypothesis is not None:
            params["hypothesis"] = hypothesis
        if method is not None:
            params["method"] = method
        return await call_runtime(runtime.validate, session_id=session_id, params=params)
