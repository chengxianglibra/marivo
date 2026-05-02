from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.deps import get_services, http_error
from app.api.models import (
    ApprovalCreateRequest,
    ApprovalDecisionRequest,
    ApprovalResponse,
    AutoFlagRequest,
)

router = APIRouter()


@router.post("/approvals", response_model=ApprovalResponse)
def create_approval(payload: ApprovalCreateRequest, request: Request) -> ApprovalResponse:
    try:
        result = get_services(request).approval_service.request_approval(
            session_id=payload.session_id,
            rec_id=payload.rec_id,
        )
        return ApprovalResponse.model_validate(result)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/approvals", response_model=list[ApprovalResponse])
def list_approvals(
    request: Request,
    session_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> list[ApprovalResponse]:
    rows = get_services(request).approval_service.list_requests(
        session_id=session_id, status=status
    )
    return [ApprovalResponse.model_validate(r) for r in rows]


@router.get("/approvals/{request_id}", response_model=ApprovalResponse)
def get_approval(request_id: str, request: Request) -> ApprovalResponse:
    try:
        result = get_services(request).approval_service.get_request(request_id)
        return ApprovalResponse.model_validate(result)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/approvals/{request_id}/approve", response_model=ApprovalResponse)
def approve_request(
    request_id: str, payload: ApprovalDecisionRequest, request: Request
) -> ApprovalResponse:
    try:
        result = get_services(request).approval_service.approve(
            request_id,
            reviewer=payload.reviewer,
            reason=payload.reason,
        )
        return ApprovalResponse.model_validate(result)
    except (KeyError, ValueError) as error:
        raise http_error(error) from error


@router.post("/approvals/{request_id}/reject", response_model=ApprovalResponse)
def reject_request(
    request_id: str, payload: ApprovalDecisionRequest, request: Request
) -> ApprovalResponse:
    try:
        result = get_services(request).approval_service.reject(
            request_id,
            reviewer=payload.reviewer,
            reason=payload.reason,
        )
        return ApprovalResponse.model_validate(result)
    except (KeyError, ValueError) as error:
        raise http_error(error) from error


@router.post(
    "/sessions/{session_id}/approvals/auto-flag",
    response_model=list[ApprovalResponse],
)
def auto_flag_approvals(
    session_id: str,
    request: Request,
    payload: AutoFlagRequest | None = None,
) -> list[ApprovalResponse]:
    services = get_services(request)
    try:
        services.service._assert_session_exists(session_id)
        threshold = payload.risk_threshold if payload else "P0"
        rows = services.approval_service.auto_flag_recommendations(
            session_id, risk_threshold=threshold
        )
        return [ApprovalResponse.model_validate(r) for r in rows]
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
