"""Pure SQL compilation and IR construction helpers.

Extracted from ``marivo.analysis_core.compiler`` as part of Phase 3c.

This module contains only pure computation:
- SQL query builders for metric_query, aggregate_query, sample_rows, etc.
- Scoped query CTE construction
- IR snapshot builders for metrics, processes, entity fields, relationships
- Validation trace/summary construction
- Compile error construction helpers

The I/O-bound orchestrator ``compile_step`` remains in the original module;
it calls these pure helpers after resolving semantic objects through the
repository.

Deferred (requires I/O):
- ``compile_step``: orchestrates normalization, resolution, validation, IR
  bundle construction, and SQL generation -- too tightly coupled to
  ``SemanticRuntimeRepository`` calls interleaved with pure logic.
- ``_build_ir_bundle``: calls ``_measurement_node`` which optionally calls
  ``semantic_repository`` for predicate filter lineage.
- ``_measurement_node``: accesses ``semantic_repository`` for predicate
  resolution when available.
- ``_resolve_imported_dimension_physical_sources``: nominally accepts a
  repository but currently only emits deprecation warnings; extracted as a
  helper once the deprecation path is removed.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal, cast

from marivo.core.semantic.ir import (
    IntentNode,
    IntentRequestSnapshot,
    IrBundle,
    IrInputSnapshot,
    LoweringRequirement,
    MetricRefSnapshot,
    OutputBinding,
    ProcessNode,
    ProcessRefSnapshot,
    ProfileUsageTrace,
    RelationshipRefSnapshot,
    SemanticCompileError,
    ValidationRecord,
    ValidationSummary,
)

# ── Data classes ─────────────────────────────────────────────────────────


@dataclass(slots=True)
class CompiledQuery:
    """Minimal compile artifact used by the phase-1 refactor."""

    sql: str
    params: list[Any] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    ir_bundle: IrBundle | None = None
    compile_error: SemanticCompileError | None = None


class SemanticCompilerError(ValueError):
    """Structured compiler failure that preserves the compile gate diagnosis."""

    def __init__(self, compile_error: SemanticCompileError) -> None:
        super().__init__(compile_error["message"])
        self.compile_error = compile_error


class SemanticRequestCompatibilityError(ValueError):
    """Structured request-level compatibility failure."""

    def __init__(self, detail: dict[str, Any]) -> None:
        super().__init__(str(detail["message"]))
        self.detail = detail


@dataclass(slots=True)
class _ScopedQueryParts:
    cte_sql: str
    params: list[Any]


_CALENDAR_ALIGNMENT_SUPPORTED_GRAINS = frozenset({"day", "week", "month"})


# ── Scoped query construction ───────────────────────────────────────────


def _build_scoped_query_parts(
    table_name: str,
    scoped_query: Mapping[str, Any],
    *,
    include_period: bool,
) -> _ScopedQueryParts:
    """Build the shared scoped CTE for windowed query execution.

    ``include_period`` controls whether the CTE also materializes an internal
    ``_period`` column for compare-style compilation. Single-window callers set
    this to ``False`` because they aggregate only the current window.
    """
    analysis_time_expr = str(scoped_query.get("analysis_time_expr") or "").strip()
    if not analysis_time_expr:
        raise ValueError("scoped_query requires 'analysis_time_expr'")

    mode = _require_scoped_query_mode(scoped_query)
    analysis_time_kind = str(scoped_query.get("analysis_time_kind") or "").strip()
    engine_type = str(scoped_query.get("engine_type") or "").strip().lower()

    current = dict(scoped_query.get("current") or {})
    current_start = str(current.get("start") or "").strip()
    current_end = str(current.get("end") or "").strip()
    if not current_start or not current_end:
        raise ValueError("scoped_query requires current.start and current.end")

    # For date_field with CAST expression, skip the CAST-based predicate
    # and rely on partition_pruning_predicate for time filtering.
    if analysis_time_kind == "date_field" and "CAST(" in analysis_time_expr:
        filters: list[str] = []
        params: list[Any] = []
        current_start = ""
        current_end = ""
    else:
        current_start = _format_scoped_bound(scoped_query, current_start)
        current_end = _format_scoped_bound(scoped_query, current_end)
        current_predicate, current_params = _build_scoped_time_predicate(
            analysis_time_expr,
            current_start,
            current_end,
            analysis_time_kind=analysis_time_kind,
            engine_type=engine_type,
        )
        filters = [f"({current_predicate})"]
        params = list(current_params)

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

        if analysis_time_kind == "date_field" and "CAST(" in analysis_time_expr:
            raise ValueError(
                "compare mode is not supported for date_field with CAST expression; "
                "the time axis must be resolved to a native date column or timestamp"
            )

        baseline_start = _format_scoped_bound(scoped_query, baseline_start)
        baseline_end = _format_scoped_bound(scoped_query, baseline_end)
        baseline_predicate, baseline_params = _build_scoped_time_predicate(
            analysis_time_expr,
            baseline_start,
            baseline_end,
            analysis_time_kind=analysis_time_kind,
            engine_type=engine_type,
        )
        if include_period:
            select_prefix = [
                "CASE",
                f"                    WHEN {current_predicate} THEN 'current'",
                f"                    WHEN {baseline_predicate} THEN 'baseline'",
                "                END AS _period",
            ]
            params = [
                *current_params,
                *baseline_params,
                *current_params,
                *baseline_params,
            ]
        filters = [f"(({current_predicate}) OR ({baseline_predicate}))"]
        if not include_period:
            params = [*current_params, *baseline_params]

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


def _build_scoped_time_predicate(
    analysis_time_expr: str,
    start: str,
    end: str,
    *,
    analysis_time_kind: str,
    engine_type: str,
) -> tuple[str, list[Any]]:
    if analysis_time_kind in {"timestamp", "partition_fields"} and engine_type == "trino":
        start_literal = _trino_timestamp_literal(start)
        end_literal = _trino_timestamp_literal(end)
        return (
            f"{analysis_time_expr} >= {start_literal} AND {analysis_time_expr} < {end_literal}",
            [],
        )
    return f"{analysis_time_expr} >= ? AND {analysis_time_expr} < ?", [start, end]


def _trino_timestamp_literal(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace(" ", "T"))
    return f"TIMESTAMP '{parsed.strftime('%Y-%m-%d %H:%M:%S')}'"


def _format_scoped_bound(scoped_query: Mapping[str, Any], value: str) -> str:
    analysis_time_kind = str(scoped_query.get("analysis_time_kind") or "").strip()
    engine_type = str(scoped_query.get("engine_type") or "").strip().lower()
    if analysis_time_kind == "timestamp":
        if engine_type == "trino":
            return value.replace("T", " ")
        return value
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


def _require_scoped_query_mode(scoped_query: Mapping[str, Any]) -> str:
    mode = str(scoped_query.get("mode") or "").strip()
    if mode not in {"single_window", "compare"}:
        raise ValueError("scoped_query.mode must be 'single_window' or 'compare'")
    return mode


# ── Metric query order normalization ────────────────────────────────────


def _normalize_metric_query_order(order: str, *, mode: str) -> tuple[str, str]:
    normalized_mode = str(mode or "").strip().lower()
    normalized = str(order or "").strip().upper()
    if normalized_mode == "compare":
        if normalized in {"ASC", "DESC"}:
            return ("delta_pct", normalized)
        if normalized in {"DELTA_PCT ASC", "DELTA_PCT DESC"}:
            field, direction = normalized.split()
            return (field.lower(), direction)
        raise ValueError(
            f"Invalid metric_query compare order '{order}'; must be delta_pct ASC or DESC"
        )
    if normalized_mode == "single_window":
        if normalized in {
            "CURRENT_VALUE ASC",
            "CURRENT_VALUE DESC",
            "CURRENT_SESSIONS ASC",
            "CURRENT_SESSIONS DESC",
        }:
            field, direction = normalized.split()
            return (field.lower(), direction)
        raise ValueError(
            f"Invalid metric_query single_window order '{order}'; must be current_value/current_sessions ASC or DESC"
        )
    raise ValueError("metric_query order mode must be 'compare' or 'single_window'")


# ── SQL builders ────────────────────────────────────────────────────────


def build_metric_query(
    metric_name: str,
    table_name: str,
    metric_sql: str,
    dimensions: list[str],
    dimension_sql_expressions: Mapping[str, str] | None = None,
    date_column: str = "event_date",
    order: str = "DELTA_PCT ASC",
    limit: int = 10,
    filter_expr: str | None = None,
    scoped_query: Mapping[str, Any] | None = None,
) -> str:
    """Build metric_query SQL for compare and single-window semantic metric queries.

    When *dimensions* is empty, an aggregate-only query is produced
    (no GROUP BY on dimensions). ``scoped_query.mode == 'compare'`` emits
    current-vs-baseline columns; ``single_window`` emits current-window
    observation columns only.
    """

    del metric_name

    dimension_sql_expressions = dimension_sql_expressions or {}
    select_dimension_exprs: list[str] = []
    group_dimension_exprs: list[str] = []
    for dimension in dimensions:
        expr = str(dimension_sql_expressions.get(dimension) or dimension).strip()
        if expr != dimension:
            select_dimension_exprs.append(f'{expr} AS "{dimension}"')
            group_dimension_exprs.append(expr)
            continue
        select_dimension_exprs.append(dimension)
        group_dimension_exprs.append(dimension)

    if dimensions:
        dim_cols = ", ".join(select_dimension_exprs)
        group_dim_cols = ", ".join(group_dimension_exprs)
        group_by_period = f"GROUP BY period, {group_dim_cols}"
        group_by_dims = f"GROUP BY {group_dim_cols}"
        select_dims = f"{dim_cols},"
    else:
        group_by_period = "GROUP BY period"
        group_by_dims = ""
        select_dims = ""

    if scoped_query is not None:
        mode = _require_scoped_query_mode(scoped_query)
        effective_order = order
        if not str(effective_order or "").strip():
            effective_order = "CURRENT_VALUE DESC" if mode == "single_window" else "DELTA_PCT ASC"
        order_field, order_direction = _normalize_metric_query_order(effective_order, mode=mode)
        if mode == "single_window":
            scoped = _build_scoped_query_parts(table_name, scoped_query, include_period=False)
            group_clause = f"GROUP BY {', '.join(group_dimension_exprs)}" if dimensions else ""
            order_clause = f"ORDER BY {order_field} {order_direction}" if order else ""
            return f"""
                WITH {scoped.cte_sql}
                SELECT
                    {select_dims}
                    ROUND({metric_sql}, 2) AS current_value,
                    COUNT(*) AS current_sessions
                FROM scoped
                {group_clause}
                {order_clause}
                LIMIT {limit}
            """

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
            ORDER BY {order_field} {order_direction}
            LIMIT {limit}
        """

    legacy_order_field, legacy_order_direction = _normalize_metric_query_order(
        order, mode="compare"
    )
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
        ORDER BY {legacy_order_field} {legacy_order_direction}
        LIMIT {limit}
    """


def _metric_query_dimension_sql_expressions(
    dimensions: list[str],
    imported_dimension_sources: list[dict[str, Any]],
) -> dict[str, str]:
    physical_names = {
        str(source.get("dimension_ref")): str(source.get("physical_name"))
        for source in imported_dimension_sources
        if source.get("dimension_ref") is not None and source.get("physical_name") is not None
    }
    return {
        dimension: physical_names[dimension]
        for dimension in dimensions
        if dimension in physical_names
    }


def _extract_agg_alias(expr: str) -> str:
    """Extract alias from e.g. 'count(*) as query_count' -> 'query_count'.

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
        -> returns    ["CASE WHEN x = 1 THEN 'a' ELSE 'b' END"]
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
        scoped_query is not None and str(scoped_query.get("mode") or "").strip() == "compare"
    )
    if compare_mode:
        assert scoped_query is not None
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

        by_period_group_by = (
            f"GROUP BY _period, {group_by_sql}" if group_by_sql else "GROUP BY _period"
        )
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

    Mirrors ``build_metric_query`` but driven by user-supplied ``select``
    and ``group_by`` instead of a registered semantic metric.

    SQL ``?`` placeholder order (6 params, same as build_metric_query):
        current_start, current_end,   (CASE WHEN ... THEN 'current')
        baseline_start, baseline_end, (CASE WHEN ... THEN 'baseline')
        baseline_start, current_end   (outer WHERE range)
    """
    agg_exprs: list[tuple[str, str]] = []
    for expr in select_exprs:
        stripped = expr.strip()
        if stripped in group_by:
            continue
        alias = _extract_agg_alias(stripped)
        agg_exprs.append((stripped, alias))

    if not agg_exprs:
        raise ValueError(
            "compare_period requires at least one aggregate expression with an alias in 'select'"
        )

    group_by_cols = ", ".join(group_by)
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

    by_period_group_by = (
        f"GROUP BY _period, {group_by_cols_expanded}"
        if group_by_cols_expanded
        else "GROUP BY _period"
    )
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
    by_period_group_by = (
        f"GROUP BY _period, {group_by_cols_expanded}"
        if group_by_cols_expanded
        else "GROUP BY _period"
    )
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


# ── Validation trace/summary builders ───────────────────────────────────


_VALIDATION_GATE_ORDER: tuple[
    Literal[
        "request_shape",
        "intent_support",
        "metric_process_compatibility",
        "binding_grounding",
        "predicate_contract",
        "scope_validation",
        "predicate_conflict",
        "dimension_compatibility",
        "intent_specific",
        "dimension_additivity",
        "lowering_precheck",
    ],
    ...,
] = (
    "request_shape",
    "intent_support",
    "metric_process_compatibility",
    "binding_grounding",
    "predicate_contract",
    "scope_validation",
    "predicate_conflict",
    "dimension_compatibility",
    "intent_specific",
    "dimension_additivity",
    "lowering_precheck",
)


def build_validation_trace(validation_result: Any) -> list[ValidationRecord]:
    """Build a validation trace from a ValidationResult-like object.

    The *validation_result* must have ``.issues`` (iterable of objects with
    ``.gate`` and ``.severity`` attributes).
    """
    failed_gates = {issue.gate for issue in validation_result.issues if issue.severity == "error"}
    warning_gates = {issue.gate for issue in validation_result.issues if issue.severity != "error"}
    trace: list[ValidationRecord] = []
    for gate in _VALIDATION_GATE_ORDER:
        if gate in failed_gates:
            continue
        record: ValidationRecord = {
            "validation_kind": gate,
            "status": "passed",
        }
        if gate in warning_gates:
            record["reason_code"] = "passed_with_warning"
        trace.append(record)
    return trace


def build_validation_summary(
    validation_result: Any, validation_trace: list[ValidationRecord]
) -> ValidationSummary:
    """Build a validation summary from a ValidationResult and its trace.

    The *validation_result* must have ``.issues`` and ``.validated_dimension_refs``
    attributes, and optionally ``.resolved_filter_time_ref``.
    """
    summary: ValidationSummary = {
        "passed_gate_count": len(validation_trace),
        "warning_count": len(
            [issue for issue in validation_result.issues if issue.severity != "error"]
        ),
        "validated_dimension_refs": list(validation_result.validated_dimension_refs),
    }
    if validation_result.resolved_filter_time_ref is not None:
        summary["resolved_filter_time_ref"] = validation_result.resolved_filter_time_ref
    return summary


def build_compile_error(validation_message: str, validation_result: Any) -> SemanticCompileError:
    """Build a SemanticCompileError from a validation result.

    The *validation_result* must have a ``.primary_error_issue()`` method.
    """
    first_error = validation_result.primary_error_issue()
    compile_error: SemanticCompileError = {
        "error_code": first_error.code,
        "failed_gate": first_error.gate,
        "message": validation_message,
    }
    if first_error.subject_ref is not None:
        compile_error["subject_ref"] = first_error.subject_ref
    if first_error.details:
        compile_error["details"] = dict(first_error.details)
    return compile_error


def build_request_compatibility_error(
    *,
    step_type: str,
    normalized_request: Any,
    validation_result: Any,
) -> dict[str, Any]:
    """Build a compatibility error detail dict from a validation result.

    *normalized_request* must have ``.intent_kind``, ``.metric_ref``,
    ``.process_ref``, ``.request_dimensions`` attributes.
    *validation_result* must have ``.issues_for_category()`` method.
    """
    issues = validation_result.issues_for_category("compatibility")
    primary_issue = issues[0]
    request_context = {
        "step_type": step_type,
        "intent_kind": normalized_request.intent_kind,
        "metric_ref": normalized_request.metric_ref,
        "process_ref": normalized_request.process_ref,
        "dimension_refs": list(normalized_request.request_dimensions),
    }
    request_context = {
        key: value for key, value in request_context.items() if value not in (None, [])
    }
    return {
        "message": "Request is incompatible with resolved semantic objects",
        "code": "semantic_request_incompatible",
        "category": "compatibility",
        "subject_ref": primary_issue.subject_ref,
        "issues": [issue.to_dict() for issue in issues],
        "request_context": request_context,
    }


# ── IR snapshot builders ────────────────────────────────────────────────


def _stable_plan_id(step_index: int, step_type: str, normalized_request: Any) -> str:
    """Compute a stable plan ID from step and request data.

    *normalized_request* must have ``.metric_ref``, ``.process_ref``,
    ``.table_name``, ``.request_dimensions``, ``.request_result_mode``.
    """
    raw = "|".join(
        [
            step_type,
            str(step_index),
            normalized_request.metric_ref or "",
            normalized_request.process_ref or "",
            normalized_request.table_name or "",
            ",".join(normalized_request.request_dimensions),
            normalized_request.request_result_mode or "",
        ]
    )
    digest = hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]
    return f"ir_plan.{step_type}.{step_index}.{digest}"


def metric_snapshot(
    metric: Any,
) -> MetricRefSnapshot:
    header = dict(metric.semantic_object.get("header") or {})
    snapshot: MetricRefSnapshot = {
        "metric_ref": metric.ref,
        "resolved_metric_revision": metric.revision,
        "resolved_metric_object_id": metric.object_id,
    }
    observation_grain_ref = _optional_str(header.get("observation_grain_ref"))
    if observation_grain_ref is not None:
        snapshot["resolved_observation_grain_ref"] = observation_grain_ref
    return snapshot


def process_snapshot(process: Any) -> ProcessRefSnapshot:
    interface_contract = dict(process.semantic_object.get("interface_contract") or {})
    snapshot: ProcessRefSnapshot = {
        "process_ref": process.ref,
    }
    anchor_time_ref = _optional_str(interface_contract.get("anchor_time_ref"))
    if anchor_time_ref is not None:
        snapshot["resolved_anchor_time_ref"] = anchor_time_ref
    return snapshot


def relationship_snapshot(relationship: Any) -> RelationshipRefSnapshot:
    return {
        "relationship_ref": relationship.relationship_ref,
        "left_entity_ref": relationship.left_entity_ref,
        "right_entity_ref": relationship.right_entity_ref,
        "revision": relationship.revision,
        "key_alignment": relationship.key_alignment,
        "time_alignment": relationship.time_alignment,
        "cardinality": relationship.cardinality,
        "grain_compatibility": relationship.grain_compatibility,
        "snapshot_effective_window_alignment": (relationship.snapshot_effective_window_alignment),
    }


def intent_request_snapshot(
    normalized_request: Any,
    resolved_inputs: Any,
) -> IntentRequestSnapshot:
    """Build an IntentRequestSnapshot dict from request and resolved inputs.

    *normalized_request* must have ``.intent_kind``, ``.request_class``,
    ``.request_dimensions``, ``.request_result_mode``, and ``.request_options``.
    *resolved_inputs* must have ``.resolved_filter_time`` (with ``.ref``).
    """
    options: dict[str, str | int | float | bool | None] = {}
    for key, value in normalized_request.request_options.items():
        if isinstance(value, (bool, int, float, str)) or value is None:
            options[key] = value
    snapshot: IntentRequestSnapshot = {
        "intent_kind": normalized_request.intent_kind,
        "request_class": normalized_request.request_class,
    }
    if normalized_request.request_dimensions:
        snapshot["requested_dimensions"] = list(normalized_request.request_dimensions)
    if normalized_request.request_result_mode is not None:
        snapshot["requested_result_mode"] = normalized_request.request_result_mode
    if resolved_inputs.resolved_filter_time is not None:
        snapshot["request_time_scope_ref"] = resolved_inputs.resolved_filter_time.ref
    if options:
        snapshot["request_options"] = options
    return snapshot


def build_ir_inputs(
    normalized_request: Any,
    resolved_inputs: Any,
) -> IrInputSnapshot:
    """Build the IR input snapshot from normalized request and resolved inputs.

    *resolved_inputs* must have ``.resolved_relationships``,
    ``.resolved_metric``, ``.resolved_process``, ``.resolved_left_process``,
    ``.resolved_right_process``.
    """
    input_snapshot: IrInputSnapshot = {
        "intent_request": intent_request_snapshot(normalized_request, resolved_inputs),
    }
    if normalized_request.metric_ref is not None:
        input_snapshot["metric_ref"] = normalized_request.metric_ref
    process_refs = [
        process_ref
        for process_ref in (
            normalized_request.process_ref,
            normalized_request.left_process_ref,
            normalized_request.right_process_ref,
        )
        if process_ref is not None
    ]
    if process_refs:
        input_snapshot["process_refs"] = process_refs
    if resolved_inputs.resolved_relationships:
        input_snapshot["resolved_relationships"] = [
            relationship_snapshot(relationship)
            for relationship in resolved_inputs.resolved_relationships.values()
        ]
    if resolved_inputs.resolved_metric is not None:
        input_snapshot["resolved_metric"] = metric_snapshot(resolved_inputs.resolved_metric)
    resolved_processes = [
        process
        for process in (
            resolved_inputs.resolved_process,
            resolved_inputs.resolved_left_process,
            resolved_inputs.resolved_right_process,
        )
        if process is not None
    ]
    if resolved_processes:
        input_snapshot["resolved_processes"] = [
            process_snapshot(process) for process in resolved_processes
        ]
    return input_snapshot


def build_lowering_requirements(
    *,
    step_index: int,
    step_type: str,
    normalized_request: Any,
    resolved_inputs: Any,
    intent_node_id: str,
) -> list[LoweringRequirement]:
    """Build lowering requirements for a compiled step.

    *normalized_request* must have ``.request_time_scope``.
    *resolved_inputs* must have ``.resolved_metric``.
    """
    requirements: list[LoweringRequirement] = [
        {
            "requirement_kind": "engine_sql_execution",
            "source_node_id": intent_node_id,
        }
    ]
    if normalized_request.request_time_scope:
        requirements.append(
            {
                "requirement_kind": "time_window_filter",
                "source_node_id": intent_node_id,
            }
        )
    return requirements


def build_profile_usage_trace(profile_traces: list[Any]) -> list[ProfileUsageTrace]:
    """Build profile usage trace entries from raw trace objects.

    Each trace object must have ``.subject_ref``, ``.applied``, ``.reason``,
    and optionally ``.profile_ref``, ``.subject_revision``, ``.resolved_subject_revision``.
    """
    trace_payload: list[ProfileUsageTrace] = []
    for trace in profile_traces:
        item: ProfileUsageTrace = {
            "subject_ref": trace.subject_ref,
            "applied": trace.applied,
            "reason": trace.reason,
        }
        if trace.profile_ref is not None:
            item["profile_ref"] = trace.profile_ref
        if trace.subject_revision is not None:
            item["subject_revision"] = trace.subject_revision
        if trace.resolved_subject_revision is not None:
            item["resolved_subject_revision"] = trace.resolved_subject_revision
        trace_payload.append(item)
    return trace_payload


# ── Calendar alignment helpers (pure) ──────────────────────────────────


def build_calendar_alignment_coverage(bucket_pairing: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute coverage summary from a calendar bucket pairing."""
    aligned_bucket_count = sum(
        1 for bucket in bucket_pairing if bucket.get("baseline_bucket_start") is not None
    )
    total_bucket_count = len(bucket_pairing)
    unpaired_bucket_count = total_bucket_count - aligned_bucket_count
    aligned_ratio = aligned_bucket_count / total_bucket_count if total_bucket_count else 0.0
    return {
        "aligned_bucket_count": aligned_bucket_count,
        "unpaired_bucket_count": unpaired_bucket_count,
        "aligned_ratio": aligned_ratio,
    }


