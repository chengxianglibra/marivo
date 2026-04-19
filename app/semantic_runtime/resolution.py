from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, ClassVar, cast

from app.analysis_core.calendar_policy import calendar_policy_catalog_entry
from app.semantic_readiness import ObjectKind, SemanticReadinessService
from app.semantic_runtime.dimensions import resolve_entity_binding_dimensions
from app.semantic_runtime.errors import (
    SemanticRuntimeInvalidRefError,
    SemanticRuntimeNotFoundError,
    SemanticRuntimeNotReadyError,
    SemanticRuntimeUnpublishedError,
)
from app.semantic_runtime.semantic_metadata import runtime_ref_kind
from app.storage.metadata import MetadataStore


@dataclass(slots=True)
class ResolvedSemanticObject:
    object_kind: str
    object_id: str
    ref: str
    semantic_object: dict[str, Any]
    status: str
    revision: int
    created_at: str
    updated_at: str


@dataclass(slots=True)
class ResolvedMetric:
    name: str
    metric_ref: str = ""
    display_name: str = ""
    description: str = ""
    metric_family: str = ""
    population_subject_ref: str | None = None
    observed_entity_ref: str = ""
    observation_grain_ref: str = ""
    sample_kind: str = ""
    value_semantics: str = ""
    aggregation_scope: str | None = None
    primary_time_ref: str | None = None
    additivity: str = ""
    metric_contract_version: str = ""
    family_payload: dict[str, Any] = field(default_factory=dict)
    definition_sql: str | None = None
    dimensions: list[str] = field(default_factory=list)
    grain: str | None = None
    measure_type: str | None = None
    allowed_dimensions: list[str] = field(default_factory=list)
    lineage: list[str] = field(default_factory=list)
    quality_expectations: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    desired_direction: str | None = None


@dataclass(slots=True)
class ResolvedEntity:
    name: str
    entity_ref: str = ""
    display_name: str = ""
    description: str = ""
    entity_contract_version: str = ""
    key_refs: list[str] = field(default_factory=list)
    uniqueness_scope: str = ""
    id_stability: str = ""
    nullable_key_policy: str = ""
    parent_entity_ref: str | None = None
    cardinality_to_parent: str | None = None
    ownership_semantics: str | None = None
    primary_time_ref: str | None = None
    stable_descriptors: list[dict[str, Any]] = field(default_factory=list)
    keys: list[str] = field(default_factory=list)
    level: str | None = None
    join_constraints: dict[str, Any] = field(default_factory=dict)
    upstream_dependencies: list[str] = field(default_factory=list)
    lineage: list[str] = field(default_factory=list)
    quality_expectations: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RuntimeSemanticAvailability:
    resolved: ResolvedSemanticObject
    lifecycle_status: str
    readiness_status: str
    blocking_requirements: list[dict[str, Any]] = field(default_factory=list)
    capabilities: dict[str, Any] = field(default_factory=dict)
    dependency_refs: list[str] = field(default_factory=list)

    @property
    def is_active(self) -> bool:
        return self.lifecycle_status == "active"

    @property
    def is_ready(self) -> bool:
        return self.readiness_status == "ready"


