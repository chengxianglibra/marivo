from __future__ import annotations

import re
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
    limit: int = 10,
    filter_expr: str | None = None,
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

    filter_clause = f" AND {filter_expr}" if filter_expr else ""

    return f"""
        WITH periodized AS (
            SELECT
                CASE
                    WHEN {date_column} BETWEEN ? AND ? THEN 'current'
                    WHEN {date_column} BETWEEN ? AND ? THEN 'baseline'
                END AS period,
                *
            FROM {table_name}
            WHERE {date_column} BETWEEN ? AND ?{filter_clause}
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
            ROUND(((current_value - baseline_value) * 1.0 / NULLIF(baseline_value, 0)) * 100, 2) AS delta_pct,
            current_sessions,
            baseline_sessions
        FROM pivoted
        ORDER BY delta_pct {order}
        LIMIT {limit}
    """


def _extract_agg_alias(expr: str) -> str:
    """Extract alias from e.g. 'count(*) as query_count' → 'query_count'.

    Raises ValueError if no alias found (required for compare_period mode).
    """
    match = re.search(r"\bAS\s+(\w+)\s*$", expr, re.IGNORECASE)
    if match:
        return match.group(1)
    raise ValueError(
        f"aggregate_query with compare_period=True requires aliases in all aggregate "
        f"expressions. Missing alias in: {expr!r}"
    )


