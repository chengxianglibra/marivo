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
    """Build a current-vs-baseline comparison query from semantic metric inputs.

    When *dimensions* is empty, an aggregate-only comparison is produced
    (no GROUP BY, single row with overall current vs baseline).
    """

    del metric_name

    if dimensions:
        dim_cols = ", ".join(dimensions)
        group_by_period = f"GROUP BY period, {dim_cols}"
        group_by_dims = f"GROUP BY {dim_cols}"
        select_dims = f"{dim_cols},"
    else:
        group_by_period = "GROUP BY period"
        group_by_dims = ""
        select_dims = ""

    return f"""
        WITH periodized AS (
            SELECT
                CASE
                    WHEN {date_column} BETWEEN ? AND ? THEN 'current'
                    WHEN {date_column} BETWEEN ? AND ? THEN 'baseline'
                END AS period,
                *
            FROM {table_name}
            WHERE {date_column} BETWEEN ? AND ?
        ),
        by_period AS (
            SELECT
                period,
                {select_dims}
                {metric_sql} AS metric_value,
                COUNT(*) AS session_count
            FROM periodized
            {group_by_period}
        ),
        pivoted AS (
            SELECT
                {select_dims}
                MAX(CASE WHEN period = 'current' THEN metric_value END) AS current_value,
                MAX(CASE WHEN period = 'baseline' THEN metric_value END) AS baseline_value,
                MAX(CASE WHEN period = 'current' THEN session_count END) AS current_sessions,
                MAX(CASE WHEN period = 'baseline' THEN session_count END) AS baseline_sessions
            FROM by_period
            {group_by_dims}
        )
        SELECT
            {select_dims}
            ROUND(current_value, 2) AS current_value,
            ROUND(baseline_value, 2) AS baseline_value,
            ROUND(((current_value - baseline_value) / NULLIF(baseline_value, 0)) * 100, 2) AS delta_pct,
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

    raise ValueError(f"Unsupported compilation step type: {step.step_type}")


def _require_param(step: AnalysisStepIR, name: str) -> str:
    value = step.params.get(name)
    if value in (None, ""):
        raise ValueError(f"{step.step_type} requires '{name}' param")
    return str(value)


