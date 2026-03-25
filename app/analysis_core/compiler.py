from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Mapping

from app.analysis_core.ir import AnalysisStepIR


@dataclass(slots=True)
class CompiledQuery:
    """Minimal compile artifact used by the phase-1 refactor."""

    sql: str
    params: list[Any] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class _ScopedQueryParts:
    cte_sql: str
    params: list[Any]


def _build_scoped_query_parts(
    table_name: str,
    scoped_query: Mapping[str, Any],
    *,
    include_period: bool,
) -> _ScopedQueryParts:
    analysis_time_expr = str(scoped_query.get("analysis_time_expr") or "").strip()
    if not analysis_time_expr:
        raise ValueError("scoped_query requires 'analysis_time_expr'")

    mode = str(scoped_query.get("mode") or "").strip() or "single_window"
    current = dict(scoped_query.get("current") or {})
    current_start = str(current.get("start") or "").strip()
    current_end = str(current.get("end") or "").strip()
    if not current_start or not current_end:
        raise ValueError("scoped_query requires current.start and current.end")
    current_start = _format_scoped_bound(scoped_query, current_start)
    current_end = _format_scoped_bound(scoped_query, current_end)

    current_predicate = f"{analysis_time_expr} >= ? AND {analysis_time_expr} < ?"
    filters: list[str] = [f"({current_predicate})"]
    params: list[Any] = []

    if include_period:
        select_prefix = ["'current' AS _period"] if mode == "single_window" else []
    else:
        select_prefix = []

    if mode == "compare":
        baseline = dict(scoped_query.get("baseline") or {})
        baseline_start = str(baseline.get("start") or "").strip()
        baseline_end = str(baseline.get("end") or "").strip()
        if not baseline_start or not baseline_end:
            raise ValueError("scoped_query compare mode requires baseline.start and baseline.end")
        baseline_start = _format_scoped_bound(scoped_query, baseline_start)
        baseline_end = _format_scoped_bound(scoped_query, baseline_end)
        baseline_predicate = f"{analysis_time_expr} >= ? AND {analysis_time_expr} < ?"
        if include_period:
            select_prefix = [
                "CASE",
                f"                    WHEN {current_predicate} THEN 'current'",
                f"                    WHEN {baseline_predicate} THEN 'baseline'",
                "                END AS _period",
            ]
            params.extend([current_start, current_end, baseline_start, baseline_end])
        filters = [f"(({current_predicate}) OR ({baseline_predicate}))"]
        params.extend([current_start, current_end, baseline_start, baseline_end])
    else:
        params.extend([current_start, current_end])

    filter_fields = (
        "partition_pruning_predicate",
        "session_constraints_filter",
        "session_raw_filter",
        "scope_constraints_filter",
        "scope_predicate_filter",
    )
    for field_name in filter_fields:
        value = str(scoped_query.get(field_name) or "").strip()
        if value:
            filters.append(f"({value})")

    if include_period and select_prefix:
        if len(select_prefix) == 1:
            select_sql = f"{select_prefix[0]},\n                *"
        else:
            select_sql = "\n".join(select_prefix) + ",\n                *"
    else:
        select_sql = "*"

    where_sql = "\n                AND ".join(filters)
    cte_sql = f"""
        scoped AS (
            SELECT
                {select_sql}
            FROM {table_name}
            WHERE {where_sql}
        )
    """
    return _ScopedQueryParts(cte_sql=cte_sql, params=params)


def _format_scoped_bound(scoped_query: Mapping[str, Any], value: str) -> str:
    analysis_time_kind = str(scoped_query.get("analysis_time_kind") or "").strip()
    if analysis_time_kind != "date_field":
        return value
    return _format_scoped_day_value(
        value,
        str(scoped_query.get("analysis_time_format") or "").strip() or None,
    )


def _format_scoped_day_value(value: str, date_format: str | None) -> str:
    if date_format == "yyyymmdd" and re.fullmatch(r"\d{8}", value):
        return value
    parsed = _parse_scoped_day_value(value)
    if date_format == "yyyymmdd":
        return parsed.strftime("%Y%m%d")
    return parsed.isoformat()


def _parse_scoped_day_value(value: str) -> date:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return date.fromisoformat(value)
    return datetime.fromisoformat(value).date()


