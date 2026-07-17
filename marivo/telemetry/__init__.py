"""Local OpenTelemetry-shaped loop-engineering telemetry for Marivo."""

from __future__ import annotations

import functools
import inspect
import json
import os
import platform
import sqlite3
import threading
import tomllib
import types
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from time import monotonic
from typing import Literal, ParamSpec, TypeVar, cast

from marivo import __version__
from marivo.config import PROJECT_MANIFEST, STATE_DIR
from marivo.project import resolve_project_root

TelemetryScalar = str | int | float | bool
TelemetryValue = TelemetryScalar | tuple[str, ...]

_P = ParamSpec("_P")
_R = TypeVar("_R")

_SCHEMA_VERSION = "2"
_STARTED_EVENT = "marivo.operation.started"
_COMPLETED_EVENT = "marivo.operation.completed"
_INSTALLATION_FILE = "project_instance_id"
_WRITE_LOCK = threading.Lock()
_DROPPED_EVENTS = 0

_SENSITIVE_PARAMETER_PARTS = (
    "credential",
    "dsn",
    "host",
    "password",
    "path",
    "row",
    "secret",
    "slice_by",
    "sql",
    "token",
)
_SAFE_STRING_PARAMETERS = frozenset(
    {
        "additivity",
        "agg",
        "alignment",
        "backend_type",
        "calendar",
        "column",
        "datasource",
        "dimension",
        "entity",
        "expect_shape",
        "format",
        "fanout_policy",
        "grain",
        "granularity",
        "id",
        "kind",
        "method",
        "metric",
        "mode",
        "name",
        "op",
        "ref",
        "source",
        "status",
        "target",
        "time_dimension",
        "track",
        "unit",
    }
)
_SAFE_IDENTIFIER_COLLECTION_PARAMETERS = frozenset(
    {
        "agg",
        "axes",
        "columns",
        "dimensions",
        "domains",
        "entities",
        "fields",
        "measures",
        "metrics",
        "primary_key",
        "refs",
        "targets",
    }
)
_SAFE_DIRECT_RESULT_FIELDS = (
    "backend_type",
    "cache_status",
    "id",
    "is_truncated",
    "latency_ms",
    "ok",
    "overall_status",
    "ref",
    "requested_limit",
    "returned_row_count",
    "schema_fingerprint",
    "status",
    "timeout_seconds",
)
_SAFE_META_FIELDS = (
    "artifact_id",
    "content_hash",
    "evidence_status",
    "kind",
    "produced_by_job",
    "ref",
    "row_count",
    "semantic_kind",
    "session_id",
)


@dataclass(frozen=True)
class _ActiveOperation:
    surface: str
    capability_id: str
    operation_id: str
    suppress_internal_load_success: bool = False


_ACTIVE_OPERATIONS: ContextVar[tuple[_ActiveOperation, ...]] = ContextVar(
    "marivo_telemetry_operations", default=()
)
_CURRENT_OPERATION: ContextVar[object | None] = ContextVar(
    "marivo_telemetry_current_operation", default=None
)


def _setting_enabled(raw: object) -> bool | None:
    if not isinstance(raw, str):
        return None
    value = raw.strip().lower()
    if value == "on":
        return True
    if value == "off":
        return False
    return None


def _project_setting(root: Path) -> bool | None:
    manifest_path = root / PROJECT_MANIFEST
    if not manifest_path.is_file():
        return None
    try:
        with open(manifest_path, "rb") as handle:
            data = tomllib.load(handle)
    except Exception:
        return None
    telemetry = data.get("telemetry")
    if not isinstance(telemetry, dict):
        return None
    return _setting_enabled(telemetry.get("enabled"))


def _enabled(root: Path) -> bool:
    env_value = _setting_enabled(os.environ.get("MARIVO_TELEMETRY"))
    if env_value is not None:
        return env_value
    project_value = _project_setting(root)
    if project_value is not None:
        return project_value
    return True


def _output_dir(root: Path) -> Path:
    return root / STATE_DIR / "telemetry"


def _output_path(root: Path) -> Path:
    return _output_dir(root) / "events.jsonl"


def _instance_id(root: Path) -> str:
    path = _output_dir(root) / _INSTALLATION_FILE
    try:
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    except OSError:
        pass
    value = f"project_{uuid.uuid4().hex}"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        try:
            existing = path.read_text(encoding="utf-8").strip()
        except OSError:
            return "unavailable"
        return existing or "unavailable"
    except OSError:
        return "unavailable"
    try:
        os.write(descriptor, f"{value}\n".encode())
    finally:
        os.close(descriptor)
    return value


