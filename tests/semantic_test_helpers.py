from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from app.datasources import DatasourceService
from app.routing import QueryRouter
from app.service import SemanticLayerService
from app.storage.analytics import AnalyticsEngine
from app.storage.metadata import MetadataStore

# Stub names for deleted model types — these are no longer functional.
# The helper functions that use them will raise NotImplementedError at runtime.
# Task 7 will migrate these helpers to use OSI v2 models.
TypedEntityCreateRequest = None  # type: ignore[assignment,misc]
TypedMetricCreateRequest = None  # type: ignore[assignment,misc]
TimeCreateRequest = None  # type: ignore[assignment,misc]
DimensionCreateRequest = None  # type: ignore[assignment,misc]

_DEFAULT_TYPED_ENTITY_REF = "entity.synthetic_subject"
_DEFAULT_TYPED_ENTITY_KEY = "key.synthetic_subject_id"
_DEFAULT_TYPED_TIME_REF = "time.event_date"


def _entity_field_ref(entity_ref: str, field_name: str) -> str:
    return f"{entity_ref}.field.{field_name.removeprefix('field.')}"


def _default_metric_input_field_refs(entity_ref: str, measure_type: str | None) -> dict[str, str]:
    kind = str(measure_type or "count").strip().lower()
    if kind in {"percentile", "quantile"}:
        return {"value_component": _entity_field_ref(entity_ref, "value")}
    if kind in {"ratio", "rate", "average", "mean"}:
        return {
            "numerator": _entity_field_ref(entity_ref, "numerator"),
            "denominator": _entity_field_ref(entity_ref, "denominator"),
        }
    if kind == "sum":
        return {"measure": _entity_field_ref(entity_ref, "value")}
    return {"count_target": _entity_field_ref(entity_ref, "user_id")}


def _metric_payload_with_default_input_fields(
    metric_name: str, measure_type: str | None, entity_ref: str
) -> dict[str, Any]:
    payload = _metric_payload_for_measure_type(metric_name, measure_type)
    for component_name, field_ref in _default_metric_input_field_refs(
        entity_ref, measure_type
    ).items():
        component = payload.get(component_name)
        if isinstance(component, dict) and "input_field_ref" not in component:
            component["input_field_ref"] = field_ref
    return payload


def _default_entity_fields_for_metric(
    entity_ref: str,
    measure_type: str | None,
    *,
    source_object_fqn: str = "analytics.watch_events",
) -> dict[str, Any]:
    _ = source_object_fqn
    input_fields = _default_metric_input_field_refs(entity_ref, measure_type)
    fields: dict[str, dict[str, Any]] = {
        "user_id": {
            "field_ref": "field.user_id",
            "value_type": "string",
            "nullable": False,
            "physical_column": "user_id",
        },
        "event_date": {
            "field_ref": "field.event_date",
            "value_type": "date",
            "nullable": False,
            "physical_column": "event_date",
        },
    }
    for field_ref in input_fields.values():
        field_name = field_ref.rsplit(".field.", 1)[-1]
        value_type = "number" if field_name in {"value", "numerator", "denominator"} else "string"
        fields.setdefault(
            field_name,
            {
                "field_ref": f"field.{field_name}",
                "value_type": value_type,
                "nullable": False,
                "physical_column": field_name,
            },
        )
    return {"fields": list(fields.values()), "binding": {}}


def _ensure_entity_has_default_fields(
    metadata: MetadataStore,
    *,
    entity_ref: str,
    fields: list[dict[str, Any]],
    binding: dict[str, Any],
) -> None:
    row = metadata.query_one(
        """
        SELECT entity_contract_id, fields_json, binding_json
        FROM semantic_entity_contracts
        WHERE entity_ref = ?
        """,
        [entity_ref],
    )
    if row is None:
        return
    current_fields = json.loads(row["fields_json"] or "[]")
    current_by_ref = {
        str(field.get("field_ref")): dict(field)
        for field in current_fields
        if isinstance(field, dict)
    }
    changed = False
    for field_spec in fields:
        field_ref = str(field_spec.get("field_ref") or "")
        if field_ref and field_ref not in current_by_ref:
            current_by_ref[field_ref] = dict(field_spec)
            changed = True
    current_binding = json.loads(row["binding_json"] or "null")
    if current_binding is None:
        current_binding = dict(binding)
        changed = True
    if not changed:
        return
    metadata.execute(
        """
        UPDATE semantic_entity_contracts
        SET fields_json = ?, binding_json = ?
        WHERE entity_contract_id = ?
        """,
        [
            json.dumps(list(current_by_ref.values())),
            json.dumps(current_binding),
            row["entity_contract_id"],
        ],
    )


