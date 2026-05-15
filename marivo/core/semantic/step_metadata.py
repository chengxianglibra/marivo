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
    metric_execution_contexts = [
        dict(context)
        for compiled in compiled_list
        for context in [compiled.metadata.get("metric_execution_context")]
        if isinstance(context, dict)
    ]
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

    if not any(
        (
            metric_refs,
            metric_revisions,
            metric_object_ids,
            process_refs,
            filter_time_refs,
            relationship_refs,
            relationship_sources,
            dimension_refs,
            ir_plan_ids,
            request_classes,
            compiler_summaries,
            metric_execution_contexts,
            resolved_refs,
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
            "request_classes": request_classes,
        },
        "relationship_refs": relationship_refs,
        "compile_context": {
            "ir_plan_ids": ir_plan_ids,
            "compiler_summaries": compiler_summaries,
            "relationship_sources": relationship_sources,
            "metric_execution_contexts": metric_execution_contexts,
        },
        "resolved_refs": resolved_refs,
    }
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