def _now_unix_nano() -> str:
    return str(int(datetime.now(UTC).timestamp() * 1_000_000_000))


def _value(value: TelemetryValue) -> dict[str, object]:
    if isinstance(value, tuple):
        return {"arrayValue": {"values": [_value(item) for item in value]}}
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    return {"stringValue": value}


def _attribute(key: str, value: TelemetryValue) -> dict[str, object]:
    return {"key": key, "value": _value(value)}


def _resource_attributes() -> list[dict[str, object]]:
    return [
        _attribute("service.name", "marivo"),
        _attribute("service.version", __version__),
        _attribute("telemetry.sdk.name", "marivo"),
        _attribute("telemetry.sdk.language", "python"),
        _attribute("process.runtime.version", platform.python_version()),
        _attribute("os.type", platform.system().lower() or "unknown"),
    ]


def _log_entry(
    event_name: str,
    *,
    status: str,
    attributes: Mapping[str, TelemetryValue],
) -> dict[str, object]:
    record_attrs = [
        _attribute("marivo.event.name", event_name),
        _attribute("marivo.event.schema_version", _SCHEMA_VERSION),
        *(_attribute(key, value) for key, value in attributes.items()),
    ]
    severity = "ERROR" if status == "error" else "INFO"
    return {
        "resourceLogs": [
            {
                "resource": {"attributes": _resource_attributes()},
                "scopeLogs": [
                    {
                        "scope": {"name": "marivo.telemetry"},
                        "logRecords": [
                            {
                                "timeUnixNano": _now_unix_nano(),
                                "severityText": severity,
                                "body": {"stringValue": event_name},
                                "attributes": record_attrs,
                            }
                        ],
                    }
                ],
            }
        ]
    }


def _record_attributes(entry: dict[str, object]) -> list[dict[str, object]]:
    resource_logs = cast("list[dict[str, object]]", entry["resourceLogs"])
    scope_logs = cast("list[dict[str, object]]", resource_logs[0]["scopeLogs"])
    log_records = cast("list[dict[str, object]]", scope_logs[0]["logRecords"])
    return cast("list[dict[str, object]]", log_records[0]["attributes"])


def _write_entry(root: Path, entry: dict[str, object]) -> None:
    global _DROPPED_EVENTS
    try:
        path = _output_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with _WRITE_LOCK:
            dropped = _DROPPED_EVENTS
            if dropped:
                _record_attributes(entry).append(
                    _attribute("marivo.telemetry.dropped_since_last_write", dropped)
                )
            payload = (json.dumps(entry, separators=(",", ":")) + "\n").encode()
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                written = os.write(descriptor, payload)
                if written != len(payload):
                    raise OSError("short telemetry append")
            finally:
                os.close(descriptor)
            _DROPPED_EVENTS = 0
    except Exception:
        with _WRITE_LOCK:
            _DROPPED_EVENTS += 1


def _safe_getattr(value: object, name: str) -> object | None:
    try:
        return getattr(value, name, None)
    except Exception:
        return None


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


def _project_root(arguments: Mapping[str, object]) -> Path:
    explicit = arguments.get("project_root")
    if isinstance(explicit, (str, Path)):
        return Path(explicit)
    for value in arguments.values():
        root = _path_from_value(value)
        if root is not None:
            return root
    return resolve_project_root()


def _session_attributes(arguments: Mapping[str, object]) -> dict[str, TelemetryValue]:
    for value in arguments.values():
        session_id = _safe_getattr(value, "id")
        project_root = _safe_getattr(value, "project_root")
        if isinstance(session_id, str) and isinstance(project_root, (str, Path)):
            attrs: dict[str, TelemetryValue] = {"marivo.session.id": session_id}
            question = _safe_getattr(value, "question")
            if isinstance(question, str) and question:
                attrs["marivo.session.question"] = question
            return attrs
        meta = _safe_getattr(value, "meta")
        session_id = _safe_getattr(meta, "session_id") if meta is not None else None
        if isinstance(session_id, str) and session_id:
            attrs = {"marivo.session.id": session_id}
            purpose = _safe_getattr(meta, "analysis_purpose")
            if isinstance(purpose, str) and purpose:
                attrs["marivo.analysis.purpose"] = purpose
            return attrs
    return {}


