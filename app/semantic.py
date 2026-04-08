from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.api.models.binding import TypedBindingCreateRequest, TypedBindingUpdateRequest
from app.api.models.compatibility_profile import (
    CompatibilityProfileCreateRequest,
    CompatibilityProfileUpdateRequest,
)
from app.api.models.entity import TypedEntityCreateRequest, TypedEntityUpdateRequest
from app.api.models.metric import TypedMetricCreateRequest, TypedMetricUpdateRequest
from app.semantic_runtime.semantic_metadata import (
    entity_runtime_metadata,
    metric_runtime_metadata,
)
from app.storage.metadata import MetadataStore


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class SemanticService:
    """CRUD for semantic entities, metrics, and mappings with revision
    tracking and draft/published lifecycle."""

    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata

    def _entity_ref_for_name(self, name: str) -> str:
        return f"entity.{name}"

    def _metric_ref_for_name(self, name: str) -> str:
        return f"metric.{name}"

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
            raise ValueError(f"Unsupported binding_scope: {binding_scope}")
        sql, object_name = sql_and_name
        if self.metadata.query_one(sql, [bound_object_ref]) is None:
            raise ValueError(f"Unknown {object_name} ref: {bound_object_ref}")

    def _validate_profile_subject_ref(self, subject_kind: str, subject_ref: str) -> None:
        lookup = {
            "metric": "SELECT metric_contract_id FROM semantic_metric_contracts WHERE metric_ref = ?",
            "process": "SELECT process_contract_id FROM semantic_process_objects WHERE process_ref = ?",
            "binding": "SELECT binding_id FROM typed_bindings WHERE binding_ref = ?",
        }
        sql = lookup.get(subject_kind)
        if sql is None:
            raise ValueError(f"Unsupported subject_kind: {subject_kind}")
        if self.metadata.query_one(sql, [subject_ref]) is None:
            raise ValueError(f"Unknown {subject_kind} ref: {subject_ref}")

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
        now = _now_iso()

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
                    now,
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
                    now,
                    now,
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
                    now,
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
                    now,
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
                    now,
                ],
            )

    # ── Entity CRUD ──────────────────────────────────────────────

    def create_entity(
        self,
        name: str,
        display_name: str,
        keys: list[str],
        description: str = "",
        level: str | None = None,
        join_constraints: dict[str, Any] | None = None,
        upstream_dependencies: list[str] | None = None,
        lineage: list[str] | None = None,
        quality_expectations: dict[str, Any] | None = None,
        properties: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        entity_id = f"ent_{uuid4().hex[:12]}"
        now = _now_iso()
        self.metadata.execute(
            """
            INSERT INTO semantic_entities
                (
                    entity_id, name, display_name, description, keys_json, level,
                    join_constraints_json, upstream_dependencies_json, lineage_json,
                    quality_expectations_json, properties_json, status, revision, created_at, updated_at
                )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', 1, ?, ?)
            """,
            [
                entity_id,
                name,
                display_name,
                description,
                json.dumps(keys),
                level,
                json.dumps(join_constraints or {}),
                json.dumps(upstream_dependencies or []),
                json.dumps(lineage or []),
                json.dumps(quality_expectations or {}),
                json.dumps(properties or {}),
                now,
                now,
            ],
        )
        entity = self.get_entity(entity_id)
        self._sync_entity_contract(entity)
        return entity

    def get_entity(self, entity_id: str) -> dict[str, Any]:
        row = self.metadata.query_one(
            "SELECT * FROM semantic_entities WHERE entity_id = ?", [entity_id]
        )
        if row is None:
            raise KeyError(f"Unknown entity: {entity_id}")
        return self._row_to_entity(row)

    def list_entities(self, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            rows = self.metadata.query_rows(
                "SELECT * FROM semantic_entities WHERE status = ? ORDER BY name", [status]
            )
        else:
            rows = self.metadata.query_rows("SELECT * FROM semantic_entities ORDER BY name")
        return [self._row_to_entity(r) for r in rows]

    def update_entity(self, entity_id: str, **kwargs: Any) -> dict[str, Any]:
        entity = self.get_entity(entity_id)  # verify exists
        now = _now_iso()
        updates: list[str] = []
        params: list[Any] = []
        for field, col in [
            ("display_name", "display_name"),
            ("description", "description"),
        ]:
            if field in kwargs:
                updates.append(f"{col} = ?")
                params.append(kwargs[field])
        if "keys" in kwargs:
            updates.append("keys_json = ?")
            params.append(json.dumps(kwargs["keys"]))
        if "level" in kwargs:
            updates.append("level = ?")
            params.append(kwargs["level"])
        if "join_constraints" in kwargs:
            updates.append("join_constraints_json = ?")
            params.append(json.dumps(kwargs["join_constraints"]))
        if "upstream_dependencies" in kwargs:
            updates.append("upstream_dependencies_json = ?")
            params.append(json.dumps(kwargs["upstream_dependencies"]))
        if "lineage" in kwargs:
            updates.append("lineage_json = ?")
            params.append(json.dumps(kwargs["lineage"]))
        if "quality_expectations" in kwargs:
            updates.append("quality_expectations_json = ?")
            params.append(json.dumps(kwargs["quality_expectations"]))
        if "properties" in kwargs:
            updates.append("properties_json = ?")
            params.append(json.dumps(kwargs["properties"]))
        if not updates:
            return entity
        updates.append("updated_at = ?")
        params.append(now)
        params.append(entity_id)
        self.metadata.execute(
            f"UPDATE semantic_entities SET {', '.join(updates)} WHERE entity_id = ?",
            params,
        )
        entity = self.get_entity(entity_id)
        self._sync_entity_contract(entity)
        return entity

    def patch_entity_properties(
        self, entity_id: str, properties_patch: dict[str, Any]
    ) -> dict[str, Any]:
        """G-5d: Incrementally merge properties_patch into a published entity's properties_json.

        Only published entities may be patched (draft entities must go through
        publish first).  Bumps revision and updated_at.

        Raises:
            KeyError: entity not found.
            ValueError: entity is not published, or properties_patch is empty/invalid.
        """
        entity = self.get_entity(entity_id)  # raises KeyError if missing
        if entity.get("status") != "published":
            raise ValueError(
                f"Entity '{entity_id}' is not published (status={entity.get('status')}). "
                "Only published entities may be patched."
            )
        if not properties_patch or not isinstance(properties_patch, dict):
            raise ValueError("properties_patch must be a non-empty dict")

        current_props: dict[str, Any] = dict(entity.get("properties") or {})
        # Deep merge: if both sides have a "fields" dict, merge field-by-field
        # so patching one column's unit doesn't wipe other columns.
        if "fields" in properties_patch and isinstance(properties_patch["fields"], dict):
            merged_fields = dict(current_props.get("fields") or {})
            for col, col_props in properties_patch["fields"].items():
                if isinstance(col_props, dict):
                    existing = dict(merged_fields.get(col) or {})
                    existing.update(col_props)
                    merged_fields[col] = existing
                else:
                    merged_fields[col] = col_props
            current_props = {k: v for k, v in current_props.items() if k != "fields"}
            current_props["fields"] = merged_fields
            remaining_patch = {k: v for k, v in properties_patch.items() if k != "fields"}
            current_props.update(remaining_patch)
        else:
            current_props.update(properties_patch)
        now = _now_iso()
        self.metadata.execute(
            "UPDATE semantic_entities SET properties_json = ?, revision = revision + 1, updated_at = ? WHERE entity_id = ?",
            [json.dumps(current_props), now, entity_id],
        )
        entity = self.get_entity(entity_id)
        self._sync_entity_contract(entity)
        return entity

    def publish_entity(self, entity_id: str) -> dict[str, Any]:
        _ = self.get_entity(entity_id)  # Validate entity exists
        now = _now_iso()
        self.metadata.execute(
            "UPDATE semantic_entities SET status = 'published', revision = revision + 1, updated_at = ? WHERE entity_id = ?",
            [now, entity_id],
        )
        entity = self.get_entity(entity_id)
        self._sync_entity_contract(entity)
        return entity

    def deprecate_entity(self, entity_id: str) -> dict[str, Any]:
        self.get_entity(entity_id)  # verify exists
        now = _now_iso()
        self.metadata.execute(
            "UPDATE semantic_entities SET status = 'deprecated', updated_at = ? WHERE entity_id = ?",
            [now, entity_id],
        )
        entity = self.get_entity(entity_id)
        self._sync_entity_contract(entity)
        return entity

    # ── Metric CRUD ──────────────────────────────────────────────

    def create_metric(
        self,
        name: str,
        display_name: str,
        definition_sql: str,
        dimensions: list[str],
        description: str = "",
        entity_id: str | None = None,
        grain: str | None = None,
        measure_type: str | None = None,
        allowed_dimensions: list[str] | None = None,
        lineage: list[str] | None = None,
        quality_expectations: dict[str, Any] | None = None,
        properties: dict[str, Any] | None = None,
        desired_direction: str | None = None,
    ) -> dict[str, Any]:
        metric_id = f"met_{uuid4().hex[:12]}"
        now = _now_iso()
        self.metadata.execute(
            """
            INSERT INTO semantic_metrics
                (
                    metric_id, name, display_name, description, definition_sql, dimensions_json,
                    entity_id, grain, measure_type, allowed_dimensions_json, lineage_json,
                    quality_expectations_json, properties_json, desired_direction,
                    status, revision, created_at, updated_at
                )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', 1, ?, ?)
            """,
            [
                metric_id,
                name,
                display_name,
                description,
                definition_sql,
                json.dumps(dimensions),
                entity_id,
                grain,
                measure_type,
                json.dumps(allowed_dimensions or []),
                json.dumps(lineage or []),
                json.dumps(quality_expectations or {}),
                json.dumps(properties or {}),
                desired_direction,
                now,
                now,
            ],
        )
        metric = self.get_metric(metric_id)
        self._sync_metric_contract(metric)
        return metric

    def get_metric(self, metric_id: str) -> dict[str, Any]:
        row = self.metadata.query_one(
            "SELECT * FROM semantic_metrics WHERE metric_id = ?", [metric_id]
        )
        if row is None:
            raise KeyError(f"Unknown metric: {metric_id}")
        return self._row_to_metric(row)

    def list_metrics(self, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            rows = self.metadata.query_rows(
                "SELECT * FROM semantic_metrics WHERE status = ? ORDER BY name", [status]
            )
        else:
            rows = self.metadata.query_rows("SELECT * FROM semantic_metrics ORDER BY name")
        return [self._row_to_metric(r) for r in rows]

    def update_metric(self, metric_id: str, **kwargs: Any) -> dict[str, Any]:
        metric = self.get_metric(metric_id)
        now = _now_iso()
        updates: list[str] = []
        params: list[Any] = []
        for field, col in [
            ("display_name", "display_name"),
            ("description", "description"),
            ("definition_sql", "definition_sql"),
            ("entity_id", "entity_id"),
        ]:
            if field in kwargs:
                updates.append(f"{col} = ?")
                params.append(kwargs[field])
        if "dimensions" in kwargs:
            updates.append("dimensions_json = ?")
            params.append(json.dumps(kwargs["dimensions"]))
        if "grain" in kwargs:
            updates.append("grain = ?")
            params.append(kwargs["grain"])
        if "measure_type" in kwargs:
            updates.append("measure_type = ?")
            params.append(kwargs["measure_type"])
        if "allowed_dimensions" in kwargs:
            updates.append("allowed_dimensions_json = ?")
            params.append(json.dumps(kwargs["allowed_dimensions"]))
        if "lineage" in kwargs:
            updates.append("lineage_json = ?")
            params.append(json.dumps(kwargs["lineage"]))
        if "quality_expectations" in kwargs:
            updates.append("quality_expectations_json = ?")
            params.append(json.dumps(kwargs["quality_expectations"]))
        if "properties" in kwargs:
            updates.append("properties_json = ?")
            params.append(json.dumps(kwargs["properties"]))
        if "desired_direction" in kwargs:
            updates.append("desired_direction = ?")
            params.append(kwargs["desired_direction"])
        if not updates:
            return metric
        updates.append("updated_at = ?")
        params.append(now)
        params.append(metric_id)
        self.metadata.execute(
            f"UPDATE semantic_metrics SET {', '.join(updates)} WHERE metric_id = ?",
            params,
        )
        metric = self.get_metric(metric_id)
        self._sync_metric_contract(metric)
        return metric

    def publish_metric(self, metric_id: str) -> dict[str, Any]:
        self.get_metric(metric_id)
        now = _now_iso()
        self.metadata.execute(
            "UPDATE semantic_metrics SET status = 'published', revision = revision + 1, updated_at = ? WHERE metric_id = ?",
            [now, metric_id],
        )
        metric = self.get_metric(metric_id)
        self._sync_metric_contract(metric)
        return metric

    # ── Mapping CRUD ─────────────────────────────────────────────

    def create_mapping(
        self,
        semantic_type: str,
        semantic_id: str,
        object_id: str,
        mapping_type: str,
        mapping_json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        mapping_id = f"map_{uuid4().hex[:12]}"
        now = _now_iso()
        self.metadata.execute(
            """
            INSERT INTO legacy_semantic_mappings
                (mapping_id, semantic_type, semantic_id, object_id, mapping_type, mapping_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
        result = self._get_mapping(mapping_id)
        assert result is not None
        return result

    def delete_mapping(self, mapping_id: str) -> None:
        existing = self._get_mapping(mapping_id)
        if existing is None:
            raise KeyError(f"Unknown mapping: {mapping_id}")
        self.metadata.execute(
            "DELETE FROM legacy_semantic_mappings WHERE mapping_id = ?", [mapping_id]
        )

    def list_mappings(
        self,
        semantic_type: str | None = None,
        semantic_id: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM legacy_semantic_mappings WHERE 1=1"
        params: list[Any] = []
        if semantic_type:
            sql += " AND semantic_type = ?"
            params.append(semantic_type)
        if semantic_id:
            sql += " AND semantic_id = ?"
            params.append(semantic_id)
        sql += " ORDER BY created_at"
        rows = self.metadata.query_rows(sql, params)
        return [self._row_to_mapping(r) for r in rows]

    def _get_mapping(self, mapping_id: str) -> dict[str, Any] | None:
        row = self.metadata.query_one(
            "SELECT * FROM legacy_semantic_mappings WHERE mapping_id = ?", [mapping_id]
        )
        if row is None:
            return None
        return self._row_to_mapping(row)

    # ── Typed entity contracts ──────────────────────────────────

    def create_typed_entity(self, payload: TypedEntityCreateRequest) -> dict[str, Any]:
        entity_contract_id = f"entc_{uuid4().hex[:12]}"
        now = _now_iso()
        hierarchy = payload.interface_contract.hierarchy
        self.metadata.execute(
            """
            INSERT INTO semantic_entity_contracts (
                entity_contract_id, entity_ref, display_name, description,
                entity_contract_version, uniqueness_scope, id_stability,
                nullable_key_policy, parent_entity_ref, cardinality_to_parent,
                ownership_semantics, primary_time_ref, status, revision,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', 1, ?, ?)
            """,
            [
                entity_contract_id,
                payload.header.entity_ref,
                payload.header.display_name or payload.header.entity_ref.removeprefix("entity."),
                payload.header.description or "",
                payload.header.entity_contract_version,
                payload.interface_contract.identity.uniqueness_scope,
                payload.interface_contract.identity.id_stability,
                payload.interface_contract.identity.nullable_key_policy or "reject",
                hierarchy.parent_entity_ref if hierarchy else None,
                hierarchy.cardinality_to_parent if hierarchy else None,
                hierarchy.ownership_semantics if hierarchy else None,
                payload.interface_contract.primary_time_ref,
                now,
                now,
            ],
        )
        self._replace_entity_key_refs(
            entity_contract_id,
            payload.interface_contract.identity.key_refs,
        )
        self._replace_entity_stable_descriptors(
            entity_contract_id,
            [
                descriptor.model_dump(mode="json")
                for descriptor in (payload.interface_contract.stable_descriptors or [])
            ],
        )
        return self.get_typed_entity(entity_contract_id)

    def get_typed_entity(self, entity_contract_id: str) -> dict[str, Any]:
        row = self.metadata.query_one(
            "SELECT * FROM semantic_entity_contracts WHERE entity_contract_id = ?",
            [entity_contract_id],
        )
        if row is None:
            raise KeyError(f"Unknown typed entity: {entity_contract_id}")
        return self._row_to_typed_entity(row)

    def list_typed_entities(self, status: str | None = None) -> dict[str, Any]:
        if status is None:
            rows = self.metadata.query_rows(
                "SELECT * FROM semantic_entity_contracts ORDER BY entity_ref"
            )
        else:
            rows = self.metadata.query_rows(
                "SELECT * FROM semantic_entity_contracts WHERE status = ? ORDER BY entity_ref",
                [status],
            )
        items = [self._row_to_typed_entity(row) for row in rows]
        return {"items": items, "total": len(items)}

    def update_typed_entity(
        self, entity_contract_id: str, payload: TypedEntityUpdateRequest
    ) -> dict[str, Any]:
        self.get_typed_entity(entity_contract_id)
        updates: list[str] = []
        params: list[Any] = []
        if payload.display_name is not None:
            updates.append("display_name = ?")
            params.append(payload.display_name)
        if payload.description is not None:
            updates.append("description = ?")
            params.append(payload.description)
        if payload.interface_contract is not None:
            hierarchy = payload.interface_contract.hierarchy
            updates.extend(
                [
                    "uniqueness_scope = ?",
                    "id_stability = ?",
                    "nullable_key_policy = ?",
                    "parent_entity_ref = ?",
                    "cardinality_to_parent = ?",
                    "ownership_semantics = ?",
                    "primary_time_ref = ?",
                ]
            )
            params.extend(
                [
                    payload.interface_contract.identity.uniqueness_scope,
                    payload.interface_contract.identity.id_stability,
                    payload.interface_contract.identity.nullable_key_policy or "reject",
                    hierarchy.parent_entity_ref if hierarchy else None,
                    hierarchy.cardinality_to_parent if hierarchy else None,
                    hierarchy.ownership_semantics if hierarchy else None,
                    payload.interface_contract.primary_time_ref,
                ]
            )
            self._replace_entity_key_refs(
                entity_contract_id,
                payload.interface_contract.identity.key_refs,
            )
            self._replace_entity_stable_descriptors(
                entity_contract_id,
                [
                    descriptor.model_dump(mode="json")
                    for descriptor in (payload.interface_contract.stable_descriptors or [])
                ],
            )
        if not updates:
            return self.get_typed_entity(entity_contract_id)
        updates.append("updated_at = ?")
        params.append(_now_iso())
        params.append(entity_contract_id)
        self.metadata.execute(
            f"UPDATE semantic_entity_contracts SET {', '.join(updates)} WHERE entity_contract_id = ?",
            params,
        )
        return self.get_typed_entity(entity_contract_id)

    def publish_typed_entity(self, entity_contract_id: str) -> dict[str, Any]:
        self.get_typed_entity(entity_contract_id)
        self.metadata.execute(
            """
            UPDATE semantic_entity_contracts
            SET status = 'published', revision = revision + 1, updated_at = ?
            WHERE entity_contract_id = ?
            """,
            [_now_iso(), entity_contract_id],
        )
        return self.get_typed_entity(entity_contract_id)

    # ── Typed metric contracts ──────────────────────────────────

    def create_typed_metric(self, payload: TypedMetricCreateRequest) -> dict[str, Any]:
        metric_contract_id = f"metc_{uuid4().hex[:12]}"
        now = _now_iso()
        self.metadata.execute(
            """
            INSERT INTO semantic_metric_contracts (
                metric_contract_id, metric_ref, display_name, description, metric_family,
                population_subject_ref, observed_entity_ref, observation_grain_ref,
                sample_kind, value_semantics, aggregation_scope, primary_time_ref,
                additivity, metric_contract_version, family_payload_json, status,
                revision, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', 1, ?, ?)
            """,
            [
                metric_contract_id,
                payload.header.metric_ref,
                payload.header.display_name or payload.header.metric_ref.removeprefix("metric."),
                payload.header.description or "",
                payload.header.metric_family,
                payload.header.population_subject_ref,
                payload.header.observed_entity_ref,
                payload.header.observation_grain_ref,
                payload.header.sample_kind,
                payload.header.value_semantics,
                payload.header.aggregation_scope,
                payload.header.primary_time_ref,
                payload.header.additivity,
                payload.header.metric_contract_version,
                json.dumps(payload.payload.model_dump(mode="json")),
                now,
                now,
            ],
        )
        return self.get_typed_metric(metric_contract_id)

    def get_typed_metric(self, metric_contract_id: str) -> dict[str, Any]:
        row = self.metadata.query_one(
            "SELECT * FROM semantic_metric_contracts WHERE metric_contract_id = ?",
            [metric_contract_id],
        )
        if row is None:
            raise KeyError(f"Unknown typed metric: {metric_contract_id}")
        return self._row_to_typed_metric(row)

    def list_typed_metrics(self, status: str | None = None) -> dict[str, Any]:
        if status is None:
            rows = self.metadata.query_rows(
                "SELECT * FROM semantic_metric_contracts ORDER BY metric_ref"
            )
        else:
            rows = self.metadata.query_rows(
                "SELECT * FROM semantic_metric_contracts WHERE status = ? ORDER BY metric_ref",
                [status],
            )
        items = [self._row_to_typed_metric(row) for row in rows]
        return {"items": items, "total": len(items)}

    def update_typed_metric(
        self, metric_contract_id: str, payload: TypedMetricUpdateRequest
    ) -> dict[str, Any]:
        current = self.get_typed_metric(metric_contract_id)
        updates: list[str] = []
        params: list[Any] = []
        if payload.display_name is not None:
            updates.append("display_name = ?")
            params.append(payload.display_name)
        if payload.description is not None:
            updates.append("description = ?")
            params.append(payload.description)
        if payload.payload is not None:
            current_family = current["header"]["metric_family"]
            if payload.payload.metric_family != current_family:
                raise ValueError(
                    f"metric_family is immutable; expected '{current_family}', got '{payload.payload.metric_family}'"
                )
            updates.append("family_payload_json = ?")
            params.append(json.dumps(payload.payload.model_dump(mode="json")))
        if not updates:
            return current
        updates.append("updated_at = ?")
        params.append(_now_iso())
        params.append(metric_contract_id)
        self.metadata.execute(
            f"UPDATE semantic_metric_contracts SET {', '.join(updates)} WHERE metric_contract_id = ?",
            params,
        )
        return self.get_typed_metric(metric_contract_id)

    def publish_typed_metric(self, metric_contract_id: str) -> dict[str, Any]:
        self.get_typed_metric(metric_contract_id)
        self.metadata.execute(
            """
            UPDATE semantic_metric_contracts
            SET status = 'published', revision = revision + 1, updated_at = ?
            WHERE metric_contract_id = ?
            """,
            [_now_iso(), metric_contract_id],
        )
        return self.get_typed_metric(metric_contract_id)

    # ── Typed bindings ──────────────────────────────────────────

    def create_typed_binding(self, payload: TypedBindingCreateRequest) -> dict[str, Any]:
        self._validate_binding_target_ref(
            payload.header.binding_scope,
            payload.header.bound_object_ref,
        )
        binding_id = f"bind_{uuid4().hex[:24]}"
        now = _now_iso()
        self.metadata.execute(
            """
            INSERT INTO typed_bindings (
                binding_id, binding_ref, binding_scope, bound_object_ref,
                binding_contract_version, display_name, description,
                status, revision, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', 1, ?, ?)
            """,
            [
                binding_id,
                payload.header.binding_ref,
                payload.header.binding_scope,
                payload.header.bound_object_ref,
                payload.header.binding_contract_version,
                payload.header.display_name,
                payload.header.description,
                now,
                now,
            ],
        )
        self._replace_binding_contract(
            binding_id,
            payload.interface_contract.model_dump(mode="json"),
        )
        return self.get_typed_binding(binding_id)

    def get_typed_binding(self, binding_id: str) -> dict[str, Any]:
        row = self.metadata.query_one(
            "SELECT * FROM typed_bindings WHERE binding_id = ?",
            [binding_id],
        )
        if row is None:
            raise KeyError(f"Unknown typed binding: {binding_id}")
        return self._row_to_typed_binding(row)

    def list_typed_bindings(self, status: str | None = None) -> dict[str, Any]:
        if status is None:
            rows = self.metadata.query_rows("SELECT * FROM typed_bindings ORDER BY binding_ref")
        else:
            rows = self.metadata.query_rows(
                "SELECT * FROM typed_bindings WHERE status = ? ORDER BY binding_ref",
                [status],
            )
        items = [self._row_to_typed_binding(row) for row in rows]
        return {"items": items, "total": len(items)}

    def update_typed_binding(
        self, binding_id: str, payload: TypedBindingUpdateRequest
    ) -> dict[str, Any]:
        self.get_typed_binding(binding_id)
        updates: list[str] = []
        params: list[Any] = []
        if payload.display_name is not None:
            updates.append("display_name = ?")
            params.append(payload.display_name)
        if payload.description is not None:
            updates.append("description = ?")
            params.append(payload.description)
        if payload.interface_contract is not None:
            self._replace_binding_contract(
                binding_id,
                payload.interface_contract.model_dump(mode="json"),
            )
        if not updates and payload.interface_contract is None:
            return self.get_typed_binding(binding_id)
        updates.append("updated_at = ?")
        params.append(_now_iso())
        params.append(binding_id)
        self.metadata.execute(
            f"UPDATE typed_bindings SET {', '.join(updates)} WHERE binding_id = ?",
            params,
        )
        return self.get_typed_binding(binding_id)

    def publish_typed_binding(self, binding_id: str) -> dict[str, Any]:
        self.get_typed_binding(binding_id)
        self.metadata.execute(
            """
            UPDATE typed_bindings
            SET status = 'published', revision = revision + 1, updated_at = ?
            WHERE binding_id = ?
            """,
            [_now_iso(), binding_id],
        )
        return self.get_typed_binding(binding_id)

    # ── Compiler compatibility profiles ────────────────────────

    def create_compatibility_profile(
        self, payload: CompatibilityProfileCreateRequest
    ) -> dict[str, Any]:
        self._validate_profile_subject_ref(payload.subject_kind, payload.subject_ref)
        profile_id = f"cprof_{uuid4().hex[:24]}"
        now = _now_iso()
        self.metadata.execute(
            """
            INSERT INTO compiler_compatibility_profiles (
                profile_id, profile_ref, profile_kind, schema_version, subject_kind,
                subject_ref, requirement_json, capability_json, status, revision,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', 1, ?, ?)
            """,
            [
                profile_id,
                payload.profile_ref,
                payload.profile_kind,
                payload.schema_version,
                payload.subject_kind,
                payload.subject_ref,
                json.dumps(
                    payload.requirement.model_dump(mode="json") if payload.requirement else {}
                ),
                json.dumps(
                    payload.capability.model_dump(mode="json") if payload.capability else {}
                ),
                now,
                now,
            ],
        )
        return self.get_compatibility_profile(profile_id)

    def get_compatibility_profile(self, profile_id: str) -> dict[str, Any]:
        row = self.metadata.query_one(
            "SELECT * FROM compiler_compatibility_profiles WHERE profile_id = ?",
            [profile_id],
        )
        if row is None:
            raise KeyError(f"Unknown compatibility profile: {profile_id}")
        return self._row_to_compatibility_profile(row)

    def list_compatibility_profiles(self, status: str | None = None) -> dict[str, Any]:
        if status is None:
            rows = self.metadata.query_rows(
                "SELECT * FROM compiler_compatibility_profiles ORDER BY profile_ref"
            )
        else:
            rows = self.metadata.query_rows(
                "SELECT * FROM compiler_compatibility_profiles WHERE status = ? ORDER BY profile_ref",
                [status],
            )
        items = [self._row_to_compatibility_profile(row) for row in rows]
        return {"items": items, "total": len(items)}

    def update_compatibility_profile(
        self, profile_id: str, payload: CompatibilityProfileUpdateRequest
    ) -> dict[str, Any]:
        current = self.get_compatibility_profile(profile_id)
        updates: list[str] = []
        params: list[Any] = []
        if payload.requirement is not None:
            if current["profile_kind"] != "requirement":
                raise ValueError("Only requirement profiles accept requirement updates")
            updates.append("requirement_json = ?")
            params.append(json.dumps(payload.requirement.model_dump(mode="json")))
        if payload.capability is not None:
            if current["profile_kind"] != "capability":
                raise ValueError("Only capability profiles accept capability updates")
            updates.append("capability_json = ?")
            params.append(json.dumps(payload.capability.model_dump(mode="json")))
        if not updates:
            return current
        updates.append("updated_at = ?")
        params.append(_now_iso())
        params.append(profile_id)
        self.metadata.execute(
            f"UPDATE compiler_compatibility_profiles SET {', '.join(updates)} WHERE profile_id = ?",
            params,
        )
        return self.get_compatibility_profile(profile_id)

    def publish_compatibility_profile(self, profile_id: str) -> dict[str, Any]:
        self.get_compatibility_profile(profile_id)
        self.metadata.execute(
            """
            UPDATE compiler_compatibility_profiles
            SET status = 'published', revision = revision + 1, updated_at = ?
            WHERE profile_id = ?
            """,
            [_now_iso(), profile_id],
        )
        return self.get_compatibility_profile(profile_id)

    # ── Row converters ───────────────────────────────────────────

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
