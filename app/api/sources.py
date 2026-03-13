from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.deps import get_services
from app.api.models import SourceRegisterRequest, SyncSelectionRequest


router = APIRouter()


@router.post("/sources")
def register_source(payload: SourceRegisterRequest, request: Request) -> dict[str, object]:
    services = get_services(request)
    return services.source_service.register_source(
        source_type=payload.source_type,
        display_name=payload.display_name,
        connection=payload.connection,
        capabilities=payload.capabilities,
    )


@router.get("/sources")
def list_sources(request: Request) -> list[dict[str, object]]:
    return get_services(request).source_service.list_sources()


@router.get("/sources/{source_id}")
def get_source(source_id: str, request: Request) -> dict[str, object]:
    try:
        return get_services(request).source_service.get_source(source_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/sources/{source_id}/sync")
def trigger_sync(source_id: str, request: Request) -> dict[str, object]:
    services = get_services(request)
    try:
        sync_mode = services.source_service.get_sync_mode(source_id)
        if sync_mode == "none":
            raise HTTPException(status_code=400, detail="Sync disabled for this source (mode=none)")
        adapter = services.source_service.get_adapter(source_id)
        if sync_mode == "by_select":
            selections = services.source_service.list_sync_selections(source_id)
            if not selections:
                raise HTTPException(status_code=400, detail="No sync selections configured for this source (mode=by_select)")
            selection_dicts = [{"schema_name": row["schema_name"], "table_name": row["table_name"]} for row in selections]
            job_id = services.sync_engine.trigger_sync(source_id, adapter, selections=selection_dicts)
        else:
            job_id = services.sync_engine.trigger_sync(source_id, adapter)
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
def add_sync_selections(source_id: str, payload: SyncSelectionRequest, request: Request) -> list[dict[str, object]]:
    try:
        return get_services(request).source_service.set_sync_selections(
            source_id,
            [{"schema_name": selection.schema_name, "table_name": selection.table_name} for selection in payload.selections],
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
def browse_catalog_tables(source_id: str, request: Request, schema: str = Query(...)) -> list[dict[str, object]]:
    try:
        return get_services(request).source_service.browse_catalog_tables(source_id, schema)
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
