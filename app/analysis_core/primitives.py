from __future__ import annotations

STEP_TAXONOMY = {
    "compare_metric": {
        "category": "primitive",
        "description": "Compare a published semantic metric between baseline and current windows.",
    },
    "profile_table": {
        "category": "primitive",
        "description": "Profile table row count and column-level completeness/cardinality signals.",
    },
    "sample_rows": {
        "category": "primitive",
        "description": "Return a bounded sample of rows from a table.",
    },
    "compare_watch_time": {
        "category": "composite",
        "description": "Domain comparison step focused on watch-time change analysis.",
    },
    "analyze_qoe": {
        "category": "composite",
        "description": "Domain QoE analysis step over player-quality signals.",
    },
    "analyze_ads": {
        "category": "composite",
        "description": "Domain ad-regression analysis step over preroll timeout signals.",
    },
    "analyze_recommendation": {
        "category": "composite",
        "description": "Domain recommendation-quality analysis step over CTR signals.",
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
OPTIONAL_STEP_TYPES = ("analyze_ads", "analyze_recommendation")


def step_category_for(step_type: str) -> str:
    return str(STEP_TAXONOMY.get(step_type, {}).get("category", "primitive"))


def is_optional_step(step_type: str) -> bool:
    return step_type in OPTIONAL_STEP_TYPES