def build_comparison_query(
    metric_name: str,
    table_name: str,
    metric_sql: str,
    dimensions: list[str],
    date_column: str = "event_date",
    order: str = "ASC",
    limit: int = 10,
    filter_expr: str | None = None,
    scoped_query: Mapping[str, Any] | None = None,
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

    if scoped_query is not None:
        scoped = _build_scoped_query_parts(table_name, scoped_query, include_period=True)
        return f"""
            WITH {scoped.cte_sql},
            by_period AS (
                SELECT
                    _period AS period,
                    {select_dims}
                    {metric_sql} AS metric_value,
                    COUNT(*) AS session_count
                FROM scoped
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


def _expand_group_by_aliases(select_exprs: list[str], group_by: list[str]) -> list[str]:
    """Expand SELECT aliases referenced in GROUP BY to their full expressions.

    Trino (standard SQL) rejects GROUP BY alias references; DuckDB accepts them.
    This expansion makes compiled SQL portable across engines.

    Example:
        select_exprs = ["CASE WHEN x = 1 THEN 'a' ELSE 'b' END AS cat", "count(*) AS n"]
        group_by     = ["cat"]
        → returns    ["CASE WHEN x = 1 THEN 'a' ELSE 'b' END"]
    """
    alias_to_expr: dict[str, str] = {}
    for expr in select_exprs:
        m = re.search(r"^(.*?)\s+AS\s+(\w+)\s*$", expr.strip(), re.IGNORECASE)
        if m:
            alias_to_expr[m.group(2).lower()] = m.group(1).strip()

    expanded: list[str] = []
    for item in group_by:
        key = item.strip().lower()
        expanded.append(alias_to_expr.get(key, item))
    return expanded


def _normalize_typed_aggregate_measures(raw_measures: Any) -> list[tuple[str, str]]:
    if not isinstance(raw_measures, list) or not raw_measures:
        raise ValueError("aggregate_query requires 'measures' param (list of measure objects)")

    normalized: list[tuple[str, str]] = []
    for measure in raw_measures:
        if not isinstance(measure, Mapping):
            raise ValueError("aggregate_query measures must be objects with 'expr' and 'as'")
        expr = str(measure.get("expr") or "").strip()
        alias = str(measure.get("as") or "").strip()
        if not expr or not alias:
            raise ValueError("aggregate_query measures must include non-empty 'expr' and 'as'")
        normalized.append((expr, alias))
    return normalized


def build_windowed_aggregate_query(
    table_name: str,
    measures: list[Mapping[str, Any]] | list[dict[str, Any]],
    group_by: list[str],
    *,
    order_by: str | None = None,
    limit: int = 100,
    scoped_query: Mapping[str, Any] | None = None,
) -> str:
    """Build typed aggregate_query SQL for single-window and compare modes.

    This is the execution-facing compiler path for the TSU aggregate contract:
    grouping is expressed via ``group_by`` and values are expressed only via
    ``measures``. When ``scoped_query.mode == 'compare'``, the shared
    scoped/periodized comparison skeleton is used.
    """

    agg_exprs = _normalize_typed_aggregate_measures(measures)
    group_by_sql = ", ".join(group_by)
    agg_select = ", ".join(f"{expr} AS {alias}" for expr, alias in agg_exprs)
    select_prefix = f"{group_by_sql}, " if group_by_sql else ""

    compare_mode = (
        scoped_query is not None
        and str(scoped_query.get("mode") or "").strip() == "compare"
    )
    if compare_mode:
        first_alias = agg_exprs[0][1]
        effective_order_by = order_by or f"{first_alias}_delta_pct DESC"
        scoped = _build_scoped_query_parts(table_name, scoped_query, include_period=True)
        by_period_select_prefix = f"_period, {group_by_sql}, " if group_by_sql else "_period, "

        pivot_parts: list[str] = []
        final_parts: list[str] = []
        for _, alias in agg_exprs:
            pivot_parts.append(
                f"MAX(CASE WHEN _period = 'current' THEN {alias} END) AS {alias}_current"
            )
            pivot_parts.append(
                f"MAX(CASE WHEN _period = 'baseline' THEN {alias} END) AS {alias}_baseline"
            )
            final_parts.append(f"{alias}_current")
            final_parts.append(f"{alias}_baseline")
            final_parts.append(
                f"ROUND(({alias}_current - {alias}_baseline) * 1.0 / NULLIF({alias}_baseline, 0) * 100, 2) "
                f"AS {alias}_delta_pct"
            )

        by_period_group_by = f"GROUP BY _period, {group_by_sql}" if group_by_sql else "GROUP BY _period"
        pivot_group_by = f"GROUP BY {group_by_sql}" if group_by_sql else ""
        pivot_select_prefix = f"{group_by_sql},\n            " if group_by_sql else ""
        final_select_prefix = f"{group_by_sql},\n            " if group_by_sql else ""
        return f"""
            WITH {scoped.cte_sql},
            by_period AS (
                SELECT {by_period_select_prefix}{agg_select}
                FROM scoped
                {by_period_group_by}
            ),
            pivoted AS (
                SELECT {pivot_select_prefix}{",\n            ".join(pivot_parts)}
                FROM by_period
                {pivot_group_by}
            )
            SELECT {final_select_prefix}{",\n            ".join(final_parts)}
            FROM pivoted
            ORDER BY {effective_order_by}
            LIMIT {limit}
        """

    group_clause = f" GROUP BY {group_by_sql}" if group_by_sql else ""
    order_clause = f" ORDER BY {order_by}" if order_by else ""

    if scoped_query is not None:
        scoped = _build_scoped_query_parts(table_name, scoped_query, include_period=False)
        return (
            f"WITH {scoped.cte_sql} "
            f"SELECT {select_prefix}{agg_select} FROM scoped{group_clause}{order_clause} LIMIT {limit}"
        )

    return f"SELECT {select_prefix}{agg_select} FROM {table_name}{group_clause}{order_clause} LIMIT {limit}"


def build_aggregate_comparison_query(
    table_name: str,
    select_exprs: list[str],
    group_by: list[str],
    date_column: str,
    order_by: str | None = None,
    limit: int = 100,
    filter_expr: str | None = None,
    scoped_query: Mapping[str, Any] | None = None,
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
    # Expanded form for by_period GROUP BY (Trino-safe: full expressions, not aliases)
    group_by_cols_expanded = ", ".join(_expand_group_by_aliases(select_exprs, group_by))
    agg_select = ", ".join(expr for expr, _ in agg_exprs)

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

    scoped_from = f"FROM {table_name}"
    if scoped_query is not None:
        scoped = _build_scoped_query_parts(table_name, scoped_query, include_period=True)
        scoped_from = "FROM scoped"
        with_prefix = f"WITH {scoped.cte_sql},"
    else:
        filter_clause = f" AND {filter_expr}" if filter_expr else ""
        with_prefix = ""

    by_period_group_by = f"GROUP BY _period, {group_by_cols_expanded}" if group_by_cols_expanded else "GROUP BY _period"
    pivot_group_by = f"GROUP BY {group_by_cols}" if group_by_cols else ""
    by_period_select_prefix = f"_period, {group_by_cols}, " if group_by_cols else "_period, "
    pivot_select_prefix = f"{group_by_cols},\n            " if group_by_cols else ""
    final_select_prefix = f"{group_by_cols},\n            " if group_by_cols else ""

    if scoped_query is not None:
        return f"""
            {with_prefix}
            by_period AS (
                SELECT {by_period_select_prefix}{agg_select}
                {scoped_from}
                {by_period_group_by}
            ),
            pivoted AS (
                SELECT {pivot_select_prefix}{pivot_select}
                FROM by_period
                {pivot_group_by}
            )
            SELECT {final_select_prefix}{final_select_agg}
            FROM pivoted
            ORDER BY {effective_order_by}
            LIMIT {limit}
        """

    filter_clause = f" AND {filter_expr}" if filter_expr else ""
    by_period_group_by = f"GROUP BY _period, {group_by_cols_expanded}" if group_by_cols_expanded else "GROUP BY _period"
    pivot_group_by = f"GROUP BY {group_by_cols}" if group_by_cols else ""
    by_period_select_prefix = f"_period, {group_by_cols}, " if group_by_cols else "_period, "
    pivot_select_prefix = f"{group_by_cols},\n            " if group_by_cols else ""
    final_select_prefix = f"{group_by_cols},\n            " if group_by_cols else ""

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
            SELECT {by_period_select_prefix}{agg_select}
            FROM periodized
            {by_period_group_by}
        ),
        pivoted AS (
            SELECT {pivot_select_prefix}{pivot_select}
            FROM by_period
            {pivot_group_by}
        )
        SELECT {final_select_prefix}{final_select_agg}
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
        scoped_query = params.get("scoped_query")
        sql = build_comparison_query(
            metric_name=metric_name,
            table_name=table_name,
            metric_sql=str(metric_sql),
            dimensions=list(dimensions),
            date_column=date_column,
            order=order,
            limit=limit,
            filter_expr=str(filter_expr) if filter_expr else None,
            scoped_query=scoped_query if isinstance(scoped_query, Mapping) else None,
        )
        compiled_params = list(semantic_context.get("period_params", []))
        if isinstance(scoped_query, Mapping):
            compiled_params = _build_scoped_query_parts(
                table_name,
                scoped_query,
                include_period=True,
            ).params
        return CompiledQuery(
            sql=sql,
            params=compiled_params,
            metadata={
                **metadata,
                "table_name": table_name,
                "metric_name": metric_name,
                "dimensions": list(dimensions),
            },
        )

    if step.step_type == "aggregate_query":
        table_name = step.table_name()
        if table_name is None:
            raise ValueError("aggregate_query requires 'table' or 'table_name' param")
        group_by = params.get("group_by", [])
        if not isinstance(group_by, list):
            raise ValueError("aggregate_query requires 'group_by' param (list of columns)")
        limit = int(params.get("limit", 100))
        scoped_query = params.get("scoped_query")
        has_scoped_query = isinstance(scoped_query, Mapping)
        order_by = params.get("order_by") or params.get("order")
        typed_measures = params.get("measures")

        if typed_measures is not None:
            sql = build_windowed_aggregate_query(
                table_name=table_name,
                measures=typed_measures,
                group_by=list(group_by),
                order_by=str(order_by) if order_by else None,
                limit=limit,
                scoped_query=scoped_query if has_scoped_query else None,
            )
            compiled_params: list[Any] = []
            compare_period = has_scoped_query and str(scoped_query.get("mode") or "") == "compare"
            if has_scoped_query:
                compiled_params = _build_scoped_query_parts(
                    table_name,
                    scoped_query,
                    include_period=compare_period,
                ).params
            return CompiledQuery(
                sql=sql,
                params=compiled_params,
                metadata={
                    **metadata,
                    "table_name": table_name,
                    "limit": limit,
                    "compare_period": compare_period,
                },
            )

        select_exprs = params.get("select")
        if not select_exprs or not isinstance(select_exprs, list):
            raise ValueError("aggregate_query requires 'select' param (list of expressions)")
        where = params.get("where")

        if params.get("compare_period") or (has_scoped_query and str(scoped_query.get("mode") or "") == "compare"):
            date_column = str(params.get("date_column", "event_date"))
            sql = build_aggregate_comparison_query(
                table_name=table_name,
                select_exprs=list(select_exprs),
                group_by=list(group_by),
                date_column=date_column,
                order_by=order_by,
                limit=limit,
                filter_expr=str(where) if where else None,
                scoped_query=scoped_query if has_scoped_query else None,
            )
            compiled_params = list(semantic_context.get("period_params", []))
            if has_scoped_query:
                compiled_params = _build_scoped_query_parts(
                    table_name,
                    scoped_query,
                    include_period=True,
                ).params
            return CompiledQuery(
                sql=sql,
                params=compiled_params,
                metadata={
                    **metadata,
                    "table_name": table_name,
                    "limit": limit,
                    "compare_period": True,
                },
            )

        select_clause = ", ".join(select_exprs)
        expanded_group_by = _expand_group_by_aliases(list(select_exprs), list(group_by))
        group_clause = f" GROUP BY {', '.join(expanded_group_by)}" if expanded_group_by else ""
        order_clause = f" ORDER BY {order_by}" if order_by else ""
        compiled_params: list[Any] = []

        if has_scoped_query:
            scoped = _build_scoped_query_parts(
                table_name,
                scoped_query,
                include_period=False,
            )
            sql = f"WITH {scoped.cte_sql} SELECT {select_clause} FROM scoped{group_clause}{order_clause} LIMIT {limit}"
            compiled_params = scoped.params
        else:
            where_clause = f" WHERE {where}" if where else ""
            sql = f"SELECT {select_clause} FROM {table_name}{where_clause}{group_clause}{order_clause} LIMIT {limit}"
        return CompiledQuery(
            sql=sql,
            params=compiled_params,
            metadata={**metadata, "table_name": table_name, "limit": limit},
        )

    raise ValueError(f"Unsupported compilation step type: {step.step_type}")


def _require_param(step: AnalysisStepIR, name: str) -> str:
    value = step.params.get(name)
    if value in (None, ""):
        raise ValueError(f"{step.step_type} requires '{name}' param")
    return str(value)
