from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.analysis_core.ir import AnalysisStepIR


@dataclass(slots=True)
class CompiledQuery:
    """Minimal compile artifact used by the phase-1 refactor."""

    sql: str
    params: list[Any] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def build_comparison_query(
    metric_name: str,
    table_name: str,
    metric_sql: str,
    dimensions: list[str],
    date_column: str = "event_date",
    order: str = "ASC",
    limit: int = 3,
) -> str:
    """Build a current-vs-baseline comparison query from semantic metric inputs."""

    del metric_name
    dim_cols = ", ".join(dimensions)
    return f"""
        WITH periodized AS (
            SELECT
                CASE
                    WHEN {date_column} BETWEEN ? AND ? THEN 'current'
                    WHEN {date_column} BETWEEN ? AND ? THEN 'baseline'
                END AS period,
                {dim_cols},
                *
            FROM {table_name}
            WHERE {date_column} BETWEEN ? AND ?
        ),
        by_period AS (
            SELECT
                period,
                {dim_cols},
                {metric_sql} AS metric_value,
                COUNT(*) AS session_count
            FROM periodized
            GROUP BY period, {dim_cols}
        ),
        pivoted AS (
            SELECT
                {dim_cols},
                MAX(CASE WHEN period = 'current' THEN metric_value END) AS current_value,
                MAX(CASE WHEN period = 'baseline' THEN metric_value END) AS baseline_value,
                MAX(CASE WHEN period = 'current' THEN session_count END) AS current_sessions,
                MAX(CASE WHEN period = 'baseline' THEN session_count END) AS baseline_sessions
            FROM by_period
            GROUP BY {dim_cols}
        )
        SELECT
            {dim_cols},
            ROUND(current_value, 2) AS current_value,
            ROUND(baseline_value, 2) AS baseline_value,
            ROUND(((current_value - baseline_value) / baseline_value) * 100, 2) AS delta_pct,
            current_sessions,
            baseline_sessions
        FROM pivoted
        ORDER BY delta_pct {order}
        LIMIT {limit}
    """


