from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request
from pydantic import ValidationError

from app.api.deps import get_services
from app.api.models import (
    CompatibilityProfileCreateRequest,
    CompatibilityProfileUpdateRequest,
    EntityPropertiesPatchRequest,
    TypedBindingCreateRequest,
    TypedBindingUpdateRequest,
    TypedEntityCreateRequest,
    TypedEntityUpdateRequest,
    TypedMetricCreateRequest,
    TypedMetricUpdateRequest,
)
from app.api.models._legacy import (
    EntityCreateRequest,
    EntityUpdateRequest,
    MappingCreateRequest,
    MetricCreateRequest,
    MetricUpdateRequest,
)

router = APIRouter()


def _validation_detail(error: ValidationError) -> list[Any]:
    return error.errors(include_url=False)


def _parse_entity_create(payload: dict[str, Any]) -> TypedEntityCreateRequest | EntityCreateRequest:
    if "header" in payload or "interface_contract" in payload:
        return TypedEntityCreateRequest.model_validate(payload)
    return EntityCreateRequest.model_validate(payload)


def _parse_entity_update(
    entity_id: str, payload: dict[str, Any]
) -> TypedEntityUpdateRequest | EntityUpdateRequest:
    if "interface_contract" in payload or entity_id.startswith("entc_"):
        return TypedEntityUpdateRequest.model_validate(payload)
    return EntityUpdateRequest.model_validate(payload)


def _parse_metric_create(payload: dict[str, Any]) -> TypedMetricCreateRequest | MetricCreateRequest:
    if "header" in payload or "payload" in payload:
        return TypedMetricCreateRequest.model_validate(payload)
    return MetricCreateRequest.model_validate(payload)


def _parse_metric_update(
    metric_id: str, payload: dict[str, Any]
) -> TypedMetricUpdateRequest | MetricUpdateRequest:
    if "payload" in payload or metric_id.startswith("metc_"):
        return TypedMetricUpdateRequest.model_validate(payload)
    return MetricUpdateRequest.model_validate(payload)


@router.post("/semantic/entities")
def create_entity(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        parsed = _parse_entity_create(payload)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=_validation_detail(error)) from error

    semantic_service = get_services(request).semantic_service
    if isinstance(parsed, TypedEntityCreateRequest):
        return semantic_service.create_typed_entity(parsed)
    return semantic_service.create_entity(
        name=parsed.name,
        display_name=parsed.display_name,
        description=parsed.description,
        keys=parsed.keys,
        level=parsed.level,
        join_constraints=parsed.join_constraints,
        upstream_dependencies=parsed.upstream_dependencies,
        lineage=parsed.lineage,
        quality_expectations=parsed.quality_expectations,
        properties=parsed.properties,
    )


@router.get("/semantic/entities")
def list_entities(
    request: Request,
    status: str | None = Query(default=None),
    surface: str | None = Query(default=None),
) -> dict[str, Any] | list[dict[str, Any]]:
    semantic_service = get_services(request).semantic_service
    if surface == "typed":
        return semantic_service.list_typed_entities(status=status)
    return semantic_service.list_entities(status=status)


