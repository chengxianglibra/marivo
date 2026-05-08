"""Registration functions for MCP intent tools."""

from __future__ import annotations

from typing import Any, Literal

from app.transports.mcp.tools._async_bridge import call_runtime
from app.transports.mcp.tools.schemas import (
    McpCompareArtifactRef,
    McpDetectTimeScope,
    McpObservationRef,
    McpObserveTimeScope,
    McpStructuredObject,
    ObserveScope,
)


def register_observe(server: Any, runtime: Any) -> None:
    @server.tool()  # type: ignore
    async def observe(
        session_id: str,
        metric: str,
        time_scope: McpObserveTimeScope,
        granularity: str | None = None,
        dimensions: list[str] | None = None,
        scope: ObserveScope | None = None,
        result_mode: str | None = None,
        calendar_policy_ref: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"metric": metric, "time_scope": time_scope.model_dump()}
        if granularity is not None:
            params["granularity"] = granularity
        if dimensions is not None:
            params["dimensions"] = dimensions
        if scope is not None:
            params["scope"] = scope.model_dump()
        if result_mode is not None:
            params["result_mode"] = result_mode
        if calendar_policy_ref is not None:
            params["calendar_policy_ref"] = calendar_policy_ref
        return await call_runtime(runtime.observe, session_id=session_id, params=params)


def register_compare(server: Any, runtime: Any) -> None:
    @server.tool()  # type: ignore
    async def compare(
        session_id: str,
        left_ref: McpObservationRef,
        right_ref: McpObservationRef,
        mode: Literal["auto", "scalar", "segmented", "time_series"] = "auto",
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "left_ref": left_ref.model_dump(exclude_none=True),
            "right_ref": right_ref.model_dump(exclude_none=True),
            "mode": mode,
        }
        return await call_runtime(runtime.compare, session_id=session_id, params=params)


def register_decompose(server: Any, runtime: Any) -> None:
    @server.tool()  # type: ignore
    async def decompose(
        session_id: str,
        compare_ref: McpCompareArtifactRef,
        dimension: str,
        method: str = "delta_share",
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "compare_ref": compare_ref.model_dump(exclude_none=True),
            "dimension": dimension,
            "method": method,
        }
        return await call_runtime(runtime.decompose, session_id=session_id, params=params)


def register_detect(server: Any, runtime: Any) -> None:
    @server.tool()  # type: ignore
    async def detect(
        session_id: str,
        metric: str,
        time_scope: McpDetectTimeScope,
        granularity: Literal["hour", "day", "week", "month"],
        scope: ObserveScope | None = None,
        split_by: str | None = None,
        profile: Literal["auto", "spike_dip", "level_shift", "seasonal_residual"] = "auto",
        sensitivity: Literal["conservative", "balanced", "aggressive"] = "balanced",
        limit: int | None = None,
        max_series: int | None = None,
        patterns: list[Literal["point_anomaly", "period_shift"]] | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "metric": metric,
            "time_scope": time_scope.model_dump(),
            "granularity": granularity,
            "profile": profile,
            "sensitivity": sensitivity,
        }
        if scope is not None:
            params["scope"] = scope.model_dump()
        if split_by is not None:
            params["split_by"] = split_by
        if limit is not None:
            params["limit"] = limit
        if max_series is not None:
            params["max_series"] = max_series
        if patterns is not None:
            params["patterns"] = patterns
        return await call_runtime(runtime.detect, session_id=session_id, params=params)


def register_correlate(server: Any, runtime: Any) -> None:
    @server.tool()  # type: ignore
    async def correlate(
        session_id: str,
        left_ref: McpStructuredObject,
        right_ref: McpStructuredObject,
        method: str = "spearman",
        min_pairs: int = 5,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "left_ref": left_ref,
            "right_ref": right_ref,
            "method": method,
            "min_pairs": min_pairs,
        }
        return await call_runtime(runtime.correlate, session_id=session_id, params=params)