def _session_creation_attributes(
    root: Path, capability_id: str, arguments: Mapping[str, object]
) -> dict[str, TelemetryValue]:
    if capability_id != "session.get_or_create":
        return {}
    attrs: dict[str, TelemetryValue] = {}
    requested = arguments.get("question")
    if isinstance(requested, str) and requested:
        attrs["marivo.session.requested_question"] = requested
    name = arguments.get("name")
    if not isinstance(name, str):
        return attrs
    db_path = root / STATE_DIR / "analysis" / "session_store.db"
    if not db_path.is_file():
        attrs["marivo.session.created"] = True
        attrs["marivo.session.question_applied"] = requested is not None
        return attrs
    try:
        with sqlite3.connect(str(db_path)) as connection:
            row = connection.execute(
                "SELECT question FROM sessions WHERE name = ?", (name,)
            ).fetchone()
    except sqlite3.Error:
        return attrs
    created = row is None
    attrs["marivo.session.created"] = created
    attrs["marivo.session.question_applied"] = created and requested is not None
    if row is not None and isinstance(row[0], str) and row[0]:
        attrs["marivo.session.question"] = row[0]
    return attrs


def _is_sensitive_parameter(name: str) -> bool:
    normalized = name.lower()
    return any(part in normalized for part in _SENSITIVE_PARAMETER_PARTS)


def _safe_identifier(value: object) -> str | None:
    if isinstance(value, str):
        return value
    for field_name in ("id", "ref", "name"):
        candidate = _safe_getattr(value, field_name)
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def _safe_reference_attributes(prefix: str, value: object) -> dict[str, TelemetryValue]:
    """Summarize stable object identity without inspecting arbitrary values."""
    attrs: dict[str, TelemetryValue] = {}
    meta = _safe_getattr(value, "meta")
    if meta is not None:
        for field_name in _SAFE_META_FIELDS:
            field_value = _safe_getattr(meta, field_name)
            if isinstance(field_value, (str, int, float, bool)) and field_value is not None:
                attrs[f"{prefix}.{field_name}"] = field_value
        return attrs
    for field_name in ("id", "ref"):
        field_value = _safe_getattr(value, field_name)
        if isinstance(field_value, str) and field_value:
            attrs[f"{prefix}.{field_name}"] = field_value
    return attrs


def _parameter_attributes(name: str, value: object) -> dict[str, TelemetryValue]:
    if name in {"self", "session", "project_root"} or _is_sensitive_parameter(name):
        return {}
    prefix = f"marivo.input.{name}"
    if value is None:
        return {f"{prefix}.present": False}
    if isinstance(value, bool):
        return {prefix: value}
    if isinstance(value, (int, float)):
        return {prefix: value}
    if isinstance(value, str):
        if name in _SAFE_STRING_PARAMETERS or name.endswith(("_id", "_ref")):
            return {prefix: value}
        return {f"{prefix}.type": "str", f"{prefix}.length": len(value)}
    if isinstance(value, Mapping):
        attrs: dict[str, TelemetryValue] = {f"{prefix}.count": len(value)}
        operators = tuple(
            sorted(
                {
                    str(item.get("op"))
                    for item in value.values()
                    if isinstance(item, Mapping) and isinstance(item.get("op"), str)
                }
            )
        )
        if operators:
            attrs[f"{prefix}.operators"] = operators
        return attrs
    if isinstance(value, (list, tuple, set, frozenset)):
        attrs = {f"{prefix}.count": len(value)}
        item_types = tuple(sorted({type(item).__name__ for item in value}))
        if item_types:
            attrs[f"{prefix}.item_types"] = item_types
        if name in _SAFE_IDENTIFIER_COLLECTION_PARAMETERS:
            identifiers = tuple(
                identifier for item in value if (identifier := _safe_identifier(item)) is not None
            )
            if identifiers:
                attrs[f"{prefix}.ids"] = identifiers
        return attrs
    attrs = {f"{prefix}.type": type(value).__name__}
    references = _safe_reference_attributes(prefix, value)
    if references:
        attrs.update(references)
    else:
        identifier = _safe_identifier(value)
        if identifier is not None:
            attrs[f"{prefix}.id"] = identifier
    return attrs


