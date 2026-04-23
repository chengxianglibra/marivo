from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal, cast

from app.analysis_core.calendar_alignment_baseline import resolve_calendar_baseline_window
from app.analysis_core.calendar_alignment_pairing import (
    resolve_calendar_bucket_pairing,
)
from app.analysis_core.calendar_data_runtime import (
    CalendarDataReaderLike,
    CalendarDataReadResult,
    CalendarDataResolutionError,
)
from app.analysis_core.calendar_policy import (
    get_calendar_policy,
)
from app.analysis_core.capability_profiles import derive_compiler_state
from app.analysis_core.ir import (
    STEP_ARTIFACT_KINDS,
    AnalysisStepIR,
    ArtifactLineageEntry,
    BindingRefSnapshot,
    CarrierBinding,
    CompileReport,
    IntentNode,
    IntentRequestSnapshot,
    IrArtifact,
    IrBundle,
    IrInputSnapshot,
    IrPlan,
    IrPlanHeader,
    LoweringRequirement,
    MeasurementNode,
    MetricRefSnapshot,
    OutputBinding,
    ProcessNode,
    ProcessRefSnapshot,
    ProfileUsageTrace,
    SemanticCompileError,
    ValidationRecord,
    ValidationSummary,
)
from app.analysis_core.typed_resolution import (
    NormalizedCompilerRequest,
    ResolvedCompilerInputs,
    normalize_step_request,
    resolve_compiler_inputs,
)
from app.analysis_core.validator import (
    ValidationIssue,
    validate_compiler_inputs,
    validation_error_message,
)
from app.evidence_engine.ref_boundary import assert_no_canonical_refs_in_semantic_payload
from app.semantic_runtime import SemanticRuntimeRepository
from app.semantic_runtime.resolution import ResolvedSemanticObject


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
    # The CAST expression is still used for SELECT/GROUP BY (DATE_TRUNC).
    filters: list[str]
    params: list[Any]
    if analysis_time_kind == "date_field" and "CAST(" in analysis_time_expr:
        # Don't add CAST-based predicate; partition_pruning_predicate handles filtering
        filters = []
        params = []
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
        # Compare mode also needs to handle CAST expression case
        baseline = dict(scoped_query.get("baseline") or {})
        baseline_start = str(baseline.get("start") or "").strip()
        baseline_end = str(baseline.get("end") or "").strip()
        if not baseline_start or not baseline_end:
            raise ValueError("scoped_query compare mode requires baseline.start and baseline.end")

        if analysis_time_kind == "date_field" and "CAST(" in analysis_time_expr:
            # For CAST expression, compare mode cannot work without time predicates
            # This is a limitation; partition_pruning_predicate covers one window only
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
)


def _build_compile_error(validation_message: str, validation_result: Any) -> SemanticCompileError:
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


def _build_request_compatibility_error(
    *,
    step_type: str,
    normalized_request: Any,
    resolved_inputs: Any,
    validation_result: Any,
) -> dict[str, Any]:
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


def _requests_imported_dimensions(resolved_inputs: ResolvedCompilerInputs) -> bool:
    requested_dimension_refs = set(resolved_inputs.normalized_request.request_dimensions)
    imported_dimension_refs = {
        bridge.dimension_ref for bridge in resolved_inputs.resolved_imported_dimensions
    }
    return bool(requested_dimension_refs & imported_dimension_refs)


