from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request

from app.api.deps import get_services
from app.api.models import (
    CompatibilityProfileCreateRequest,
    CompatibilityProfileResponse,
    CompatibilityProfileUpdateRequest,
    DimensionCreateRequest,
    DimensionResponse,
    DimensionUpdateRequest,
    EnumSetCreateRequest,
    EnumSetResponse,
    EnumSetUpdateRequest,
    ProcessObjectCreateRequest,
    ProcessObjectResponse,
    ProcessObjectUpdateRequest,
    SemanticValidateActionResponse,
    TimeCreateRequest,
    TimeResponse,
    TimeUpdateRequest,
    TypedBindingCreateRequest,
    TypedBindingResponse,
    TypedBindingUpdateRequest,
    TypedEntityCreateRequest,
    TypedEntityResponse,
    TypedEntityUpdateRequest,
    TypedMetricCreateRequest,
    TypedMetricResponse,
    TypedMetricUpdateRequest,
)

router = APIRouter()


def _run_route_action(
    action: Callable[[], dict[str, Any]],
    *,
    value_error_status: int = 422,
    structured_value_error: bool = False,
) -> dict[str, Any]:
    try:
        return action()
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        error_code = getattr(error, "code", None)
        error_category = getattr(error, "category", None)
        if structured_value_error and isinstance(error_code, str):
            raise HTTPException(
                status_code=value_error_status,
                detail={
                    "message": str(error),
                    "code": error_code,
                    "category": error_category,
                },
            ) from error
        raise HTTPException(status_code=value_error_status, detail=str(error)) from error


@router.post("/semantic/entities", response_model=TypedEntityResponse)
def create_entity(
    request: Request, payload: TypedEntityCreateRequest = Body(...)
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.create_typed_entity(payload))


@router.get("/semantic/entities")
def list_entities(
    request: Request,
    status: str | None = Query(default=None),
    lifecycle_status: str | None = Query(default=None),
    readiness_status: str | None = Query(default=None),
    detail: bool = Query(
        default=False, description="Return full detail instead of lightweight format."
    ),
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.list_typed_entities(
            status=status,
            lifecycle_status=lifecycle_status,
            readiness_status=readiness_status,
            detail=detail,
        )
    )


@router.get("/semantic/entities/{entity_id}", response_model=TypedEntityResponse)
def get_entity(
    entity_id: str,
    request: Request,
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.read_typed_entity(entity_id))


@router.put("/semantic/entities/{entity_id}", response_model=TypedEntityResponse)
def update_entity(
    entity_id: str,
    request: Request,
    payload: TypedEntityUpdateRequest = Body(...),
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.update_typed_entity(entity_id, payload))


@router.post("/semantic/entities/{entity_id}/publish")
def publish_entity(entity_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.publish_typed_entity(entity_id),
        structured_value_error=True,
    )


@router.post(
    "/semantic/entities/{entity_id}/validate", response_model=SemanticValidateActionResponse
)
def validate_entity(entity_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.validate_typed_entity(entity_id),
        structured_value_error=True,
    )


@router.post("/semantic/entities/{entity_id}/activate", response_model=TypedEntityResponse)
def activate_entity(entity_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.activate_typed_entity(entity_id),
        structured_value_error=True,
    )


@router.post("/semantic/entities/{entity_id}/deprecate", response_model=TypedEntityResponse)
def deprecate_entity(entity_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.deprecate_typed_entity(entity_id),
        structured_value_error=True,
    )


@router.post("/semantic/metrics", response_model=TypedMetricResponse)
def create_metric(
    request: Request, payload: TypedMetricCreateRequest = Body(...)
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.create_typed_metric(payload))


@router.get("/semantic/metrics")
def list_metrics(
    request: Request,
    status: str | None = Query(default=None),
    lifecycle_status: str | None = Query(default=None),
    readiness_status: str | None = Query(default=None),
    detail: bool = Query(
        default=False, description="Return full detail instead of lightweight format."
    ),
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.list_typed_metrics(
            status=status,
            lifecycle_status=lifecycle_status,
            readiness_status=readiness_status,
            detail=detail,
        )
    )


@router.get("/semantic/metrics/{metric_id}", response_model=TypedMetricResponse)
def get_metric(
    metric_id: str,
    request: Request,
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.read_typed_metric(metric_id))


@router.put("/semantic/metrics/{metric_id}", response_model=TypedMetricResponse)
def update_metric(
    metric_id: str,
    request: Request,
    payload: TypedMetricUpdateRequest = Body(...),
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.update_typed_metric(metric_id, payload))


@router.post("/semantic/metrics/{metric_id}/publish")
def publish_metric(metric_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.publish_typed_metric(metric_id),
        structured_value_error=True,
    )


