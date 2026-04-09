from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.models.binding import TypedBindingCreateRequest
from app.api.models.dimension import DimensionCreateRequest
from app.api.models.entity import TypedEntityCreateRequest
from app.api.models.metric import TypedMetricCreateRequest
from app.api.models.time import TimeCreateRequest
from app.semantic import SemanticService
from app.storage.metadata import MetadataStore

_DEFAULT_TYPED_ENTITY_REF = "entity.synthetic_subject"
_DEFAULT_TYPED_ENTITY_KEY = "key.synthetic_subject_id"
_DEFAULT_TYPED_TIME_REF = "time.event_date"


def _semantic_service_for_metadata(metadata: MetadataStore) -> SemanticService:
    return SemanticService(metadata)


def ensure_published_typed_entity(
    metadata: MetadataStore,
    *,
    entity_name: str = "synthetic_subject",
    display_name: str | None = None,
    key_refs: Sequence[str] | None = None,
) -> str:
    entity_ref = f"entity.{entity_name}"
    existing = metadata.query_one(
        "SELECT entity_contract_id, status FROM semantic_entity_contracts WHERE entity_ref = ?",
        [entity_ref],
    )
    service = _semantic_service_for_metadata(metadata)
    if existing is None:
        created = service.create_typed_entity(
            TypedEntityCreateRequest.model_validate(
                {
                    "header": {
                        "entity_ref": entity_ref,
                        "display_name": display_name or entity_name.replace("_", " ").title(),
                        "entity_contract_version": "entity.v4",
                    },
                    "interface_contract": {
                        "identity": {
                            "key_refs": list(key_refs or [_DEFAULT_TYPED_ENTITY_KEY]),
                            "uniqueness_scope": "global",
                            "id_stability": "stable",
                        }
                    },
                }
            )
        )
        service.publish_typed_entity(created["entity_contract_id"])
        return entity_ref
    if existing["status"] != "published":
        service.publish_typed_entity(str(existing["entity_contract_id"]))
    return entity_ref


def ensure_published_typed_metric(
    metadata: MetadataStore,
    *,
    metric_name: str,
    display_name: str | None = None,
    observed_entity_ref: str | None = None,
    grain: str | None = None,
    dimensions: Sequence[str] | None = None,
) -> str:
    metric_ref = f"metric.{metric_name}"
    existing = metadata.query_one(
        """
        SELECT metric_contract_id, status, observed_entity_ref, primary_time_ref
        FROM semantic_metric_contracts
        WHERE metric_ref = ?
        """,
        [metric_ref],
    )
    service = _semantic_service_for_metadata(metadata)
    entity_ref = observed_entity_ref or ensure_published_typed_entity(metadata)
    primary_time_ref = ensure_published_typed_time(metadata)
    for dimension_name in dimensions or []:
        ensure_published_typed_dimension(metadata, dimension_name=dimension_name)
    if existing is None:
        created = service.create_typed_metric(
            TypedMetricCreateRequest.model_validate(
                {
                    "header": {
                        "metric_ref": metric_ref,
                        "display_name": display_name or metric_name.replace("_", " ").title(),
                        "metric_family": "count_metric",
                        "observed_entity_ref": entity_ref,
                        "observation_grain_ref": f"grain.{grain or 'row'}",
                        "sample_kind": "numeric",
                        "value_semantics": "count",
                        "primary_time_ref": primary_time_ref,
                        "additivity": "additive",
                        "metric_contract_version": "metric.v1",
                    },
                    "payload": {
                        "metric_family": "count_metric",
                        "count_target": {
                            "name": metric_name,
                            "semantics": f"Legacy compatibility contract for {metric_name}",
                            "aggregation": "count",
                        },
                    },
                }
            )
        )
        service.publish_typed_metric(created["metric_contract_id"])
        return metric_ref
    existing_entity_ref = str(existing["observed_entity_ref"] or "").strip()
    if existing_entity_ref.startswith("entity."):
        ensure_published_typed_entity(
            metadata,
            entity_name=existing_entity_ref.removeprefix("entity."),
        )
    if entity_ref != existing_entity_ref and entity_ref.startswith("entity."):
        ensure_published_typed_entity(
            metadata,
            entity_name=entity_ref.removeprefix("entity."),
        )
    existing_time_ref = str(existing["primary_time_ref"] or "").strip()
    if existing_time_ref.startswith("time."):
        ensure_published_typed_time(metadata, time_ref=existing_time_ref)
    if existing["status"] != "published":
        service.publish_typed_metric(str(existing["metric_contract_id"]))
    return metric_ref


