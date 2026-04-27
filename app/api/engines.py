from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.api.deps import get_services
from app.api.models import (
    EngineDeleteResponse,
    EngineRegisterRequest,
    EngineResponse,
    EngineUpdateRequest,
)
from app.registry.source_registry import DependencyError

router = APIRouter()


@router.post("/engines", response_model=EngineResponse)
def register_engine(payload: EngineRegisterRequest, request: Request) -> EngineResponse:
    services = get_services(request)
    try:
        return EngineResponse.model_validate(
            services.engine_service.register_engine(
                engine_type=payload.engine_type,
                display_name=payload.display_name,
                connection=payload.connection,
                auth=payload.auth.model_dump(exclude_none=True),
                default_namespace=(
                    payload.default_namespace.model_dump(by_alias=True)
                    if payload.default_namespace is not None
                    else None
                ),
                deployment_capabilities=payload.deployment_capabilities.model_dump(
                    exclude_unset=True
                ),
                policy=payload.policy.model_dump(),
            )
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/engines", response_model=list[EngineResponse])
def list_engines(request: Request) -> list[EngineResponse]:
    return [
        EngineResponse.model_validate(engine)
        for engine in get_services(request).engine_service.list_engines()
    ]


@router.get("/engines/{engine_id}", response_model=EngineResponse)
def get_engine(engine_id: str, request: Request) -> EngineResponse:
    try:
        return EngineResponse.model_validate(
            get_services(request).engine_service.get_engine(engine_id)
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.put("/engines/{engine_id}", response_model=EngineResponse)
def update_engine(engine_id: str, payload: EngineUpdateRequest, request: Request) -> EngineResponse:
    default_namespace = None
    if "default_namespace" in payload.model_fields_set:
        default_namespace = (
            payload.default_namespace.model_dump(by_alias=True)
            if payload.default_namespace is not None
            else {}
        )
    try:
        return EngineResponse.model_validate(
            get_services(request).engine_service.update_engine(
                engine_id,
                display_name=payload.display_name,
                connection=payload.connection,
                auth=payload.auth.model_dump(exclude_none=True)
                if payload.auth is not None
                else None,
                default_namespace=default_namespace,
                deployment_capabilities=(
                    payload.deployment_capabilities.model_dump(exclude_unset=True)
                    if payload.deployment_capabilities is not None
                    else None
                ),
                policy=payload.policy.model_dump() if payload.policy is not None else None,
            )
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.delete("/engines/{engine_id}", response_model=EngineDeleteResponse)
def delete_engine(engine_id: str, request: Request) -> EngineDeleteResponse:
    try:
        get_services(request).engine_service.delete_engine(engine_id)
        return EngineDeleteResponse(status="deleted", engine_id=engine_id)
    except DependencyError as error:
        raise HTTPException(
            status_code=409, detail={"message": str(error), "dependencies": error.dependencies}
        ) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
