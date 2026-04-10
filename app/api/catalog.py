from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.deps import get_services
from app.api.models import CatalogObjectDetail, CatalogSearchResult
from app.semantic_runtime import (
    SemanticRuntimeInvalidRefError,
    SemanticRuntimeNotFoundError,
    SemanticRuntimeUnpublishedError,
)

router = APIRouter()


@router.get("/catalog/search", response_model=list[CatalogSearchResult])
def catalog_search(
    request: Request,
    q: str = Query(..., min_length=1),
    type: str | None = Query(default=None),
) -> list[dict[str, object]]:
    try:
        return get_services(request).catalog_runtime.search(q, object_type=type)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/catalog/objects/{object_kind}/{object_id}", response_model=CatalogObjectDetail)
def get_catalog_object_detail(
    object_kind: str, object_id: str, request: Request
) -> dict[str, object]:
    try:
        return get_services(request).catalog_runtime.get_catalog_object_detail(
            object_kind, object_id
        )
    except (
        SemanticRuntimeInvalidRefError,
        SemanticRuntimeNotFoundError,
        SemanticRuntimeUnpublishedError,
    ) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/semantic/resolve/{name}")
def resolve_term(name: str, request: Request) -> dict[str, object]:
    try:
        return get_services(request).catalog_runtime.resolve(name)
    except SemanticRuntimeInvalidRefError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except (SemanticRuntimeNotFoundError, SemanticRuntimeUnpublishedError, KeyError) as error:
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
