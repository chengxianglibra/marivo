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
