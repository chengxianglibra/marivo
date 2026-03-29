from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.deps import get_services

router = APIRouter()


@router.get("/health")
def health(request: Request) -> dict[str, str]:
    services = get_services(request)
    return {"status": "ok", "db_path": str(services.resolved_path)}


@router.get("/catalog")
def catalog(request: Request) -> dict[str, object]:
    return get_services(request).service.discover_catalog()