def ensure_published_typed_dimension(
    metadata: MetadataStore,
    *,
    dimension_name: str,
    display_name: str | None = None,
) -> str:
    dimension_ref = f"dimension.{dimension_name}"
    existing = metadata.query_one(
        "SELECT dimension_contract_id, status FROM semantic_dimension_contracts WHERE dimension_ref = ?",
        [dimension_ref],
    )
    service = _semantic_service_for_metadata(metadata)
    if existing is None:
        created = service.create_dimension(
            DimensionCreateRequest.model_validate(
                {
                    "header": {
                        "dimension_ref": dimension_ref,
                        "display_name": display_name or dimension_name.replace("_", " ").title(),
                        "dimension_contract_version": "dimension.v1",
                    },
                    "interface_contract": {
                        "value_domain": {
                            "structure_kind": "flat",
                            "value_type": "string",
                            "domain_kind": "open",
                        },
                        "grouping": {"supports_grouping": True},
                    },
                }
            )
        )
        service.publish_dimension(created["dimension_contract_id"])
        return dimension_ref
    if existing["status"] != "published":
        service.publish_dimension(str(existing["dimension_contract_id"]))
    return dimension_ref


def ensure_published_typed_time(
    metadata: MetadataStore,
    *,
    time_ref: str = _DEFAULT_TYPED_TIME_REF,
    display_name: str = "Event Date",
) -> str:
    existing = metadata.query_one(
        "SELECT time_contract_id, status FROM semantic_time_objects WHERE time_ref = ?",
        [time_ref],
    )
    service = _semantic_service_for_metadata(metadata)
    if existing is None:
        created = service.create_time_semantic(
            TimeCreateRequest.model_validate(
                {
                    "header": {
                        "time_ref": time_ref,
                        "display_name": display_name,
                        "semantic_roles": ["measurement"],
                        "time_contract_version": "time.v1",
                    }
                }
            )
        )
        service.publish_time_semantic(created["time_contract_id"])
        return time_ref
    if existing["status"] != "published":
        service.publish_time_semantic(str(existing["time_contract_id"]))
    return time_ref


def create_legacy_entity(
    client: TestClient,
    *,
    name: str,
    display_name: str,
    keys: Sequence[str],
    description: str = "",
    level: str | None = None,
    join_constraints: dict[str, Any] | None = None,
    upstream_dependencies: Sequence[str] | None = None,
    lineage: Sequence[str] | None = None,
    quality_expectations: dict[str, Any] | None = None,
    properties: dict[str, Any] | None = None,
) -> dict[str, Any]:
    entity = client.app.state.semantic_service.create_entity(
        name=name,
        display_name=display_name,
        description=description,
        keys=list(keys),
        level=level,
        join_constraints=join_constraints,
        upstream_dependencies=list(upstream_dependencies or []),
        lineage=list(lineage or []),
        quality_expectations=quality_expectations,
        properties=properties,
    )
    metadata_store = getattr(client.app.state, "metadata_store", None)
    if metadata_store is None:
        metadata_store = client.app.state.services.metadata_store
    ensure_published_typed_entity(
        metadata_store,
        entity_name=name,
        display_name=display_name,
        key_refs=keys,
    )
    return entity