def _semantic_service_for_metadata(metadata: MetadataStore) -> Any:
    # SemanticService removed during OSI v2 migration; these helpers will be
    # updated in Task 7.  Return a stub that raises on any method call.
    return _RemovedSemanticServiceStub()


class _RemovedSemanticServiceStub:
    """Stub that raises NotImplementedError on any method call.

    SemanticService was removed during OSI v2 migration.
    This stub exists so that test collection/setup does not crash at import time.
    See Task 7 for the full migration.
    """

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"SemanticService.{name}() removed — see Task 7")


def _metadata_store_from_client(client: TestClient) -> MetadataStore:
    metadata_store = getattr(client.app.state, "metadata_store", None)
    if metadata_store is None:
        metadata_store = client.app.state.services.metadata_store
    return metadata_store


def build_semantic_layer_service(
    metadata: MetadataStore,
    analytics: AnalyticsEngine,
) -> SemanticLayerService:
    from unittest.mock import MagicMock

    from app.core.engine import CoreEngine

    service = SemanticLayerService(metadata, analytics)
    service.query_router = QueryRouter(metadata, DatasourceService(metadata))
    # Phase 3b: ensure _core_engine and _runtime_ports are set so migrated
    # intent runners can access them through the service registration lambdas.
    # ports is currently unused by migrated runners; a MagicMock suffices for now.
    if service._core_engine is None:
        service._core_engine = CoreEngine(service)
    if service._runtime_ports is None:
        service._runtime_ports = MagicMock()  # type: ignore[assignment]
    return service


