"""Registration functions for MCP intent tools."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field

from marivo.contracts.generated import aoi
from marivo.runtime.intents.diagnose_projection import compact_diagnose_envelope
from marivo.transports.mcp.tools._async_bridge import call_runtime
from marivo.transports.mcp.tools.schemas import (
    McpAoiSliceRef,
    McpExpression,
    McpTestHypothesis,
    McpTimeScope,
    McpTimeScopeValidated,
    McpValidateHypothesis,
)

_ReasoningField = Annotated[
    str | None,
    Field(
        description=(
            "Optional free-text explanation of why this intent was called, "
            "what hypothesis it tests, or what decision led to this step. "
            "Included in the analysis report for auditability."
        )
    ),
]

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
            "segmented, time_series, or panel observe artifacts when left and right have "
            "the same observation family. Segmented observe artifacts such as "
            "dimensions=['log_hour'] are valid with compare_type='normal'; "
            "calendar-aligned compare types require time_series or panel artifacts."
        )
    ),
]

TimeGranularity = Annotated[
    Literal["hour", "day", "week", "month", "quarter", "year"],
    Field(
        description=(
            "Required AOI time granularity used as the statistical sample unit for "
            "the `grain` field in hypothesis testing. This is not an observe output selector."
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
) -> aoi.Observe:
    payload = _omit_none(
        {
            "metric": metric,
            "time_scope": time_scope.model_dump(),
            "filter": _dump_expression(filter_expression),
            "granularity": granularity,
            "dimensions": dimensions,
        }
    )
    return aoi.Observe.model_validate(payload)


def to_aoi_compare_request(
    current_artifact_id: str,
    baseline_artifact_id: str,
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
                "current_artifact_id": current_artifact_id,
                "baseline_artifact_id": baseline_artifact_id,
                "compare_type": compare_type,
            }
        )
    )


def to_aoi_decompose_request(
    compare_artifact_id: str,
    dimension: str,
    limit: int | None = None,
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
    method: Literal["pearson", "spearman"] | None = None,
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
    dimension: str | None = None,
    sensitivity: Literal["conservative", "balanced", "aggressive"] = "aggressive",
    limit: int | None = None,
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
    current: McpAoiSliceRef,
    baseline: McpAoiSliceRef,
    grain: Literal["hour", "day", "week", "month", "quarter", "year"],
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
            "current": _to_aoi_slice(current),
            "baseline": _to_aoi_slice(baseline),
            "grain": grain,
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
    current: McpAoiSliceRef,
    baseline: McpAoiSliceRef,
    grain: Literal["hour", "day", "week", "month", "quarter", "year"],
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
            "current": _to_aoi_slice(current),
            "baseline": _to_aoi_slice(baseline),
            "grain": grain,
            "hypothesis": {
                "family": "two_sample_mean",
                "alternative": hypothesis_model.alternative or "two_sided",
                "significance": hypothesis_model.significance or "balanced",
            },
        }
    )


def to_aoi_attribute_request(
    metric: str,
    current: McpAoiSliceRef,
    baseline: McpAoiSliceRef,
    dimensions: list[str],
    decomposition_method: Literal["delta_share"] = "delta_share",
    decomposition_limit: int = 5,
) -> aoi.Attribute:
    return aoi.Attribute.model_validate(
        {
            "metric": metric,
            "current": _to_aoi_slice(current),
            "baseline": _to_aoi_slice(baseline),
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
    @server.tool(  # type: ignore
        description=(
            "Observe one semantic metric over an AOI time_scope. Omit both granularity and "
            "dimensions for scalar output, provide only granularity for time_series output, "
            "provide only dimensions for segmented output, or provide both granularity and "
            "dimensions for panel output."
        )
    )
    async def observe(
        session_id: Annotated[
            str,
            Field(description="Marivo analysis session ID that owns this intent call."),
        ],
        metric: Annotated[
            str,
            Field(
                min_length=1,
                description="Semantic metric identifier, e.g. 'total_query_count'.",
            ),
        ],
        time_scope: Annotated[
            McpTimeScopeValidated,
            Field(description="AOI TimeScope for the observed metric data slice."),
        ],
        granularity: Annotated[
            Literal["hour", "day", "week", "month", "quarter", "year"] | None,
            Field(
                description=(
                    "Time-series observe selector. Provide this without dimensions for time_series mode; "
                    "provide with dimensions for panel mode. Omit both granularity and "
                    "dimensions for scalar observe."
                )
            ),
        ] = None,
        dimensions: Annotated[
            list[str] | None,
            Field(
                min_length=1,
                description=(
                    "Segmented observe selector. Provide a non-empty dimension list without "
                    "granularity for segmented mode; provide with granularity for panel mode. "
                    "Omit both dimensions and granularity for scalar observe."
                ),
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
        reasoning: _ReasoningField = None,
    ) -> dict[str, Any]:
        request = to_aoi_observe_request(
            metric=metric,
            time_scope=time_scope,
            granularity=granularity,
            dimensions=dimensions,
            filter_expression=filter_expression,
        )
        return await call_runtime(
            runtime.observe, session_id=session_id, request=request, reasoning=reasoning
        )


def register_compare(server: Any, runtime: Any) -> None:
    @server.tool(  # type: ignore
        description=(
            "Compare two committed observe artifacts from the same session. scalar, segmented, "
            "time_series, and panel observations are valid for compare_type='normal'; "
            "holiday_aligned, weekday_aligned, and holiday_and_weekday_aligned require "
            "time-axis observe inputs."
        )
    )
    async def compare(
        session_id: Annotated[
            str,
            Field(description="Marivo analysis session ID that owns this intent call."),
        ],
        current_artifact_id: CompareObserveArtifactId,
        baseline_artifact_id: CompareObserveArtifactId,
        compare_type: Annotated[
            Literal[
                "normal",
                "holiday_aligned",
                "weekday_aligned",
                "holiday_and_weekday_aligned",
            ],
            Field(
                description=(
                    "normal compares scalar, segmented, time_series, or panel observations. "
                    "Calendar-aligned values are valid for time_series or panel observations."
                )
            ),
        ] = "normal",
        reasoning: _ReasoningField = None,
    ) -> dict[str, Any]:
        request = to_aoi_compare_request(
            current_artifact_id=current_artifact_id,
            baseline_artifact_id=baseline_artifact_id,
            compare_type=compare_type,
        )
        return await call_runtime(
            runtime.compare, session_id=session_id, request=request, reasoning=reasoning
        )


def register_decompose(server: Any, runtime: Any) -> None:
    @server.tool(  # type: ignore
        description=(
            "Decompose the delta from a committed compare artifact by one dimension. "
            "Inputs are string artifact IDs from the same session; the method is fixed "
            "to delta_share. Returns an attribution_frame artifact with ranked_contributions "
            "payload."
        )
    )
    async def decompose(
        session_id: Annotated[
            str,
            Field(description="Marivo analysis session ID that owns this intent call."),
        ],
        compare_artifact_id: Annotated[
            str,
            Field(
                min_length=1,
                description=(
                    "Committed compare artifact ID from this session, e.g. 'art_compare_1'."
                ),
            ),
        ],
        dimension: Annotated[
            str,
            Field(
                min_length=1,
                description="Dimension name to decompose by, e.g. 'cluster' or 'department'.",
            ),
        ],
        limit: Annotated[
            int | None,
            Field(
                ge=1,
                description="Maximum top dimension values to return; omit to use service default.",
            ),
        ] = None,
        reasoning: _ReasoningField = None,
    ) -> dict[str, Any]:
        request = to_aoi_decompose_request(
            compare_artifact_id=compare_artifact_id,
            dimension=dimension,
            limit=limit,
        )
        return await call_runtime(
            runtime.decompose, session_id=session_id, request=request, reasoning=reasoning
        )


def register_detect(server: Any, runtime: Any) -> None:
    @server.tool(  # type: ignore
        description=(
            "Detect anomaly candidates for one semantic metric over an AOI time_scope at a "
            "required time granularity."
        )
    )
    async def detect(
        session_id: Annotated[
            str,
            Field(description="Marivo analysis session ID that owns this intent call."),
        ],
        metric: Annotated[
            str,
            Field(
                min_length=1,
                description="Semantic metric identifier, e.g. 'total_query_count'.",
            ),
        ],
        time_scope: Annotated[
            McpTimeScope,
            Field(description="AOI TimeScope for the metric data slice."),
        ],
        granularity: Annotated[
            Literal["hour", "day", "week", "month", "quarter", "year"],
            Field(description="Required AOI time bucket granularity for anomaly scanning."),
        ],
        strategy: Annotated[
            Literal["point_anomaly", "period_shift"],
            Field(description="Detection strategy: point anomalies or period shifts."),
        ],
        filter_expression: Annotated[
            McpExpression | None,
            Field(
                description=(
                    "Optional AOI Expression filter object, e.g. "
                    "{'dialects': [{'dialect': 'ANSI_SQL', 'expression': \"region = 'US'\"}]}."
                )
            ),
        ] = None,
        dimension: Annotated[
            str | None,
            Field(
                description=(
                    "Optional single dimension that splits the scan into independent series; "
                    "omit to scan the overall metric series."
                ),
            ),
        ] = None,
        sensitivity: Annotated[
            Literal["conservative", "balanced", "aggressive"],
            Field(description="Detection sensitivity preset. Defaults to aggressive."),
        ] = "aggressive",
        limit: Annotated[
            int | None,
            Field(
                ge=1,
                description="Maximum anomaly candidates to return; omit to use service default.",
            ),
        ] = None,
        reasoning: _ReasoningField = None,
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
        return await call_runtime(
            runtime.detect, session_id=session_id, request=request, reasoning=reasoning
        )


def register_correlate(server: Any, runtime: Any) -> None:
    @server.tool(  # type: ignore
        description=(
            "Correlate two committed observe(time_series) artifacts from the same session. "
            "Use artifact IDs returned by observe calls with granularity; scalar or segmented "
            "observe artifacts are invalid."
        )
    )
    async def correlate(
        session_id: Annotated[
            str,
            Field(description="Marivo analysis session ID that owns this intent call."),
        ],
        left_artifact_id: TimeSeriesObserveArtifactId,
        right_artifact_id: TimeSeriesObserveArtifactId,
        method: Annotated[
            Literal["pearson", "spearman"] | None,
            Field(description="Correlation method; omit to use the service default."),
        ] = None,
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
        reasoning: _ReasoningField = None,
    ) -> dict[str, Any]:
        request = to_aoi_correlate_request(
            left_artifact_id=left_artifact_id,
            right_artifact_id=right_artifact_id,
            method=method,
            min_pairs=min_pairs,
        )
        return await call_runtime(
            runtime.correlate, session_id=session_id, request=request, reasoning=reasoning
        )


def register_forecast(server: Any, runtime: Any) -> None:
    @server.tool(  # type: ignore
        description=(
            "Forecast from one committed observe(time_series) artifact from the same session. "
            "source_artifact_id must come from observe(granularity=...), not from datasource, "
            "scalar observe, segmented observe, or forecast output."
        )
    )
    async def forecast(
        session_id: Annotated[
            str,
            Field(description="Marivo analysis session ID that owns this intent call."),
        ],
        source_artifact_id: TimeSeriesObserveArtifactId,
        horizon: Annotated[
            int,
            Field(
                ge=1,
                description=(
                    "Number of future buckets to forecast, in units of the source "
                    "observe(time_series) granularity."
                ),
            ),
        ],
        reasoning: _ReasoningField = None,
    ) -> dict[str, Any]:
        request = to_aoi_forecast_request(
            source_artifact_id=source_artifact_id,
            horizon=horizon,
        )
        return await call_runtime(
            runtime.forecast, session_id=session_id, request=request, reasoning=reasoning
        )


def register_attribute(server: Any, runtime: Any) -> None:
    @server.tool(  # type: ignore
        description=(
            "Attribute a known current-vs-baseline metric change. Decompose results are "
            "attribution_frame artifacts with ranked_contributions payloads."
        )
    )
    async def attribute(
        session_id: Annotated[
            str,
            Field(description="Marivo analysis session ID that owns this intent call."),
        ],
        metric: Annotated[
            str,
            Field(
                min_length=1,
                description="Semantic metric identifier, e.g. 'total_query_count'.",
            ),
        ],
        current: Annotated[
            McpAoiSliceRef,
            Field(description="Current AOI slice: time_scope plus optional filter."),
        ],
        baseline: Annotated[
            McpAoiSliceRef,
            Field(description="Baseline AOI slice: time_scope plus optional filter."),
        ],
        dimensions: Annotated[
            list[str],
            Field(
                min_length=1,
                description=(
                    "Attribution dimensions used to explain the known current-vs-baseline "
                    "change. Each dimension produces an independent attribution_frame "
                    "artifact with ranked_contributions payload."
                ),
            ),
        ],
        decomposition_method: Annotated[
            Literal["delta_share"],
            Field(
                description=(
                    "AOI decomposition method. The method is auto-derived from the metric's "
                    "decomposition_semantics.type at runtime; the parameter value is fixed to "
                    "delta_share for wire compatibility."
                )
            ),
        ] = "delta_share",
        decomposition_limit: Annotated[
            int,
            Field(
                ge=1,
                description="Maximum driver rows returned per attribution dimension. Defaults to 5.",
            ),
        ] = 5,
        reasoning: _ReasoningField = None,
    ) -> dict[str, Any]:
        request = to_aoi_attribute_request(
            metric=metric,
            current=current,
            baseline=baseline,
            dimensions=dimensions,
            decomposition_method=decomposition_method,
            decomposition_limit=decomposition_limit,
        )
        return await call_runtime(
            runtime.attribute, session_id=session_id, request=request, reasoning=reasoning
        )


def register_diagnose(server: Any, runtime: Any) -> None:
    @server.tool(  # type: ignore
        description=(
            "Run bounded auto-detect anomaly diagnosis. The tool first detects anomalous "
            "candidates in time_scope at granularity, then follows up with compare and "
            "decompose across the requested dimensions. Decompose results are "
            "attribution_frame artifacts with ranked_contributions payloads."
        )
    )
    async def diagnose(
        session_id: Annotated[
            str,
            Field(description="Marivo analysis session ID that owns this intent call."),
        ],
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
        include_details: Annotated[
            bool,
            Field(
                description=(
                    "Return full embedded AOI artifacts and driver rows. Defaults to false so "
                    "agent-facing diagnose calls return a compact summary with refs for lazy "
                    "detail loading."
                )
            ),
        ] = False,
        reasoning: _ReasoningField = None,
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
        response = await call_runtime(
            runtime.diagnose, session_id=session_id, request=request, reasoning=reasoning
        )
        if not include_details and isinstance(response.get("data"), dict):
            response = {**response, "data": compact_diagnose_envelope(response["data"])}
        return response


def register_test_intent(server: Any, runtime: Any) -> None:
    @server.tool(  # type: ignore
        description=(
            "Run a fixed-family numeric hypothesis test over current and baseline AOI slices. "
            "MCP fixes kind='numeric' and hypothesis.family='two_sample_mean'; do not pass "
            "kind, method, family, alpha, or label. grain is required, uses AOI "
            "TimeGranularity values, and defines the statistical sample unit."
        )
    )
    async def test_intent(
        session_id: Annotated[
            str,
            Field(description="Marivo analysis session ID that owns this intent call."),
        ],
        metric: Annotated[
            str,
            Field(
                min_length=1,
                description="Semantic metric identifier, e.g. 'total_query_count'.",
            ),
        ],
        current: Annotated[
            McpAoiSliceRef,
            Field(description="Current AOI slice: time_scope plus optional filter."),
        ],
        baseline: Annotated[
            McpAoiSliceRef,
            Field(description="Baseline AOI slice: time_scope plus optional filter."),
        ],
        grain: TimeGranularity,
        hypothesis: Annotated[
            McpTestHypothesis,
            Field(
                description=(
                    "Structured hypothesis object with only alternative and significance; "
                    "family is fixed internally to two_sample_mean."
                ),
            ),
        ],
        reasoning: _ReasoningField = None,
    ) -> dict[str, Any]:
        request = to_aoi_test_request(
            metric=metric,
            current=current,
            baseline=baseline,
            grain=grain,
            hypothesis=hypothesis,
        )
        return await call_runtime(
            runtime.test, session_id=session_id, request=request, reasoning=reasoning
        )


def register_validate(server: Any, runtime: Any) -> None:
    @server.tool(  # type: ignore
        description=(
            "Run derived validation for current and baseline AOI slices using the fixed "
            "two_sample_mean hypothesis family. MCP fills missing hypothesis defaults and "
            "does not expose method, family, alpha, or label. grain is required, uses AOI "
            "TimeGranularity values, and defines the statistical sample unit for the "
            "wrapped test."
        )
    )
    async def validate(
        session_id: Annotated[
            str,
            Field(description="Marivo analysis session ID that owns this intent call."),
        ],
        metric: Annotated[
            str,
            Field(
                min_length=1,
                description="Semantic metric identifier, e.g. 'total_query_count'.",
            ),
        ],
        current: Annotated[
            McpAoiSliceRef,
            Field(description="Current AOI slice: time_scope plus optional filter."),
        ],
        baseline: Annotated[
            McpAoiSliceRef,
            Field(description="Baseline AOI slice: time_scope plus optional filter."),
        ],
        grain: TimeGranularity,
        hypothesis: Annotated[
            McpValidateHypothesis | None,
            Field(
                description=(
                    "Optional structured hypothesis with alternative and significance only; "
                    "family defaults internally to two_sample_mean."
                ),
            ),
        ] = None,
        reasoning: _ReasoningField = None,
    ) -> dict[str, Any]:
        request = to_aoi_validate_request(
            metric=metric,
            current=current,
            baseline=baseline,
            grain=grain,
            hypothesis=hypothesis,
        )
        return await call_runtime(
            runtime.validate, session_id=session_id, request=request, reasoning=reasoning
        )
