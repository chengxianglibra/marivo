from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.deps import get_services
from app.api.models import (
    ColumnPropertiesUpdateRequest,
    SourceRegisterRequest,
    SourceUpdateRequest,
    SyncSelectionRequest,
)
from app.registry.source_registry import DependencyError

router = APIRouter()


@router.post("/sources")
def register_source(payload: SourceRegisterRequest, request: Request) -> dict[str, object]:
    services = get_services(request)
    try:
        return services.source_service.register_source(
            source_type=payload.source_type,
            display_name=payload.display_name,
            authority=payload.authority.model_dump(),
            sync=payload.sync.model_dump(),
            policy=payload.policy.model_dump(),
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/sources")
def list_sources(request: Request) -> list[dict[str, object]]:
    return get_services(request).source_service.list_sources()


@router.get("/sources/{source_id}")
def get_source(source_id: str, request: Request) -> dict[str, object]:
    try:
        return get_services(request).source_service.get_source(source_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.put("/sources/{source_id}")
def update_source(
    source_id: str, payload: SourceUpdateRequest, request: Request
) -> dict[str, object]:
    try:
        return get_services(request).source_service.update_source(
            source_id,
            display_name=payload.display_name,
            authority=payload.authority.model_dump() if payload.authority is not None else None,
            sync=payload.sync.model_dump() if payload.sync is not None else None,
            policy=payload.policy.model_dump() if payload.policy is not None else None,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.delete("/sources/{source_id}")
def delete_source(source_id: str, request: Request) -> dict[str, object]:
    try:
        get_services(request).source_service.delete_source(source_id)
        return {"status": "deleted", "source_id": source_id}
    except DependencyError as error:
        raise HTTPException(
            status_code=409, detail={"message": str(error), "dependencies": error.dependencies}
        ) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/sources/{source_id}/sync")
def trigger_sync(source_id: str, request: Request) -> dict[str, object]:
    services = get_services(request)
    try:
        sync_mode = services.source_service.get_sync_mode(source_id)
        if sync_mode == "none":
            raise HTTPException(status_code=400, detail="Sync disabled for this source (mode=none)")
        if sync_mode == "selected":
            selections = services.source_service.list_sync_selections(source_id)
            if not selections:
                raise HTTPException(
                    status_code=400,
                    detail="No sync selections configured for this source (mode=selected)",
                )
            selection_dicts = [
                {"schema_name": row["schema_name"], "table_name": row["table_name"]}
                for row in selections
            ]
            adapter = services.source_service.get_adapter(source_id)
            job_id = services.sync_engine.trigger_sync(
                source_id, adapter, selections=selection_dicts
            )
        elif sync_mode == "all":
            adapter = services.source_service.get_adapter(source_id)
            job_id = services.sync_engine.trigger_sync(source_id, adapter)
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown sync.mode '{sync_mode}'. Supported modes: 'selected', 'all', 'none'",
            )
        return {"job_id": job_id, "source_id": source_id, "status": "succeeded"}
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


# These endpoints must stay ahead of /sync/{job_id} so that "selections"
# does not get captured as a job id.
@router.get("/sources/{source_id}/sync/selections")
def list_sync_selections(source_id: str, request: Request) -> list[dict[str, object]]:
    services = get_services(request)
    try:
        services.source_service.get_source(source_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return services.source_service.list_sync_selections(source_id)


@router.post("/sources/{source_id}/sync/selections")
def add_sync_selections(
    source_id: str, payload: SyncSelectionRequest, request: Request
) -> list[dict[str, object]]:
    try:
        return get_services(request).source_service.set_sync_selections(
            source_id,
            [
                {"schema_name": selection.schema_name, "table_name": selection.table_name}
                for selection in payload.selections
            ],
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.delete("/sources/{source_id}/sync/selections")
def clear_sync_selections(source_id: str, request: Request) -> dict[str, str]:
    try:
        get_services(request).source_service.clear_sync_selections(source_id)
        return {"status": "cleared", "source_id": source_id}
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.delete("/sources/{source_id}/sync/selections/{selection_id}")
def remove_sync_selection(source_id: str, selection_id: str, request: Request) -> dict[str, str]:
    services = get_services(request)
    try:
        services.source_service.get_source(source_id)
        services.source_service.remove_sync_selection(selection_id)
        return {"status": "deleted", "selection_id": selection_id}
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/sources/{source_id}/sync/{job_id}")
def get_sync_status(source_id: str, job_id: str, request: Request) -> dict[str, object]:
    try:
        return get_services(request).sync_engine.get_sync_status(job_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/sources/{source_id}/catalog/schemas")
def browse_catalog_schemas(source_id: str, request: Request) -> list[dict[str, object]]:
    try:
        return get_services(request).source_service.browse_catalog_schemas(source_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/sources/{source_id}/catalog/tables")
def browse_catalog_tables(
    source_id: str, request: Request, schema: str = Query(...)
) -> list[dict[str, object]]:
    try:
        return get_services(request).source_service.browse_catalog_tables(source_id, schema)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/sources/{source_id}/catalog/preview")
def preview_table(
    source_id: str,
    request: Request,
    schema: str = Query(..., description="Schema name"),
    table: str = Query(..., description="Table name"),
    limit: int = Query(default=100, ge=1, description="Max rows to return"),
    columns: str | None = Query(default=None, description="Comma-separated column names"),
) -> dict[str, object]:
    """Preview sample rows from a source table (live query, no persistence).

    The limit is clamped to a maximum of 1000 rows at the adapter level.
    """
    services = get_services(request)
    column_list = None
    if columns:
        column_list = [c.strip() for c in columns.split(",") if c.strip()]
        # Treat empty column list as None (all columns) to avoid "SELECT  FROM ..."
        if not column_list:
            column_list = None
    try:
        return services.source_service.preview_table(
            source_id=source_id,
            schema_name=schema,
            table_name=table,
            limit=limit,
            columns=column_list,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.patch("/sources/{source_id}/objects/{object_id}/properties")
def patch_column_properties(
    source_id: str, object_id: str, payload: ColumnPropertiesUpdateRequest, request: Request
) -> dict[str, object]:
    services = get_services(request)
    user_props = {k: v for k, v in payload.model_dump().items() if v is not None}
    try:
        return services.source_service.patch_object_properties(source_id, object_id, user_props)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/sources/{source_id}/objects/{object_id}")
def get_source_object(source_id: str, object_id: str, request: Request) -> dict[str, object]:
    services = get_services(request)
    try:
        services.source_service.get_source(source_id)
        return services.source_service.get_object(source_id, object_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/sources/{source_id}/objects")
def list_source_objects(
    source_id: str,
    request: Request,
    type: str | None = Query(default=None),
    schema: str | None = Query(default=None, alias="schema"),
) -> list[dict[str, object]]:
    services = get_services(request)
    try:
        services.source_service.get_source(source_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return services.source_service.list_objects(source_id, object_type=type, schema_name=schema)