def _input_attributes(
    capability_id: str, arguments: Mapping[str, object]
) -> dict[str, TelemetryValue]:
    attrs: dict[str, TelemetryValue] = {}
    receiver = arguments.get("self")
    if receiver is not None:
        attrs.update(_safe_reference_attributes("marivo.input", receiver))
    purpose = arguments.get("analysis_purpose")
    if isinstance(purpose, str) and purpose:
        attrs["marivo.analysis.purpose"] = purpose
    reason = arguments.get("reason")
    if capability_id == "raw_sql" and isinstance(reason, str) and reason:
        attrs["marivo.datasource.raw_sql.reason"] = reason
    for name, value in arguments.items():
        if name in {"analysis_purpose", "question", "reason"}:
            continue
        attrs.update(_parameter_attributes(name, value))
    attrs.update(_session_attributes(arguments))
    return attrs


def _operation_origin(
    surface: str,
    capability_id: str,
    stack: tuple[_ActiveOperation, ...],
) -> Literal["explicit", "delegated", "internal_load"]:
    if not stack:
        return "explicit"
    loaded_by_session = surface in {"datasource", "semantic"} and any(
        active.surface == "analysis" and active.capability_id == "session.get_or_create"
        for active in stack
    )
    loaded_by_public_semantic_call = surface == "semantic" and any(
        active.surface == "semantic" and active.capability_id == "load" for active in stack
    )
    if capability_id != "load" and (loaded_by_session or loaded_by_public_semantic_call):
        return "internal_load"
    return "delegated"


def _counted_kinds(value: object) -> tuple[int, tuple[str, ...]] | None:
    if not isinstance(value, (list, tuple)):
        return None
    kinds = tuple(
        sorted(
            {
                kind
                for item in value
                if isinstance((kind := _safe_getattr(item, "kind")), str) and kind
            }
        )
    )
    return len(value), kinds


def _result_attributes(result: object) -> dict[str, TelemetryValue]:
    attrs: dict[str, TelemetryValue] = {
        "marivo.result.type": type(result).__name__,
    }
    if result is None:
        return attrs
    shape = _safe_getattr(result, "shape")
    if (
        isinstance(shape, tuple)
        and len(shape) == 2
        and all(isinstance(item, int) for item in shape)
    ):
        attrs["marivo.result.row_count"] = shape[0]
        attrs["marivo.result.column_count"] = shape[1]
    meta = _safe_getattr(result, "meta")
    if meta is not None:
        for field_name in _SAFE_META_FIELDS:
            value = _safe_getattr(meta, field_name)
            if isinstance(value, (str, int, float, bool)) and value is not None:
                attrs[f"marivo.result.{field_name}"] = value
    for field_name in _SAFE_DIRECT_RESULT_FIELDS:
        value = _safe_getattr(result, field_name)
        if isinstance(value, (str, int, float, bool)) and value is not None:
            attrs[f"marivo.result.{field_name}"] = value
        else:
            identifier = _safe_identifier(value) if value is not None else None
            if identifier is not None:
                attrs[f"marivo.result.{field_name}"] = identifier
    for field_name in ("blockers", "warnings", "issues"):
        counted = _counted_kinds(_safe_getattr(result, field_name))
        if counted is None:
            continue
        count, kinds = counted
        attrs[f"marivo.result.{field_name}_count"] = count
        if kinds:
            attrs[f"marivo.result.{field_name}_kinds"] = kinds
    if isinstance(result, (list, tuple)):
        attrs["marivo.result.count"] = len(result)
    session_id = _safe_getattr(result, "id")
    session_root = _safe_getattr(result, "project_root")
    if isinstance(session_id, str) and isinstance(session_root, (str, Path)):
        attrs["marivo.session.id"] = session_id
        question = _safe_getattr(result, "question")
        if isinstance(question, str) and question:
            attrs["marivo.session.question"] = question
    return attrs


def _error_domain(exc: BaseException) -> str:
    module = type(exc).__module__
    if module.startswith("marivo.analysis"):
        return "analysis"
    if module.startswith("marivo.datasource"):
        return "datasource"
    if module.startswith("marivo.semantic"):
        return "semantic"
    return "runtime"


