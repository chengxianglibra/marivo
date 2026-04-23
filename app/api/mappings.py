from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.deps import get_services
from app.api.models import MappingCreateRequest, MappingUpdateRequest

router = APIRouter()


@router.post("/mappings")
def create_mapping(payload: MappingCreateRequest, request: Request) -> dict[str, object]:
    services = get_services(request)
    try:
        return services.mapping_service.create_mapping(
            source_id=payload.source_id,
            engine_id=payload.engine_id,
            priority=payload.priority,
            catalog_mappings=[entry.model_dump() for entry in payload.catalog_mappings],
            status=payload.status,
        )
    except (KeyError, ValueError) as error:
        raise HTTPException(
            status_code=404 if isinstance(error, KeyError) else 400,
            detail=str(error),
        ) from error


@router.get("/mappings")
def list_mappings(
    request: Request,
    source_id: str | None = Query(default=None),
    engine_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> list[dict[str, object]]:
    try:
        return get_services(request).mapping_service.list_mappings(
            source_id=source_id,
            engine_id=engine_id,
            status=status,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/mappings/{mapping_id}")
def get_mapping(mapping_id: str, request: Request) -> dict[str, object]:
    try:
        return get_services(request).mapping_service.get_mapping(mapping_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.put("/mappings/{mapping_id}")
def update_mapping(
    mapping_id: str,
    payload: MappingUpdateRequest,
    request: Request,
) -> dict[str, object]:
    try:
        return get_services(request).mapping_service.update_mapping(
            mapping_id,
            priority=payload.priority,
            catalog_mappings=(
                None
                if payload.catalog_mappings is None
                else [entry.model_dump() for entry in payload.catalog_mappings]
            ),
            status=payload.status,
        )
    except (KeyError, ValueError) as error:
        raise HTTPException(
            status_code=404 if isinstance(error, KeyError) else 400,
            detail=str(error),
        ) from error


@router.delete("/mappings/{mapping_id}")
def delete_mapping(mapping_id: str, request: Request) -> dict[str, str]:
    try:
        get_services(request).mapping_service.delete_mapping(mapping_id)
        return {"status": "deleted", "mapping_id": mapping_id}
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