def _resolve_imported_dimension_physical_sources(
    resolved_inputs: ResolvedCompilerInputs,
    *,
    semantic_repository: SemanticRuntimeRepository | None,
) -> tuple[list[dict[str, Any]], list[ValidationIssue]]:
    if not _requests_imported_dimensions(resolved_inputs):
        return [], []
    if semantic_repository is None:
        return [], [
            ValidationIssue(
                code="COMPILER_DIMENSION_IMPORT_PHYSICAL_UNRESOLVED",
                gate="dimension_compatibility",
                category="compatibility",
                severity="error",
                message="Imported dimension physical source resolution requires semantic_repository",
                subject_ref=resolved_inputs.resolved_metric.ref
                if resolved_inputs.resolved_metric is not None
                else None,
            )
        ]

    requested_dimension_refs = set(resolved_inputs.normalized_request.request_dimensions)
    imported_dimension_refs = {
        bridge.dimension_ref: bridge
        for bridge in resolved_inputs.resolved_imported_dimensions
        if bridge.dimension_ref in requested_dimension_refs
    }
    resolved_sources: list[dict[str, Any]] = []
    issues: list[ValidationIssue] = []
    for dimension_ref, bridge in sorted(imported_dimension_refs.items()):
        imported_binding = semantic_repository.resolve_binding_ref(bridge.source_binding_ref)
        interface_contract = dict(imported_binding.semantic_object.get("interface_contract") or {})
        matching_field_bindings = [
            dict(field_binding)
            for field_binding in interface_contract.get("field_bindings") or []
            if str(field_binding.get("semantic_ref") or "").strip() == dimension_ref
            and str((field_binding.get("target") or {}).get("target_kind") or "").strip()
            == "stable_descriptor"
        ]
        if len(matching_field_bindings) != 1:
            issues.append(
                ValidationIssue(
                    code="COMPILER_DIMENSION_IMPORT_LINEAGE_MISSING",
                    gate="dimension_compatibility",
                    category="compatibility",
                    severity="error",
                    message="Imported dimension bridge does not resolve to a unique field lineage",
                    subject_ref=dimension_ref,
                    details={
                        "metric_ref": resolved_inputs.resolved_metric.ref
                        if resolved_inputs.resolved_metric is not None
                        else None,
                        "source_binding_ref": bridge.source_binding_ref,
                        "source_entity_ref": bridge.source_entity_ref,
                        "import_key": bridge.import_key,
                        "match_count": len(matching_field_bindings),
                    },
                )
            )
            continue

        field_binding = matching_field_bindings[0]
        carrier_binding_key = _optional_str(field_binding.get("carrier_binding_key"))
        surface_ref = _optional_str(field_binding.get("surface_ref"))
        carrier_bindings = {
            _optional_str(carrier_binding.get("binding_key")): dict(carrier_binding)
            for carrier_binding in interface_contract.get("carrier_bindings") or []
            if _optional_str(carrier_binding.get("binding_key")) is not None
        }
        carrier_binding = carrier_bindings.get(carrier_binding_key)
        carrier_locator: dict[str, Any] | str | None = None
        if carrier_binding is not None:
            raw_carrier_locator = carrier_binding.get("carrier_locator")
            if isinstance(raw_carrier_locator, dict):
                carrier_locator = dict(cast("dict[str, Any]", raw_carrier_locator))
            elif isinstance(raw_carrier_locator, str) and raw_carrier_locator.strip():
                carrier_locator = raw_carrier_locator.strip()
        physical_name = None
        if carrier_binding is not None:
            for field_surface in carrier_binding.get("field_surfaces") or []:
                if _optional_str(field_surface.get("surface_ref")) == surface_ref:
                    physical_name = _optional_str(field_surface.get("physical_name"))
                    break
        if carrier_binding_key is None or surface_ref is None or carrier_binding is None:
            issues.append(
                ValidationIssue(
                    code="COMPILER_DIMENSION_IMPORT_LINEAGE_MISSING",
                    gate="dimension_compatibility",
                    category="compatibility",
                    severity="error",
                    message="Imported dimension bridge lineage is incomplete",
                    subject_ref=dimension_ref,
                    details={
                        "metric_ref": resolved_inputs.resolved_metric.ref
                        if resolved_inputs.resolved_metric is not None
                        else None,
                        "source_binding_ref": bridge.source_binding_ref,
                        "source_entity_ref": bridge.source_entity_ref,
                        "import_key": bridge.import_key,
                        "carrier_binding_key": carrier_binding_key,
                        "surface_ref": surface_ref,
                    },
                )
            )
            continue
        if carrier_locator is None or physical_name is None:
            issues.append(
                ValidationIssue(
                    code="COMPILER_DIMENSION_IMPORT_PHYSICAL_UNRESOLVED",
                    gate="dimension_compatibility",
                    category="compatibility",
                    severity="error",
                    message="Imported dimension bridge cannot resolve a physical carrier source",
                    subject_ref=dimension_ref,
                    details={
                        "metric_ref": resolved_inputs.resolved_metric.ref
                        if resolved_inputs.resolved_metric is not None
                        else None,
                        "source_binding_ref": bridge.source_binding_ref,
                        "source_entity_ref": bridge.source_entity_ref,
                        "import_key": bridge.import_key,
                        "carrier_binding_key": carrier_binding_key,
                        "surface_ref": surface_ref,
                        "physical_name": physical_name,
                    },
                )
            )
            continue
        resolved_source: dict[str, Any] = {
            "dimension_ref": dimension_ref,
            "source_binding_ref": bridge.source_binding_ref,
            "source_entity_ref": bridge.source_entity_ref,
            "import_key": bridge.import_key,
            "carrier_binding_key": carrier_binding_key,
            "carrier_locator": carrier_locator,
            "surface_ref": surface_ref,
            "physical_name": physical_name,
        }
        source_object_ref = _optional_str(carrier_binding.get("source_object_ref"))
        if source_object_ref is not None:
            resolved_source["source_object_ref"] = source_object_ref
        resolved_sources.append(resolved_source)

    return resolved_sources, issues


