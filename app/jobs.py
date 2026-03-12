"""Async job orchestration: background step/workflow/plan execution."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from app.storage.metadata import MetadataStore

if TYPE_CHECKING:
    from app.planning import PlanningService
    from app.service import SemanticLayerService

logger = logging.getLogger(__name__)


class JobService:
    def __init__(
        self,
        metadata: MetadataStore,
        service: SemanticLayerService,
        planning_service: PlanningService | None = None,
    ) -> None:
        self.metadata = metadata
        self.service = service
        self.planning_service = planning_service
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._cancel_events: dict[str, asyncio.Event] = {}

    def submit_job(self, session_id: str, job_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        valid_types = ("step", "workflow", "plan")
        if job_type not in valid_types:
            raise ValueError(f"Invalid job_type: {job_type}. Must be one of {valid_types}")

        job_id = f"job_{uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        self.metadata.execute(
            """
            INSERT INTO jobs (job_id, session_id, job_type, payload_json, status, submitted_at)
            VALUES (?, ?, ?, ?, 'pending', ?)
            """,
            [job_id, session_id, job_type, json.dumps(payload or {}), now],
        )

        cancel_event = asyncio.Event()
        self._cancel_events[job_id] = cancel_event

        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(self._execute_job(job_id, session_id, job_type, payload or {}))
            self._tasks[job_id] = task
        except RuntimeError:
            # No running event loop (e.g. in sync tests) — execute synchronously
            self._execute_job_sync(job_id, session_id, job_type, payload or {})

        return self.get_job(job_id)

    def get_job(self, job_id: str) -> dict[str, Any]:
        row = self.metadata.query_one("SELECT * FROM jobs WHERE job_id = ?", [job_id])
        if row is None:
            raise KeyError(f"Unknown job: {job_id}")
        return self._deserialize_job(row)

    def list_jobs(
        self,
        session_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM jobs WHERE 1=1"
        params: list[Any] = []
        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY submitted_at DESC"
        return [self._deserialize_job(r) for r in self.metadata.query_rows(query, params)]

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        job = self.get_job(job_id)
        if job["status"] in ("completed", "failed", "cancelled"):
            raise ValueError(f"Cannot cancel job in '{job['status']}' status")

        # Signal cancellation
        cancel_event = self._cancel_events.get(job_id)
        if cancel_event:
            cancel_event.set()

        # Cancel the asyncio task if running
        task = self._tasks.get(job_id)
        if task and not task.done():
            task.cancel()

        now = datetime.now(timezone.utc).isoformat()
        self.metadata.execute(
            "UPDATE jobs SET status = 'cancelled', completed_at = ? WHERE job_id = ?",
            [now, job_id],
        )
        return self.get_job(job_id)

    async def _execute_job(
        self,
        job_id: str,
        session_id: str,
        job_type: str,
        payload: dict[str, Any],
    ) -> None:
        """Background job executor (async)."""
        now = datetime.now(timezone.utc).isoformat()
        self.metadata.execute(
            "UPDATE jobs SET status = 'running', started_at = ? WHERE job_id = ?",
            [now, job_id],
        )
        try:
            result = self._run_payload(session_id, job_type, payload)
            completed_at = datetime.now(timezone.utc).isoformat()
            self.metadata.execute(
                "UPDATE jobs SET status = 'completed', result_json = ?, completed_at = ? WHERE job_id = ?",
                [json.dumps(result, default=str), completed_at, job_id],
            )
        except Exception as exc:
            failed_at = datetime.now(timezone.utc).isoformat()
            self.metadata.execute(
                "UPDATE jobs SET status = 'failed', error_message = ?, completed_at = ? WHERE job_id = ?",
                [str(exc), failed_at, job_id],
            )
        finally:
            self._tasks.pop(job_id, None)
            self._cancel_events.pop(job_id, None)

    def _execute_job_sync(
        self,
        job_id: str,
        session_id: str,
        job_type: str,
        payload: dict[str, Any],
    ) -> None:
        """Synchronous job executor (for non-async contexts)."""
        now = datetime.now(timezone.utc).isoformat()
        self.metadata.execute(
            "UPDATE jobs SET status = 'running', started_at = ? WHERE job_id = ?",
            [now, job_id],
        )
        try:
            result = self._run_payload(session_id, job_type, payload)
            completed_at = datetime.now(timezone.utc).isoformat()
            self.metadata.execute(
                "UPDATE jobs SET status = 'completed', result_json = ?, completed_at = ? WHERE job_id = ?",
                [json.dumps(result, default=str), completed_at, job_id],
            )
        except Exception as exc:
            failed_at = datetime.now(timezone.utc).isoformat()
            self.metadata.execute(
                "UPDATE jobs SET status = 'failed', error_message = ?, completed_at = ? WHERE job_id = ?",
                [str(exc), failed_at, job_id],
            )
        finally:
            self._cancel_events.pop(job_id, None)

    def _run_payload(self, session_id: str, job_type: str, payload: dict[str, Any]) -> Any:
        if job_type == "step":
            step_type = payload.get("step_type")
            if not step_type:
                raise ValueError("Job payload for 'step' must include 'step_type'")
            params = payload.get("params")
            return self.service.run_step(session_id, step_type, params=params)
        elif job_type == "workflow":
            return self.service.run_watch_time_drop_workflow(session_id)
        elif job_type == "plan":
            plan_id = payload.get("plan_id")
            if not plan_id:
                raise ValueError("Job payload for 'plan' must include 'plan_id'")
            if self.planning_service is None:
                raise ValueError("PlanningService not configured for job execution")
            return self.planning_service.execute_plan(plan_id, self.service)
        else:
            raise ValueError(f"Unknown job_type: {job_type}")

    def _deserialize_job(self, row: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {
            "job_id": row["job_id"],
            "session_id": row["session_id"],
            "job_type": row["job_type"],
            "payload": json.loads(row["payload_json"]),
            "status": row["status"],
            "submitted_at": row["submitted_at"],
            "started_at": row.get("started_at"),
            "completed_at": row.get("completed_at"),
        }
        if row.get("result_json"):
            result["result"] = json.loads(row["result_json"])
        if row.get("error_message"):
            result["error_message"] = row["error_message"]
        return result
