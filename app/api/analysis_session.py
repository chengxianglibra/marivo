"""Session API — analysis session and semantic snapshot routes."""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Body, HTTPException, Request

from app.semantic_service_v2.session import SessionService

router = APIRouter(prefix="/analysis-sessions", tags=["analysis-sessions"])


def _get_session_service(request: Request) -> SessionService:
    return cast("SessionService", request.app.state.session_service)


@router.post("")
def create_session(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    svc = _get_session_service(request)
    requesting_user = payload.get("requesting_user")
    if not requesting_user:
        raise HTTPException(status_code=400, detail="requesting_user is required")
    return svc.create_session(requesting_user)


@router.get("/{session_id}")
def get_session(session_id: str, request: Request) -> dict[str, Any]:
    svc = _get_session_service(request)
    return svc.get_session(session_id)


@router.post("/{session_id}/end")
def end_session(session_id: str, request: Request) -> dict[str, Any]:
    svc = _get_session_service(request)
    return svc.end_session(session_id)