def register_test_intent(server: Any, runtime: Any) -> None:
    @server.tool()  # type: ignore
    async def test_intent(
        session_id: str,
        left_ref: McpStructuredObject,
        right_ref: McpStructuredObject,
        hypothesis: McpStructuredObject,
        method: str = "auto",
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "left_ref": left_ref,
            "right_ref": right_ref,
            "hypothesis": hypothesis,
            "method": method,
        }
        return await call_runtime(runtime.test, session_id=session_id, params=params)


def register_forecast(server: Any, runtime: Any) -> None:
    @server.tool()  # type: ignore
    async def forecast(
        session_id: str,
        source_ref: McpStructuredObject,
        horizon: int,
        profile: str = "auto",
        interval_level: float | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "source_ref": source_ref,
            "horizon": horizon,
            "profile": profile,
        }
        if interval_level is not None:
            params["interval_level"] = interval_level
        return await call_runtime(runtime.forecast, session_id=session_id, params=params)


def register_attribute(server: Any, runtime: Any) -> None:
    @server.tool()  # type: ignore
    async def attribute(
        session_id: str,
        metric: str,
        left: McpStructuredObject,
        right: McpStructuredObject,
        dimensions: list[str],
        decomposition_method: str = "delta_share",
        decomposition_limit: int = 5,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "metric": metric,
            "left": left,
            "right": right,
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
        mode: Literal["auto_detect", "explicit_compare"] = "auto_detect",
        time_scope: McpStructuredObject | None = None,
        granularity: Literal["hour", "day", "week", "month"] | None = None,
        current: McpStructuredObject | None = None,
        baseline: McpStructuredObject | None = None,
        scope: ObserveScope | None = None,
        detect_split_by: str | None = None,
        profile: Literal["auto", "spike_dip", "level_shift", "seasonal_residual"] = "auto",
        sensitivity: Literal["conservative", "balanced", "aggressive"] = "balanced",
        candidate_limit: int | None = None,
        followup_limit: int | None = 3,
        decomposition_limit: int | None = 5,
        patterns: list[Literal["point_anomaly", "period_shift"]] | None = None,
        baseline_policy: Literal[
            "previous_adjacent_equal_length"
        ] = "previous_adjacent_equal_length",
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "metric": metric,
            "candidate_dimensions": candidate_dimensions,
            "mode": mode,
            "profile": profile,
            "sensitivity": sensitivity,
            "baseline_policy": baseline_policy,
        }
        if time_scope is not None:
            params["time_scope"] = time_scope
        if granularity is not None:
            params["granularity"] = granularity
        if current is not None:
            params["current"] = current
        if baseline is not None:
            params["baseline"] = baseline
        if scope is not None:
            params["scope"] = scope.model_dump()
        if detect_split_by is not None:
            params["detect_split_by"] = detect_split_by
        if candidate_limit is not None:
            params["candidate_limit"] = candidate_limit
        if followup_limit is not None:
            params["followup_limit"] = followup_limit
        if decomposition_limit is not None:
            params["decomposition_limit"] = decomposition_limit
        if patterns is not None:
            params["patterns"] = patterns
        return await call_runtime(runtime.diagnose, session_id=session_id, params=params)


def register_validate(server: Any, runtime: Any) -> None:
    @server.tool()  # type: ignore
    async def validate(
        session_id: str,
        metric: str,
        left: McpStructuredObject,
        right: McpStructuredObject,
        sample_kind: Literal["auto", "numeric", "rate"] | None = None,
        hypothesis: McpStructuredObject | None = None,
        method: Literal["auto", "welch_t", "two_proportion_z"] | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "metric": metric,
            "left": left,
            "right": right,
        }
        if sample_kind is not None:
            params["sample_kind"] = sample_kind
        if hypothesis is not None:
            params["hypothesis"] = hypothesis
        if method is not None:
            params["method"] = method
        return await call_runtime(runtime.validate, session_id=session_id, params=params)
