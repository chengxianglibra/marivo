from __future__ import annotations

STEP_TAXONOMY = {
    "metric_query": {
        "category": "primitive",
        "description": (
            "Query a published semantic metric across typed time windows. "
            "Params: table, metric, time_scope (required), dimensions, scope, time_axis, order, limit. "
            "time_scope is the only time-window contract; scope is the only non-time row/entity scope; "
            "legacy params metric_name, table_name, period_start, period_end, baseline_start, baseline_end, "
            "comparison_type, date_column, where, and filter are no longer part of the public contract."
        ),
    },
    "profile_table": {
        "category": "primitive",
        "description": "Profile table row count and column-level completeness/cardinality signals.",
    },
    "sample_rows": {
        "category": "primitive",
        "description": "Return a bounded sample of rows from a table.",
    },
    "aggregate_query": {
        "category": "primitive",
        "description": (
            "Run a window-aware typed aggregate query on a table. "
            "Params: table, measures, time_scope (required), group_by, scope, time_axis, order, limit. "
            "time_scope is the only time-window contract; scope is the only non-time row/entity scope; "
            "legacy params select, where, compare_period, and date_column are no longer part of the public contract."
        ),
    },
    "attribute_change": {
        "category": "primitive",
        "description": (
            "Attribute a metric change across candidate dimensions using current "
            "and baseline windows; produces contribution_shift observations."
        ),
    },
    "correlate_metrics": {
        "category": "primitive",
        "description": (
            "Compute Spearman (and optionally Pearson) correlation between two "
            "numeric series produced by prior steps; emits a correlation_result "
            "observation carrying rho, p_value, n, method, and observed_window."
        ),
    },
    "synthesize_findings": {
        "category": "composite",
        "description": "Workflow synthesis step that turns observations into claims and recommendations.",
    },
}

PRIMITIVE_STEP_TYPES = tuple(
    step_type
    for step_type, metadata in STEP_TAXONOMY.items()
    if metadata["category"] == "primitive"
)
COMPOSITE_STEP_TYPES = tuple(
    step_type
    for step_type, metadata in STEP_TAXONOMY.items()
    if metadata["category"] == "composite"
)
SUPPORTED_STEP_TYPES = COMPOSITE_STEP_TYPES + PRIMITIVE_STEP_TYPES


def step_category_for(step_type: str) -> str:
    return str(STEP_TAXONOMY.get(step_type, {}).get("category", "primitive"))
