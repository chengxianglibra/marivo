from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.deps import get_services
from app.api.models import (
    RouteCapabilityProfileResponse,
    RouteEngineResponse,
    RouteResolveRequest,
    RouteResolveResponse,
)
from app.routing import RoutingIntent

router = APIRouter()


@router.post("/routing/resolve", response_model=RouteResolveResponse)
def routing_resolve(payload: RouteResolveRequest, request: Request) -> RouteResolveResponse:
    services = get_services(request)
    routing_intent = None
    if payload.routing_intent is not None:
        routing_intent = RoutingIntent(**payload.routing_intent.model_dump())
    resolution = services.query_router.resolve_route(
        payload.table_names,
        routing_intent=routing_intent,
        include_runtime_engine=False,
    )
    if resolution.resolved:
        route = resolution.require_route()
        engine = services.engine_service.get_engine(route.engine_id)
        return RouteResolveResponse(
            resolved=True,
            failure_code=None,
            table_names=payload.table_names,
            engine=RouteEngineResponse(
                engine_id=str(engine["engine_id"]),
                engine_type=str(engine["engine_type"]),
                display_name=str(engine["display_name"]),
            ),
            qualified_names=route.qualified_names,
            selection_reason=route.selection_reason,
            routing_detail=route.routing_detail,
            capability_profile=(
                RouteCapabilityProfileResponse(**route.capability_profile.to_dict())
                if route.capability_profile is not None
                else None
            ),
        )

    failure = resolution.failure
    return RouteResolveResponse(
        resolved=False,
        failure_code=None if failure is None else failure.code,
        table_names=payload.table_names,
        engine=None,
        qualified_names={},
        selection_reason=None if failure is None else failure.message,
        routing_detail={} if failure is None else failure.routing_detail,
        capability_profile=None,
    )
