from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.models import CatalogObjectDetail, CatalogSearchResult

router = APIRouter()


@router.get("/catalog/search", response_model=list[CatalogSearchResult])
def catalog_search(
    request: Request,
    q: str = Query(..., min_length=1),
    type: str | None = Query(default=None),
    readiness: str = Query(default="ready"),
) -> list[dict[str, object]]:
    # NOTE: catalog_runtime removed during OSI v2 migration; see Task 7
    raise HTTPException(status_code=501, detail="Catalog search temporarily disabled")


@router.get("/catalog/objects/{object_kind}/{object_id}", response_model=CatalogObjectDetail)
def get_catalog_object_detail(
    object_kind: str, object_id: str, request: Request
) -> dict[str, object]:
    # NOTE: catalog_runtime removed during OSI v2 migration; see Task 7
    raise HTTPException(status_code=501, detail="Catalog object detail temporarily disabled")


@router.get("/semantic/resolve/{name}")
def resolve_term(name: str, request: Request) -> dict[str, object]:
    # NOTE: catalog_runtime removed during OSI v2 migration; see Task 7
    raise HTTPException(status_code=501, detail="Term resolution temporarily disabled")


@router.get("/sessions/{session_id}/planner-context")
def planner_context(session_id: str, request: Request) -> dict[str, object]:
    # NOTE: catalog_runtime removed during OSI v2 migration; see Task 7
    raise HTTPException(status_code=501, detail="Planner context temporarily disabled")


@router.get("/catalog/graph")
def catalog_graph(
    request: Request,
    root: str = Query(...),
    depth: int = Query(default=2, ge=1, le=5),
) -> dict[str, object]:
    # NOTE: catalog_runtime removed during OSI v2 migration; see Task 7
    raise HTTPException(status_code=501, detail="Catalog graph temporarily disabled")