@router.post(
    "/semantic/metrics/{metric_id}/validate", response_model=SemanticValidateActionResponse
)
def validate_metric(metric_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.validate_typed_metric(metric_id),
        structured_value_error=True,
    )


@router.post("/semantic/metrics/{metric_id}/activate", response_model=TypedMetricResponse)
def activate_metric(metric_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.activate_typed_metric(metric_id),
        structured_value_error=True,
    )


@router.post("/semantic/metrics/{metric_id}/deprecate", response_model=TypedMetricResponse)
def deprecate_metric(metric_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.deprecate_typed_metric(metric_id),
        structured_value_error=True,
    )


@router.post("/semantic/process-objects", response_model=ProcessObjectResponse)
def create_process_object(
    request: Request, payload: ProcessObjectCreateRequest = Body(...)
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.create_process_object(payload))


@router.get("/semantic/process-objects")
def list_process_objects(
    request: Request,
    status: str | None = Query(default=None),
    lifecycle_status: str | None = Query(default=None),
    readiness_status: str | None = Query(default=None),
    detail: bool = Query(
        default=False, description="Return full detail instead of lightweight format."
    ),
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.list_process_objects(
            status=status,
            lifecycle_status=lifecycle_status,
            readiness_status=readiness_status,
            detail=detail,
        )
    )


@router.get("/semantic/process-objects/{process_contract_id}", response_model=ProcessObjectResponse)
def get_process_object(process_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.read_process_object(process_contract_id))


@router.put("/semantic/process-objects/{process_contract_id}", response_model=ProcessObjectResponse)
def update_process_object(
    process_contract_id: str,
    request: Request,
    payload: ProcessObjectUpdateRequest = Body(...),
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.update_process_object(process_contract_id, payload)
    )


@router.post("/semantic/process-objects/{process_contract_id}/publish")
def publish_process_object(process_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.publish_process_object(process_contract_id),
        structured_value_error=True,
    )


@router.post(
    "/semantic/process-objects/{process_contract_id}/validate",
    response_model=SemanticValidateActionResponse,
)
def validate_process_object(process_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.validate_process_object(process_contract_id),
        structured_value_error=True,
    )


@router.post(
    "/semantic/process-objects/{process_contract_id}/activate",
    response_model=ProcessObjectResponse,
)
def activate_process_object(process_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.activate_process_object(process_contract_id),
        structured_value_error=True,
    )


@router.post(
    "/semantic/process-objects/{process_contract_id}/deprecate",
    response_model=ProcessObjectResponse,
)
def deprecate_process_object(process_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.deprecate_process_object(process_contract_id),
        structured_value_error=True,
    )


@router.post("/semantic/dimensions", response_model=DimensionResponse)
def create_dimension(
    request: Request, payload: DimensionCreateRequest = Body(...)
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.create_dimension(payload))


@router.get("/semantic/dimensions")
def list_dimensions(
    request: Request,
    status: str | None = Query(default=None),
    lifecycle_status: str | None = Query(default=None),
    readiness_status: str | None = Query(default=None),
    detail: bool = Query(
        default=False, description="Return full detail instead of lightweight format."
    ),
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.list_dimensions(
            status=status,
            lifecycle_status=lifecycle_status,
            readiness_status=readiness_status,
            detail=detail,
        )
    )


@router.get("/semantic/dimensions/{dimension_contract_id}", response_model=DimensionResponse)
def get_dimension(dimension_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.read_dimension(dimension_contract_id))


@router.put("/semantic/dimensions/{dimension_contract_id}", response_model=DimensionResponse)
def update_dimension(
    dimension_contract_id: str,
    request: Request,
    payload: DimensionUpdateRequest = Body(...),
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.update_dimension(dimension_contract_id, payload)
    )


@router.post("/semantic/dimensions/{dimension_contract_id}/publish")
def publish_dimension(dimension_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.publish_dimension(dimension_contract_id),
        structured_value_error=True,
    )


@router.post(
    "/semantic/dimensions/{dimension_contract_id}/validate",
    response_model=SemanticValidateActionResponse,
)
def validate_dimension(dimension_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.validate_dimension(dimension_contract_id),
        structured_value_error=True,
    )


@router.post(
    "/semantic/dimensions/{dimension_contract_id}/activate", response_model=DimensionResponse
)
def activate_dimension(dimension_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.activate_dimension(dimension_contract_id),
        structured_value_error=True,
    )


@router.post(
    "/semantic/dimensions/{dimension_contract_id}/deprecate", response_model=DimensionResponse
)
def deprecate_dimension(dimension_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.deprecate_dimension(dimension_contract_id),
        structured_value_error=True,
    )