def seed_duckdb_source_object(
    metadata: MetadataStore,
    *,
    source_id: str,
    object_id: str,
    display_name: str,
    table_name: str,
    table_fqn: str,
    now: str,
    connection: dict[str, Any] | None = None,
    db_path: str | Path | None = None,
    authority_locator: dict[str, Any] | None = None,
    properties: dict[str, Any] | None = None,
    sync_version: str | None = None,
    synced_at: str | None = None,
    status: str = "active",
    columns: Sequence[tuple[str, str]] | None = None,
) -> None:
    """Seed a DuckDB datasource and dataset-native semantic grounding."""
    effective_connection: dict[str, Any]
    if connection is not None:
        effective_connection = connection
    elif db_path is not None:
        effective_connection = {"path": str(db_path)}
    else:
        effective_connection = {}
    metadata.insert_ignore(
        "datasources",
        [
            "datasource_id",
            "datasource_type",
            "display_name",
            "connection_json",
            "status",
            "created_at",
            "updated_at",
        ],
        [
            source_id,
            "duckdb",
            display_name,
            json.dumps(effective_connection),
            status,
            now,
            now,
        ],
    )
    dataset_name = table_name.replace(".", "_")
    existing_model = metadata.query_one(
        "SELECT model_id FROM semantic_models WHERE name = ?",
        [f"fixture_{dataset_name}"],
    )
    if existing_model is None:
        metadata.insert_ignore(
            "semantic_models",
            [
                "model_id",
                "name",
                "description",
                "visibility",
                "revision",
                "created_at",
                "updated_at",
            ],
            [
                None,
                f"fixture_{dataset_name}",
                f"Fixture model for {table_fqn}",
                "public",
                1,
                now,
                now,
            ],
        )
        existing_model = metadata.query_one(
            "SELECT model_id FROM semantic_models WHERE name = ?",
            [f"fixture_{dataset_name}"],
        )
    model_id = str(existing_model["model_id"])
    metadata.insert_ignore(
        "semantic_datasets",
        [
            "model_id",
            "name",
            "source",
            "primary_key",
            "description",
            "datasource_id",
            "created_at",
            "updated_at",
        ],
        [
            model_id,
            dataset_name,
            table_fqn,
            json.dumps(["id"]),
            f"Fixture dataset for {table_fqn}",
            source_id,
            now,
            now,
        ],
    )
    dataset_row = metadata.query_one(
        "SELECT dataset_id FROM semantic_datasets WHERE model_id = ? AND name = ?",
        [model_id, dataset_name],
    )
    if dataset_row is not None:
        resolved_columns = list(
            columns
            or [
                ("id", "string"),
                ("event_date", "date"),
                ("value", "number"),
                ("numerator", "number"),
                ("denominator", "number"),
                ("cluster", "string"),
                (table_name.removeprefix("dimension."), "string"),
            ]
        )
        for position, (field_name, data_type) in enumerate(resolved_columns):
            metadata.insert_ignore(
                "semantic_fields",
                [
                    "dataset_id",
                    "name",
                    "expression",
                    "is_time",
                    "data_type",
                    "position",
                    "created_at",
                    "updated_at",
                ],
                [
                    dataset_row["dataset_id"],
                    field_name,
                    json.dumps({"dialects": [{"dialect": "ANSI_SQL", "expression": field_name}]}),
                    1 if field_name == "event_date" else 0,
                    data_type,
                    position,
                    now,
                    now,
                ],
            )
    _ = object_id
    _ = authority_locator
    _ = properties
    _ = sync_version
    _ = synced_at


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
    measure_type: str | None = None,
    source_object_fqn: str = "analytics.watch_events",
) -> str:
    entity_ref = f"entity.{entity_name}"
    existing = metadata.query_one(
        "SELECT entity_contract_id, status FROM semantic_entity_contracts WHERE entity_ref = ?",
        [entity_ref],
    )
    grounding = _default_entity_fields_for_metric(
        entity_ref,
        measure_type,
        source_object_fqn=source_object_fqn,
    )
    if existing is None:
        entity_contract_id = f"entc_{uuid4().hex[:24]}"
        now = datetime.now(UTC).isoformat()
        normalized_keys = [
            key if str(key).startswith("key.") else f"key.{key}"
            for key in list(key_refs or [_DEFAULT_TYPED_ENTITY_KEY])
        ]
        metadata.execute(
            """
            INSERT INTO semantic_entity_contracts (
                entity_contract_id, entity_ref, display_name, description,
                properties_json, catalog_metadata_json, entity_contract_version,
                entity_kind, uniqueness_scope, id_stability, nullable_key_policy,
                primary_time_ref, fields_json, binding_json, status, revision,
                created_at, updated_at
            ) VALUES (?, ?, ?, '', '{}', '{}', 'entity.v4', 'business_entity',
                'global', 'stable', 'reject', ?, ?, ?, 'published', 1, ?, ?)
            """,
            [
                entity_contract_id,
                entity_ref,
                display_name or entity_name.replace("_", " ").title(),
                _DEFAULT_TYPED_TIME_REF,
                json.dumps(grounding["fields"]),
                json.dumps(grounding["binding"]),
                now,
                now,
            ],
        )
        for position, key_ref in enumerate(normalized_keys, start=1):
            metadata.insert_ignore(
                "semantic_entity_key_refs",
                ["entity_contract_id", "position", "key_ref", "description"],
                [entity_contract_id, position, key_ref, None],
            )
        return entity_ref
    if existing["status"] != "published":
        metadata.execute(
            "UPDATE semantic_entity_contracts SET status = 'published' WHERE entity_contract_id = ?",
            [existing["entity_contract_id"]],
        )
    _ensure_entity_has_default_fields(
        metadata,
        entity_ref=entity_ref,
        fields=grounding["fields"],
        binding=grounding["binding"],
    )
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
    entity_ref = f"entity.{name}"
    ensure_published_typed_entity(
        metadata_store,
        entity_name=name,
        display_name=display_name,
        key_refs=keys,
    )
    if properties:
        metadata_store.execute(
            """
            UPDATE semantic_entity_contracts
            SET properties_json = ?
            WHERE entity_ref = ?
            """,
            [json.dumps(properties), entity_ref],
        )
    if primary_time_ref is not None:
        metadata_store.execute(
            """
            UPDATE semantic_entity_contracts
            SET primary_time_ref = ?
            WHERE entity_ref = ?
            """,
            [primary_time_ref, entity_ref],
        )
    if description:
        metadata_store.execute(
            """
            UPDATE semantic_entity_contracts
            SET description = ?
            WHERE entity_ref = ?
            """,
            [description, entity_ref],
        )
    row = metadata_store.query_one(
        "SELECT * FROM semantic_entity_contracts WHERE entity_ref = ?",
        [entity_ref],
    )
    if row is None:
        raise AssertionError(f"Expected typed entity fixture for {entity_ref}")
    return dict(row)


