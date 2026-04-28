from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal
from uuid import uuid4

from app.api.models.binding import (
    BindingDeriveRevisionRequest,
    TypedBindingCreateRequest,
    TypedBindingUpdateRequest,
)

from .common import SemanticServiceSupport, now_iso


class TypedBindingService(SemanticServiceSupport):
    def create_typed_binding(self, payload: TypedBindingCreateRequest) -> dict[str, Any]:
        interface_contract = payload.interface_contract.model_dump(mode="json")
        self._validate_typed_binding_contract(
            binding_ref=payload.header.binding_ref,
            binding_scope=payload.header.binding_scope,
            bound_object_ref=payload.header.bound_object_ref,
            interface_contract=interface_contract,
            require_published_dependencies=False,
        )
        binding_id = f"bind_{uuid4().hex[:24]}"
        created_at = now_iso()
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
                created_at,
                created_at,
            ],
        )
        self._replace_binding_contract(binding_id, interface_contract)
        return self.get_typed_binding(binding_id)

    def read_typed_binding(self, binding_identifier: str) -> dict[str, Any]:
        row = self.metadata.query_one(
            "SELECT * FROM typed_bindings WHERE binding_id = ?",
            [binding_identifier],
        )
        if row is None:
            row = self.metadata.query_one(
                """
                SELECT *
                FROM typed_bindings
                WHERE binding_ref = ?
                ORDER BY CASE WHEN status = 'published' THEN 0 ELSE 1 END, revision DESC
                """,
                [binding_identifier],
            )
        if row is None:
            raise self._not_found(f"Unknown typed binding: {binding_identifier}")
        return self._row_to_typed_binding(row)

    def get_typed_binding(self, binding_id: str) -> dict[str, Any]:
        row = self.metadata.query_one(
            "SELECT * FROM typed_bindings WHERE binding_id = ?",
            [binding_id],
        )
        if row is None:
            raise self._not_found(f"Unknown typed binding: {binding_id}")
        return self._row_to_typed_binding(row)

    def list_typed_bindings(
        self,
        status: str | None = None,
        lifecycle_status: str | None = None,
        readiness_status: str | None = None,
        detail: bool = False,
    ) -> dict[str, Any]:
        status = self._resolve_semantic_filters(status=status, lifecycle_status=lifecycle_status)
        if status is None:
            rows = self.metadata.query_rows("SELECT * FROM typed_bindings ORDER BY binding_ref")
        else:
            rows = self.metadata.query_rows(
                "SELECT * FROM typed_bindings WHERE status = ? ORDER BY binding_ref",
                [status],
            )
        mode: Literal["list", "detail"] = "detail" if detail else "list"
        list_context = self._list_context()
        filtered_rows = rows
        if readiness_status is not None:
            filtered_rows = []
            for row in rows:
                snapshot = list_context.load_dependency_snapshot(str(row["binding_ref"]))
                if snapshot is None:
                    continue
                if list_context.readiness_for(snapshot).get("readiness_status") == readiness_status:
                    filtered_rows.append(row)
        items = [
            self._row_to_typed_binding(
                row,
                mode=mode,
                include_dependents=detail,
                list_context=list_context,
            )
            for row in filtered_rows
        ]
        return {"items": items, "total": len(items)}

    def update_typed_binding(
        self, binding_id: str, payload: TypedBindingUpdateRequest
    ) -> dict[str, Any]:
        current = self.get_typed_binding(binding_id)
        self._require_lifecycle_action_status(
            action="activate",
            status=current["status"],
            object_label="Typed binding",
            object_id=binding_id,
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
            self._validate_typed_binding_contract(
                binding_ref=current["header"]["binding_ref"],
                binding_scope=current["header"]["binding_scope"],
                bound_object_ref=current["header"]["bound_object_ref"],
                interface_contract=interface_contract,
                require_published_dependencies=False,
            )
            self._replace_binding_contract(binding_id, interface_contract)
        if not updates and payload.interface_contract is None:
            return current
        updates.extend(["revision = revision + 1", "updated_at = ?"])
        params.append(now_iso())
        params.append(binding_id)
        self.metadata.execute(
            f"UPDATE typed_bindings SET {', '.join(updates)} WHERE binding_id = ?",
            params,
        )
        return self.get_typed_binding(binding_id)

    def derive_binding_revision(
        self, binding_identifier: str, payload: BindingDeriveRevisionRequest
    ) -> dict[str, Any]:
        row = self.metadata.query_one(
            "SELECT * FROM typed_bindings WHERE binding_id = ?",
            [binding_identifier],
        )
        if row is None:
            row = self.metadata.query_one(
                """
                SELECT *
                FROM typed_bindings
                WHERE binding_ref = ?
                ORDER BY CASE WHEN status = 'published' THEN 0 ELSE 1 END, revision DESC
                """,
                [binding_identifier],
            )
        if row is None:
            raise self._not_found(f"Unknown typed binding: {binding_identifier}")

        current = self._row_to_typed_binding(row)
        current_revision = int(current["revision"])
        if payload.base_revision != current_revision:
            raise self._state_error(
                "Binding base_revision does not match current revision: "
                f"expected {current_revision}, got {payload.base_revision}"
            )
        if current["header"]["binding_scope"] != "metric":
            raise self._validation_error(
                "Binding revision derivation currently requires metric scope"
            )
        if current["header"]["bound_object_ref"] != payload.target_metric_ref:
            raise self._validation_error(
                "target_metric_ref must match the binding bound_object_ref"
            )

        interface_contract = deepcopy(current["interface_contract"])
        for addition in payload.coverage_additions:
            self._append_binding_coverage(interface_contract, addition.model_dump(mode="json"))

        next_revision = current_revision + 1
        binding_id = f"bind_{uuid4().hex[:24]}"
        created_at = now_iso()
        header = current["header"]
        self.metadata.execute(
            """
            INSERT INTO typed_bindings (
                binding_id, binding_ref, binding_scope, bound_object_ref,
                binding_contract_version, display_name, description,
                status, revision, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?)
            """,
            [
                binding_id,
                header["binding_ref"],
                header["binding_scope"],
                header["bound_object_ref"],
                header["binding_contract_version"],
                header.get("display_name"),
                header.get("description"),
                next_revision,
                created_at,
                created_at,
            ],
        )
        self._replace_binding_contract(binding_id, interface_contract)
        return self.get_typed_binding(binding_id)

    def _append_binding_coverage(
        self, interface_contract: dict[str, Any], addition: dict[str, str]
    ) -> None:
        coverage_target = addition["coverage_target"]
        field_ref = addition["field_ref"]
        target_key = coverage_target.removeprefix("metric_input.")
        field_bindings = interface_contract.setdefault("field_bindings", [])
        for field_binding in field_bindings:
            if field_binding.get("semantic_ref") == coverage_target:
                raise self._validation_error(
                    f"Binding already covers coverage_target: {coverage_target}"
                )

        matching_carrier_keys = []
        for carrier in interface_contract.get("carrier_bindings") or []:
            field_surface_refs = {
                surface.get("surface_ref") for surface in carrier.get("field_surfaces") or []
            }
            if field_ref in field_surface_refs:
                matching_carrier_keys.append(str(carrier["binding_key"]))
        if not matching_carrier_keys:
            raise self._validation_error(
                f"coverage field_ref does not exist on any carrier field surface: {field_ref}"
            )
        if len(matching_carrier_keys) > 1:
            raise self._validation_error(
                f"coverage field_ref is ambiguous across carrier bindings: {field_ref}"
            )

        field_bindings.append(
            {
                "carrier_binding_key": matching_carrier_keys[0],
                "target": {
                    "target_kind": "metric_input",
                    "target_key": target_key,
                },
                "semantic_ref": coverage_target,
                "surface_ref": field_ref,
            }
        )

    def validate_typed_binding(self, binding_id: str) -> dict[str, Any]:
        current = self.get_typed_binding(binding_id)
        self._validate_record(
            object_id=binding_id,
            object_label="Typed binding",
            status=current["status"],
            reference_validator=lambda: self._validate_typed_binding_contract(
                binding_ref=current["header"]["binding_ref"],
                binding_scope=current["header"]["binding_scope"],
                bound_object_ref=current["header"]["bound_object_ref"],
                interface_contract=current["interface_contract"],
                require_published_dependencies=True,
            ),
        )
        return self.get_typed_binding(binding_id)

    def activate_typed_binding(self, binding_id: str) -> dict[str, Any]:
        current = self.get_typed_binding(binding_id)
        active_same_ref = self.metadata.query_one(
            """
            SELECT binding_id
            FROM typed_bindings
            WHERE binding_ref = ? AND status = 'published' AND binding_id <> ?
            ORDER BY revision DESC
            """,
            [current["header"]["binding_ref"], binding_id],
        )
        if active_same_ref is not None:
            self._require_lifecycle_action_status(
                action="activate",
                status=current["status"],
                object_label="Typed binding",
                object_id=binding_id,
            )
            self._run_publish_reference_validation(
                lambda: self._validate_typed_binding_contract(
                    binding_ref=current["header"]["binding_ref"],
                    binding_scope=current["header"]["binding_scope"],
                    bound_object_ref=current["header"]["bound_object_ref"],
                    interface_contract=current["interface_contract"],
                    require_published_dependencies=True,
                )
            )
            now = now_iso()
            with self.metadata.connect() as con:
                self.metadata.execute_sql(
                    con,
                    """
                    UPDATE typed_bindings
                    SET status = 'deprecated', updated_at = ?
                    WHERE binding_ref = ? AND status = 'published' AND binding_id <> ?
                    """,
                    [now, current["header"]["binding_ref"], binding_id],
                )
                self.metadata.execute_sql(
                    con,
                    """
                    UPDATE typed_bindings
                    SET status = 'published', updated_at = ?
                    WHERE binding_id = ?
                    """,
                    [now, binding_id],
                )
                con.commit()
            return self.get_typed_binding(binding_id)

        self._activate_record(
            table_name="typed_bindings",
            id_column="binding_id",
            object_id=binding_id,
            object_label="Typed binding",
            status=current["status"],
            reference_validator=lambda: self._validate_typed_binding_contract(
                binding_ref=current["header"]["binding_ref"],
                binding_scope=current["header"]["binding_scope"],
                bound_object_ref=current["header"]["bound_object_ref"],
                interface_contract=current["interface_contract"],
                require_published_dependencies=True,
            ),
        )
        return self.get_typed_binding(binding_id)

    def deprecate_typed_binding(self, binding_id: str) -> dict[str, Any]:
        current = self.get_typed_binding(binding_id)
        self._deprecate_record(
            table_name="typed_bindings",
            id_column="binding_id",
            object_id=binding_id,
            object_label="Typed binding",
            status=current["status"],
        )
        return self.get_typed_binding(binding_id)

    def publish_typed_binding(self, binding_id: str) -> dict[str, Any]:
        return self.activate_typed_binding(binding_id)
