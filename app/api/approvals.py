from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.deps import get_services, http_error
from app.api.models import ApprovalCreateRequest, ApprovalDecisionRequest, AutoFlagRequest

router = APIRouter()


@router.post("/approvals")
def create_approval(payload: ApprovalCreateRequest, request: Request) -> dict[str, object]:
    try:
        return get_services(request).approval_service.request_approval(
            session_id=payload.session_id,
            rec_id=payload.rec_id,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/approvals")
def list_approvals(
    request: Request,
    session_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> list[dict[str, object]]:
    return get_services(request).approval_service.list_requests(
        session_id=session_id, status=status
    )


@router.get("/approvals/{request_id}")
def get_approval(request_id: str, request: Request) -> dict[str, object]:
    try:
        return get_services(request).approval_service.get_request(request_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/approvals/{request_id}/approve")
def approve_request(
    request_id: str, payload: ApprovalDecisionRequest, request: Request
) -> dict[str, object]:
    try:
        return get_services(request).approval_service.approve(
            request_id,
            reviewer=payload.reviewer,
            reason=payload.reason,
        )
    except (KeyError, ValueError) as error:
        raise http_error(error) from error


@router.post("/approvals/{request_id}/reject")
def reject_request(
    request_id: str, payload: ApprovalDecisionRequest, request: Request
) -> dict[str, object]:
    try:
        return get_services(request).approval_service.reject(
            request_id,
            reviewer=payload.reviewer,
            reason=payload.reason,
        )
    except (KeyError, ValueError) as error:
        raise http_error(error) from error


@router.post("/sessions/{session_id}/approvals/auto-flag")
def auto_flag_approvals(
    session_id: str,
    request: Request,
    payload: AutoFlagRequest | None = None,
) -> list[dict[str, object]]:
    services = get_services(request)
    try:
        services.service._assert_session_exists(session_id)
        threshold = payload.risk_threshold if payload else "P0"
        return services.approval_service.auto_flag_recommendations(
            session_id, risk_threshold=threshold
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
