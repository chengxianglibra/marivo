"""Pure typed resolution data classes and helpers.

Extracted from ``app.analysis_core.typed_resolution`` as part of Phase 3c.

This module contains only pure computation:
- Data classes for normalized requests and resolved inputs
- Pure helper functions for ref normalization, string processing, and
  entity field / predicate extraction
- Pure entity composition builder
- Pure entity field snapshot builder

Deferred (requires I/O via semantic_repository or conditional imports):
- ``normalize_step_request``: reads step params and calls
  ``normalize_metric_query_request``, ``normalize_aggregate_query_request``,
  ``validate_calendar_policy_ref``, and conditionally imports
  ``_normalize_time_scope``.
- ``resolve_compiler_inputs``: calls ``semantic_repository.resolve_*`` methods.
- ``_resolve_runtime_ref``: calls the resolver callback.
- ``_resolved_filter_time_ref``: reads from resolved objects (pure data) but
  only used inside resolve_compiler_inputs.
- ``_resolve_imported_dimension_bridges``: accepts repository but currently
  only extracts from metric header.
- ``_resolve_entity_field_groundings``: calls repository to resolve entities.
- ``_collect_entity_field_usages``: calls ``_resolve_predicates_for_field_usage``.
- ``_resolve_predicates_for_field_usage``: calls repository to resolve predicates.
- ``_metric_predicate_refs``: pure data extraction but only used by I/O functions.
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
    request_calendar_policy_ref: str | None = None
    request_options: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ResolvedImportedDimensionBridge:
    dimension_ref: str
    source_binding_ref: str
    source_entity_ref: str
    import_key: str


@dataclass(slots=True)
class ResolvedEntityField:
    field_ref: str
    entity_ref: str
    local_field_ref: str
    entity_revision: int
    value_type: str | None = None
    nullable: bool | None = None
    unit: str | None = None
    enum_hint: str | None = None
    profile_summary: dict[str, Any] | None = None
    sensitivity_tags: list[str] = field(default_factory=list)
    physical_column: str | None = None
    physical_expression_locator: dict[str, Any] | None = None
    source_object_ref: str | None = None
    source_object_fqn: str | None = None
    carrier_kind: str | None = None
    usage_paths: list[str] = field(default_factory=list)


@dataclass(slots=True)
class FieldResolutionIssue:
    code: str
    field_ref: str
    message: str
    usage_path: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EntityComposition:
    anchor_entity_ref: str | None
    component_entity_refs: list[str] = field(default_factory=list)
    all_entity_refs: list[str] = field(default_factory=list)
    is_cross_entity: bool = False


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
    metric_entity_anchor_ref: str | None = None
    resolved_imported_dimensions: list[ResolvedImportedDimensionBridge] = field(
        default_factory=list
    )
    imported_dimension_conflicts: dict[str, list[ResolvedImportedDimensionBridge]] = field(
        default_factory=dict
    )
    resolved_entity_fields: dict[str, ResolvedEntityField] = field(default_factory=dict)
    entity_field_usage_details: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    field_resolution_issues: list[FieldResolutionIssue] = field(default_factory=list)
    entity_composition: EntityComposition = field(default_factory=lambda: EntityComposition(None))
    resolved_relationships: dict[str, ResolvedRelationship] = field(default_factory=dict)
    warnings: list[dict[str, Any]] = field(default_factory=list)

    @property
    def resolved_dimension_refs(self) -> list[str]:
        return [dimension.ref for dimension in self.resolved_dimensions]

    @property
    def resolved_imported_dimension_refs(self) -> list[str]:
        return [dimension.dimension_ref for dimension in self.resolved_imported_dimensions]

    @property
    def resolved_entity_field_refs(self) -> list[str]:
        return sorted(self.resolved_entity_fields)


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


def metric_entity_anchor_ref(metric: Any) -> str | None:
    """Extract the entity anchor ref from a resolved metric's header.

    *metric* must have ``.semantic_object`` (dict-like with ``header``).
    """
    header = dict(metric.semantic_object.get("header") or {})
    observed_entity_ref = _optional_str(header.get("observed_entity_ref"))
    if observed_entity_ref is not None:
        return observed_entity_ref
    return _optional_str(header.get("population_subject_ref"))


def metric_component_items(payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Extract (component_name, component_dict) pairs from a metric payload."""
    items: list[tuple[str, dict[str, Any]]] = []
    for component_name in (
        "count_target",
        "measure",
        "numerator",
        "denominator",
        "value_component",
        "score_source",
    ):
        component = payload.get(component_name)
        if isinstance(component, dict):
            items.append((component_name, component))
    return items


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


# ── Pure entity field helpers ──────────────────────────────────────────