def _build_validation_trace(validation_result: Any) -> list[ValidationRecord]:
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


def _build_validation_summary(
    validation_result: Any, validation_trace: list[ValidationRecord]
) -> ValidationSummary:
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


def _build_profile_usage_trace(profile_traces: list[Any]) -> list[ProfileUsageTrace]:
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


def _build_lowering_requirements(
    *,
    step: AnalysisStepIR,
    normalized_request: NormalizedCompilerRequest,
    resolved_inputs: ResolvedCompilerInputs,
    intent_node_id: str,
) -> list[LoweringRequirement]:
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
    if resolved_inputs.resolved_bindings and resolved_inputs.resolved_metric is not None:
        requirements.append(
            {
                "requirement_kind": "semantic_binding_grounding",
                "source_node_id": f"measurement:{step.index}",
            }
        )
    return requirements


def _resolve_calendar_alignment_plan(
    normalized_request: NormalizedCompilerRequest,
    *,
    semantic_context: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    semantic_context = semantic_context or {}
    policy_ref = normalized_request.request_calendar_policy_ref
    if policy_ref is None:
        return None
    request_time_scope = normalized_request.request_time_scope or {}
    if not request_time_scope:
        return None
    mode = str(request_time_scope.get("mode") or "").strip()
    if mode != "single_window":
        return None
    grain = str(request_time_scope.get("grain") or "").strip()
    if grain == "hour":
        raise SemanticRequestCompatibilityError(
            {
                "message": "Calendar alignment policies do not support hour-grain observe windows",
                "code": "calendar_policy_hour_grain_unsupported",
                "category": "compatibility",
                "issues": [
                    {
                        "code": "calendar_policy_hour_grain_unsupported",
                        "message": (
                            "calendar_policy_ref requires a day/week/month window; "
                            "hour-grain observe requests are not supported"
                        ),
                        "details": {
                            "policy_ref": policy_ref,
                            "request_grain": grain,
                        },
                    }
                ],
                "request_context": {
                    "intent_kind": normalized_request.intent_kind,
                    "calendar_policy_ref": policy_ref,
                    "request_grain": grain,
                },
            }
        )
    if grain not in _CALENDAR_ALIGNMENT_SUPPORTED_GRAINS:
        return None

    current_window = _date_window_from_time_scope(request_time_scope)
    policy = get_calendar_policy(policy_ref)
    baseline_window = resolve_calendar_baseline_window(
        current_window=current_window,
        rule=policy.resolved_baseline_generation_rule,
    )
    calendar_data = _read_calendar_alignment_data(
        current_window=current_window,
        baseline_window=baseline_window,
        semantic_context=semantic_context,
    )
    pairing_resolution = resolve_calendar_bucket_pairing(
        current_window=current_window,
        baseline_window=baseline_window,
        matching_strategy=policy.matching_strategy,
        fallback_strategy=policy.fallback_strategy,
        annotation_rows=calendar_data.annotation_rows,
    )
    bucket_pairing = pairing_resolution.bucket_pairing
    comparability_warnings = pairing_resolution.comparability_warnings
    coverage_summary = _build_calendar_alignment_coverage(bucket_pairing)
    return {
        "policy_ref": policy.policy_ref,
        "comparison_basis": policy.comparison_basis,
        "resolved_calendar_source": calendar_data.resolved_calendar_source,
        "resolved_calendar_version": calendar_data.resolved_calendar_version,
        "resolved_baseline_generation_rule": {
            "strategy": policy.resolved_baseline_generation_rule.strategy,
            "offset_value": policy.resolved_baseline_generation_rule.offset_value,
            "offset_unit": policy.resolved_baseline_generation_rule.offset_unit,
            "fixed_start": None,
            "fixed_end": None,
            "named_window_ref": None,
        },
        "current_window": _serialize_calendar_window(current_window),
        "baseline_window": _serialize_calendar_window(baseline_window),
        "bucket_pairing": bucket_pairing,
        "rollup_safe": pairing_resolution.rollup_safe,
        "coverage_summary": coverage_summary,
        "comparability_warnings": comparability_warnings,
        "source_lineage": calendar_data.source_lineage,
    }


def _date_window_from_time_scope(time_scope: Mapping[str, Any]) -> tuple[date, date]:
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


def _read_calendar_alignment_data(
    *,
    current_window: tuple[date, date],
    baseline_window: tuple[date, date],
    semantic_context: Mapping[str, Any],
) -> CalendarDataReadResult:
    reader = semantic_context.get("calendar_data_reader")
    if not isinstance(reader, CalendarDataReaderLike):
        raise SemanticRequestCompatibilityError(
            {
                "message": "Calendar alignment requires a configured calendar data reader",
                "code": "calendar_data_missing",
                "category": "compatibility",
                "issues": [
                    {
                        "code": "calendar_data_missing",
                        "message": (
                            "calendar_policy_ref requires a configured calendar snapshot reader; "
                            "temporary annotation snapshot injection is no longer supported"
                        ),
                        "details": {},
                    }
                ],
                "request_context": {
                    "current_window": _serialize_calendar_window(current_window),
                    "baseline_window": _serialize_calendar_window(baseline_window),
                },
            }
        )
    try:
        return reader.read_for_alignment(
            current_window=current_window,
            baseline_window=baseline_window,
        )
    except CalendarDataResolutionError as error:
        raise SemanticRequestCompatibilityError(
            {
                "message": str(error),
                "code": "calendar_data_missing",
                "category": "compatibility",
                "issues": [
                    {
                        "code": "calendar_data_missing",
                        "message": str(error),
                        "details": dict(error.details),
                    }
                ],
                "request_context": {
                    "current_window": _serialize_calendar_window(current_window),
                    "baseline_window": _serialize_calendar_window(baseline_window),
                },
            }
        ) from error


def _build_calendar_alignment_coverage(bucket_pairing: list[dict[str, Any]]) -> dict[str, Any]:
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


def _serialize_calendar_window(window: tuple[date, date] | None) -> dict[str, str] | None:
    if window is None:
        return None
    return {
        "start": window[0].isoformat(),
        "end": window[1].isoformat(),
    }


def _stable_plan_id(step: AnalysisStepIR, normalized_request: NormalizedCompilerRequest) -> str:
    raw = "|".join(
        [
            step.step_type,
            str(step.index),
            normalized_request.metric_ref or "",
            normalized_request.process_ref or "",
            normalized_request.table_name or "",
            ",".join(normalized_request.request_dimensions),
            normalized_request.request_result_mode or "",
        ]
    )
    digest = hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]
    return f"ir_plan.{step.step_type}.{step.index}.{digest}"


