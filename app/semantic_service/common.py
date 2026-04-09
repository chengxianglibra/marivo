from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.semantic_runtime.semantic_metadata import (
    entity_runtime_metadata,
    metric_runtime_metadata,
)
from app.storage.metadata import MetadataStore

from .errors import SemanticNotFoundError, SemanticStateError, SemanticValidationError


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


class SemanticServiceSupport:
    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata

    def _entity_ref_for_name(self, name: str) -> str:
        return f"entity.{name}"

    def _metric_ref_for_name(self, name: str) -> str:
        return f"metric.{name}"

    def _not_found(self, message: str) -> SemanticNotFoundError:
        return SemanticNotFoundError(message)

    def _validation_error(self, message: str) -> SemanticValidationError:
        return SemanticValidationError(message)

    def _state_error(self, message: str) -> SemanticStateError:
        return SemanticStateError(message)

    def _sync_entity_contract(self, entity: dict[str, Any]) -> None:
        entity_contract_id = entity["entity_id"]
        entity_ref = self._entity_ref_for_name(str(entity["name"]))
        key_refs = [self._normalize_key_ref(str(key)) for key in list(entity.get("keys") or [])]
        contract_row = {
            "entity_contract_id": entity_contract_id,
            "entity_ref": entity_ref,
            "display_name": entity["display_name"],
            "description": entity.get("description") or "",
            "entity_contract_version": "entity.v1",
            "uniqueness_scope": "global",
            "id_stability": self._infer_entity_stability(entity.get("level")),
            "nullable_key_policy": "reject",
            "parent_entity_ref": None,
            "cardinality_to_parent": None,
            "ownership_semantics": None,
            "primary_time_ref": None,
            "status": entity["status"],
            "revision": entity["revision"],
            "created_at": entity["created_at"],
            "updated_at": entity["updated_at"],
        }
        self.metadata.execute(
            """
            INSERT INTO semantic_entity_contracts (
                entity_contract_id, entity_ref, display_name, description,
                entity_contract_version, uniqueness_scope, id_stability,
                nullable_key_policy, parent_entity_ref, cardinality_to_parent,
                ownership_semantics, primary_time_ref, status, revision,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entity_contract_id) DO UPDATE SET
                entity_ref = excluded.entity_ref,
                display_name = excluded.display_name,
                description = excluded.description,
                entity_contract_version = excluded.entity_contract_version,
                uniqueness_scope = excluded.uniqueness_scope,
                id_stability = excluded.id_stability,
                nullable_key_policy = excluded.nullable_key_policy,
                parent_entity_ref = excluded.parent_entity_ref,
                cardinality_to_parent = excluded.cardinality_to_parent,
                ownership_semantics = excluded.ownership_semantics,
                primary_time_ref = excluded.primary_time_ref,
                status = excluded.status,
                revision = excluded.revision,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at
            """,
            [
                contract_row["entity_contract_id"],
                contract_row["entity_ref"],
                contract_row["display_name"],
                contract_row["description"],
                contract_row["entity_contract_version"],
                contract_row["uniqueness_scope"],
                contract_row["id_stability"],
                contract_row["nullable_key_policy"],
                contract_row["parent_entity_ref"],
                contract_row["cardinality_to_parent"],
                contract_row["ownership_semantics"],
                contract_row["primary_time_ref"],
                contract_row["status"],
                contract_row["revision"],
                contract_row["created_at"],
                contract_row["updated_at"],
            ],
        )
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
        self.metadata.execute(
            "DELETE FROM semantic_entity_stable_descriptors WHERE entity_contract_id = ?",
            [entity_contract_id],
        )

    def _sync_metric_contract(self, metric: dict[str, Any]) -> None:
        metric_contract_id = metric["metric_id"]
        observed_entity_ref = self._resolve_observed_entity_ref(metric)
        metric_family, sample_kind, value_semantics, additivity = self._infer_metric_contract_axes(
            metric.get("measure_type")
        )
        observation_grain_ref = self._normalize_grain_ref(
            metric.get("grain")
        ) or self._normalize_grain_ref(metric["name"])
        family_payload = {
            "definition_sql": metric["definition_sql"],
            "dimensions": list(metric.get("dimensions") or []),
            "allowed_dimensions": list(metric.get("allowed_dimensions") or []),
            "grain": metric.get("grain"),
            "measure_type": metric.get("measure_type"),
            "desired_direction": metric.get("desired_direction"),
        }
        contract_row = [
            metric_contract_id,
            self._metric_ref_for_name(str(metric["name"])),
            metric["display_name"],
            metric.get("description") or "",
            metric_family,
            None,
            observed_entity_ref,
            observation_grain_ref,
            sample_kind,
            value_semantics,
            self._legacy_aggregation_scope(metric.get("grain")),
            None,
            additivity,
            "metric.v1",
            json.dumps(family_payload),
            metric["status"],
            metric["revision"],
            metric["created_at"],
            metric["updated_at"],
        ]
        self.metadata.execute(
            """
            INSERT INTO semantic_metric_contracts (
                metric_contract_id, metric_ref, display_name, description, metric_family,
                population_subject_ref, observed_entity_ref, observation_grain_ref,
                sample_kind, value_semantics, aggregation_scope, primary_time_ref,
                additivity, metric_contract_version, family_payload_json, status,
                revision, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(metric_contract_id) DO UPDATE SET
                metric_ref = excluded.metric_ref,
                display_name = excluded.display_name,
                description = excluded.description,
                metric_family = excluded.metric_family,
                population_subject_ref = excluded.population_subject_ref,
                observed_entity_ref = excluded.observed_entity_ref,
                observation_grain_ref = excluded.observation_grain_ref,
                sample_kind = excluded.sample_kind,
                value_semantics = excluded.value_semantics,
                aggregation_scope = excluded.aggregation_scope,
                primary_time_ref = excluded.primary_time_ref,
                additivity = excluded.additivity,
                metric_contract_version = excluded.metric_contract_version,
                family_payload_json = excluded.family_payload_json,
                status = excluded.status,
                revision = excluded.revision,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at
            """,
            contract_row,
        )

    def _resolve_observed_entity_ref(self, metric: dict[str, Any]) -> str:
        entity_id = metric.get("entity_id")
        if entity_id:
            row = self.metadata.query_one(
                "SELECT name FROM semantic_entities WHERE entity_id = ?",
                [entity_id],
            )
            if row is not None and row.get("name"):
                return self._entity_ref_for_name(str(row["name"]))
        preferred = self.metadata.query_one(
            "SELECT name FROM semantic_entities WHERE status = 'published' AND name = 'user' ORDER BY name LIMIT 1"
        )
        if preferred is not None and preferred.get("name"):
            return self._entity_ref_for_name(str(preferred["name"]))
        published_entities = self.metadata.query_rows(
            "SELECT name FROM semantic_entities WHERE status = 'published' ORDER BY name"
        )
        if len(published_entities) == 1:
            return self._entity_ref_for_name(str(published_entities[0]["name"]))
        return self._entity_ref_for_name(str(metric["name"]))

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
    def _infer_metric_contract_axes(measure_type: str | None) -> tuple[str, str, str, str]:
        kind = str(measure_type or "count").strip().lower()
        if kind in {"ratio", "rate"}:
            return ("rate_metric", "rate", "ratio", "non_additive")
        if kind in {"average", "mean"}:
            return ("average_metric", "numeric", "mean", "non_additive")
        if kind == "sum":
            return ("sum_metric", "numeric", "sum", "additive")
        if kind == "count":
            return ("count_metric", "numeric", "count", "additive")
        if kind in {"percentile", "quantile"}:
            return ("distribution_metric", "numeric", "distribution_statistic", "non_additive")
        if kind == "survival":
            return ("survival_metric", "survival", "survival_probability", "non_additive")
        if kind == "score":
            return ("score_metric", "numeric", "score", "non_additive")
        return ("count_metric", "numeric", "count", "additive")

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

    def _validate_profile_subject_ref(self, subject_kind: str, subject_ref: str) -> None:
        lookup = {
            "metric": "SELECT metric_contract_id FROM semantic_metric_contracts WHERE metric_ref = ?",
            "process": "SELECT process_contract_id FROM semantic_process_objects WHERE process_ref = ?",
            "binding": "SELECT binding_id FROM typed_bindings WHERE binding_ref = ?",
        }
        sql = lookup.get(subject_kind)
        if sql is None:
            raise self._validation_error(f"Unsupported subject_kind: {subject_kind}")
        if self.metadata.query_one(sql, [subject_ref]) is None:
            raise self._validation_error(f"Unknown {subject_kind} ref: {subject_ref}")

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
                    carrier["carrier_locator"],
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
        return {
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
        return {
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
                "stable_descriptors": [
                    {
                        "dimension_ref": descriptor_row["dimension_ref"],
                        "cardinality": descriptor_row["cardinality"],
                    }
                    for descriptor_row in descriptor_rows
                ]
                or None,
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

    def _row_to_enum_set(self, row: dict[str, Any]) -> dict[str, Any]:
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
        return {
            "enum_set_contract_id": row["enum_set_contract_id"],
            "header": {
                "enum_set_ref": row["enum_set_ref"],
                "value_type": row["value_type"],
            },
            "display_name": row["display_name"],
            "description": row["description"],
            "versions": versions,
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
                    or None,
                    "time_surfaces": [dict(surface_row) for surface_row in time_surface_rows]
                    or None,
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

    def _row_to_compatibility_profile(self, row: dict[str, Any]) -> dict[str, Any]:
        requirement = json.loads(row["requirement_json"])
        capability = json.loads(row["capability_json"])
        return {
            "profile_id": row["profile_id"],
            "profile_ref": row["profile_ref"],
            "profile_kind": row["profile_kind"],
            "schema_version": row["schema_version"],
            "subject_kind": row["subject_kind"],
            "subject_ref": row["subject_ref"],
            "requirement": requirement or None,
            "capability": capability or None,
            "status": row["status"],
            "revision": row["revision"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
