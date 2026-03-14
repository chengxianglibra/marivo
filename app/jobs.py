"""Async job orchestration: background step/plan execution."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from uuid import uuid4

from app.observability import MetricsCollector, observability_context
from app.runtime_contracts import JobExecutor, PlanExecutor
from app.storage.metadata import MetadataStore
from app.storage.repositories import JobRepository

logger = logging.getLogger(__name__)


class JobService:
    def __init__(
        self,
        metadata: MetadataStore,
        service: JobExecutor,
        planning_service: PlanExecutor | None = None,
        job_repository: JobRepository | None = None,
        metrics: MetricsCollector | None = None,
    ) -> None:
        self.metadata = metadata
        self.service = service
        self.planning_service = planning_service
        self.repository = job_repository or JobRepository(metadata)
        self.metrics = metrics
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._cancel_events: dict[str, asyncio.Event] = {}

    def submit_job(self, session_id: str, job_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        valid_types = ("step", "plan")
        if job_type not in valid_types:
            raise ValueError(f"Invalid job_type: {job_type}. Must be one of {valid_types}")

        job_id = f"job_{uuid4().hex[:12]}"
        self.repository.create(job_id, session_id, job_type, payload or {})

        cancel_event = asyncio.Event()
        self._cancel_events[job_id] = cancel_event

        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(self._execute_job(job_id, session_id, job_type, payload or {}))
            self._tasks[job_id] = task
        except RuntimeError:
            self._execute_job_sync(job_id, session_id, job_type, payload or {})

        return self.get_job(job_id)

    def get_job(self, job_id: str) -> dict[str, Any]:
        job = self.repository.get(job_id)
        if job is None:
            raise KeyError(f"Unknown job: {job_id}")
        return job

    def list_jobs(
        self,
        session_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.repository.list(session_id=session_id, status=status)

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        job = self.get_job(job_id)
        if job["status"] in ("completed", "failed", "cancelled"):
            raise ValueError(f"Cannot cancel job in '{job['status']}' status")

        cancel_event = self._cancel_events.get(job_id)
        if cancel_event:
            cancel_event.set()

        task = self._tasks.get(job_id)
        if task and not task.done():
            task.cancel()

        self.repository.mark_cancelled(job_id)
        return self.get_job(job_id)

    async def _execute_job(
        self,
        job_id: str,
        session_id: str,
        job_type: str,
        payload: dict[str, Any],
    ) -> None:
        self.repository.mark_running(job_id)
        if self.metrics is not None:
            self.metrics.active_jobs += 1
        try:
            start = time.perf_counter()
            with observability_context(session_id=session_id, execution_stage=self._job_stage(job_type)):
                result = self._run_payload(session_id, job_type, payload)
            duration_ms = (time.perf_counter() - start) * 1000
            self.repository.mark_completed(job_id, result)
            if self.metrics is not None:
                self.metrics.record_execution_stage(self._job_stage(job_type), duration_ms)
        except Exception as exc:
            self.repository.mark_failed(job_id, str(exc))
        finally:
            if self.metrics is not None:
                self.metrics.active_jobs = max(0, self.metrics.active_jobs - 1)
            self._tasks.pop(job_id, None)
            self._cancel_events.pop(job_id, None)

    def _execute_job_sync(
        self,
        job_id: str,
        session_id: str,
        job_type: str,
        payload: dict[str, Any],
    ) -> None:
        self.repository.mark_running(job_id)
        if self.metrics is not None:
            self.metrics.active_jobs += 1
        try:
            start = time.perf_counter()
            with observability_context(session_id=session_id, execution_stage=self._job_stage(job_type)):
                result = self._run_payload(session_id, job_type, payload)
            duration_ms = (time.perf_counter() - start) * 1000
            self.repository.mark_completed(job_id, result)
            if self.metrics is not None:
                self.metrics.record_execution_stage(self._job_stage(job_type), duration_ms)
        except Exception as exc:
            self.repository.mark_failed(job_id, str(exc))
        finally:
            if self.metrics is not None:
                self.metrics.active_jobs = max(0, self.metrics.active_jobs - 1)
            self._cancel_events.pop(job_id, None)

    def _run_payload(self, session_id: str, job_type: str, payload: dict[str, Any]) -> Any:
        if job_type == "step":
            step_type = payload.get("step_type")
            if not step_type:
                raise ValueError("Job payload for 'step' must include 'step_type'")
            params = payload.get("params")
            return self.service.run_step(session_id, step_type, params=params)
        if job_type == "plan":
            plan_id = payload.get("plan_id")
            if not plan_id:
                raise ValueError("Job payload for 'plan' must include 'plan_id'")
            if self.planning_service is None:
                raise ValueError("PlanningService not configured for job execution")
            return self.planning_service.execute_plan(plan_id, self.service)
        raise ValueError(f"Unknown job_type: {job_type}")

    def _job_stage(self, job_type: str) -> str:
        return "planner" if job_type == "plan" else "executor"
