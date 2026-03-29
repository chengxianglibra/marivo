from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.deps import get_services
from app.api.models import (
    EntityCreateRequest,
    EntityPropertiesPatchRequest,
    EntityUpdateRequest,
    MappingCreateRequest,
    MetricCreateRequest,
    MetricUpdateRequest,
)

router = APIRouter()


@router.post("/semantic/entities")
def create_entity(payload: EntityCreateRequest, request: Request) -> dict[str, object]:
    return get_services(request).semantic_service.create_entity(
        name=payload.name,
        display_name=payload.display_name,
        description=payload.description,
        keys=payload.keys,
        level=payload.level,
        join_constraints=payload.join_constraints,
        upstream_dependencies=payload.upstream_dependencies,
        lineage=payload.lineage,
        quality_expectations=payload.quality_expectations,
        properties=payload.properties,
    )


@router.get("/semantic/entities")
def list_entities(
    request: Request, status: str | None = Query(default=None)
) -> list[dict[str, object]]:
    return get_services(request).semantic_service.list_entities(status=status)


@router.get("/semantic/entities/{entity_id}")
def get_entity(entity_id: str, request: Request) -> dict[str, object]:
    try:
        return get_services(request).semantic_service.get_entity(entity_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.put("/semantic/entities/{entity_id}")
def update_entity(
    entity_id: str, payload: EntityUpdateRequest, request: Request
) -> dict[str, object]:
    try:
        return get_services(request).semantic_service.update_entity(
            entity_id, **payload.model_dump(exclude_none=True)
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.patch("/semantic/entities/{entity_id}/properties")
def patch_entity_properties(
    entity_id: str, payload: EntityPropertiesPatchRequest, request: Request
) -> dict[str, object]:
    """G-5d: Incrementally patch properties on a published entity (e.g. apply a unit hint)."""
    try:
        return get_services(request).semantic_service.patch_entity_properties(
            entity_id, payload.properties
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/semantic/entities/{entity_id}/publish")
def publish_entity(entity_id: str, request: Request) -> dict[str, object]:
    try:
        return get_services(request).semantic_service.publish_entity(entity_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/semantic/metrics")
def create_metric(payload: MetricCreateRequest, request: Request) -> dict[str, object]:
    return get_services(request).semantic_service.create_metric(
        name=payload.name,
        display_name=payload.display_name,
        description=payload.description,
        definition_sql=payload.definition_sql,
        dimensions=payload.dimensions,
        entity_id=payload.entity_id,
        grain=payload.grain,
        measure_type=payload.measure_type,
        allowed_dimensions=payload.allowed_dimensions,
        lineage=payload.lineage,
        quality_expectations=payload.quality_expectations,
        properties=payload.properties,
        desired_direction=payload.desired_direction,
    )


@router.get("/semantic/metrics")
def list_metrics(
    request: Request, status: str | None = Query(default=None)
) -> list[dict[str, object]]:
    return get_services(request).semantic_service.list_metrics(status=status)


@router.get("/semantic/metrics/{metric_id}")
def get_metric(metric_id: str, request: Request) -> dict[str, object]:
    try:
        return get_services(request).semantic_service.get_metric(metric_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.put("/semantic/metrics/{metric_id}")
def update_metric(
    metric_id: str, payload: MetricUpdateRequest, request: Request
) -> dict[str, object]:
    try:
        return get_services(request).semantic_service.update_metric(
            metric_id, **payload.model_dump(exclude_unset=True)
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/semantic/metrics/{metric_id}/publish")
def publish_metric(metric_id: str, request: Request) -> dict[str, object]:
    try:
        return get_services(request).semantic_service.publish_metric(metric_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/semantic/mappings")
def create_mapping(payload: MappingCreateRequest, request: Request) -> dict[str, object]:
    return get_services(request).semantic_service.create_mapping(
        semantic_type=payload.semantic_type,
        semantic_id=payload.semantic_id,
        object_id=payload.object_id,
        mapping_type=payload.mapping_type,
        mapping_json=payload.mapping_json,
    )


@router.get("/semantic/mappings")
def list_mappings(
    request: Request,
    semantic_type: str | None = Query(default=None),
    semantic_id: str | None = Query(default=None),
) -> list[dict[str, object]]:
    return get_services(request).semantic_service.list_mappings(
        semantic_type=semantic_type,
        semantic_id=semantic_id,
    )


@router.delete("/semantic/mappings/{mapping_id}")
def delete_mapping(mapping_id: str, request: Request) -> dict[str, str]:
    try:
        get_services(request).semantic_service.delete_mapping(mapping_id)
        return {"status": "deleted", "mapping_id": mapping_id}
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
