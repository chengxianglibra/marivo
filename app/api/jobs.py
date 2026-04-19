from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.deps import get_services, http_error
from app.api.models import JobSubmitRequest

router = APIRouter()


@router.post("/jobs")
def submit_job(payload: JobSubmitRequest, request: Request) -> dict[str, object]:
    try:
        return get_services(request).job_service.submit_job(
            session_id=payload.session_id,
            job_type=payload.job_type,
            payload=payload.payload,
        )
    except (KeyError, ValueError) as error:
        raise http_error(error) from error


@router.get("/jobs")
def list_jobs(
    request: Request,
    session_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> list[dict[str, object]]:
    return get_services(request).job_service.list_jobs(session_id=session_id, status=status)


@router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request) -> dict[str, object]:
    try:
        return get_services(request).job_service.get_job(job_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request) -> dict[str, object]:
    try:
        return get_services(request).job_service.cancel_job(job_id)
    except (KeyError, ValueError) as error:
        raise http_error(error) from error
