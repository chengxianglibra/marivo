"""Pure typed resolution data classes and helpers.

Extracted from ``marivo.analysis_core.typed_resolution`` as part of Phase 3c.

This module contains only pure computation:
- Data classes for normalized requests and resolved inputs
- Pure helper functions for ref normalization, string processing, and
  entity field / predicate extraction
- Pure entity composition builder
- Pure entity field snapshot builder

Deferred (requires I/O via semantic_repository or conditional imports):
- ``normalize_step_request``: reads step params and calls
  ``normalize_metric_query_request``, ``normalize_aggregate_query_request``,
  and conditionally imports ``_normalize_time_scope``.
- ``resolve_compiler_inputs``: calls ``semantic_repository.resolve_*`` methods.
- ``_resolve_runtime_ref``: calls the resolver callback.
- ``_resolved_filter_time_ref``: reads from resolved objects (pure data) but
  only used inside resolve_compiler_inputs.
- ``_request_options_from_windowed_request``: calls ``asdict()`` on a request
  object; simple but tightly coupled to time_scope module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# ── Data classes ─────────────────────────────────────────────────────────


RequestClass = Literal["root_metric_process", "typed_ref", "derived_macro"]


@dataclass(slots=True)
class NormalizedCompilerRequest:
    intent_kind: str
    request_class: RequestClass
    table_name: str | None
    metric_ref: str | None = None
    process_ref: str | None = None
    left_process_ref: str | None = None
    right_process_ref: str | None = None
    upstream_refs: list[str] = field(default_factory=list)
    request_scope: dict[str, Any] | None = None
    request_scope_predicate_ref: str | None = None
    request_time_scope: dict[str, Any] | None = None
    request_dimensions: list[str] = field(default_factory=list)
    request_result_mode: str | None = None
    request_options: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ResolvedRelationship:
    relationship_ref: str
    left_entity_ref: str
    right_entity_ref: str
    key_alignment: dict[str, Any] = field(default_factory=dict)
    time_alignment: dict[str, Any] | None = None
    cardinality: str | None = None
    grain_compatibility: dict[str, Any] | None = None
    snapshot_effective_window_alignment: dict[str, Any] | None = None
    revision: int | None = None


@dataclass(slots=True)
class ResolvedCompilerInputs:
    normalized_request: NormalizedCompilerRequest
    resolved_metric: Any | None = None
    resolved_process: Any | None = None
    resolved_left_process: Any | None = None
    resolved_right_process: Any | None = None
    resolved_filter_time: Any | None = None
    resolved_dimensions: list[Any] = field(default_factory=list)
    resolved_relationships: dict[str, ResolvedRelationship] = field(default_factory=dict)
    warnings: list[dict[str, Any]] = field(default_factory=list)

    @property
    def resolved_dimension_refs(self) -> list[str]:
        return [dimension.ref for dimension in self.resolved_dimensions]


# ── Pure ref normalization ──────────────────────────────────────────────

# Minimal inline version of runtime_ref_kind to avoid core -> semantic_runtime
# cross-boundary import.  The canonical source is
# app.semantic_runtime.semantic_metadata.SUPPORTED_RUNTIME_REF_KINDS.
_REF_KIND_PREFIXES: dict[str, str] = {
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


def _ref_kind(semantic_ref: str) -> str | None:
    """Return the ref kind for a semantic ref (e.g. 'metric' for 'metric.revenue')."""
    normalized = semantic_ref.strip()
    for prefix, kind in _REF_KIND_PREFIXES.items():
        if normalized.startswith(prefix):
            return kind
    return None


def normalize_metric_ref(metric_name: str) -> str:
    """Normalize a metric name to a full metric ref.

    If the name already starts with ``metric.``, it is returned as-is.
    Otherwise, ``metric.`` is prepended.
    """
    normalized = metric_name.strip()
    if _ref_kind(normalized) == "metric":
        return normalized
    return f"metric.{normalized}"


def normalize_dimension_refs(dimensions: list[str]) -> list[str]:
    """Normalize and deduplicate a list of dimension references.

    Raises ValueError if any ref has a non-dimension ref kind (e.g. ``metric.``).
    """
    normalized: list[str] = []
    seen: set[str] = set()
    for dimension in dimensions:
        candidate = dimension.strip()
        if not candidate:
            continue
        ref_kind = _ref_kind(candidate)
        if ref_kind is not None and ref_kind != "dimension":
            raise ValueError(f"Invalid dimension ref: {dimension}")
        if candidate not in seen:
            normalized.append(candidate)
            seen.add(candidate)
    return normalized


# ── Pure string / value helpers ─────────────────────────────────────────


def mapping_dict(value: Any) -> dict[str, Any] | None:
    """Convert a Mapping to a dict, or return None."""
    from collections.abc import Mapping as MappingABC

    if not isinstance(value, MappingABC):
        return None
    return dict(value)


def string_list(value: Any) -> list[str]:
    """Normalize a value to a deduplicated list of non-empty strings."""
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        item_text = str(item or "").strip()
        if not item_text or item_text in seen:
            continue
        normalized.append(item_text)
        seen.add(item_text)
    return normalized


def optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def filter_none_dict(**values: Any) -> dict[str, Any]:
    """Return a dict with None values removed."""
    return {key: value for key, value in values.items() if value is not None}


# ── Pure metric helpers ────────────────────────────────────────────────

# ── Pure predicate helpers ─────────────────────────────────────────────


def predicate_atoms(expression: dict[str, Any]) -> list[dict[str, Any]]:
    """Recursively extract atomic predicate expressions from a nested expression."""
    if expression.get("target_ref") is not None:
        return [expression]
    atoms: list[dict[str, Any]] = []
    for item in expression.get("items") or []:
        if isinstance(item, dict):
            atoms.extend(predicate_atoms(item))
    return atoms


# ── Private helpers ─────────────────────────────────────────────────────


def _optional_str(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _optional_boolish(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return bool(value)
