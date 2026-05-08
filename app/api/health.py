from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.deps import get_services

router = APIRouter()


@router.get("/health")
def health(request: Request) -> dict[str, str]:
    get_services(request)
    return {"status": "ok"}


@router.get("/catalog")
def catalog(request: Request) -> dict[str, object]:
    return get_services(request).runtime.discover_catalog()
