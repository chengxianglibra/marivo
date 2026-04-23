from __future__ import annotations

import json
from typing import Any, Literal
from uuid import uuid4

from pydantic import TypeAdapter

from app.api.models.dimension import DimensionCreateRequest, DimensionUpdateRequest
from app.api.models.entity import TypedEntityCreateRequest, TypedEntityUpdateRequest
from app.api.models.enum_set import (
    EnumSetCreateRequest,
    EnumSetUpdateRequest,
    _raw_value_matches_enum_value_type,
)
from app.api.models.metric import (
    MetricPayload,
    TypedMetricCreateRequest,
    TypedMetricUpdateRequest,
    _collect_aggregation_methods,
)
from app.api.models.predicate import PredicateCreateRequest, PredicateUpdateRequest
from app.api.models.process_object import ProcessObjectCreateRequest, ProcessObjectUpdateRequest
from app.api.models.time import TimeCreateRequest, TimeUpdateRequest

from .common import SemanticServiceSupport, now_iso


class TypedObjectService(SemanticServiceSupport):
    def _apply_contract_update(
        self,
        *,
        table_name: str,
        id_column: str,
        object_id: str,
        updates: list[str],
        params: list[Any],
    ) -> None:
        updates.extend(["revision = revision + 1", "updated_at = ?"])
        params.append(now_iso())
        params.append(object_id)
        self.metadata.execute(
            f"UPDATE {table_name} SET {', '.join(updates)} WHERE {id_column} = ?",
            params,
        )

    def create_typed_entity(self, payload: TypedEntityCreateRequest) -> dict[str, Any]:
        entity_contract_id = f"entc_{uuid4().hex[:12]}"
        created_at = now_iso()
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
                created_at,
                created_at,
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

    def read_typed_entity(self, entity_identifier: str) -> dict[str, Any]:
        row = self.metadata.query_one(
            "SELECT * FROM semantic_entity_contracts WHERE entity_contract_id = ?",
            [entity_identifier],
        )
        if row is None:
            row = self.metadata.query_one(
                "SELECT * FROM semantic_entity_contracts WHERE entity_ref = ?",
                [entity_identifier],
            )
        if row is None:
            raise self._not_found(f"Unknown typed entity: {entity_identifier}")
        return self._row_to_typed_entity(row)

    def get_typed_entity(self, entity_contract_id: str) -> dict[str, Any]:
        row = self.metadata.query_one(
            "SELECT * FROM semantic_entity_contracts WHERE entity_contract_id = ?",
            [entity_contract_id],
        )
        if row is None:
            raise self._not_found(f"Unknown typed entity: {entity_contract_id}")
        return self._row_to_typed_entity(row)

    def list_typed_entities(
        self,
        status: str | None = None,
        lifecycle_status: str | None = None,
        readiness_status: str | None = None,
        detail: bool = False,
    ) -> dict[str, Any]:
        mode: Literal["list", "detail"] = "detail" if detail else "list"
        status = self._resolve_semantic_filters(status=status, lifecycle_status=lifecycle_status)
        if status is None:
            rows = self.metadata.query_rows(
                "SELECT * FROM semantic_entity_contracts ORDER BY entity_ref"
            )
        else:
            rows = self.metadata.query_rows(
                "SELECT * FROM semantic_entity_contracts WHERE status = ? ORDER BY entity_ref",
                [status],
            )
        items = [
            item
            for row in rows
            if self._matches_readiness_filter(
                item := self._row_to_typed_entity(row, mode=mode),
                readiness_status=readiness_status,
            )
        ]
        return {"items": items, "total": len(items)}

    def update_typed_entity(
        self, entity_contract_id: str, payload: TypedEntityUpdateRequest
    ) -> dict[str, Any]:
        current = self.get_typed_entity(entity_contract_id)
        self._require_lifecycle_action_status(
            action="activate",
            status=current["status"],
            object_label="Typed entity",
            object_id=entity_contract_id,
        )
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
            return current
        self._apply_contract_update(
            table_name="semantic_entity_contracts",
            id_column="entity_contract_id",
            object_id=entity_contract_id,
            updates=updates,
            params=params,
        )
        return self.get_typed_entity(entity_contract_id)

    def validate_typed_entity(self, entity_contract_id: str) -> dict[str, Any]:
        current = self.get_typed_entity(entity_contract_id)
        self._validate_record(
            object_id=entity_contract_id,
            object_label="Typed entity",
            status=current["status"],
            reference_validator=lambda: self._validate_published_entity_contract_refs(
                current["interface_contract"]
            ),
        )
        return self.get_typed_entity(entity_contract_id)

    def activate_typed_entity(self, entity_contract_id: str) -> dict[str, Any]:
        current = self.get_typed_entity(entity_contract_id)
        self._activate_record(
            table_name="semantic_entity_contracts",
            id_column="entity_contract_id",
            object_id=entity_contract_id,
            object_label="Typed entity",
            status=current["status"],
            reference_validator=lambda: self._validate_published_entity_contract_refs(
                current["interface_contract"]
            ),
        )
        return self.get_typed_entity(entity_contract_id)

    def deprecate_typed_entity(self, entity_contract_id: str) -> dict[str, Any]:
        current = self.get_typed_entity(entity_contract_id)
        self._deprecate_record(
            table_name="semantic_entity_contracts",
            id_column="entity_contract_id",
            object_id=entity_contract_id,
            object_label="Typed entity",
            status=current["status"],
        )
        return self.get_typed_entity(entity_contract_id)

    def publish_typed_entity(self, entity_contract_id: str) -> dict[str, Any]:
        return self.activate_typed_entity(entity_contract_id)

    def create_typed_metric(self, payload: TypedMetricCreateRequest) -> dict[str, Any]:
        metric_contract_id = f"metc_{uuid4().hex[:12]}"
        created_at = now_iso()
        self.metadata.execute(
            """
            INSERT INTO semantic_metric_contracts (
                metric_contract_id, metric_ref, display_name, description, metric_family,
                population_subject_ref, observed_entity_ref, observation_grain_ref,
                sample_kind, value_semantics, aggregation_scope, primary_time_ref,
                additivity_constraints_json, default_predicate_refs_json,
                metric_contract_version, family_payload_json, status,
                revision, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', 1, ?, ?)
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
                json.dumps(payload.header.additivity_constraints.model_dump(mode="json")),
                json.dumps(payload.header.default_predicate_refs or []),
                payload.header.metric_contract_version,
                json.dumps(payload.payload.model_dump(mode="json")),
                created_at,
                created_at,
            ],
        )
        return self.get_typed_metric(metric_contract_id)

    def read_typed_metric(self, metric_identifier: str) -> dict[str, Any]:
        row = self.metadata.query_one(
            "SELECT * FROM semantic_metric_contracts WHERE metric_contract_id = ?",
            [metric_identifier],
        )
        if row is None:
            row = self.metadata.query_one(
                "SELECT * FROM semantic_metric_contracts WHERE metric_ref = ?",
                [metric_identifier],
            )
        if row is None:
            raise self._not_found(f"Unknown typed metric: {metric_identifier}")
        return self._row_to_typed_metric(row)

    def get_typed_metric(self, metric_contract_id: str) -> dict[str, Any]:
        row = self.metadata.query_one(
            "SELECT * FROM semantic_metric_contracts WHERE metric_contract_id = ?",
            [metric_contract_id],
        )
        if row is None:
            raise self._not_found(f"Unknown typed metric: {metric_contract_id}")
        return self._row_to_typed_metric(row)

    def list_typed_metrics(
        self,
        status: str | None = None,
        lifecycle_status: str | None = None,
        readiness_status: str | None = None,
        detail: bool = False,
    ) -> dict[str, Any]:
        mode: Literal["list", "detail"] = "detail" if detail else "list"
        status = self._resolve_semantic_filters(status=status, lifecycle_status=lifecycle_status)
        if status is None:
            rows = self.metadata.query_rows(
                "SELECT * FROM semantic_metric_contracts ORDER BY metric_ref"
            )
        else:
            rows = self.metadata.query_rows(
                "SELECT * FROM semantic_metric_contracts WHERE status = ? ORDER BY metric_ref",
                [status],
            )
        list_context = self._list_context()
        filtered_rows = rows
        if readiness_status is not None:
            filtered_rows = []
            for row in rows:
                snapshot = list_context.load_dependency_snapshot(str(row["metric_ref"]))
                if snapshot is None:
                    continue
                if list_context.readiness_for(snapshot).get("readiness_status") == readiness_status:
                    filtered_rows.append(row)
        items = [
            self._row_to_typed_metric(row, mode=mode, list_context=list_context)
            for row in filtered_rows
        ]
        return {"items": items, "total": len(items)}

    def update_typed_metric(
        self, metric_contract_id: str, payload: TypedMetricUpdateRequest
    ) -> dict[str, Any]:
        current = self.get_typed_metric(metric_contract_id)
        self._require_lifecycle_action_status(
            action="activate",
            status=current["status"],
            object_label="Typed metric",
            object_id=metric_contract_id,
        )
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
                raise self._validation_error(
                    f"metric_family is immutable; expected '{current_family}', got '{payload.payload.metric_family}'"
                )
            updates.append("family_payload_json = ?")
            params.append(json.dumps(payload.payload.model_dump(mode="json")))
        if payload.additivity_constraints is not None:
            if current["status"] != "draft":
                raise self._validation_error(
                    "additivity_constraints can only be updated in draft status"
                )
            updates.append("additivity_constraints_json = ?")
            params.append(json.dumps(payload.additivity_constraints.model_dump(mode="json")))
        if payload.default_predicate_refs is not None:
            updates.append("default_predicate_refs_json = ?")
            params.append(json.dumps(payload.default_predicate_refs))
        if not updates:
            return current
        # Cross-field validation: count_distinct + dimension_policy='all' is invalid
        effective_constraints = (
            payload.additivity_constraints.model_dump(mode="json")
            if payload.additivity_constraints is not None
            else current["header"].get("additivity_constraints", {})
        )
        effective_payload = payload.payload
        if effective_payload is None:
            raw_payload = current.get("payload")
            if isinstance(raw_payload, dict):
                try:
                    effective_payload = TypeAdapter(MetricPayload).validate_python(raw_payload)
                except Exception:
                    effective_payload = None
        if (
            effective_constraints.get("dimension_policy") == "all"
            and effective_payload is not None
            and "count_distinct" in _collect_aggregation_methods(effective_payload)
        ):
            raise self._validation_error(
                "Metrics with count_distinct aggregation must not use "
                "dimension_policy='all'; use 'subset' or 'none' instead."
            )
        self._apply_contract_update(
            table_name="semantic_metric_contracts",
            id_column="metric_contract_id",
            object_id=metric_contract_id,
            updates=updates,
            params=params,
        )
        return self.get_typed_metric(metric_contract_id)

    def validate_typed_metric(self, metric_contract_id: str) -> dict[str, Any]:
        current = self.get_typed_metric(metric_contract_id)

        def _validate_refs() -> None:
            self._validate_published_metric_header_refs(current["header"])
            self._validate_published_metric_predicate_refs(current["header"], current["payload"])

        self._validate_record(
            object_id=metric_contract_id,
            object_label="Typed metric",
            status=current["status"],
            reference_validator=_validate_refs,
        )
        return self.get_typed_metric(metric_contract_id)

    def activate_typed_metric(self, metric_contract_id: str) -> dict[str, Any]:
        current = self.get_typed_metric(metric_contract_id)

        def _validate_refs() -> None:
            self._validate_published_metric_header_refs(current["header"])
            self._validate_published_metric_predicate_refs(current["header"], current["payload"])

        self._activate_record(
            table_name="semantic_metric_contracts",
            id_column="metric_contract_id",
            object_id=metric_contract_id,
            object_label="Typed metric",
            status=current["status"],
            reference_validator=_validate_refs,
        )
        return self.get_typed_metric(metric_contract_id)

    def deprecate_typed_metric(self, metric_contract_id: str) -> dict[str, Any]:
        current = self.get_typed_metric(metric_contract_id)
        self._deprecate_record(
            table_name="semantic_metric_contracts",
            id_column="metric_contract_id",
            object_id=metric_contract_id,
            object_label="Typed metric",
            status=current["status"],
        )
        return self.get_typed_metric(metric_contract_id)

    def publish_typed_metric(self, metric_contract_id: str) -> dict[str, Any]:
        return self.activate_typed_metric(metric_contract_id)

    def create_process_object(self, payload: ProcessObjectCreateRequest) -> dict[str, Any]:
        interface_contract = payload.interface_contract.model_dump(mode="json")
        payload_json = payload.payload.model_dump(mode="json")
        self._validate_process_refs(interface_contract, payload_json)
        process_contract_id = f"proc_{uuid4().hex[:24]}"
        created_at = now_iso()
        self.metadata.execute(
            """
            INSERT INTO semantic_process_objects (
                process_contract_id, process_ref, display_name, description, process_type,
                process_contract_version, contract_mode, context_kind, population_subject_ref,
                membership_cardinality, entity_ref, emitted_grain_ref, subject_cardinality,
                anchor_time_ref, process_payload_json, status, revision, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', 1, ?, ?)
            """,
            [
                process_contract_id,
                payload.header.process_ref,
                payload.header.display_name or payload.header.process_ref.removeprefix("process."),
                payload.header.description or "",
                payload.header.process_type,
                payload.header.process_contract_version,
                interface_contract["contract_mode"],
                interface_contract.get("context_kind"),
                interface_contract["population_subject_ref"],
                interface_contract.get("membership_cardinality"),
                interface_contract.get("entity_ref"),
                interface_contract.get("emitted_grain_ref"),
                interface_contract.get("subject_cardinality"),
                interface_contract.get("anchor_time_ref"),
                json.dumps(payload_json),
                created_at,
                created_at,
            ],
        )
        self._replace_process_exported_dimension_refs(
            process_contract_id,
            interface_contract.get("exported_dimension_refs"),
        )
        return self.get_process_object(process_contract_id)

    def read_process_object(self, process_identifier: str) -> dict[str, Any]:
        row = self.metadata.query_one(
            "SELECT * FROM semantic_process_objects WHERE process_contract_id = ?",
            [process_identifier],
        )
        if row is None:
            row = self.metadata.query_one(
                "SELECT * FROM semantic_process_objects WHERE process_ref = ?",
                [process_identifier],
            )
        if row is None:
            raise self._not_found(f"Unknown process object: {process_identifier}")
        return self._row_to_process_object(row)

    def get_process_object(self, process_contract_id: str) -> dict[str, Any]:
        row = self.metadata.query_one(
            "SELECT * FROM semantic_process_objects WHERE process_contract_id = ?",
            [process_contract_id],
        )
        if row is None:
            raise self._not_found(f"Unknown process object: {process_contract_id}")
        return self._row_to_process_object(row)

    def list_process_objects(
        self,
        status: str | None = None,
        lifecycle_status: str | None = None,
        readiness_status: str | None = None,
        detail: bool = False,
    ) -> dict[str, Any]:
        status = self._resolve_semantic_filters(status=status, lifecycle_status=lifecycle_status)
        if status is None:
            rows = self.metadata.query_rows(
                "SELECT * FROM semantic_process_objects ORDER BY process_ref"
            )
        else:
            rows = self.metadata.query_rows(
                "SELECT * FROM semantic_process_objects WHERE status = ? ORDER BY process_ref",
                [status],
            )
        mode: Literal["list", "detail"] = "detail" if detail else "list"
        items = [
            item
            for row in rows
            if self._matches_readiness_filter(
                item := self._row_to_process_object(row, mode=mode, include_dependents=detail),
                readiness_status=readiness_status,
            )
        ]
        return {"items": items, "total": len(items)}

    def update_process_object(
        self, process_contract_id: str, payload: ProcessObjectUpdateRequest
    ) -> dict[str, Any]:
        current = self.get_process_object(process_contract_id)
        self._require_lifecycle_action_status(
            action="activate",
            status=current["status"],
            object_label="Process object",
            object_id=process_contract_id,
        )
        updates: list[str] = []
        params: list[Any] = []
        interface_contract = payload.interface_contract
        process_payload = payload.payload
        if interface_contract is not None:
            interface_contract_json = interface_contract.model_dump(mode="json")
            payload_json = (
                process_payload.model_dump(mode="json")
                if process_payload is not None
                else current["payload"]
            )
            self._validate_process_refs(interface_contract_json, payload_json)
            updates.extend(
                [
                    "contract_mode = ?",
                    "context_kind = ?",
                    "population_subject_ref = ?",
                    "membership_cardinality = ?",
                    "entity_ref = ?",
                    "emitted_grain_ref = ?",
                    "subject_cardinality = ?",
                    "anchor_time_ref = ?",
                ]
            )
            params.extend(
                [
                    interface_contract_json["contract_mode"],
                    interface_contract_json.get("context_kind"),
                    interface_contract_json["population_subject_ref"],
                    interface_contract_json.get("membership_cardinality"),
                    interface_contract_json.get("entity_ref"),
                    interface_contract_json.get("emitted_grain_ref"),
                    interface_contract_json.get("subject_cardinality"),
                    interface_contract_json.get("anchor_time_ref"),
                ]
            )
            self._replace_process_exported_dimension_refs(
                process_contract_id,
                interface_contract_json.get("exported_dimension_refs"),
            )
        if process_payload is not None:
            payload_json = process_payload.model_dump(mode="json")
            interface_contract_json = (
                interface_contract.model_dump(mode="json")
                if interface_contract is not None
                else current["interface_contract"]
            )
            current_process_type = current["header"]["process_type"]
            if payload_json["process_type"] != current_process_type:
                raise self._validation_error(
                    f"process_type is immutable; expected '{current_process_type}', got '{payload_json['process_type']}'"
                )
            self._validate_process_refs(interface_contract_json, payload_json)
            updates.append("process_payload_json = ?")
            params.append(json.dumps(payload_json))
        if payload.display_name is not None:
            updates.append("display_name = ?")
            params.append(payload.display_name)
        if payload.description is not None:
            updates.append("description = ?")
            params.append(payload.description)
        if not updates:
            return current
        self._apply_contract_update(
            table_name="semantic_process_objects",
            id_column="process_contract_id",
            object_id=process_contract_id,
            updates=updates,
            params=params,
        )
        return self.get_process_object(process_contract_id)

    def validate_process_object(self, process_contract_id: str) -> dict[str, Any]:
        current = self.get_process_object(process_contract_id)
        self._validate_record(
            object_id=process_contract_id,
            object_label="Process object",
            status=current["status"],
            reference_validator=lambda: self._validate_published_process_refs(
                current["interface_contract"], current["payload"]
            ),
        )
        return self.get_process_object(process_contract_id)

    def activate_process_object(self, process_contract_id: str) -> dict[str, Any]:
        current = self.get_process_object(process_contract_id)
        self._activate_record(
            table_name="semantic_process_objects",
            id_column="process_contract_id",
            object_id=process_contract_id,
            object_label="Process object",
            status=current["status"],
            reference_validator=lambda: self._validate_published_process_refs(
                current["interface_contract"], current["payload"]
            ),
        )
        return self.get_process_object(process_contract_id)

    def deprecate_process_object(self, process_contract_id: str) -> dict[str, Any]:
        current = self.get_process_object(process_contract_id)
        self._deprecate_record(
            table_name="semantic_process_objects",
            id_column="process_contract_id",
            object_id=process_contract_id,
            object_label="Process object",
            status=current["status"],
        )
        return self.get_process_object(process_contract_id)

    def publish_process_object(self, process_contract_id: str) -> dict[str, Any]:
        return self.activate_process_object(process_contract_id)

    def create_dimension(self, payload: DimensionCreateRequest) -> dict[str, Any]:
        interface_contract = payload.interface_contract.model_dump(mode="json")
        self._validate_dimension_contract_refs(interface_contract)
        hierarchy = interface_contract.get("hierarchy")
        if hierarchy and hierarchy.get("parent_dimension_ref"):
            self._validate_no_dimension_cycle(
                payload.header.dimension_ref,
                hierarchy["parent_dimension_ref"],
            )
        value_domain = interface_contract["value_domain"]
        grouping = interface_contract.get("grouping")
        time_derived_requirement = interface_contract.get("time_derived_requirement")
        dimension_contract_id = f"dimc_{uuid4().hex[:24]}"
        created_at = now_iso()
        self.metadata.execute(
            """
            INSERT INTO semantic_dimension_contracts (
                dimension_contract_id, dimension_ref, display_name, description,
                dimension_contract_version, structure_kind, semantic_role, value_type,
                domain_kind, enum_set_ref, enum_version, hierarchy_type,
                parent_dimension_ref, supports_grouping, required_time_anchor_ref,
                status, revision, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', 1, ?, ?)
            """,
            [
                dimension_contract_id,
                payload.header.dimension_ref,
                payload.header.display_name
                or payload.header.dimension_ref.removeprefix("dimension."),
                payload.header.description or "",
                payload.header.dimension_contract_version,
                value_domain["structure_kind"],
                value_domain.get("semantic_role"),
                value_domain["value_type"],
                value_domain["domain_kind"],
                value_domain.get("enum_set_ref"),
                value_domain.get("enum_version"),
                hierarchy.get("hierarchy_type") if hierarchy else None,
                hierarchy.get("parent_dimension_ref") if hierarchy else None,
                1 if grouping is None or grouping.get("supports_grouping", True) else 0,
                (
                    time_derived_requirement.get("required_time_anchor_ref")
                    if time_derived_requirement
                    else None
                ),
                created_at,
                created_at,
            ],
        )
        return self.get_dimension(dimension_contract_id)

    def get_dimension(self, dimension_contract_id: str) -> dict[str, Any]:
        row = self.metadata.query_one(
            "SELECT * FROM semantic_dimension_contracts WHERE dimension_contract_id = ?",
            [dimension_contract_id],
        )
        if row is None:
            raise self._not_found(f"Unknown dimension: {dimension_contract_id}")
        return self._row_to_dimension(row)

    def list_dimensions(
        self,
        status: str | None = None,
        lifecycle_status: str | None = None,
        readiness_status: str | None = None,
        detail: bool = False,
    ) -> dict[str, Any]:
        status = self._resolve_semantic_filters(status=status, lifecycle_status=lifecycle_status)
        if status is None:
            rows = self.metadata.query_rows(
                "SELECT * FROM semantic_dimension_contracts ORDER BY dimension_ref"
            )
        else:
            rows = self.metadata.query_rows(
                "SELECT * FROM semantic_dimension_contracts WHERE status = ? ORDER BY dimension_ref",
                [status],
            )
        mode: Literal["list", "detail"] = "detail" if detail else "list"
        list_context = self._list_context()
        filtered_rows = rows
        if readiness_status is not None:
            filtered_rows = []
            for row in rows:
                snapshot = list_context.load_dependency_snapshot(str(row["dimension_ref"]))
                if snapshot is None:
                    continue
                if list_context.readiness_for(snapshot).get("readiness_status") == readiness_status:
                    filtered_rows.append(row)
        items = [
            self._row_to_dimension(
                row,
                mode=mode,
                include_dependents=detail,
                list_context=list_context,
            )
            for row in filtered_rows
        ]
        return {"items": items, "total": len(items)}

    def update_dimension(
        self, dimension_contract_id: str, payload: DimensionUpdateRequest
    ) -> dict[str, Any]:
        current = self.get_dimension(dimension_contract_id)
        self._require_lifecycle_action_status(
            action="activate",
            status=current["status"],
            object_label="Dimension",
            object_id=dimension_contract_id,
        )
        updates: list[str] = []
        params: list[Any] = []
        if payload.display_name is not None:
            updates.append("display_name = ?")
            params.append(payload.display_name)
        if payload.description is not None:
            updates.append("description = ?")
            params.append(payload.description)
        if payload.interface_contract is not None:
            interface_contract = payload.interface_contract.model_dump(mode="json")
            self._validate_dimension_contract_refs(interface_contract)
            hierarchy = interface_contract.get("hierarchy")
            if hierarchy and hierarchy.get("parent_dimension_ref"):
                self._validate_no_dimension_cycle(
                    current["header"]["dimension_ref"],
                    hierarchy["parent_dimension_ref"],
                )
            value_domain = interface_contract["value_domain"]
            grouping = interface_contract.get("grouping")
            time_derived_requirement = interface_contract.get("time_derived_requirement")
            updates.extend(
                [
                    "structure_kind = ?",
                    "semantic_role = ?",
                    "value_type = ?",
                    "domain_kind = ?",
                    "enum_set_ref = ?",
                    "enum_version = ?",
                    "hierarchy_type = ?",
                    "parent_dimension_ref = ?",
                    "supports_grouping = ?",
                    "required_time_anchor_ref = ?",
                ]
            )
            params.extend(
                [
                    value_domain["structure_kind"],
                    value_domain.get("semantic_role"),
                    value_domain["value_type"],
                    value_domain["domain_kind"],
                    value_domain.get("enum_set_ref"),
                    value_domain.get("enum_version"),
                    hierarchy.get("hierarchy_type") if hierarchy else None,
                    hierarchy.get("parent_dimension_ref") if hierarchy else None,
                    1 if grouping is None or grouping.get("supports_grouping", True) else 0,
                    (
                        time_derived_requirement.get("required_time_anchor_ref")
                        if time_derived_requirement
                        else None
                    ),
                ]
            )
        if not updates:
            return current
        self._apply_contract_update(
            table_name="semantic_dimension_contracts",
            id_column="dimension_contract_id",
            object_id=dimension_contract_id,
            updates=updates,
            params=params,
        )
        return self.get_dimension(dimension_contract_id)

    def read_dimension(self, dimension_identifier: str) -> dict[str, Any]:
        row = self.metadata.query_one(
            "SELECT * FROM semantic_dimension_contracts WHERE dimension_contract_id = ?",
            [dimension_identifier],
        )
        if row is None:
            row = self.metadata.query_one(
                "SELECT * FROM semantic_dimension_contracts WHERE dimension_ref = ?",
                [dimension_identifier],
            )
        if row is None:
            raise self._not_found(f"Unknown dimension: {dimension_identifier}")
        return self._row_to_dimension(row)

    def validate_dimension(self, dimension_contract_id: str) -> dict[str, Any]:
        current = self.get_dimension(dimension_contract_id)
        self._validate_record(
            object_id=dimension_contract_id,
            object_label="Dimension",
            status=current["status"],
            reference_validator=lambda: self._validate_published_dimension_contract_refs(
                current["interface_contract"]
            ),
        )
        return self.get_dimension(dimension_contract_id)

    def activate_dimension(self, dimension_contract_id: str) -> dict[str, Any]:
        current = self.get_dimension(dimension_contract_id)
        self._activate_record(
            table_name="semantic_dimension_contracts",
            id_column="dimension_contract_id",
            object_id=dimension_contract_id,
            object_label="Dimension",
            status=current["status"],
            reference_validator=lambda: self._validate_published_dimension_contract_refs(
                current["interface_contract"]
            ),
        )
        return self.get_dimension(dimension_contract_id)

    def deprecate_dimension(self, dimension_contract_id: str) -> dict[str, Any]:
        current = self.get_dimension(dimension_contract_id)
        self._deprecate_record(
            table_name="semantic_dimension_contracts",
            id_column="dimension_contract_id",
            object_id=dimension_contract_id,
            object_label="Dimension",
            status=current["status"],
        )
        return self.get_dimension(dimension_contract_id)

    def publish_dimension(self, dimension_contract_id: str) -> dict[str, Any]:
        return self.activate_dimension(dimension_contract_id)

    # -------------------------------------------------------------------------
    # Predicate CRUD
    # -------------------------------------------------------------------------

    def create_predicate(self, payload: PredicateCreateRequest) -> dict[str, Any]:
        interface_contract = payload.interface_contract.model_dump(mode="json")
        predicate_contract_id = f"predc_{uuid4().hex[:24]}"
        created_at = now_iso()
        self.metadata.execute(
            """
            INSERT INTO semantic_predicate_contracts (
                predicate_contract_id, predicate_ref, display_name, description,
                subject_ref, predicate_contract_version, payload_json,
                status, revision, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', 1, ?, ?)
            """,
            [
                predicate_contract_id,
                payload.header.predicate_ref,
                payload.header.display_name
                or payload.header.predicate_ref.removeprefix("predicate."),
                payload.header.description or "",
                payload.header.subject_ref,
                payload.header.predicate_contract_version,
                json.dumps(interface_contract),
                created_at,
                created_at,
            ],
        )
        return self.get_predicate(predicate_contract_id)

    def get_predicate(self, predicate_contract_id: str) -> dict[str, Any]:
        row = self.metadata.query_one(
            "SELECT * FROM semantic_predicate_contracts WHERE predicate_contract_id = ?",
            [predicate_contract_id],
        )
        if row is None:
            raise self._not_found(f"Unknown predicate: {predicate_contract_id}")
        return self._row_to_predicate(row)

    def read_predicate(self, predicate_identifier: str) -> dict[str, Any]:
        row = self.metadata.query_one(
            "SELECT * FROM semantic_predicate_contracts WHERE predicate_contract_id = ?",
            [predicate_identifier],
        )
        if row is None:
            row = self.metadata.query_one(
                "SELECT * FROM semantic_predicate_contracts WHERE predicate_ref = ?",
                [predicate_identifier],
            )
        if row is None:
            raise self._not_found(f"Unknown predicate: {predicate_identifier}")
        return self._row_to_predicate(row)

    def list_predicates(
        self,
        status: str | None = None,
        lifecycle_status: str | None = None,
        readiness_status: str | None = None,
        detail: bool = False,
    ) -> dict[str, Any]:
        status = self._resolve_semantic_filters(status=status, lifecycle_status=lifecycle_status)
        if status is None:
            rows = self.metadata.query_rows(
                "SELECT * FROM semantic_predicate_contracts ORDER BY predicate_ref"
            )
        else:
            rows = self.metadata.query_rows(
                "SELECT * FROM semantic_predicate_contracts WHERE status = ? ORDER BY predicate_ref",
                [status],
            )
        mode: Literal["list", "detail"] = "detail" if detail else "list"
        list_context = self._list_context()
        filtered_rows = rows
        if readiness_status is not None:
            filtered_rows = []
            for row in rows:
                snapshot = list_context.load_dependency_snapshot(str(row["predicate_ref"]))
                if snapshot is None:
                    continue
                if list_context.readiness_for(snapshot).get("readiness_status") == readiness_status:
                    filtered_rows.append(row)
        items = [
            self._row_to_predicate(
                row,
                mode=mode,
                include_dependents=detail,
                list_context=list_context,
            )
            for row in filtered_rows
        ]
        return {"items": items, "total": len(items)}

    def update_predicate(
        self, predicate_contract_id: str, payload: PredicateUpdateRequest
    ) -> dict[str, Any]:
        current = self.get_predicate(predicate_contract_id)
        self._require_lifecycle_action_status(
            action="activate",
            status=current["status"],
            object_label="Predicate",
            object_id=predicate_contract_id,
        )
        updates: list[str] = []
        params: list[Any] = []
        if payload.display_name is not None:
            updates.append("display_name = ?")
            params.append(payload.display_name)
        if payload.description is not None:
            updates.append("description = ?")
            params.append(payload.description)
        if payload.interface_contract is not None:
            interface_contract = payload.interface_contract.model_dump(mode="json")
            updates.append("payload_json = ?")
            params.append(json.dumps(interface_contract))
        if not updates:
            return current
        self._apply_contract_update(
            table_name="semantic_predicate_contracts",
            id_column="predicate_contract_id",
            object_id=predicate_contract_id,
            updates=updates,
            params=params,
        )
        return self.get_predicate(predicate_contract_id)

    def validate_predicate(self, predicate_contract_id: str) -> dict[str, Any]:
        current = self.get_predicate(predicate_contract_id)
        self._validate_record(
            object_id=predicate_contract_id,
            object_label="Predicate",
            status=current["status"],
            reference_validator=lambda: self._validate_published_predicate_contract_refs(
                current["interface_contract"],
                subject_ref=current["header"]["subject_ref"],
            ),
        )
        return self.get_predicate(predicate_contract_id)

    def activate_predicate(self, predicate_contract_id: str) -> dict[str, Any]:
        current = self.get_predicate(predicate_contract_id)
        self._activate_record(
            table_name="semantic_predicate_contracts",
            id_column="predicate_contract_id",
            object_id=predicate_contract_id,
            object_label="Predicate",
            status=current["status"],
            reference_validator=lambda: self._validate_published_predicate_contract_refs(
                current["interface_contract"],
                subject_ref=current["header"]["subject_ref"],
            ),
        )
        return self.get_predicate(predicate_contract_id)

    def deprecate_predicate(self, predicate_contract_id: str) -> dict[str, Any]:
        current = self.get_predicate(predicate_contract_id)
        self._deprecate_record(
            table_name="semantic_predicate_contracts",
            id_column="predicate_contract_id",
            object_id=predicate_contract_id,
            object_label="Predicate",
            status=current["status"],
        )
        return self.get_predicate(predicate_contract_id)

    def publish_predicate(self, predicate_contract_id: str) -> dict[str, Any]:
        return self.activate_predicate(predicate_contract_id)

    def create_time_semantic(self, payload: TimeCreateRequest) -> dict[str, Any]:
        time_contract_id = f"timec_{uuid4().hex[:24]}"
        created_at = now_iso()
        semantic_roles = set(payload.header.semantic_roles)
        if not semantic_roles:
            raise self._validation_error(
                "At least one semantic role must be specified "
                "(business_anchor, measurement, or operational_support)"
            )
        self.metadata.execute(
            """
            INSERT INTO semantic_time_objects (
                time_contract_id, time_ref, display_name, description,
                time_contract_version, business_anchor, measurement,
                operational_support, status, revision, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', 1, ?, ?)
            """,
            [
                time_contract_id,
                payload.header.time_ref,
                payload.header.display_name or payload.header.time_ref.removeprefix("time."),
                payload.header.description or "",
                payload.header.time_contract_version,
                1 if "business_anchor" in semantic_roles else 0,
                1 if "measurement" in semantic_roles else 0,
                1 if "operational_support" in semantic_roles else 0,
                created_at,
                created_at,
            ],
        )
        return self.get_time_semantic(time_contract_id)

    def read_time_semantic(self, time_identifier: str) -> dict[str, Any]:
        row = self.metadata.query_one(
            "SELECT * FROM semantic_time_objects WHERE time_contract_id = ?",
            [time_identifier],
        )
        if row is None:
            row = self.metadata.query_one(
                "SELECT * FROM semantic_time_objects WHERE time_ref = ?",
                [time_identifier],
            )
        if row is None:
            raise self._not_found(f"Unknown time semantic: {time_identifier}")
        return self._row_to_time_semantic(row)

    def get_time_semantic(self, time_contract_id: str) -> dict[str, Any]:
        row = self.metadata.query_one(
            "SELECT * FROM semantic_time_objects WHERE time_contract_id = ?",
            [time_contract_id],
        )
        if row is None:
            raise self._not_found(f"Unknown time semantic: {time_contract_id}")
        return self._row_to_time_semantic(row)

    def list_time_semantics(
        self,
        status: str | None = None,
        lifecycle_status: str | None = None,
        readiness_status: str | None = None,
        detail: bool = False,
    ) -> dict[str, Any]:
        status = self._resolve_semantic_filters(status=status, lifecycle_status=lifecycle_status)
        if status is None:
            rows = self.metadata.query_rows("SELECT * FROM semantic_time_objects ORDER BY time_ref")
        else:
            rows = self.metadata.query_rows(
                "SELECT * FROM semantic_time_objects WHERE status = ? ORDER BY time_ref",
                [status],
            )
        mode: Literal["list", "detail"] = "detail" if detail else "list"
        items = [
            item
            for row in rows
            if self._matches_readiness_filter(
                item := self._row_to_time_semantic(row, mode=mode, include_dependents=detail),
                readiness_status=readiness_status,
            )
        ]
        return {"items": items, "total": len(items)}

    def update_time_semantic(
        self, time_contract_id: str, payload: TimeUpdateRequest
    ) -> dict[str, Any]:
        current = self.get_time_semantic(time_contract_id)
        self._require_lifecycle_action_status(
            action="activate",
            status=current["status"],
            object_label="Time semantic",
            object_id=time_contract_id,
        )
        updates: list[str] = []
        params: list[Any] = []
        if payload.display_name is not None:
            updates.append("display_name = ?")
            params.append(payload.display_name)
        if payload.description is not None:
            updates.append("description = ?")
            params.append(payload.description)
        if payload.semantic_roles is not None:
            semantic_roles = set(payload.semantic_roles)
            if not semantic_roles:
                raise self._validation_error(
                    "At least one semantic role must be specified "
                    "(business_anchor, measurement, or operational_support)"
                )
            updates.extend(["business_anchor = ?", "measurement = ?", "operational_support = ?"])
            params.extend(
                [
                    1 if "business_anchor" in semantic_roles else 0,
                    1 if "measurement" in semantic_roles else 0,
                    1 if "operational_support" in semantic_roles else 0,
                ]
            )
        if not updates:
            return current
        self._apply_contract_update(
            table_name="semantic_time_objects",
            id_column="time_contract_id",
            object_id=time_contract_id,
            updates=updates,
            params=params,
        )
        return self.get_time_semantic(time_contract_id)

    def validate_time_semantic(self, time_contract_id: str) -> dict[str, Any]:
        current = self.get_time_semantic(time_contract_id)
        self._validate_record(
            object_id=time_contract_id,
            object_label="Time semantic",
            status=current["status"],
        )
        return self.get_time_semantic(time_contract_id)

    def activate_time_semantic(self, time_contract_id: str) -> dict[str, Any]:
        current = self.get_time_semantic(time_contract_id)
        self._activate_record(
            table_name="semantic_time_objects",
            id_column="time_contract_id",
            object_id=time_contract_id,
            object_label="Time semantic",
            status=current["status"],
        )
        return self.get_time_semantic(time_contract_id)

    def deprecate_time_semantic(self, time_contract_id: str) -> dict[str, Any]:
        current = self.get_time_semantic(time_contract_id)
        self._deprecate_record(
            table_name="semantic_time_objects",
            id_column="time_contract_id",
            object_id=time_contract_id,
            object_label="Time semantic",
            status=current["status"],
        )
        return self.get_time_semantic(time_contract_id)

    def publish_time_semantic(self, time_contract_id: str) -> dict[str, Any]:
        return self.activate_time_semantic(time_contract_id)

    def create_enum_set(self, payload: EnumSetCreateRequest) -> dict[str, Any]:
        enum_set_contract_id = f"enumc_{uuid4().hex[:24]}"
        created_at = now_iso()
        self.metadata.execute(
            """
            INSERT INTO semantic_enum_sets (
                enum_set_contract_id, enum_set_ref, display_name, description, value_type,
                status, revision, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'draft', 1, ?, ?)
            """,
            [
                enum_set_contract_id,
                payload.header.enum_set_ref,
                payload.display_name,
                payload.description,
                payload.header.value_type,
                created_at,
                created_at,
            ],
        )
        self._replace_enum_set_versions(
            enum_set_contract_id,
            [version.model_dump(mode="json") for version in payload.versions],
        )
        return self.get_enum_set(enum_set_contract_id)

    def read_enum_set(self, enum_set_identifier: str) -> dict[str, Any]:
        row = self.metadata.query_one(
            "SELECT * FROM semantic_enum_sets WHERE enum_set_contract_id = ?",
            [enum_set_identifier],
        )
        if row is None:
            row = self.metadata.query_one(
                "SELECT * FROM semantic_enum_sets WHERE enum_set_ref = ?",
                [enum_set_identifier],
            )
        if row is None:
            raise self._not_found(f"Unknown enum set: {enum_set_identifier}")
        return self._row_to_enum_set(row)

    def get_enum_set(self, enum_set_contract_id: str) -> dict[str, Any]:
        row = self.metadata.query_one(
            "SELECT * FROM semantic_enum_sets WHERE enum_set_contract_id = ?",
            [enum_set_contract_id],
        )
        if row is None:
            raise self._not_found(f"Unknown enum set: {enum_set_contract_id}")
        return self._row_to_enum_set(row)

    def list_enum_sets(
        self,
        status: str | None = None,
        lifecycle_status: str | None = None,
        readiness_status: str | None = None,
        detail: bool = False,
    ) -> dict[str, Any]:
        status = self._resolve_semantic_filters(status=status, lifecycle_status=lifecycle_status)
        if status is None:
            rows = self.metadata.query_rows(
                "SELECT * FROM semantic_enum_sets ORDER BY enum_set_ref"
            )
        else:
            rows = self.metadata.query_rows(
                "SELECT * FROM semantic_enum_sets WHERE status = ? ORDER BY enum_set_ref",
                [status],
            )
        mode: Literal["list", "detail"] = "detail" if detail else "list"
        items = [
            item
            for row in rows
            if self._matches_readiness_filter(
                item := self._row_to_enum_set(row, mode=mode, include_dependents=detail),
                readiness_status=readiness_status,
            )
        ]
        return {"items": items, "total": len(items)}

    def update_enum_set(
        self, enum_set_contract_id: str, payload: EnumSetUpdateRequest
    ) -> dict[str, Any]:
        current = self.get_enum_set(enum_set_contract_id)
        self._require_lifecycle_action_status(
            action="activate",
            status=current["status"],
            object_label="Enum set",
            object_id=enum_set_contract_id,
        )
        updates: list[str] = []
        params: list[Any] = []
        if payload.display_name is not None:
            updates.append("display_name = ?")
            params.append(payload.display_name)
        if payload.description is not None:
            updates.append("description = ?")
            params.append(payload.description)
        if payload.versions is not None:
            current_value_type = current["header"]["value_type"]
            for version in payload.versions:
                for value in version.values:
                    if not _raw_value_matches_enum_value_type(value.raw_value, current_value_type):
                        raise self._validation_error(
                            f"raw_value for value_key '{value.value_key}' in enum_version "
                            f"'{version.enum_version}' must match header.value_type "
                            f"'{current_value_type}'"
                        )
            self._replace_enum_set_versions(
                enum_set_contract_id,
                [version.model_dump(mode="json") for version in payload.versions],
            )
        if not updates and payload.versions is None:
            return current
        self._apply_contract_update(
            table_name="semantic_enum_sets",
            id_column="enum_set_contract_id",
            object_id=enum_set_contract_id,
            updates=updates,
            params=params,
        )
        return self.get_enum_set(enum_set_contract_id)

    def validate_enum_set(self, enum_set_contract_id: str) -> dict[str, Any]:
        current = self.get_enum_set(enum_set_contract_id)
        self._validate_record(
            object_id=enum_set_contract_id,
            object_label="Enum set",
            status=current["status"],
        )
        return self.get_enum_set(enum_set_contract_id)

    def activate_enum_set(self, enum_set_contract_id: str) -> dict[str, Any]:
        current = self.get_enum_set(enum_set_contract_id)
        self._activate_record(
            table_name="semantic_enum_sets",
            id_column="enum_set_contract_id",
            object_id=enum_set_contract_id,
            object_label="Enum set",
            status=current["status"],
        )
        return self.get_enum_set(enum_set_contract_id)

    def deprecate_enum_set(self, enum_set_contract_id: str) -> dict[str, Any]:
        current = self.get_enum_set(enum_set_contract_id)
        self._deprecate_record(
            table_name="semantic_enum_sets",
            id_column="enum_set_contract_id",
            object_id=enum_set_contract_id,
            object_label="Enum set",
            status=current["status"],
        )
        return self.get_enum_set(enum_set_contract_id)

    def publish_enum_set(self, enum_set_contract_id: str) -> dict[str, Any]:
        return self.activate_enum_set(enum_set_contract_id)
