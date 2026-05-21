from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from marivo.adapters.metadata import MetadataStore
from marivo.ports.analytics import AnalyticsEngine
from marivo.runtime.runtime import MarivoRuntime

# Stub names for deleted model types — these are no longer functional.
# Contract-era entity/time/dimension helpers still target removed tables.
# Active metric fixtures have been migrated to the OSI v2 storage layout.
TypedMetricCreateRequest = None  # type: ignore[assignment,misc]
TimeCreateRequest = None  # type: ignore[assignment,misc]
DimensionCreateRequest = None  # type: ignore[assignment,misc]

_DEFAULT_TYPED_ENTITY_REF = "entity.synthetic_subject"
_DEFAULT_TYPED_ENTITY_KEY = "key.synthetic_subject_id"
_DEFAULT_TYPED_TIME_REF = "time.event_date"


def _entity_field_ref(entity_ref: str, field_name: str) -> str:
    return f"{entity_ref}.field.{field_name.removeprefix('field.')}"


def _default_metric_aggregation_semantics(entity_ref: str, measure_type: str | None) -> str:
    kind = str(measure_type or "count").strip().lower()
    if kind in {"ratio", "rate"}:
        return "ratio"
    return "sum"


def _metric_payload_with_default_input_fields(
    metric_name: str, measure_type: str | None, entity_ref: str
) -> dict[str, Any]:
    payload = _metric_payload_for_measure_type(metric_name, measure_type)
    _ = _default_metric_aggregation_semantics(entity_ref, measure_type)
    return payload