def _error_attributes(exc: BaseException) -> dict[str, TelemetryValue]:
    attrs: dict[str, TelemetryValue] = {
        "marivo.error.domain": _error_domain(exc),
        "marivo.error.class": type(exc).__name__,
    }
    for field_name in ("kind", "code", "stage", "constraint_id"):
        value = _safe_getattr(exc, field_name)
        if isinstance(value, str) and value:
            attrs[f"marivo.error.{field_name}"] = value
    repair = _safe_getattr(exc, "repair")
    if repair is not None:
        repair_kind = _safe_getattr(repair, "kind")
        if isinstance(repair_kind, str):
            attrs["marivo.repair.kind"] = repair_kind
        target = _safe_getattr(repair, "help_target")
        target_surface = _safe_getattr(target, "surface") if target is not None else None
        target_id = _safe_getattr(target, "canonical_id") if target is not None else None
        if isinstance(target_surface, str):
            attrs["marivo.repair.help_surface"] = target_surface
        if isinstance(target_id, str):
            attrs["marivo.repair.help_target"] = target_id
    effect = _safe_getattr(exc, "effect_observed")
    if effect is not None:
        query_executed = _safe_getattr(effect, "query_executed")
        scope_state = _safe_getattr(effect, "scope_state")
        if isinstance(query_executed, bool):
            attrs["marivo.error.query_executed"] = query_executed
        if isinstance(scope_state, str):
            attrs["marivo.error.scope_state"] = scope_state
    return attrs


@dataclass
class _Operation:
    surface: str
    capability_id: str
    capability_kind: str
    root: Path
    attributes: dict[str, TelemetryValue]
    defer_start_write: bool = False
    operation_id: str = field(default_factory=lambda: f"op_{uuid.uuid4().hex}")
    started: float = field(default_factory=monotonic)
    phase_durations_ms: dict[str, int] = field(default_factory=dict)
    result: object = None
    _stack_token: Token[tuple[_ActiveOperation, ...]] | None = None
    _current_token: Token[object | None] | None = None
    _start_entry: dict[str, object] | None = None
    parent_id: str | None = None
    origin: Literal["explicit", "delegated", "internal_load"] = "explicit"
    failure_stage: str | None = None
    suppress_success: bool = False
    enabled: bool = False

    def __enter__(self) -> _Operation:
        self.enabled = _enabled(self.root)
        if not self.enabled:
            return self
        stack = _ACTIVE_OPERATIONS.get()
        self.parent_id = stack[-1].operation_id if stack else None
        self.origin = _operation_origin(self.surface, self.capability_id, stack)
        suppress_internal_load_success = any(
            active.suppress_internal_load_success for active in stack
        )
        self.suppress_success = self.origin == "internal_load" and suppress_internal_load_success
        common = self._common_attributes(status="started")
        self._start_entry = _log_entry(_STARTED_EVENT, status="started", attributes=common)
        if not self.defer_start_write and not self.suppress_success:
            _write_entry(self.root, self._start_entry)
        suppress_descendants = suppress_internal_load_success or (
            self.capability_id == "session.get_or_create"
            and self.attributes.get("marivo.session.created") is False
        )
        active = _ActiveOperation(
            self.surface,
            self.capability_id,
            self.operation_id,
            suppress_internal_load_success=suppress_descendants,
        )
        self._stack_token = _ACTIVE_OPERATIONS.set((*stack, active))
        self._current_token = _CURRENT_OPERATION.set(self)
        return self

    def _common_attributes(self, *, status: str) -> dict[str, TelemetryValue]:
        attrs: dict[str, TelemetryValue] = {
            "marivo.project.instance_id": _instance_id(self.root),
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

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: types.TracebackType | None,
    ) -> Literal[False]:
        del exc_type, traceback
        if not self.enabled:
            return False
        if self._current_token is not None:
            _CURRENT_OPERATION.reset(self._current_token)
        if self._stack_token is not None:
            _ACTIVE_OPERATIONS.reset(self._stack_token)
        status = "error" if exc is not None else "ok"
        attrs = self._common_attributes(status=status)
        attrs["marivo.operation.duration_ms"] = int((monotonic() - self.started) * 1000)
        for phase, duration_ms in sorted(self.phase_durations_ms.items()):
            attrs[f"marivo.phase.{phase}.duration_ms"] = duration_ms
        if exc is not None:
            attrs.update(_error_attributes(exc))
            if "marivo.error.stage" not in attrs and self.failure_stage is not None:
                attrs["marivo.error.stage"] = self.failure_stage
        else:
            attrs.update(_result_attributes(self.result))
        completed = _log_entry(_COMPLETED_EVENT, status=status, attributes=attrs)
        if self.suppress_success and exc is None:
            return False
        if (self.defer_start_write or self.suppress_success) and self._start_entry is not None:
            _write_entry(self.root, self._start_entry)
        _write_entry(self.root, completed)
        return False


