from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request
from starlette.responses import PlainTextResponse

from marivo.transports.http.deps import get_services

router = APIRouter()


@router.get("/metrics")
def get_metrics(request: Request, format: str | None = Query(default=None)) -> Any:
    metrics = get_services(request).metrics
    if metrics is None:
        return {"error": "Metrics collection is disabled"}
    if format == "prometheus":
        return PlainTextResponse(metrics.prometheus(), media_type="text/plain")
    return metrics.snapshot()
