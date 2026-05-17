from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

INTENT_TAXONOMY: dict[str, dict[str, str]] = {
    "observe": {
        "category": "atomic",
        "description": (
            "Read a typed observation for a semantic metric. "
            "Params: metric, time_scope (required), result_mode, scope, granularity, dimensions."
        ),
    },
    "compare": {
        "category": "atomic",
        "description": (
            "Compute a typed delta between two observations. "
            "Params: left_artifact_id, right_artifact_id, compare_type."
        ),
    },
    "decompose": {
        "category": "atomic",
        "description": (
            "Attribute a delta across candidate dimensions. "
            "Params: compare_artifact_id, dimension, limit."
        ),
    },
    "correlate": {
        "category": "atomic",
        "description": (
            "Estimate statistical association between two time-series. "
            "Params: left_artifact_id, right_artifact_id, method."
        ),
    },
    "detect": {
        "category": "atomic",
        "description": (
            "Scan a metric time range for ranked anomaly candidates. "
            "Params: metric, time_scope (required), scope, sensitivity, max_series."
        ),
    },
    "test": {
        "category": "atomic",
        "description": (
            "Evaluate a typed statistical hypothesis. "
            "Params: metric, left, right, kind, hypothesis."
        ),
    },
    "forecast": {
        "category": "atomic",
        "description": (
            "Project a time-series into future buckets. "
            "Params: source_artifact_id, horizon, profile."
        ),
    },
    "validate": {
        "category": "derived",
        "description": (
            "Derived intent: validate a metric hypothesis. "
            "Expands to a fixed-family statistical test and returns a validation bundle. "
            "Params: metric, left, right, hypothesis."
        ),
    },
    "attribute": {
        "category": "derived",
        "description": (
            "Derived intent: attribute a metric change. "
            "Expands to observe + observe + compare + decompose. "
            "Params: metric, current_time_scope, baseline_time_scope, candidate_dimensions, top_k, min_contribution_pct."
        ),
    },
    "diagnose": {
        "category": "derived",
        "description": (
            "Derived intent: diagnose anomalies in a metric. "
            "Expands to detect + compare + decompose on top-K candidates. "
            "Params: metric, time_scope, scope, sensitivity, top_k_candidates."
        ),
    },
}

ATOMIC_INTENT_TYPES: tuple[str, ...] = tuple(
    k for k, v in INTENT_TAXONOMY.items() if v["category"] == "atomic"
)
DERIVED_INTENT_TYPES: tuple[str, ...] = tuple(
    k for k, v in INTENT_TAXONOMY.items() if v["category"] == "derived"
)
SUPPORTED_INTENT_TYPES: tuple[str, ...] = ATOMIC_INTENT_TYPES + DERIVED_INTENT_TYPES


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


# ── Pure step helpers ───────────────────────────────────────────────────


def new_step_id() -> str:
    """Generate a new unique step ID."""
    return f"step_{uuid4().hex[:12]}"


def make_provenance(
    sql: str = "",
    params: list[Any] | None = None,
    engine_type: str = "duckdb",
    routing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a provenance token for a step execution.

    *routing* is an optional dict of routing feedback context.  When the
    caller is the service layer, it passes ``self._routing_feedback_context``
    so that the routing metadata is preserved in the provenance record.
    """
    query_hash = hashlib.sha256(sql.encode()).hexdigest()[:16] if sql else ""
    provenance: dict[str, Any] = {
        "query_hash": query_hash,
        "engine": engine_type,
        "timestamp": datetime.now(UTC).isoformat(),
        "param_count": len(params) if params else 0,
    }
    if routing:
        provenance["routing"] = dict(routing)
    return provenance
