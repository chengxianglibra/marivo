from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.deps import get_services, http_error
from app.api.models import RouteResolveRequest
from app.routing import RoutingIntent

router = APIRouter()


@router.post("/routing/resolve")
def routing_resolve(payload: RouteResolveRequest, request: Request) -> dict[str, object]:
    services = get_services(request)
    try:
        routing_intent = None
        if payload.routing_intent is not None:
            routing_intent = RoutingIntent(**payload.routing_intent.model_dump())
        route = services.query_router.resolve_tables(
            payload.table_names,
            routing_intent=routing_intent,
        )
        engine = services.engine_service.get_engine(route.engine_id)
        return {
            "resolved": True,
            "table_names": payload.table_names,
            "engine": {
                "engine_id": engine["engine_id"],
                "engine_type": engine["engine_type"],
                "display_name": engine["display_name"],
            },
            "qualified_names": route.qualified_names,
            "selection_reason": route.selection_reason,
            "routing_detail": route.routing_detail,
            "capability_profile": (
                route.capability_profile.to_dict() if route.capability_profile is not None else None
            ),
        }
    except (KeyError, ValueError) as error:
        raise http_error(error) from error