def compile_step(
    step: AnalysisStepIR,
    *,
    engine_type: str,
    semantic_context: dict[str, Any] | None = None,
) -> CompiledQuery:
    """Compile a step IR into an engine-agnostic query artifact."""

    semantic_context = semantic_context or {}
    params = dict(step.params)
    metadata = {"engine_type": engine_type, "step_type": step.step_type}

    if step.step_type == "sample_rows":
        table_name = _require_param(step, "table_name")
        limit = int(params.get("limit", 10))
        return CompiledQuery(
            sql=f"SELECT * FROM {table_name} LIMIT {limit}",
            metadata={**metadata, "table_name": table_name, "limit": limit},
        )

    if step.step_type == "profile_table_row_count":
        table_name = _require_param(step, "table_name")
        return CompiledQuery(
            sql=f"SELECT COUNT(*) AS row_count FROM {table_name}",
            metadata={**metadata, "table_name": table_name},
        )

    if step.step_type == "profile_table_columns":
        short_name = str(params.get("short_name") or _require_param(step, "table_name").split(".")[-1])
        return CompiledQuery(
            sql=f"SELECT column_name FROM information_schema.columns WHERE table_name = '{short_name}'",
            metadata={**metadata, "short_name": short_name},
        )

    if step.step_type == "profile_table_column_profile":
        table_name = _require_param(step, "table_name")
        column_name = _require_param(step, "column_name")
        return CompiledQuery(
            sql=f"""
                SELECT
                    COUNT(*) AS total,
                    COUNT({column_name}) AS non_null,
                    COUNT(DISTINCT {column_name}) AS distinct_count
                FROM {table_name}
            """,
            metadata={**metadata, "table_name": table_name, "column_name": column_name},
        )

    if step.step_type == "compare_metric":
        table_name = _require_param(step, "table_name")
        metric_name = _require_param(step, "metric_name")
        metric_sql = semantic_context.get("metric_sql")
        dimensions = semantic_context.get("dimensions")
        if metric_sql is None or dimensions is None:
            raise ValueError("compare_metric compilation requires semantic_context with 'metric_sql' and 'dimensions'")
        date_column = str(params.get("date_column", "event_date"))
        limit = int(params.get("limit", 3))
        sql = build_comparison_query(
            metric_name=metric_name,
            table_name=table_name,
            metric_sql=str(metric_sql),
            dimensions=list(dimensions),
            date_column=date_column,
            order="ASC",
            limit=limit,
        )
        return CompiledQuery(
            sql=sql,
            params=list(semantic_context.get("period_params", [])),
            metadata={
                **metadata,
                "table_name": table_name,
                "metric_name": metric_name,
                "dimensions": list(dimensions),
            },
        )

    if step.step_type == "compare_watch_time_top_slices":
        table_name = str(params.get("table_name", "analytics.watch_events"))
        limit = int(params.get("limit", 3))
        return CompiledQuery(
            sql=f"""
                WITH periodized AS (
                    SELECT
                        CASE
                            WHEN event_date BETWEEN ? AND ? THEN 'current'
                            WHEN event_date BETWEEN ? AND ? THEN 'baseline'
                        END AS period,
                        platform,
                        app_version,
                        network_type,
                        content_type,
                        play_duration_seconds
                    FROM {table_name}
                    WHERE event_date BETWEEN ? AND ?
                ),
                aggregated AS (
                    SELECT
                        platform,
                        app_version,
                        network_type,
                        content_type,
                        AVG(play_duration_seconds) FILTER (WHERE period = 'current') AS current_watch_time,
                        AVG(play_duration_seconds) FILTER (WHERE period = 'baseline') AS baseline_watch_time,
                        COUNT(*) FILTER (WHERE period = 'current') AS current_sessions,
                        COUNT(*) FILTER (WHERE period = 'baseline') AS baseline_sessions
                    FROM periodized
                    GROUP BY 1, 2, 3, 4
                )
                SELECT
                    platform,
                    app_version,
                    network_type,
                    content_type,
                    ROUND(current_watch_time, 2) AS current_watch_time,
                    ROUND(baseline_watch_time, 2) AS baseline_watch_time,
                    ROUND(((current_watch_time - baseline_watch_time) / baseline_watch_time) * 100, 2) AS delta_pct,
                    current_sessions,
                    baseline_sessions
                FROM aggregated
                ORDER BY delta_pct ASC
                LIMIT {limit}
            """,
            params=_require_period_params(step, semantic_context),
            metadata={**metadata, "table_name": table_name, "limit": limit},
        )

    if step.step_type == "compare_watch_time_overall":
        table_name = str(params.get("table_name", "analytics.watch_events"))
        return CompiledQuery(
            sql=f"""
                WITH periodized AS (
                    SELECT
                        CASE
                            WHEN event_date BETWEEN ? AND ? THEN 'current'
                            WHEN event_date BETWEEN ? AND ? THEN 'baseline'
                        END AS period,
                        play_duration_seconds
                    FROM {table_name}
                    WHERE event_date BETWEEN ? AND ?
                )
                SELECT
                    ROUND(AVG(play_duration_seconds) FILTER (WHERE period = 'current'), 2) AS current_watch_time,
                    ROUND(AVG(play_duration_seconds) FILTER (WHERE period = 'baseline'), 2) AS baseline_watch_time,
                    ROUND(
                        (
                            (AVG(play_duration_seconds) FILTER (WHERE period = 'current'))
                            - (AVG(play_duration_seconds) FILTER (WHERE period = 'baseline'))
                        ) / (AVG(play_duration_seconds) FILTER (WHERE period = 'baseline')) * 100,
                        2
                    ) AS delta_pct
                FROM periodized
            """,
            params=_require_period_params(step, semantic_context),
            metadata={**metadata, "table_name": table_name},
        )

    if step.step_type == "analyze_qoe":
        table_name = str(params.get("table_name", "analytics.player_qoe"))
        limit = int(params.get("limit", 3))
        return CompiledQuery(
            sql=f"""
                WITH periodized AS (
                    SELECT
                        CASE
                            WHEN event_date BETWEEN ? AND ? THEN 'current'
                            WHEN event_date BETWEEN ? AND ? THEN 'baseline'
                        END AS period,
                        platform,
                        app_version,
                        network_type,
                        content_type,
                        first_frame_time_ms
                    FROM {table_name}
                    WHERE event_date BETWEEN ? AND ?
                ),
                aggregated AS (
                    SELECT
                        platform,
                        app_version,
                        network_type,
                        content_type,
                        AVG(first_frame_time_ms) FILTER (WHERE period = 'current') AS current_first_frame_ms,
                        AVG(first_frame_time_ms) FILTER (WHERE period = 'baseline') AS baseline_first_frame_ms,
                        COUNT(*) FILTER (WHERE period = 'current') AS current_sessions,
                        COUNT(*) FILTER (WHERE period = 'baseline') AS baseline_sessions
                    FROM periodized
                    GROUP BY 1, 2, 3, 4
                )
                SELECT
                    platform,
                    app_version,
                    network_type,
                    content_type,
                    ROUND(current_first_frame_ms, 2) AS current_first_frame_ms,
                    ROUND(baseline_first_frame_ms, 2) AS baseline_first_frame_ms,
                    ROUND(((current_first_frame_ms - baseline_first_frame_ms) / baseline_first_frame_ms) * 100, 2) AS delta_pct,
                    ROUND(current_first_frame_ms - baseline_first_frame_ms, 2) AS delta_ms,
                    current_sessions,
                    baseline_sessions
                FROM aggregated
                ORDER BY delta_pct DESC
                LIMIT {limit}
            """,
            params=_require_period_params(step, semantic_context),
            metadata={**metadata, "table_name": table_name, "limit": limit},
        )

    if step.step_type == "analyze_ads":
        table_name = str(params.get("table_name", "analytics.ad_events"))
        limit = int(params.get("limit", 3))
        return CompiledQuery(
            sql=f"""
                WITH periodized AS (
                    SELECT
                        CASE
                            WHEN event_date BETWEEN ? AND ? THEN 'current'
                            WHEN event_date BETWEEN ? AND ? THEN 'baseline'
                        END AS period,
                        platform,
                        app_version,
                        network_type,
                        content_type,
                        preroll_timeout
                    FROM {table_name}
                    WHERE event_date BETWEEN ? AND ?
                ),
                aggregated AS (
                    SELECT
                        platform,
                        app_version,
                        network_type,
                        content_type,
                        AVG(preroll_timeout::DOUBLE) FILTER (WHERE period = 'current') AS current_timeout_rate,
                        AVG(preroll_timeout::DOUBLE) FILTER (WHERE period = 'baseline') AS baseline_timeout_rate,
                        COUNT(*) FILTER (WHERE period = 'current') AS current_sessions,
                        COUNT(*) FILTER (WHERE period = 'baseline') AS baseline_sessions
                    FROM periodized
                    GROUP BY 1, 2, 3, 4
                )
                SELECT
                    platform,
                    app_version,
                    network_type,
                    content_type,
                    ROUND(current_timeout_rate, 4) AS current_timeout_rate,
                    ROUND(baseline_timeout_rate, 4) AS baseline_timeout_rate,
                    ROUND(current_timeout_rate - baseline_timeout_rate, 4) AS delta_rate,
                    current_sessions,
                    baseline_sessions
                FROM aggregated
                ORDER BY delta_rate DESC
                LIMIT {limit}
            """,
            params=_require_period_params(step, semantic_context),
            metadata={**metadata, "table_name": table_name, "limit": limit},
        )

    if step.step_type == "analyze_recommendation":
        table_name = str(params.get("table_name", "analytics.recommendation_events"))
        limit = int(params.get("limit", 3))
        return CompiledQuery(
            sql=f"""
                WITH periodized AS (
                    SELECT
                        CASE
                            WHEN event_date BETWEEN ? AND ? THEN 'current'
                            WHEN event_date BETWEEN ? AND ? THEN 'baseline'
                        END AS period,
                        platform,
                        app_version,
                        network_type,
                        content_type,
                        impressions,
                        clicks
                    FROM {table_name}
                    WHERE event_date BETWEEN ? AND ?
                ),
                aggregated AS (
                    SELECT
                        platform,
                        app_version,
                        network_type,
                        content_type,
                        SUM(clicks) FILTER (WHERE period = 'current')::DOUBLE / SUM(impressions) FILTER (WHERE period = 'current') AS current_ctr,
                        SUM(clicks) FILTER (WHERE period = 'baseline')::DOUBLE / SUM(impressions) FILTER (WHERE period = 'baseline') AS baseline_ctr,
                        COUNT(*) FILTER (WHERE period = 'current') AS current_sessions,
                        COUNT(*) FILTER (WHERE period = 'baseline') AS baseline_sessions
                    FROM periodized
                    GROUP BY 1, 2, 3, 4
                )
                SELECT
                    platform,
                    app_version,
                    network_type,
                    content_type,
                    ROUND(current_ctr, 4) AS current_ctr,
                    ROUND(baseline_ctr, 4) AS baseline_ctr,
                    ROUND(((current_ctr - baseline_ctr) / baseline_ctr) * 100, 2) AS delta_ctr_pct,
                    current_sessions,
                    baseline_sessions
                FROM aggregated
                ORDER BY delta_ctr_pct DESC
                LIMIT {limit}
            """,
            params=_require_period_params(step, semantic_context),
            metadata={**metadata, "table_name": table_name, "limit": limit},
        )

    raise ValueError(f"Unsupported compilation step type: {step.step_type}")


def _require_param(step: AnalysisStepIR, name: str) -> str:
    value = step.params.get(name)
    if value in (None, ""):
        raise ValueError(f"{step.step_type} requires '{name}' param")
    return str(value)


def _require_period_params(step: AnalysisStepIR, semantic_context: dict[str, Any]) -> list[Any]:
    period_params = semantic_context.get("period_params")
    if period_params is None:
        raise ValueError(f"{step.step_type} compilation requires semantic_context with 'period_params'")
    return list(period_params)
