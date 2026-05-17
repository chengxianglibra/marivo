"""Registration functions for MCP intent tools."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field

from marivo.contracts.generated import aoi
from marivo.transports.mcp.tools._async_bridge import call_runtime
from marivo.transports.mcp.tools.schemas import (
    McpAoiSliceRef,
    McpExpression,
    McpTestHypothesis,
    McpTimeScope,
    McpTimeScopeValidated,
    McpValidateHypothesis,
)

TimeSeriesObserveArtifactId = Annotated[
    str,
    Field(
        description=(
            "Committed observe(time_series) artifact ID from this session. Produce it with "
            "observe(granularity=...) and no dimensions; scalar, segmented, datasource, and "
            "forecast artifacts are not valid."
        )
    ),
]

CompareObserveArtifactId = Annotated[
    str,
    Field(
        description=(
            "Committed observe artifact ID from this session. compare accepts scalar, "
            "segmented, or time_series observe artifacts when left and right have the same "
            "observation family. Segmented observe artifacts such as dimensions=['log_hour'] "
            "are valid with compare_type='normal'; calendar-aligned compare types require "
            "time_series artifacts."
        )
    ),
]


def _omit_none(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}


def _dump_expression(expression: McpExpression | dict[str, Any] | None) -> dict[str, Any] | None:
    if expression is None:
        return None
    if isinstance(expression, McpExpression):
        return expression.model_dump(exclude_none=True)
    return expression


def to_aoi_observe_request(
    metric: str,
    time_scope: McpTimeScope,
    granularity: Literal["hour", "day", "week", "month", "quarter", "year"] | None = None,
    dimensions: list[str] | None = None,
    filter_expression: McpExpression | dict[str, Any] | None = None,
) -> aoi.Observe1 | aoi.Observe2 | aoi.Observe3:
    payload = _omit_none(
        {
            "metric": metric,
            "time_scope": time_scope.model_dump(),
            "filter": _dump_expression(filter_expression),
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
        "holiday_aligned",
        "weekday_aligned",
        "holiday_and_weekday_aligned",
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
) -> aoi.Forecast:
    return aoi.Forecast.model_validate(
        _omit_none(
            {
                "source_artifact_id": source_artifact_id,
                "horizon": horizon,
            }
        )
    )


def to_aoi_correlate_request(
    left_artifact_id: str,
    right_artifact_id: str,
    method: Literal["pearson", "spearman"] = None,  # type: ignore[assignment]  # noqa: RUF013
    min_pairs: int | None = None,
) -> aoi.Correlate:
    return aoi.Correlate.model_validate(
        _omit_none(
            {
                "left_artifact_id": left_artifact_id,
                "right_artifact_id": right_artifact_id,
                "method": method,
                "min_pairs": min_pairs,
            }
        )
    )


def to_aoi_detect_request(
    metric: str,
    time_scope: McpTimeScope,
    granularity: Literal["hour", "day", "week", "month", "quarter", "year"],
    strategy: Literal["point_anomaly", "period_shift"],
    filter_expression: McpExpression | dict[str, Any] | None = None,
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
                "filter": _dump_expression(filter_expression),
                "dimension": dimension,
                "strategy": strategy,
                "sensitivity": sensitivity,
                "limit": limit,
            }
        )
    )


def _to_aoi_slice(slice_ref: McpAoiSliceRef) -> dict[str, Any]:
    payload = {
        "time_scope": slice_ref.time_scope.model_dump(),
    }
    if slice_ref.filter is not None:
        payload["filter"] = slice_ref.filter.model_dump(exclude_none=True)
    return payload


def to_aoi_test_request(
    metric: str,
    left: McpAoiSliceRef,
    right: McpAoiSliceRef,
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


def to_aoi_validate_request(
    metric: str,
    left: McpAoiSliceRef,
    right: McpAoiSliceRef,
    hypothesis: McpValidateHypothesis | dict[str, Any] | None = None,
) -> aoi.Validate:
    hypothesis_model = (
        hypothesis
        if isinstance(hypothesis, McpValidateHypothesis)
        else McpValidateHypothesis.model_validate(hypothesis or {})
    )
    return aoi.Validate.model_validate(
        {
            "metric": metric,
            "left": _to_aoi_slice(left),
            "right": _to_aoi_slice(right),
            "hypothesis": {
                "family": "two_sample_mean",
                "alternative": hypothesis_model.alternative or "two_sided",
                "significance": hypothesis_model.significance or "balanced",
            },
        }
    )


def to_aoi_attribute_request(
    metric: str,
    left: McpAoiSliceRef,
    right: McpAoiSliceRef,
    dimensions: list[str],
    decomposition_method: Literal["delta_share"] = "delta_share",
    decomposition_limit: int = 5,
) -> aoi.Attribute:
    return aoi.Attribute.model_validate(
        {
            "metric": metric,
            "left": _to_aoi_slice(left),
            "right": _to_aoi_slice(right),
            "dimensions": dimensions,
            "decomposition_method": decomposition_method,
            "decomposition_limit": decomposition_limit,
        }
    )


def to_aoi_diagnose_request(
    metric: str,
    dimensions: list[str],
    strategy: Literal["point_anomaly", "period_shift"],
    time_scope: McpTimeScope,
    granularity: Literal["hour", "day", "week", "month", "quarter", "year"],
    filter_expression: McpExpression | dict[str, Any] | None = None,
    scan_dimension: str | None = None,
    sensitivity: Literal["conservative", "balanced", "aggressive"] = "aggressive",
    candidate_limit: int = 3,
    decomposition_limit: int = 5,
) -> aoi.Diagnose:
    return aoi.Diagnose.model_validate(
        _omit_none(
            {
                "metric": metric,
                "time_scope": time_scope.model_dump(),
                "granularity": granularity,
                "filter": _dump_expression(filter_expression),
                "scan_dimension": scan_dimension,
                "dimensions": dimensions,
                "strategy": strategy,
                "sensitivity": sensitivity,
                "candidate_limit": candidate_limit,
                "decomposition_limit": decomposition_limit,
            }
        )
    )


def register_observe(server: Any, runtime: Any) -> None:
    @server.tool()  # type: ignore
    async def observe(
        session_id: str,
        metric: str,
        time_scope: McpTimeScopeValidated,
        granularity: Annotated[
            Literal["hour", "day", "week", "month", "quarter", "year"] | None,
            Field(
                description=(
                    "Time-series observe selector. Provide this without dimensions; omit both "
                    "granularity and dimensions for scalar observe."
                )
            ),
        ] = None,
        dimensions: Annotated[
            list[str] | None,
            Field(
                description=(
                    "Segmented observe selector. Provide this without granularity; omit both "
                    "dimensions and granularity for scalar observe."
                )
            ),
        ] = None,
        filter_expression: Annotated[
            McpExpression | None,
            Field(
                description=(
                    "Optional AOI Expression filter object, e.g. "
                    "{'dialects': [{'dialect': 'ANSI_SQL', 'expression': \"region = 'US'\"}]}."
                )
            ),
        ] = None,
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
    @server.tool(  # type: ignore
        description=(
            "Compare two committed observe artifacts from the same session. scalar, segmented, "
            "and time_series observations are valid for compare_type='normal'; "
            "holiday_aligned, weekday_aligned, and holiday_and_weekday_aligned require "
            "observe(time_series) inputs."
        )
    )
    async def compare(
        session_id: str,
        left_artifact_id: CompareObserveArtifactId,
        right_artifact_id: CompareObserveArtifactId,
        compare_type: Annotated[
            Literal[
                "normal",
                "holiday_aligned",
                "weekday_aligned",
                "holiday_and_weekday_aligned",
            ],
            Field(
                description=(
                    "normal compares scalar, segmented, or time_series observations. "
                    "Calendar-aligned values are only valid for time_series observations."
                )
            ),
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
        filter_expression: Annotated[
            McpExpression | None,
            Field(
                description=(
                    "Optional AOI Expression filter object, e.g. "
                    "{'dialects': [{'dialect': 'ANSI_SQL', 'expression': \"region = 'US'\"}]}."
                )
            ),
        ] = None,
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
    @server.tool(  # type: ignore
        description=(
            "Correlate two committed observe(time_series) artifacts from the same session. "
            "Use artifact IDs returned by observe calls with granularity; scalar or segmented "
            "observe artifacts are invalid."
        )
    )
    async def correlate(
        session_id: str,
        left_artifact_id: TimeSeriesObserveArtifactId,
        right_artifact_id: TimeSeriesObserveArtifactId,
        method: Literal["pearson", "spearman"] = None,  # type: ignore[assignment]  # noqa: RUF013
        min_pairs: Annotated[
            int | None,
            Field(
                ge=1,
                description=(
                    "Minimum aligned numeric pair count required to run; omit to use "
                    "the service default of 5."
                ),
            ),
        ] = None,
    ) -> dict[str, Any]:
        request = to_aoi_correlate_request(
            left_artifact_id=left_artifact_id,
            right_artifact_id=right_artifact_id,
            method=method,
            min_pairs=min_pairs,
        )
        return await call_runtime(runtime.correlate, session_id=session_id, request=request)


def register_forecast(server: Any, runtime: Any) -> None:
    @server.tool(  # type: ignore
        description=(
            "Forecast from one committed observe(time_series) artifact from the same session. "
            "source_artifact_id must come from observe(granularity=...), not from datasource, "
            "scalar observe, segmented observe, or forecast output."
        )
    )
    async def forecast(
        session_id: str,
        source_artifact_id: TimeSeriesObserveArtifactId,
        horizon: int,
    ) -> dict[str, Any]:
        request = to_aoi_forecast_request(
            source_artifact_id=source_artifact_id,
            horizon=horizon,
        )
        return await call_runtime(runtime.forecast, session_id=session_id, request=request)


def register_attribute(server: Any, runtime: Any) -> None:
    @server.tool(  # type: ignore
        description=(
            "Attribute a known current-vs-baseline metric change. Use left for the "
            "current slice and right for the baseline slice."
        )
    )
    async def attribute(
        session_id: str,
        metric: str,
        left: McpAoiSliceRef,
        right: McpAoiSliceRef,
        dimensions: Annotated[
            list[str],
            Field(
                description=(
                    "Attribution dimensions used to explain the known current-vs-baseline "
                    "change. Each dimension produces an independent decompose result."
                )
            ),
        ],
        decomposition_method: Literal["delta_share"] = "delta_share",
        decomposition_limit: int = 5,
    ) -> dict[str, Any]:
        request = to_aoi_attribute_request(
            metric=metric,
            left=left,
            right=right,
            dimensions=dimensions,
            decomposition_method=decomposition_method,
            decomposition_limit=decomposition_limit,
        )
        return await call_runtime(runtime.attribute, session_id=session_id, request=request)


def register_diagnose(server: Any, runtime: Any) -> None:
    @server.tool(  # type: ignore
        description=(
            "Run bounded auto-detect anomaly diagnosis. The tool first detects anomalous "
            "candidates in time_scope at granularity, then follows up with compare and "
            "decompose across the requested dimensions."
        )
    )
    async def diagnose(
        session_id: str,
        metric: Annotated[
            str,
            Field(
                description=(
                    "Semantic metric identifier to diagnose. Diagnose runs against exactly "
                    "one metric."
                )
            ),
        ],
        dimensions: Annotated[
            list[str],
            Field(
                description=(
                    "Required attribution dimensions used after anomaly candidates are found. "
                    "These drive follow-up decompose steps and are independent from "
                    "scan_dimension."
                )
            ),
        ],
        strategy: Annotated[
            Literal["point_anomaly", "period_shift"],
            Field(description="Detection strategy passed to the internal detect step."),
        ],
        time_scope: Annotated[
            McpTimeScope,
            Field(
                description=(
                    "Required time range scanned by the internal detect step before follow-up "
                    "diagnosis."
                )
            ),
        ],
        granularity: Annotated[
            Literal["hour", "day", "week", "month", "quarter", "year"],
            Field(description="Required time bucket granularity used by the internal detect scan."),
        ],
        filter_expression: Annotated[
            McpExpression | None,
            Field(
                description=(
                    "Optional AOI Expression applied to candidate detection and follow-up "
                    "observe/compare/decompose steps."
                )
            ),
        ] = None,
        scan_dimension: Annotated[
            str | None,
            Field(
                description=(
                    "Optional single dimension used only to split the internal detect scan into "
                    "independent time series. Omit to scan the overall metric series; this is "
                    "independent from attribution dimensions."
                )
            ),
        ] = None,
        sensitivity: Annotated[
            Literal["conservative", "balanced", "aggressive"],
            Field(
                description=(
                    "Detection sensitivity preset passed to the internal detect step. Defaults "
                    "to aggressive."
                )
            ),
        ] = "aggressive",
        candidate_limit: Annotated[
            int | None,
            Field(
                description=(
                    "Maximum anomaly candidates to diagnose end-to-end. This bounds follow-up "
                    "candidates, not driver rows."
                ),
                ge=1,
            ),
        ] = 3,
        decomposition_limit: Annotated[
            int | None,
            Field(
                description=(
                    "Maximum driver rows returned per diagnosed candidate and attribution "
                    "dimension. This does not limit how many candidates are diagnosed."
                ),
                ge=1,
            ),
        ] = 5,
    ) -> dict[str, Any]:
        request = to_aoi_diagnose_request(
            metric=metric,
            dimensions=dimensions,
            strategy=strategy,
            time_scope=time_scope,
            granularity=granularity,
            filter_expression=filter_expression,
            scan_dimension=scan_dimension,
            sensitivity=sensitivity,
            candidate_limit=candidate_limit if candidate_limit is not None else 3,
            decomposition_limit=decomposition_limit if decomposition_limit is not None else 5,
        )
        return await call_runtime(runtime.diagnose, session_id=session_id, request=request)


def register_test_intent(server: Any, runtime: Any) -> None:
    @server.tool()  # type: ignore
    async def test_intent(
        session_id: str,
        metric: str,
        left: McpAoiSliceRef,
        right: McpAoiSliceRef,
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
        left: McpAoiSliceRef,
        right: McpAoiSliceRef,
        hypothesis: McpValidateHypothesis | None = None,
    ) -> dict[str, Any]:
        request = to_aoi_validate_request(
            metric=metric,
            left=left,
            right=right,
            hypothesis=hypothesis,
        )
        return await call_runtime(runtime.validate, session_id=session_id, request=request)
