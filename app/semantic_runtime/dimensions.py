from __future__ import annotations

from app.storage.metadata import MetadataStore


def resolve_entity_binding_dimensions(metadata: MetadataStore, entity_ref: str) -> list[str]:
    """Return canonical dimension refs exposed by published entity bindings."""
    rows = metadata.query_rows(
        """
        SELECT field_bindings.semantic_ref
        FROM typed_bindings
        JOIN field_bindings ON field_bindings.binding_id = typed_bindings.binding_id
        WHERE typed_bindings.bound_object_ref = ?
          AND typed_bindings.binding_scope = 'entity'
          AND typed_bindings.status = 'published'
          AND field_bindings.target_kind = 'stable_descriptor'
        ORDER BY typed_bindings.binding_ref, field_bindings.carrier_binding_key, field_bindings.target_key
        """,
        [entity_ref],
    )

    dimensions: list[str] = []
    seen: set[str] = set()
    for row in rows:
        semantic_ref = str(row.get("semantic_ref") or "").strip()
        if not semantic_ref.startswith("dimension.") or semantic_ref in seen:
            continue
        seen.add(semantic_ref)
        dimensions.append(semantic_ref)
    return dimensions
