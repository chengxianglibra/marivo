from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.api.deps import get_services
from app.api.models import EngineRegisterRequest

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
