from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

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


def _metadata_store_from_client(client: TestClient) -> MetadataStore:
    metadata_store = getattr(client.app.state, "metadata_store", None)
    if metadata_store is None:
        metadata_store = client.app.state.services.metadata_store
    return metadata_store


def _metric_payload_for_measure_type(metric_name: str, measure_type: str | None) -> dict[str, Any]:
    kind = str(measure_type or "count").strip().lower()
    if kind in {"percentile", "quantile"}:
        return {
            "metric_family": "distribution_metric",
            "value_component": {
                "name": metric_name,
                "semantics": f"Distribution value component for {metric_name}",
                "aggregation": "sum",
            },
            "distribution_spec": {
                "kind": "percentile",
                "percentile": 0.95,
            },
        }
    if kind in {"ratio", "rate"}:
        return {
            "metric_family": "rate_metric",
            "numerator": {
                "name": f"{metric_name}_numerator",
                "semantics": f"Rate numerator for {metric_name}",
                "aggregation": "sum",
            },
            "denominator": {
                "name": f"{metric_name}_denominator",
                "semantics": f"Rate denominator for {metric_name}",
                "aggregation": "count",
            },
        }
    if kind in {"average", "mean"}:
        return {
            "metric_family": "average_metric",
            "numerator": {
                "name": f"{metric_name}_numerator",
                "semantics": f"Average numerator for {metric_name}",
                "aggregation": "sum",
            },
            "denominator": {
                "name": f"{metric_name}_denominator",
                "semantics": f"Average denominator for {metric_name}",
                "aggregation": "count",
            },
        }
    if kind == "sum":
        return {
            "metric_family": "sum_metric",
            "measure": {
                "name": metric_name,
                "semantics": f"Summed measure for {metric_name}",
                "aggregation": "sum",
            },
        }
    return {
        "metric_family": "count_metric",
        "count_target": {
            "name": metric_name,
            "semantics": f"Count target for {metric_name}",
            "aggregation": "count",
        },
    }


def _metric_header_axes(measure_type: str | None) -> tuple[str, str, str, dict[str, Any]]:
    kind = str(measure_type or "count").strip().lower()
    if kind in {"percentile", "quantile"}:
        return (
            "distribution_metric",
            "numeric",
            "distribution_statistic",
            {"dimension_policy": "none", "time_axis_policy": "non_additive"},
        )
    if kind in {"ratio", "rate"}:
        return (
            "rate_metric",
            "rate",
            "ratio",
            {"dimension_policy": "none", "time_axis_policy": "non_additive"},
        )
    if kind in {"average", "mean"}:
        return (
            "average_metric",
            "numeric",
            "mean",
            {"dimension_policy": "none", "time_axis_policy": "non_additive"},
        )
    if kind == "sum":
        return (
            "sum_metric",
            "numeric",
            "sum",
            {"dimension_policy": "all", "time_axis_policy": "additive"},
        )
    return (
        "count_metric",
        "numeric",
        "count",
        {"dimension_policy": "all", "time_axis_policy": "additive"},
    )


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
                            "key_refs": [
                                key if str(key).startswith("key.") else f"key.{key}"
                                for key in list(key_refs or [_DEFAULT_TYPED_ENTITY_KEY])
                            ],
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


