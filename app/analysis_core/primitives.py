from __future__ import annotations

STEP_TAXONOMY = {
    "compare_metric": {
        "category": "primitive",
        "description": (
            "Compare a published semantic metric between baseline and current windows. "
            "Params: metric_name, table_name, period_end (required), period_start (optional, defaults to period_end for single-day), "
            "comparison_type (dod|wow|mom|yoy — sets baseline automatically), "
            "baseline_start/baseline_end (explicit override, takes priority over comparison_type), "
            "dimensions, order (ASC|DESC), limit. "
            "Unequal windows produce a warning in summary/debug but are not rejected."
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
        "description": "Run an ad-hoc GROUP BY + aggregation query on a table.",
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
