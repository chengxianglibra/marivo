"""Observability: structured logging, request timing middleware, and metrics collection."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any, ClassVar

from starlette.types import ASGIApp, Message, Receive, Scope, Send

# ── Correlation context ─────────────────────────────────────────────

correlation_session_id: ContextVar[str] = ContextVar("correlation_session_id", default="")
correlation_step_id: ContextVar[str] = ContextVar("correlation_step_id", default="")
correlation_plan_id: ContextVar[str] = ContextVar("correlation_plan_id", default="")
correlation_planner_id: ContextVar[str] = ContextVar("correlation_planner_id", default="")
correlation_compiler_id: ContextVar[str] = ContextVar("correlation_compiler_id", default="")
correlation_execution_stage: ContextVar[str] = ContextVar("correlation_execution_stage", default="")
correlation_datasource_id: ContextVar[str] = ContextVar("correlation_datasource_id", default="")


@contextmanager
def observability_context(
    *,
    session_id: str | None = None,
    step_id: str | None = None,
    plan_id: str | None = None,
    planner_id: str | None = None,
    compiler_id: str | None = None,
    execution_stage: str | None = None,
    datasource_id: str | None = None,
) -> Iterator[None]:
    tokens: list[tuple[ContextVar[str], Token[str]]] = []
    for variable, value in (
        (correlation_session_id, session_id),
        (correlation_step_id, step_id),
        (correlation_plan_id, plan_id),
        (correlation_planner_id, planner_id),
        (correlation_compiler_id, compiler_id),
        (correlation_execution_stage, execution_stage),
        (correlation_datasource_id, datasource_id),
    ):
        if value:
            tokens.append((variable, variable.set(value)))
    try:
        yield
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)


# ── Structured JSON formatter ───────────────────────────────────────


class JSONFormatter(logging.Formatter):
    """Emit log records as single-line JSON with correlation IDs."""

    _reserved_record_fields: ClassVar[set[str]] = {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }

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
        planner_id = correlation_planner_id.get("")
        if planner_id:
            entry["planner_id"] = planner_id
        compiler_id = correlation_compiler_id.get("")
        if compiler_id:
            entry["compiler_id"] = compiler_id
        execution_stage = correlation_execution_stage.get("")
        if execution_stage:
            entry["execution_stage"] = execution_stage
        datasource_id = correlation_datasource_id.get("")
        if datasource_id:
            entry["datasource_id"] = datasource_id
        for key, value in record.__dict__.items():
            if key in self._reserved_record_fields or key.startswith("_"):
                continue
            entry[key] = value
        if record.exc_info and record.exc_info[1]:
            entry["exception"] = str(record.exc_info[1])
        return json.dumps(entry, default=str)


def setup_logging(level: str = "INFO") -> None:
    """Configure root logger with JSON formatter.

    The LOG_LEVEL environment variable, if set, overrides the *level* argument.
    Set LOG_LEVEL=WARNING to suppress INFO output during test runs.
    """
    import os

    effective_level = os.environ.get("LOG_LEVEL", level).upper()
    root = logging.getLogger()
    # Remove existing handlers to avoid duplicates during tests
    root.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    root.addHandler(handler)
    root.setLevel(getattr(logging, effective_level, logging.INFO))


# ── Metrics collector ───────────────────────────────────────────────


class MetricsCollector:
    """In-process metrics collector (no external dependencies)."""

    def __init__(self) -> None:
        self.request_count: dict[str, int] = {}
        self.request_duration_sum: dict[str, float] = {}
        self.error_count: dict[int, int] = {}
        self.step_count: dict[str, int] = {}
        self.step_duration: dict[str, list[float]] = {}
        self.step_dimension_count: dict[str, int] = {}
        self.step_dimension_duration_sum: dict[str, float] = {}
        self.execution_stage_count: dict[str, int] = {}
        self.execution_stage_duration_sum: dict[str, float] = {}
        self.active_sessions: int = 0

    def record_request(self, method: str, path: str, status_code: int, duration_ms: float) -> None:
        key = f"{method}:{path}"
        self.request_count[key] = self.request_count.get(key, 0) + 1
        self.request_duration_sum[key] = self.request_duration_sum.get(key, 0.0) + duration_ms
        if status_code >= 400:
            self.error_count[status_code] = self.error_count.get(status_code, 0) + 1

    def _dimension_key(self, **labels: str | None) -> str:
        return "|".join(f"{key}={value or 'unknown'}" for key, value in labels.items())

    def record_step(
        self,
        step_type: str,
        duration_ms: float,
        *,
        planner: str | None = None,
        compiler: str | None = None,
        engine: str | None = None,
        stage: str | None = None,
    ) -> None:
        self.step_count[step_type] = self.step_count.get(step_type, 0) + 1
        self.step_duration.setdefault(step_type, []).append(duration_ms)
        dimension_key = self._dimension_key(
            step_type=step_type,
            planner=planner,
            compiler=compiler,
            engine=engine,
            stage=stage,
        )
        self.step_dimension_count[dimension_key] = (
            self.step_dimension_count.get(dimension_key, 0) + 1
        )
        self.step_dimension_duration_sum[dimension_key] = (
            self.step_dimension_duration_sum.get(dimension_key, 0.0) + duration_ms
        )

    def record_execution_stage(
        self,
        stage_name: str,
        duration_ms: float,
        *,
        planner: str | None = None,
        compiler: str | None = None,
        engine: str | None = None,
    ) -> None:
        dimension_key = self._dimension_key(
            stage=stage_name,
            planner=planner,
            compiler=compiler,
            engine=engine,
        )
        self.execution_stage_count[dimension_key] = (
            self.execution_stage_count.get(dimension_key, 0) + 1
        )
        self.execution_stage_duration_sum[dimension_key] = (
            self.execution_stage_duration_sum.get(dimension_key, 0.0) + duration_ms
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "request_count": dict(self.request_count),
            "request_duration_sum_ms": dict(self.request_duration_sum),
            "error_count": dict(self.error_count),
            "step_count": dict(self.step_count),
            "step_duration_ms": {k: list(v) for k, v in self.step_duration.items()},
            "step_dimension_count": dict(self.step_dimension_count),
            "step_dimension_duration_sum_ms": dict(self.step_dimension_duration_sum),
            "execution_stage_count": dict(self.execution_stage_count),
            "execution_stage_duration_sum_ms": dict(self.execution_stage_duration_sum),
            "active_sessions": self.active_sessions,
        }

    def prometheus(self) -> str:
        """Render Prometheus-compatible text exposition."""
        lines: list[str] = [
            "# HELP marivo_requests_total Total HTTP requests",
            "# TYPE marivo_requests_total counter",
        ]
        for key, count in sorted(self.request_count.items()):
            method, path = key.split(":", 1)
            lines.append(f'marivo_requests_total{{method="{method}",path="{path}"}} {count}')
        lines.extend(
            [
                "# HELP marivo_request_duration_seconds_sum Sum of request durations",
                "# TYPE marivo_request_duration_seconds_sum counter",
            ]
        )
        for key, total in sorted(self.request_duration_sum.items()):
            method, path = key.split(":", 1)
            lines.append(
                f'marivo_request_duration_seconds_sum{{method="{method}",path="{path}"}} {total / 1000:.4f}'
            )
        lines.extend(
            [
                "# HELP marivo_errors_total Total HTTP errors by status code",
                "# TYPE marivo_errors_total counter",
            ]
        )
        for code, count in sorted(self.error_count.items()):
            lines.append(f'marivo_errors_total{{status_code="{code}"}} {count}')
        lines.extend(
            [
                "# HELP marivo_step_executions_total Total step executions",
                "# TYPE marivo_step_executions_total counter",
            ]
        )
        for step_type, count in sorted(self.step_count.items()):
            lines.append(f'marivo_step_executions_total{{step_type="{step_type}"}} {count}')
        lines.extend(
            [
                "# HELP marivo_step_duration_seconds_sum Sum of step durations",
                "# TYPE marivo_step_duration_seconds_sum counter",
            ]
        )
        for step_type, durations in sorted(self.step_duration.items()):
            total_sec = sum(durations) / 1000
            lines.append(
                f'marivo_step_duration_seconds_sum{{step_type="{step_type}"}} {total_sec:.4f}'
            )
        lines.extend(
            [
                "# HELP marivo_step_executions_by_dimension_total Total step executions by planner/compiler/engine/stage labels",
                "# TYPE marivo_step_executions_by_dimension_total counter",
            ]
        )
        for key, count in sorted(self.step_dimension_count.items()):
            labels = dict(item.split("=", 1) for item in key.split("|"))
            lines.append(
                "marivo_step_executions_by_dimension_total{"
                + ",".join(f'{label}="{value}"' for label, value in labels.items())
                + f"}} {count}"
            )
        lines.extend(
            [
                "# HELP marivo_execution_stage_seconds_sum Sum of execution stage durations",
                "# TYPE marivo_execution_stage_seconds_sum counter",
            ]
        )
        for key, total in sorted(self.execution_stage_duration_sum.items()):
            labels = dict(item.split("=", 1) for item in key.split("|"))
            lines.append(
                "marivo_execution_stage_seconds_sum{"
                + ",".join(f'{label}="{value}"' for label, value in labels.items())
                + f"}} {total / 1000:.4f}"
            )
        lines.extend(
            [
                "# HELP marivo_active_sessions Current active sessions",
                "# TYPE marivo_active_sessions gauge",
                f"marivo_active_sessions {self.active_sessions}",
            ]
        )
        return "\n".join(lines) + "\n"


# ── Timing middleware ───────────────────────────────────────────────

_http_logger = logging.getLogger("marivo.http")


class TimingMiddleware:
    """Pure-ASGI middleware that measures request duration.

    Unlike BaseHTTPMiddleware, this does not buffer the response body,
    so it is compatible with SSE streaming (e.g. FastMCP streamable-http).
    Duration is measured from request start to last body chunk sent.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        status_code: int = 0

        async def send_with_capture(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        await self.app(scope, receive, send_with_capture)
        duration_ms = (time.perf_counter() - start) * 1000

        app = scope.get("app")
        metrics: MetricsCollector | None = (
            getattr(app.state, "metrics", None) if app is not None else None
        )
        if metrics is not None:
            path = scope.get("path", "")
            method = scope.get("method", "")
            metrics.record_request(method, path, status_code, duration_ms)
        _http_logger.info(
            "HTTP %s %s %d %.1fms",
            scope.get("method", ""),
            scope.get("path", ""),
            status_code,
            duration_ms,
        )