def build_aggregate_comparison_query(
    table_name: str,
    select_exprs: list[str],
    group_by: list[str],
    date_column: str,
    order_by: str | None = None,
    limit: int = 100,
    filter_expr: str | None = None,
) -> str:
    """Build a current-vs-baseline comparison query for ad-hoc aggregate steps.

    Mirrors ``build_comparison_query`` but driven by user-supplied ``select``
    and ``group_by`` instead of a registered semantic metric.

    SQL ``?`` placeholder order (6 params, same as build_comparison_query):
        current_start, current_end,   (CASE WHEN … THEN 'current')
        baseline_start, baseline_end, (CASE WHEN … THEN 'baseline')
        baseline_start, current_end   (outer WHERE range)
    """
    # Identify aggregate expressions: those NOT in group_by (or containing an AS alias)
    agg_exprs: list[tuple[str, str]] = []  # (expr, alias)
    for expr in select_exprs:
        stripped = expr.strip()
        if stripped in group_by:
            continue  # plain dimension column — skip
        alias = _extract_agg_alias(stripped)
        agg_exprs.append((stripped, alias))

    if not agg_exprs:
        raise ValueError(
            "compare_period requires at least one aggregate expression with an alias in 'select'"
        )

    group_by_cols = ", ".join(group_by)
    agg_select = ", ".join(expr for expr, _ in agg_exprs)
    filter_clause = f" AND {filter_expr}" if filter_expr else ""

    pivot_parts: list[str] = []
    for _, alias in agg_exprs:
        pivot_parts.append(
            f"MAX(CASE WHEN _period = 'current' THEN {alias} END) AS {alias}_current"
        )
        pivot_parts.append(
            f"MAX(CASE WHEN _period = 'baseline' THEN {alias} END) AS {alias}_baseline"
        )
    pivot_select = ",\n            ".join(pivot_parts)

    final_parts: list[str] = []
    for _, alias in agg_exprs:
        final_parts.append(f"{alias}_current")
        final_parts.append(f"{alias}_baseline")
        final_parts.append(
            f"ROUND(({alias}_current - {alias}_baseline) * 1.0 / NULLIF({alias}_baseline, 0) * 100, 2)"
            f" AS {alias}_delta_pct"
        )
    final_select_agg = ",\n            ".join(final_parts)

    first_alias = agg_exprs[0][1]
    effective_order_by = order_by or f"{first_alias}_delta_pct DESC"

    return f"""
        WITH periodized AS (
            SELECT
                CASE
                    WHEN {date_column} BETWEEN ? AND ? THEN 'current'
                    WHEN {date_column} BETWEEN ? AND ? THEN 'baseline'
                END AS _period,
                *
            FROM {table_name}
            WHERE {date_column} BETWEEN ? AND ?{filter_clause}
        ),
        by_period AS (
            SELECT _period, {group_by_cols}, {agg_select}
            FROM periodized
            GROUP BY _period, {group_by_cols}
        ),
        pivoted AS (
            SELECT {group_by_cols},
            {pivot_select}
            FROM by_period
            GROUP BY {group_by_cols}
        )
        SELECT {group_by_cols},
            {final_select_agg}
        FROM pivoted
        ORDER BY {effective_order_by}
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

        # Column selection
        columns = params.get("columns")
        columns_clause = ", ".join(columns) if columns else "*"

        # WHERE clause construction
        where_parts: list[str] = []
        if params.get("filter"):
            where_parts.append(str(params["filter"]))
        date_column = params.get("date_column")
        date_value = params.get("date_value")
        if date_column and date_value:
            where_parts.append(f"{date_column} = '{date_value}'")
        where_clause = f" WHERE {' AND '.join(where_parts)}" if where_parts else ""

        return CompiledQuery(
            sql=f"SELECT {columns_clause} FROM {table_name}{where_clause} LIMIT {limit}",
            metadata={**metadata, "table_name": table_name, "limit": limit},
        )

    if step.step_type == "profile_table_row_count":
        table_name = _require_param(step, "table_name")
        return CompiledQuery(
            sql=f"SELECT COUNT(*) AS row_count FROM {table_name}",
            metadata={**metadata, "table_name": table_name},
        )

    if step.step_type == "profile_table_columns":
        full_table = _require_param(step, "table_name")
        short_name = str(params.get("short_name") or full_table.split(".")[-1])
        parts = full_table.split(".")
        where_clauses = [f"table_name = '{short_name}'"]
        if len(parts) >= 3:
            where_clauses.append(f"table_catalog = '{parts[0]}'")
            where_clauses.append(f"table_schema = '{parts[1]}'")
        elif len(parts) == 2:
            where_clauses.append(f"table_schema = '{parts[0]}'")
        where_sql = " AND ".join(where_clauses)
        return CompiledQuery(
            sql=f"SELECT column_name FROM information_schema.columns WHERE {where_sql}",
            metadata={**metadata, "short_name": short_name},
        )

    if step.step_type == "profile_table_column_profile":
        table_name = _require_param(step, "table_name")
        column_name = _require_param(step, "column_name")
        date_column = params.get("date_column")
        date_value = params.get("date_value")
        where_clause = ""
        if date_column and date_value:
            where_clause = f" WHERE {date_column} = '{date_value}'"
        return CompiledQuery(
            sql=f"""
                SELECT
                    COUNT(*) AS total,
                    COUNT({column_name}) AS non_null,
                    COUNT(DISTINCT {column_name}) AS distinct_count
                FROM {table_name}{where_clause}
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
        limit = int(params.get("limit", 10))
        order = str(params.get("order", "ASC")).upper()
        if order not in ("ASC", "DESC"):
            raise ValueError(f"Invalid order '{order}'; must be ASC or DESC")
        filter_expr = params.get("filter") or None
        sql = build_comparison_query(
            metric_name=metric_name,
            table_name=table_name,
            metric_sql=str(metric_sql),
            dimensions=list(dimensions),
            date_column=date_column,
            order=order,
            limit=limit,
            filter_expr=str(filter_expr) if filter_expr else None,
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

    if step.step_type == "aggregate_query":
        table_name = _require_param(step, "table_name")
        select_exprs = params.get("select")
        if not select_exprs or not isinstance(select_exprs, list):
            raise ValueError("aggregate_query requires 'select' param (list of expressions)")
        group_by = params.get("group_by")
        if not group_by or not isinstance(group_by, list):
            raise ValueError("aggregate_query requires 'group_by' param (list of columns)")
        where = params.get("where")
        order_by = params.get("order_by")
        limit = int(params.get("limit", 100))

        if params.get("compare_period"):
            date_column = str(params.get("date_column", "event_date"))
            sql = build_aggregate_comparison_query(
                table_name=table_name,
                select_exprs=list(select_exprs),
                group_by=list(group_by),
                date_column=date_column,
                order_by=order_by,
                limit=limit,
                filter_expr=str(where) if where else None,
            )
            return CompiledQuery(
                sql=sql,
                params=list(semantic_context.get("period_params", [])),
                metadata={**metadata, "table_name": table_name, "limit": limit, "compare_period": True},
            )

        select_clause = ", ".join(select_exprs)
        where_clause = f" WHERE {where}" if where else ""
        group_clause = f" GROUP BY {', '.join(group_by)}"
        order_clause = f" ORDER BY {order_by}" if order_by else ""

        sql = f"SELECT {select_clause} FROM {table_name}{where_clause}{group_clause}{order_clause} LIMIT {limit}"
        return CompiledQuery(
            sql=sql,
            metadata={**metadata, "table_name": table_name, "limit": limit},
        )

    raise ValueError(f"Unsupported compilation step type: {step.step_type}")


def _require_param(step: AnalysisStepIR, name: str) -> str:
    value = step.params.get(name)
    if value in (None, ""):
        raise ValueError(f"{step.step_type} requires '{name}' param")
    return str(value)