def _metric_snapshot(metric: ResolvedSemanticObject) -> MetricRefSnapshot:
    header = dict(metric.semantic_object.get("header") or {})
    snapshot: MetricRefSnapshot = {
        "metric_ref": metric.ref,
    }
    primary_time_ref = _optional_str(header.get("primary_time_ref"))
    observation_grain_ref = _optional_str(header.get("observation_grain_ref"))
    if primary_time_ref is not None:
        snapshot["resolved_primary_time_ref"] = primary_time_ref
    if observation_grain_ref is not None:
        snapshot["resolved_observation_grain_ref"] = observation_grain_ref
    return snapshot


def _process_snapshot(process: ResolvedSemanticObject) -> ProcessRefSnapshot:
    interface_contract = dict(process.semantic_object.get("interface_contract") or {})
    snapshot: ProcessRefSnapshot = {
        "process_ref": process.ref,
    }
    anchor_time_ref = _optional_str(interface_contract.get("anchor_time_ref"))
    if anchor_time_ref is not None:
        snapshot["resolved_anchor_time_ref"] = anchor_time_ref
    return snapshot


def _binding_snapshot(binding: ResolvedSemanticObject) -> BindingRefSnapshot:
    return {
        "binding_ref": binding.ref,
        "bound_object_ref": str(binding.semantic_object.get("bound_object_ref") or ""),
    }


def _intent_request_snapshot(
    normalized_request: NormalizedCompilerRequest,
    resolved_inputs: ResolvedCompilerInputs,
) -> IntentRequestSnapshot:
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
    if normalized_request.request_calendar_policy_ref is not None:
        snapshot["requested_calendar_policy_ref"] = normalized_request.request_calendar_policy_ref
    if resolved_inputs.resolved_filter_time is not None:
        snapshot["request_time_scope_ref"] = resolved_inputs.resolved_filter_time.ref
    if options:
        snapshot["request_options"] = options
    return snapshot


def _build_ir_inputs(
    normalized_request: NormalizedCompilerRequest,
    resolved_inputs: ResolvedCompilerInputs,
) -> IrInputSnapshot:
    input_snapshot: IrInputSnapshot = {
        "intent_request": _intent_request_snapshot(normalized_request, resolved_inputs),
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
    if resolved_inputs.resolved_bindings:
        input_snapshot["binding_refs"] = [
            binding.ref for binding in resolved_inputs.resolved_bindings
        ]
        input_snapshot["resolved_bindings"] = [
            _binding_snapshot(binding) for binding in resolved_inputs.resolved_bindings
        ]
    if resolved_inputs.resolved_metric is not None:
        input_snapshot["resolved_metric"] = _metric_snapshot(resolved_inputs.resolved_metric)
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
            _process_snapshot(process) for process in resolved_processes
        ]
    return input_snapshot


