from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.deps import get_services, http_error
from app.api.models import JobResponse, JobSubmitRequest

router = APIRouter()


@router.post("/jobs", response_model=JobResponse)
def submit_job(payload: JobSubmitRequest, request: Request) -> JobResponse:
    try:
        result = get_services(request).job_service.submit_job(
            session_id=payload.session_id,
            job_type=payload.job_type,
            payload=payload.payload.model_dump(),
        )
        return JobResponse.model_validate(result)
    except (KeyError, ValueError) as error:
        raise http_error(error) from error


@router.get("/jobs", response_model=list[JobResponse])
def list_jobs(
    request: Request,
    session_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> list[JobResponse]:
    rows = get_services(request).job_service.list_jobs(session_id=session_id, status=status)
    return [JobResponse.model_validate(r) for r in rows]


@router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: str, request: Request) -> JobResponse:
    try:
        result = get_services(request).job_service.get_job(job_id)
        return JobResponse.model_validate(result)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/jobs/{job_id}/cancel", response_model=JobResponse)
def cancel_job(job_id: str, request: Request) -> JobResponse:
    try:
        result = get_services(request).job_service.cancel_job(job_id)
        return JobResponse.model_validate(result)
    except (KeyError, ValueError) as error:
        raise http_error(error) from error
