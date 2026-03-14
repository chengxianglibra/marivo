from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx


class OmniDBApiError(RuntimeError):
    """Actionable error returned by the OmniDB FastAPI wrapper."""


class OmniDBApiClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = client

    @asynccontextmanager
    async def session(self) -> AsyncIterator[httpx.AsyncClient]:
        if self._client is not None:
            yield self._client
            return

        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout, trust_env=False) as client:
            yield client

    # ── Existing endpoints ───────────────────────────────────────

    async def get_health(self) -> dict[str, Any]:
        return await self._request("GET", "/health")

    async def get_catalog(self) -> dict[str, Any]:
        return await self._request("GET", "/catalog")

    async def create_session(
        self,
        goal: str,
        constraints: dict[str, Any] | None = None,
        budget: dict[str, Any] | None = None,
        policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {"goal": goal}
        if constraints is not None:
            payload["constraints"] = constraints
        if budget is not None:
            payload["budget"] = budget
        if policy is not None:
            payload["policy"] = policy
        return await self._request("POST", "/sessions", json=payload)

    async def run_step(self, session_id: str, step_type: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._request("POST", f"/sessions/{session_id}/steps/{step_type}", json=params)

    async def get_evidence(self, session_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/sessions/{session_id}/evidence")

    # ── New endpoints (Phase 4) ──────────────────────────────────

    async def list_sources(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/sources")

    async def search_catalog(self, query: str, object_type: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, str] = {"q": query}
        if object_type:
            params["type"] = object_type
        return await self._request("GET", "/catalog/search", params=params)

    async def resolve_term(self, name: str) -> dict[str, Any]:
        return await self._request("GET", f"/semantic/resolve/{name}")

    async def get_planner_context(self, session_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/sessions/{session_id}/planner-context")

    # ── Planning endpoints ────────────────────────────────────────

    async def draft_plan(self, session_id: str, steps: list[dict[str, Any]]) -> dict[str, Any]:
        return await self._request("POST", f"/sessions/{session_id}/plans", json={"steps": steps})

    async def validate_plan(self, session_id: str, plan_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/sessions/{session_id}/plans/{plan_id}/validate")

    async def execute_plan(
        self,
        session_id: str,
        plan_id: str,
        *,
        continue_on_failure: bool = False,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if continue_on_failure:
            body["continue_on_failure"] = True
        return await self._request(
            "POST",
            f"/sessions/{session_id}/plans/{plan_id}/execute",
            json=body if body else None,
        )

    # ── Job endpoints ────────────────────────────────────────────

    async def submit_job(
        self,
        session_id: str,
        job_type: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._request("POST", "/jobs", json={
            "session_id": session_id,
            "job_type": job_type,
            "payload": payload or {},
        })

    async def get_job(self, job_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/jobs/{job_id}")

    # ── Approval endpoints ───────────────────────────────────────

    async def list_approvals(
        self,
        session_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, str] = {}
        if session_id:
            params["session_id"] = session_id
        if status:
            params["status"] = status
        return await self._request("GET", "/approvals", params=params or None)

    # ── HTTP internals ───────────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> Any:
        try:
            async with self.session() as client:
                response = await client.request(method, path, json=json, params=params)
        except httpx.ConnectError as error:
            raise OmniDBApiError(
                f"Could not reach the OmniDB FastAPI service at {self.base_url}. "
                "Start it with `uvicorn app.main:app --reload` or set OMNIDB_API_BASE_URL."
            ) from error
        except httpx.TimeoutException as error:
            raise OmniDBApiError(
                f"The OmniDB FastAPI service at {self.base_url} timed out. "
                "Try again or increase OMNIDB_API_TIMEOUT."
            ) from error
        except httpx.RemoteProtocolError as error:
            raise OmniDBApiError(
                f"The OmniDB FastAPI service at {self.base_url} closed the connection unexpectedly. "
                "If you are using localhost, verify the service is running and that no proxy is intercepting the request."
            ) from error

        if response.is_error:
            detail = self._extract_error_detail(response)
            raise OmniDBApiError(
                f"FastAPI service returned HTTP {response.status_code} for {method} {path}: {detail}"
            )

        try:
            return response.json()
        except json.JSONDecodeError as error:
            raise OmniDBApiError(
                f"FastAPI service returned a non-JSON response for {method} {path}. "
                "Verify the service is the expected OmniDB API."
            ) from error

    def _extract_error_detail(self, response: httpx.Response) -> str:
        try:
            payload = response.json()
        except json.JSONDecodeError:
            return response.text or "unknown error"

        if isinstance(payload, dict) and "detail" in payload:
            return str(payload["detail"])
        return json.dumps(payload, ensure_ascii=False)