def serialize_calendar_window(window: tuple[date, date] | None) -> dict[str, str] | None:
    """Serialize a (start, end) date window to a dict."""
    if window is None:
        return None
    return {
        "start": window[0].isoformat(),
        "end": window[1].isoformat(),
    }


def date_window_from_time_scope(time_scope: Mapping[str, Any]) -> tuple[date, date]:
    """Extract a (start, end) date window from a time_scope mapping."""
    current = dict(time_scope.get("current") or {})
    start = _parse_date_like(str(current.get("start") or ""))
    end = _parse_date_like(str(current.get("end") or ""))
    if start >= end:
        raise ValueError("calendar alignment requires time_scope.current.start < end")
    return start, end


def _parse_date_like(value: str) -> date:
    if not value:
        raise ValueError("calendar alignment requires date window boundaries")
    with_datetime = value.replace(" ", "T")
    try:
        return datetime.fromisoformat(with_datetime).date()
    except ValueError:
        return date.fromisoformat(value[:10])


# ── Intent/Measurement/Process node builders (pure subset) ─────────────


def build_process_node(step_index: int, process: Any) -> ProcessNode:
    """Build a ProcessNode IR entry from a resolved process."""
    interface_contract = dict(process.semantic_object.get("interface_contract") or {})
    node: ProcessNode = {
        "node_id": f"process:{step_index}:{process.ref}",
        "node_type": "process",
        "process_ref": process.ref,
        "process_type": _optional_str(process.semantic_object.get("process_type")) or "",
        "contract_mode": cast(
            "Literal['context_provider', 'entity_stream']",
            _optional_str(interface_contract.get("contract_mode")) or "context_provider",
        ),
        "population_subject_ref": _optional_str(interface_contract.get("population_subject_ref"))
        or "",
    }
    context_kind = _optional_str(interface_contract.get("context_kind"))
    entity_ref = _optional_str(interface_contract.get("entity_ref"))
    emitted_grain_ref = _optional_str(interface_contract.get("emitted_grain_ref"))
    membership_cardinality = _optional_str(interface_contract.get("membership_cardinality"))
    subject_cardinality = _optional_str(interface_contract.get("subject_cardinality"))
    if context_kind is not None:
        node["context_kind"] = context_kind
    if entity_ref is not None:
        node["entity_ref"] = entity_ref
    if emitted_grain_ref is not None:
        node["emitted_grain_ref"] = emitted_grain_ref
    if membership_cardinality in {"exclusive_one", "repeatable_many"}:
        node["membership_cardinality"] = cast(
            "Literal['exclusive_one', 'repeatable_many']", membership_cardinality
        )
    if subject_cardinality in {"one", "many"}:
        node["subject_cardinality"] = cast("Literal['one', 'many']", subject_cardinality)
    return node


