"""Legacy semantic_runtime.semantic_metadata stubs — preserved for import compatibility."""

from __future__ import annotations

SUPPORTED_RUNTIME_REF_KINDS: dict[str, str] = {
    "entity.": "entity",
    "metric.": "metric",
    "process.": "process",
    "dimension.": "dimension",
    "time.": "time",
    "binding.": "binding",
    "relationship.": "relationship",
    "calendar_policy.": "calendar_policy",
    "predicate.": "predicate",
}


def runtime_ref_kind(semantic_ref: str) -> str | None:
    normalized_ref = semantic_ref.strip()
    for prefix, kind in SUPPORTED_RUNTIME_REF_KINDS.items():
        if normalized_ref.startswith(prefix):
            return kind
    return None
