from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from app.metric_inputs import required_metric_input_slots
from app.semantic_readiness import (
    ObjectKind,
    ReadinessObjectSnapshot,
    SemanticReadinessService,
    binding_contract_target_exists,
)
from app.semantic_runtime.semantic_metadata import (
    entity_runtime_metadata,
    metric_runtime_metadata,
)
from app.storage.metadata import MetadataStore
from app.time_contracts import normalize_timestamp_format
from app.time_scope import _normalize_date_format, _normalize_hour_format

from .errors import (
    SemanticCompatibilityError,
    SemanticNotFoundError,
    SemanticStateError,
    SemanticValidationError,
)


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _normalize_carrier_locator(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return {
            "catalog": _optional_str(value.get("catalog")),
            "schema": _optional_str(value.get("schema")) or _optional_str(value.get("schema_name")),
            "table": _optional_str(value.get("table")),
        }
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        try:
            payload = json.loads(normalized)
        except json.JSONDecodeError:
            parts = [part.strip() for part in normalized.split(".") if part.strip()]
            if len(parts) >= 3:
                return {"catalog": parts[-3], "schema": parts[-2], "table": parts[-1]}
            if len(parts) == 2:
                return {"catalog": None, "schema": parts[0], "table": parts[1]}
            if len(parts) == 1:
                return {"catalog": None, "schema": None, "table": parts[0]}
            return None
        if isinstance(payload, dict):
            return _normalize_carrier_locator(payload)
        if isinstance(payload, str):
            return _normalize_carrier_locator(payload)
    return None


def _carrier_locator_ref(value: Any) -> str | None:
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    locator = _normalize_carrier_locator(value)
    if locator is None:
        return None
    normalized = ".".join(
        part
        for part in [
            _optional_str(locator.get("catalog")),
            _optional_str(locator.get("schema")),
            _optional_str(locator.get("table")),
        ]
        if part is not None
    )
    return normalized or None


def _locator_matches_source_object(
    source_object: dict[str, Any], locator: dict[str, Any] | str
) -> bool:
    fqn = _optional_str(source_object.get("fqn"))
    native_name = _optional_str(source_object.get("native_name"))
    authority_locator = _normalize_carrier_locator(source_object.get("authority_locator")) or {}
    authority_fqn = ".".join(
        part
        for part in [
            _optional_str(authority_locator.get("catalog")),
            _optional_str(authority_locator.get("schema")),
            _optional_str(authority_locator.get("table")),
        ]
        if part is not None
    )
    if isinstance(locator, str):
        normalized = locator.strip()
        if not normalized:
            return False
        if normalized in {
            candidate for candidate in [fqn, native_name, authority_fqn] if candidate
        }:
            return True
    normalized_locator = _normalize_carrier_locator(locator)
    if normalized_locator is None:
        return False
    normalized_locator_fqn = ".".join(
        part
        for part in [
            _optional_str(normalized_locator.get("catalog")),
            _optional_str(normalized_locator.get("schema")),
            _optional_str(normalized_locator.get("table")),
        ]
        if part is not None
    )
    if normalized_locator_fqn and normalized_locator_fqn in {
        candidate for candidate in [fqn, native_name, authority_fqn] if candidate
    }:
        return True
    if not authority_locator:
        return False
    return all(
        normalized_locator.get(key) is None
        or authority_locator.get(key) == normalized_locator.get(key)
        for key in ("catalog", "schema", "table")
    )


class _SemanticListContext:
    """Request-local semantic caches for list endpoints.

    The default readiness loaders are intentionally lazy for single-object reads,
    but list endpoints need the same data for many objects. This context batches
    those reads once per request without changing public response contracts.
    """

    def __init__(self, service: Any) -> None:
        self._service = service
        self._snapshots_by_ref: dict[str, ReadinessObjectSnapshot] | None = None
        self._readiness_by_ref: dict[str, dict[str, Any]] = {}
        self._bindings_by_id: dict[str, dict[str, Any]] | None = None
        self._bindings_by_ref: dict[str, dict[str, Any]] | None = None
        self._bindings_by_subject: dict[str, list[dict[str, Any]]] | None = None
        self._source_objects_by_id: dict[str, dict[str, Any]] | None = None
        self._source_objects_by_fqn: dict[str, dict[str, Any]] | None = None
        self._profiles_by_subject: dict[tuple[str, str], list[dict[str, Any]]] | None = None
        self._dependent_refs_by_ref: dict[str, list[str]] | None = None

    def load_dependency_snapshot(self, ref: str) -> ReadinessObjectSnapshot | None:
        self._ensure_snapshots()
        assert self._snapshots_by_ref is not None
        return self._snapshots_by_ref.get(ref)

    def load_subject_bindings(self, subject_ref: str) -> list[dict[str, Any]]:
        self._ensure_bindings()
        assert self._bindings_by_subject is not None
        return [dict(binding) for binding in self._bindings_by_subject.get(subject_ref, [])]

    def load_binding_imports(self, binding_ref: str) -> list[dict[str, Any]]:
        self._ensure_bindings()
        assert self._bindings_by_ref is not None
        binding = self._bindings_by_ref.get(binding_ref)
        if binding is None:
            return []
        return list((binding.get("interface_contract") or {}).get("imports") or [])

    def load_carrier_source_object(self, carrier_binding: dict[str, Any]) -> dict[str, Any] | None:
        self._ensure_source_objects()
        assert self._source_objects_by_id is not None
        assert self._source_objects_by_fqn is not None
        source_object_ref = carrier_binding.get("source_object_ref")
        if isinstance(source_object_ref, str) and source_object_ref:
            return self._source_objects_by_id.get(
                source_object_ref
            ) or self._source_objects_by_fqn.get(source_object_ref)
        raw_locator = carrier_binding.get("carrier_locator")
        if raw_locator is None:
            return None
        for source_object in self._source_objects_by_id.values():
            if _locator_matches_source_object(source_object, raw_locator):
                return dict(source_object)
        return None

    def load_profiles(self, subject_kind: str, subject_ref: str) -> list[dict[str, Any]]:
        self._ensure_profiles()
        assert self._profiles_by_subject is not None
        return list(self._profiles_by_subject.get((subject_kind, subject_ref), []))

    def binding_contract_for(self, binding_id: str) -> dict[str, Any] | None:
        self._ensure_bindings()
        assert self._bindings_by_id is not None
        return self._bindings_by_id.get(binding_id)

    def dependent_refs_for(self, ref: str) -> list[str]:
        self._ensure_dependent_refs()
        assert self._dependent_refs_by_ref is not None
        return list(self._dependent_refs_by_ref.get(ref, []))

    def readiness_for(self, snapshot: ReadinessObjectSnapshot) -> dict[str, Any]:
        cached = self._readiness_by_ref.get(snapshot.ref)
        if cached is not None:
            return dict(cached)
        result: dict[str, Any] = dict(
            self._service.readiness_service.evaluate_snapshot(
                object_kind=snapshot.object_kind,
                object_id=snapshot.object_id,
                ref=snapshot.ref,
                status=snapshot.status,
                revision=snapshot.revision,
                semantic_object=snapshot.semantic_object,
                dependency_snapshot_loader=self.load_dependency_snapshot,
                subject_bindings_loader=self.load_subject_bindings,
                binding_imports_loader=self.load_binding_imports,
                carrier_source_object_loader=self.load_carrier_source_object,
                profiles_loader=self.load_profiles,
            ).contract_payload()
        )
        self._readiness_by_ref[snapshot.ref] = result
        return result

    def _ensure_dependent_refs(self) -> None:
        if self._dependent_refs_by_ref is not None:
            return
        self._ensure_snapshots()
        assert self._snapshots_by_ref is not None
        dependents: dict[str, list[str]] = defaultdict(list)
        seen: dict[str, set[str]] = defaultdict(set)
        for candidate_ref, snapshot in self._snapshots_by_ref.items():
            for dependency_ref in self._service._dependency_refs_for_object(
                object_kind=snapshot.object_kind,
                obj=snapshot.semantic_object,
            ):
                if dependency_ref == candidate_ref or candidate_ref in seen[dependency_ref]:
                    continue
                seen[dependency_ref].add(candidate_ref)
                dependents[dependency_ref].append(candidate_ref)
        self._dependent_refs_by_ref = dict(dependents)

    def _ensure_source_objects(self) -> None:
        if self._source_objects_by_id is not None and self._source_objects_by_fqn is not None:
            return
        rows = self._service.metadata.query_rows("SELECT * FROM source_objects")
        self._source_objects_by_id = {}
        source_objects_by_fqn: dict[str, dict[str, Any]] = {}
        for row in rows:
            source_object = dict(row)
            source_object["authority_locator"] = json.loads(str(row["authority_locator_json"]))
            self._source_objects_by_id[str(row["object_id"])] = source_object
            source_objects_by_fqn.setdefault(str(row["fqn"]), source_object)
        self._source_objects_by_fqn = source_objects_by_fqn

    def _ensure_profiles(self) -> None:
        if self._profiles_by_subject is not None:
            return
        rows = self._service.metadata.query_rows(
            """
            SELECT *
            FROM compiler_compatibility_profiles
            WHERE status = 'published'
            ORDER BY profile_ref
            """
        )
        profiles: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            profile = {
                **dict(row),
                "requirement": json.loads(row["requirement_json"] or "{}"),
                "capability": json.loads(row["capability_json"] or "{}"),
            }
            profiles[(str(row["subject_kind"]), str(row["subject_ref"]))].append(profile)
        self._profiles_by_subject = dict(profiles)

    def _ensure_bindings(self) -> None:
        if self._bindings_by_id is not None:
            return
        binding_rows = self._service.metadata.query_rows(
            "SELECT * FROM typed_bindings ORDER BY binding_ref"
        )
        binding_ids = [str(row["binding_id"]) for row in binding_rows]
        imports_by_binding = self._group_rows_by_binding_id(
            """
            SELECT import_key, imported_binding_ref, required_ref_prefixes_json, binding_id
            FROM binding_imports
            ORDER BY id
            """
        )
        carriers_by_binding = self._group_rows_by_binding_id(
            """
            SELECT *
            FROM carrier_bindings
            ORDER BY binding_id, binding_key
            """
        )
        field_surfaces_by_carrier = self._group_rows_by_key(
            """
            SELECT carrier_binding_id, surface_ref, physical_name, field_type
            FROM carrier_field_surfaces
            ORDER BY carrier_binding_id, position
            """,
            "carrier_binding_id",
        )
        time_surfaces_by_carrier = self._group_rows_by_key(
            """
            SELECT carrier_binding_id, surface_ref, physical_name, time_granularity
            FROM carrier_time_surfaces
            ORDER BY carrier_binding_id, position
            """,
            "carrier_binding_id",
        )
        field_bindings_by_binding = self._group_rows_by_binding_id(
            """
            SELECT carrier_binding_key, target_kind, target_key, context_ref, semantic_ref,
                   surface_ref, field_type_ref, nullability_policy, repeated_value_policy,
                   binding_id
            FROM field_bindings
            ORDER BY binding_id, carrier_binding_key, target_kind, target_key
            """
        )
        time_bindings_by_binding = self._group_rows_by_binding_id(
            """
            SELECT carrier_binding_key, target_kind, target_key, context_ref, semantic_ref,
                   resolution_kind, timestamp_surface_ref, timestamp_format,
                   date_surface_ref, date_format,
                   hour_surface_ref, hour_format, timezone_strategy, binding_id
            FROM time_bindings
            ORDER BY binding_id, carrier_binding_key, target_kind, target_key, semantic_ref
            """
        )
        joins_by_binding = self._group_rows_by_binding_id(
            """
            SELECT relation_key, left_binding_key, right_binding_key, join_kind,
                   key_ref_pairs_json, cardinality, temporal_constraint_refs_json,
                   compatibility_rule_refs_json, binding_id
            FROM join_relations
            ORDER BY binding_id, relation_key
            """
        )
        policies_by_binding = self._group_rows_by_binding_id(
            """
            SELECT policy_key, policy_type, policy_target_path, anchor_ref,
                   grace_period_ref, behavior, binding_id
            FROM consumption_policies
            ORDER BY binding_id, policy_key
            """
        )
        bindings_by_id: dict[str, dict[str, Any]] = {}
        bindings_by_ref: dict[str, dict[str, Any]] = {}
        bindings_by_subject: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in binding_rows:
            binding_id = str(row["binding_id"])
            contract = self._binding_contract_from_rows(
                row=row,
                import_rows=imports_by_binding.get(binding_id, []),
                carrier_rows=carriers_by_binding.get(binding_id, []),
                field_surfaces_by_carrier=field_surfaces_by_carrier,
                time_surfaces_by_carrier=time_surfaces_by_carrier,
                field_binding_rows=field_bindings_by_binding.get(binding_id, []),
                time_binding_rows=time_bindings_by_binding.get(binding_id, []),
                join_rows=joins_by_binding.get(binding_id, []),
                policy_rows=policies_by_binding.get(binding_id, []),
            )
            bindings_by_id[binding_id] = contract
            bindings_by_ref[str(row["binding_ref"])] = contract
            bindings_by_subject[str(row["bound_object_ref"])].append(contract)
        for binding_id in binding_ids:
            bindings_by_id.setdefault(binding_id, {})
        self._bindings_by_id = bindings_by_id
        self._bindings_by_ref = bindings_by_ref
        self._bindings_by_subject = dict(bindings_by_subject)

    def _ensure_snapshots(self) -> None:
        if self._snapshots_by_ref is not None:
            return
        snapshots: dict[str, ReadinessObjectSnapshot] = {}
        self._add_entity_snapshots(snapshots)
        self._add_metric_snapshots(snapshots)
        self._add_process_snapshots(snapshots)
        self._add_dimension_snapshots(snapshots)
        self._add_time_snapshots(snapshots)
        self._add_enum_snapshots(snapshots)
        self._add_binding_snapshots(snapshots)
        self._add_profile_snapshots(snapshots)
        self._add_predicate_snapshots(snapshots)
        self._snapshots_by_ref = snapshots

    def _add_entity_snapshots(self, snapshots: dict[str, ReadinessObjectSnapshot]) -> None:
        rows = self._service.metadata.query_rows("SELECT * FROM semantic_entity_contracts")
        key_rows = self._group_rows_by_key(
            """
            SELECT entity_contract_id, key_ref
            FROM semantic_entity_key_refs
            ORDER BY entity_contract_id, position
            """,
            "entity_contract_id",
        )
        descriptor_rows = self._group_rows_by_key(
            """
            SELECT entity_contract_id, dimension_ref, cardinality
            FROM semantic_entity_stable_descriptors
            ORDER BY entity_contract_id, position
            """,
            "entity_contract_id",
        )
        for row in rows:
            entity_id = str(row["entity_contract_id"])
            hierarchy = None
            if row["parent_entity_ref"] is not None:
                hierarchy = {
                    "parent_entity_ref": row["parent_entity_ref"],
                    "cardinality_to_parent": row["cardinality_to_parent"],
                    "ownership_semantics": row["ownership_semantics"],
                }
            semantic_object = {
                "header": {
                    "entity_ref": row["entity_ref"],
                    "entity_contract_version": row["entity_contract_version"],
                },
                "interface_contract": {
                    "identity": {
                        "key_refs": [key_row["key_ref"] for key_row in key_rows.get(entity_id, [])],
                        "uniqueness_scope": row["uniqueness_scope"],
                        "id_stability": row["id_stability"],
                        "nullable_key_policy": row["nullable_key_policy"],
                    },
                    "hierarchy": hierarchy,
                    "primary_time_ref": row["primary_time_ref"],
                    "stable_descriptors": [
                        {
                            "dimension_ref": descriptor_row["dimension_ref"],
                            "cardinality": descriptor_row["cardinality"],
                        }
                        for descriptor_row in descriptor_rows.get(entity_id, [])
                    ],
                },
            }
            self._add_snapshot(
                snapshots, "entity", entity_id, str(row["entity_ref"]), row, semantic_object
            )

    def _add_metric_snapshots(self, snapshots: dict[str, ReadinessObjectSnapshot]) -> None:
        rows = self._service.metadata.query_rows("SELECT * FROM semantic_metric_contracts")
        for row in rows:
            semantic_object = {
                "header": {
                    "metric_ref": row["metric_ref"],
                    "metric_family": row["metric_family"],
                    "population_subject_ref": row["population_subject_ref"],
                    "observed_entity_ref": row["observed_entity_ref"],
                    "observation_grain_ref": row["observation_grain_ref"],
                    "sample_kind": row["sample_kind"],
                    "value_semantics": row["value_semantics"],
                    "aggregation_scope": row["aggregation_scope"],
                    "primary_time_ref": row["primary_time_ref"],
                    "additivity_constraints": json.loads(
                        row["additivity_constraints_json"] or "null"
                    ),
                    "metric_contract_version": row["metric_contract_version"],
                },
                "payload": json.loads(row["family_payload_json"]),
            }
            self._add_snapshot(
                snapshots,
                "metric",
                str(row["metric_contract_id"]),
                str(row["metric_ref"]),
                row,
                semantic_object,
            )

    def _add_process_snapshots(self, snapshots: dict[str, ReadinessObjectSnapshot]) -> None:
        rows = self._service.metadata.query_rows("SELECT * FROM semantic_process_objects")
        exported_rows = self._group_rows_by_key(
            """
            SELECT process_contract_id, dimension_ref
            FROM semantic_process_exported_dimension_refs
            ORDER BY process_contract_id, position
            """,
            "process_contract_id",
        )
        for row in rows:
            process_id = str(row["process_contract_id"])
            interface_contract: dict[str, Any] = {
                "contract_mode": row["contract_mode"],
                "population_subject_ref": row["population_subject_ref"],
                "anchor_time_ref": row["anchor_time_ref"],
                "exported_dimension_refs": [
                    exported_row["dimension_ref"]
                    for exported_row in exported_rows.get(process_id, [])
                ],
            }
            if row["contract_mode"] == "context_provider":
                interface_contract["context_kind"] = row["context_kind"]
                interface_contract["membership_cardinality"] = row["membership_cardinality"]
            else:
                interface_contract["entity_ref"] = row["entity_ref"]
                interface_contract["emitted_grain_ref"] = row["emitted_grain_ref"]
                interface_contract["subject_cardinality"] = row["subject_cardinality"]
            semantic_object = {
                "header": {
                    "process_ref": row["process_ref"],
                    "process_type": row["process_type"],
                    "process_contract_version": row["process_contract_version"],
                },
                "interface_contract": interface_contract,
                "payload": json.loads(row["process_payload_json"]),
            }
            self._add_snapshot(
                snapshots, "process", process_id, str(row["process_ref"]), row, semantic_object
            )

    def _add_dimension_snapshots(self, snapshots: dict[str, ReadinessObjectSnapshot]) -> None:
        rows = self._service.metadata.query_rows("SELECT * FROM semantic_dimension_contracts")
        for row in rows:
            interface_contract = self._dimension_interface_contract(row)
            semantic_object = {
                "header": {
                    "dimension_ref": row["dimension_ref"],
                    "dimension_contract_version": row["dimension_contract_version"],
                },
                "interface_contract": interface_contract,
            }
            self._add_snapshot(
                snapshots,
                "dimension",
                str(row["dimension_contract_id"]),
                str(row["dimension_ref"]),
                row,
                semantic_object,
            )

    def _add_time_snapshots(self, snapshots: dict[str, ReadinessObjectSnapshot]) -> None:
        rows = self._service.metadata.query_rows("SELECT * FROM semantic_time_objects")
        for row in rows:
            semantic_roles = self._time_semantic_roles(row)
            semantic_object = {
                "header": {
                    "time_ref": row["time_ref"],
                    "semantic_roles": semantic_roles,
                    "time_contract_version": row["time_contract_version"],
                }
            }
            self._add_snapshot(
                snapshots,
                "time",
                str(row["time_contract_id"]),
                str(row["time_ref"]),
                row,
                semantic_object,
            )

    def _add_enum_snapshots(self, snapshots: dict[str, ReadinessObjectSnapshot]) -> None:
        rows = self._service.metadata.query_rows("SELECT * FROM semantic_enum_sets")
        versions_by_enum = self._enum_versions_by_enum_set()
        for row in rows:
            semantic_object = {
                "header": {
                    "enum_set_ref": row["enum_set_ref"],
                    "value_type": row["value_type"],
                },
                "versions": versions_by_enum.get(str(row["enum_set_contract_id"]), []),
            }
            self._add_snapshot(
                snapshots,
                "enum",
                str(row["enum_set_contract_id"]),
                str(row["enum_set_ref"]),
                row,
                semantic_object,
            )

    def _add_binding_snapshots(self, snapshots: dict[str, ReadinessObjectSnapshot]) -> None:
        self._ensure_bindings()
        assert self._bindings_by_id is not None
        for binding in self._bindings_by_id.values():
            if not binding:
                continue
            self._add_snapshot(
                snapshots,
                "binding",
                str(binding["binding_id"]),
                str(binding["binding_ref"]),
                binding,
                binding,
            )

    def _add_profile_snapshots(self, snapshots: dict[str, ReadinessObjectSnapshot]) -> None:
        rows = self._service.metadata.query_rows("SELECT * FROM compiler_compatibility_profiles")
        for row in rows:
            semantic_object = {
                "profile_ref": row["profile_ref"],
                "profile_kind": row["profile_kind"],
                "subject_kind": row["subject_kind"],
                "subject_ref": row["subject_ref"],
                "subject_revision": row["subject_revision"],
                "requirement": json.loads(row["requirement_json"] or "{}") or None,
                "capability": json.loads(row["capability_json"] or "{}") or None,
            }
            self._add_snapshot(
                snapshots,
                "compiler_profile",
                str(row["profile_id"]),
                str(row["profile_ref"]),
                row,
                semantic_object,
            )

    def _add_predicate_snapshots(self, snapshots: dict[str, ReadinessObjectSnapshot]) -> None:
        rows = self._service.metadata.query_rows("SELECT * FROM semantic_predicate_contracts")
        for row in rows:
            payload = json.loads(row["payload_json"] or "{}")
            semantic_object = {
                "header": {
                    "predicate_ref": row["predicate_ref"],
                    "subject_ref": row["subject_ref"],
                    "predicate_contract_version": row["predicate_contract_version"],
                },
                "interface_contract": payload,
            }
            self._add_snapshot(
                snapshots,
                "predicate",
                str(row["predicate_contract_id"]),
                str(row["predicate_ref"]),
                row,
                semantic_object,
            )

    @staticmethod
    def _add_snapshot(
        snapshots: dict[str, ReadinessObjectSnapshot],
        object_kind: ObjectKind,
        object_id: str,
        ref: str,
        row: dict[str, Any],
        semantic_object: dict[str, Any],
    ) -> None:
        snapshots[ref] = ReadinessObjectSnapshot(
            object_kind=object_kind,
            object_id=object_id,
            ref=ref,
            status=str(row["status"]),
            revision=int(row["revision"]),
            semantic_object=semantic_object,
        )

    def _binding_contract_from_rows(
        self,
        *,
        row: dict[str, Any],
        import_rows: list[dict[str, Any]],
        carrier_rows: list[dict[str, Any]],
        field_surfaces_by_carrier: dict[str, list[dict[str, Any]]],
        time_surfaces_by_carrier: dict[str, list[dict[str, Any]]],
        field_binding_rows: list[dict[str, Any]],
        time_binding_rows: list[dict[str, Any]],
        join_rows: list[dict[str, Any]],
        policy_rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        carriers: list[dict[str, Any]] = []
        for carrier_row in carrier_rows:
            carrier_binding_id = str(carrier_row["carrier_binding_id"])
            carriers.append(
                {
                    "binding_key": carrier_row["binding_key"],
                    "source_object_ref": carrier_row["source_object_ref"],
                    "carrier_kind": carrier_row["carrier_kind"],
                    "carrier_locator": _normalize_carrier_locator(carrier_row["carrier_locator"]),
                    "binding_role": carrier_row["binding_role"],
                    "semantic_role_ref": carrier_row["semantic_role_ref"],
                    "grain_ref": carrier_row["grain_ref"],
                    "primary_entity_ref": carrier_row["primary_entity_ref"],
                    "row_filter_refs": json.loads(carrier_row["row_filter_refs_json"]),
                    "freshness_policy_ref": carrier_row["freshness_policy_ref"],
                    "field_surfaces": [
                        {
                            "surface_ref": surface_row["surface_ref"],
                            "physical_name": surface_row["physical_name"],
                            "field_type": surface_row["field_type"],
                        }
                        for surface_row in field_surfaces_by_carrier.get(carrier_binding_id, [])
                    ]
                    or None,
                    "time_surfaces": [
                        {
                            "surface_ref": surface_row["surface_ref"],
                            "physical_name": surface_row["physical_name"],
                            "time_granularity": surface_row["time_granularity"],
                        }
                        for surface_row in time_surfaces_by_carrier.get(carrier_binding_id, [])
                    ]
                    or None,
                }
            )
        binding = {
            "binding_id": row["binding_id"],
            "binding_ref": row["binding_ref"],
            "binding_scope": row["binding_scope"],
            "bound_object_ref": row["bound_object_ref"],
            "header": {
                "binding_ref": row["binding_ref"],
                "display_name": row.get("display_name"),
                "description": row.get("description"),
                "binding_scope": row["binding_scope"],
                "bound_object_ref": row["bound_object_ref"],
                "binding_contract_version": row["binding_contract_version"],
            },
            "status": row["status"],
            "revision": row["revision"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
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
        }
        return binding

    def _enum_versions_by_enum_set(self) -> dict[str, list[dict[str, Any]]]:
        version_rows = self._service.metadata.query_rows(
            """
            SELECT enum_set_contract_id, enum_set_version_id, enum_version
            FROM semantic_enum_set_versions
            ORDER BY enum_set_contract_id, enum_version
            """
        )
        value_rows = self._group_rows_by_key(
            """
            SELECT enum_set_version_id, value_key, raw_value, label, aliases_json
            FROM semantic_enum_set_values
            ORDER BY enum_set_version_id, position
            """,
            "enum_set_version_id",
        )
        versions_by_enum: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for version_row in version_rows:
            version_id = str(version_row["enum_set_version_id"])
            versions_by_enum[str(version_row["enum_set_contract_id"])].append(
                {
                    "enum_version": version_row["enum_version"],
                    "values": [
                        {
                            "value_key": value_row["value_key"],
                            "raw_value": json.loads(value_row["raw_value"]),
                            "label": value_row["label"],
                            "aliases": json.loads(value_row["aliases_json"]) or None,
                        }
                        for value_row in value_rows.get(version_id, [])
                    ],
                }
            )
        return dict(versions_by_enum)

    @staticmethod
    def _dimension_interface_contract(row: dict[str, Any]) -> dict[str, Any]:
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
        return interface_contract

    @staticmethod
    def _time_semantic_roles(row: dict[str, Any]) -> list[str]:
        semantic_roles: list[str] = []
        if row["business_anchor"]:
            semantic_roles.append("business_anchor")
        if row["measurement"]:
            semantic_roles.append("measurement")
        if row["operational_support"]:
            semantic_roles.append("operational_support")
        return semantic_roles

    def _group_rows_by_binding_id(self, sql: str) -> dict[str, list[dict[str, Any]]]:
        return self._group_rows_by_key(sql, "binding_id")

    def _group_rows_by_key(self, sql: str, key: str) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in self._service.metadata.query_rows(sql):
            grouped[str(row[key])].append(row)
        return dict(grouped)


class SemanticServiceSupport:
    SemanticLifecycleAction = Literal["validate", "activate", "deprecate", "publish"]

    _dependency_prefixes = (
        "entity.",
        "metric.",
        "process.",
        "dimension.",
        "time.",
        "enum.",
        "binding.",
        "compiler_profile.",
        "source_object.",
    )

    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata
        self.readiness_service = SemanticReadinessService(metadata)

    def _list_context(self) -> _SemanticListContext:
        return _SemanticListContext(self)

    def _evaluate_readiness(
        self,
        *,
        object_kind: ObjectKind,
        object_id: str,
        ref: str,
        status: str,
        revision: int,
        semantic_object: dict[str, Any],
        list_context: _SemanticListContext | None = None,
    ) -> dict[str, Any]:
        if list_context is not None:
            snapshot = ReadinessObjectSnapshot(
                object_kind=object_kind,
                object_id=object_id,
                ref=ref,
                status=status,
                revision=revision,
                semantic_object=semantic_object,
            )
            return list_context.readiness_for(snapshot)
        result = self.readiness_service.evaluate_snapshot(
            object_kind=object_kind,
            object_id=object_id,
            ref=ref,
            status=status,
            revision=revision,
            semantic_object=semantic_object,
        )
        return result.contract_payload()

    def _resolve_semantic_filters(
        self,
        *,
        status: str | None,
        lifecycle_status: str | None,
    ) -> str | None:
        normalized_status = status
        valid_statuses = {"draft", "published", "deprecated"}
        if normalized_status is not None and normalized_status not in valid_statuses:
            raise self._validation_error(
                "Unsupported status filter. Expected one of: draft, published, deprecated."
            )
        if lifecycle_status is None:
            return normalized_status
        lifecycle_to_status = {
            "draft": "draft",
            "active": "published",
            "deprecated": "deprecated",
        }
        resolved_status = lifecycle_to_status.get(lifecycle_status)
        if resolved_status is None:
            raise self._validation_error(
                "Unsupported lifecycle_status filter. Expected one of: draft, active, deprecated."
            )
        if normalized_status is not None and normalized_status != resolved_status:
            raise self._validation_error(
                "status and lifecycle_status filters conflict. "
                f"status={status!r} maps differently than lifecycle_status={lifecycle_status!r}."
            )
        return resolved_status

    @staticmethod
    def _matches_readiness_filter(
        item: dict[str, Any],
        *,
        readiness_status: str | None,
    ) -> bool:
        if readiness_status is None:
            return True
        return str(item.get("readiness_status") or "") == readiness_status

    @staticmethod
    def _required_metric_binding_slots(
        header: dict[str, Any], payload: dict[str, Any]
    ) -> list[str]:
        metric_family = str(
            header.get("metric_family") or payload.get("metric_family") or ""
        ).strip()
        return list(required_metric_input_slots(metric_family))

    def _augment_object_with_readiness(
        self,
        base: dict[str, Any],
        *,
        object_kind: ObjectKind,
        row: dict[str, Any],
        id_field: str,
        ref: str,
        mode: Literal["list", "detail"] = "detail",
        include_dependents: bool = True,
        list_context: _SemanticListContext | None = None,
    ) -> dict[str, Any]:
        """Augment a semantic object dict with computed readiness fields.

        This helper bundles the common pattern of extracting id/status/revision
        from a database row and calling _evaluate_readiness to add lifecycle_status,
        readiness_status, blocking_requirements, and capabilities.

        Args:
            base: The base semantic object dict to augment (mutated in-place).
            object_kind: The semantic object kind (entity, metric, process, etc).
            row: The database row dict containing id, status, revision fields.
            id_field: The key in row for the object's ID (e.g., "entity_id", "metric_id").
            ref: The pre-computed ref string (e.g., "entity.user", "metric.watch_time").
            mode: "list" for lightweight format, "detail" for full format.

        Returns:
            The augmented base dict (same object, mutated in-place).
        """
        result = self._evaluate_readiness(
            object_kind=object_kind,
            object_id=str(row[id_field]),
            ref=ref,
            status=str(row["status"]),
            revision=int(row["revision"]),
            semantic_object=base,
            list_context=list_context,
        )
        # result contains: lifecycle_status, readiness_status, blocking_requirements, capabilities
        # base already contains: status, revision, created_at, updated_at (from row)
        base["lifecycle_status"] = result["lifecycle_status"]
        base["readiness_status"] = result["readiness_status"]

        if mode == "list":
            # Lightweight: blocker_count and capabilities_summary
            base["blocker_count"] = len(result.get("blocking_requirements") or [])
            caps = result.get("capabilities") or {}
            base["capabilities_summary"] = {
                k: bool(v) if isinstance(v, bool) else v is not None for k, v in caps.items()
            }
        else:
            # Full detail: blocking_requirements, capabilities, dependency_refs, dependent_refs
            base["blocking_requirements"] = result.get("blocking_requirements") or []
            base["capabilities"] = result.get("capabilities") or {}
            base["dependency_refs"] = self._dependency_refs_for_object(
                object_kind=object_kind, obj=base
            )
            if include_dependents:
                base["dependent_refs"] = (
                    list_context.dependent_refs_for(ref)
                    if list_context is not None
                    else self._dependent_refs_for_ref(ref)
                )
            else:
                base["dependent_refs"] = []
        return base

    def _dependent_refs_for_ref(self, ref: str) -> list[str]:
        return _SemanticListContext(self).dependent_refs_for(ref)

    def _iter_semantic_objects_for_dependency_scan(
        self,
    ) -> list[tuple[ObjectKind, str, dict[str, Any]]]:
        objects: list[tuple[ObjectKind, str, dict[str, Any]]] = []

        entity_rows = self.metadata.query_rows("SELECT * FROM semantic_entity_contracts")
        for row in entity_rows:
            entity = self._row_to_typed_entity(row, include_dependents=False)
            objects.append(("entity", str(row["entity_ref"]), entity))

        metric_rows = self.metadata.query_rows("SELECT * FROM semantic_metric_contracts")
        for row in metric_rows:
            metric = self._row_to_typed_metric(row, include_dependents=False)
            objects.append(("metric", str(row["metric_ref"]), metric))

        process_rows = self.metadata.query_rows("SELECT * FROM semantic_process_objects")
        for row in process_rows:
            process = self._row_to_process_object(row, include_dependents=False)
            objects.append(("process", str(row["process_ref"]), process))

        dimension_rows = self.metadata.query_rows("SELECT * FROM semantic_dimension_contracts")
        for row in dimension_rows:
            dimension = self._row_to_dimension(row, include_dependents=False)
            objects.append(("dimension", str(row["dimension_ref"]), dimension))

        time_rows = self.metadata.query_rows("SELECT * FROM semantic_time_objects")
        for row in time_rows:
            time_semantic = self._row_to_time_semantic(row, include_dependents=False)
            objects.append(("time", str(row["time_ref"]), time_semantic))

        enum_rows = self.metadata.query_rows("SELECT * FROM semantic_enum_sets")
        for row in enum_rows:
            enum_set = self._row_to_enum_set(row, include_dependents=False)
            objects.append(("enum", str(row["enum_set_ref"]), enum_set))

        binding_rows = self.metadata.query_rows("SELECT * FROM typed_bindings")
        for row in binding_rows:
            binding = self._row_to_typed_binding(row, include_dependents=False)
            objects.append(("binding", str(row["binding_ref"]), binding))

        profile_rows = self.metadata.query_rows("SELECT * FROM compiler_compatibility_profiles")
        for row in profile_rows:
            profile = self._row_to_compatibility_profile(row, include_dependents=False)
            objects.append(("compiler_profile", str(row["profile_ref"]), profile))

        return objects

    @classmethod
    def _append_dependency_ref(
        cls, refs: list[str], seen: set[str], value: str | None, *, allow_locator: bool = False
    ) -> None:
        if value is None:
            return
        ref = str(value).strip()
        if not ref:
            return
        if not allow_locator and not ref.startswith(cls._dependency_prefixes):
            return
        if ref in seen:
            return
        seen.add(ref)
        refs.append(ref)

    def _dependency_refs_for_object(
        self, *, object_kind: ObjectKind, obj: dict[str, Any]
    ) -> list[str]:
        refs: list[str] = []
        seen: set[str] = set()
        if object_kind == "entity":
            interface_contract = obj.get("interface_contract") or {}
            hierarchy = interface_contract.get("hierarchy") or {}
            self._append_dependency_ref(refs, seen, hierarchy.get("parent_entity_ref"))
            self._append_dependency_ref(refs, seen, interface_contract.get("primary_time_ref"))
            for descriptor in interface_contract.get("stable_descriptors") or []:
                self._append_dependency_ref(refs, seen, descriptor.get("dimension_ref"))
            return refs
        if object_kind == "metric":
            header = obj.get("header") or {}
            payload = obj.get("payload") or {}
            for value in (
                header.get("population_subject_ref"),
                header.get("observed_entity_ref"),
                header.get("primary_time_ref"),
            ):
                self._append_dependency_ref(refs, seen, value)
            self._collect_dependency_refs(payload, refs, seen)
            return refs
        if object_kind == "process":
            header = obj.get("header") or {}
            interface_contract = obj.get("interface_contract") or {}
            self._append_dependency_ref(refs, seen, header.get("process_ref"))
            self._collect_dependency_refs(interface_contract, refs, seen)
            self._collect_dependency_refs(obj.get("payload") or {}, refs, seen)
            refs = [ref for ref in refs if ref != header.get("process_ref")]
            return refs
        if object_kind == "dimension":
            interface_contract = obj.get("interface_contract") or {}
            self._collect_dependency_refs(interface_contract, refs, seen)
            return refs
        if object_kind == "time":
            return refs
        if object_kind == "enum":
            return refs
        if object_kind == "binding":
            header = obj.get("header") or {}
            interface_contract = obj.get("interface_contract") or {}
            self._append_dependency_ref(refs, seen, header.get("bound_object_ref"))
            for imported in interface_contract.get("imports") or []:
                self._append_dependency_ref(refs, seen, imported.get("binding_ref"))
            for carrier in interface_contract.get("carrier_bindings") or []:
                self._append_dependency_ref(refs, seen, carrier.get("primary_entity_ref"))
                self._append_dependency_ref(refs, seen, carrier.get("source_object_ref"))
                self._append_dependency_ref(
                    refs,
                    seen,
                    _carrier_locator_ref(carrier.get("carrier_locator")),
                    allow_locator=True,
                )
            for field_binding in interface_contract.get("field_bindings") or []:
                self._append_dependency_ref(refs, seen, field_binding.get("semantic_ref"))
                target = field_binding.get("target") or {}
                self._append_dependency_ref(refs, seen, target.get("target_key"))
                self._append_dependency_ref(refs, seen, target.get("context_ref"))
            for join_relation in interface_contract.get("join_relations") or []:
                for key_pair in join_relation.get("key_ref_pairs") or []:
                    self._collect_dependency_refs(key_pair, refs, seen)
            for policy in interface_contract.get("consumption_policies") or []:
                self._append_dependency_ref(refs, seen, policy.get("anchor_ref"))
                self._append_dependency_ref(refs, seen, policy.get("grace_period_ref"))
            return refs
        if object_kind == "compiler_profile":
            self._append_dependency_ref(refs, seen, obj.get("subject_ref"))
            return refs
        return refs

    def _collect_dependency_refs(
        self, value: Any, refs: list[str], seen: set[str], *, allow_locator: bool = False
    ) -> None:
        if isinstance(value, str):
            self._append_dependency_ref(refs, seen, value, allow_locator=allow_locator)
            return
        if isinstance(value, dict):
            for nested in value.values():
                self._collect_dependency_refs(nested, refs, seen, allow_locator=allow_locator)
            return
        if isinstance(value, list):
            for item in value:
                self._collect_dependency_refs(item, refs, seen, allow_locator=allow_locator)

    def _entity_ref_for_name(self, name: str) -> str:
        return f"entity.{name}"

    def _metric_ref_for_name(self, name: str) -> str:
        return f"metric.{name}"

    def _not_found(self, message: str) -> SemanticNotFoundError:
        return SemanticNotFoundError(message)

    def _validation_error(
        self,
        message: str,
        *,
        code: str = "semantic_validation_error",
        category: str = "validation",
        field_path: str | None = None,
        remediation: dict[str, Any] | None = None,
        examples: list[dict[str, Any]] | None = None,
    ) -> SemanticValidationError:
        return SemanticValidationError(
            message,
            code=code,
            category=category,
            field_path=field_path,
            remediation=remediation,
            examples=examples,
        )

    def _state_error(
        self,
        message: str,
        *,
        code: str = "semantic_state_error",
        category: str = "state",
    ) -> SemanticStateError:
        return SemanticStateError(message, code=code, category=category)

    def _compatibility_error(
        self,
        message: str,
        *,
        code: str = "semantic_compatibility_error",
        category: str = "compatibility",
    ) -> SemanticCompatibilityError:
        return SemanticCompatibilityError(message, code=code, category=category)

    def _require_lifecycle_action_status(
        self,
        *,
        action: SemanticLifecycleAction,
        status: str,
        object_label: str,
        object_id: str,
    ) -> None:
        allowed_statuses: tuple[str, ...]
        expected_label: str
        if action == "validate":
            allowed_statuses = ("draft", "published")
            expected_label = "draft or published"
        elif action in {"activate", "publish"}:
            allowed_statuses = ("draft",)
            expected_label = "draft"
        elif action == "deprecate":
            allowed_statuses = ("published",)
            expected_label = "published"
        else:
            raise self._state_error(
                f"Unsupported lifecycle action '{action}'.",
                code="semantic_lifecycle_action_unsupported",
            )
        if status not in allowed_statuses:
            action_name = "publish" if action == "publish" else action
            raise self._state_error(
                f"{object_label} '{object_id}' cannot {action_name} from status={status}; "
                f"expected {expected_label}.",
                code=f"{action_name}_state_error",
            )

    def _run_publish_reference_validation(self, validator: Any) -> None:
        try:
            validator()
        except SemanticValidationError as error:
            raise self._validation_error(
                str(error),
                code="reference_validation_error",
            ) from error

    def _run_publish_compatibility_validation(self, validator: Any) -> None:
        try:
            validator()
        except SemanticValidationError as error:
            raise self._validation_error(
                str(error),
                code="publish_compatibility_validation_error",
            ) from error
        except SemanticCompatibilityError as error:
            raise self._compatibility_error(
                str(error),
                code="compatibility_validation_error",
            ) from error

    def _validate_record(
        self,
        *,
        object_id: str,
        object_label: str,
        status: str,
        reference_validator: Any | None = None,
        compatibility_validator: Any | None = None,
    ) -> None:
        self._require_lifecycle_action_status(
            action="validate",
            status=status,
            object_label=object_label,
            object_id=object_id,
        )
        if reference_validator is not None:
            self._run_publish_reference_validation(reference_validator)
        if compatibility_validator is not None:
            self._run_publish_compatibility_validation(compatibility_validator)

    def _activate_record(
        self,
        *,
        table_name: str,
        id_column: str,
        object_id: str,
        object_label: str,
        status: str,
        reference_validator: Any | None = None,
        compatibility_validator: Any | None = None,
    ) -> None:
        self._require_lifecycle_action_status(
            action="activate",
            status=status,
            object_label=object_label,
            object_id=object_id,
        )
        if reference_validator is not None:
            self._run_publish_reference_validation(reference_validator)
        if compatibility_validator is not None:
            self._run_publish_compatibility_validation(compatibility_validator)
        self.metadata.execute(
            f"""
            UPDATE {table_name}
            SET status = 'published', revision = revision + 1, updated_at = ?
            WHERE {id_column} = ?
            """,
            [now_iso(), object_id],
        )

    def _deprecate_record(
        self,
        *,
        table_name: str,
        id_column: str,
        object_id: str,
        object_label: str,
        status: str,
    ) -> None:
        self._require_lifecycle_action_status(
            action="deprecate",
            status=status,
            object_label=object_label,
            object_id=object_id,
        )
        self.metadata.execute(
            f"""
            UPDATE {table_name}
            SET status = 'deprecated', revision = revision + 1, updated_at = ?
            WHERE {id_column} = ?
            """,
            [now_iso(), object_id],
        )

    def _publish_record(
        self,
        *,
        table_name: str,
        id_column: str,
        object_id: str,
        object_label: str,
        status: str,
        reference_validator: Any | None = None,
        compatibility_validator: Any | None = None,
    ) -> None:
        self._activate_record(
            table_name=table_name,
            id_column=id_column,
            object_id=object_id,
            object_label=object_label,
            status=status,
            reference_validator=reference_validator,
            compatibility_validator=compatibility_validator,
        )

    @staticmethod
    def _normalize_key_ref(value: str) -> str:
        key = value.strip()
        return key if key.startswith("key.") else f"key.{key}"

    @staticmethod
    def _normalize_grain_ref(value: str | None) -> str | None:
        if value is None:
            return None
        grain = str(value).strip()
        if not grain:
            return None
        return grain if grain.startswith("grain.") else f"grain.{grain}"

    @staticmethod
    def _infer_entity_stability(level: str | None) -> str:
        if str(level or "").strip().lower() in {"session", "event"}:
            return "ephemeral"
        return "stable"

    @staticmethod
    def _infer_metric_contract_axes(
        measure_type: str | None,
    ) -> tuple[str, str, str, dict[str, Any]]:
        kind = str(measure_type or "count").strip().lower()
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
        if kind == "count":
            return (
                "count_metric",
                "numeric",
                "count",
                {"dimension_policy": "all", "time_axis_policy": "additive"},
            )
        if kind in {"percentile", "quantile"}:
            return (
                "distribution_metric",
                "numeric",
                "distribution_statistic",
                {"dimension_policy": "none", "time_axis_policy": "non_additive"},
            )
        if kind == "survival":
            return (
                "survival_metric",
                "survival",
                "survival_probability",
                {"dimension_policy": "none", "time_axis_policy": "non_additive"},
            )
        if kind == "score":
            return (
                "score_metric",
                "numeric",
                "score",
                {"dimension_policy": "none", "time_axis_policy": "non_additive"},
            )
        return (
            "count_metric",
            "numeric",
            "count",
            {"dimension_policy": "all", "time_axis_policy": "additive"},
        )

    @staticmethod
    def _legacy_aggregation_scope(grain: str | None) -> str | None:
        if grain is None:
            return None
        grain_value = str(grain).strip().lower()
        if grain_value in {"session", "event"}:
            return grain_value
        return "window"

    def _typed_entity_exists(self, entity_contract_id: str) -> bool:
        return (
            self.metadata.query_one(
                "SELECT entity_contract_id FROM semantic_entity_contracts WHERE entity_contract_id = ?",
                [entity_contract_id],
            )
            is not None
        )

    def _typed_metric_exists(self, metric_contract_id: str) -> bool:
        return (
            self.metadata.query_one(
                "SELECT metric_contract_id FROM semantic_metric_contracts WHERE metric_contract_id = ?",
                [metric_contract_id],
            )
            is not None
        )

    def _typed_binding_exists(self, binding_id: str) -> bool:
        return (
            self.metadata.query_one(
                "SELECT binding_id FROM typed_bindings WHERE binding_id = ?",
                [binding_id],
            )
            is not None
        )

    def _typed_profile_exists(self, profile_id: str) -> bool:
        return (
            self.metadata.query_one(
                "SELECT profile_id FROM compiler_compatibility_profiles WHERE profile_id = ?",
                [profile_id],
            )
            is not None
        )

    def _ref_exists(self, sql: str, ref_value: str) -> bool:
        return self.metadata.query_one(sql, [ref_value]) is not None

    def _require_ref_exists(self, sql: str, ref_value: str, ref_name: str) -> None:
        if not self._ref_exists(sql, ref_value):
            raise self._validation_error(f"Unknown {ref_name}: {ref_value}")

    def _require_published_ref_exists(self, sql: str, ref_value: str, ref_name: str) -> None:
        if not self._ref_exists(sql, ref_value):
            raise self._validation_error(f"{ref_name.capitalize()} must be published: {ref_value}")

    def _validate_entity_ref(self, entity_ref: str) -> None:
        self._require_ref_exists(
            "SELECT entity_contract_id FROM semantic_entity_contracts WHERE entity_ref = ?",
            entity_ref,
            "entity ref",
        )

    def _validate_published_entity_ref(self, entity_ref: str) -> None:
        self._validate_entity_ref(entity_ref)
        self._require_published_ref_exists(
            """
            SELECT entity_contract_id
            FROM semantic_entity_contracts
            WHERE entity_ref = ? AND status = 'published'
            """,
            entity_ref,
            "entity ref",
        )

    @staticmethod
    def _resolve_entity_ref_from_alias(ref: str) -> str:
        """Map an alias-prefixed ref (subject/population/event) to its backing entity ref.

        In v1, subject.X, population.X, and event.X are namespace aliases for entity.X.
        The semantic_entity_contracts table only stores entity.* values, so alias
        refs must be translated before querying.
        """
        for prefix in ("subject.", "population.", "event."):
            if ref.startswith(prefix):
                return "entity." + ref[len(prefix) :]
        return ref

    def _validate_dimension_ref(self, dimension_ref: str) -> None:
        self._require_ref_exists(
            "SELECT dimension_contract_id FROM semantic_dimension_contracts WHERE dimension_ref = ?",
            dimension_ref,
            "dimension ref",
        )

    def _validate_published_dimension_ref(self, dimension_ref: str) -> None:
        self._validate_dimension_ref(dimension_ref)
        self._require_published_ref_exists(
            """
            SELECT dimension_contract_id
            FROM semantic_dimension_contracts
            WHERE dimension_ref = ? AND status = 'published'
            """,
            dimension_ref,
            "dimension ref",
        )

    def _validate_time_ref(self, time_ref: str) -> None:
        self._require_ref_exists(
            "SELECT time_contract_id FROM semantic_time_objects WHERE time_ref = ?",
            time_ref,
            "time ref",
        )

    def _validate_published_time_ref(self, time_ref: str) -> None:
        self._validate_time_ref(time_ref)
        self._require_published_ref_exists(
            """
            SELECT time_contract_id
            FROM semantic_time_objects
            WHERE time_ref = ? AND status = 'published'
            """,
            time_ref,
            "time ref",
        )

    def _validate_enum_set_ref(self, enum_set_ref: str) -> None:
        self._require_ref_exists(
            "SELECT enum_set_contract_id FROM semantic_enum_sets WHERE enum_set_ref = ?",
            enum_set_ref,
            "enum set ref",
        )

    def _validate_published_enum_set_ref(self, enum_set_ref: str) -> None:
        self._validate_enum_set_ref(enum_set_ref)
        self._require_published_ref_exists(
            """
            SELECT enum_set_contract_id
            FROM semantic_enum_sets
            WHERE enum_set_ref = ? AND status = 'published'
            """,
            enum_set_ref,
            "enum set ref",
        )

    def _validate_predicate_ref(self, predicate_ref: str) -> None:
        self._require_ref_exists(
            "SELECT predicate_contract_id FROM semantic_predicate_contracts WHERE predicate_ref = ?",
            predicate_ref,
            "predicate ref",
        )

    def _validate_published_predicate_ref(self, predicate_ref: str) -> None:
        self._validate_predicate_ref(predicate_ref)
        self._require_published_ref_exists(
            """
            SELECT predicate_contract_id
            FROM semantic_predicate_contracts
            WHERE predicate_ref = ? AND status = 'published'
            """,
            predicate_ref,
            "predicate ref",
        )

    def _get_predicate_by_ref(self, predicate_ref: str) -> dict[str, Any] | None:
        row = self.metadata.query_one(
            "SELECT * FROM semantic_predicate_contracts WHERE predicate_ref = ?",
            [predicate_ref],
        )
        return None if row is None else self._row_to_predicate(row)

    def _validate_predicate_refs_with_usage(
        self,
        refs: list[str] | None,
        *,
        required_usage: str,
        field_name: str,
        require_published: bool = True,
    ) -> None:
        for ref in refs or []:
            if require_published:
                self._validate_published_predicate_ref(ref)
            else:
                self._validate_predicate_ref(ref)
            predicate = self._get_predicate_by_ref(ref)
            if predicate is not None:
                allowed = predicate.get("interface_contract", {}).get("allowed_usage") or []
                if required_usage not in allowed:
                    raise self._validation_error(
                        f"{field_name} references '{ref}' which does not declare "
                        f"'{required_usage}' in allowed_usage (has: {allowed})"
                    )

    @staticmethod
    def _extract_target_refs(expression: dict[str, Any]) -> list[str]:
        """Recursively extract all target_ref values from a predicate expression tree."""
        refs: list[str] = []
        if expression.get("target_ref") is not None:
            refs.append(expression["target_ref"])
        for item in expression.get("items") or []:
            refs.extend(SemanticServiceSupport._extract_target_refs(item))
        return refs

    def _validate_semantic_ref_target(
        self, target_ref: str, *, require_published: bool = False
    ) -> None:
        """Validate that a target_ref points to an existing (optionally published) semantic object.

        Dispatches to the appropriate per-kind validator based on the ref prefix.
        """
        if target_ref.startswith("dimension."):
            if require_published:
                self._validate_published_dimension_ref(target_ref)
            else:
                self._validate_dimension_ref(target_ref)
        elif target_ref.startswith("entity."):
            if require_published:
                self._validate_published_entity_ref(target_ref)
            else:
                self._validate_entity_ref(target_ref)
        elif target_ref.startswith("key."):
            self._require_ref_exists(
                "SELECT entity_contract_id FROM semantic_entity_key_refs WHERE key_ref = ?",
                target_ref,
                "key ref",
            )
        elif target_ref.startswith("enum."):
            if require_published:
                self._validate_published_enum_set_ref(target_ref)
            else:
                self._validate_enum_set_ref(target_ref)
        elif target_ref.startswith(("subject.", "population.", "event.")):
            resolved = self._resolve_entity_ref_from_alias(target_ref)
            if require_published:
                self._validate_published_entity_ref(resolved)
            else:
                self._validate_entity_ref(resolved)
        elif target_ref.startswith("field."):
            self._require_ref_exists(
                "SELECT carrier_binding_id FROM carrier_field_surfaces WHERE surface_ref = ?",
                target_ref,
                "field ref",
            )

    def _validate_predicate_contract_refs(
        self,
        interface_contract: dict[str, Any],
        *,
        subject_ref: str | None = None,
        require_published: bool = False,
    ) -> None:
        if subject_ref is not None:
            resolved = self._resolve_entity_ref_from_alias(subject_ref)
            if require_published:
                self._validate_published_entity_ref(resolved)
            else:
                self._validate_entity_ref(resolved)
        expression = interface_contract.get("expression")
        if expression is not None:
            for target_ref in self._extract_target_refs(expression):
                self._validate_semantic_ref_target(target_ref, require_published=require_published)

    def _validate_published_predicate_contract_refs(
        self, interface_contract: dict[str, Any], *, subject_ref: str | None = None
    ) -> None:
        self._validate_predicate_contract_refs(
            interface_contract, subject_ref=subject_ref, require_published=True
        )

    def _validate_dimension_refs(self, dimension_refs: list[str] | None) -> None:
        for dimension_ref in dimension_refs or []:
            self._validate_dimension_ref(dimension_ref)

    def _validate_published_dimension_refs(self, dimension_refs: list[str] | None) -> None:
        for dimension_ref in dimension_refs or []:
            self._validate_published_dimension_ref(dimension_ref)

    def _validate_published_entity_contract_refs(self, interface_contract: dict[str, Any]) -> None:
        if interface_contract.get("primary_time_ref") is not None:
            self._validate_published_time_ref(interface_contract["primary_time_ref"])
        for descriptor in interface_contract.get("stable_descriptors") or []:
            self._validate_published_dimension_ref(descriptor["dimension_ref"])

    def _validate_published_metric_header_refs(self, header: dict[str, Any]) -> None:
        if header.get("primary_time_ref") is not None:
            self._validate_published_time_ref(header["primary_time_ref"])
        if header.get("observed_entity_ref") is not None:
            self._validate_published_entity_ref(header["observed_entity_ref"])

    def _validate_published_metric_predicate_refs(
        self, header: dict[str, Any], payload: dict[str, Any]
    ) -> None:
        self._validate_predicate_refs_with_usage(
            header.get("default_predicate_refs"),
            required_usage="metric_qualifier",
            field_name="default_predicate_refs",
        )
        component_fields = (
            "count_target",
            "measure",
            "numerator",
            "denominator",
            "value_component",
            "score_source",
        )
        for field in component_fields:
            component = payload.get(field)
            if component is not None:
                qualifier_refs = component.get("qualifier_refs")
                if qualifier_refs:
                    self._validate_predicate_refs_with_usage(
                        qualifier_refs,
                        required_usage="metric_qualifier",
                        field_name=f"{field}.qualifier_refs",
                    )

    def _validate_request_scope_predicate_ref(self, predicate_ref: str | None) -> None:
        if predicate_ref is None:
            return
        self._validate_predicate_refs_with_usage(
            [predicate_ref],
            required_usage="request_scope",
            field_name="scope.predicate_ref",
        )

    def _validate_governance_predicate_refs(self, refs: list[str] | None) -> None:
        self._validate_predicate_refs_with_usage(
            refs,
            required_usage="governance_policy",
            field_name="governance_predicate_refs",
        )

    def _replace_process_exported_dimension_refs(
        self, process_contract_id: str, dimension_refs: list[str] | None
    ) -> None:
        self.metadata.execute(
            "DELETE FROM semantic_process_exported_dimension_refs WHERE process_contract_id = ?",
            [process_contract_id],
        )
        for position, dimension_ref in enumerate(dimension_refs or [], start=1):
            self.metadata.execute(
                """
                INSERT INTO semantic_process_exported_dimension_refs (
                    process_contract_id, position, dimension_ref
                ) VALUES (?, ?, ?)
                """,
                [process_contract_id, position, dimension_ref],
            )

    def _replace_enum_set_versions(
        self, enum_set_contract_id: str, versions: list[dict[str, Any]]
    ) -> None:
        version_rows = self.metadata.query_rows(
            """
            SELECT enum_set_version_id
            FROM semantic_enum_set_versions
            WHERE enum_set_contract_id = ?
            """,
            [enum_set_contract_id],
        )
        for version_row in version_rows:
            self.metadata.execute(
                "DELETE FROM semantic_enum_set_values WHERE enum_set_version_id = ?",
                [version_row["enum_set_version_id"]],
            )
        self.metadata.execute(
            "DELETE FROM semantic_enum_set_versions WHERE enum_set_contract_id = ?",
            [enum_set_contract_id],
        )
        created_at = now_iso()
        for version in versions:
            enum_set_version_id = f"esv_{uuid4().hex[:24]}"
            self.metadata.execute(
                """
                INSERT INTO semantic_enum_set_versions (
                    enum_set_version_id, enum_set_contract_id, enum_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [
                    enum_set_version_id,
                    enum_set_contract_id,
                    version["enum_version"],
                    created_at,
                    created_at,
                ],
            )
            for position, value in enumerate(version["values"], start=1):
                self.metadata.execute(
                    """
                    INSERT INTO semantic_enum_set_values (
                        enum_set_version_id, position, value_key, raw_value, label, aliases_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        enum_set_version_id,
                        position,
                        value["value_key"],
                        json.dumps(value["raw_value"]),
                        value["label"],
                        json.dumps(value.get("aliases") or []),
                    ],
                )

    def _validate_process_payload_refs(
        self, payload: dict[str, Any], *, require_published: bool = False
    ) -> None:
        validate_time = (
            self._validate_published_time_ref if require_published else self._validate_time_ref
        )
        process_type = payload["process_type"]
        if process_type == "experiment_context":
            analysis_window = payload.get("analysis_window")
            if analysis_window and analysis_window.get("anchor_ref") is not None:
                validate_time(analysis_window["anchor_ref"])
        elif process_type == "cohort_definition":
            validate_time(payload["cohort_anchor_ref"])
            observation_window = payload.get("observation_window")
            if observation_window and observation_window.get("anchor_ref") is not None:
                validate_time(observation_window["anchor_ref"])
            if payload.get("return_anchor_ref") is not None:
                validate_time(payload["return_anchor_ref"])
        elif process_type == "lifecycle_state_machine":
            if payload.get("evaluation_anchor_ref") is not None:
                validate_time(payload["evaluation_anchor_ref"])
            if payload.get("transition_anchor_ref") is not None:
                validate_time(payload["transition_anchor_ref"])

    def _validate_published_process_payload_refs(self, payload: dict[str, Any]) -> None:
        self._validate_process_payload_refs(payload, require_published=True)

    def _validate_process_refs(
        self,
        interface_contract: dict[str, Any],
        payload: dict[str, Any],
        *,
        require_published: bool = False,
    ) -> None:
        validate_entity = (
            self._validate_published_entity_ref if require_published else self._validate_entity_ref
        )
        validate_time = (
            self._validate_published_time_ref if require_published else self._validate_time_ref
        )
        validate_dims = (
            self._validate_published_dimension_refs
            if require_published
            else self._validate_dimension_refs
        )
        if interface_contract.get("contract_mode") == "entity_stream":
            validate_entity(interface_contract["entity_ref"])
        if interface_contract.get("anchor_time_ref") is not None:
            validate_time(interface_contract["anchor_time_ref"])
        validate_dims(interface_contract.get("exported_dimension_refs"))
        self._validate_process_payload_refs(payload, require_published=require_published)

    def _validate_published_process_refs(
        self,
        interface_contract: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        self._validate_process_refs(interface_contract, payload, require_published=True)

    def _validate_dimension_contract_refs(
        self, interface_contract: dict[str, Any], *, require_published: bool = False
    ) -> None:
        validate_enum = (
            self._validate_published_enum_set_ref
            if require_published
            else self._validate_enum_set_ref
        )
        validate_dim = (
            self._validate_published_dimension_ref
            if require_published
            else self._validate_dimension_ref
        )
        validate_time = (
            self._validate_published_time_ref if require_published else self._validate_time_ref
        )
        value_domain = interface_contract["value_domain"]
        if value_domain.get("enum_set_ref") is not None:
            validate_enum(value_domain["enum_set_ref"])
        hierarchy = interface_contract.get("hierarchy")
        if hierarchy and hierarchy.get("parent_dimension_ref") is not None:
            validate_dim(hierarchy["parent_dimension_ref"])
        time_derived_requirement = interface_contract.get("time_derived_requirement")
        if (
            time_derived_requirement
            and time_derived_requirement.get("required_time_anchor_ref") is not None
        ):
            validate_time(time_derived_requirement["required_time_anchor_ref"])

    def _validate_published_dimension_contract_refs(
        self, interface_contract: dict[str, Any]
    ) -> None:
        self._validate_dimension_contract_refs(interface_contract, require_published=True)

    def _validate_no_dimension_cycle(self, dimension_ref: str, parent_dimension_ref: str) -> None:
        visited: set[str] = set()
        current: str | None = parent_dimension_ref
        while current is not None:
            if current == dimension_ref:
                raise self._validation_error(
                    f"Circular dimension hierarchy: '{dimension_ref}' already appears as an ancestor"
                )
            if current in visited:
                break
            visited.add(current)
            row = self.metadata.query_one(
                "SELECT parent_dimension_ref FROM semantic_dimension_contracts WHERE dimension_ref = ?",
                [current],
            )
            current = row["parent_dimension_ref"] if row else None

    def _validate_binding_target_ref(self, binding_scope: str, bound_object_ref: str) -> None:
        lookup = {
            "entity": (
                "SELECT entity_contract_id FROM semantic_entity_contracts WHERE entity_ref = ?",
                "entity",
            ),
            "process_object": (
                "SELECT process_contract_id FROM semantic_process_objects WHERE process_ref = ?",
                "process_object",
            ),
            "metric": (
                "SELECT metric_contract_id FROM semantic_metric_contracts WHERE metric_ref = ?",
                "metric",
            ),
        }
        sql_and_name = lookup.get(binding_scope)
        if sql_and_name is None:
            raise self._validation_error(f"Unsupported binding_scope: {binding_scope}")
        sql, object_name = sql_and_name
        if self.metadata.query_one(sql, [bound_object_ref]) is None:
            raise self._validation_error(f"Unknown {object_name} ref: {bound_object_ref}")

    def _get_entity_contract_by_ref(self, entity_ref: str) -> dict[str, Any] | None:
        row = self.metadata.query_one(
            "SELECT * FROM semantic_entity_contracts WHERE entity_ref = ?",
            [entity_ref],
        )
        return None if row is None else self._row_to_typed_entity(row)

    def _get_metric_contract_by_ref(self, metric_ref: str) -> dict[str, Any] | None:
        row = self.metadata.query_one(
            "SELECT * FROM semantic_metric_contracts WHERE metric_ref = ?",
            [metric_ref],
        )
        return None if row is None else self._row_to_typed_metric(row)

    def _get_process_object_by_ref(self, process_ref: str) -> dict[str, Any] | None:
        row = self.metadata.query_one(
            "SELECT * FROM semantic_process_objects WHERE process_ref = ?",
            [process_ref],
        )
        return None if row is None else self._row_to_process_object(row)

    def _get_dimension_by_ref(self, dimension_ref: str) -> dict[str, Any] | None:
        row = self.metadata.query_one(
            "SELECT * FROM semantic_dimension_contracts WHERE dimension_ref = ?",
            [dimension_ref],
        )
        return None if row is None else self._row_to_dimension(row)

    def _get_time_semantic_by_ref(self, time_ref: str) -> dict[str, Any] | None:
        row = self.metadata.query_one(
            "SELECT * FROM semantic_time_objects WHERE time_ref = ?",
            [time_ref],
        )
        return None if row is None else self._row_to_time_semantic(row)

    def _get_typed_binding_by_ref(self, binding_ref: str) -> dict[str, Any] | None:
        row = self.metadata.query_one(
            "SELECT * FROM typed_bindings WHERE binding_ref = ?",
            [binding_ref],
        )
        return None if row is None else self._row_to_typed_binding(row)

    def _validate_published_binding_ref(self, binding_ref: str) -> None:
        row = self.metadata.query_one(
            """
            SELECT binding_id
            FROM typed_bindings
            WHERE binding_ref = ? AND status = 'published'
            """,
            [binding_ref],
        )
        if row is None:
            raise self._validation_error(f"Binding ref must be published: {binding_ref}")

    def _resolve_binding_source_object(
        self,
        carrier: dict[str, Any],
        *,
        require_resolution: bool,
    ) -> dict[str, Any] | None:
        source_object_ref = carrier.get("source_object_ref")
        carrier_locator = carrier.get("carrier_locator")
        carrier_kind = carrier["carrier_kind"]

        if source_object_ref is not None:
            row = self.metadata.query_one(
                """
                SELECT object_id, object_type, fqn, authority_locator_json
                FROM source_objects
                WHERE object_id = ?
                """,
                [source_object_ref],
            )
            if row is None:
                raise self._validation_error(f"Unknown source_object_ref: {source_object_ref}")
            if row["object_type"] != carrier_kind:
                raise self._validation_error(
                    "carrier_kind does not match resolved source object type "
                    f"for carrier '{carrier['binding_key']}': expected '{carrier_kind}', "
                    f"got '{row['object_type']}'"
                )
            row_dict = dict(row)
            row_dict["authority_locator"] = json.loads(str(row["authority_locator_json"]))
            if carrier_locator is None or not _locator_matches_source_object(
                row_dict, carrier_locator
            ):
                raise self._validation_error(
                    "carrier_locator does not match resolved source object authority locator "
                    f"for carrier '{carrier['binding_key']}': expected '{row_dict['authority_locator']}', "
                    f"got '{carrier_locator}'"
                )
            return row_dict

        if not require_resolution:
            return None

        rows = self.metadata.query_rows(
            """
            SELECT object_id, object_type, fqn, authority_locator_json
            FROM source_objects
            WHERE object_type = ?
            ORDER BY object_id
            """,
            [carrier_kind],
        )
        matching_rows: list[dict[str, Any]] = []
        for row in rows:
            row_dict = dict(row)
            row_dict["authority_locator"] = json.loads(str(row["authority_locator_json"]))
            if carrier_locator is not None and _locator_matches_source_object(
                row_dict, carrier_locator
            ):
                matching_rows.append(row_dict)
        rows = matching_rows
        if not rows:
            raise self._validation_error(
                "carrier_locator must resolve to a synced source_object at publish time: "
                f"{carrier_locator}"
            )
        if len(rows) > 1:
            raise self._validation_error(
                "carrier_locator resolved to multiple source_objects; use source_object_ref to "
                f"disambiguate: {carrier_locator}"
            )
        return dict(rows[0])

    def _validate_binding_field_target(
        self,
        field_binding: dict[str, Any],
        *,
        require_published_refs: bool,
    ) -> None:
        target = field_binding["target"]
        target_kind = target["target_kind"]
        target_key = str(target.get("target_key") or "")
        semantic_ref = str(field_binding["semantic_ref"])

        if target_kind == "identity_key":
            if not target_key.startswith("key."):
                raise self._validation_error(
                    "identity_key target_key must use 'key.' prefix: "
                    f"{field_binding['carrier_binding_key']} -> {target_key}"
                )
            if semantic_ref != target_key:
                raise self._validation_error(
                    "identity_key semantic_ref must match target_key exactly: "
                    f"{semantic_ref} != {target_key}"
                )
            return

        if target_kind == "primary_time":
            if not semantic_ref.startswith("time."):
                raise self._validation_error(
                    f"primary_time semantic_ref must use 'time.' prefix, got: {semantic_ref}"
                )
            if target_key and semantic_ref != target_key:
                raise self._validation_error(
                    "primary_time semantic_ref must match target_key when target_key is provided: "
                    f"{semantic_ref} != {target_key}"
                )
            if require_published_refs:
                self._validate_published_time_ref(semantic_ref)
            else:
                self._validate_time_ref(semantic_ref)
            return

        if target_kind == "stable_descriptor":
            if not target_key.startswith("dimension."):
                raise self._validation_error(
                    f"stable_descriptor target_key must use 'dimension.' prefix, got: {target_key}"
                )
            if semantic_ref != target_key:
                raise self._validation_error(
                    "stable_descriptor semantic_ref must match target_key exactly: "
                    f"{semantic_ref} != {target_key}"
                )
            if require_published_refs:
                self._validate_published_dimension_ref(semantic_ref)
            else:
                self._validate_dimension_ref(semantic_ref)
            return

        if target_kind == "population_subject":
            if not target_key.startswith("key."):
                raise self._validation_error(
                    f"population_subject target_key must use 'key.' prefix, got: {target_key}"
                )
            if not semantic_ref.startswith("key."):
                raise self._validation_error(
                    f"population_subject semantic_ref must use 'key.' prefix, got: {semantic_ref}"
                )
            return

        if target_kind == "analysis_window_anchor":
            if not semantic_ref.startswith("time."):
                raise self._validation_error(
                    "analysis_window_anchor semantic_ref must use 'time.' prefix, "
                    f"got: {semantic_ref}"
                )
            if require_published_refs:
                self._validate_published_time_ref(semantic_ref)
            else:
                self._validate_time_ref(semantic_ref)
            return

        if target_kind == "process_context":
            if not target_key.startswith("process."):
                raise self._validation_error(
                    f"process_context target_key must use 'process.' prefix, got: {target_key}"
                )
            if not semantic_ref.startswith("process."):
                raise self._validation_error(
                    f"process_context semantic_ref must use 'process.' prefix, got: {semantic_ref}"
                )
            return

        if target_kind == "metric_input":
            if not semantic_ref.startswith("metric_input."):
                raise self._validation_error(
                    f"metric_input semantic_ref must use 'metric_input.' prefix, got: {semantic_ref}",
                    code="metric_input_semantic_ref_prefix_invalid",
                    field_path="interface_contract.field_bindings[].semantic_ref",
                    remediation={
                        "example_patch": {
                            "target": {"target_kind": "metric_input", "target_key": "numerator"},
                            "semantic_ref": "metric_input.numerator",
                        }
                    },
                )
            if not target_key:
                raise self._validation_error(
                    "metric_input target_key must not be empty",
                    code="metric_input_target_key_invalid",
                    field_path="interface_contract.field_bindings[].target.target_key",
                    remediation={"target_key": "numerator"},
                )
            if target_key.startswith("metric_input."):
                raise self._validation_error(
                    "metric_input target_key must be the metric family slot name "
                    "(for example count_target, measure, numerator, denominator), "
                    f"not a semantic ref: {target_key}",
                    code="metric_input_target_key_invalid",
                    field_path="interface_contract.field_bindings[].target.target_key",
                    remediation={"target_key": "numerator"},
                )
            return

        if target_kind == "measure":
            raise self._validation_error(
                "Unsupported target_kind: 'measure'. measure.* is a metric payload ref, not a "
                "binding target_kind. Bind physical fields through target_kind='metric_input' "
                "and use the metric family slot name as target_key."
            )

        raise self._validation_error(f"Unsupported target_kind: {target_kind}")

    def _validate_time_binding_target(
        self,
        time_binding: dict[str, Any],
        *,
        carrier_time_surfaces: dict[str, set[str]],
        require_published_refs: bool,
    ) -> None:
        carrier_binding_key = str(time_binding.get("carrier_binding_key") or "")
        if carrier_binding_key not in carrier_time_surfaces:
            raise self._validation_error(
                f"Unknown carrier_binding_key in time binding: {carrier_binding_key}"
            )

        target = dict(time_binding.get("target") or {})
        target_kind = str(target.get("target_kind") or "")
        target_key = str(target.get("target_key") or "")
        semantic_ref = str(time_binding.get("semantic_ref") or "")
        if target_kind not in {"primary_time", "analysis_window_anchor"}:
            raise self._validation_error(
                "time_binding target_kind must be 'primary_time' or 'analysis_window_anchor'"
            )
        if not semantic_ref.startswith("time."):
            raise self._validation_error(
                f"time_binding semantic_ref must use 'time.' prefix, got: {semantic_ref}"
            )
        if target_kind == "primary_time" and target_key and semantic_ref != target_key:
            raise self._validation_error(
                "primary_time semantic_ref must match target_key when target_key is provided: "
                f"{semantic_ref} != {target_key}"
            )
        if require_published_refs:
            self._validate_published_time_ref(semantic_ref)
        else:
            self._validate_time_ref(semantic_ref)

        resolution_kind = str(time_binding.get("resolution_kind") or "")
        timestamp_surface_ref = _optional_str(time_binding.get("timestamp_surface_ref"))
        date_surface_ref = _optional_str(time_binding.get("date_surface_ref"))
        hour_surface_ref = _optional_str(time_binding.get("hour_surface_ref"))
        timestamp_format = _optional_str(time_binding.get("timestamp_format"))
        date_format = _optional_str(time_binding.get("date_format"))
        hour_format = _optional_str(time_binding.get("hour_format"))
        timezone_strategy = _optional_str(time_binding.get("timezone_strategy"))

        if timezone_strategy not in {None, "session_consistent_naive"}:
            raise self._validation_error(
                "time_binding timezone_strategy must be 'session_consistent_naive' when provided"
            )

        surface_refs = carrier_time_surfaces[carrier_binding_key]

        def ensure_surface_exists(label: str, surface_ref: str | None) -> None:
            if surface_ref is None:
                return
            if surface_ref not in surface_refs:
                raise self._validation_error(
                    f"time_binding {label} does not exist on carrier "
                    f"'{carrier_binding_key}': {surface_ref}"
                )

        ensure_surface_exists("timestamp_surface_ref", timestamp_surface_ref)
        ensure_surface_exists("date_surface_ref", date_surface_ref)
        ensure_surface_exists("hour_surface_ref", hour_surface_ref)

        if resolution_kind == "timestamp_column":
            if timestamp_surface_ref is None:
                raise self._validation_error(
                    "time_binding timestamp_column resolution requires timestamp_surface_ref"
                )
            if timestamp_format is not None:
                try:
                    normalize_timestamp_format(timestamp_format)
                except ValueError as exc:
                    raise self._validation_error(str(exc)) from exc
            if any(
                value is not None
                for value in (date_surface_ref, date_format, hour_surface_ref, hour_format)
            ):
                raise self._validation_error(
                    "time_binding timestamp_column resolution cannot include date/hour surfaces or formats"
                )
        elif resolution_kind == "date_column":
            if date_surface_ref is None:
                raise self._validation_error(
                    "time_binding date_column resolution requires date_surface_ref"
                )
            if (
                timestamp_surface_ref is not None
                or timestamp_format is not None
                or hour_surface_ref is not None
            ):
                raise self._validation_error(
                    "time_binding date_column resolution cannot include timestamp/hour surfaces "
                    "or timestamp_format"
                )
            if hour_format is not None:
                raise self._validation_error(
                    "time_binding date_column resolution cannot include hour_format"
                )
            if date_format is not None and _normalize_date_format(date_format) is None:
                raise self._validation_error(f"Unsupported time_binding date_format: {date_format}")
        elif resolution_kind == "date_hour_columns":
            if date_surface_ref is None or hour_surface_ref is None:
                raise self._validation_error(
                    "time_binding date_hour_columns resolution requires date_surface_ref and hour_surface_ref"
                )
            if timestamp_surface_ref is not None or timestamp_format is not None:
                raise self._validation_error(
                    "time_binding date_hour_columns resolution cannot include "
                    "timestamp_surface_ref or timestamp_format"
                )
            if date_format is not None and _normalize_date_format(date_format) is None:
                raise self._validation_error(f"Unsupported time_binding date_format: {date_format}")
            if hour_format is not None and _normalize_hour_format(hour_format) is None:
                raise self._validation_error(f"Unsupported time_binding hour_format: {hour_format}")
        else:
            raise self._validation_error(
                f"Unsupported time_binding resolution_kind: {resolution_kind}"
            )

    def _validate_process_dimension_anchor_requirements(
        self,
        process_object: dict[str, Any],
        *,
        require_published_refs: bool,
    ) -> None:
        interface_contract = process_object["interface_contract"]
        payload = process_object["payload"]
        available_anchor_refs = {
            ref
            for ref in [
                interface_contract.get("anchor_time_ref"),
                payload.get("cohort_anchor_ref"),
                payload.get("return_anchor_ref"),
                (payload.get("analysis_window") or {}).get("anchor_ref"),
                (payload.get("observation_window") or {}).get("anchor_ref"),
            ]
            if ref is not None
        }

        for dimension_ref in interface_contract.get("exported_dimension_refs") or []:
            dimension = self._get_dimension_by_ref(dimension_ref)
            if dimension is None:
                raise self._validation_error(f"Unknown dimension ref: {dimension_ref}")
            if require_published_refs and dimension["status"] != "published":
                raise self._validation_error(
                    f"Referenced dimension must be published before binding publish: {dimension_ref}"
                )
            requirement = (
                dimension["interface_contract"].get("time_derived_requirement") or {}
            ).get("required_time_anchor_ref")
            if requirement is not None and requirement not in available_anchor_refs:
                raise self._validation_error(
                    "Process exported time_derived dimension requires a matching time anchor: "
                    f"{dimension_ref} requires {requirement}"
                )

    def _binding_source_keys(self, carrier_bindings: list[dict[str, Any]]) -> set[str]:
        keys: set[str] = set()
        for carrier in carrier_bindings:
            source_object = self._resolve_binding_source_object(carrier, require_resolution=False)
            if source_object is not None:
                for key in ("object_id", "fqn", "native_name"):
                    value = _optional_str(source_object.get(key))
                    if value is not None:
                        keys.add(value)
            source_object_ref = _optional_str(carrier.get("source_object_ref"))
            if source_object_ref is not None:
                keys.add(source_object_ref)
            locator_ref = _carrier_locator_ref(carrier.get("carrier_locator"))
            if locator_ref is not None:
                keys.add(locator_ref)
        return keys

    @staticmethod
    def _matches_required_prefix(value: str | None, prefixes: list[Any]) -> bool:
        if value is None:
            return False
        normalized_prefixes = [str(prefix).strip() for prefix in prefixes if str(prefix).strip()]
        return not normalized_prefixes or any(
            value.startswith(prefix) for prefix in normalized_prefixes
        )

    def _binding_target_exists_effective(
        self,
        *,
        local_bindings: list[dict[str, Any]],
        imports: list[dict[str, Any]],
        carrier_bindings: list[dict[str, Any]],
        target_kind: str,
        target_key: str | None,
        semantic_ref: str | None,
    ) -> bool:
        if binding_contract_target_exists(
            local_bindings,
            target_kind=target_kind,
            target_key=target_key,
            semantic_ref=semantic_ref,
        ):
            return True
        if target_kind == "metric_input":
            return False
        local_source_keys = self._binding_source_keys(carrier_bindings)
        candidates: list[str] = []
        for binding_import in imports:
            imported_binding_ref = _optional_str(binding_import.get("binding_ref"))
            if imported_binding_ref is None:
                continue
            imported_binding = self._get_typed_binding_by_ref(imported_binding_ref)
            if imported_binding is None or imported_binding.get("status") != "published":
                continue
            imported_contract = dict(imported_binding.get("interface_contract") or {})
            imported_source_keys = self._binding_source_keys(
                list(imported_contract.get("carrier_bindings") or [])
            )
            if (
                local_source_keys
                and imported_source_keys
                and not local_source_keys.intersection(imported_source_keys)
            ):
                continue
            prefixes = list(binding_import.get("required_ref_prefixes") or [])
            imported_bindings = list(imported_contract.get("field_bindings") or []) + list(
                imported_contract.get("time_bindings") or []
            )
            for imported_target in imported_bindings:
                target = dict(imported_target.get("target") or {})
                imported_kind = str(target.get("target_kind") or "")
                imported_key = str(target.get("target_key") or "")
                imported_ref = _optional_str(imported_target.get("semantic_ref"))
                if imported_kind != target_kind:
                    continue
                if target_key is not None and imported_key != target_key:
                    continue
                if semantic_ref is not None and imported_ref != semantic_ref:
                    continue
                if not (
                    self._matches_required_prefix(imported_ref, prefixes)
                    or self._matches_required_prefix(imported_key, prefixes)
                ):
                    continue
                candidates.append(imported_binding_ref)
        if len(set(candidates)) > 1:
            raise self._validation_error(
                f"Binding has ambiguous imported {target_kind} coverage for {target_key}",
                code="binding_imported_target_coverage_ambiguous",
                field_path="interface_contract.imports",
                remediation={"candidate_binding_refs": sorted(set(candidates))},
            )
        return bool(candidates)

    def _validate_binding_scope_compatibility(
        self,
        *,
        binding_scope: str,
        bound_object: dict[str, Any],
        imports: list[dict[str, Any]],
        field_bindings: list[dict[str, Any]],
        time_bindings: list[dict[str, Any]],
        carrier_bindings: list[dict[str, Any]],
        join_relations: list[dict[str, Any]],
        require_published_refs: bool,
    ) -> None:
        target_kinds = {
            binding["target"]["target_kind"] for binding in [*field_bindings, *time_bindings]
        }
        time_target_bindings = field_bindings + time_bindings

        if binding_scope == "entity":
            entity_ref = bound_object["header"]["entity_ref"]
            interface_contract = bound_object["interface_contract"]
            allowed_target_kinds = {"identity_key", "primary_time", "stable_descriptor"}
            unexpected = target_kinds - allowed_target_kinds
            if unexpected:
                raise self._validation_error(
                    f"Entity binding cannot use target kinds: {sorted(unexpected)}",
                    code="binding_target_kind_not_allowed_for_scope",
                    field_path="interface_contract.field_bindings[].target.target_kind",
                    remediation={
                        "binding_scope": binding_scope,
                        "allowed_target_kinds": sorted(allowed_target_kinds),
                        "invalid_target_kinds": sorted(unexpected),
                    },
                )
            for key_ref in interface_contract["identity"]["key_refs"]:
                if not binding_contract_target_exists(
                    field_bindings,
                    target_kind="identity_key",
                    target_key=key_ref,
                    semantic_ref=key_ref,
                ):
                    raise self._validation_error(
                        f"Entity binding must map identity key '{key_ref}' for {entity_ref}"
                    )
            primary_time_ref = interface_contract.get("primary_time_ref")
            if primary_time_ref is not None and not binding_contract_target_exists(
                time_target_bindings,
                target_kind="primary_time",
                semantic_ref=primary_time_ref,
            ):
                raise self._validation_error(
                    f"Entity binding must map primary_time_ref '{primary_time_ref}' for {entity_ref}"
                )
            for descriptor in interface_contract.get("stable_descriptors") or []:
                dimension_ref = descriptor["dimension_ref"]
                if not binding_contract_target_exists(
                    field_bindings,
                    target_kind="stable_descriptor",
                    target_key=dimension_ref,
                    semantic_ref=dimension_ref,
                ):
                    raise self._validation_error(
                        "Entity binding must map stable descriptor "
                        f"'{dimension_ref}' for {entity_ref}"
                    )
            for carrier in carrier_bindings:
                primary_entity_ref = carrier.get("primary_entity_ref")
                if primary_entity_ref is not None and primary_entity_ref != entity_ref:
                    raise self._validation_error(
                        "Entity binding carrier primary_entity_ref must match bound entity_ref: "
                        f"{primary_entity_ref} != {entity_ref}"
                    )
            return

        if binding_scope == "process_object":
            interface_contract = bound_object["interface_contract"]
            allowed_target_kinds = {
                "population_subject",
                "primary_time",
                "analysis_window_anchor",
                "process_context",
            }
            unexpected = target_kinds - allowed_target_kinds
            if unexpected:
                raise self._validation_error(
                    f"Process binding cannot use target kinds: {sorted(unexpected)}",
                    code="binding_target_kind_not_allowed_for_scope",
                    field_path="interface_contract.field_bindings[].target.target_kind",
                    remediation={
                        "binding_scope": binding_scope,
                        "allowed_target_kinds": sorted(allowed_target_kinds),
                        "invalid_target_kinds": sorted(unexpected),
                    },
                )
            if not any(
                field_binding["target"]["target_kind"] == "population_subject"
                for field_binding in field_bindings
            ):
                raise self._validation_error(
                    "Process binding must map at least one population_subject target"
                )
            anchor_time_ref = interface_contract.get("anchor_time_ref")
            if anchor_time_ref is not None and not any(
                binding["semantic_ref"] == anchor_time_ref
                and binding["target"]["target_kind"] in {"primary_time", "analysis_window_anchor"}
                for binding in time_target_bindings
            ):
                raise self._validation_error(
                    "Process binding must map its anchor_time_ref via primary_time or "
                    f"analysis_window_anchor: {anchor_time_ref}"
                )
            if bound_object["header"]["process_type"] == "experiment_context":
                if not any(
                    field_binding["target"]["target_kind"] == "process_context"
                    for field_binding in field_bindings
                ):
                    raise self._validation_error(
                        "experiment_context binding must map at least one process_context target"
                    )
                if bound_object["payload"].get("analysis_window") is not None and not any(
                    field_binding["target"]["target_kind"] == "analysis_window_anchor"
                    for field_binding in field_bindings
                ):
                    raise self._validation_error(
                        "experiment_context binding with analysis_window must map an "
                        "analysis_window_anchor"
                    )
                has_anchor_binding = any(
                    binding["target"]["target_kind"] == "analysis_window_anchor"
                    for binding in time_target_bindings
                )
                if has_anchor_binding and bound_object["payload"].get("analysis_window") is None:
                    raise self._validation_error(
                        "Binding declares analysis_window_anchor but process does not define "
                        "analysis_window"
                    )
            if len(carrier_bindings) > 1 and not join_relations:
                raise self._validation_error(
                    "Bindings with multiple process carriers must declare join_relations"
                )
            self._validate_process_dimension_anchor_requirements(
                bound_object,
                require_published_refs=require_published_refs,
            )
            return

        if binding_scope == "metric":
            header = bound_object["header"]
            payload = bound_object["payload"]
            allowed_target_kinds = {"population_subject", "primary_time", "metric_input"}
            unexpected = target_kinds - allowed_target_kinds
            if unexpected:
                raise self._validation_error(
                    f"Metric binding cannot use target kinds: {sorted(unexpected)}",
                    code="binding_target_kind_not_allowed_for_scope",
                    field_path="interface_contract.field_bindings[].target.target_kind",
                    remediation={
                        "binding_scope": binding_scope,
                        "allowed_target_kinds": sorted(allowed_target_kinds),
                        "invalid_target_kinds": sorted(unexpected),
                    },
                )
            population_subject_ref = header.get("population_subject_ref")
            if population_subject_ref is not None and not self._binding_target_exists_effective(
                local_bindings=time_target_bindings,
                imports=imports,
                carrier_bindings=carrier_bindings,
                target_kind="population_subject",
                target_key=None,
                semantic_ref=None,
            ):
                raise self._validation_error(
                    "Metric binding must map population_subject when the metric declares "
                    "population_subject_ref"
                )
            primary_time_ref = header.get("primary_time_ref")
            if primary_time_ref is not None and not self._binding_target_exists_effective(
                local_bindings=time_target_bindings,
                imports=imports,
                carrier_bindings=carrier_bindings,
                target_kind="primary_time",
                target_key=primary_time_ref,
                semantic_ref=primary_time_ref,
            ):
                raise self._validation_error(
                    f"Metric binding must map primary_time_ref '{primary_time_ref}'",
                    code="binding_primary_time_missing",
                    field_path="interface_contract.time_bindings",
                    remediation={
                        "required_target": "primary_time",
                        "semantic_ref": primary_time_ref,
                    },
                )
            metric_input_keys = {
                field_binding["target"]["target_key"]
                for field_binding in field_bindings
                if field_binding["target"]["target_kind"] == "metric_input"
            }
            required_metric_input_keys = set(self._required_metric_binding_slots(header, payload))
            if required_metric_input_keys and not metric_input_keys:
                raise self._validation_error(
                    "Metric binding must map at least one metric_input target",
                    code="binding_required_metric_input_missing",
                    field_path="interface_contract.field_bindings",
                    remediation={
                        "required_metric_input_target_keys": sorted(required_metric_input_keys),
                        "example_patch": {
                            "target": {"target_kind": "metric_input", "target_key": "measure"},
                            "semantic_ref": "metric_input.measure",
                        },
                    },
                )
            unexpected_metric_input_keys = metric_input_keys - required_metric_input_keys
            if unexpected_metric_input_keys:
                raise self._validation_error(
                    "Metric binding uses unsupported metric_input target_key values for "
                    f"{header['metric_family']}: {sorted(unexpected_metric_input_keys)}. "
                    f"Expected subset of {sorted(required_metric_input_keys)}.",
                    code="metric_input_target_key_invalid",
                    field_path="interface_contract.field_bindings[].target.target_key",
                    remediation={
                        "allowed_target_keys": sorted(required_metric_input_keys),
                        "invalid_target_keys": sorted(unexpected_metric_input_keys),
                    },
                )
            if header["metric_family"] == "rate_metric" and not required_metric_input_keys.issubset(
                metric_input_keys
            ):
                missing_metric_input_keys = sorted(required_metric_input_keys - metric_input_keys)
                raise self._validation_error(
                    f"{header['metric_family']} binding must map both 'numerator' and "
                    f"'denominator' metric_input targets; missing {missing_metric_input_keys}",
                    code="binding_required_metric_input_missing",
                    field_path="interface_contract.field_bindings",
                    remediation={
                        "missing_target_keys": missing_metric_input_keys,
                        "example_patch": [
                            {
                                "target": {"target_kind": "metric_input", "target_key": key},
                                "semantic_ref": f"metric_input.{key}",
                            }
                            for key in missing_metric_input_keys
                        ],
                    },
                )
            if header["metric_family"] == "survival_metric" and primary_time_ref is None:
                raise self._validation_error(
                    "survival_metric binding requires metric.primary_time_ref to be set"
                )
            return

        raise self._validation_error(f"Unsupported binding_scope: {binding_scope}")

    def _validate_typed_binding_contract(
        self,
        *,
        binding_ref: str,
        binding_scope: str,
        bound_object_ref: str,
        interface_contract: dict[str, Any],
        require_published_dependencies: bool,
    ) -> None:
        bound_object_lookup = {
            "entity": self._get_entity_contract_by_ref,
            "process_object": self._get_process_object_by_ref,
            "metric": self._get_metric_contract_by_ref,
        }
        resolver = bound_object_lookup.get(binding_scope)
        if resolver is None:
            raise self._validation_error(f"Unsupported binding_scope: {binding_scope}")
        bound_object = resolver(bound_object_ref)
        if bound_object is None:
            raise self._validation_error(f"Unknown {binding_scope} ref: {bound_object_ref}")
        if require_published_dependencies and bound_object["status"] != "published":
            raise self._validation_error(
                "Referenced semantic object must be published before binding publish: "
                f"{bound_object_ref}"
            )

        imports = interface_contract.get("imports") or []
        carrier_bindings = interface_contract.get("carrier_bindings") or []
        field_bindings = interface_contract.get("field_bindings") or []
        time_bindings = interface_contract.get("time_bindings") or []
        join_relations = interface_contract.get("join_relations") or []
        consumption_policies = interface_contract.get("consumption_policies") or []

        if not carrier_bindings:
            raise self._validation_error("Binding interface_contract must include carrier_bindings")
        if not field_bindings and not time_bindings:
            raise self._validation_error(
                "Binding interface_contract must include field_bindings or time_bindings"
            )

        import_keys: set[str] = set()
        for binding_import in imports:
            import_key = binding_import["import_key"]
            if import_key in import_keys:
                raise self._validation_error(f"Duplicate binding import key: {import_key}")
            import_keys.add(import_key)
            imported_binding_ref = binding_import["binding_ref"]
            if imported_binding_ref == binding_ref:
                raise self._validation_error("Binding cannot import itself")
            imported_binding = self._get_typed_binding_by_ref(imported_binding_ref)
            if imported_binding is None:
                raise self._validation_error(
                    f"Unknown imported binding_ref: {imported_binding_ref}"
                )
            if require_published_dependencies and imported_binding["status"] != "published":
                raise self._validation_error(
                    "Imported binding must be published before binding publish: "
                    f"{imported_binding_ref}"
                )

        carriers_by_key: dict[str, dict[str, Any]] = {}
        carrier_field_surfaces: dict[str, set[str]] = {}
        carrier_time_surfaces: dict[str, set[str]] = {}
        for carrier in carrier_bindings:
            binding_key = carrier["binding_key"]
            if binding_key in carriers_by_key:
                raise self._validation_error(f"Duplicate carrier binding_key: {binding_key}")
            field_surfaces = carrier.get("field_surfaces") or []
            time_surfaces = carrier.get("time_surfaces") or []
            field_surface_refs = [surface["surface_ref"] for surface in field_surfaces]
            time_surface_refs = [surface["surface_ref"] for surface in time_surfaces]
            if len(field_surface_refs) != len(set(field_surface_refs)):
                raise self._validation_error(
                    f"Duplicate field surface_ref in carrier '{binding_key}'"
                )
            if len(time_surface_refs) != len(set(time_surface_refs)):
                raise self._validation_error(
                    f"Duplicate time surface_ref in carrier '{binding_key}'"
                )
            if carrier.get("primary_entity_ref") is not None:
                if require_published_dependencies:
                    self._validate_published_entity_ref(carrier["primary_entity_ref"])
                else:
                    self._validate_entity_ref(carrier["primary_entity_ref"])
            row_filter_refs = carrier.get("row_filter_refs")
            if row_filter_refs:
                self._validate_predicate_refs_with_usage(
                    row_filter_refs,
                    required_usage="carrier_row_filter",
                    field_name="row_filter_refs",
                    require_published=require_published_dependencies,
                )
            self._resolve_binding_source_object(
                carrier,
                require_resolution=require_published_dependencies,
            )
            carriers_by_key[binding_key] = carrier
            carrier_field_surfaces[binding_key] = set(field_surface_refs)
            carrier_time_surfaces[binding_key] = set(time_surface_refs)

        for field_binding in field_bindings:
            carrier_binding_key = field_binding["carrier_binding_key"]
            if carrier_binding_key not in carriers_by_key:
                raise self._validation_error(
                    f"Unknown carrier_binding_key in field binding: {carrier_binding_key}"
                )
            if field_binding["surface_ref"] not in carrier_field_surfaces[carrier_binding_key]:
                raise self._validation_error(
                    "Field binding surface_ref does not exist on carrier "
                    f"'{carrier_binding_key}': {field_binding['surface_ref']}"
                )
            self._validate_binding_field_target(
                field_binding,
                require_published_refs=require_published_dependencies,
            )

        seen_time_targets: set[tuple[str, str, str]] = set()
        for time_binding in time_bindings:
            self._validate_time_binding_target(
                time_binding,
                carrier_time_surfaces=carrier_time_surfaces,
                require_published_refs=require_published_dependencies,
            )
            target = dict(time_binding.get("target") or {})
            dedupe_key = (
                str(time_binding.get("carrier_binding_key") or ""),
                str(target.get("target_kind") or ""),
                str(time_binding.get("semantic_ref") or ""),
            )
            if dedupe_key in seen_time_targets:
                raise self._validation_error(
                    "Duplicate time binding target on carrier "
                    f"'{dedupe_key[0]}' for semantic ref '{dedupe_key[2]}'"
                )
            seen_time_targets.add(dedupe_key)

        field_bindings_by_carrier: dict[str, list[dict[str, Any]]] = {}
        for field_binding in field_bindings:
            field_bindings_by_carrier.setdefault(field_binding["carrier_binding_key"], []).append(
                field_binding
            )

        for join_relation in join_relations:
            left_binding_key = join_relation["left_binding_key"]
            right_binding_key = join_relation["right_binding_key"]
            if left_binding_key not in carriers_by_key:
                raise self._validation_error(
                    f"join_relation references unknown left_binding_key: {left_binding_key}"
                )
            if right_binding_key not in carriers_by_key:
                raise self._validation_error(
                    f"join_relation references unknown right_binding_key: {right_binding_key}"
                )
            key_ref_pairs = join_relation.get("key_ref_pairs") or []
            temporal_constraint_refs = join_relation.get("temporal_constraint_refs") or []
            if not key_ref_pairs and not temporal_constraint_refs:
                raise self._validation_error(
                    "join_relation must declare key_ref_pairs or temporal_constraint_refs"
                )
            for left_key_ref, right_key_ref in key_ref_pairs:
                if not str(left_key_ref).startswith("key."):
                    raise self._validation_error(
                        f"join_relation left key ref must use 'key.' prefix: {left_key_ref}"
                    )
                if not str(right_key_ref).startswith("key."):
                    raise self._validation_error(
                        f"join_relation right key ref must use 'key.' prefix: {right_key_ref}"
                    )
                if not any(
                    field_binding["semantic_ref"] == left_key_ref
                    for field_binding in field_bindings_by_carrier.get(left_binding_key, [])
                ):
                    raise self._validation_error(
                        "join_relation left key ref is not mapped on carrier "
                        f"'{left_binding_key}': {left_key_ref}"
                    )
                if not any(
                    field_binding["semantic_ref"] == right_key_ref
                    for field_binding in field_bindings_by_carrier.get(right_binding_key, [])
                ):
                    raise self._validation_error(
                        "join_relation right key ref is not mapped on carrier "
                        f"'{right_binding_key}': {right_key_ref}"
                    )

        reserved_policy_roots = {"analysis_window", "observation_window"}
        for policy in consumption_policies:
            anchor_ref = policy.get("anchor_ref")
            if anchor_ref is not None:
                if require_published_dependencies:
                    self._validate_published_time_ref(anchor_ref)
                else:
                    self._validate_time_ref(anchor_ref)
            policy_target_path = str(policy["policy_target_path"])
            if "." in policy_target_path:
                root, _ = policy_target_path.split(".", 1)
                if (
                    root not in reserved_policy_roots
                    and root not in carriers_by_key
                    and root not in import_keys
                ):
                    raise self._validation_error(
                        "consumption policy target path must reference a known root "
                        f"(carrier/import/policy root), got: {policy_target_path}"
                    )

        self._validate_binding_scope_compatibility(
            binding_scope=binding_scope,
            bound_object=bound_object,
            imports=imports,
            field_bindings=field_bindings,
            time_bindings=time_bindings,
            carrier_bindings=carrier_bindings,
            join_relations=join_relations,
            require_published_refs=require_published_dependencies,
        )

    def _validate_profile_subject_ref(
        self,
        subject_kind: str,
        subject_ref: str,
        *,
        require_published: bool = False,
    ) -> None:
        lookup = {
            "metric": (
                "SELECT metric_contract_id FROM semantic_metric_contracts WHERE metric_ref = ?",
                """
                SELECT metric_contract_id
                FROM semantic_metric_contracts
                WHERE metric_ref = ? AND status = 'published'
                """,
            ),
            "process": (
                "SELECT process_contract_id FROM semantic_process_objects WHERE process_ref = ?",
                """
                SELECT process_contract_id
                FROM semantic_process_objects
                WHERE process_ref = ? AND status = 'published'
                """,
            ),
            "binding": (
                "SELECT binding_id FROM typed_bindings WHERE binding_ref = ?",
                """
                SELECT binding_id
                FROM typed_bindings
                WHERE binding_ref = ? AND status = 'published'
                """,
            ),
        }
        sql_pair = lookup.get(subject_kind)
        if sql_pair is None:
            raise self._validation_error(f"Unsupported subject_kind: {subject_kind}")
        exists_sql, published_sql = sql_pair
        if self.metadata.query_one(exists_sql, [subject_ref]) is None:
            raise self._validation_error(f"Unknown {subject_kind} ref: {subject_ref}")
        if require_published and self.metadata.query_one(published_sql, [subject_ref]) is None:
            raise self._compatibility_error(
                f"Compatibility profile subject must be published before profile publish: {subject_ref}",
                code="profile_subject_not_published",
            )

    def _published_profile_subject_revision(self, subject_kind: str, subject_ref: str) -> int:
        lookup = {
            "metric": (
                """
                SELECT revision
                FROM semantic_metric_contracts
                WHERE metric_ref = ? AND status = 'published'
                """,
                "metric",
            ),
            "process": (
                """
                SELECT revision
                FROM semantic_process_objects
                WHERE process_ref = ? AND status = 'published'
                """,
                "process",
            ),
            "binding": (
                """
                SELECT revision
                FROM typed_bindings
                WHERE binding_ref = ? AND status = 'published'
                """,
                "binding",
            ),
        }
        sql_pair = lookup.get(subject_kind)
        if sql_pair is None:
            raise self._validation_error(f"Unsupported subject_kind: {subject_kind}")
        sql, _label = sql_pair
        row = self.metadata.query_one(sql, [subject_ref])
        if row is None:
            raise self._compatibility_error(
                f"Compatibility profile subject must be published before profile publish: {subject_ref}",
                code="profile_subject_not_published",
            )
        return int(row["revision"])

    def _replace_entity_key_refs(self, entity_contract_id: str, key_refs: list[str]) -> None:
        self.metadata.execute(
            "DELETE FROM semantic_entity_key_refs WHERE entity_contract_id = ?",
            [entity_contract_id],
        )
        for position, key_ref in enumerate(key_refs, start=1):
            self.metadata.execute(
                """
                INSERT INTO semantic_entity_key_refs (
                    entity_contract_id, position, key_ref, description
                ) VALUES (?, ?, ?, ?)
                """,
                [entity_contract_id, position, key_ref, None],
            )

    def _replace_entity_stable_descriptors(
        self, entity_contract_id: str, stable_descriptors: list[dict[str, Any]] | None
    ) -> None:
        self.metadata.execute(
            "DELETE FROM semantic_entity_stable_descriptors WHERE entity_contract_id = ?",
            [entity_contract_id],
        )
        for position, descriptor in enumerate(stable_descriptors or [], start=1):
            self.metadata.execute(
                """
                INSERT INTO semantic_entity_stable_descriptors (
                    entity_contract_id, position, dimension_ref, cardinality
                ) VALUES (?, ?, ?, ?)
                """,
                [
                    entity_contract_id,
                    position,
                    descriptor["dimension_ref"],
                    descriptor.get("cardinality"),
                ],
            )

    def _delete_binding_children(self, binding_id: str) -> None:
        carrier_rows = self.metadata.query_rows(
            "SELECT carrier_binding_id FROM carrier_bindings WHERE binding_id = ?",
            [binding_id],
        )
        for carrier_row in carrier_rows:
            carrier_binding_id = carrier_row["carrier_binding_id"]
            self.metadata.execute(
                "DELETE FROM carrier_field_surfaces WHERE carrier_binding_id = ?",
                [carrier_binding_id],
            )
            self.metadata.execute(
                "DELETE FROM carrier_time_surfaces WHERE carrier_binding_id = ?",
                [carrier_binding_id],
            )
        self.metadata.execute("DELETE FROM field_bindings WHERE binding_id = ?", [binding_id])
        self.metadata.execute("DELETE FROM time_bindings WHERE binding_id = ?", [binding_id])
        self.metadata.execute("DELETE FROM join_relations WHERE binding_id = ?", [binding_id])
        self.metadata.execute("DELETE FROM consumption_policies WHERE binding_id = ?", [binding_id])
        self.metadata.execute("DELETE FROM binding_imports WHERE binding_id = ?", [binding_id])
        self.metadata.execute("DELETE FROM carrier_bindings WHERE binding_id = ?", [binding_id])

    def _replace_binding_contract(
        self, binding_id: str, interface_contract: dict[str, Any]
    ) -> None:
        self._delete_binding_children(binding_id)
        created_at = now_iso()

        for binding_import in interface_contract.get("imports", []):
            self.metadata.execute(
                """
                INSERT INTO binding_imports (
                    binding_id, import_key, imported_binding_ref,
                    required_ref_prefixes_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [
                    binding_id,
                    binding_import["import_key"],
                    binding_import["binding_ref"],
                    json.dumps(binding_import.get("required_ref_prefixes") or []),
                    created_at,
                ],
            )

        for carrier in interface_contract.get("carrier_bindings", []):
            carrier_binding_id = f"carb_{uuid4().hex[:24]}"
            self.metadata.execute(
                """
                INSERT INTO carrier_bindings (
                    carrier_binding_id, binding_id, binding_key, source_object_ref,
                    carrier_kind, carrier_locator, binding_role, semantic_role_ref,
                    grain_ref, primary_entity_ref, row_filter_refs_json,
                    freshness_policy_ref, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    carrier_binding_id,
                    binding_id,
                    carrier["binding_key"],
                    carrier.get("source_object_ref"),
                    carrier["carrier_kind"],
                    json.dumps(carrier["carrier_locator"]),
                    carrier["binding_role"],
                    carrier.get("semantic_role_ref"),
                    carrier.get("grain_ref"),
                    carrier.get("primary_entity_ref"),
                    json.dumps(carrier.get("row_filter_refs") or []),
                    carrier.get("freshness_policy_ref"),
                    created_at,
                    created_at,
                ],
            )
            for position, field_surface in enumerate(carrier.get("field_surfaces") or [], start=1):
                self.metadata.execute(
                    """
                    INSERT INTO carrier_field_surfaces (
                        carrier_binding_id, position, surface_ref, physical_name, field_type
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        carrier_binding_id,
                        position,
                        field_surface["surface_ref"],
                        field_surface["physical_name"],
                        field_surface.get("field_type"),
                    ],
                )
            for position, time_surface in enumerate(carrier.get("time_surfaces") or [], start=1):
                self.metadata.execute(
                    """
                    INSERT INTO carrier_time_surfaces (
                        carrier_binding_id, position, surface_ref, physical_name, time_granularity
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        carrier_binding_id,
                        position,
                        time_surface["surface_ref"],
                        time_surface["physical_name"],
                        time_surface.get("time_granularity"),
                    ],
                )

        for field_binding in interface_contract.get("field_bindings", []):
            self.metadata.execute(
                """
                INSERT INTO field_bindings (
                    field_binding_id, binding_id, carrier_binding_key, target_kind, target_key,
                    context_ref, semantic_ref, surface_ref, field_type_ref,
                    nullability_policy, repeated_value_policy, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    f"fbind_{uuid4().hex[:24]}",
                    binding_id,
                    field_binding["carrier_binding_key"],
                    field_binding["target"]["target_kind"],
                    field_binding["target"]["target_key"],
                    field_binding["target"].get("context_ref"),
                    field_binding["semantic_ref"],
                    field_binding["surface_ref"],
                    field_binding.get("field_type_ref"),
                    field_binding.get("nullability_policy"),
                    field_binding.get("repeated_value_policy"),
                    created_at,
                ],
            )

        for time_binding in interface_contract.get("time_bindings", []):
            target = time_binding["target"]
            self.metadata.execute(
                """
                INSERT INTO time_bindings (
                    time_binding_id, binding_id, carrier_binding_key, target_kind, target_key,
                    context_ref, semantic_ref, resolution_kind, timestamp_surface_ref,
                    timestamp_format,
                    date_surface_ref, date_format, hour_surface_ref, hour_format,
                    timezone_strategy, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    f"tbind_{uuid4().hex[:24]}",
                    binding_id,
                    time_binding["carrier_binding_key"],
                    target["target_kind"],
                    target.get("target_key") or "",
                    target.get("context_ref"),
                    time_binding["semantic_ref"],
                    time_binding["resolution_kind"],
                    time_binding.get("timestamp_surface_ref"),
                    time_binding.get("timestamp_format"),
                    time_binding.get("date_surface_ref"),
                    _normalize_date_format(time_binding.get("date_format")),
                    time_binding.get("hour_surface_ref"),
                    _normalize_hour_format(time_binding.get("hour_format")),
                    time_binding.get("timezone_strategy"),
                    created_at,
                ],
            )

        for join_relation in interface_contract.get("join_relations", []):
            self.metadata.execute(
                """
                INSERT INTO join_relations (
                    relation_id, binding_id, relation_key, left_binding_key, right_binding_key,
                    join_kind, key_ref_pairs_json, cardinality, temporal_constraint_refs_json,
                    compatibility_rule_refs_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    f"jrel_{uuid4().hex[:24]}",
                    binding_id,
                    join_relation["relation_key"],
                    join_relation["left_binding_key"],
                    join_relation["right_binding_key"],
                    join_relation.get("join_kind"),
                    json.dumps(join_relation.get("key_ref_pairs") or []),
                    join_relation.get("cardinality"),
                    json.dumps(join_relation.get("temporal_constraint_refs") or []),
                    json.dumps(join_relation.get("compatibility_rule_refs") or []),
                    created_at,
                ],
            )

        for policy in interface_contract.get("consumption_policies", []):
            self.metadata.execute(
                """
                INSERT INTO consumption_policies (
                    policy_id, binding_id, policy_key, policy_type, policy_target_path,
                    anchor_ref, grace_period_ref, behavior, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    f"cpol_{uuid4().hex[:24]}",
                    binding_id,
                    policy["policy_key"],
                    policy["policy_type"],
                    policy["policy_target_path"],
                    policy.get("anchor_ref"),
                    policy.get("grace_period_ref"),
                    policy.get("behavior"),
                    created_at,
                ],
            )

    def _row_to_entity(self, row: dict[str, Any]) -> dict[str, Any]:
        properties = json.loads(row["properties_json"])
        semantic_metadata = entity_runtime_metadata(
            level=row["level"],
            join_constraints_json=row["join_constraints_json"],
            upstream_dependencies_json=row["upstream_dependencies_json"],
            lineage_json=row["lineage_json"],
            quality_expectations_json=row["quality_expectations_json"],
        )
        entity = {
            "entity_id": row["entity_id"],
            "name": row["name"],
            "display_name": row["display_name"],
            "description": row["description"],
            "keys": json.loads(row["keys_json"]),
            "properties": properties,
            **semantic_metadata,
            "status": row["status"],
            "revision": row["revision"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        return self._augment_object_with_readiness(
            entity,
            object_kind="entity",
            row=row,
            id_field="entity_id",
            ref=self._entity_ref_for_name(str(row["name"])),
        )

    def _row_to_metric(self, row: dict[str, Any]) -> dict[str, Any]:
        properties = json.loads(row["properties_json"])
        dimensions = json.loads(row["dimensions_json"])
        semantic_metadata = metric_runtime_metadata(
            grain=row["grain"],
            measure_type=row["measure_type"],
            allowed_dimensions_json=row["allowed_dimensions_json"],
            lineage_json=row["lineage_json"],
            quality_expectations_json=row["quality_expectations_json"],
            dimensions=dimensions,
        )
        metric = {
            "metric_id": row["metric_id"],
            "name": row["name"],
            "display_name": row["display_name"],
            "description": row["description"],
            "definition_sql": row["definition_sql"],
            "dimensions": dimensions,
            "entity_id": row["entity_id"],
            "desired_direction": row.get("desired_direction"),
            "properties": properties,
            **semantic_metadata,
            "status": row["status"],
            "revision": row["revision"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        return self._augment_object_with_readiness(
            metric,
            object_kind="metric",
            row=row,
            id_field="metric_id",
            ref=self._metric_ref_for_name(str(row["name"])),
        )

    def _row_to_mapping(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "mapping_id": row["mapping_id"],
            "semantic_type": row["semantic_type"],
            "semantic_id": row["semantic_id"],
            "object_id": row["object_id"],
            "mapping_type": row["mapping_type"],
            "mapping_json": json.loads(row["mapping_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _row_to_typed_entity(
        self,
        row: dict[str, Any],
        mode: Literal["list", "detail"] = "detail",
        *,
        include_dependents: bool = True,
    ) -> dict[str, Any]:
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
        entity = {
            "entity_contract_id": row["entity_contract_id"],
            "header": {
                "entity_ref": row["entity_ref"],
                "display_name": row["display_name"],
                "description": row["description"],
                "entity_contract_version": row["entity_contract_version"],
            },
            "status": row["status"],
            "revision": row["revision"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            # Readiness evaluation needs the full contract even for lightweight list items.
            "interface_contract": {
                "identity": {
                    "key_refs": [key_row["key_ref"] for key_row in key_rows],
                    "uniqueness_scope": row["uniqueness_scope"],
                    "id_stability": row["id_stability"],
                    "nullable_key_policy": row["nullable_key_policy"],
                },
                "hierarchy": hierarchy,
                "primary_time_ref": row["primary_time_ref"],
                "stable_descriptors": [
                    {
                        "dimension_ref": descriptor_row["dimension_ref"],
                        "cardinality": descriptor_row["cardinality"],
                    }
                    for descriptor_row in descriptor_rows
                ]
                or None,
            },
        }
        entity = self._augment_object_with_readiness(
            entity,
            object_kind="entity",
            row=row,
            id_field="entity_contract_id",
            ref=str(row["entity_ref"]),
            mode=mode,
            include_dependents=include_dependents,
        )
        if mode == "list":
            entity.pop("interface_contract", None)
        return entity

    def _row_to_typed_metric(
        self,
        row: dict[str, Any],
        mode: Literal["list", "detail"] = "detail",
        *,
        include_dependents: bool = True,
        list_context: _SemanticListContext | None = None,
    ) -> dict[str, Any]:
        metric = {
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
                "additivity_constraints": json.loads(row["additivity_constraints_json"] or "null"),
                "default_predicate_refs": json.loads(row["default_predicate_refs_json"] or "[]")
                or None,
                "metric_contract_version": row["metric_contract_version"],
            },
            "status": row["status"],
            "revision": row["revision"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            # Readiness evaluation needs the full metric family contract even for list rows.
            "payload": json.loads(row["family_payload_json"]),
        }
        metric = self._augment_object_with_readiness(
            metric,
            object_kind="metric",
            row=row,
            id_field="metric_contract_id",
            ref=str(row["metric_ref"]),
            mode=mode,
            include_dependents=include_dependents,
            list_context=list_context,
        )
        if mode == "list":
            metric.pop("payload", None)
        return metric

    def _row_to_process_object(
        self,
        row: dict[str, Any],
        mode: Literal["list", "detail"] = "detail",
        *,
        include_dependents: bool = True,
    ) -> dict[str, Any]:
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
        process_object = {
            "process_contract_id": row["process_contract_id"],
            "header": {
                "process_ref": row["process_ref"],
                "display_name": row["display_name"],
                "description": row["description"],
                "process_type": row["process_type"],
                "process_contract_version": row["process_contract_version"],
            },
            "status": row["status"],
            "revision": row["revision"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            # Readiness evaluation needs the full contract even for lightweight list items.
            "interface_contract": interface_contract,
            "payload": json.loads(row["process_payload_json"]),
        }
        process_object = self._augment_object_with_readiness(
            process_object,
            object_kind="process",
            row=row,
            id_field="process_contract_id",
            ref=str(row["process_ref"]),
            mode=mode,
            include_dependents=include_dependents,
        )
        if mode == "list":
            process_object.pop("interface_contract", None)
            process_object.pop("payload", None)
        return process_object

    def _row_to_dimension(
        self,
        row: dict[str, Any],
        mode: Literal["list", "detail"] = "detail",
        *,
        include_dependents: bool = True,
        list_context: _SemanticListContext | None = None,
    ) -> dict[str, Any]:
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
        dimension = {
            "dimension_contract_id": row["dimension_contract_id"],
            "header": {
                "dimension_ref": row["dimension_ref"],
                "display_name": row["display_name"],
                "description": row["description"],
                "dimension_contract_version": row["dimension_contract_version"],
            },
            "status": row["status"],
            "revision": row["revision"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            # Readiness evaluation needs the full contract even for lightweight list items.
            "interface_contract": interface_contract,
        }
        dimension = self._augment_object_with_readiness(
            dimension,
            object_kind="dimension",
            row=row,
            id_field="dimension_contract_id",
            ref=str(row["dimension_ref"]),
            mode=mode,
            include_dependents=include_dependents,
            list_context=list_context,
        )
        if mode == "list":
            dimension.pop("interface_contract", None)
        return dimension

    def _row_to_time_semantic(
        self,
        row: dict[str, Any],
        mode: Literal["list", "detail"] = "detail",
        *,
        include_dependents: bool = True,
    ) -> dict[str, Any]:
        semantic_roles: list[str] = []
        if row["business_anchor"]:
            semantic_roles.append("business_anchor")
        if row["measurement"]:
            semantic_roles.append("measurement")
        if row["operational_support"]:
            semantic_roles.append("operational_support")
        time_semantic = {
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
        return self._augment_object_with_readiness(
            time_semantic,
            object_kind="time",
            row=row,
            id_field="time_contract_id",
            ref=str(row["time_ref"]),
            mode=mode,
            include_dependents=include_dependents,
        )

    def _row_to_enum_set(
        self,
        row: dict[str, Any],
        mode: Literal["list", "detail"] = "detail",
        *,
        include_dependents: bool = True,
    ) -> dict[str, Any]:
        version_rows = self.metadata.query_rows(
            """
            SELECT enum_set_version_id, enum_version
            FROM semantic_enum_set_versions
            WHERE enum_set_contract_id = ?
            ORDER BY enum_version
            """,
            [row["enum_set_contract_id"]],
        )
        versions: list[dict[str, Any]] = []
        for version_row in version_rows:
            value_rows = self.metadata.query_rows(
                """
                SELECT value_key, raw_value, label, aliases_json
                FROM semantic_enum_set_values
                WHERE enum_set_version_id = ?
                ORDER BY position
                """,
                [version_row["enum_set_version_id"]],
            )
            versions.append(
                {
                    "enum_version": version_row["enum_version"],
                    "values": [
                        {
                            "value_key": value_row["value_key"],
                            "raw_value": json.loads(value_row["raw_value"]),
                            "label": value_row["label"],
                            "aliases": json.loads(value_row["aliases_json"]) or None,
                        }
                        for value_row in value_rows
                    ],
                }
            )
        enum_set = {
            "enum_set_contract_id": row["enum_set_contract_id"],
            "header": {
                "enum_set_ref": row["enum_set_ref"],
                "value_type": row["value_type"],
            },
            "status": row["status"],
            "revision": row["revision"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        if mode == "detail":
            enum_set["display_name"] = row["display_name"]
            enum_set["description"] = row["description"]
            enum_set["versions"] = versions
        return self._augment_object_with_readiness(
            enum_set,
            object_kind="enum",
            row=row,
            id_field="enum_set_contract_id",
            ref=str(row["enum_set_ref"]),
            mode=mode,
            include_dependents=include_dependents,
        )

    def _row_to_typed_binding(
        self,
        row: dict[str, Any],
        mode: Literal["list", "detail"] = "detail",
        *,
        include_dependents: bool = True,
        list_context: _SemanticListContext | None = None,
    ) -> dict[str, Any]:
        preloaded = (
            list_context.binding_contract_for(str(row["binding_id"]))
            if list_context is not None
            else None
        )
        if preloaded is None:
            single_context = _SemanticListContext(self)
            carrier_rows = self.metadata.query_rows(
                """
                SELECT *
                FROM carrier_bindings
                WHERE binding_id = ?
                ORDER BY binding_key
                """,
                [row["binding_id"]],
            )
            field_surfaces_by_carrier: dict[str, list[dict[str, Any]]] = {}
            time_surfaces_by_carrier: dict[str, list[dict[str, Any]]] = {}
            for carrier_row in carrier_rows:
                carrier_binding_id = str(carrier_row["carrier_binding_id"])
                field_surfaces_by_carrier[carrier_binding_id] = self.metadata.query_rows(
                    """
                    SELECT surface_ref, physical_name, field_type
                    FROM carrier_field_surfaces
                    WHERE carrier_binding_id = ?
                    ORDER BY position
                    """,
                    [carrier_binding_id],
                )
                time_surfaces_by_carrier[carrier_binding_id] = self.metadata.query_rows(
                    """
                    SELECT surface_ref, physical_name, time_granularity
                    FROM carrier_time_surfaces
                    WHERE carrier_binding_id = ?
                    ORDER BY position
                    """,
                    [carrier_binding_id],
                )
            preloaded = single_context._binding_contract_from_rows(
                row=row,
                import_rows=self.metadata.query_rows(
                    """
                    SELECT import_key, imported_binding_ref, required_ref_prefixes_json
                    FROM binding_imports
                    WHERE binding_id = ?
                    ORDER BY id
                    """,
                    [row["binding_id"]],
                ),
                carrier_rows=carrier_rows,
                field_surfaces_by_carrier=field_surfaces_by_carrier,
                time_surfaces_by_carrier=time_surfaces_by_carrier,
                field_binding_rows=self.metadata.query_rows(
                    """
                    SELECT carrier_binding_key, target_kind, target_key, context_ref, semantic_ref,
                           surface_ref, field_type_ref, nullability_policy, repeated_value_policy
                    FROM field_bindings
                    WHERE binding_id = ?
                    ORDER BY carrier_binding_key, target_kind, target_key
                    """,
                    [row["binding_id"]],
                ),
                time_binding_rows=self.metadata.query_rows(
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
                ),
                join_rows=self.metadata.query_rows(
                    """
                    SELECT relation_key, left_binding_key, right_binding_key, join_kind,
                           key_ref_pairs_json, cardinality, temporal_constraint_refs_json,
                           compatibility_rule_refs_json
                    FROM join_relations
                    WHERE binding_id = ?
                    ORDER BY relation_key
                    """,
                    [row["binding_id"]],
                ),
                policy_rows=self.metadata.query_rows(
                    """
                    SELECT policy_key, policy_type, policy_target_path, anchor_ref,
                           grace_period_ref, behavior
                    FROM consumption_policies
                    WHERE binding_id = ?
                    ORDER BY policy_key
                    """,
                    [row["binding_id"]],
                ),
            )
        binding = {
            "binding_id": preloaded["binding_id"],
            "header": dict(preloaded["header"]),
            "status": preloaded["status"],
            "revision": preloaded["revision"],
            "created_at": preloaded["created_at"],
            "updated_at": preloaded["updated_at"],
            # Readiness evaluation needs the full contract even for lightweight list items.
            "interface_contract": preloaded["interface_contract"],
        }
        binding = self._augment_object_with_readiness(
            binding,
            object_kind="binding",
            row=row,
            id_field="binding_id",
            ref=str(row["binding_ref"]),
            mode=mode,
            include_dependents=include_dependents,
            list_context=list_context,
        )
        if mode == "list":
            binding.pop("interface_contract", None)
        return binding

    def _row_to_compatibility_profile(
        self,
        row: dict[str, Any],
        mode: Literal["list", "detail"] = "detail",
        *,
        include_dependents: bool = True,
    ) -> dict[str, Any]:
        requirement = json.loads(row["requirement_json"])
        capability = json.loads(row["capability_json"])
        profile = {
            "profile_id": row["profile_id"],
            "profile_ref": row["profile_ref"],
            "subject_kind": row["subject_kind"],
            "subject_ref": row["subject_ref"],
            "status": row["status"],
            "revision": row["revision"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        if mode == "detail":
            profile["profile_kind"] = row["profile_kind"]
            profile["schema_version"] = row["schema_version"]
            profile["subject_revision"] = row["subject_revision"]
            profile["requirement"] = requirement or None
            profile["capability"] = capability or None
        return self._augment_object_with_readiness(
            profile,
            object_kind="compiler_profile",
            row=row,
            id_field="profile_id",
            ref=str(row["profile_ref"]),
            mode=mode,
            include_dependents=include_dependents,
        )

    def _row_to_predicate(
        self,
        row: dict[str, Any],
        mode: Literal["list", "detail"] = "detail",
        *,
        include_dependents: bool = True,
        list_context: _SemanticListContext | None = None,
    ) -> dict[str, Any]:
        payload = json.loads(row["payload_json"] or "{}")
        predicate = {
            "predicate_contract_id": row["predicate_contract_id"],
            "header": {
                "predicate_ref": row["predicate_ref"],
                "display_name": row["display_name"],
                "description": row["description"],
                "subject_ref": row["subject_ref"],
                "predicate_contract_version": row["predicate_contract_version"],
            },
            "status": row["status"],
            "revision": row["revision"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "interface_contract": payload,
        }
        predicate = self._augment_object_with_readiness(
            predicate,
            object_kind="predicate",
            row=row,
            id_field="predicate_contract_id",
            ref=str(row["predicate_ref"]),
            mode=mode,
            include_dependents=include_dependents,
            list_context=list_context,
        )
        if mode == "list":
            predicate.pop("interface_contract", None)
        return predicate
