"""Pure step semantic metadata builder.

Extracted from ``SemanticLayerService.build_step_semantic_metadata`` as part of
Phase 4b-1 (CoreEngine pure method migration).

This module contains only pure computation: it assembles a typed semantic
snapshot dict from the metadata carried by one or more CompiledQuery objects.
"""

from __future__ import annotations

from typing import Any

# ── Helpers ──────────────────────────────────────────────────────────────


def _merge_unique_str(values: list[str | None]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for value in values:
        if value is None or value in seen:
            continue
        seen.add(value)
        merged.append(value)
    return merged


def _build_calendar_policy_binding(
    resolved_calendar_alignments: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not resolved_calendar_alignments:
        return None

    def require_string(alignment: dict[str, Any], field: str) -> str:
        value = alignment.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError(f"resolved_calendar_alignment missing {field}")
        return value

    def require_source_lineage(alignment: dict[str, Any]) -> dict[str, str]:
        source_lineage = alignment.get("source_lineage")
        if not isinstance(source_lineage, dict) or not source_lineage:
            raise ValueError("resolved_calendar_alignment missing source_lineage metadata")
        normalized: dict[str, str] = {}
        for field in ("table_fqn", "calendar_version"):
            value = source_lineage.get(field)
            if not isinstance(value, str) or not value:
                raise ValueError(f"resolved_calendar_alignment source_lineage missing {field}")
            normalized[field] = value
        return normalized

    bindings: list[dict[str, Any]] = []
    for alignment in resolved_calendar_alignments:
        bindings.append(
            {
                "policy_ref": require_string(alignment, "policy_ref"),
                "comparison_basis": require_string(alignment, "comparison_basis"),
                "resolved_calendar_source": require_string(alignment, "resolved_calendar_source"),
                "resolved_calendar_version": require_string(alignment, "resolved_calendar_version"),
                "source_lineage": require_source_lineage(alignment),
            }
        )

    first_binding = bindings[0]
    for binding in bindings[1:]:
        if binding != first_binding:
            raise ValueError("conflicting calendar policy bindings in compiled step metadata")
    return first_binding


# ── Public API ───────────────────────────────────────────────────────────


def build_step_semantic_metadata(
    compiled_queries: Any,
) -> dict[str, Any] | None:
    """Assemble a typed semantic snapshot from one or more CompiledQuery objects.

    Each *compiled_queries* element must have a ``.metadata`` dict with keys
    such as ``resolved_metric_ref``, ``resolved_dimension_refs``, etc.

    Returns ``None`` if no meaningful metadata is found.
    """
    compiled_list = compiled_queries if isinstance(compiled_queries, list) else [compiled_queries]
    if not compiled_list:
        return None

    metric_refs = _merge_unique_str(
        [
            str(compiled.metadata.get("resolved_metric_ref"))
            if compiled.metadata.get("resolved_metric_ref")
            else None
            for compiled in compiled_list
        ]
    )
    metric_revisions = [
        int(compiled.metadata["resolved_metric_revision"])
        for compiled in compiled_list
        if compiled.metadata.get("resolved_metric_revision") is not None
    ]
    metric_object_ids = _merge_unique_str(
        [
            str(compiled.metadata.get("resolved_metric_object_id"))
            if compiled.metadata.get("resolved_metric_object_id")
            else None
            for compiled in compiled_list
        ]
    )
    process_refs = _merge_unique_str(
        [
            str(compiled.metadata.get("resolved_process_ref"))
            if compiled.metadata.get("resolved_process_ref")
            else None
            for compiled in compiled_list
        ]
    )
    filter_time_refs = _merge_unique_str(
        [
            str(compiled.metadata.get("resolved_filter_time_ref"))
            if compiled.metadata.get("resolved_filter_time_ref")
            else None
            for compiled in compiled_list
        ]
    )
    entity_field_refs = _merge_unique_str(
        [
            field_ref
            for compiled in compiled_list
            for field_ref in list(compiled.metadata.get("resolved_entity_field_refs") or [])
        ]
    )
    entity_field_sources = [
        dict(source)
        for compiled in compiled_list
        for source_list in [compiled.metadata.get("resolved_entity_field_sources")]
        if isinstance(source_list, list)
        for source in source_list
        if isinstance(source, dict)
    ]
    relationship_refs = _merge_unique_str(
        [
            relationship_ref
            for compiled in compiled_list
            for relationship_ref in list(compiled.metadata.get("resolved_relationship_refs") or [])
        ]
    )
    relationship_sources = [
        dict(source)
        for compiled in compiled_list
        for source_list in [compiled.metadata.get("resolved_relationship_sources")]
        if isinstance(source_list, list)
        for source in source_list
        if isinstance(source, dict)
    ]
    dimension_refs = _merge_unique_str(
        [
            dimension_ref
            for compiled in compiled_list
            for dimension_ref in list(compiled.metadata.get("resolved_dimension_refs") or [])
        ]
    )
    ir_plan_ids = _merge_unique_str(
        [
            str(compiled.metadata.get("ir_plan_id"))
            if compiled.metadata.get("ir_plan_id")
            else None
            for compiled in compiled_list
        ]
    )
    request_classes = _merge_unique_str(
        [
            str(compiled.metadata.get("normalized_request_class"))
            if compiled.metadata.get("normalized_request_class")
            else None
            for compiled in compiled_list
        ]
    )
    compiler_summaries = [
        dict(summary)
        for compiled in compiled_list
        for summary in [compiled.metadata.get("compiler_summary")]
        if isinstance(summary, dict)
    ]
    resolved_calendar_alignments = [
        dict(summary)
        for compiled in compiled_list
        for summary in [compiled.metadata.get("resolved_calendar_alignment")]
        if isinstance(summary, dict)
    ]
    imported_dimension_lineage = [
        dict(summary)
        for compiled in compiled_list
        for summary in [compiled.metadata.get("resolved_imported_dimensions")]
        if isinstance(summary, list)
        for summary in summary
        if isinstance(summary, dict)
    ]
    imported_dimension_conflicts = [
        {
            "dimension_ref": dimension_ref,
            "candidates": [
                dict(candidate) for candidate in candidates if isinstance(candidate, dict)
            ],
        }
        for compiled in compiled_list
        for conflict_map in [compiled.metadata.get("imported_dimension_conflicts")]
        if isinstance(conflict_map, dict)
        for dimension_ref, candidates in conflict_map.items()
        if isinstance(candidates, list)
    ]
    imported_dimension_sources = [
        dict(source)
        for compiled in compiled_list
        for source_list in [compiled.metadata.get("resolved_imported_dimension_sources")]
        if isinstance(source_list, list)
        for source in source_list
        if isinstance(source, dict)
    ]
    metric_execution_contexts = [
        dict(context)
        for compiled in compiled_list
        for context in [compiled.metadata.get("metric_execution_context")]
        if isinstance(context, dict)
    ]
    metric_entity_anchor_refs = _merge_unique_str(
        [
            str(compiled.metadata.get("metric_entity_anchor_ref"))
            if compiled.metadata.get("metric_entity_anchor_ref")
            else None
            for compiled in compiled_list
        ]
    )
    resolved_refs: dict[str, dict[str, Any]] = {}
    for compiled in compiled_list:
        metric_ref = compiled.metadata.get("resolved_metric_ref")
        if not metric_ref:
            continue
        metric_ref_text = str(metric_ref)
        resolved = resolved_refs.setdefault(
            metric_ref_text,
            {
                "ref": metric_ref_text,
            },
        )
        metric_revision = compiled.metadata.get("resolved_metric_revision")
        if metric_revision is not None:
            resolved["revision"] = int(metric_revision)
        metric_object_id = compiled.metadata.get("resolved_metric_object_id")
        if metric_object_id:
            resolved["object_id"] = str(metric_object_id)
    calendar_policy_binding = _build_calendar_policy_binding(resolved_calendar_alignments)

    if not any(
        (
            metric_refs,
            metric_revisions,
            metric_object_ids,
            process_refs,
            filter_time_refs,
            entity_field_refs,
            entity_field_sources,
            relationship_refs,
            relationship_sources,
            dimension_refs,
            ir_plan_ids,
            request_classes,
            compiler_summaries,
            resolved_calendar_alignments,
            imported_dimension_lineage,
            imported_dimension_conflicts,
            imported_dimension_sources,
            metric_execution_contexts,
            metric_entity_anchor_refs,
            resolved_refs,
            calendar_policy_binding,
        )
    ):
        return None

    snapshot: dict[str, Any] = {
        "schema_version": "step_semantic_metadata.v1",
        "metadata_kind": "typed_semantic_snapshot",
        "typed_inputs": {
            "metric_ref": metric_refs[0] if metric_refs else None,
            "resolved_metric_revision": metric_revisions[0] if metric_revisions else None,
            "resolved_metric_object_id": metric_object_ids[0] if metric_object_ids else None,
            "process_ref": process_refs[0] if process_refs else None,
            "dimension_refs": dimension_refs,
            "filter_time_ref": filter_time_refs[0] if filter_time_refs else None,
            "metric_entity_anchor_ref": (
                metric_entity_anchor_refs[0] if metric_entity_anchor_refs else None
            ),
            "request_classes": request_classes,
        },
        "entity_field_refs": entity_field_refs,
        "relationship_refs": relationship_refs,
        "compile_context": {
            "ir_plan_ids": ir_plan_ids,
            "compiler_summaries": compiler_summaries,
            "entity_field_sources": entity_field_sources,
            "relationship_sources": relationship_sources,
            "resolved_calendar_alignments": resolved_calendar_alignments,
            "imported_dimension_lineage": imported_dimension_lineage,
            "imported_dimension_conflicts": imported_dimension_conflicts,
            "imported_dimension_sources": imported_dimension_sources,
            "metric_execution_contexts": metric_execution_contexts,
            "calendar_policy_binding": calendar_policy_binding,
        },
        "resolved_refs": resolved_refs,
    }
    for source in entity_field_sources:
        field_ref = source.get("field_ref")
        if not field_ref:
            continue
        resolved = resolved_refs.setdefault(str(field_ref), {"ref": str(field_ref)})
        if source.get("entity_revision") is not None:
            resolved["entity_revision"] = int(source["entity_revision"])
        if source.get("entity_ref") is not None:
            resolved["entity_ref"] = str(source["entity_ref"])
        if source.get("physical_column") is not None:
            resolved["physical_column"] = str(source["physical_column"])
        if source.get("physical_expression_locator") is not None:
            resolved["physical_expression_locator"] = source["physical_expression_locator"]
        if source.get("source_object_ref") is not None:
            resolved["source_object_ref"] = str(source["source_object_ref"])
        if source.get("source_object_fqn") is not None:
            resolved["source_object_fqn"] = str(source["source_object_fqn"])
    for source in relationship_sources:
        relationship_ref = source.get("relationship_ref")
        if not relationship_ref:
            continue
        resolved = resolved_refs.setdefault(str(relationship_ref), {"ref": str(relationship_ref)})
        if source.get("revision") is not None:
            resolved["revision"] = int(source["revision"])
        for key in (
            "left_entity_ref",
            "right_entity_ref",
            "key_alignment",
            "time_alignment",
            "cardinality",
            "grain_compatibility",
            "snapshot_effective_window_alignment",
        ):
            if source.get(key) is not None:
                resolved[key] = source[key]
    return snapshot
