from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.deps import get_services
from app.api.models import (
    ColumnPropertiesUpdateRequest,
    DatasourceRegisterRequest,
    DatasourceResponse,
    DatasourceUpdateRequest,
    SyncSelectionRequest,
)
from app.registry.datasource_registry import DependencyError

router = APIRouter()


def _parse_preview_filters(raw: str | None) -> dict[str, str | int | float | bool | None] | None:
    if raw is None or not raw.strip():
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("filters must be a JSON object or array of {column, value}") from error
    if isinstance(parsed, dict):
        items = parsed.items()
    elif isinstance(parsed, list):
        normalized: dict[str, Any] = {}
        for item in parsed:
            if not isinstance(item, dict) or "column" not in item or "value" not in item:
                raise ValueError("filters array items must contain column and value")
            normalized[str(item["column"])] = item["value"]
        items = normalized.items()
    else:
        raise ValueError("filters must be a JSON object or array of {column, value}")
    filters: dict[str, str | int | float | bool | None] = {}
    for column, value in items:
        if not isinstance(column, str) or not column.strip():
            raise ValueError("filter column names must be non-empty strings")
        if not isinstance(value, str | int | float | bool) and value is not None:
            raise ValueError("filter values must be scalar strings, numbers, booleans, or null")
        filters[column.strip()] = value
    return filters or None


@router.post("/datasources", response_model=DatasourceResponse)
def register_datasource(payload: DatasourceRegisterRequest, request: Request) -> DatasourceResponse:
    services = get_services(request)
    try:
        return DatasourceResponse.model_validate(
            services.datasource_service.register_datasource(
                datasource_type=payload.datasource_type,
                display_name=payload.display_name,
                connection=payload.connection.model_dump(),
                sync_mode=payload.sync_mode,
                policy=payload.policy.model_dump(),
            )
        )
    except (ValueError, KeyError) as error:
        raise _http_error(error) from error


@router.get("/datasources", response_model=list[DatasourceResponse])
def list_datasources(request: Request) -> list[DatasourceResponse]:
    services = get_services(request)
    return [
        DatasourceResponse.model_validate(ds)
        for ds in services.datasource_service.list_datasources()
    ]


@router.get("/datasources/{datasource_id}", response_model=DatasourceResponse)
def get_datasource(datasource_id: str, request: Request) -> DatasourceResponse:
    services = get_services(request)
    try:
        return DatasourceResponse.model_validate(
            services.datasource_service.get_datasource(datasource_id)
        )
    except KeyError as error:
        raise _http_error(error) from error


@router.put("/datasources/{datasource_id}", response_model=DatasourceResponse)
def update_datasource(
    datasource_id: str, payload: DatasourceUpdateRequest, request: Request
) -> DatasourceResponse:
    services = get_services(request)
    try:
        return DatasourceResponse.model_validate(
            services.datasource_service.update_datasource(
                datasource_id=datasource_id,
                display_name=payload.display_name,
                connection=payload.connection.model_dump() if payload.connection else None,
                sync_mode=payload.sync_mode,
                policy=payload.policy.model_dump() if payload.policy else None,
            )
        )
    except (ValueError, KeyError) as error:
        raise _http_error(error) from error


@router.delete("/datasources/{datasource_id}")
def delete_datasource(datasource_id: str, request: Request) -> dict[str, Any]:
    services = get_services(request)
    try:
        services.datasource_service.delete_datasource(datasource_id)
        return {"status": "deleted", "datasource_id": datasource_id}
    except KeyError as error:
        raise _http_error(error) from error
    except DependencyError as error:
        raise HTTPException(
            status_code=409, detail={"message": str(error), "dependencies": error.dependencies}
        ) from error


@router.post("/datasources/{datasource_id}/sync")
def trigger_sync(datasource_id: str, request: Request) -> dict[str, object]:
    services = get_services(request)
    try:
        sync_mode = services.datasource_service.get_sync_mode(datasource_id)
        if sync_mode == "none":
            raise HTTPException(
                status_code=400, detail="Sync disabled for this datasource (mode=none)"
            )
        if sync_mode == "selected":
            selections = services.datasource_service.list_sync_selections(datasource_id)
            if not selections:
                raise HTTPException(
                    status_code=400,
                    detail="No sync selections configured for this datasource (mode=selected)",
                )
            selection_dicts = [
                {"schema_name": row["schema_name"], "table_name": row["table_name"]}
                for row in selections
            ]
            adapter = services.datasource_service.get_adapter(datasource_id)
            job_id = services.sync_engine.trigger_sync(
                datasource_id, adapter, selections=selection_dicts
            )
        elif sync_mode == "all":
            adapter = services.datasource_service.get_adapter(datasource_id)
            job_id = services.sync_engine.trigger_sync(datasource_id, adapter)
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown sync_mode '{sync_mode}'. Supported modes: 'selected', 'all', 'none'",
            )
        return {"job_id": job_id, "datasource_id": datasource_id, "status": "succeeded"}
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