def publish_legacy_entity(client: TestClient, entity_id: str) -> dict[str, Any]:
    entity = client.app.state.semantic_service.publish_entity(entity_id)
    metadata_store = getattr(client.app.state, "metadata_store", None)
    if metadata_store is None:
        metadata_store = client.app.state.services.metadata_store
    legacy_row = metadata_store.query_one(
        "SELECT name, display_name, keys_json FROM semantic_entities WHERE entity_id = ?",
        [entity_id],
    )
    if legacy_row is not None:
        ensure_published_typed_entity(
            metadata_store,
            entity_name=str(legacy_row["name"]),
            display_name=str(legacy_row["display_name"]),
            key_refs=json.loads(legacy_row["keys_json"] or "[]") or [_DEFAULT_TYPED_ENTITY_KEY],
        )
    return entity


def update_legacy_entity(
    client: TestClient,
    entity_id: str,
    **kwargs: Any,
) -> dict[str, Any]:
    return client.app.state.semantic_service.update_entity(entity_id, **kwargs)


def patch_legacy_entity_properties(
    client: TestClient,
    entity_id: str,
    properties: dict[str, Any],
) -> dict[str, Any]:
    return client.app.state.semantic_service.patch_entity_properties(entity_id, properties)


def create_legacy_metric(
    client: TestClient,
    *,
    name: str,
    display_name: str,
    definition_sql: str,
    dimensions: Sequence[str],
    description: str = "",
    entity_id: str | None = None,
    grain: str | None = None,
    measure_type: str | None = None,
    allowed_dimensions: Sequence[str] | None = None,
    lineage: Sequence[str] | None = None,
    quality_expectations: dict[str, Any] | None = None,
    properties: dict[str, Any] | None = None,
    desired_direction: str | None = None,
) -> dict[str, Any]:
    metric = client.app.state.semantic_service.create_metric(
        name=name,
        display_name=display_name,
        description=description,
        definition_sql=definition_sql,
        dimensions=list(dimensions),
        entity_id=entity_id,
        grain=grain,
        measure_type=measure_type,
        allowed_dimensions=list(allowed_dimensions) if allowed_dimensions is not None else None,
        lineage=list(lineage or []),
        quality_expectations=quality_expectations,
        properties=properties,
        desired_direction=desired_direction,
    )
    metadata_store = getattr(client.app.state, "metadata_store", None)
    if metadata_store is None:
        metadata_store = client.app.state.services.metadata_store
    observed_entity_ref: str | None = None
    if entity_id is not None:
        legacy_entity = metadata_store.query_one(
            "SELECT name FROM semantic_entities WHERE entity_id = ?",
            [entity_id],
        )
        if legacy_entity is not None:
            observed_entity_ref = f"entity.{legacy_entity['name']}"
    ensure_published_typed_metric(
        metadata_store,
        metric_name=name,
        display_name=display_name,
        observed_entity_ref=observed_entity_ref,
        grain=grain,
        dimensions=dimensions,
    )
    return metric


def publish_legacy_metric(client: TestClient, metric_id: str) -> dict[str, Any]:
    metric = client.app.state.semantic_service.publish_metric(metric_id)
    metadata_store = getattr(client.app.state, "metadata_store", None)
    if metadata_store is None:
        metadata_store = client.app.state.services.metadata_store
    legacy_row = metadata_store.query_one(
        """
        SELECT m.name, m.display_name, m.grain, m.dimensions_json, e.name AS entity_name
        FROM semantic_metrics m
        LEFT JOIN semantic_entities e ON e.entity_id = m.entity_id
        WHERE m.metric_id = ?
        """,
        [metric_id],
    )
    if legacy_row is not None:
        observed_entity_ref = (
            f"entity.{legacy_row['entity_name']}" if legacy_row["entity_name"] is not None else None
        )
        ensure_published_typed_metric(
            metadata_store,
            metric_name=str(legacy_row["name"]),
            display_name=str(legacy_row["display_name"]),
            observed_entity_ref=observed_entity_ref,
            grain=str(legacy_row["grain"]) if legacy_row["grain"] is not None else None,
            dimensions=json.loads(legacy_row.get("dimensions_json", "[]")),
        )
    return metric


