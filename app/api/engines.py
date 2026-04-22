from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.deps import get_services
from app.api.models import BindingCreateRequest, EngineRegisterRequest

router = APIRouter()


@router.post("/engines")
def register_engine(payload: EngineRegisterRequest, request: Request) -> dict[str, object]:
    services = get_services(request)
    try:
        return services.engine_service.register_engine(
            engine_type=payload.engine_type,
            display_name=payload.display_name,
            connection=payload.connection,
            default_namespace=(
                payload.default_namespace.model_dump(by_alias=True)
                if payload.default_namespace is not None
                else None
            ),
            deployment_capabilities=payload.deployment_capabilities.model_dump(exclude_unset=True),
            policy=payload.policy.model_dump(),
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/engines")
def list_engines(request: Request) -> list[dict[str, object]]:
    return get_services(request).engine_service.list_engines()


@router.get("/engines/{engine_id}")
def get_engine(engine_id: str, request: Request) -> dict[str, object]:
    try:
        return get_services(request).engine_service.get_engine(engine_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/bindings")
def create_binding(payload: BindingCreateRequest, request: Request) -> dict[str, object]:
    services = get_services(request)
    try:
        return services.binding_service.create_binding(
            source_id=payload.source_id,
            engine_id=payload.engine_id,
            priority=payload.priority,
            namespace=payload.namespace,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/bindings")
def list_bindings(
    request: Request,
    source_id: str | None = Query(default=None),
    engine_id: str | None = Query(default=None),
) -> list[dict[str, object]]:
    return get_services(request).binding_service.list_bindings(
        source_id=source_id, engine_id=engine_id
    )


@router.get("/bindings/{binding_id}")
def get_binding(binding_id: str, request: Request) -> dict[str, object]:
    try:
        return get_services(request).binding_service.get_binding(binding_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.delete("/bindings/{binding_id}")
def delete_binding(binding_id: str, request: Request) -> dict[str, str]:
    try:
        get_services(request).binding_service.delete_binding(binding_id)
        return {"status": "deleted", "binding_id": binding_id}
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/sources/{source_id}/engines")
def list_source_engines(source_id: str, request: Request) -> list[dict[str, object]]:
    services = get_services(request)
    try:
        services.source_service.get_source(source_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return services.binding_service.get_engines_for_source(source_id)