# These endpoints must stay ahead of /sync/{job_id} so that "selections"
# does not get captured as a job id.
@router.get("/datasources/{datasource_id}/sync/selections")
def list_sync_selections(datasource_id: str, request: Request) -> list[dict[str, object]]:
    services = get_services(request)
    try:
        services.datasource_service.get_datasource(datasource_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return services.datasource_service.list_sync_selections(datasource_id)


@router.post("/datasources/{datasource_id}/sync/selections")
def add_sync_selections(
    datasource_id: str, payload: SyncSelectionRequest, request: Request
) -> list[dict[str, object]]:
    services = get_services(request)
    try:
        services.datasource_service.get_datasource(datasource_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    results: list[dict[str, object]] = []
    for selection in payload.selections:
        result = services.datasource_service.add_sync_selection(
            datasource_id,
            schema_name=selection.schema_name,
            table_name=selection.table_name,
        )
        results.append(result)
    return results


@router.delete("/datasources/{datasource_id}/sync/selections")
def clear_sync_selections(datasource_id: str, request: Request) -> dict[str, str]:
    try:
        get_services(request).datasource_service.clear_sync_selections(datasource_id)
        return {"status": "cleared", "datasource_id": datasource_id}
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.delete("/datasources/{datasource_id}/sync/selections/{selection_id}")
def remove_sync_selection(
    datasource_id: str, selection_id: str, request: Request
) -> dict[str, str]:
    services = get_services(request)
    try:
        services.datasource_service.get_datasource(datasource_id)
        services.datasource_service.remove_sync_selection(selection_id)
        return {"status": "deleted", "selection_id": selection_id}
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/datasources/{datasource_id}/sync/{job_id}")
def get_sync_status(datasource_id: str, job_id: str, request: Request) -> dict[str, object]:
    try:
        return get_services(request).sync_engine.get_sync_status(job_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/datasources/{datasource_id}/browse/schemas")
def browse_catalog_schemas(datasource_id: str, request: Request) -> list[dict[str, object]]:
    try:
        return get_services(request).datasource_service.browse_catalog_schemas(datasource_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/datasources/{datasource_id}/browse/tables")
def browse_catalog_tables(
    datasource_id: str, request: Request, schema_name: str | None = Query(None)
) -> list[dict[str, object]]:
    try:
        if schema_name is None:
            raise ValueError("schema_name query parameter is required")
        return get_services(request).datasource_service.browse_catalog_tables(
            datasource_id, schema_name=schema_name
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/datasources/{datasource_id}/catalog/preview")
def preview_table(
    datasource_id: str,
    request: Request,
    schema: str = Query(..., description="Schema name"),
    table: str = Query(..., description="Table name"),
    limit: int = Query(default=100, ge=1, description="Max rows to return"),
    columns: str | None = Query(default=None, description="Comma-separated column names"),
    filters: str | None = Query(
        default=None,
        description="JSON object or array of {column,value} equality filters",
    ),
) -> dict[str, object]:
    """Preview sample rows from a datasource table (live query, no persistence).

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
        filter_map = _parse_preview_filters(filters)
        return services.datasource_service.preview_table(
            datasource_id=datasource_id,
            schema_name=schema,
            table_name=table,
            limit=limit,
            columns=column_list,
            filters=filter_map,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.patch("/datasources/{datasource_id}/objects/{object_id}/properties")
def patch_column_properties(
    datasource_id: str, object_id: str, payload: ColumnPropertiesUpdateRequest, request: Request
) -> dict[str, object]:
    services = get_services(request)
    user_props = {k: v for k, v in payload.model_dump().items() if v is not None}
    try:
        return services.datasource_service.patch_object_properties(
            datasource_id, object_id, user_props
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/datasources/{datasource_id}/objects/{object_id}")
def get_datasource_object(
    datasource_id: str, object_id: str, request: Request
) -> dict[str, object]:
    services = get_services(request)
    try:
        services.datasource_service.get_datasource(datasource_id)
        return services.datasource_service.get_object(datasource_id, object_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/datasources/{datasource_id}/objects")
def list_datasource_objects(
    datasource_id: str,
    request: Request,
    type: str | None = Query(default=None),
    schema: str | None = Query(default=None, alias="schema"),
) -> list[dict[str, object]]:
    services = get_services(request)
    try:
        services.datasource_service.get_datasource(datasource_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return services.datasource_service.list_objects(
        datasource_id, object_type=type, schema_name=schema
    )


def _http_error(error: KeyError | ValueError) -> HTTPException:
    if isinstance(error, KeyError):
        return HTTPException(status_code=404, detail=str(error))
    return HTTPException(status_code=400, detail=str(error))
