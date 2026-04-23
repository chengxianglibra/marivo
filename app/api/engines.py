from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.api.deps import get_services
from app.api.models import EngineRegisterRequest, EngineResponse

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
                default_namespace=(
                    payload.default_namespace.model_dump(by_alias=True)
                    if payload.default_namespace is not None
                    else None
                ),
                deployment_capabilities=payload.deployment_capabilities.model_dump(exclude_unset=True),
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
        return EngineResponse.model_validate(get_services(request).engine_service.get_engine(engine_id))
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
