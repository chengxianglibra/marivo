from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from fastapi import APIRouter, Body, HTTPException, Query, Request
from pydantic import ValidationError

from app.api.deps import get_services
from app.api.errors import (
    GuidedValidationError,
    build_validation_error_payload,
    sanitize_validation_errors,
)
from app.api.models import (
    CompatibilityProfileCreateRequest,
    CompatibilityProfileUpdateRequest,
    DimensionCreateRequest,
    DimensionUpdateRequest,
    EnumSetCreateRequest,
    EnumSetUpdateRequest,
    ProcessObjectCreateRequest,
    ProcessObjectUpdateRequest,
    TimeCreateRequest,
    TimeUpdateRequest,
    TypedBindingCreateRequest,
    TypedBindingUpdateRequest,
    TypedEntityCreateRequest,
    TypedEntityListResponse,
    TypedEntityResponse,
    TypedEntityUpdateRequest,
    TypedMetricCreateRequest,
    TypedMetricListResponse,
    TypedMetricResponse,
    TypedMetricUpdateRequest,
)

router = APIRouter()

ActionResultT = TypeVar("ActionResultT")
PayloadParser = Callable[[dict[str, Any]], Any]


def _run_route_action(  # noqa: UP047
    action: Callable[[], ActionResultT],
    *,
    value_error_status: int = 422,
    structured_value_error: bool = False,
) -> ActionResultT:
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


def _parse_payload(payload: dict[str, Any], parser: PayloadParser, request: Request) -> Any:
    try:
        return parser(payload)
    except ValidationError as error:
        raise GuidedValidationError(
            build_validation_error_payload(request, sanitize_validation_errors(error))
        ) from error


def _handle_create(
    request: Request,
    payload: dict[str, Any],
    *,
    parser: PayloadParser,
    action: Callable[[Any], dict[str, Any]],
) -> dict[str, Any]:
    parsed = _parse_payload(payload, parser, request)
    return _run_route_action(lambda: action(parsed))


def _handle_update(
    request: Request,
    payload: dict[str, Any],
    *,
    parser: PayloadParser,
    action: Callable[[Any], dict[str, Any]],
) -> dict[str, Any]:
    parsed = _parse_payload(payload, parser, request)
    return _run_route_action(lambda: action(parsed))


@router.post("/semantic/entities", response_model=TypedEntityResponse)
def create_entity(
    request: Request, payload: TypedEntityCreateRequest = Body(...)
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.create_typed_entity(payload))


@router.get("/semantic/entities", response_model=TypedEntityListResponse)
def list_entities(
    request: Request,
    status: str | None = Query(default=None),
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.list_typed_entities(status=status))


@router.get("/semantic/entities/{entity_id}", response_model=TypedEntityResponse)
def get_entity(
    entity_id: str,
    request: Request,
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.get_typed_entity(entity_id))


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


@router.post("/semantic/metrics", response_model=TypedMetricResponse)
def create_metric(
    request: Request, payload: TypedMetricCreateRequest = Body(...)
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.create_typed_metric(payload))


@router.get("/semantic/metrics", response_model=TypedMetricListResponse)
def list_metrics(
    request: Request,
    status: str | None = Query(default=None),
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.list_typed_metrics(status=status))


@router.get("/semantic/metrics/{metric_id}", response_model=TypedMetricResponse)
def get_metric(
    metric_id: str,
    request: Request,
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.get_typed_metric(metric_id))


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