def _measurement_node(
    *,
    step: AnalysisStepIR,
    resolved_metric: ResolvedSemanticObject,
    resolved_bindings: list[ResolvedSemanticObject],
    output_binding: OutputBinding,
    resolved_inputs: ResolvedCompilerInputs | None = None,
    semantic_repository: Any | None = None,
    governance_repository: Any | None = None,
) -> MeasurementNode:
    header = dict(resolved_metric.semantic_object.get("header") or {})
    carrier_bindings: list[CarrierBinding] = []
    for binding in resolved_bindings:
        interface_contract = dict(binding.semantic_object.get("interface_contract") or {})
        surfaces = [
            str(field_binding.get("surface_ref") or "")
            for field_binding in interface_contract.get("field_bindings") or []
            if str(field_binding.get("surface_ref") or "").strip()
        ]
        for carrier_binding in interface_contract.get("carrier_bindings") or []:
            binding_payload: CarrierBinding = {
                "binding_ref": binding.ref,
            }
            source_object_ref = _optional_str(carrier_binding.get("source_object_ref"))
            carrier_locator = carrier_binding.get("carrier_locator")
            if source_object_ref is not None:
                binding_payload["source_object_ref"] = source_object_ref
            if isinstance(carrier_locator, dict):
                binding_payload["carrier_locator"] = dict(cast("dict[str, Any]", carrier_locator))
            elif isinstance(carrier_locator, str) and carrier_locator.strip():
                binding_payload["carrier_locator"] = carrier_locator.strip()
            if surfaces:
                binding_payload["consumed_surface_refs"] = sorted(set(surfaces))
            carrier_bindings.append(binding_payload)
    sample_kind = cast(
        "Literal['numeric', 'rate', 'binary', 'survival']",
        _optional_str(header.get("sample_kind")) or "numeric",
    )
    constraints = header.get("additivity_constraints") or {}
    dimension_policy = str(constraints.get("dimension_policy", "none"))
    time_axis_policy = str(constraints.get("time_axis_policy", "non_additive"))
    additive_dimensions = constraints.get("additive_dimensions")
    node: MeasurementNode = {
        "node_id": f"measurement:{step.index}",
        "node_type": "measurement",
        "metric_ref": resolved_metric.ref,
        "observed_entity_ref": _optional_str(header.get("observed_entity_ref")) or "",
        "observation_grain_ref": _optional_str(header.get("observation_grain_ref")) or "",
        "sample_kind": sample_kind,
        "value_semantics": _optional_str(header.get("value_semantics")) or "",
        "dimension_policy": dimension_policy,
        "time_axis_policy": time_axis_policy,
        "output_bindings": [output_binding],
    }
    if additive_dimensions is not None:
        node["additive_dimensions"] = additive_dimensions
    inferential_summary_mode = _optional_str(header.get("inferential_summary_mode"))
    if inferential_summary_mode is not None:
        node["inferential_summary_mode"] = inferential_summary_mode
    if carrier_bindings:
        node["carrier_bindings"] = carrier_bindings
    # Attach predicate filter lineage if repository is available
    if semantic_repository is not None and resolved_inputs is not None:
        from app.analysis_core.predicate_validator import (
            build_predicate_filter_lineage,
            collect_layered_predicate_refs,
        )

        layered_refs = collect_layered_predicate_refs(resolved_inputs, governance_repository)
        if layered_refs:
            node["predicate_filter_lineage"] = build_predicate_filter_lineage(layered_refs)
    return node