def publish_typed_entity(client: TestClient, entity_contract_id: str) -> dict[str, Any]:
    metadata_store = _metadata_store_from_client(client)
    metadata_store.execute(
        "UPDATE semantic_entity_contracts SET status = 'published' WHERE entity_contract_id = ?",
        [entity_contract_id],
    )
    row = metadata_store.query_one(
        "SELECT * FROM semantic_entity_contracts WHERE entity_contract_id = ?",
        [entity_contract_id],
    )
    if row is None:
        raise AssertionError(f"Expected typed entity fixture for {entity_contract_id}")
    return dict(row)


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
        f"""
        UPDATE semantic_entity_contracts
        SET properties_json = ?, revision = revision + 1, updated_at = {metadata_store.dialect.now_sql()}
        WHERE entity_contract_id = ?
        """,
        [json.dumps(merged), entity_contract_id],
    )
    row = metadata_store.query_one(
        "SELECT * FROM semantic_entity_contracts WHERE entity_contract_id = ?",
        [entity_contract_id],
    )
    if row is None:
        raise AssertionError(f"Expected typed entity fixture for {entity_contract_id}")
    return dict(row)


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
    if existing is None:
        now = datetime.now(UTC).isoformat()
        metadata.execute(
            """
            INSERT INTO semantic_time_objects (
                time_contract_id, time_ref, display_name, description,
                time_contract_version, business_anchor, measurement,
                operational_support, source_field_ref, catalog_metadata_json,
                status, revision, created_at, updated_at
            ) VALUES (?, ?, ?, '', 'time.v1', 0, 1, 0, ?, '{}', 'published', 1, ?, ?)
            """,
            [
                f"timec_{uuid4().hex[:24]}",
                time_ref,
                display_name,
                _entity_field_ref(_DEFAULT_TYPED_ENTITY_REF, "event_date"),
                now,
                now,
            ],
        )
        return time_ref
    if existing["status"] != "published":
        metadata.execute(
            "UPDATE semantic_time_objects SET status = 'published' WHERE time_contract_id = ?",
            [existing["time_contract_id"]],
        )
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
    if existing is None:
        now = datetime.now(UTC).isoformat()
        metadata.execute(
            """
            INSERT INTO semantic_dimension_contracts (
                dimension_contract_id, dimension_ref, display_name, description,
                dimension_contract_version, structure_kind, semantic_role, value_type,
                domain_kind, supports_grouping, dimension_payload_json,
                catalog_metadata_json, status, revision, created_at, updated_at
            ) VALUES (?, ?, ?, '', 'dimension.v1', 'flat', 'category', 'string',
                'open', 1, '{}', '{}', 'published', 1, ?, ?)
            """,
            [
                f"dimc_{uuid4().hex[:24]}",
                dimension_ref,
                display_name or dimension_name.replace("_", " ").title(),
                now,
                now,
            ],
        )
        return dimension_ref
    if existing["status"] != "published":
        metadata.execute(
            "UPDATE semantic_dimension_contracts SET status = 'published' WHERE dimension_contract_id = ?",
            [existing["dimension_contract_id"]],
        )
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
    entity_ref = observed_entity_ref or ensure_published_typed_entity(
        metadata,
        measure_type=measure_type,
    )
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
        metric_contract_id = f"metc_{uuid4().hex[:24]}"
        now = datetime.now(UTC).isoformat()
        metadata.execute(
            """
            INSERT INTO semantic_metric_contracts (
                metric_contract_id, metric_ref, display_name, description,
                metric_family, observed_entity_ref, observation_grain_ref,
                sample_kind, value_semantics, aggregation_scope, primary_time_ref,
                additivity_constraints_json, metric_contract_version,
                family_payload_json, catalog_metadata_json, status, revision,
                is_latest_active, created_at, updated_at
            ) VALUES (?, ?, ?, '', ?, ?, ?, ?, ?, 'window', ?, ?, 'metric.v1',
                ?, '{}', 'published', 1, 1, ?, ?)
            """,
            [
                metric_contract_id,
                metric_ref,
                display_name or metric_name.replace("_", " ").title(),
                metric_family,
                entity_ref,
                f"grain.{grain or 'row'}",
                sample_kind,
                value_semantics,
                primary_time_ref,
                json.dumps(additivity_constraints),
                json.dumps(
                    _metric_payload_with_default_input_fields(metric_name, measure_type, entity_ref)
                ),
                now,
                now,
            ],
        )
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
    if "observed_dataset" not in family_payload:
        dataset_row = metadata.query_one(
            """
            SELECT name, source, datasource_id
            FROM semantic_datasets
            WHERE datasource_id IS NOT NULL
            ORDER BY updated_at DESC, dataset_id
            LIMIT 1
            """
        )
        if dataset_row is not None:
            family_payload["observed_dataset"] = str(dataset_row["name"])
            family_payload["dataset_source"] = str(dataset_row["source"])
            family_payload["datasource_id"] = str(dataset_row["datasource_id"])
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
        metadata.execute(
            "UPDATE semantic_metric_contracts SET status = 'published', is_latest_active = 1 WHERE metric_contract_id = ?",
            [metric_contract_id],
        )
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
    observed_entity_ref = entity_ref or ensure_published_typed_entity(
        metadata_store,
        measure_type=measure_type,
    )
    primary_time_ref = ensure_published_typed_time(metadata_store)
    for dimension_name in dimensions:
        if dimension_name.startswith("dimension."):
            ensure_published_typed_dimension(
                metadata_store,
                dimension_name=dimension_name.removeprefix("dimension."),
            )
        elif dimension_name != "event_date":
            ensure_published_typed_dimension(metadata_store, dimension_name=dimension_name)

    metric_ref = ensure_published_typed_metric(
        metadata_store,
        metric_name=name,
        display_name=display_name,
        observed_entity_ref=observed_entity_ref,
        grain=grain,
        dimensions=dimensions,
        definition_sql=definition_sql,
        measure_type=measure_type,
        allowed_dimensions=allowed_dimensions,
        quality_expectations=quality_expectations,
        desired_direction=desired_direction,
    )
    row = metadata_store.query_one(
        "SELECT metric_contract_id, family_payload_json FROM semantic_metric_contracts WHERE metric_ref = ?",
        [metric_ref],
    )
    if row is None:
        raise AssertionError(f"Expected typed metric fixture for {metric_ref}")
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
        [description, json.dumps(family_payload), row["metric_contract_id"]],
    )
    updated = metadata_store.query_one(
        "SELECT * FROM semantic_metric_contracts WHERE metric_contract_id = ?",
        [row["metric_contract_id"]],
    )
    if updated is None:
        raise AssertionError(f"Expected typed metric fixture for {metric_ref}")
    return dict(updated)


