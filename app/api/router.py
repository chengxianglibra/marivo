from __future__ import annotations

from fastapi import FastAPI

from app.api import (
    approvals,
    catalog,
    engines,
    governance,
    health,
    jobs,
    metrics,
    openapi_fragments,
    routing,
    semantic,
    sessions,
    sources,
)


def include_api_routers(app: FastAPI) -> None:
    for router in (
        health.router,
        openapi_fragments.router,
        sessions.router,
        sources.router,
        engines.router,
        routing.router,
        semantic.router,
        catalog.router,
        governance.router,
        jobs.router,
        approvals.router,
        metrics.router,
    ):
        app.include_router(router)
