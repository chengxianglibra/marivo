"""Mode and capture policy for project-local usage telemetry."""

from __future__ import annotations

import os
from contextvars import ContextVar
from pathlib import Path
from typing import Literal

from marivo._kernel.config import load_project_config

TelemetryMode = Literal["off", "on"]
CapturePolicy = Literal["none", "failure", "short", "long"]
_LOAD_POLICY: ContextVar[tuple[Path, TelemetryMode] | None] = ContextVar(
    "marivo_load_telemetry_policy", default=None
)


def _setting_mode(raw: object) -> TelemetryMode | None:
    if not isinstance(raw, str):
        return None
    value = raw.strip().lower()
    if value == "on":
        return "on"
    if value == "off":
        return "off"
    return None


def _project_setting(root: Path) -> TelemetryMode | None:
    try:
        return load_project_config(root).telemetry_mode
    except Exception:
        # Telemetry must remain non-interfering. Project-loading surfaces and
        # doctor report explicit configuration errors through their own paths.
        return None


def _mode(root: Path) -> TelemetryMode:
    env_value = _setting_mode(os.environ.get("MARIVO_TELEMETRY"))
    if env_value is not None:
        return env_value
    load_policy = _LOAD_POLICY.get()
    if load_policy is not None and load_policy[0] == root:
        return load_policy[1]
    project_value = _project_setting(root)
    if project_value is not None:
        return project_value
    return "on"


def _capture_policy(
    surface: str,
    capability_id: str,
    capability_kind: str,
    *,
    default_stage: str | None = None,
) -> CapturePolicy:
    """Select durable events by effect, not by public discoverability."""
    if surface == "cli":
        return "short" if capability_id == "init" else "failure"
    if surface == "help":
        return "failure"
    if surface == "analysis":
        if capability_kind == "constructor":
            return "none"
        if capability_kind == "operator" or capability_id == "boundary.to_pandas":
            return "long"
        if capability_id in {
            "session.get_or_create",
            "session.resume",
        }:
            return "short"
        return "failure"
    if surface == "semantic":
        if capability_id.startswith("ref.") or capability_id == "ref":
            return "none"
        if capability_id == "load" or default_stage in {"connect", "execute"}:
            return "long"
        return "failure"
    if surface == "datasource":
        if capability_id in {"register", "remove"}:
            return "short"
        if default_stage in {"connect", "execute"}:
            return "long"
        return "failure"
    return "failure"
