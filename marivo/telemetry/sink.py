"""Write the v3 usage event format and maintain local retention."""

from __future__ import annotations

import json
import os
import platform
import threading
import uuid
from collections.abc import Mapping
from contextlib import suppress
from datetime import date, datetime
from pathlib import Path
from typing import cast

from marivo._kernel import jsonl
from marivo._kernel.compat import UTC
from marivo._kernel.config import STATE_DIR
from marivo._kernel.environment import MARIVO_VERSION
from marivo.telemetry.sanitizer import TelemetryValue

_SCHEMA_VERSION = "3"


_INSTALLATION_FILE = "project_instance_id"
_EVENT_FILE_PREFIX = "events-"
_MAX_EVENT_FILE_BYTES = 128 * 1024 * 1024
_MAX_HISTORICAL_TELEMETRY_BYTES = 1024 * 1024 * 1024
_RETENTION_DAYS = 14
_WRITE_LOCK = threading.Lock()
_DROPPED_EVENTS = 0
_LAST_PRUNED_DATE: dict[Path, date] = {}


def _output_dir(root: Path) -> Path:
    return root / STATE_DIR / "telemetry"


def _entry_date(entry: dict[str, object]) -> date:
    resource_logs = cast("list[dict[str, object]]", entry["resourceLogs"])
    scope_logs = cast("list[dict[str, object]]", resource_logs[0]["scopeLogs"])
    log_records = cast("list[dict[str, object]]", scope_logs[0]["logRecords"])
    timestamp = cast("str", log_records[0]["timeUnixNano"])
    return datetime.fromtimestamp(int(timestamp) // 1_000_000_000, UTC).date()


def _output_path(root: Path, *, event_date: date, payload_bytes: int) -> Path:
    return jsonl.output_path(
        _output_dir(root),
        _EVENT_FILE_PREFIX,
        event_date=event_date,
        payload_bytes=payload_bytes,
        max_bytes=_MAX_EVENT_FILE_BYTES,
    )


def _prune_historical_files(directory: Path, *, current_date: date) -> None:
    jsonl.prune(
        directory,
        _EVENT_FILE_PREFIX,
        current_date=current_date,
        retention_days=_RETENTION_DAYS,
        max_historical_bytes=_MAX_HISTORICAL_TELEMETRY_BYTES,
        last_pruned=_LAST_PRUNED_DATE,
    )


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
        if value.bit_length() > 192:
            return {"stringValue": f"int with {value.bit_length()} bits"}
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    return {"stringValue": value}


def _attribute(key: str, value: TelemetryValue) -> dict[str, object]:
    return {"key": key, "value": _value(value)}


def _resource_attributes() -> list[dict[str, object]]:
    return [
        _attribute("service.name", "marivo"),
        _attribute("service.version", MARIVO_VERSION),
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
        with _WRITE_LOCK:
            dropped = _DROPPED_EVENTS
            if dropped:
                _record_attributes(entry).append(
                    _attribute("marivo.telemetry.dropped_since_last_write", dropped)
                )
            payload = (json.dumps(entry, separators=(",", ":")) + "\n").encode()
            event_date = _entry_date(entry)
            path = _output_path(root, event_date=event_date, payload_bytes=len(payload))
            jsonl.append(path, payload)
            _DROPPED_EVENTS = 0
            with suppress(Exception):
                _prune_historical_files(path.parent, current_date=event_date)
    except Exception:
        with _WRITE_LOCK:
            _DROPPED_EVENTS += 1


def emit_event(
    root: Path, name: str, *, status: str, attributes: Mapping[str, TelemetryValue]
) -> None:
    _write_entry(
        root,
        _log_entry(
            name,
            status=status,
            attributes={"marivo._kernel.project.instance_id": _instance_id(root), **attributes},
        ),
    )