def publish_typed_metric(client: TestClient, metric_contract_id: str) -> dict[str, Any]:
    metadata_store = _metadata_store_from_client(client)
    metadata_store.execute(
        """
        UPDATE semantic_metric_contracts
        SET status = 'published', is_latest_active = 1
        WHERE metric_contract_id = ?
        """,
        [metric_contract_id],
    )
    row = metadata_store.query_one(
        "SELECT * FROM semantic_metric_contracts WHERE metric_contract_id = ?",
        [metric_contract_id],
    )
    if row is None:
        raise AssertionError(f"Expected typed metric fixture for {metric_contract_id}")
    return dict(row)


def ensure_published_typed_metric_binding(
    metadata: MetadataStore,
    *,
    metric_name: str,
    carrier_locator: str | dict[str, Any],
    source_object_ref: str | None = None,
    binding_role: str = "primary",
    surface_name: str = "value",
    surface_physical_name: str | None = None,
    dimension_names: Sequence[str] | None = None,
    metric_input_target_keys: Sequence[str] | None = None,
    binding_imports: Sequence[dict[str, Any]] | None = None,
    field_surfaces_extra: Sequence[dict[str, Any]] | None = None,
    time_surfaces: Sequence[dict[str, Any]] | None = None,
    time_axis_links: Sequence[dict[str, Any]] | None = None,
) -> str:
    binding_ref = f"binding.{metric_name}_primary"
    ensure_published_typed_metric(
        metadata,
        metric_name=metric_name,
        display_name=metric_name,
        dimensions=dimension_names,
    )
    for dimension_name in dimension_names or []:
        if dimension_name != "event_date":
            ensure_published_typed_dimension(metadata, dimension_name=dimension_name)

    _ = carrier_locator
    _ = source_object_ref
    _ = binding_role
    _ = surface_name
    _ = surface_physical_name
    _ = metric_input_target_keys
    _ = binding_imports
    _ = field_surfaces_extra
    _ = time_surfaces
    _ = time_axis_links
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
    _ = client
    _ = object_id
    _ = carrier_locator
    _ = binding_role
    _ = mapping_type
    _ = metric_input_target_keys
    raise NotImplementedError("Typed metric binding fixtures were removed")
