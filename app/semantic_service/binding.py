from __future__ import annotations

from typing import Any, Literal, cast
from uuid import uuid4

from app.api.models.binding import TypedBindingCreateRequest, TypedBindingUpdateRequest

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

    def get_typed_binding(self, binding_id: str) -> dict[str, Any]:
        row = self.metadata.query_one(
            "SELECT * FROM typed_bindings WHERE binding_id = ?",
            [binding_id],
        )
        if row is None:
            raise self._not_found(f"Unknown typed binding: {binding_id}")
        return self._row_to_typed_binding(row)

    def list_typed_bindings(
        self, status: str | None = None, detail: bool = False
    ) -> dict[str, Any]:
        if status is None:
            rows = self.metadata.query_rows("SELECT * FROM typed_bindings ORDER BY binding_ref")
        else:
            rows = self.metadata.query_rows(
                "SELECT * FROM typed_bindings WHERE status = ? ORDER BY binding_ref",
                [status],
            )
        mode = cast("Literal['list', 'detail']", "detail" if detail else "list")
        items = [
            self._row_to_typed_binding(row, mode=mode, include_dependents=detail) for row in rows
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
