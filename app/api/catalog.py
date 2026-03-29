from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.deps import get_services

router = APIRouter()


@router.get("/catalog/search")
def catalog_search(
    request: Request,
    q: str = Query(..., min_length=1),
    type: str | None = Query(default=None),
) -> list[dict[str, object]]:
    return get_services(request).catalog_runtime.search(q, object_type=type)


@router.get("/semantic/resolve/{name}")
def resolve_term(name: str, request: Request) -> dict[str, object]:
    try:
        return get_services(request).catalog_runtime.resolve(name)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/sessions/{session_id}/planner-context")
def planner_context(session_id: str, request: Request) -> dict[str, object]:
    services = get_services(request)
    try:
        services.service._assert_session_exists(session_id)
        return services.catalog_runtime.planner_context(session_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/catalog/graph")
def catalog_graph(
    request: Request,
    root: str = Query(...),
    depth: int = Query(default=2, ge=1, le=5),
) -> dict[str, object]:
    try:
        return get_services(request).catalog_runtime.graph(root, depth)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
