from __future__ import annotations

from fastapi import APIRouter, Request

from marivo.api.deps import get_services
from marivo.api.models import (
    RouteEngineResponse,
    RouteResolveRequest,
    RouteResolveResponse,
    RoutingDetail,
)
from marivo.routing import RoutingIntent

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
        datasource = services.datasource_service.get_datasource(route.datasource_id)
        return RouteResolveResponse(
            resolved=True,
            failure_code=None,
            table_names=payload.table_names,
            engine=RouteEngineResponse(
                datasource_id=str(datasource["datasource_id"]),
                datasource_type=str(datasource["datasource_type"]),
                display_name=str(datasource["display_name"]),
            ),
            qualified_names=route.qualified_names,
            selection_reason=route.selection_reason,
            routing_detail=RoutingDetail.model_validate(route.routing_detail),
            capability_profile=None,
        )

    failure = resolution.failure
    return RouteResolveResponse(
        resolved=False,
        failure_code=None if failure is None else failure.code,
        table_names=payload.table_names,
        engine=None,
        qualified_names={},
        selection_reason=None if failure is None else failure.message,
        routing_detail=RoutingDetail.model_validate(failure.routing_detail if failure else {}),
        capability_profile=None,
    )
