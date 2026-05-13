"""Registration functions for MCP semantic model V2 CRUD tools."""

from __future__ import annotations

from typing import Any

from marivo.identity import require_user, resolve_user
from marivo.transports.mcp.tools._async_bridge import call_runtime
from marivo.transports.mcp.tools.schemas import (
    McpDatasetPayload,
    McpFieldPayload,
    McpFieldUpdatePayload,
    McpMetricPayload,
    McpMetricUpdatePayload,
    McpOsiDocumentPayload,
    McpRelationshipPayload,
    McpRelationshipUpdatePayload,
    McpSemanticModelPayload,
)


def register_semantic_tools(server: Any, runtime: Any) -> None:
    svc = runtime.get_service("semantic_v2")

    # ------------------------------------------------------------------
    # SemanticModel CRUD
    # ------------------------------------------------------------------

    @server.tool()  # type: ignore
    async def create_semantic_model(payload: McpSemanticModelPayload) -> dict[str, Any]:
        """Create a semantic model via POST /semantic-models from an OSI document fragment."""
        return await call_runtime(
            svc.create_semantic_model, model_data=payload.model_dump(by_alias=True)
        )

    @server.tool()  # type: ignore
    async def list_semantic_models() -> dict[str, Any]:
        """List semantic models via GET /semantic-models."""
        return await call_runtime(svc.list_semantic_models, requesting_user=resolve_user())

    @server.tool()  # type: ignore
    async def get_semantic_model(model: str) -> dict[str, Any]:
        """Get a semantic model as an OSI document via GET /semantic-models/{model}."""
        return await call_runtime(
            svc.get_semantic_model,
            name=model,
            requesting_user=resolve_user(),
        )

    @server.tool()  # type: ignore
    async def import_osi_document(document: McpOsiDocumentPayload) -> dict[str, Any]:
        """Import an OSI document into the current user's private working copy."""
        return await call_runtime(
            svc.import_osi_document,
            doc_data=document.model_dump(by_alias=True),
        )

    @server.tool()  # type: ignore
    async def export_osi_document(semantic_model_name: str | None = None) -> dict[str, Any]:
        """Export the current user's private working copy as an OSI document."""
        return await call_runtime(
            svc.export_osi_document,
            semantic_model_name=semantic_model_name,
        )

    @server.tool()  # type: ignore
    async def update_semantic_model(
        model: str,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Update top-level fields of a semantic model via PUT /semantic-models/{model}."""
        updates: dict[str, Any] = {}
        if description is not None:
            updates["description"] = description
        return await call_runtime(
            svc.update_semantic_model,
            name=model,
            updates=updates,
            owner_user=require_user(),
        )

    @server.tool()  # type: ignore
    async def delete_semantic_model(model: str) -> dict[str, Any]:
        """Delete a semantic model via DELETE /semantic-models/{model}."""
        return await call_runtime(svc.delete_semantic_model, name=model, owner_user=require_user())

    @server.tool()  # type: ignore
    async def get_semantic_model_readiness(model: str) -> dict[str, Any]:
        """Get readiness status for a semantic model via GET /semantic-models/{model}/readiness."""
        return await call_runtime(
            svc.get_readiness,
            model_name=model,
            requesting_user=resolve_user(),
        )

    # ------------------------------------------------------------------
    # Dataset CRUD
    # ------------------------------------------------------------------

    @server.tool()  # type: ignore
    async def create_dataset(
        model: str,
        payload: McpDatasetPayload,
    ) -> dict[str, Any]:
        """Create a dataset within a model via POST /semantic-models/{model}/datasets."""
        return await call_runtime(
            svc.create_dataset,
            model_name=model,
            ds_data=payload.model_dump(by_alias=True),
            owner_user=require_user(),
        )

    @server.tool()  # type: ignore
    async def list_datasets(model: str) -> dict[str, Any]:
        """List datasets in a model via GET /semantic-models/{model}/datasets."""
        return await call_runtime(
            svc.list_datasets,
            model_name=model,
            requesting_user=resolve_user(),
        )

    @server.tool()  # type: ignore
    async def get_dataset(model: str, name: str) -> dict[str, Any]:
        """Get a dataset by name within a model via GET /semantic-models/{model}/datasets/{name}."""
        return await call_runtime(
            svc.get_dataset,
            model_name=model,
            dataset_name=name,
            requesting_user=resolve_user(),
        )

    @server.tool()  # type: ignore
    async def update_dataset(
        model: str,
        name: str,
        description: str | None = None,
        source: str | None = None,
        primary_key: list[str] | None = None,
        unique_keys: list[list[str]] | None = None,
    ) -> dict[str, Any]:
        """Update a dataset's top-level fields via PUT /semantic-models/{model}/datasets/{name}."""
        updates: dict[str, Any] = {}
        if description is not None:
            updates["description"] = description
        if source is not None:
            updates["source"] = source
        if primary_key is not None:
            updates["primary_key"] = primary_key
        if unique_keys is not None:
            updates["unique_keys"] = unique_keys
        return await call_runtime(
            svc.update_dataset,
            model_name=model,
            dataset_name=name,
            updates=updates,
            owner_user=require_user(),
        )

    @server.tool()  # type: ignore
    async def delete_dataset(model: str, name: str) -> dict[str, Any]:
        """Delete a dataset via DELETE /semantic-models/{model}/datasets/{name}."""
        return await call_runtime(
            svc.delete_dataset, model_name=model, dataset_name=name, owner_user=require_user()
        )

    # ------------------------------------------------------------------
    # Field CRUD
    # ------------------------------------------------------------------

    @server.tool()  # type: ignore
    async def create_field(
        model: str,
        dataset: str,
        payload: McpFieldPayload,
    ) -> dict[str, Any]:
        """Create a field in a dataset."""
        return await call_runtime(
            svc.create_field,
            model_name=model,
            dataset_name=dataset,
            field_data=payload.model_dump(by_alias=True),
            owner_user=require_user(),
        )

    @server.tool()  # type: ignore
    async def list_fields(model: str, dataset: str) -> dict[str, Any]:
        """List fields in a dataset."""
        return await call_runtime(
            svc.list_fields,
            model_name=model,
            dataset_name=dataset,
            requesting_user=resolve_user(),
        )

    @server.tool()  # type: ignore
    async def get_field(model: str, dataset: str, name: str) -> dict[str, Any]:
        """Get a field by name."""
        return await call_runtime(
            svc.get_field,
            model_name=model,
            dataset_name=dataset,
            field_name=name,
            requesting_user=resolve_user(),
        )

    @server.tool()  # type: ignore
    async def update_field(
        model: str,
        dataset: str,
        name: str,
        payload: McpFieldUpdatePayload,
    ) -> dict[str, Any]:
        """Patch a field by name."""
        updates = {
            field_name: getattr(payload, field_name) for field_name in payload.model_fields_set
        }
        return await call_runtime(
            svc.update_field,
            model_name=model,
            dataset_name=dataset,
            field_name=name,
            updates=updates,
            owner_user=require_user(),
        )

    @server.tool()  # type: ignore
    async def delete_field(model: str, dataset: str, name: str) -> dict[str, Any]:
        """Delete a field by name."""
        return await call_runtime(
            svc.delete_field,
            model_name=model,
            dataset_name=dataset,
            field_name=name,
            owner_user=require_user(),
        )

    # ------------------------------------------------------------------
    # Relationship CRUD
    # ------------------------------------------------------------------

    @server.tool()  # type: ignore
    async def create_relationship(
        model: str,
        payload: McpRelationshipPayload,
    ) -> dict[str, Any]:
        """Create a relationship within a model via POST /semantic-models/{model}/relationships."""
        return await call_runtime(
            svc.create_relationship,
            model_name=model,
            rel_data=payload.model_dump(by_alias=True),
            owner_user=require_user(),
        )

    @server.tool()  # type: ignore
    async def list_relationships(model: str) -> dict[str, Any]:
        """List relationships in a model via GET /semantic-models/{model}/relationships."""
        return await call_runtime(
            svc.list_relationships,
            model_name=model,
            requesting_user=resolve_user(),
        )

    @server.tool()  # type: ignore
    async def get_relationship(model: str, name: str) -> dict[str, Any]:
        """Get a relationship by name within a model via GET /semantic-models/{model}/relationships/{name}."""
        return await call_runtime(
            svc.get_relationship,
            model_name=model,
            rel_name=name,
            requesting_user=resolve_user(),
        )

    @server.tool()  # type: ignore
    async def update_relationship(
        model: str,
        name: str,
        payload: McpRelationshipUpdatePayload,
    ) -> dict[str, Any]:
        """Update a relationship's fields via PUT /semantic-models/{model}/relationships/{name}."""
        return await call_runtime(
            svc.update_relationship,
            model_name=model,
            rel_name=name,
            updates=payload.model_dump(exclude_none=True),
            owner_user=require_user(),
        )

    @server.tool()  # type: ignore
    async def delete_relationship(model: str, name: str) -> dict[str, Any]:
        """Delete a relationship via DELETE /semantic-models/{model}/relationships/{name}."""
        return await call_runtime(
            svc.delete_relationship, model_name=model, rel_name=name, owner_user=require_user()
        )

    # ------------------------------------------------------------------
    # Metric CRUD
    # ------------------------------------------------------------------

    @server.tool()  # type: ignore
    async def create_metric(
        model: str,
        payload: McpMetricPayload,
    ) -> dict[str, Any]:
        """Create a metric within a model via POST /semantic-models/{model}/metrics."""
        return await call_runtime(
            svc.create_metric,
            model_name=model,
            metric_data=payload.model_dump(by_alias=True),
            owner_user=require_user(),
        )

    @server.tool()  # type: ignore
    async def list_metrics(model: str) -> dict[str, Any]:
        """List metrics in a model via GET /semantic-models/{model}/metrics."""
        return await call_runtime(
            svc.list_metrics,
            model_name=model,
            requesting_user=resolve_user(),
        )

    @server.tool()  # type: ignore
    async def get_metric(model: str, name: str) -> dict[str, Any]:
        """Get a metric by name within a model via GET /semantic-models/{model}/metrics/{name}."""
        return await call_runtime(
            svc.get_metric,
            model_name=model,
            metric_name=name,
            requesting_user=resolve_user(),
        )

    @server.tool()  # type: ignore
    async def update_metric(
        model: str,
        name: str,
        payload: McpMetricUpdatePayload,
    ) -> dict[str, Any]:
        """Update a metric's fields via PUT /semantic-models/{model}/metrics/{name}."""
        return await call_runtime(
            svc.update_metric,
            model_name=model,
            metric_name=name,
            updates=payload.model_dump(exclude_none=True),
            owner_user=require_user(),
        )

    @server.tool()  # type: ignore
    async def delete_metric(model: str, name: str) -> dict[str, Any]:
        """Delete a metric via DELETE /semantic-models/{model}/metrics/{name}."""
        return await call_runtime(
            svc.delete_metric, model_name=model, metric_name=name, owner_user=require_user()
        )
