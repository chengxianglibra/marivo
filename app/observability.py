"""Observability: structured logging, request timing middleware, and metrics collection."""

from __future__ import annotations

import json
import logging
import time
from contextvars import ContextVar
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


# ── Correlation context ─────────────────────────────────────────────

correlation_session_id: ContextVar[str] = ContextVar("correlation_session_id", default="")
correlation_step_id: ContextVar[str] = ContextVar("correlation_step_id", default="")
correlation_plan_id: ContextVar[str] = ContextVar("correlation_plan_id", default="")


# ── Structured JSON formatter ───────────────────────────────────────

class JSONFormatter(logging.Formatter):
    """Emit log records as single-line JSON with correlation IDs."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        session_id = correlation_session_id.get("")
        if session_id:
            entry["session_id"] = session_id
        step_id = correlation_step_id.get("")
        if step_id:
            entry["step_id"] = step_id
        plan_id = correlation_plan_id.get("")
        if plan_id:
            entry["plan_id"] = plan_id
        if record.exc_info and record.exc_info[1]:
            entry["exception"] = str(record.exc_info[1])
        return json.dumps(entry, default=str)


def setup_logging(level: str = "INFO") -> None:
    """Configure root logger with JSON formatter."""
    root = logging.getLogger()
    # Remove existing handlers to avoid duplicates during tests
    root.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


# ── Metrics collector ───────────────────────────────────────────────

class MetricsCollector:
    """In-process metrics collector (no external dependencies)."""

    def __init__(self) -> None:
        self.request_count: dict[str, int] = {}
        self.request_duration_sum: dict[str, float] = {}
        self.error_count: dict[int, int] = {}
        self.step_count: dict[str, int] = {}
        self.step_duration: dict[str, list[float]] = {}
        self.active_sessions: int = 0
        self.active_jobs: int = 0

    def record_request(self, method: str, path: str, status_code: int, duration_ms: float) -> None:
        key = f"{method}:{path}"
        self.request_count[key] = self.request_count.get(key, 0) + 1
        self.request_duration_sum[key] = self.request_duration_sum.get(key, 0.0) + duration_ms
        if status_code >= 400:
            self.error_count[status_code] = self.error_count.get(status_code, 0) + 1

    def record_step(self, step_type: str, duration_ms: float) -> None:
        self.step_count[step_type] = self.step_count.get(step_type, 0) + 1
        self.step_duration.setdefault(step_type, []).append(duration_ms)

    def snapshot(self) -> dict[str, Any]:
        return {
            "request_count": dict(self.request_count),
            "request_duration_sum_ms": dict(self.request_duration_sum),
            "error_count": dict(self.error_count),
            "step_count": dict(self.step_count),
            "step_duration_ms": {k: list(v) for k, v in self.step_duration.items()},
            "active_sessions": self.active_sessions,
            "active_jobs": self.active_jobs,
        }

    def prometheus(self) -> str:
        """Render Prometheus-compatible text exposition."""
        lines: list[str] = [
            "# HELP omnidb_requests_total Total HTTP requests",
            "# TYPE omnidb_requests_total counter",
        ]
        for key, count in sorted(self.request_count.items()):
            method, path = key.split(":", 1)
            lines.append(f'omnidb_requests_total{{method="{method}",path="{path}"}} {count}')
        lines.extend([
            "# HELP omnidb_request_duration_seconds_sum Sum of request durations",
            "# TYPE omnidb_request_duration_seconds_sum counter",
        ])
        for key, total in sorted(self.request_duration_sum.items()):
            method, path = key.split(":", 1)
            lines.append(f'omnidb_request_duration_seconds_sum{{method="{method}",path="{path}"}} {total / 1000:.4f}')
        lines.extend([
            "# HELP omnidb_errors_total Total HTTP errors by status code",
            "# TYPE omnidb_errors_total counter",
        ])
        for code, count in sorted(self.error_count.items()):
            lines.append(f'omnidb_errors_total{{status_code="{code}"}} {count}')
        lines.extend([
            "# HELP omnidb_step_executions_total Total step executions",
            "# TYPE omnidb_step_executions_total counter",
        ])
        for step_type, count in sorted(self.step_count.items()):
            lines.append(f'omnidb_step_executions_total{{step_type="{step_type}"}} {count}')
        lines.extend([
            "# HELP omnidb_step_duration_seconds_sum Sum of step durations",
            "# TYPE omnidb_step_duration_seconds_sum counter",
        ])
        for step_type, durations in sorted(self.step_duration.items()):
            total_sec = sum(durations) / 1000
            lines.append(f'omnidb_step_duration_seconds_sum{{step_type="{step_type}"}} {total_sec:.4f}')
        lines.extend([
            "# HELP omnidb_active_sessions Current active sessions",
            "# TYPE omnidb_active_sessions gauge",
            f"omnidb_active_sessions {self.active_sessions}",
            "# HELP omnidb_active_jobs Current active jobs",
            "# TYPE omnidb_active_jobs gauge",
            f"omnidb_active_jobs {self.active_jobs}",
        ])
        return "\n".join(lines) + "\n"


# ── Timing middleware ───────────────────────────────────────────────

class TimingMiddleware(BaseHTTPMiddleware):
    """Measures request duration and records it in MetricsCollector."""

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000
        metrics: MetricsCollector | None = getattr(request.app.state, "metrics", None)
        if metrics is not None:
            path = request.url.path
            metrics.record_request(request.method, path, response.status_code, duration_ms)
        logger = logging.getLogger("omnidb.http")
        logger.info(
            "HTTP %s %s %d %.1fms",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response