@contextmanager
def telemetry_stage(name: str) -> Iterator[None]:
    """Record one internal phase duration on the active public operation."""
    raw_operation = _CURRENT_OPERATION.get()
    if not isinstance(raw_operation, _Operation) or not raw_operation.enabled:
        yield
        return
    operation = raw_operation
    started = monotonic()
    try:
        yield
    except BaseException:
        operation.failure_stage = name
        raise
    finally:
        elapsed = int((monotonic() - started) * 1000)
        operation.phase_durations_ms[name] = operation.phase_durations_ms.get(name, 0) + elapsed


def staged(name: str) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]:
    """Decorate an internal phase without creating a second operation."""

    def decorate(func: Callable[_P, _R]) -> Callable[_P, _R]:
        @functools.wraps(func)
        def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            with telemetry_stage(name):
                return func(*args, **kwargs)

        return wrapped

    return decorate


def _bind_arguments(
    signature: inspect.Signature, args: tuple[object, ...], kwargs: dict[str, object]
) -> dict[str, object]:
    try:
        bound = signature.bind_partial(*args, **kwargs)
    except TypeError:
        return dict(kwargs)
    bound.apply_defaults()
    return dict(bound.arguments)


def _already_active(surface: str, capability_id: str) -> bool:
    return any(
        active.surface == surface and active.capability_id == capability_id
        for active in _ACTIVE_OPERATIONS.get()
    )


def tracked_capability(
    *,
    surface: str,
    capability_id: str,
    capability_kind: str,
    default_stage: str | None = None,
) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]:
    """Decorate one public capability with v2 operation telemetry."""

    def decorate(func: Callable[_P, _R]) -> Callable[_P, _R]:
        signature = inspect.signature(func)

        @functools.wraps(func)
        def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            if _already_active(surface, capability_id):
                return func(*args, **kwargs)
            arguments = _bind_arguments(
                signature,
                cast("tuple[object, ...]", args),
                cast("dict[str, object]", kwargs),
            )
            root = _project_root(arguments)
            attrs = _input_attributes(capability_id, arguments)
            attrs.update(_session_creation_attributes(root, capability_id, arguments))
            operation = _Operation(
                surface=surface,
                capability_id=capability_id,
                capability_kind=capability_kind,
                root=root,
                attributes=attrs,
                defer_start_write=surface == "cli" and capability_id == "init",
            )
            with operation:
                if default_stage is None:
                    result = func(*args, **kwargs)
                else:
                    with telemetry_stage(default_stage):
                        result = func(*args, **kwargs)
                operation.result = result
                return result

        marker = set(getattr(func, "__marivo_telemetry_capabilities__", ()))
        marker.add((surface, capability_id))
        wrapped.__dict__["__marivo_telemetry_capabilities__"] = frozenset(marker)
        return wrapped

    return decorate


def _resolve_owner(path: str) -> tuple[object, str]:
    parts = path.split(".")
    for index in range(len(parts) - 1, 0, -1):
        try:
            owner: object = import_module(".".join(parts[:index]))
        except ModuleNotFoundError:
            continue
        try:
            for attribute in parts[index:-1]:
                owner = getattr(owner, attribute)
        except AttributeError:
            continue
        return owner, parts[-1]
    raise ImportError(f"cannot resolve telemetry callable owner for {path!r}")


