from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.api.deps import get_services, http_error


router = APIRouter()


@router.post("/sessions/{session_id}/plans")
def draft_plan(session_id: str, body: dict[str, Any], request: Request) -> dict[str, object]:
    services = get_services(request)
    try:
        services.service._assert_session_exists(session_id)
        return services.planning_service.draft_plan(session_id, body.get("steps", []))
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/sessions/{session_id}/plans")
def list_plans(session_id: str, request: Request) -> list[dict[str, object]]:
    return get_services(request).planning_service.list_plans(session_id)


@router.get("/sessions/{session_id}/plans/{plan_id}")
def get_plan(session_id: str, plan_id: str, request: Request) -> dict[str, object]:
    try:
        return get_services(request).planning_service.get_plan(plan_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.patch("/sessions/{session_id}/plans/{plan_id}")
def patch_plan(session_id: str, plan_id: str, body: dict[str, Any], request: Request) -> dict[str, object]:
    try:
        return get_services(request).planning_service.patch_plan(plan_id, steps=body.get("steps"))
    except (KeyError, ValueError) as error:
        raise http_error(error) from error


@router.post("/sessions/{session_id}/plans/{plan_id}/validate")
def validate_plan(session_id: str, plan_id: str, request: Request) -> dict[str, object]:
    try:
        return get_services(request).planning_service.validate_plan(plan_id)
    except (KeyError, ValueError) as error:
        raise http_error(error) from error


@router.post("/sessions/{session_id}/plans/{plan_id}/approve")
def approve_plan(session_id: str, plan_id: str, request: Request) -> dict[str, object]:
    try:
        return get_services(request).planning_service.approve_plan(plan_id)
    except (KeyError, ValueError) as error:
        raise http_error(error) from error


@router.post("/sessions/{session_id}/plans/{plan_id}/execute")
def execute_plan(
    session_id: str,
    plan_id: str,
    request: Request,
    body: dict[str, Any] | None = None,
) -> dict[str, object]:
    services = get_services(request)
    continue_on_failure = (body or {}).get("continue_on_failure", False)
    try:
        return services.planning_service.execute_plan(
            plan_id,
            services.service,
            continue_on_failure=bool(continue_on_failure),
        )
    except (KeyError, ValueError) as error:
        raise http_error(error) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Engine execution error: {error}") from error


@router.get("/sessions/{session_id}/plans/{plan_id}/explain")
def explain_plan(session_id: str, plan_id: str, request: Request) -> dict[str, object]:
    try:
        return get_services(request).planning_service.explain_plan(plan_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/sessions/{session_id}/plans/{plan_id}/estimate-costs")
def estimate_plan_costs(session_id: str, plan_id: str, request: Request) -> dict[str, object]:
    services = get_services(request)
    try:
        return services.planning_service.estimate_costs(plan_id, services.analytics_engine)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/sessions/{session_id}/plans/{plan_id}/budget-check")
def check_plan_budget(session_id: str, plan_id: str, request: Request) -> dict[str, object]:
    try:
        return get_services(request).planning_service.check_budget(plan_id, session_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
