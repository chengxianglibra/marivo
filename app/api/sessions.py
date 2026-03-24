from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.deps import get_services, http_error
from app.api.models import AttributeChangeStep, SessionCreateRequest
from app.reflection.context import build_reflection_context


router = APIRouter()


@router.post("/sessions")
def create_session(payload: SessionCreateRequest, request: Request) -> dict[str, object]:
    return get_services(request).service.create_session(
        goal=payload.goal,
        constraints=payload.constraints,
        budget=payload.budget,
        policy=payload.policy,
        raw_filter=payload.raw_filter,
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


@router.get("/sessions/{session_id}/reflection-context")
def get_reflection_context(
    session_id: str,
    request: Request,
    plan_id: str | None = Query(default=None),
) -> dict[str, object]:
    services = get_services(request)
    if not services.reflection_enabled:
        raise HTTPException(status_code=404, detail="Reflection context is disabled")
    try:
        return build_reflection_context(services.metadata_store, session_id, plan_id=plan_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/sessions/{session_id}/steps/attribute_change")
def run_attribute_change(
    session_id: str,
    payload: AttributeChangeStep,
    request: Request,
) -> dict[str, object]:
    try:
        return get_services(request).service.run_step(
            session_id,
            "attribute_change",
            params=payload.model_dump(exclude_none=True),
        )
    except KeyError as error:
        raise http_error(error) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Engine execution error: {error}") from error


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
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Engine execution error: {error}") from error


@router.get("/sessions/{session_id}/evidence")
def evidence_graph(session_id: str, request: Request) -> dict[str, object]:
    try:
        return get_services(request).service.get_evidence_graph(session_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
