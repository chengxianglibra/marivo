from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from marivo.transports.http.deps import get_services
from marivo.transports.http.models import (
    BrowseSchemaItem,
    BrowseTableItem,
    DatasourceColumnResponse,
    DatasourceDeleteResponse,
    DatasourceRegisterRequest,
    DatasourceResponse,
    DatasourceUpdateRequest,
    TablePreviewResponse,
)

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
            )
        )
    except ValueError as error:
        if "user_required" in str(error):
            raise HTTPException(status_code=401, detail=str(error)) from error
        raise _http_error(error) from error
    except KeyError as error:
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


@router.get(
    "/datasources/{datasource_id}/browse/columns",
    response_model=list[DatasourceColumnResponse],
)
def browse_catalog_columns(
    datasource_id: str,
    request: Request,
    schema_name: str | None = Query(None),
    table_name: str | None = Query(None),
) -> list[DatasourceColumnResponse]:
    try:
        if schema_name is None:
            raise ValueError("schema_name query parameter is required")
        if table_name is None:
            raise ValueError("table_name query parameter is required")
        return [
            DatasourceColumnResponse.model_validate(item)
            for item in get_services(request).datasource_service.browse_catalog_columns(
                datasource_id,
                schema_name=schema_name,
                table_name=table_name,
            )
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


def _http_error(error: KeyError | ValueError) -> HTTPException:
    if isinstance(error, KeyError):
        return HTTPException(status_code=404, detail=str(error))
    return HTTPException(status_code=400, detail=str(error))
