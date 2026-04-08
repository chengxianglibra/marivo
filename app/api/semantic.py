from __future__ import annotations

from collections.abc import Callable
from typing import Any, NoReturn, TypeVar

from fastapi import APIRouter, Body, HTTPException, Query, Request
from pydantic import ValidationError

from app.api.deps import get_services
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
    TypedEntityUpdateRequest,
    TypedMetricCreateRequest,
    TypedMetricUpdateRequest,
)
from app.api.models._legacy import MappingCreateRequest

router = APIRouter()

PayloadParser = Callable[[dict[str, Any]], Any]
ActionResultT = TypeVar("ActionResultT")


def _validation_detail(error: ValidationError) -> list[Any]:
    return error.errors(include_url=False)


def _raise_http_422_from_validation(error: ValidationError) -> NoReturn:
    raise HTTPException(status_code=422, detail=_validation_detail(error)) from error


def _run_route_action(  # noqa: UP047
    action: Callable[[], ActionResultT],
    *,
    value_error_status: int = 422,
) -> ActionResultT:
    try:
        return action()
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=value_error_status, detail=str(error)) from error


def _parse_payload(payload: dict[str, Any], parser: PayloadParser) -> Any:
    try:
        return parser(payload)
    except ValidationError as error:
        _raise_http_422_from_validation(error)


def _handle_create(
    payload: dict[str, Any],
    *,
    parser: PayloadParser,
    action: Callable[[Any], dict[str, Any]],
) -> dict[str, Any]:
    parsed = _parse_payload(payload, parser)
    return _run_route_action(lambda: action(parsed))


def _handle_update(
    payload: dict[str, Any],
    *,
    parser: PayloadParser,
    action: Callable[[Any], dict[str, Any]],
) -> dict[str, Any]:
    parsed = _parse_payload(payload, parser)
    return _run_route_action(lambda: action(parsed))


@router.post("/semantic/entities")
def create_entity(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _handle_create(
        payload,
        parser=TypedEntityCreateRequest.model_validate,
        action=semantic_service.create_typed_entity,
    )


@router.get("/semantic/entities")
def list_entities(
    request: Request,
    status: str | None = Query(default=None),
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.list_typed_entities(status=status))


@router.get("/semantic/entities/{entity_id}")
def get_entity(
    entity_id: str,
    request: Request,
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.get_typed_entity(entity_id))


@router.put("/semantic/entities/{entity_id}")
def update_entity(
    entity_id: str, request: Request, payload: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _handle_update(
        payload,
        parser=TypedEntityUpdateRequest.model_validate,
        action=lambda parsed: semantic_service.update_typed_entity(entity_id, parsed),
    )


@router.post("/semantic/entities/{entity_id}/publish")
def publish_entity(entity_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.publish_typed_entity(entity_id))


@router.post("/semantic/metrics")
def create_metric(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _handle_create(
        payload,
        parser=TypedMetricCreateRequest.model_validate,
        action=semantic_service.create_typed_metric,
    )


@router.get("/semantic/metrics")
def list_metrics(
    request: Request,
    status: str | None = Query(default=None),
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.list_typed_metrics(status=status))


@router.get("/semantic/metrics/{metric_id}")
def get_metric(
    metric_id: str,
    request: Request,
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.get_typed_metric(metric_id))


@router.put("/semantic/metrics/{metric_id}")
def update_metric(
    metric_id: str, request: Request, payload: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _handle_update(
        payload,
        parser=TypedMetricUpdateRequest.model_validate,
        action=lambda parsed: semantic_service.update_typed_metric(metric_id, parsed),
    )


@router.post("/semantic/metrics/{metric_id}/publish")
def publish_metric(metric_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.publish_typed_metric(metric_id))


@router.post("/semantic/process-objects")
def create_process_object(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _handle_create(
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
        payload,
        parser=ProcessObjectUpdateRequest.model_validate,
        action=lambda parsed: semantic_service.update_process_object(process_contract_id, parsed),
    )


@router.post("/semantic/process-objects/{process_contract_id}/publish")
def publish_process_object(process_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.publish_process_object(process_contract_id))


@router.post("/semantic/dimensions")
def create_dimension(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _handle_create(
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
        payload,
        parser=DimensionUpdateRequest.model_validate,
        action=lambda parsed: semantic_service.update_dimension(dimension_contract_id, parsed),
    )


@router.post("/semantic/dimensions/{dimension_contract_id}/publish")
def publish_dimension(dimension_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.publish_dimension(dimension_contract_id))


@router.post("/semantic/time")
def create_time_semantic(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _handle_create(
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
        payload,
        parser=TimeUpdateRequest.model_validate,
        action=lambda parsed: semantic_service.update_time_semantic(time_contract_id, parsed),
    )


@router.post("/semantic/time/{time_contract_id}/publish")
def publish_time_semantic(time_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.publish_time_semantic(time_contract_id))


@router.post("/semantic/enum-sets")
def create_enum_set(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _handle_create(
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
        payload,
        parser=EnumSetUpdateRequest.model_validate,
        action=lambda parsed: semantic_service.update_enum_set(enum_set_contract_id, parsed),
    )


@router.post("/semantic/enum-sets/{enum_set_contract_id}/publish")
def publish_enum_set(enum_set_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.publish_enum_set(enum_set_contract_id))


@router.post("/semantic/bindings")
def create_typed_binding(payload: TypedBindingCreateRequest, request: Request) -> dict[str, Any]:
    return _run_route_action(
        lambda: get_services(request).semantic_service.create_typed_binding(payload)
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
    binding_id: str, payload: TypedBindingUpdateRequest, request: Request
) -> dict[str, Any]:
    return _run_route_action(
        lambda: get_services(request).semantic_service.update_typed_binding(binding_id, payload)
    )


@router.post("/semantic/bindings/{binding_id}/publish")
def publish_typed_binding(binding_id: str, request: Request) -> dict[str, Any]:
    return _run_route_action(
        lambda: get_services(request).semantic_service.publish_typed_binding(binding_id)
    )


@router.post("/compiler/compatibility-profiles")
def create_compatibility_profile(
    payload: CompatibilityProfileCreateRequest, request: Request
) -> dict[str, Any]:
    return _run_route_action(
        lambda: get_services(request).semantic_service.create_compatibility_profile(payload)
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
    profile_id: str, payload: CompatibilityProfileUpdateRequest, request: Request
) -> dict[str, Any]:
    return _run_route_action(
        lambda: get_services(request).semantic_service.update_compatibility_profile(
            profile_id, payload
        )
    )


@router.post("/compiler/compatibility-profiles/{profile_id}/publish")
def publish_compatibility_profile(profile_id: str, request: Request) -> dict[str, Any]:
    return _run_route_action(
        lambda: get_services(request).semantic_service.publish_compatibility_profile(profile_id)
    )


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
    def _action() -> dict[str, str]:
        get_services(request).semantic_service.delete_mapping(mapping_id)
        return {"status": "deleted", "mapping_id": mapping_id}

    return _run_route_action(_action)