def build_intent_node(
    *,
    step_index: int,
    step_type: str,
    normalized_request: Any,
    output_binding: OutputBinding,
    depends_on: list[str],
) -> IntentNode:
    """Build an IntentNode IR entry."""
    node: IntentNode = {
        "node_id": f"intent:{step_index}",
        "node_type": "intent",
        "intent_kind": step_type,
        "intent_level": "root",
        "depends_on": depends_on,
        "output_bindings": [output_binding],
    }
    if normalized_request.request_dimensions:
        node["requested_dimensions"] = list(normalized_request.request_dimensions)
    if normalized_request.request_result_mode is not None:
        node["requested_result_mode"] = normalized_request.request_result_mode
    return node


# ── Utility ─────────────────────────────────────────────────────────────


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def requests_imported_dimensions(resolved_inputs: Any) -> bool:
    """Check whether the resolved inputs request any imported dimensions.

    *resolved_inputs* must have ``.normalized_request.request_dimensions`` and
    ``.resolved_imported_dimensions`` (iterable with ``.dimension_ref``).
    """
    requested_dimension_refs = set(resolved_inputs.normalized_request.request_dimensions)
    imported_dimension_refs = {
        bridge.dimension_ref for bridge in resolved_inputs.resolved_imported_dimensions
    }
    return bool(requested_dimension_refs & imported_dimension_refs)