def collect_entity_field_refs_from_value(value: Any) -> list[str]:
    """Recursively collect entity field refs from a nested value structure."""
    refs: list[str] = []
    if isinstance(value, dict):
        for nested in value.values():
            refs.extend(collect_entity_field_refs_from_value(nested))
    elif isinstance(value, list):
        for nested in value:
            refs.extend(collect_entity_field_refs_from_value(nested))
    elif isinstance(value, str) and normalize_entity_field_ref(value) is not None:
        refs.append(value)
    return refs


def build_entity_composition(
    resolved: Any,
    field_usages: dict[str, list[str]],
) -> EntityComposition:
    """Build an EntityComposition from resolved inputs and field usages.

    *resolved* must have ``.resolved_metric`` (with ``.semantic_object``).
    *field_usages* maps field_ref -> list of usage_paths.
    """
    anchor_entity_ref = None
    if resolved.resolved_metric is not None:
        anchor_entity_ref = metric_entity_anchor_ref(resolved.resolved_metric)
    component_entity_refs: list[str] = []
    all_entity_refs: list[str] = []
    seen_components: set[str] = set()
    seen_all: set[str] = set()
    for field_ref, usage_paths in field_usages.items():
        entity_ref, _local = split_entity_field_ref(field_ref)
        if entity_ref is None:
            continue
        if entity_ref not in seen_all:
            all_entity_refs.append(entity_ref)
            seen_all.add(entity_ref)
        if entity_ref not in seen_components and any(
            path.startswith("metric.") and path.endswith(".input_field_ref") for path in usage_paths
        ):
            component_entity_refs.append(entity_ref)
            seen_components.add(entity_ref)
    if anchor_entity_ref is not None and anchor_entity_ref not in seen_all:
        all_entity_refs.append(anchor_entity_ref)
        seen_all.add(anchor_entity_ref)
    return EntityComposition(
        anchor_entity_ref=anchor_entity_ref,
        component_entity_refs=sorted(component_entity_refs),
        all_entity_refs=sorted(all_entity_refs),
        is_cross_entity=len(seen_all) > 1,
    )


def entity_field_snapshot(
    field_ref: str,
    *,
    local_field_ref: str,
    entity: Any,
    usage_paths: list[str],
) -> ResolvedEntityField | None:
    """Build a ResolvedEntityField from a resolved entity.

    *entity* must have ``.ref``, ``.revision``, ``.semantic_object``
    (dict-like with ``interface_contract``).
    Returns None if the field is not found in the entity's interface contract.
    """
    interface_contract = dict(entity.semantic_object.get("interface_contract") or {})
    field = None
    for candidate in interface_contract.get("fields") or []:
        if (
            isinstance(candidate, dict)
            and _optional_str(candidate.get("field_ref")) == local_field_ref
        ):
            field = dict(candidate)
            break
    if field is None:
        return None
    binding = dict(interface_contract.get("binding") or {})
    entity_ref = entity.ref
    return ResolvedEntityField(
        field_ref=field_ref,
        entity_ref=entity_ref,
        local_field_ref=local_field_ref,
        entity_revision=entity.revision,
        value_type=_optional_str(field.get("value_type")),
        nullable=_optional_boolish(field.get("nullable")),
        unit=_optional_str(field.get("unit")),
        enum_hint=_optional_str(field.get("enum_hint")),
        profile_summary=dict(field.get("profile_summary") or {})
        if isinstance(field.get("profile_summary"), dict)
        else None,
        sensitivity_tags=[str(tag) for tag in field.get("sensitivity_tags") or []],
        physical_column=_optional_str(field.get("physical_column")),
        physical_expression_locator=dict(field.get("physical_expression_locator") or {})
        if isinstance(field.get("physical_expression_locator"), dict)
        else None,
        source_object_ref=_optional_str(binding.get("source_object_ref")),
        source_object_fqn=_optional_str(binding.get("source_object_fqn")),
        carrier_kind=_optional_str(binding.get("carrier_kind")),
        usage_paths=list(usage_paths),
    )


def normalize_entity_field_ref(value: str | None) -> str | None:
    """Normalize a string to an entity field ref, or return None."""
    text = _optional_str(value)
    if text is None:
        return None
    if text.startswith("entity.") and ".field." in text:
        return text
    if text.startswith("field."):
        return text
    return None


def split_entity_field_ref(value: str) -> tuple[str | None, str]:
    """Split an entity field ref into (entity_ref, local_field_ref).

    For ``entity.user.field.name`` returns ``("entity.user", "field.name")``.
    For ``field.name`` returns ``(None, "field.name")``.
    """
    if value.startswith("field."):
        return None, value
    if value.startswith("entity.") and ".field." in value:
        entity_ref, field_name = value.split(".field.", 1)
        return entity_ref, f"field.{field_name}"
    return None, value


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
