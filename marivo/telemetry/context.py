"""Operation context, execution diagnostics, and nested telemetry stages."""

from __future__ import annotations

import types
import uuid
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import Literal

from marivo._kernel import execution_log
from marivo._kernel.input_context import InputContext
from marivo._kernel.project import resolve_project_root
from marivo.telemetry.config import CapturePolicy, TelemetryMode, _capture_policy, _mode
from marivo.telemetry.sanitizer import (
    TelemetryValue,
    _error_attributes,
    _result_attributes,
    _safe_getattr,
    _session_attributes,
)


@dataclass(frozen=True)
class _ActiveOperation:
    surface: str
    capability_id: str
    operation_id: str
    emitted: bool = True


_ACTIVE_OPERATIONS: ContextVar[tuple[_ActiveOperation, ...]] = ContextVar(
    "marivo_telemetry_operations", default=()
)
_CURRENT_OPERATION: ContextVar[_Operation | None] = ContextVar(
    "marivo_telemetry_current_operation", default=None
)


def _path_from_value(value: object) -> Path | None:
    direct = _safe_getattr(value, "project_root")
    if isinstance(direct, (str, Path)):
        return Path(direct)
    private = _safe_getattr(value, "_project_root")
    if isinstance(private, (str, Path)):
        return Path(private)
    meta = _safe_getattr(value, "meta")
    meta_root = _safe_getattr(meta, "project_root") if meta is not None else None
    if isinstance(meta_root, (str, Path)):
        return Path(meta_root)
    return None


def _captured_project_root(value: object) -> Path | None:
    if isinstance(value, tuple):
        for item in value:
            root = _captured_project_root(item)
            if root is not None:
                return root
        return None
    root = _path_from_value(value)
    if root is not None:
        return root
    context = _safe_getattr(value, "_context")
    return context.project_root if isinstance(context, InputContext) else None


def _project_root(arguments: Mapping[str, object], *, captured_inputs: bool = False) -> Path:
    explicit = arguments.get("project_root")
    if isinstance(explicit, (str, Path)):
        return Path(explicit)
    workspace = arguments.get("workspace_dir")
    if isinstance(workspace, (str, Path)):
        return Path(workspace)
    for name, value in arguments.items():
        root = _path_from_value(value)
        if (
            root is None
            and captured_inputs
            and name in {"entity", "target", "population", "by", "factors", "via", "steps"}
        ):
            # Only inspect binding arguments, never literal keys or result rows.
            root = _captured_project_root(value)
        if root is not None:
            return root
    active = _CURRENT_OPERATION.get()
    if active is not None:
        return active.root
    return resolve_project_root()


def _operation_origin(
    surface: str,
    capability_id: str,
    stack: tuple[_ActiveOperation, ...],
) -> Literal["explicit", "delegated", "internal_load"]:
    if not stack:
        return "explicit"
    session_recovery_capabilities = {"session.get_or_create", "session.resume"}
    loaded_by_session = surface in {"datasource", "semantic"} and any(
        active.surface == "analysis" and active.capability_id in session_recovery_capabilities
        for active in stack
    )
    loaded_by_public_semantic_call = surface == "semantic" and any(
        active.surface == "semantic" and active.capability_id == "load" for active in stack
    )
    if capability_id != "load" and (loaded_by_session or loaded_by_public_semantic_call):
        return "internal_load"
    return "delegated"


