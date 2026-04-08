from __future__ import annotations

from collections.abc import Callable
from typing import Any, NoReturn, TypeVar

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
    semantic_service = get_services(request).semantic_service
    return _handle_create(
        payload,
        parser=_parse_entity_create,
        action=lambda parsed: (
            semantic_service.create_typed_entity(parsed)
            if isinstance(parsed, TypedEntityCreateRequest)
            else semantic_service.create_entity(
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
        ),
    )


@router.get("/semantic/entities")
def list_entities(
    request: Request,
    status: str | None = Query(default=None),
    surface: str | None = Query(default=None),
) -> dict[str, Any] | list[dict[str, Any]]:
    semantic_service = get_services(request).semantic_service

    def _action() -> dict[str, Any] | list[dict[str, Any]]:
        if surface == "typed":
            return semantic_service.list_typed_entities(status=status)
        return semantic_service.list_entities(status=status)

    return _run_route_action(_action)


@router.get("/semantic/entities/{entity_id}")
def get_entity(
    entity_id: str,
    request: Request,
    surface: str | None = Query(default=None),
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service

    def _action() -> dict[str, Any]:
        if surface == "typed" or entity_id.startswith("entc_"):
            return semantic_service.get_typed_entity(entity_id)
        try:
            return semantic_service.get_entity(entity_id)
        except KeyError:
            return semantic_service.get_typed_entity(entity_id)

    return _run_route_action(_action)


@router.put("/semantic/entities/{entity_id}")
def update_entity(
    entity_id: str, request: Request, payload: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _handle_update(
        payload,
        parser=lambda raw_payload: _parse_entity_update(entity_id, raw_payload),
        action=lambda parsed: (
            semantic_service.update_typed_entity(entity_id, parsed)
            if isinstance(parsed, TypedEntityUpdateRequest)
            else semantic_service.update_entity(entity_id, **parsed.model_dump(exclude_none=True))
        ),
    )


@router.patch("/semantic/entities/{entity_id}/properties")
def patch_entity_properties(
    entity_id: str, payload: EntityPropertiesPatchRequest, request: Request
) -> dict[str, Any]:
    return _run_route_action(
        lambda: get_services(request).semantic_service.patch_entity_properties(
            entity_id, payload.properties
        )
    )


@router.post("/semantic/entities/{entity_id}/publish")
def publish_entity(entity_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service

    def _action() -> dict[str, Any]:
        if entity_id.startswith("entc_"):
            return semantic_service.publish_typed_entity(entity_id)
        try:
            return semantic_service.publish_entity(entity_id)
        except KeyError:
            return semantic_service.publish_typed_entity(entity_id)

    return _run_route_action(_action)


@router.post("/semantic/metrics")
def create_metric(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _handle_create(
        payload,
        parser=_parse_metric_create,
        action=lambda parsed: (
            semantic_service.create_typed_metric(parsed)
            if isinstance(parsed, TypedMetricCreateRequest)
            else semantic_service.create_metric(
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
        ),
    )


@router.get("/semantic/metrics")
def list_metrics(
    request: Request,
    status: str | None = Query(default=None),
    surface: str | None = Query(default=None),
) -> dict[str, Any] | list[dict[str, Any]]:
    semantic_service = get_services(request).semantic_service

    def _action() -> dict[str, Any] | list[dict[str, Any]]:
        if surface == "typed":
            return semantic_service.list_typed_metrics(status=status)
        return semantic_service.list_metrics(status=status)

    return _run_route_action(_action)


@router.get("/semantic/metrics/{metric_id}")
def get_metric(
    metric_id: str,
    request: Request,
    surface: str | None = Query(default=None),
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service

    def _action() -> dict[str, Any]:
        if surface == "typed" or metric_id.startswith("metc_"):
            return semantic_service.get_typed_metric(metric_id)
        try:
            return semantic_service.get_metric(metric_id)
        except KeyError:
            return semantic_service.get_typed_metric(metric_id)

    return _run_route_action(_action)


@router.put("/semantic/metrics/{metric_id}")
def update_metric(
    metric_id: str, request: Request, payload: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _handle_update(
        payload,
        parser=lambda raw_payload: _parse_metric_update(metric_id, raw_payload),
        action=lambda parsed: (
            semantic_service.update_typed_metric(metric_id, parsed)
            if isinstance(parsed, TypedMetricUpdateRequest)
            else semantic_service.update_metric(metric_id, **parsed.model_dump(exclude_unset=True))
        ),
    )


@router.post("/semantic/metrics/{metric_id}/publish")
def publish_metric(metric_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service

    def _action() -> dict[str, Any]:
        if metric_id.startswith("metc_"):
            return semantic_service.publish_typed_metric(metric_id)
        try:
            return semantic_service.publish_metric(metric_id)
        except KeyError:
            return semantic_service.publish_typed_metric(metric_id)

    return _run_route_action(_action)


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