@router.post("/semantic/time", response_model=TimeResponse)
def create_time_semantic(
    request: Request, payload: TimeCreateRequest = Body(...)
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.create_time_semantic(payload))


@router.get("/semantic/time")
def list_time_semantics(
    request: Request,
    status: str | None = Query(default=None),
    lifecycle_status: str | None = Query(default=None),
    readiness_status: str | None = Query(default=None),
    detail: bool = Query(
        default=False, description="Return full detail instead of lightweight format."
    ),
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.list_time_semantics(
            status=status,
            lifecycle_status=lifecycle_status,
            readiness_status=readiness_status,
            detail=detail,
        )
    )


@router.get("/semantic/time/{time_contract_id}", response_model=TimeResponse)
def get_time_semantic(time_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.read_time_semantic(time_contract_id))


@router.put("/semantic/time/{time_contract_id}", response_model=TimeResponse)
def update_time_semantic(
    time_contract_id: str,
    request: Request,
    payload: TimeUpdateRequest = Body(...),
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.update_time_semantic(time_contract_id, payload)
    )


@router.post("/semantic/time/{time_contract_id}/publish")
def publish_time_semantic(time_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.publish_time_semantic(time_contract_id),
        structured_value_error=True,
    )


@router.post(
    "/semantic/time/{time_contract_id}/validate", response_model=SemanticValidateActionResponse
)
def validate_time_semantic(time_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.validate_time_semantic(time_contract_id),
        structured_value_error=True,
    )


@router.post("/semantic/time/{time_contract_id}/activate", response_model=TimeResponse)
def activate_time_semantic(time_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.activate_time_semantic(time_contract_id),
        structured_value_error=True,
    )


@router.post("/semantic/time/{time_contract_id}/deprecate", response_model=TimeResponse)
def deprecate_time_semantic(time_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.deprecate_time_semantic(time_contract_id),
        structured_value_error=True,
    )


@router.post("/semantic/enum-sets", response_model=EnumSetResponse)
def create_enum_set(request: Request, payload: EnumSetCreateRequest = Body(...)) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.create_enum_set(payload))


@router.get("/semantic/enum-sets")
def list_enum_sets(
    request: Request,
    status: str | None = Query(default=None),
    lifecycle_status: str | None = Query(default=None),
    readiness_status: str | None = Query(default=None),
    detail: bool = Query(
        default=False, description="Return full detail instead of lightweight format."
    ),
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.list_enum_sets(
            status=status,
            lifecycle_status=lifecycle_status,
            readiness_status=readiness_status,
            detail=detail,
        )
    )


@router.get("/semantic/enum-sets/{enum_set_contract_id}", response_model=EnumSetResponse)
def get_enum_set(enum_set_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.read_enum_set(enum_set_contract_id))


@router.put("/semantic/enum-sets/{enum_set_contract_id}", response_model=EnumSetResponse)
def update_enum_set(
    enum_set_contract_id: str,
    request: Request,
    payload: EnumSetUpdateRequest = Body(...),
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.update_enum_set(enum_set_contract_id, payload)
    )


@router.post("/semantic/enum-sets/{enum_set_contract_id}/publish")
def publish_enum_set(enum_set_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.publish_enum_set(enum_set_contract_id),
        structured_value_error=True,
    )


@router.post(
    "/semantic/enum-sets/{enum_set_contract_id}/validate",
    response_model=SemanticValidateActionResponse,
)
def validate_enum_set(enum_set_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.validate_enum_set(enum_set_contract_id),
        structured_value_error=True,
    )


@router.post("/semantic/enum-sets/{enum_set_contract_id}/activate", response_model=EnumSetResponse)
def activate_enum_set(enum_set_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.activate_enum_set(enum_set_contract_id),
        structured_value_error=True,
    )


@router.post("/semantic/enum-sets/{enum_set_contract_id}/deprecate", response_model=EnumSetResponse)
def deprecate_enum_set(enum_set_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.deprecate_enum_set(enum_set_contract_id),
        structured_value_error=True,
    )


@router.post("/semantic/bindings", response_model=TypedBindingResponse)
def create_typed_binding(
    request: Request, payload: TypedBindingCreateRequest = Body(...)
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.create_typed_binding(payload))


@router.get("/semantic/bindings")
def list_typed_bindings(
    request: Request,
    status: str | None = Query(default=None),
    lifecycle_status: str | None = Query(default=None),
    readiness_status: str | None = Query(default=None),
    detail: bool = Query(
        default=False, description="Return full detail instead of lightweight format."
    ),
) -> dict[str, Any]:
    return _run_route_action(
        lambda: get_services(request).semantic_service.list_typed_bindings(
            status=status,
            lifecycle_status=lifecycle_status,
            readiness_status=readiness_status,
            detail=detail,
        )
    )