@router.post("/semantic/process-objects")
def create_process_object(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _handle_create(
        request,
        payload,
        parser=ProcessObjectCreateRequest.model_validate,
        action=semantic_service.create_process_object,
    )


@router.get("/semantic/process-objects")
def list_process_objects(
    request: Request, status: str | None = Query(default=None)
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.list_process_objects(status=status))


@router.get("/semantic/process-objects/{process_contract_id}")
def get_process_object(process_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.get_process_object(process_contract_id))


@router.put("/semantic/process-objects/{process_contract_id}")
def update_process_object(
    process_contract_id: str, request: Request, payload: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _handle_update(
        request,
        payload,
        parser=ProcessObjectUpdateRequest.model_validate,
        action=lambda parsed: semantic_service.update_process_object(process_contract_id, parsed),
    )


@router.post("/semantic/process-objects/{process_contract_id}/publish")
def publish_process_object(process_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.publish_process_object(process_contract_id),
        structured_value_error=True,
    )


@router.post("/semantic/dimensions")
def create_dimension(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _handle_create(
        request,
        payload,
        parser=DimensionCreateRequest.model_validate,
        action=semantic_service.create_dimension,
    )


@router.get("/semantic/dimensions")
def list_dimensions(request: Request, status: str | None = Query(default=None)) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.list_dimensions(status=status))


@router.get("/semantic/dimensions/{dimension_contract_id}")
def get_dimension(dimension_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.get_dimension(dimension_contract_id))


@router.put("/semantic/dimensions/{dimension_contract_id}")
def update_dimension(
    dimension_contract_id: str, request: Request, payload: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _handle_update(
        request,
        payload,
        parser=DimensionUpdateRequest.model_validate,
        action=lambda parsed: semantic_service.update_dimension(dimension_contract_id, parsed),
    )


@router.post("/semantic/dimensions/{dimension_contract_id}/publish")
def publish_dimension(dimension_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.publish_dimension(dimension_contract_id),
        structured_value_error=True,
    )


@router.post("/semantic/time")
def create_time_semantic(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _handle_create(
        request,
        payload,
        parser=TimeCreateRequest.model_validate,
        action=semantic_service.create_time_semantic,
    )


@router.get("/semantic/time")
def list_time_semantics(
    request: Request, status: str | None = Query(default=None)
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.list_time_semantics(status=status))


@router.get("/semantic/time/{time_contract_id}")
def get_time_semantic(time_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.get_time_semantic(time_contract_id))


@router.put("/semantic/time/{time_contract_id}")
def update_time_semantic(
    time_contract_id: str, request: Request, payload: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _handle_update(
        request,
        payload,
        parser=TimeUpdateRequest.model_validate,
        action=lambda parsed: semantic_service.update_time_semantic(time_contract_id, parsed),
    )


@router.post("/semantic/time/{time_contract_id}/publish")
def publish_time_semantic(time_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.publish_time_semantic(time_contract_id),
        structured_value_error=True,
    )


@router.post("/semantic/enum-sets")
def create_enum_set(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _handle_create(
        request,
        payload,
        parser=EnumSetCreateRequest.model_validate,
        action=semantic_service.create_enum_set,
    )


@router.get("/semantic/enum-sets")
def list_enum_sets(request: Request, status: str | None = Query(default=None)) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.list_enum_sets(status=status))


@router.get("/semantic/enum-sets/{enum_set_contract_id}")
def get_enum_set(enum_set_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.get_enum_set(enum_set_contract_id))


@router.put("/semantic/enum-sets/{enum_set_contract_id}")
def update_enum_set(
    enum_set_contract_id: str, request: Request, payload: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _handle_update(
        request,
        payload,
        parser=EnumSetUpdateRequest.model_validate,
        action=lambda parsed: semantic_service.update_enum_set(enum_set_contract_id, parsed),
    )


@router.post("/semantic/enum-sets/{enum_set_contract_id}/publish")
def publish_enum_set(enum_set_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.publish_enum_set(enum_set_contract_id),
        structured_value_error=True,
    )


@router.post("/semantic/bindings")
def create_typed_binding(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _handle_create(
        request,
        payload,
        parser=TypedBindingCreateRequest.model_validate,
        action=semantic_service.create_typed_binding,
    )


@router.get("/semantic/bindings")
def list_typed_bindings(
    request: Request, status: str | None = Query(default=None)
) -> dict[str, Any]:
    return _run_route_action(
        lambda: get_services(request).semantic_service.list_typed_bindings(status=status)
    )


@router.get("/semantic/bindings/{binding_id}")
def get_typed_binding(binding_id: str, request: Request) -> dict[str, Any]:
    return _run_route_action(
        lambda: get_services(request).semantic_service.get_typed_binding(binding_id)
    )


@router.put("/semantic/bindings/{binding_id}")
def update_typed_binding(
    binding_id: str, request: Request, payload: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _handle_update(
        request,
        payload,
        parser=TypedBindingUpdateRequest.model_validate,
        action=lambda parsed: semantic_service.update_typed_binding(binding_id, parsed),
    )


@router.post("/semantic/bindings/{binding_id}/publish")
def publish_typed_binding(binding_id: str, request: Request) -> dict[str, Any]:
    return _run_route_action(
        lambda: get_services(request).semantic_service.publish_typed_binding(binding_id),
        structured_value_error=True,
    )


@router.post("/compiler/compatibility-profiles")
def create_compatibility_profile(
    request: Request, payload: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _handle_create(
        request,
        payload,
        parser=CompatibilityProfileCreateRequest.model_validate,
        action=semantic_service.create_compatibility_profile,
    )


@router.get("/compiler/compatibility-profiles")
def list_compatibility_profiles(
    request: Request, status: str | None = Query(default=None)
) -> dict[str, Any]:
    return _run_route_action(
        lambda: get_services(request).semantic_service.list_compatibility_profiles(status=status)
    )


@router.get("/compiler/compatibility-profiles/{profile_id}")
def get_compatibility_profile(profile_id: str, request: Request) -> dict[str, Any]:
    return _run_route_action(
        lambda: get_services(request).semantic_service.get_compatibility_profile(profile_id)
    )


@router.put("/compiler/compatibility-profiles/{profile_id}")
def update_compatibility_profile(
    profile_id: str, request: Request, payload: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _handle_update(
        request,
        payload,
        parser=CompatibilityProfileUpdateRequest.model_validate,
        action=lambda parsed: semantic_service.update_compatibility_profile(profile_id, parsed),
    )


@router.post("/compiler/compatibility-profiles/{profile_id}/publish")
def publish_compatibility_profile(profile_id: str, request: Request) -> dict[str, Any]:
    return _run_route_action(
        lambda: get_services(request).semantic_service.publish_compatibility_profile(profile_id),
        structured_value_error=True,
    )