class SemanticRuntimeMetadataReader:
    """Read typed semantic objects from metadata without exposing service mutators."""

    _RUNTIME_CONFIG: ClassVar[dict[str, tuple[str, str, str]]] = {
        "entity": ("semantic_entity_contracts", "entity_ref", "_row_to_typed_entity"),
        "metric": ("semantic_metric_contracts", "metric_ref", "_row_to_typed_metric"),
        "process": ("semantic_process_objects", "process_ref", "_row_to_process_object"),
        "dimension": ("semantic_dimension_contracts", "dimension_ref", "_row_to_dimension"),
        "time": ("semantic_time_objects", "time_ref", "_row_to_time_semantic"),
        "binding": ("typed_bindings", "binding_ref", "_row_to_typed_binding"),
    }

    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata

    def load_by_ref(
        self, semantic_ref: str, *, published_only: bool = True
    ) -> tuple[str, dict[str, Any]] | None:
        object_kind = runtime_ref_kind(semantic_ref)
        if object_kind is None:
            return None
        table_name, ref_column, converter_name = self._RUNTIME_CONFIG[object_kind]
        status_predicate = " AND status = 'published'" if published_only else ""
        row = self.metadata.query_one(
            f"SELECT * FROM {table_name} WHERE {ref_column} = ?{status_predicate}",
            [semantic_ref],
        )
        if row is None:
            return None
        converter = getattr(self, converter_name)
        return object_kind, converter(row)

    def _row_to_typed_entity(self, row: dict[str, Any]) -> dict[str, Any]:
        key_rows = self.metadata.query_rows(
            """
            SELECT key_ref
            FROM semantic_entity_key_refs
            WHERE entity_contract_id = ?
            ORDER BY position
            """,
            [row["entity_contract_id"]],
        )
        descriptor_rows = self.metadata.query_rows(
            """
            SELECT dimension_ref, cardinality
            FROM semantic_entity_stable_descriptors
            WHERE entity_contract_id = ?
            ORDER BY position
            """,
            [row["entity_contract_id"]],
        )
        hierarchy = None
        if row["parent_entity_ref"] is not None:
            hierarchy = {
                "parent_entity_ref": row["parent_entity_ref"],
                "cardinality_to_parent": row["cardinality_to_parent"],
                "ownership_semantics": row["ownership_semantics"],
            }
        return {
            "entity_contract_id": row["entity_contract_id"],
            "header": {
                "entity_ref": row["entity_ref"],
                "display_name": row["display_name"],
                "description": row["description"],
                "entity_contract_version": row["entity_contract_version"],
            },
            "interface_contract": {
                "identity": {
                    "key_refs": [key_row["key_ref"] for key_row in key_rows],
                    "uniqueness_scope": row["uniqueness_scope"],
                    "id_stability": row["id_stability"],
                    "nullable_key_policy": row["nullable_key_policy"],
                },
                "hierarchy": hierarchy,
                "primary_time_ref": row["primary_time_ref"],
                "stable_descriptors": (
                    [
                        {
                            "dimension_ref": descriptor_row["dimension_ref"],
                            "cardinality": descriptor_row["cardinality"],
                        }
                        for descriptor_row in descriptor_rows
                    ]
                    if descriptor_rows
                    else None
                ),
            },
            "status": row["status"],
            "revision": row["revision"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _row_to_typed_metric(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "metric_contract_id": row["metric_contract_id"],
            "header": {
                "metric_ref": row["metric_ref"],
                "display_name": row["display_name"],
                "description": row["description"],
                "metric_family": row["metric_family"],
                "population_subject_ref": row["population_subject_ref"],
                "observed_entity_ref": row["observed_entity_ref"],
                "observation_grain_ref": row["observation_grain_ref"],
                "sample_kind": row["sample_kind"],
                "value_semantics": row["value_semantics"],
                "aggregation_scope": row["aggregation_scope"],
                "primary_time_ref": row["primary_time_ref"],
                "additivity": row["additivity"],
                "metric_contract_version": row["metric_contract_version"],
            },
            "payload": json.loads(row["family_payload_json"]),
            "status": row["status"],
            "revision": row["revision"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _row_to_process_object(self, row: dict[str, Any]) -> dict[str, Any]:
        exported_dimension_rows = self.metadata.query_rows(
            """
            SELECT dimension_ref
            FROM semantic_process_exported_dimension_refs
            WHERE process_contract_id = ?
            ORDER BY position
            """,
            [row["process_contract_id"]],
        )
        interface_contract: dict[str, Any] = {
            "contract_mode": row["contract_mode"],
            "population_subject_ref": row["population_subject_ref"],
            "anchor_time_ref": row["anchor_time_ref"],
            "exported_dimension_refs": [
                exported_dimension_row["dimension_ref"]
                for exported_dimension_row in exported_dimension_rows
            ],
        }
        if row["contract_mode"] == "context_provider":
            interface_contract["context_kind"] = row["context_kind"]
            interface_contract["membership_cardinality"] = row["membership_cardinality"]
        else:
            interface_contract["entity_ref"] = row["entity_ref"]
            interface_contract["emitted_grain_ref"] = row["emitted_grain_ref"]
            interface_contract["subject_cardinality"] = row["subject_cardinality"]
        return {
            "process_contract_id": row["process_contract_id"],
            "header": {
                "process_ref": row["process_ref"],
                "display_name": row["display_name"],
                "description": row["description"],
                "process_type": row["process_type"],
                "process_contract_version": row["process_contract_version"],
            },
            "interface_contract": interface_contract,
            "payload": json.loads(row["process_payload_json"]),
            "status": row["status"],
            "revision": row["revision"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _row_to_dimension(self, row: dict[str, Any]) -> dict[str, Any]:
        value_domain: dict[str, Any] = {
            "structure_kind": row["structure_kind"],
            "semantic_role": row["semantic_role"],
            "value_type": row["value_type"],
            "domain_kind": row["domain_kind"],
            "enum_set_ref": row["enum_set_ref"],
            "enum_version": row["enum_version"],
        }
        interface_contract: dict[str, Any] = {"value_domain": value_domain}
        if row["hierarchy_type"] is not None:
            interface_contract["hierarchy"] = {
                "hierarchy_type": row["hierarchy_type"],
                "parent_dimension_ref": row["parent_dimension_ref"],
            }
        interface_contract["grouping"] = {"supports_grouping": bool(row["supports_grouping"])}
        if row["required_time_anchor_ref"] is not None:
            interface_contract["time_derived_requirement"] = {
                "required_time_anchor_ref": row["required_time_anchor_ref"],
            }
        return {
            "dimension_contract_id": row["dimension_contract_id"],
            "header": {
                "dimension_ref": row["dimension_ref"],
                "display_name": row["display_name"],
                "description": row["description"],
                "dimension_contract_version": row["dimension_contract_version"],
            },
            "interface_contract": interface_contract,
            "status": row["status"],
            "revision": row["revision"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _row_to_time_semantic(self, row: dict[str, Any]) -> dict[str, Any]:
        semantic_roles: list[str] = []
        if row["business_anchor"]:
            semantic_roles.append("business_anchor")
        if row["measurement"]:
            semantic_roles.append("measurement")
        if row["operational_support"]:
            semantic_roles.append("operational_support")
        return {
            "time_contract_id": row["time_contract_id"],
            "header": {
                "time_ref": row["time_ref"],
                "display_name": row["display_name"],
                "description": row["description"],
                "semantic_roles": semantic_roles,
                "time_contract_version": row["time_contract_version"],
            },
            "status": row["status"],
            "revision": row["revision"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _row_to_typed_binding(self, row: dict[str, Any]) -> dict[str, Any]:
        import_rows = self.metadata.query_rows(
            """
            SELECT import_key, imported_binding_ref, required_ref_prefixes_json
            FROM binding_imports
            WHERE binding_id = ?
            ORDER BY id
            """,
            [row["binding_id"]],
        )
        carrier_rows = self.metadata.query_rows(
            """
            SELECT *
            FROM carrier_bindings
            WHERE binding_id = ?
            ORDER BY binding_key
            """,
            [row["binding_id"]],
        )
        carriers: list[dict[str, Any]] = []
        for carrier_row in carrier_rows:
            field_surface_rows = self.metadata.query_rows(
                """
                SELECT surface_ref, physical_name, field_type
                FROM carrier_field_surfaces
                WHERE carrier_binding_id = ?
                ORDER BY position
                """,
                [carrier_row["carrier_binding_id"]],
            )
            time_surface_rows = self.metadata.query_rows(
                """
                SELECT surface_ref, physical_name, time_granularity
                FROM carrier_time_surfaces
                WHERE carrier_binding_id = ?
                ORDER BY position
                """,
                [carrier_row["carrier_binding_id"]],
            )
            carriers.append(
                {
                    "binding_key": carrier_row["binding_key"],
                    "source_object_ref": carrier_row["source_object_ref"],
                    "carrier_kind": carrier_row["carrier_kind"],
                    "carrier_locator": carrier_row["carrier_locator"],
                    "binding_role": carrier_row["binding_role"],
                    "semantic_role_ref": carrier_row["semantic_role_ref"],
                    "grain_ref": carrier_row["grain_ref"],
                    "primary_entity_ref": carrier_row["primary_entity_ref"],
                    "row_filter_refs": json.loads(carrier_row["row_filter_refs_json"]),
                    "freshness_policy_ref": carrier_row["freshness_policy_ref"],
                    "field_surfaces": [dict(surface_row) for surface_row in field_surface_rows]
                    if field_surface_rows
                    else None,
                    "time_surfaces": [dict(surface_row) for surface_row in time_surface_rows]
                    if time_surface_rows
                    else None,
                }
            )
        field_binding_rows = self.metadata.query_rows(
            """
            SELECT carrier_binding_key, target_kind, target_key, context_ref, semantic_ref,
                   surface_ref, field_type_ref, nullability_policy, repeated_value_policy
            FROM field_bindings
            WHERE binding_id = ?
            ORDER BY carrier_binding_key, target_kind, target_key
            """,
            [row["binding_id"]],
        )
        time_binding_rows = self.metadata.query_rows(
            """
            SELECT carrier_binding_key, target_kind, target_key, context_ref, semantic_ref,
                   resolution_kind, timestamp_surface_ref, timestamp_format,
                   date_surface_ref, date_format,
                   hour_surface_ref, hour_format, timezone_strategy
            FROM time_bindings
            WHERE binding_id = ?
            ORDER BY carrier_binding_key, target_kind, target_key, semantic_ref
            """,
            [row["binding_id"]],
        )
        join_rows = self.metadata.query_rows(
            """
            SELECT relation_key, left_binding_key, right_binding_key, join_kind,
                   key_ref_pairs_json, cardinality, temporal_constraint_refs_json,
                   compatibility_rule_refs_json
            FROM join_relations
            WHERE binding_id = ?
            ORDER BY relation_key
            """,
            [row["binding_id"]],
        )
        policy_rows = self.metadata.query_rows(
            """
            SELECT policy_key, policy_type, policy_target_path, anchor_ref,
                   grace_period_ref, behavior
            FROM consumption_policies
            WHERE binding_id = ?
            ORDER BY policy_key
            """,
            [row["binding_id"]],
        )
        return {
            "binding_id": row["binding_id"],
            "header": {
                "binding_ref": row["binding_ref"],
                "display_name": row["display_name"],
                "description": row["description"],
                "binding_scope": row["binding_scope"],
                "bound_object_ref": row["bound_object_ref"],
                "binding_contract_version": row["binding_contract_version"],
            },
            "interface_contract": {
                "imports": [
                    {
                        "import_key": import_row["import_key"],
                        "binding_ref": import_row["imported_binding_ref"],
                        "required_ref_prefixes": json.loads(
                            import_row["required_ref_prefixes_json"]
                        ),
                    }
                    for import_row in import_rows
                ],
                "carrier_bindings": carriers,
                "field_bindings": [
                    {
                        "carrier_binding_key": field_binding_row["carrier_binding_key"],
                        "target": {
                            "target_kind": field_binding_row["target_kind"],
                            "target_key": field_binding_row["target_key"],
                            "context_ref": field_binding_row["context_ref"],
                        },
                        "semantic_ref": field_binding_row["semantic_ref"],
                        "surface_ref": field_binding_row["surface_ref"],
                        "field_type_ref": field_binding_row["field_type_ref"],
                        "nullability_policy": field_binding_row["nullability_policy"],
                        "repeated_value_policy": field_binding_row["repeated_value_policy"],
                    }
                    for field_binding_row in field_binding_rows
                ],
                "time_bindings": [
                    {
                        "carrier_binding_key": time_binding_row["carrier_binding_key"],
                        "target": {
                            "target_kind": time_binding_row["target_kind"],
                            "target_key": time_binding_row["target_key"],
                            "context_ref": time_binding_row["context_ref"],
                        },
                        "semantic_ref": time_binding_row["semantic_ref"],
                        "resolution_kind": time_binding_row["resolution_kind"],
                        "timestamp_surface_ref": time_binding_row["timestamp_surface_ref"],
                        "timestamp_format": time_binding_row["timestamp_format"],
                        "date_surface_ref": time_binding_row["date_surface_ref"],
                        "date_format": time_binding_row["date_format"],
                        "hour_surface_ref": time_binding_row["hour_surface_ref"],
                        "hour_format": time_binding_row["hour_format"],
                        "timezone_strategy": time_binding_row["timezone_strategy"],
                    }
                    for time_binding_row in time_binding_rows
                ],
                "join_relations": [
                    {
                        "relation_key": join_row["relation_key"],
                        "left_binding_key": join_row["left_binding_key"],
                        "right_binding_key": join_row["right_binding_key"],
                        "join_kind": join_row["join_kind"],
                        "key_ref_pairs": json.loads(join_row["key_ref_pairs_json"]),
                        "cardinality": join_row["cardinality"],
                        "temporal_constraint_refs": json.loads(
                            join_row["temporal_constraint_refs_json"]
                        ),
                        "compatibility_rule_refs": json.loads(
                            join_row["compatibility_rule_refs_json"]
                        ),
                    }
                    for join_row in join_rows
                ],
                "consumption_policies": [
                    {
                        "policy_key": policy_row["policy_key"],
                        "policy_type": policy_row["policy_type"],
                        "policy_target_path": policy_row["policy_target_path"],
                        "anchor_ref": policy_row["anchor_ref"],
                        "grace_period_ref": policy_row["grace_period_ref"],
                        "behavior": policy_row["behavior"],
                    }
                    for policy_row in policy_rows
                ],
            },
            "status": row["status"],
            "revision": row["revision"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }


class SemanticResolver:
    """Resolve published semantic objects into typed runtime models."""

    _OBJECT_ID_FIELDS: ClassVar[dict[str, str]] = {
        "entity": "entity_contract_id",
        "metric": "metric_contract_id",
        "process": "process_contract_id",
        "dimension": "dimension_contract_id",
        "time": "time_contract_id",
        "binding": "binding_id",
        "calendar_policy": "policy_ref",
    }
    _REF_FIELDS: ClassVar[dict[str, str]] = {
        "entity": "entity_ref",
        "metric": "metric_ref",
        "process": "process_ref",
        "dimension": "dimension_ref",
        "time": "time_ref",
        "binding": "binding_ref",
        "calendar_policy": "policy_ref",
    }

    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata
        self.loader = SemanticRuntimeMetadataReader(metadata)
        self.readiness_service = SemanticReadinessService(metadata)

    def resolve_ref(self, semantic_ref: str) -> ResolvedSemanticObject:
        availability = self.inspect_ref(semantic_ref)
        if not availability.is_active:
            raise SemanticRuntimeUnpublishedError(
                f"Semantic ref is not active: {semantic_ref}",
                semantic_ref=semantic_ref,
            )
        if not availability.is_ready:
            raise SemanticRuntimeNotReadyError(
                f"Semantic ref is not ready: {semantic_ref}",
                semantic_ref=semantic_ref,
                object_kind=availability.resolved.object_kind,
                lifecycle_status=availability.lifecycle_status,
                readiness_status=availability.readiness_status,
                blocking_requirements=availability.blocking_requirements,
                capabilities=availability.capabilities,
                dependency_refs=availability.dependency_refs,
            )
        return availability.resolved

    def inspect_ref(self, semantic_ref: str) -> RuntimeSemanticAvailability:
        object_kind = runtime_ref_kind(semantic_ref)
        if object_kind is None:
            raise SemanticRuntimeInvalidRefError(
                f"Unsupported semantic ref: {semantic_ref}",
                semantic_ref=semantic_ref,
            )
        if object_kind == "calendar_policy":
            entry = calendar_policy_catalog_entry(semantic_ref)
            resolved = ResolvedSemanticObject(
                object_kind="calendar_policy",
                object_id=entry.object_id,
                ref=entry.policy_ref,
                semantic_object={
                    "policy_ref": entry.policy_ref,
                    "display_name": entry.display_name,
                    "description": entry.description,
                    "comparison_basis": entry.comparison_basis,
                    "resolved_alignment_mode": entry.resolved_alignment_mode,
                    "resolved_calendar_source": entry.resolved_calendar_source,
                    "window_tags": list(entry.window_tags),
                    "use_when": list(entry.use_when),
                    "avoid_when": list(entry.avoid_when),
                    "matching_strategy_summary": list(entry.matching_strategy_summary),
                    "fallback_strategy": list(entry.fallback_strategy),
                    "coverage_behavior": entry.coverage_behavior,
                    "system_managed": entry.system_managed,
                    "catalog_source": entry.catalog_source,
                    "status": entry.status,
                    "revision": entry.revision,
                    "created_at": entry.created_at,
                    "updated_at": entry.updated_at,
                },
                status=entry.status,
                revision=entry.revision,
                created_at=entry.created_at,
                updated_at=entry.updated_at,
            )
            return RuntimeSemanticAvailability(
                resolved=resolved,
                lifecycle_status=entry.lifecycle_status,
                readiness_status=entry.readiness_status,
                blocking_requirements=[],
                capabilities={"supports_observe_calendar_alignment": True},
                dependency_refs=[],
            )

        loaded = self.loader.load_by_ref(semantic_ref, published_only=False)
        if loaded is None:
            raise SemanticRuntimeNotFoundError(
                f"Unknown semantic ref: {semantic_ref}",
                semantic_ref=semantic_ref,
            )
        resolved = self._build_resolved_object(*loaded)
        readiness = self.readiness_service.evaluate_snapshot(
            object_kind=cast("ObjectKind", resolved.object_kind),
            object_id=resolved.object_id,
            ref=resolved.ref,
            status=resolved.status,
            revision=resolved.revision,
            semantic_object=dict(resolved.semantic_object),
        )
        return RuntimeSemanticAvailability(
            resolved=resolved,
            lifecycle_status=readiness.lifecycle_status,
            readiness_status=readiness.readiness_status,
            blocking_requirements=[item.to_dict() for item in readiness.blocking_requirements],
            capabilities=dict(readiness.capabilities),
            dependency_refs=_dependency_refs_for_object(
                object_kind=cast("ObjectKind", resolved.object_kind),
                semantic_object=resolved.semantic_object,
            ),
        )

    def resolve_entity_ref(self, entity_ref: str) -> ResolvedSemanticObject:
        return self._resolve_ref_of_kind(entity_ref, expected_kind="entity")

    def resolve_metric_ref(self, metric_ref: str) -> ResolvedSemanticObject:
        return self._resolve_ref_of_kind(metric_ref, expected_kind="metric")

    def resolve_process_ref(self, process_ref: str) -> ResolvedSemanticObject:
        return self._resolve_ref_of_kind(process_ref, expected_kind="process")

    def resolve_dimension_ref(self, dimension_ref: str) -> ResolvedSemanticObject:
        return self._resolve_ref_of_kind(dimension_ref, expected_kind="dimension")

    def resolve_time_ref(self, time_ref: str) -> ResolvedSemanticObject:
        return self._resolve_ref_of_kind(time_ref, expected_kind="time")

    def resolve_binding_ref(self, binding_ref: str) -> ResolvedSemanticObject:
        return self._resolve_ref_of_kind(binding_ref, expected_kind="binding")

    def resolve_metric(self, metric_name: str) -> ResolvedMetric | None:
        try:
            resolved_contract = self.resolve_metric_ref(f"metric.{metric_name}")
        except (
            SemanticRuntimeInvalidRefError,
            SemanticRuntimeNotFoundError,
            SemanticRuntimeNotReadyError,
            SemanticRuntimeUnpublishedError,
        ):
            return None

        semantic_object = resolved_contract.semantic_object
        header = semantic_object["header"]
        family_payload = dict(semantic_object["payload"])
        metric_ref = str(header["metric_ref"])

        return ResolvedMetric(
            name=metric_ref.removeprefix("metric."),
            metric_ref=str(header["metric_ref"]),
            display_name=str(header["display_name"]),
            description=str(header["description"]),
            metric_family=str(header["metric_family"]),
            population_subject_ref=header["population_subject_ref"],
            observed_entity_ref=str(header["observed_entity_ref"]),
            observation_grain_ref=str(header["observation_grain_ref"]),
            sample_kind=str(header["sample_kind"]),
            value_semantics=str(header["value_semantics"]),
            aggregation_scope=header["aggregation_scope"],
            primary_time_ref=header["primary_time_ref"],
            additivity=str(header["additivity"]),
            metric_contract_version=str(header["metric_contract_version"]),
            family_payload=family_payload,
            definition_sql=family_payload.get("definition_sql"),
            dimensions=_metric_dimensions(
                self.metadata,
                header=header,
                family_payload=family_payload,
            ),
            grain=family_payload.get("grain"),
            measure_type=family_payload.get("measure_type"),
            allowed_dimensions=list(family_payload.get("allowed_dimensions") or []),
            lineage=list(family_payload.get("lineage") or []),
            quality_expectations=dict(family_payload.get("quality_expectations") or {}),
            desired_direction=family_payload.get("desired_direction"),
            metadata={
                "metric_contract_id": semantic_object["metric_contract_id"],
                "display_name": header["display_name"],
                "description": header["description"],
                "status": resolved_contract.status,
                "revision": resolved_contract.revision,
                "created_at": resolved_contract.created_at,
                "updated_at": resolved_contract.updated_at,
            },
        )

    def resolve_entity(self, entity_name: str) -> ResolvedEntity | None:
        try:
            resolved_contract = self.resolve_entity_ref(f"entity.{entity_name}")
        except (
            SemanticRuntimeInvalidRefError,
            SemanticRuntimeNotFoundError,
            SemanticRuntimeNotReadyError,
            SemanticRuntimeUnpublishedError,
        ):
            return None

        semantic_object = resolved_contract.semantic_object
        header = semantic_object["header"]
        interface_contract = semantic_object["interface_contract"]
        identity = interface_contract["identity"]
        hierarchy = interface_contract.get("hierarchy") or {}
        entity_ref = str(header["entity_ref"])

        return ResolvedEntity(
            name=entity_ref.removeprefix("entity."),
            entity_ref=str(header["entity_ref"]),
            display_name=str(header["display_name"]),
            description=str(header["description"]),
            entity_contract_version=str(header["entity_contract_version"]),
            key_refs=list(identity["key_refs"]),
            uniqueness_scope=str(identity["uniqueness_scope"]),
            id_stability=str(identity["id_stability"]),
            nullable_key_policy=str(identity["nullable_key_policy"]),
            parent_entity_ref=hierarchy.get("parent_entity_ref"),
            cardinality_to_parent=hierarchy.get("cardinality_to_parent"),
            ownership_semantics=hierarchy.get("ownership_semantics"),
            primary_time_ref=interface_contract.get("primary_time_ref"),
            stable_descriptors=list(interface_contract.get("stable_descriptors") or []),
            keys=list(identity["key_refs"]),
            level=None,
            join_constraints={},
            upstream_dependencies=[],
            lineage=[],
            quality_expectations={},
            metadata={
                "entity_contract_id": semantic_object["entity_contract_id"],
                "display_name": header["display_name"],
                "description": header["description"],
                "status": resolved_contract.status,
                "revision": resolved_contract.revision,
                "created_at": resolved_contract.created_at,
                "updated_at": resolved_contract.updated_at,
            },
        )

    def _resolve_ref_of_kind(
        self, semantic_ref: str, *, expected_kind: str
    ) -> ResolvedSemanticObject:
        actual_kind = runtime_ref_kind(semantic_ref)
        if actual_kind != expected_kind:
            raise SemanticRuntimeInvalidRefError(
                f"Expected {expected_kind} ref, got: {semantic_ref}",
                semantic_ref=semantic_ref,
            )
        return self.resolve_ref(semantic_ref)

    def _build_resolved_object(
        self, object_kind: str, semantic_object: dict[str, Any]
    ) -> ResolvedSemanticObject:
        object_id_field = self._OBJECT_ID_FIELDS[object_kind]
        ref_field = self._REF_FIELDS[object_kind]
        header = semantic_object["header"]
        return ResolvedSemanticObject(
            object_kind=object_kind,
            object_id=str(semantic_object[object_id_field]),
            ref=str(header[ref_field]),
            semantic_object=semantic_object,
            status=str(semantic_object["status"]),
            revision=int(semantic_object["revision"]),
            created_at=str(semantic_object["created_at"]),
            updated_at=str(semantic_object["updated_at"]),
        )


_DEPENDENCY_PREFIXES = (
    "entity.",
    "metric.",
    "process.",
    "dimension.",
    "time.",
    "enum.",
    "binding.",
    "compiler_profile.",
    "calendar_policy.",
    "subject.",
    "source_object.",
)


def _dependency_refs_for_object(
    *, object_kind: ObjectKind, semantic_object: dict[str, Any]
) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()
    if object_kind == "entity":
        interface_contract = semantic_object.get("interface_contract") or {}
        hierarchy = interface_contract.get("hierarchy") or {}
        _append_dependency_ref(refs, seen, hierarchy.get("parent_entity_ref"))
        _append_dependency_ref(refs, seen, interface_contract.get("primary_time_ref"))
        for descriptor in interface_contract.get("stable_descriptors") or []:
            _append_dependency_ref(refs, seen, descriptor.get("dimension_ref"))
        return refs
    if object_kind == "metric":
        header = semantic_object.get("header") or {}
        payload = semantic_object.get("payload") or {}
        for value in (
            header.get("population_subject_ref"),
            header.get("observed_entity_ref"),
            header.get("primary_time_ref"),
        ):
            _append_dependency_ref(refs, seen, value)
        _collect_dependency_refs(payload, refs, seen)
        return refs
    if object_kind == "process":
        header = semantic_object.get("header") or {}
        interface_contract = semantic_object.get("interface_contract") or {}
        _collect_dependency_refs(interface_contract, refs, seen)
        _collect_dependency_refs(semantic_object.get("payload") or {}, refs, seen)
        return [ref for ref in refs if ref != header.get("process_ref")]
    if object_kind == "dimension":
        _collect_dependency_refs(semantic_object.get("interface_contract") or {}, refs, seen)
        return refs
    if object_kind in {"time", "enum"}:
        return refs
    if object_kind == "binding":
        header = semantic_object.get("header") or {}
        interface_contract = semantic_object.get("interface_contract") or {}
        _append_dependency_ref(refs, seen, header.get("bound_object_ref"))
        for binding_import in interface_contract.get("imports") or []:
            _append_dependency_ref(refs, seen, binding_import.get("binding_ref"))
        for carrier in interface_contract.get("carrier_bindings") or []:
            _append_dependency_ref(refs, seen, carrier.get("primary_entity_ref"))
            _append_dependency_ref(refs, seen, carrier.get("source_object_ref"))
            _append_dependency_ref(refs, seen, carrier.get("carrier_locator"), allow_locator=True)
        for field_binding in interface_contract.get("field_bindings") or []:
            _append_dependency_ref(refs, seen, field_binding.get("semantic_ref"))
            target = field_binding.get("target") or {}
            _append_dependency_ref(refs, seen, target.get("target_key"))
            _append_dependency_ref(refs, seen, target.get("context_ref"))
        for join_relation in interface_contract.get("join_relations") or []:
            for key_pair in join_relation.get("key_ref_pairs") or []:
                _collect_dependency_refs(key_pair, refs, seen)
        for policy in interface_contract.get("consumption_policies") or []:
            _append_dependency_ref(refs, seen, policy.get("anchor_ref"))
            _append_dependency_ref(refs, seen, policy.get("grace_period_ref"))
        return refs
    return refs


def _append_dependency_ref(
    refs: list[str],
    seen: set[str],
    value: str | None,
    *,
    allow_locator: bool = False,
) -> None:
    if value is None:
        return
    ref = str(value).strip()
    if not ref:
        return
    if not allow_locator and not ref.startswith(_DEPENDENCY_PREFIXES):
        return
    if ref in seen:
        return
    seen.add(ref)
    refs.append(ref)


def _collect_dependency_refs(
    value: Any, refs: list[str], seen: set[str], *, allow_locator: bool = False
) -> None:
    if isinstance(value, str):
        _append_dependency_ref(refs, seen, value, allow_locator=allow_locator)
        return
    if isinstance(value, dict):
        for nested in value.values():
            _collect_dependency_refs(nested, refs, seen, allow_locator=allow_locator)
        return
    if isinstance(value, list):
        for item in value:
            _collect_dependency_refs(item, refs, seen, allow_locator=allow_locator)


def _metric_dimensions(
    metadata: MetadataStore,
    *,
    header: dict[str, Any],
    family_payload: dict[str, Any],
) -> list[str]:
    explicit_dimensions = family_payload.get("dimensions")
    if explicit_dimensions is not None:
        return [str(dimension) for dimension in list(explicit_dimensions)]

    observed_entity_ref = str(header.get("observed_entity_ref") or "").strip()
    if not observed_entity_ref:
        return []
    return resolve_entity_binding_dimensions(metadata, observed_entity_ref)