@router.get("/semantic/bindings/{binding_id}", response_model=TypedBindingResponse)
def get_typed_binding(binding_id: str, request: Request) -> dict[str, Any]:
    return _run_route_action(
        lambda: get_services(request).semantic_service.read_typed_binding(binding_id)
    )


@router.put("/semantic/bindings/{binding_id}", response_model=TypedBindingResponse)
def update_typed_binding(
    binding_id: str,
    request: Request,
    payload: TypedBindingUpdateRequest = Body(...),
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.update_typed_binding(binding_id, payload))


@router.post("/semantic/bindings/{binding_id}/publish")
def publish_typed_binding(binding_id: str, request: Request) -> dict[str, Any]:
    return _run_route_action(
        lambda: get_services(request).semantic_service.publish_typed_binding(binding_id),
        structured_value_error=True,
    )


@router.post(
    "/semantic/bindings/{binding_id}/validate", response_model=SemanticValidateActionResponse
)
def validate_typed_binding(binding_id: str, request: Request) -> dict[str, Any]:
    return _run_route_action(
        lambda: get_services(request).semantic_service.validate_typed_binding(binding_id),
        structured_value_error=True,
    )


@router.post("/semantic/bindings/{binding_id}/activate", response_model=TypedBindingResponse)
def activate_typed_binding(binding_id: str, request: Request) -> dict[str, Any]:
    return _run_route_action(
        lambda: get_services(request).semantic_service.activate_typed_binding(binding_id),
        structured_value_error=True,
    )


@router.post("/semantic/bindings/{binding_id}/deprecate", response_model=TypedBindingResponse)
def deprecate_typed_binding(binding_id: str, request: Request) -> dict[str, Any]:
    return _run_route_action(
        lambda: get_services(request).semantic_service.deprecate_typed_binding(binding_id),
        structured_value_error=True,
    )


@router.post("/compiler/compatibility-profiles", response_model=CompatibilityProfileResponse)
def create_compatibility_profile(
    request: Request, payload: CompatibilityProfileCreateRequest = Body(...)
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.create_compatibility_profile(payload))


@router.get("/compiler/compatibility-profiles")
def list_compatibility_profiles(
    request: Request,
    status: str | None = Query(default=None),
    lifecycle_status: str | None = Query(default=None),
    readiness_status: str | None = Query(default=None),
    detail: bool = Query(
        default=False, description="Return full detail instead of lightweight format."
    ),
) -> dict[str, Any]:
    return _run_route_action(
        lambda: get_services(request).semantic_service.list_compatibility_profiles(
            status=status,
            lifecycle_status=lifecycle_status,
            readiness_status=readiness_status,
            detail=detail,
        )
    )


@router.get(
    "/compiler/compatibility-profiles/{profile_id}", response_model=CompatibilityProfileResponse
)
def get_compatibility_profile(profile_id: str, request: Request) -> dict[str, Any]:
    return _run_route_action(
        lambda: get_services(request).semantic_service.read_compatibility_profile(profile_id)
    )


@router.put(
    "/compiler/compatibility-profiles/{profile_id}", response_model=CompatibilityProfileResponse
)
def update_compatibility_profile(
    profile_id: str,
    request: Request,
    payload: CompatibilityProfileUpdateRequest = Body(...),
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.update_compatibility_profile(profile_id, payload)
    )


@router.post("/compiler/compatibility-profiles/{profile_id}/publish")
def publish_compatibility_profile(profile_id: str, request: Request) -> dict[str, Any]:
    return _run_route_action(
        lambda: get_services(request).semantic_service.publish_compatibility_profile(profile_id),
        structured_value_error=True,
    )


@router.post(
    "/compiler/compatibility-profiles/{profile_id}/validate",
    response_model=SemanticValidateActionResponse,
)
def validate_compatibility_profile(profile_id: str, request: Request) -> dict[str, Any]:
    return _run_route_action(
        lambda: get_services(request).semantic_service.validate_compatibility_profile(profile_id),
        structured_value_error=True,
    )


@router.post(
    "/compiler/compatibility-profiles/{profile_id}/activate",
    response_model=CompatibilityProfileResponse,
)
def activate_compatibility_profile(profile_id: str, request: Request) -> dict[str, Any]:
    return _run_route_action(
        lambda: get_services(request).semantic_service.activate_compatibility_profile(profile_id),
        structured_value_error=True,
    )


@router.post(
    "/compiler/compatibility-profiles/{profile_id}/deprecate",
    response_model=CompatibilityProfileResponse,
)
def deprecate_compatibility_profile(profile_id: str, request: Request) -> dict[str, Any]:
    return _run_route_action(
        lambda: get_services(request).semantic_service.deprecate_compatibility_profile(profile_id),
        structured_value_error=True,
    )