def _default_entity_fields_for_metric(
    entity_ref: str,
    measure_type: str | None,
    *,
    source_object_fqn: str = "analytics.watch_events",
) -> dict[str, Any]:
    _ = source_object_fqn
    aggregation_semantics = _default_metric_aggregation_semantics(entity_ref, measure_type)
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
    if aggregation_semantics == "ratio":
        for field_name in ("numerator", "denominator"):
            fields.setdefault(
                field_name,
                {
                    "field_ref": f"field.{field_name}",
                    "value_type": "number",
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


def build_runtime(
    metadata: MetadataStore,
    analytics: AnalyticsEngine,
) -> MarivoRuntime:
    """Construct a MarivoRuntime suitable for tests.

    This is the preferred helper for tests.  It builds the
    CoreEngine + RuntimePorts stack and returns the ``MarivoRuntime`` facade.

    The metadata store and analytics engine are wired into the ports
    container so that runtime.metadata and runtime.analytics
    are available for tests that need direct store access.

    A real ``MetadataArtifactStoreAdapter`` is wired as ``ports.artifact_store``
    so that ``runtime.commit_artifact_with_extraction(...)`` operates against
    the real metadata store (required by extraction boundary tests).

    A real ``MetadataSessionStoreAdapter`` is wired as ``ports.session_store``
    so that ``runtime.create_session(...)`` operates against the real metadata
    store.
    """
    from marivo.adapters.server.artifact_store import (
        MetadataArtifactStoreAdapter,
        MetadataStepStoreAdapter,
    )
    from marivo.adapters.server.audit_log import FileAuditLogAdapter
    from marivo.adapters.server.authz import NoopAuthZAdapter
    from marivo.adapters.server.cache_store import InMemoryCacheStore
    from marivo.adapters.server.data_source import RoutingDataSource
    from marivo.adapters.server.session_store import SqlSessionStoreAdapter
    from marivo.adapters.server.telemetry import LocalTelemetryAdapter
    from marivo.config import MarivoConfig
    from marivo.core.engine import CoreEngine
    from marivo.datasources import DatasourceService
    from marivo.routing import QueryRouter
    from marivo.runtime.evidence.semantic_repository import SemanticRuntimeRepository
    from marivo.runtime.ports import RuntimePorts
    from marivo.time_axis_metadata import TimeAxisMetadataProvider

    class _NoopModelStore:
        def get(self, selector: object) -> None:
            _ = selector
            return None

        def save(self, model: object, *, actor: object) -> int:
            _ = (model, actor)
            return 1

        def list(self, query: object) -> list[object]:
            _ = query
            return []

    class _NoopEvidenceStore:
        def write(self, evidence: object) -> str:
            _ = evidence
            return "evidence.test"

        def read(self, ref: object) -> object:
            raise KeyError(ref)

    datasource_service = DatasourceService(metadata)
    query_router = QueryRouter(metadata, datasource_service)
    ports = RuntimePorts(
        model_store=_NoopModelStore(),
        session_store=SqlSessionStoreAdapter(metadata),
        evidence_store=_NoopEvidenceStore(),
        data_source=RoutingDataSource(
            registry=datasource_service,
            query_router=query_router,
            default_engine=analytics,
        ),
        cache_store=InMemoryCacheStore(),
        authz=NoopAuthZAdapter(),
        audit_log=FileAuditLogAdapter(),
        telemetry=LocalTelemetryAdapter(),
        runtime_config=type(
            "_TestRuntimeConfig",
            (),
            {"get": lambda self, key: getattr(MarivoConfig(), key, None)},
        )(),
        artifact_store=MetadataArtifactStoreAdapter(metadata),
        step_store=MetadataStepStoreAdapter(metadata),
    )
    core = CoreEngine()
    runtime = MarivoRuntime(ports, core)
    runtime.register_service("datasource", datasource_service)
    runtime.register_service("query_router", query_router)
    runtime.register_service("semantic_repository", SemanticRuntimeRepository(metadata))
    runtime.wire_metadata(metadata)
    runtime.wire_analytics(analytics)
    runtime.wire_time_axis_metadata_provider(TimeAxisMetadataProvider(metadata))
    return runtime


def build_semantic_layer_service(
    metadata: MetadataStore,
    analytics: AnalyticsEngine,
) -> Any:
    """Backward-compatible helper: returns a MarivoRuntime.

    Prefer ``build_runtime`` for new tests.
    """
    return build_runtime(metadata, analytics)


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
                "created_at",
                "updated_at",
            ],
            [
                None,
                f"fixture_{dataset_name}",
                f"Fixture model for {table_fqn}",
                "public",
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
            "measure": {
                "name": metric_name,
                "semantics": f"Summed measure for {metric_name}",
                "aggregation": "sum",
            },
        }
    return {}


def _metric_header_axes(measure_type: str | None) -> tuple[str, str, list[str]]:
    kind = str(measure_type or "count").strip().lower()
    if kind in {"percentile", "quantile"}:
        return (
            "distribution_statistic",
            "distribution_statistic",
            [],
        )
    if kind in {"ratio", "rate"}:
        return (
            "ratio",
            "ratio",
            [],
        )
    if kind in {"average", "mean"}:
        return (
            "weighted_average",
            "mean",
            [],
        )
    if kind == "sum":
        return (
            "sum",
            "sum",
            ["country", "device", "date"],
        )
    return (
        "sum",
        "count",
        ["country", "device", "date"],
    )


def _latest_fixture_dataset(metadata: MetadataStore) -> dict[str, Any] | None:
    return metadata.query_one(
        """
        SELECT dataset_id, model_id, name, source, datasource_id
        FROM semantic_datasets
        WHERE datasource_id IS NOT NULL AND datasource_id != ''
        ORDER BY updated_at DESC, dataset_id DESC
        LIMIT 1
        """
    )


def _ensure_dataset_field(
    metadata: MetadataStore,
    *,
    dataset_id: int,
    field_name: str,
    expression_name: str,
    is_time: bool,
    is_dimension: bool,
    data_type: str | None,
    support_min_granularity: str | None = None,
) -> None:
    existing = metadata.query_one(
        "SELECT field_id, position FROM semantic_fields WHERE dataset_id = ? AND name = ?",
        [dataset_id, field_name],
    )
    expression = json.dumps({"dialects": [{"dialect": "ANSI_SQL", "expression": expression_name}]})
    if existing is None:
        position_row = metadata.query_one(
            "SELECT COALESCE(MAX(position), -1) AS max_position FROM semantic_fields WHERE dataset_id = ?",
            [dataset_id],
        )
        next_position = int(position_row["max_position"]) + 1 if position_row is not None else 0
        now = datetime.now(UTC).isoformat()
        metadata.execute(
            """
            INSERT INTO semantic_fields (
                dataset_id, name, expression, is_time, is_dimension,
                data_type, support_min_granularity, position, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                dataset_id,
                field_name,
                expression,
                1 if is_time else 0,
                1 if is_dimension else 0,
                data_type,
                support_min_granularity if is_time else None,
                next_position,
                now,
                now,
            ],
        )
        return
    metadata.execute(
        """
        UPDATE semantic_fields
        SET expression = ?, is_time = ?, is_dimension = ?, data_type = ?,
            support_min_granularity = ?,
            updated_at = datetime('now')
        WHERE field_id = ?
        """,
        [
            expression,
            1 if is_time else 0,
            1 if is_dimension else 0,
            data_type,
            support_min_granularity if is_time else None,
            existing["field_id"],
        ],
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
                fields_json, binding_json, status, revision,
                created_at, updated_at
            ) VALUES (?, ?, ?, '', '{}', '{}', 'entity.v4', 'business_entity',
                'global', 'stable', 'reject', ?, ?, 'published', 1, ?, ?)
            """,
            [
                entity_contract_id,
                entity_ref,
                display_name or entity_name.replace("_", " ").title(),
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
        "SELECT metric_id, model_id FROM semantic_metrics WHERE name = ?",
        [metric_name],
    )
    dataset_row = _latest_fixture_dataset(metadata)
    if existing is None and dataset_row is None:
        raise ValueError(
            "ensure_published_typed_metric requires a seeded semantic_datasets row "
            "with datasource_id before inserting semantic_metrics"
        )
    model_id = int(existing["model_id"]) if existing is not None else int(dataset_row["model_id"])
    if dataset_row is None:
        dataset_row = metadata.query_one(
            """
            SELECT dataset_id, model_id, name, source, datasource_id
            FROM semantic_datasets
            WHERE model_id = ?
            ORDER BY updated_at DESC, dataset_id DESC
            LIMIT 1
            """,
            [model_id],
        )
    if dataset_row is None:
        raise ValueError(
            f"Model {model_id} has no semantic_datasets row for metric '{metric_name}'"
        )

    dimension_names = list(dimensions or [])
    aggregation_semantics, _, _ = _metric_header_axes(measure_type)
    metric_sql = definition_sql or "COUNT(*)"

    for dimension_name in dimension_names:
        physical_name = dimension_name.removeprefix("dimension.")
        is_time = physical_name == "event_date"
        _ensure_dataset_field(
            metadata,
            dataset_id=int(dataset_row["dataset_id"]),
            field_name=dimension_name,
            expression_name=physical_name,
            is_time=is_time,
            is_dimension=True,
            data_type="date" if is_time else None,
            support_min_granularity="day" if is_time else None,
        )
    metric_expression = json.dumps(
        {"dialects": [{"dialect": "ANSI_SQL", "expression": metric_sql}]}
    )
    metric_description_parts = [
        display_name or metric_name.replace("_", " ").title(),
        f"grain={grain or 'row'}",
    ]
    if measure_type is not None:
        metric_description_parts.append(f"measure_type={measure_type}")
    if desired_direction is not None:
        metric_description_parts.append(f"desired_direction={desired_direction}")
    if quality_expectations:
        metric_description_parts.append(
            f"quality_expectations={json.dumps(quality_expectations, sort_keys=True)}"
        )
    description = " | ".join(metric_description_parts)
    if existing is None:
        metadata.execute(
            """
            INSERT INTO semantic_metrics (
                model_id, name, expression, description, aggregation_semantics
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                model_id,
                metric_name,
                metric_expression,
                description,
                aggregation_semantics,
            ],
        )
    else:
        metadata.execute(
            """
            UPDATE semantic_metrics
            SET expression = ?, description = ?, aggregation_semantics = ?,
                updated_at = datetime('now')
            WHERE metric_id = ?
            """,
            [
                metric_expression,
                description,
                aggregation_semantics,
                existing["metric_id"],
            ],
        )
    return metric_ref


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

    _ = carrier_locator
    _ = source_object_ref
    _ = binding_role
    _ = surface_name
    _ = surface_physical_name
    _ = binding_imports
    _ = field_surfaces_extra
    _ = time_surfaces
    _ = time_axis_links
    return binding_ref
