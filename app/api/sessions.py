from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.deps import get_services, http_error
from app.api.models import SessionCreateRequest


router = APIRouter()


@router.post("/sessions")
def create_session(payload: SessionCreateRequest, request: Request) -> dict[str, object]:
    return get_services(request).service.create_session(
        goal=payload.goal,
        constraints=payload.constraints,
        budget=payload.budget,
        policy=payload.policy,
    )


@router.get("/sessions")
def list_sessions(request: Request, status: str | None = Query(default=None)) -> list[dict[str, object]]:
    return get_services(request).service.list_sessions(status=status)


@router.get("/sessions/{session_id}")
def get_session(session_id: str, request: Request) -> dict[str, object]:
    try:
        return get_services(request).service.get_session(session_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/sessions/{session_id}/steps/{step_type}")
def run_step(
    session_id: str,
    step_type: str,
    request: Request,
    body: dict[str, Any] | None = None,
) -> dict[str, object]:
    try:
        return get_services(request).service.run_step(session_id, step_type, params=body)
    except (KeyError, ValueError) as error:
        raise http_error(error) from error


@router.post("/sessions/{session_id}/workflow/watch-time-drop")
def run_watch_time_drop(session_id: str, request: Request) -> dict[str, object]:
    try:
        return get_services(request).service.run_watch_time_drop_workflow(session_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/sessions/{session_id}/evidence")
def evidence_graph(session_id: str, request: Request) -> dict[str, object]:
    try:
        return get_services(request).service.get_evidence_graph(session_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
