"""Local OpenTelemetry-shaped usage telemetry for Marivo."""

from __future__ import annotations

import json
import os
import platform
import tomllib
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from marivo import __version__
from marivo.config import PROJECT_MANIFEST, STATE_DIR
from marivo.project import resolve_project_root

TelemetryValue = str | int | float | bool

TELEMETRY_INTENTS = {
    "marivo.analysis.assess_quality",
    "marivo.analysis.attribute",
    "marivo.analysis.compare",
    "marivo.analysis.correlate",
    "marivo.analysis.derive_metric_frame",
    "marivo.analysis.discover.cross_sectional_outliers",
    "marivo.analysis.discover.driver_axes",
    "marivo.analysis.discover.interesting_slices",
    "marivo.analysis.discover.interesting_windows",
    "marivo.analysis.discover.period_shifts",
    "marivo.analysis.discover.point_anomalies",
    "marivo.analysis.escape_hatch.explore_ibis",
    "marivo.analysis.escape_hatch.from_pandas",
    "marivo.analysis.escape_hatch.promote_attribution_frame",
    "marivo.analysis.escape_hatch.promote_delta_frame",
    "marivo.analysis.escape_hatch.promote_metric_frame",
    "marivo.analysis.forecast",
    "marivo.analysis.hypothesis_test",
    "marivo.analysis.observe",
    "marivo.analysis.frame.transform.bottomk",
    "marivo.analysis.frame.transform.filter",
    "marivo.analysis.frame.transform.normalize",
    "marivo.analysis.frame.transform.rank",
    "marivo.analysis.frame.transform.rollup",
    "marivo.analysis.frame.transform.slice",
    "marivo.analysis.frame.transform.topk",
    "marivo.analysis.frame.transform.window",
    "marivo.cli.init",
}

__all__ = ["TELEMETRY_INTENTS", "track_event", "track_operation"]


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


def _session_id(session: object | None) -> str | None:
    value = getattr(session, "id", None)
    return value if isinstance(value, str) and value else None


def _project_root(session: object | None, project_root: Path | None) -> Path:
    session_root = getattr(session, "project_root", None)
    if isinstance(session_root, Path):
        return session_root
    if isinstance(session_root, str):
        return Path(session_root)
    if project_root is not None:
        return project_root
    return resolve_project_root()


def _output_path(root: Path) -> Path:
    return root / STATE_DIR / "telemetry" / "events.jsonl"


def _now_unix_nano() -> str:
    return str(int(datetime.now(UTC).timestamp() * 1_000_000_000))


def _value(value: TelemetryValue) -> dict[str, object]:
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
    family: str,
    intent: str,
    session: object | None,
    status: str,
    duration_ms: int | None,
    error_type: str | None,
    attributes: Mapping[str, TelemetryValue] | None,
) -> dict[str, object]:
    record_attrs = [
        _attribute("marivo.event.name", event_name),
        _attribute("marivo.event.schema_version", "1"),
        _attribute("marivo.intent.family", family),
        _attribute("marivo.intent.name", intent),
        _attribute("marivo.operation.status", status),
    ]
    session_id = _session_id(session)
    if session_id is not None:
        record_attrs.append(_attribute("marivo.session.id", session_id))
    if duration_ms is not None:
        record_attrs.append(_attribute("marivo.duration_ms", duration_ms))
    if error_type is not None:
        record_attrs.append(_attribute("marivo.error.type", error_type))
    for key, value in (attributes or {}).items():
        record_attrs.append(_attribute(key, value))
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
                                "severityText": "INFO" if status == "ok" else "ERROR",
                                "body": {"stringValue": event_name},
                                "attributes": record_attrs,
                            }
                        ],
                    }
                ],
            }
        ]
    }


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
    """Append a local OTLP-shaped log record for a Marivo usage event."""
    try:
        root = _project_root(session, project_root)
        if not _enabled(root):
            return
        path = _output_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = _log_entry(
            event_name,
            family=family,
            intent=intent,
            session=session,
            status=status,
            duration_ms=duration_ms,
            error_type=error_type,
            attributes=attributes,
        )
        with path.open("a", encoding="utf-8") as handle:
            json.dump(entry, handle, separators=(",", ":"))
            handle.write("\n")
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
) -> Iterator[None]:
    """Record operation duration and status while preserving caller behavior."""
    started = monotonic()
    try:
        yield
    except BaseException as exc:
        track_event(
            event_name,
            family=family,
            intent=intent,
            session=session,
            project_root=project_root,
            status="error",
            duration_ms=int((monotonic() - started) * 1000),
            error_type=type(exc).__name__,
            attributes=attributes,
        )
        raise
    else:
        track_event(
            event_name,
            family=family,
            intent=intent,
            session=session,
            project_root=project_root,
            status="ok",
            duration_ms=int((monotonic() - started) * 1000),
            attributes=attributes,
        )