def create_legacy_mapping(
    client: TestClient,
    *,
    semantic_type: str,
    semantic_id: str,
    object_id: str,
    mapping_type: str,
    mapping_json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    mapping_id = f"map_{uuid4().hex[:12]}"
    now = "2026-04-09T00:00:00+00:00"
    metadata_store = getattr(client.app.state, "metadata_store", None)
    if metadata_store is None:
        metadata_store = client.app.state.services.metadata_store
    payload = {
        "mapping_id": mapping_id,
        "semantic_type": semantic_type,
        "semantic_id": semantic_id,
        "object_id": object_id,
        "mapping_type": mapping_type,
        "mapping_json": mapping_json or {},
        "created_at": now,
        "updated_at": now,
    }
    metadata_store.execute(
        """
        INSERT INTO legacy_semantic_mappings (
            mapping_id, semantic_type, semantic_id, object_id, mapping_type,
            mapping_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            mapping_id,
            semantic_type,
            semantic_id,
            object_id,
            mapping_type,
            json.dumps(mapping_json or {}),
            now,
            now,
        ],
    )
    if semantic_type == "metric":
        metric_row = metadata_store.query_one(
            "SELECT name FROM semantic_metrics WHERE metric_id = ?",
            [semantic_id],
        )
        source_row = metadata_store.query_one(
            "SELECT fqn FROM source_objects WHERE object_id = ?",
            [object_id],
        )
        if metric_row is not None and source_row is not None:
            ensure_published_typed_metric_binding(
                metadata_store,
                metric_name=str(metric_row["name"]),
                carrier_locator=str(source_row["fqn"]),
            )
    return payload


def ensure_published_typed_metric_binding(
    metadata: MetadataStore,
    *,
    metric_name: str,
    carrier_locator: str,
    surface_name: str = "value",
    dimension_names: Sequence[str] | None = None,
) -> str:
    binding_ref = f"binding.{metric_name}_primary"
    existing = metadata.query_one(
        "SELECT binding_id, status FROM typed_bindings WHERE binding_ref = ?",
        [binding_ref],
    )
    service = _semantic_service_for_metadata(metadata)
    ensure_published_typed_metric(
        metadata,
        metric_name=metric_name,
        display_name=metric_name,
        dimensions=dimension_names,
    )
    for dimension_name in dimension_names or []:
        ensure_published_typed_dimension(metadata, dimension_name=dimension_name)
    field_surfaces = [
        {
            "surface_ref": "field.event_date",
            "physical_name": "event_date",
        },
        {
            "surface_ref": f"field.{surface_name}",
            "physical_name": surface_name,
        },
    ]
    for dimension_name in dimension_names or []:
        if dimension_name == "event_date":
            continue
        field_surfaces.append(
            {
                "surface_ref": f"field.{dimension_name}",
                "physical_name": dimension_name,
            }
        )
    field_bindings = [
        {
            "carrier_binding_key": "primary",
            "target": {
                "target_kind": "primary_time",
                "target_key": _DEFAULT_TYPED_TIME_REF,
            },
            "semantic_ref": _DEFAULT_TYPED_TIME_REF,
            "surface_ref": "field.event_date",
        },
        {
            "carrier_binding_key": "primary",
            "target": {
                "target_kind": "metric_input",
                "target_key": f"metric_input.{surface_name}",
            },
            "semantic_ref": f"metric_input.{surface_name}",
            "surface_ref": f"field.{surface_name}",
        },
    ]
    if existing is None:
        created = service.create_typed_binding(
            TypedBindingCreateRequest.model_validate(
                {
                    "header": {
                        "binding_ref": binding_ref,
                        "display_name": f"{metric_name} Primary Binding",
                        "binding_scope": "metric",
                        "bound_object_ref": f"metric.{metric_name}",
                        "binding_contract_version": "binding.v1",
                    },
                    "interface_contract": {
                        "carrier_bindings": [
                            {
                                "binding_key": "primary",
                                "carrier_kind": "table",
                                "carrier_locator": carrier_locator,
                                "binding_role": "primary",
                                "field_surfaces": field_surfaces,
                            }
                        ],
                        "field_bindings": field_bindings,
                    },
                }
            )
        )
        service.publish_typed_binding(created["binding_id"])
        return binding_ref
    if existing["status"] != "published":
        service.publish_typed_binding(str(existing["binding_id"]))
    return binding_ref
