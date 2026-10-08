"""Always-on project-local execution diagnostics, independent of usage telemetry."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from time import monotonic
from typing import Literal, TypeAlias
from uuid import uuid4

from marivo import _jsonl
from marivo._compat import UTC
from marivo.project import resolve_project_root

LogValue: TypeAlias = str | int | float | bool | None
QueryState: TypeAlias = Literal["succeeded", "failed", "closed_early"]
_MAX_FILE_BYTES = 128 * 1024 * 1024
_MAX_HISTORICAL_BYTES = 1024 * 1024 * 1024
_RETENTION_DAYS = 14
_LOCK = threading.Lock()
_DROPPED: dict[Path, int] = {}
_WARNED: set[tuple[Path, str]] = set()
_LAST_PRUNED: dict[Path, date] = {}
_logger = logging.getLogger("marivo.execution")


@dataclass
class ExecutionContext:
    root: Path
    fields: dict[str, LogValue] = field(default_factory=dict)


_CONTEXT: ContextVar[ExecutionContext | None] = ContextVar("marivo_execution_log", default=None)


def snapshot(project_root: Path | None = None) -> ExecutionContext:
    current = _CONTEXT.get()
    root = (
        project_root.resolve()
        if project_root is not None
        else current.root
        if current is not None
        else resolve_project_root()
    )
    return ExecutionContext(root, dict(current.fields) if current and current.root == root else {})


@contextmanager
def scope(project_root: Path | None = None, **fields: LogValue) -> Iterator[ExecutionContext]:
    context = snapshot(project_root)
    context.fields.update({key: value for key, value in fields.items() if value is not None})
    token = _CONTEXT.set(context)
    try:
        yield context
    finally:
        _CONTEXT.reset(token)


def annotate(**fields: LogValue) -> None:
    current = _CONTEXT.get()
    if current is not None:
        current.fields.update(fields)


def _warn(root: Path, kind: str, error: Exception) -> None:
    key = (root, kind)
    if key not in _WARNED:
        _WARNED.add(key)
        # A caller-installed logging handler must not disrupt query execution either.
        with suppress(Exception):
            _logger.warning("Execution log %s failed (error_type=%s)", kind, type(error).__name__)


def emit(
    event: str,
    *,
    context: ExecutionContext | None = None,
    severity: str = "INFO",
    fields: Mapping[str, LogValue] | None = None,
    **facts: LogValue,
) -> None:
    selected = context if context is not None else snapshot()
    root = selected.root
    with _LOCK:
        try:
            now = datetime.now(UTC)
            entry = {
                "schema_version": 1,
                "timestamp": now.isoformat(),
                "severity": severity,
                "event": event,
                **selected.fields,
                **(fields or {}),
                **facts,
            }
            dropped = _DROPPED.get(root, 0)
            if dropped:
                entry["dropped_since_last_write"] = dropped
            payload = (json.dumps(entry, ensure_ascii=True, separators=(",", ":")) + "\n").encode()
            directory = root / ".marivo" / "logs"
            path = _jsonl.output_path(
                directory,
                "execution-",
                event_date=now.date(),
                payload_bytes=len(payload),
                max_bytes=_MAX_FILE_BYTES,
            )
            _jsonl.append(path, payload)
        except Exception as error:
            _DROPPED[root] = _DROPPED.get(root, 0) + 1
            _warn(root, "write", error)
            return
        _DROPPED.pop(root, None)
        _WARNED.discard((root, "write"))
        try:
            _jsonl.prune(
                directory,
                "execution-",
                current_date=now.date(),
                retention_days=_RETENTION_DAYS,
                max_historical_bytes=_MAX_HISTORICAL_BYTES,
                last_pruned=_LAST_PRUNED,
            )
        except Exception as error:
            _warn(root, "cleanup", error)
        else:
            _WARNED.discard((root, "cleanup"))


def error_fields(error: BaseException, *, sensitive: bool = False) -> dict[str, LogValue]:
    fields: dict[str, LogValue] = {"error_type": type(error).__name__}
    for name in ("stage", "code", "kind", "constraint_id", "timeout_seconds"):
        with suppress(Exception):
            value = getattr(error, name, None)
            if isinstance(value, (str, int, float, bool)):
                fields[f"error_{name}"] = value
    if isinstance(error, Exception) and not sensitive:
        from marivo.datasource.errors import _backend_failure_summary

        with suppress(Exception):
            summary = _backend_failure_summary(error)
            fields.update(
                error_message=summary.message,
                backend_code=summary.backend_code,
                backend_name=summary.backend_name,
            )
    return fields


class QueryLog:
    """One actual submission; completion follows consumption and cursor release."""

    def __init__(
        self,
        sql: str,
        *,
        backend: str,
        purpose: str,
        project_root: Path | None = None,
        sensitive: bool = False,
        **fields: LogValue,
    ) -> None:
        self.context = snapshot(project_root)
        self.context.fields.update(
            query_id=f"query_{uuid4().hex}", backend=backend, purpose=purpose, **fields
        )
        self.started = monotonic()
        self.rows = 0
        self.arrow_bytes: int | None = None
        self.error: BaseException | None = None
        self.sensitive = sensitive
        self.finished = False
        emit("query.submitted", context=self.context, sql=sql)

    def __enter__(self) -> QueryLog:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        error: BaseException | None,
        traceback: object,
    ) -> Literal[False]:
        del exc_type, traceback
        self.error = error
        self.finish("failed" if error is not None else "succeeded")
        return False

    def finish(self, state: QueryState, **fields: LogValue) -> None:
        if self.finished:
            return
        self.finished = True
        failure = error_fields(self.error, sensitive=self.sensitive) if self.error else {}
        emit(
            "query.completed",
            context=self.context,
            severity="ERROR" if state == "failed" else "INFO",
            state=state,
            duration_ms=int((monotonic() - self.started) * 1000),
            consumed_rows=self.rows,
            consumed_arrow_bytes=self.arrow_bytes,
            fields={**failure, **fields},
        )


@contextmanager
def stage(name: str) -> Iterator[dict[str, LogValue]]:
    fields: dict[str, LogValue] = {}
    entered = monotonic()
    with scope(stage=name) as context:
        emit("stage.started", context=context)
        try:
            yield fields
        except BaseException as error:
            emit(
                "stage.completed",
                context=context,
                severity="ERROR",
                state="failed",
                duration_ms=int((monotonic() - entered) * 1000),
                fields=error_fields(error),
            )
            raise
        else:
            emit(
                "stage.completed",
                context=context,
                state="succeeded",
                duration_ms=int((monotonic() - entered) * 1000),
                fields=fields,
            )