def install_surface_instrumentation(
    *, surface: str, descriptors: Iterable[object], root_module: types.ModuleType
) -> frozenset[str]:
    """Install telemetry wrappers for registered public functions and methods."""
    installed: set[str] = set()
    for descriptor in descriptors:
        capability_id = _safe_getattr(descriptor, "id") or _safe_getattr(descriptor, "canonical_id")
        path = _safe_getattr(descriptor, "callable_path")
        kind = _safe_getattr(descriptor, "kind")
        if not isinstance(capability_id, str) or not isinstance(path, str):
            continue
        owner, attribute_name = _resolve_owner(path)
        raw = inspect.getattr_static(owner, attribute_name)
        if isinstance(raw, (property, type)):
            continue
        descriptor_kind = kind if isinstance(kind, str) else "callable"
        effects = _safe_getattr(descriptor, "effects")
        data_access = _safe_getattr(effects, "data_access") if effects is not None else None
        connection = _safe_getattr(effects, "connection") if effects is not None else None
        if capability_id == "load":
            default_stage = "resolve"
        elif connection == "opens_connection":
            default_stage = "connect"
        elif (
            surface == "analysis" and descriptor_kind in {"operator", "boundary"}
        ) or data_access in {
            "live_metadata_read",
            "scoped_data_read",
            "potentially_unbounded_read",
        }:
            default_stage = "execute"
        else:
            default_stage = None
        if isinstance(raw, staticmethod):
            original = raw.__func__
            wrapped = tracked_capability(
                surface=surface,
                capability_id=capability_id,
                capability_kind=descriptor_kind,
                default_stage=default_stage,
            )(original)
            replacement: object = staticmethod(wrapped)
        elif isinstance(raw, classmethod):
            original = raw.__func__
            wrapped = tracked_capability(
                surface=surface,
                capability_id=capability_id,
                capability_kind=descriptor_kind,
                default_stage=default_stage,
            )(original)
            replacement = classmethod(wrapped)
        elif callable(raw):
            original = cast("Callable[..., object]", raw)
            wrapped = tracked_capability(
                surface=surface,
                capability_id=capability_id,
                capability_kind=descriptor_kind,
                default_stage=default_stage,
            )(original)
            replacement = wrapped
        else:
            continue
        setattr(owner, attribute_name, replacement)
        for export_name, exported in tuple(vars(root_module).items()):
            if exported is original:
                setattr(root_module, export_name, wrapped)
        installed.add(capability_id)
    current = set(getattr(root_module, "__marivo_telemetry_capabilities__", ()))
    current.update(installed)
    root_module.__dict__["__marivo_telemetry_capabilities__"] = frozenset(current)
    return frozenset(installed)


def _legacy_identity(event_name: str, intent: str) -> tuple[str, str]:
    prefix = "marivo."
    if event_name.startswith(prefix):
        parts = event_name[len(prefix) :].split(".")
        if parts:
            return parts[0], ".".join(parts[1:]) or intent
    return "runtime", intent


def track_event(
    event_name: str,
    *,
    family: str,
    intent: str,
    session: object | None = None,
    project_root: Path | None = None,
    status: str = "ok",
    duration_ms: int | None = None,
    error_type: str | None = None,
    attributes: Mapping[str, TelemetryValue] | None = None,
) -> None:
    """Append one custom v2 event without changing caller behavior."""
    try:
        arguments: dict[str, object] = {"session": session, "project_root": project_root}
        root = _project_root(arguments)
        if not _enabled(root):
            return
        surface, capability_id = _legacy_identity(event_name, intent)
        attrs: dict[str, TelemetryValue] = {
            "marivo.project.instance_id": _instance_id(root),
            "marivo.surface": surface,
            "marivo.capability.id": capability_id,
            "marivo.capability.kind": family,
            "marivo.operation.status": status,
            **_session_attributes(arguments),
            **dict(attributes or {}),
        }
        if duration_ms is not None:
            attrs["marivo.operation.duration_ms"] = duration_ms
        if error_type is not None:
            attrs["marivo.error.class"] = error_type
        _write_entry(root, _log_entry(event_name, status=status, attributes=attrs))
    except Exception:
        return


@contextmanager
def track_operation(
    event_name: str,
    *,
    family: str,
    intent: str,
    session: object | None = None,
    project_root: Path | None = None,
    attributes: Mapping[str, TelemetryValue] | None = None,
) -> Iterator[_Operation | None]:
    """Record a v2 operation pair while preserving the legacy internal call shape."""
    surface, capability_id = _legacy_identity(event_name, intent)
    if _already_active(surface, capability_id):
        with telemetry_stage("execute"):
            yield None
        return
    arguments: dict[str, object] = {"session": session, "project_root": project_root}
    root = _project_root(arguments)
    operation = _Operation(
        surface=surface,
        capability_id=capability_id,
        capability_kind=family,
        root=root,
        attributes={**_session_attributes(arguments), **dict(attributes or {})},
        defer_start_write=surface == "cli" and capability_id == "init",
    )
    with operation:
        yield operation


__all__ = [
    "install_surface_instrumentation",
    "staged",
    "telemetry_stage",
    "track_event",
    "track_operation",
    "tracked_capability",
]