def create_typed_entity(
    client: TestClient,
    *,
    name: str,
    display_name: str,
    keys: Sequence[str],
    description: str = "",
    primary_time_ref: str | None = None,
    properties: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata_store = _metadata_store_from_client(client)
    payload = {
        "header": {
            "entity_ref": f"entity.{name}",
            "display_name": display_name,
            "description": description,
            "entity_contract_version": "entity.v1",
        },
        "interface_contract": {
            "identity": {
                "key_refs": [key if str(key).startswith("key.") else f"key.{key}" for key in keys],
                "uniqueness_scope": "global",
                "id_stability": "stable",
            },
            "primary_time_ref": primary_time_ref,
        },
    }
    entity = client.app.state.semantic_service.create_typed_entity(
        TypedEntityCreateRequest.model_validate(payload)
    )
    if properties:
        metadata_store.execute(
            "UPDATE semantic_entity_contracts SET properties_json = ? WHERE entity_contract_id = ?",
            [json.dumps(properties), entity["entity_contract_id"]],
        )
        entity = client.app.state.semantic_service.get_typed_entity(entity["entity_contract_id"])
    return entity


def publish_typed_entity(client: TestClient, entity_contract_id: str) -> dict[str, Any]:
    return client.app.state.semantic_service.publish_typed_entity(entity_contract_id)


def patch_typed_entity_properties(
    client: TestClient,
    entity_contract_id: str,
    properties_patch: dict[str, Any],
) -> dict[str, Any]:
    metadata_store = _metadata_store_from_client(client)
    current = metadata_store.query_one(
        "SELECT properties_json FROM semantic_entity_contracts WHERE entity_contract_id = ?",
        [entity_contract_id],
    )
    existing = json.loads(current["properties_json"] or "{}") if current is not None else {}
    merged = {**existing, **properties_patch}
    metadata_store.execute(
        """
        UPDATE semantic_entity_contracts
        SET properties_json = ?, revision = revision + 1, updated_at = datetime('now')
        WHERE entity_contract_id = ?
        """,
        [json.dumps(merged), entity_contract_id],
    )
    return client.app.state.semantic_service.get_typed_entity(entity_contract_id)


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


def ensure_published_typed_metric(
    metadata: MetadataStore,
    *,
    metric_name: str,
    display_name: str | None = None,
    observed_entity_ref: str | None = None,
    grain: str | None = None,
    dimensions: Sequence[str] | None = None,
    definition_sql: str | None = None,
    measure_type: str | None = None,
    allowed_dimensions: Sequence[str] | None = None,
    quality_expectations: dict[str, Any] | None = None,
    desired_direction: str | None = None,
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
        if dimension_name.startswith("dimension."):
            ensure_published_typed_dimension(
                metadata,
                dimension_name=dimension_name.removeprefix("dimension."),
            )
        elif dimension_name != "event_date":
            ensure_published_typed_dimension(metadata, dimension_name=dimension_name)

    metric_family, sample_kind, value_semantics, additivity_constraints = _metric_header_axes(
        measure_type
    )
    if existing is None:
        created = service.create_typed_metric(
            TypedMetricCreateRequest.model_validate(
                {
                    "header": {
                        "metric_ref": metric_ref,
                        "display_name": display_name or metric_name.replace("_", " ").title(),
                        "metric_family": metric_family,
                        "observed_entity_ref": entity_ref,
                        "observation_grain_ref": f"grain.{grain or 'row'}",
                        "sample_kind": sample_kind,
                        "value_semantics": value_semantics,
                        "aggregation_scope": "window",
                        "primary_time_ref": primary_time_ref,
                        "additivity_constraints": additivity_constraints,
                        "metric_contract_version": "metric.v1",
                    },
                    "payload": _metric_payload_for_measure_type(metric_name, measure_type),
                }
            )
        )
        metric_contract_id = created["metric_contract_id"]
    else:
        metric_contract_id = str(existing["metric_contract_id"])

    row = metadata.query_one(
        "SELECT family_payload_json FROM semantic_metric_contracts WHERE metric_contract_id = ?",
        [metric_contract_id],
    )
    family_payload = json.loads(row["family_payload_json"] or "{}") if row is not None else {}
    if definition_sql is not None:
        family_payload["definition_sql"] = definition_sql
    if dimensions is not None:
        family_payload["dimensions"] = list(dimensions)
    if grain is not None:
        family_payload["grain"] = grain
    if measure_type is not None:
        family_payload["measure_type"] = measure_type
    if allowed_dimensions is not None:
        family_payload["allowed_dimensions"] = list(allowed_dimensions)
    if quality_expectations is not None:
        family_payload["quality_expectations"] = dict(quality_expectations)
    if desired_direction is not None:
        family_payload["desired_direction"] = desired_direction
    metadata.execute(
        "UPDATE semantic_metric_contracts SET family_payload_json = ? WHERE metric_contract_id = ?",
        [json.dumps(family_payload), metric_contract_id],
    )

    if existing is None or existing["status"] != "published":
        service.publish_typed_metric(metric_contract_id)
    return metric_ref


def create_typed_metric(
    client: TestClient,
    *,
    name: str,
    display_name: str,
    definition_sql: str,
    dimensions: Sequence[str],
    description: str = "",
    entity_ref: str | None = None,
    grain: str | None = None,
    measure_type: str | None = None,
    allowed_dimensions: Sequence[str] | None = None,
    quality_expectations: dict[str, Any] | None = None,
    desired_direction: str | None = None,
) -> dict[str, Any]:
    metadata_store = _metadata_store_from_client(client)
    observed_entity_ref = entity_ref or ensure_published_typed_entity(metadata_store)
    primary_time_ref = ensure_published_typed_time(metadata_store)
    for dimension_name in dimensions:
        if dimension_name.startswith("dimension."):
            ensure_published_typed_dimension(
                metadata_store,
                dimension_name=dimension_name.removeprefix("dimension."),
            )
        elif dimension_name != "event_date":
            ensure_published_typed_dimension(metadata_store, dimension_name=dimension_name)

    metric_family, sample_kind, value_semantics, additivity_constraints = _metric_header_axes(
        measure_type
    )
    metric = client.app.state.semantic_service.create_typed_metric(
        TypedMetricCreateRequest.model_validate(
            {
                "header": {
                    "metric_ref": f"metric.{name}",
                    "display_name": display_name,
                    "description": description,
                    "metric_family": metric_family,
                    "observed_entity_ref": observed_entity_ref,
                    "observation_grain_ref": f"grain.{grain or 'row'}",
                    "sample_kind": sample_kind,
                    "value_semantics": value_semantics,
                    "aggregation_scope": "window",
                    "primary_time_ref": primary_time_ref,
                    "additivity_constraints": additivity_constraints,
                    "metric_contract_version": "metric.v1",
                },
                "payload": _metric_payload_for_measure_type(name, measure_type),
            }
        )
    )
    row = metadata_store.query_one(
        "SELECT family_payload_json FROM semantic_metric_contracts WHERE metric_contract_id = ?",
        [metric["metric_contract_id"]],
    )
    family_payload = json.loads(row["family_payload_json"] or "{}") if row is not None else {}
    family_payload.update(
        {
            "definition_sql": definition_sql,
            "dimensions": list(dimensions),
            "grain": grain,
            "measure_type": measure_type,
            "allowed_dimensions": list(allowed_dimensions or []),
            "quality_expectations": dict(quality_expectations or {}),
            "desired_direction": desired_direction,
        }
    )
    metadata_store.execute(
        """
        UPDATE semantic_metric_contracts
        SET description = ?, family_payload_json = ?
        WHERE metric_contract_id = ?
        """,
        [description, json.dumps(family_payload), metric["metric_contract_id"]],
    )
    return client.app.state.semantic_service.get_typed_metric(metric["metric_contract_id"])


def publish_typed_metric(client: TestClient, metric_contract_id: str) -> dict[str, Any]:
    return client.app.state.semantic_service.publish_typed_metric(metric_contract_id)


def _structured_carrier_locator(
    metadata: MetadataStore,
    *,
    carrier_locator: str | dict[str, Any],
    source_object_ref: str | None,
) -> dict[str, Any]:
    if isinstance(carrier_locator, dict):
        return dict(carrier_locator)
    if source_object_ref is not None:
        row = metadata.query_one(
            "SELECT authority_locator_json FROM source_objects WHERE object_id = ?",
            [source_object_ref],
        )
        if row is not None:
            return json.loads(str(row["authority_locator_json"]))
    row = metadata.query_one(
        "SELECT authority_locator_json FROM source_objects WHERE fqn = ?",
        [carrier_locator],
    )
    if row is not None:
        return json.loads(str(row["authority_locator_json"]))
    parts = [part for part in carrier_locator.split(".") if part]
    if len(parts) == 3:
        return {"catalog": parts[0], "schema": parts[1], "table": parts[2]}
    if len(parts) == 2:
        return {"catalog": None, "schema": parts[0], "table": parts[1]}
    raise AssertionError(f"Unable to derive structured carrier locator from {carrier_locator!r}")


def ensure_published_typed_metric_binding(
    metadata: MetadataStore,
    *,
    metric_name: str,
    carrier_locator: str | dict[str, Any],
    source_object_ref: str | None = None,
    binding_role: str = "primary",
    surface_name: str = "value",
    dimension_names: Sequence[str] | None = None,
    metric_input_target_keys: Sequence[str] | None = None,
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
        if dimension_name != "event_date":
            ensure_published_typed_dimension(metadata, dimension_name=dimension_name)

    metric_row = metadata.query_one(
        """
        SELECT metric_family
        FROM semantic_metric_contracts
        WHERE metric_ref = ?
        """,
        [f"metric.{metric_name}"],
    )
    metric_family = str(metric_row["metric_family"]) if metric_row is not None else "count_metric"
    structured_locator = _structured_carrier_locator(
        metadata,
        carrier_locator=carrier_locator,
        source_object_ref=source_object_ref,
    )

    field_surfaces = [
        {"surface_ref": "field.event_date", "physical_name": "event_date"},
        {"surface_ref": f"field.{surface_name}", "physical_name": surface_name},
    ]
    field_bindings = [
        {
            "carrier_binding_key": "primary",
            "target": {"target_kind": "primary_time", "target_key": _DEFAULT_TYPED_TIME_REF},
            "semantic_ref": _DEFAULT_TYPED_TIME_REF,
            "surface_ref": "field.event_date",
        }
    ]
    metric_input_keys: list[str]
    if metric_input_target_keys is not None:
        metric_input_keys = [str(target_key) for target_key in metric_input_target_keys]
    elif metric_family in {"rate_metric", "average_metric"}:
        metric_input_keys = ["numerator", "denominator"]
    elif metric_family == "sum_metric":
        metric_input_keys = ["measure"]
    elif metric_family == "distribution_metric":
        metric_input_keys = ["value_component"]
    elif metric_family == "score_metric":
        metric_input_keys = ["score_source"]
    else:
        metric_input_keys = ["count_target"]

    for target_key in metric_input_keys:
        field_bindings.append(
            {
                "carrier_binding_key": "primary",
                "target": {
                    "target_kind": "metric_input",
                    "target_key": target_key,
                },
                "semantic_ref": f"metric_input.{target_key}",
                "surface_ref": f"field.{surface_name}",
            }
        )
    for dimension_name in dimension_names or []:
        if dimension_name == "event_date":
            continue
        field_surfaces.append(
            {
                "surface_ref": f"field.{dimension_name}",
                "physical_name": dimension_name,
            }
        )

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
                                "source_object_ref": source_object_ref,
                                "carrier_kind": "table",
                                "carrier_locator": structured_locator,
                                "binding_role": binding_role,
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


def create_typed_metric_binding(
    client: TestClient,
    *,
    metric_ref: str,
    object_id: str,
    carrier_locator: str | dict[str, Any],
    binding_role: str = "primary",
    mapping_type: str | None = None,
    metric_input_target_keys: Sequence[str] | None = None,
) -> dict[str, Any]:
    _ = mapping_type
    metadata_store = _metadata_store_from_client(client)
    metric_name = metric_ref.removeprefix("metric.")
    binding_ref = ensure_published_typed_metric_binding(
        metadata_store,
        metric_name=metric_name,
        carrier_locator=carrier_locator,
        source_object_ref=object_id,
        binding_role=binding_role,
        metric_input_target_keys=metric_input_target_keys,
    )
    row = metadata_store.query_one(
        "SELECT binding_id FROM typed_bindings WHERE binding_ref = ?",
        [binding_ref],
    )
    if row is None:
        raise AssertionError(f"Expected typed binding for {binding_ref}")
    return client.app.state.semantic_service.get_typed_binding(str(row["binding_id"]))