def _process_node(step: AnalysisStepIR, process: ResolvedSemanticObject) -> ProcessNode:
    interface_contract = dict(process.semantic_object.get("interface_contract") or {})
    node: ProcessNode = {
        "node_id": f"process:{step.index}:{process.ref}",
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


def _intent_node(
    *,
    step: AnalysisStepIR,
    normalized_request: NormalizedCompilerRequest,
    output_binding: OutputBinding,
    depends_on: list[str],
) -> IntentNode:
    node: IntentNode = {
        "node_id": f"intent:{step.index}",
        "node_type": "intent",
        "intent_kind": step.step_type,
        "intent_level": "root",
        "depends_on": depends_on,
        "output_bindings": [output_binding],
    }
    if normalized_request.request_dimensions:
        node["requested_dimensions"] = list(normalized_request.request_dimensions)
    if normalized_request.request_result_mode is not None:
        node["requested_result_mode"] = normalized_request.request_result_mode
    return node


def _build_ir_bundle(
    *,
    step: AnalysisStepIR,
    normalized_request: NormalizedCompilerRequest,
    resolved_inputs: ResolvedCompilerInputs,
    validation_result: Any,
    derived_state: Any,
    semantic_context: Mapping[str, Any] | None = None,
) -> IrBundle:
    plan_id = _stable_plan_id(step, normalized_request)
    artifact_id = f"artifact:{plan_id}:output"
    output_binding: OutputBinding = {
        "artifact_id": artifact_id,
        "artifact_kind": STEP_ARTIFACT_KINDS.get(step.step_type, "table"),
    }

    nodes: list[MeasurementNode | ProcessNode | IntentNode] = []
    depends_on: list[str] = []
    if resolved_inputs.resolved_metric is not None:
        measurement_node = _measurement_node(
            step=step,
            resolved_metric=resolved_inputs.resolved_metric,
            resolved_bindings=resolved_inputs.resolved_bindings,
            output_binding=output_binding,
            resolved_inputs=resolved_inputs,
            semantic_repository=semantic_context.get("semantic_repository")
            if semantic_context
            else None,
            governance_repository=semantic_context.get("governance_repository")
            if semantic_context
            else None,
        )
        nodes.append(measurement_node)
        depends_on.append(measurement_node["node_id"])
    for process in (
        resolved_inputs.resolved_process,
        resolved_inputs.resolved_left_process,
        resolved_inputs.resolved_right_process,
    ):
        if process is None:
            continue
        process_node = _process_node(step, process)
        nodes.append(process_node)
        depends_on.append(process_node["node_id"])
    intent_node = _intent_node(
        step=step,
        normalized_request=normalized_request,
        output_binding=output_binding,
        depends_on=depends_on,
    )
    nodes.append(intent_node)

    lineage = [
        {
            "source_artifact_id": upstream_ref,
            "relationship": "consumes",
        }
        for upstream_ref in normalized_request.upstream_refs
    ]
    artifact: IrArtifact = {
        "artifact_id": artifact_id,
        "artifact_kind": output_binding["artifact_kind"],
        "producer_node_id": intent_node["node_id"],
    }
    if normalized_request.metric_ref is not None:
        artifact["output_semantics_ref"] = normalized_request.metric_ref
    if normalized_request.request_result_mode is not None:
        artifact["result_mode"] = normalized_request.request_result_mode
    if lineage:
        artifact["lineage"] = cast("list[ArtifactLineageEntry]", lineage)

    header: IrPlanHeader = {
        "ir_version": "v1",
        "plan_id": plan_id,
        "plan_kind": "atomic",
        "root_intent_kind": step.step_type,
    }
    if normalized_request.request_result_mode is not None:
        header["result_mode"] = normalized_request.request_result_mode

    lowering_requirements = _build_lowering_requirements(
        step=step,
        normalized_request=normalized_request,
        resolved_inputs=resolved_inputs,
        intent_node_id=intent_node["node_id"],
    )
    validation_trace = _build_validation_trace(validation_result)
    resolved_calendar_alignment = _resolve_calendar_alignment_plan(
        normalized_request,
        semantic_context=semantic_context,
    )
    compile_report: CompileReport = {
        "validation_trace": validation_trace,
        "validation_summary": _build_validation_summary(validation_result, validation_trace),
        "lowering_requirements": lowering_requirements,
    }
    if resolved_calendar_alignment is not None:
        compile_report["resolved_calendar_alignment"] = resolved_calendar_alignment
    profile_usage_trace = _build_profile_usage_trace(derived_state.profile_traces)
    if profile_usage_trace:
        compile_report["profile_usage_trace"] = profile_usage_trace
    if derived_state.usage_trace:
        compile_report["compiler_usage_trace"] = list(derived_state.usage_trace)

    plan: IrPlan = {
        "header": header,
        "inputs": _build_ir_inputs(normalized_request, resolved_inputs),
        "artifacts": [artifact],
        "nodes": nodes,
    }
    return {
        "plan": plan,
        "compile_report": compile_report,
    }


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def compile_step(
    step: AnalysisStepIR,
    *,
    engine_type: str,
    semantic_context: dict[str, Any] | None = None,
) -> CompiledQuery:
    """Compile a step IR into an engine-agnostic query artifact."""

    semantic_context = semantic_context or {}
    semantic_repository = semantic_context.get("semantic_repository")
    binding_reader = semantic_context.get("binding_reader")
    compatibility_profile_reader = semantic_context.get("compatibility_profile_reader")
    if semantic_repository is not None and not isinstance(
        semantic_repository, SemanticRuntimeRepository
    ):
        raise ValueError("semantic_context.semantic_repository must be a SemanticRuntimeRepository")
    normalized_request = normalize_step_request(step, semantic_context=semantic_context)
    resolved_inputs = resolve_compiler_inputs(
        normalized_request,
        semantic_repository=semantic_repository,
        binding_reader=binding_reader,
    )
    derived_state = derive_compiler_state(
        intent_kind=step.step_type,
        resolved_metric=resolved_inputs.resolved_metric,
        resolved_process=resolved_inputs.resolved_process,
        resolved_bindings=resolved_inputs.resolved_bindings,
        profile_reader=compatibility_profile_reader,
    )
    validation_result = validate_compiler_inputs(
        step_type=step.step_type,
        resolved_inputs=resolved_inputs,
        derived_state=derived_state,
        semantic_repository=semantic_repository,
        governance_repository=semantic_context.get("governance_repository"),
    )
    imported_dimension_sources, imported_dimension_issues = (
        _resolve_imported_dimension_physical_sources(
            resolved_inputs,
            semantic_repository=semantic_repository,
        )
    )
    if imported_dimension_issues:
        compatibility_issues = [
            issue for issue in imported_dimension_issues if issue.category == "compatibility"
        ]
        if compatibility_issues and len(compatibility_issues) == len(imported_dimension_issues):
            request_context = {
                "step_type": step.step_type,
                "intent_kind": normalized_request.intent_kind,
                "metric_ref": normalized_request.metric_ref,
                "process_ref": normalized_request.process_ref,
                "dimension_refs": list(normalized_request.request_dimensions),
            }
            request_context = {
                key: value for key, value in request_context.items() if value not in (None, [])
            }
            raise SemanticRequestCompatibilityError(
                {
                    "message": "Request is incompatible with resolved semantic objects",
                    "code": "semantic_request_incompatible",
                    "category": "compatibility",
                    "subject_ref": compatibility_issues[0].subject_ref,
                    "issues": [issue.to_dict() for issue in compatibility_issues],
                    "request_context": request_context,
                }
            )
        compile_error: dict[str, Any] = {
            "error_code": imported_dimension_issues[0].code,
            "failed_gate": imported_dimension_issues[0].gate,
            "message": imported_dimension_issues[0].message,
        }
        if imported_dimension_issues[0].subject_ref is not None:
            compile_error["subject_ref"] = imported_dimension_issues[0].subject_ref
        if imported_dimension_issues[0].details:
            compile_error["details"] = dict(imported_dimension_issues[0].details)
        raise SemanticCompilerError(cast("SemanticCompileError", compile_error))
    if not validation_result.ok:
        compatibility_issues = validation_result.issues_for_category("compatibility")
        non_compatibility_issues = [
            issue for issue in validation_result.error_issues() if issue.category != "compatibility"
        ]
        if compatibility_issues and not non_compatibility_issues:
            raise SemanticRequestCompatibilityError(
                _build_request_compatibility_error(
                    step_type=step.step_type,
                    normalized_request=normalized_request,
                    resolved_inputs=resolved_inputs,
                    validation_result=validation_result,
                )
            )
        raise SemanticCompilerError(
            _build_compile_error(validation_error_message(validation_result), validation_result)
        )
    ir_bundle = _build_ir_bundle(
        step=step,
        normalized_request=normalized_request,
        resolved_inputs=resolved_inputs,
        validation_result=validation_result,
        derived_state=derived_state,
        semantic_context=semantic_context,
    )
    assert_no_canonical_refs_in_semantic_payload(ir_bundle, surface="compiler_ir_bundle")
    params = dict(step.params)
    metadata = {
        "engine_type": engine_type,
        "step_type": step.step_type,
        "ir_plan_id": ir_bundle["plan"]["header"]["plan_id"],
        "normalized_request_class": normalized_request.request_class,
        "resolved_metric_ref": resolved_inputs.resolved_metric.ref
        if resolved_inputs.resolved_metric is not None
        else None,
        "resolved_process_ref": resolved_inputs.resolved_process.ref
        if resolved_inputs.resolved_process is not None
        else None,
        "resolved_filter_time_ref": resolved_inputs.resolved_filter_time.ref
        if resolved_inputs.resolved_filter_time is not None
        else None,
        "resolved_dimension_refs": resolved_inputs.resolved_dimension_refs,
        "resolved_binding_refs": [binding.ref for binding in resolved_inputs.resolved_bindings],
        "metric_entity_anchor_ref": resolved_inputs.metric_entity_anchor_ref,
        "resolved_imported_dimensions": [
            {
                "dimension_ref": bridge.dimension_ref,
                "source_binding_ref": bridge.source_binding_ref,
                "source_entity_ref": bridge.source_entity_ref,
                "import_key": bridge.import_key,
            }
            for bridge in resolved_inputs.resolved_imported_dimensions
        ],
        "imported_dimension_conflicts": {
            dimension_ref: [
                {
                    "dimension_ref": bridge.dimension_ref,
                    "source_binding_ref": bridge.source_binding_ref,
                    "source_entity_ref": bridge.source_entity_ref,
                    "import_key": bridge.import_key,
                }
                for bridge in bridges
            ]
            for dimension_ref, bridges in resolved_inputs.imported_dimension_conflicts.items()
        },
        "resolved_imported_dimension_sources": imported_dimension_sources,
        "compiler_summary": ir_bundle["compile_report"]["validation_summary"],
        "resolved_calendar_alignment": ir_bundle["compile_report"].get(
            "resolved_calendar_alignment"
        ),
    }
    assert_no_canonical_refs_in_semantic_payload(metadata, surface="compiler_metadata")
    table_name: str | None = None
    compiled_params: list[Any] = []

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
            ir_bundle=ir_bundle,
        )

    if step.step_type == "profile_table_row_count":
        table_name = _require_param(step, "table_name")
        return CompiledQuery(
            sql=f"SELECT COUNT(*) AS row_count FROM {table_name}",
            metadata={**metadata, "table_name": table_name},
            ir_bundle=ir_bundle,
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
            ir_bundle=ir_bundle,
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
            ir_bundle=ir_bundle,
        )

    if step.step_type == "metric_query":
        table_name = step.table_name()
        if table_name is None:
            raise ValueError("metric_query requires 'table' or 'table_name' param")
        metric_name = (
            step.primary_metric_name()
            or params.get("metric")
            or _require_param(step, "metric_name")
        )
        metric_sql = semantic_context.get("metric_sql")
        dimensions = semantic_context.get("dimensions")
        if metric_sql is None or dimensions is None:
            raise ValueError(
                "metric_query compilation requires semantic_context with 'metric_sql' and 'dimensions'"
            )
        limit = int(params.get("limit", 10))
        order_param = params.get("order")
        scoped_query = params.get("scoped_query")
        mode = "compare"
        if isinstance(scoped_query, Mapping):
            mode = _require_scoped_query_mode(scoped_query)
        default_order = "CURRENT_VALUE DESC" if mode == "single_window" else "DELTA_PCT ASC"
        order = str(order_param or default_order).upper()
        _normalize_metric_query_order(order, mode=mode)
        sql = build_metric_query(
            metric_name=metric_name,
            table_name=table_name,
            metric_sql=str(metric_sql),
            dimensions=list(dimensions),
            dimension_sql_expressions=_metric_query_dimension_sql_expressions(
                list(dimensions),
                imported_dimension_sources,
            ),
            order=order,
            limit=limit,
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
            ir_bundle=ir_bundle,
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
        scoped_query_m: Mapping[str, Any] | None = scoped_query if has_scoped_query else None
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
            compiled_params = []
            compare_period = (
                scoped_query_m is not None and str(scoped_query_m.get("mode") or "") == "compare"
            )
            if scoped_query_m is not None:
                compiled_params = _build_scoped_query_parts(
                    table_name,
                    scoped_query_m,
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
                ir_bundle=ir_bundle,
            )

        select_exprs = params.get("select")
        if not select_exprs or not isinstance(select_exprs, list):
            raise ValueError("aggregate_query requires 'select' param (list of expressions)")
        where = params.get("where")

        if params.get("compare_period") or (
            scoped_query_m is not None and str(scoped_query_m.get("mode") or "") == "compare"
        ):
            date_column = str(params.get("date_column", "event_date"))
            sql = build_aggregate_comparison_query(
                table_name=table_name,
                select_exprs=list(select_exprs),
                group_by=list(group_by),
                date_column=date_column,
                order_by=order_by,
                limit=limit,
                filter_expr=str(where) if where else None,
                scoped_query=scoped_query_m,
            )
            compiled_params = list(semantic_context.get("period_params", []))
            if scoped_query_m is not None:
                compiled_params = _build_scoped_query_parts(
                    table_name,
                    scoped_query_m,
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
                ir_bundle=ir_bundle,
            )

        select_clause = ", ".join(select_exprs)
        expanded_group_by = _expand_group_by_aliases(list(select_exprs), list(group_by))
        group_clause = f" GROUP BY {', '.join(expanded_group_by)}" if expanded_group_by else ""
        order_clause = f" ORDER BY {order_by}" if order_by else ""
        compiled_params = []

        if scoped_query_m is not None:
            scoped = _build_scoped_query_parts(
                table_name,
                scoped_query_m,
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
            ir_bundle=ir_bundle,
        )

    raise ValueError(f"Unsupported compilation step type: {step.step_type}")


def _require_param(step: AnalysisStepIR, name: str) -> str:
    value = step.params.get(name)
    if value in (None, ""):
        raise ValueError(f"{step.step_type} requires '{name}' param")
    return str(value)
