from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.deps import get_services
from app.api.models import (
    BrowseSchemaItem,
    BrowseTableItem,
    ColumnPropertiesUpdateRequest,
    DatasourceDeleteResponse,
    DatasourceRegisterRequest,
    DatasourceResponse,
    DatasourceUpdateRequest,
    ObjectPropertiesResponse,
    SourceObjectResponse,
    SyncClearedResponse,
    SyncJobStatusResponse,
    SyncSelectionDeletedResponse,
    SyncSelectionRequest,
    SyncSelectionResponse,
    SyncTriggerResponse,
    TablePreviewResponse,
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
                connection=payload.connection.model_dump(exclude={"datasource_type"}),
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
                connection=payload.connection.model_dump(exclude={"datasource_type"})
                if payload.connection is not None
                else None,
                sync_mode=payload.sync_mode,
                policy=payload.policy.model_dump() if payload.policy else None,
            )
        )
    except (ValueError, KeyError) as error:
        raise _http_error(error) from error


@router.delete("/datasources/{datasource_id}", response_model=DatasourceDeleteResponse)
def delete_datasource(datasource_id: str, request: Request) -> DatasourceDeleteResponse:
    services = get_services(request)
    try:
        services.datasource_service.delete_datasource(datasource_id)
        return DatasourceDeleteResponse(datasource_id=datasource_id, deleted=True)
    except KeyError as error:
        raise _http_error(error) from error
    except DependencyError as error:
        raise HTTPException(
            status_code=409, detail={"message": str(error), "dependencies": error.dependencies}
        ) from error


@router.post("/datasources/{datasource_id}/sync", response_model=SyncTriggerResponse)
def trigger_sync(datasource_id: str, request: Request) -> SyncTriggerResponse:
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
        return SyncTriggerResponse(job_id=job_id, datasource_id=datasource_id, status="succeeded")
    except KeyError as error:
        raise _http_error(error) from error


# These endpoints must stay ahead of /sync/{job_id} so that "selections"
# does not get captured as a job id.
@router.get(
    "/datasources/{datasource_id}/sync/selections", response_model=list[SyncSelectionResponse]
)
def list_sync_selections(datasource_id: str, request: Request) -> list[SyncSelectionResponse]:
    services = get_services(request)
    try:
        services.datasource_service.get_datasource(datasource_id)
    except KeyError as error:
        raise _http_error(error) from error
    return [
        SyncSelectionResponse.model_validate(row)
        for row in services.datasource_service.list_sync_selections(datasource_id)
    ]


@router.post(
    "/datasources/{datasource_id}/sync/selections", response_model=list[SyncSelectionResponse]
)
def add_sync_selections(
    datasource_id: str, payload: SyncSelectionRequest, request: Request
) -> list[SyncSelectionResponse]:
    services = get_services(request)
    try:
        services.datasource_service.get_datasource(datasource_id)
    except KeyError as error:
        raise _http_error(error) from error
    results: list[SyncSelectionResponse] = []
    for selection in payload.selections:
        result = services.datasource_service.add_sync_selection(
            datasource_id,
            schema_name=selection.schema_name,
            table_name=selection.table_name,
        )
        results.append(SyncSelectionResponse.model_validate(result))
    return results


@router.delete("/datasources/{datasource_id}/sync/selections", response_model=SyncClearedResponse)
def clear_sync_selections(datasource_id: str, request: Request) -> SyncClearedResponse:
    try:
        get_services(request).datasource_service.clear_sync_selections(datasource_id)
        return SyncClearedResponse(status="cleared", datasource_id=datasource_id)
    except KeyError as error:
        raise _http_error(error) from error


@router.delete(
    "/datasources/{datasource_id}/sync/selections/{selection_id}",
    response_model=SyncSelectionDeletedResponse,
)
def remove_sync_selection(
    datasource_id: str, selection_id: str, request: Request
) -> SyncSelectionDeletedResponse:
    services = get_services(request)
    try:
        services.datasource_service.get_datasource(datasource_id)
        services.datasource_service.remove_sync_selection(selection_id)
        return SyncSelectionDeletedResponse(status="deleted", selection_id=selection_id)
    except KeyError as error:
        raise _http_error(error) from error


