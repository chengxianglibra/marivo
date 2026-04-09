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

from .errors import (
    SemanticCompatibilityError,
    SemanticNotFoundError,
    SemanticStateError,
    SemanticValidationError,
)


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

    def _validation_error(
        self,
        message: str,
        *,
        code: str = "semantic_validation_error",
        category: str = "validation",
    ) -> SemanticValidationError:
        return SemanticValidationError(message, code=code, category=category)

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

    def _require_draft_status(self, status: str, object_label: str, object_id: str) -> None:
        if status != "draft":
            raise self._state_error(
                f"{object_label} '{object_id}' is not in draft status (status={status}).",
                code="publish_state_error",
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
        self._require_draft_status(status, object_label, object_id)
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

    def _binding_contract_target_exists(
        self,
        field_bindings: list[dict[str, Any]],
        *,
        target_kind: str,
        target_key: str | None = None,
        semantic_ref: str | None = None,
    ) -> bool:
        for field_binding in field_bindings:
            target = field_binding["target"]
            if target["target_kind"] != target_kind:
                continue
            if target_key is not None and target.get("target_key") != target_key:
                continue
            if semantic_ref is not None and field_binding.get("semantic_ref") != semantic_ref:
                continue
            return True
        return False

    def _resolve_binding_source_object(
        self,
        carrier: dict[str, Any],
        *,
        require_resolution: bool,
    ) -> dict[str, Any] | None:
        source_object_ref = carrier.get("source_object_ref")
        carrier_locator = carrier["carrier_locator"]
        carrier_kind = carrier["carrier_kind"]

        if source_object_ref is not None:
            row = self.metadata.query_one(
                """
                SELECT object_id, object_type, fqn
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
            if row["fqn"] != carrier_locator:
                raise self._validation_error(
                    "carrier_locator does not match resolved source object FQN "
                    f"for carrier '{carrier['binding_key']}': expected '{row['fqn']}', "
                    f"got '{carrier_locator}'"
                )
            return dict(row)

        if not require_resolution:
            return None

        rows = self.metadata.query_rows(
            """
            SELECT object_id, object_type, fqn
            FROM source_objects
            WHERE fqn = ? AND object_type = ?
            ORDER BY object_id
            """,
            [carrier_locator, carrier_kind],
        )
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
                    f"metric_input semantic_ref must use 'metric_input.' prefix, got: {semantic_ref}"
                )
            if not target_key:
                raise self._validation_error("metric_input target_key must not be empty")
            return

        raise self._validation_error(f"Unsupported target_kind: {target_kind}")

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

    def _validate_binding_scope_compatibility(
        self,
        *,
        binding_scope: str,
        bound_object: dict[str, Any],
        field_bindings: list[dict[str, Any]],
        carrier_bindings: list[dict[str, Any]],
        join_relations: list[dict[str, Any]],
        require_published_refs: bool,
    ) -> None:
        target_kinds = {field_binding["target"]["target_kind"] for field_binding in field_bindings}

        if binding_scope == "entity":
            entity_ref = bound_object["header"]["entity_ref"]
            interface_contract = bound_object["interface_contract"]
            allowed_target_kinds = {"identity_key", "primary_time", "stable_descriptor"}
            unexpected = target_kinds - allowed_target_kinds
            if unexpected:
                raise self._validation_error(
                    f"Entity binding cannot use target kinds: {sorted(unexpected)}"
                )
            for key_ref in interface_contract["identity"]["key_refs"]:
                if not self._binding_contract_target_exists(
                    field_bindings,
                    target_kind="identity_key",
                    target_key=key_ref,
                    semantic_ref=key_ref,
                ):
                    raise self._validation_error(
                        f"Entity binding must map identity key '{key_ref}' for {entity_ref}"
                    )
            primary_time_ref = interface_contract.get("primary_time_ref")
            if primary_time_ref is not None and not self._binding_contract_target_exists(
                field_bindings,
                target_kind="primary_time",
                semantic_ref=primary_time_ref,
            ):
                raise self._validation_error(
                    f"Entity binding must map primary_time_ref '{primary_time_ref}' for {entity_ref}"
                )
            for descriptor in interface_contract.get("stable_descriptors") or []:
                dimension_ref = descriptor["dimension_ref"]
                if not self._binding_contract_target_exists(
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
                    f"Process binding cannot use target kinds: {sorted(unexpected)}"
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
                field_binding["semantic_ref"] == anchor_time_ref
                and field_binding["target"]["target_kind"]
                in {"primary_time", "analysis_window_anchor"}
                for field_binding in field_bindings
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
                    field_binding["target"]["target_kind"] == "analysis_window_anchor"
                    for field_binding in field_bindings
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
            allowed_target_kinds = {"population_subject", "primary_time", "metric_input"}
            unexpected = target_kinds - allowed_target_kinds
            if unexpected:
                raise self._validation_error(
                    f"Metric binding cannot use target kinds: {sorted(unexpected)}"
                )
            if header.get("population_subject_ref") is not None and not any(
                field_binding["target"]["target_kind"] == "population_subject"
                for field_binding in field_bindings
            ):
                raise self._validation_error(
                    "Metric binding must map population_subject when the metric declares "
                    "population_subject_ref"
                )
            primary_time_ref = header.get("primary_time_ref")
            if primary_time_ref is not None and not self._binding_contract_target_exists(
                field_bindings,
                target_kind="primary_time",
                semantic_ref=primary_time_ref,
            ):
                raise self._validation_error(
                    f"Metric binding must map primary_time_ref '{primary_time_ref}'"
                )
            metric_input_keys = {
                field_binding["target"]["target_key"]
                for field_binding in field_bindings
                if field_binding["target"]["target_kind"] == "metric_input"
            }
            if not metric_input_keys:
                raise self._validation_error(
                    "Metric binding must map at least one metric_input target"
                )
            if header["metric_family"] == "rate_metric" and not {
                "numerator",
                "denominator",
            }.issubset(metric_input_keys):
                raise self._validation_error(
                    "rate_metric binding must map both 'numerator' and 'denominator' metric_input targets"
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
        join_relations = interface_contract.get("join_relations") or []
        consumption_policies = interface_contract.get("consumption_policies") or []

        if not carrier_bindings:
            raise self._validation_error("Binding interface_contract must include carrier_bindings")
        if not field_bindings:
            raise self._validation_error("Binding interface_contract must include field_bindings")

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
            field_bindings=field_bindings,
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
            "subject_revision": row["subject_revision"],
            "requirement": requirement or None,
            "capability": capability or None,
            "status": row["status"],
            "revision": row["revision"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