@router.get("/semantic/entities/{entity_id}")
def get_entity(
    entity_id: str,
    request: Request,
    surface: str | None = Query(default=None),
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    try:
        if surface == "typed" or entity_id.startswith("entc_"):
            return semantic_service.get_typed_entity(entity_id)
        return semantic_service.get_entity(entity_id)
    except KeyError:
        try:
            return semantic_service.get_typed_entity(entity_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error


@router.put("/semantic/entities/{entity_id}")
def update_entity(
    entity_id: str, request: Request, payload: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    try:
        parsed = _parse_entity_update(entity_id, payload)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=_validation_detail(error)) from error

    semantic_service = get_services(request).semantic_service
    try:
        if isinstance(parsed, TypedEntityUpdateRequest):
            return semantic_service.update_typed_entity(entity_id, parsed)
        return semantic_service.update_entity(entity_id, **parsed.model_dump(exclude_none=True))
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.patch("/semantic/entities/{entity_id}/properties")
def patch_entity_properties(
    entity_id: str, payload: EntityPropertiesPatchRequest, request: Request
) -> dict[str, Any]:
    try:
        return get_services(request).semantic_service.patch_entity_properties(
            entity_id, payload.properties
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/semantic/entities/{entity_id}/publish")
def publish_entity(entity_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    try:
        if entity_id.startswith("entc_"):
            return semantic_service.publish_typed_entity(entity_id)
        return semantic_service.publish_entity(entity_id)
    except KeyError:
        try:
            return semantic_service.publish_typed_entity(entity_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/semantic/metrics")
def create_metric(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        parsed = _parse_metric_create(payload)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=_validation_detail(error)) from error

    semantic_service = get_services(request).semantic_service
    if isinstance(parsed, TypedMetricCreateRequest):
        return semantic_service.create_typed_metric(parsed)
    return semantic_service.create_metric(
        name=parsed.name,
        display_name=parsed.display_name,
        description=parsed.description,
        definition_sql=parsed.definition_sql,
        dimensions=parsed.dimensions,
        entity_id=parsed.entity_id,
        grain=parsed.grain,
        measure_type=parsed.measure_type,
        allowed_dimensions=parsed.allowed_dimensions,
        lineage=parsed.lineage,
        quality_expectations=parsed.quality_expectations,
        properties=parsed.properties,
        desired_direction=parsed.desired_direction,
    )


@router.get("/semantic/metrics")
def list_metrics(
    request: Request,
    status: str | None = Query(default=None),
    surface: str | None = Query(default=None),
) -> dict[str, Any] | list[dict[str, Any]]:
    semantic_service = get_services(request).semantic_service
    if surface == "typed":
        return semantic_service.list_typed_metrics(status=status)
    return semantic_service.list_metrics(status=status)


@router.get("/semantic/metrics/{metric_id}")
def get_metric(
    metric_id: str,
    request: Request,
    surface: str | None = Query(default=None),
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    try:
        if surface == "typed" or metric_id.startswith("metc_"):
            return semantic_service.get_typed_metric(metric_id)
        return semantic_service.get_metric(metric_id)
    except KeyError:
        try:
            return semantic_service.get_typed_metric(metric_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error


@router.put("/semantic/metrics/{metric_id}")
def update_metric(
    metric_id: str, request: Request, payload: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    try:
        parsed = _parse_metric_update(metric_id, payload)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=_validation_detail(error)) from error

    semantic_service = get_services(request).semantic_service
    try:
        if isinstance(parsed, TypedMetricUpdateRequest):
            return semantic_service.update_typed_metric(metric_id, parsed)
        return semantic_service.update_metric(metric_id, **parsed.model_dump(exclude_unset=True))
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/semantic/metrics/{metric_id}/publish")
def publish_metric(metric_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    try:
        if metric_id.startswith("metc_"):
            return semantic_service.publish_typed_metric(metric_id)
        return semantic_service.publish_metric(metric_id)
    except KeyError:
        try:
            return semantic_service.publish_typed_metric(metric_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/semantic/bindings")
def create_typed_binding(payload: TypedBindingCreateRequest, request: Request) -> dict[str, Any]:
    try:
        return get_services(request).semantic_service.create_typed_binding(payload)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/semantic/bindings")
def list_typed_bindings(
    request: Request, status: str | None = Query(default=None)
) -> dict[str, Any]:
    return get_services(request).semantic_service.list_typed_bindings(status=status)


@router.get("/semantic/bindings/{binding_id}")
def get_typed_binding(binding_id: str, request: Request) -> dict[str, Any]:
    try:
        return get_services(request).semantic_service.get_typed_binding(binding_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.put("/semantic/bindings/{binding_id}")
def update_typed_binding(
    binding_id: str, payload: TypedBindingUpdateRequest, request: Request
) -> dict[str, Any]:
    try:
        return get_services(request).semantic_service.update_typed_binding(binding_id, payload)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/semantic/bindings/{binding_id}/publish")
def publish_typed_binding(binding_id: str, request: Request) -> dict[str, Any]:
    try:
        return get_services(request).semantic_service.publish_typed_binding(binding_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/compiler/compatibility-profiles")
def create_compatibility_profile(
    payload: CompatibilityProfileCreateRequest, request: Request
) -> dict[str, Any]:
    try:
        return get_services(request).semantic_service.create_compatibility_profile(payload)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/compiler/compatibility-profiles")
def list_compatibility_profiles(
    request: Request, status: str | None = Query(default=None)
) -> dict[str, Any]:
    return get_services(request).semantic_service.list_compatibility_profiles(status=status)


@router.get("/compiler/compatibility-profiles/{profile_id}")
def get_compatibility_profile(profile_id: str, request: Request) -> dict[str, Any]:
    try:
        return get_services(request).semantic_service.get_compatibility_profile(profile_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.put("/compiler/compatibility-profiles/{profile_id}")
def update_compatibility_profile(
    profile_id: str, payload: CompatibilityProfileUpdateRequest, request: Request
) -> dict[str, Any]:
    try:
        return get_services(request).semantic_service.update_compatibility_profile(
            profile_id, payload
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/compiler/compatibility-profiles/{profile_id}/publish")
def publish_compatibility_profile(profile_id: str, request: Request) -> dict[str, Any]:
    try:
        return get_services(request).semantic_service.publish_compatibility_profile(profile_id)
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