@router.get("/datasources/{datasource_id}/sync/{job_id}", response_model=SyncJobStatusResponse)
def get_sync_status(datasource_id: str, job_id: str, request: Request) -> SyncJobStatusResponse:
    try:
        row = get_services(request).sync_engine.get_sync_status(job_id)
        return SyncJobStatusResponse(
            job_id=row["job_id"],
            datasource_id=row["datasource_id"],
            job_type=row["job_type"],
            status=row["status"],
            started_at=row.get("started_at"),
            finished_at=row.get("finished_at"),
            objects_synced=row.get("objects_synced"),
            error_message=row.get("error_message"),
        )
    except KeyError as error:
        raise _http_error(error) from error


@router.get("/datasources/{datasource_id}/browse/schemas", response_model=list[BrowseSchemaItem])
def browse_catalog_schemas(datasource_id: str, request: Request) -> list[BrowseSchemaItem]:
    try:
        rows = get_services(request).datasource_service.browse_catalog_schemas(datasource_id)
        return [
            BrowseSchemaItem(schema_name=row["name"], table_count=row.get("table_count", 0))
            for row in rows
        ]
    except KeyError as error:
        raise _http_error(error) from error


@router.get("/datasources/{datasource_id}/browse/tables", response_model=list[BrowseTableItem])
def browse_catalog_tables(
    datasource_id: str, request: Request, schema_name: str | None = Query(None)
) -> list[BrowseTableItem]:
    try:
        if schema_name is None:
            raise ValueError("schema_name query parameter is required")
        rows = get_services(request).datasource_service.browse_catalog_tables(
            datasource_id, schema_name=schema_name
        )
        return [
            BrowseTableItem(
                table_name=row["name"],
                schema_name=row.get("schema", schema_name),
                row_count=row.get("row_count"),
                column_count=row.get("column_count"),
            )
            for row in rows
        ]
    except (KeyError, ValueError) as error:
        raise _http_error(error) from error


@router.get("/datasources/{datasource_id}/catalog/preview", response_model=TablePreviewResponse)
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
) -> TablePreviewResponse:
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
        result = services.datasource_service.preview_table(
            datasource_id=datasource_id,
            schema_name=schema,
            table_name=table,
            limit=limit,
            columns=column_list,
            filters=filter_map,
        )
        return TablePreviewResponse.model_validate(result)
    except (KeyError, ValueError) as error:
        raise _http_error(error) from error


@router.patch(
    "/datasources/{datasource_id}/objects/{object_id}/properties",
    response_model=ObjectPropertiesResponse,
)
def patch_column_properties(
    datasource_id: str, object_id: str, payload: ColumnPropertiesUpdateRequest, request: Request
) -> ObjectPropertiesResponse:
    services = get_services(request)
    user_props = {k: v for k, v in payload.model_dump().items() if v is not None}
    try:
        result = services.datasource_service.patch_object_properties(
            datasource_id, object_id, user_props
        )
        return ObjectPropertiesResponse(
            object_id=result["object_id"],
            properties=result["properties"],
        )
    except (KeyError, ValueError) as error:
        raise _http_error(error) from error


@router.get("/datasources/{datasource_id}/objects/{object_id}", response_model=SourceObjectResponse)
def get_datasource_object(
    datasource_id: str, object_id: str, request: Request
) -> SourceObjectResponse:
    services = get_services(request)
    try:
        services.datasource_service.get_datasource(datasource_id)
        return SourceObjectResponse.model_validate(
            services.datasource_service.get_object(datasource_id, object_id)
        )
    except KeyError as error:
        raise _http_error(error) from error


@router.get("/datasources/{datasource_id}/objects", response_model=list[SourceObjectResponse])
def list_datasource_objects(
    datasource_id: str,
    request: Request,
    type: str | None = Query(default=None),
    schema: str | None = Query(default=None, alias="schema"),
) -> list[SourceObjectResponse]:
    services = get_services(request)
    try:
        services.datasource_service.get_datasource(datasource_id)
    except KeyError as error:
        raise _http_error(error) from error
    return [
        SourceObjectResponse.model_validate(obj)
        for obj in services.datasource_service.list_objects(
            datasource_id, object_type=type, schema_name=schema
        )
    ]


def _http_error(error: KeyError | ValueError) -> HTTPException:
    if isinstance(error, KeyError):
        return HTTPException(status_code=404, detail=str(error))
    return HTTPException(status_code=400, detail=str(error))