@dataclass
class _Operation:
    surface: str
    capability_id: str
    capability_kind: str
    root: Path
    attributes: dict[str, TelemetryValue]
    capture: CapturePolicy = "long"
    mode: TelemetryMode | None = None
    question_requested: bool | None = None
    operation_id: str = field(default_factory=lambda: f"op_{uuid.uuid4().hex}")
    started: float = field(default_factory=monotonic)
    phase_durations_ms: dict[str, int] = field(default_factory=dict)
    result: object = None
    _log_scope: AbstractContextManager[execution_log.ExecutionContext] | None = None
    _stack_token: Token[tuple[_ActiveOperation, ...]] | None = None
    _current_token: Token[_Operation | None] | None = None
    _record_success: bool = False
    parent_id: str | None = None
    origin: Literal["explicit", "delegated", "internal_load"] = "explicit"
    failure_stage: str | None = None
    enabled: bool = False

    def __enter__(self) -> _Operation:
        # Nested operations inherit this scope's canonical project root.
        self._log_scope = execution_log.scope(self.root)
        context = self._log_scope.__enter__()
        self.root = context.root
        fields: dict[str, execution_log.LogValue] = {
            "operation_id": self.operation_id,
            "parent_operation_id": context.fields.get("operation_id"),
            "session_id": (
                value
                if isinstance(value := self.attributes.get("marivo.session.id"), str)
                else None
            ),
            "capability": self.capability_id,
            "surface": self.surface,
        }
        context.fields.update({key: value for key, value in fields.items() if value is not None})
        self.mode = _mode(self.root) if self.mode is None else self.mode
        self.enabled = self.mode != "off" and self.capture != "none"
        if not self.enabled:
            self._current_token = _CURRENT_OPERATION.set(self)
            return self
        stack = _ACTIVE_OPERATIONS.get()
        self.parent_id = next(
            (active.operation_id for active in reversed(stack) if active.emitted), None
        )
        self.origin = _operation_origin(self.surface, self.capability_id, stack)
        self._record_success = self.origin == "explicit" and self.capture in {"short", "long"}
        if self._record_success and self.capture == "long":
            self._emit(
                "marivo.operation.started", "started", self._common_attributes(status="started")
            )
        active = _ActiveOperation(
            self.surface,
            self.capability_id,
            self.operation_id,
            emitted=self._record_success,
        )
        self._stack_token = _ACTIVE_OPERATIONS.set((*stack, active))
        self._current_token = _CURRENT_OPERATION.set(self)
        return self

    def _common_attributes(self, *, status: str) -> dict[str, TelemetryValue]:
        attrs: dict[str, TelemetryValue] = {
            "marivo.surface": self.surface,
            "marivo.capability.id": self.capability_id,
            "marivo.capability.kind": self.capability_kind,
            "marivo.operation.id": self.operation_id,
            "marivo.operation.origin": self.origin,
            "marivo.operation.status": status,
            **self.attributes,
        }
        if self.parent_id is not None:
            attrs["marivo.operation.parent_id"] = self.parent_id
        return attrs

    def _emit(self, name: str, status: str, attributes: Mapping[str, TelemetryValue]) -> None:
        from marivo.telemetry.sink import emit_event  # noqa: PLC0415 - load the sink only when writing

        emit_event(self.root, name, status=status, attributes=attributes)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: types.TracebackType | None,
    ) -> Literal[False]:
        if self._log_scope is not None:
            self._log_scope.__exit__(exc_type, exc, traceback)
        if self._current_token is not None:
            _CURRENT_OPERATION.reset(self._current_token)
        if not self.enabled:
            return False
        if self._stack_token is not None:
            _ACTIVE_OPERATIONS.reset(self._stack_token)
        if exc is None and not self._record_success:
            return False
        status = "error" if exc is not None else "ok"
        attrs = self._common_attributes(status=status)
        if self.question_requested is not None:
            attrs["marivo.session.question_applied"] = self.question_requested and exc is None
        attrs["marivo.operation.duration_ms"] = int((monotonic() - self.started) * 1000)
        for phase, duration_ms in sorted(self.phase_durations_ms.items()):
            attrs[f"marivo.phase.{phase}.duration_ms"] = duration_ms
        if exc is not None:
            attrs.update(_error_attributes(exc))
            if "marivo.error.stage" not in attrs and self.failure_stage is not None:
                attrs["marivo.error.stage"] = self.failure_stage
        else:
            attrs.update(_result_attributes(self.result))
        self._emit("marivo.operation.completed", status, attrs)
        return False


@contextmanager
def telemetry_stage(name: str) -> Iterator[None]:
    """Record one internal phase duration on the active public operation."""
    operation = _CURRENT_OPERATION.get()
    if operation is None or not operation.enabled:
        yield
        return
    started = monotonic()
    try:
        yield
    except BaseException:
        operation.failure_stage = name
        raise
    finally:
        elapsed = int((monotonic() - started) * 1000)
        operation.phase_durations_ms[name] = operation.phase_durations_ms.get(name, 0) + elapsed


def _add_operation_attributes(attributes: Mapping[str, TelemetryValue]) -> None:
    """Attach bounded non-sensitive facts to the active public operation."""

    operation = _CURRENT_OPERATION.get()
    if isinstance(operation, _Operation) and operation.enabled:
        operation.attributes.update(attributes)


def _already_active(surface: str, capability_id: str) -> bool:
    return any(
        active.surface == surface and active.capability_id == capability_id
        for active in _ACTIVE_OPERATIONS.get()
    )


@contextmanager
def track_operation(
    *,
    surface: str,
    capability_id: str,
    capability_kind: str,
    session: object | None = None,
    project_root: Path | None = None,
    attributes: Mapping[str, TelemetryValue] | None = None,
) -> Iterator[_Operation | None]:
    """Record a mode-selected operation with an explicit native capability identity."""
    if _already_active(surface, capability_id):
        with telemetry_stage("execute"):
            yield None
        return
    arguments: dict[str, object] = {"session": session, "project_root": project_root}
    root = _project_root(arguments)
    operation = _Operation(
        surface=surface,
        capability_id=capability_id,
        capability_kind=capability_kind,
        root=root,
        attributes={**_session_attributes(arguments), **dict(attributes or {})},
        capture=_capture_policy(surface, capability_id, capability_kind),
    )
    with operation:
        yield operation
