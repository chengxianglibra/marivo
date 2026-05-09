from __future__ import annotations

import contextlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from marivo.transports.cli._exitcodes import EXIT_CONFIG_INVALID, EXIT_RUNTIME_NOT_RUNNING
from marivo.transports.cli._output import CliError

MANIFEST_VERSION: str = "0.1.0"
MANIFEST_MODE: str = "local"

REQUIRED_FIELDS: tuple[str, ...] = (
    "version",
    "workspace_root",
    "mode",
    "base_url",
    "host",
    "port",
    "pid",
    "started_at",
    "config_path",
    "metadata_path",
)


class RuntimeManifest:
    """Typed representation of .marivo/runtime.json."""

    def __init__(
        self,
        *,
        workspace_root: str,
        host: str,
        port: int,
        pid: int,
        config_path: str,
        metadata_path: str,
    ) -> None:
        self.version: str = MANIFEST_VERSION
        self.workspace_root: str = workspace_root
        self.mode: str = MANIFEST_MODE
        self.host: str = host
        self.port: int = port
        self.pid: int = pid
        self.started_at: str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.config_path: str = config_path
        self.metadata_path: str = metadata_path
        self.base_url: str = f"http://{host}:{port}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "workspace_root": self.workspace_root,
            "mode": self.mode,
            "base_url": self.base_url,
            "host": self.host,
            "port": self.port,
            "pid": self.pid,
            "started_at": self.started_at,
            "config_path": self.config_path,
            "metadata_path": self.metadata_path,
        }

    def write_atomic(self, manifest_path: Path) -> None:
        """Atomic write via temp file + os.replace (producer obligation P-2)."""
        tmp_path = manifest_path.parent / f"runtime.json.tmp.{os.getpid()}"
        try:
            tmp_path.write_text(json.dumps(self.to_dict(), indent=2) + "\n")
            os.replace(str(tmp_path), str(manifest_path))
        except BaseException:
            with contextlib.suppress(OSError):
                tmp_path.unlink()
            raise


def read_manifest(manifest_path: Path) -> dict[str, Any]:
    """Read and parse runtime.json.

    Raises CliError with EXIT_RUNTIME_NOT_RUNNING if file not found.
    Raises CliError with EXIT_CONFIG_INVALID on JSON parse failure.
    """
    if not manifest_path.is_file():
        raise CliError(
            EXIT_RUNTIME_NOT_RUNNING,
            f"Runtime manifest not found: {manifest_path}",
            json_data={
                "error": {
                    "code": EXIT_RUNTIME_NOT_RUNNING,
                    "message": f"Runtime manifest not found: {manifest_path}",
                }
            },
        )

    try:
        data = json.loads(manifest_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        raise CliError(
            EXIT_CONFIG_INVALID,
            f"Runtime manifest is not valid JSON: {manifest_path}: {e}",
            json_data={
                "error": {
                    "code": EXIT_CONFIG_INVALID,
                    "message": f"Runtime manifest is not valid JSON: {manifest_path}: {e}",
                }
            },
        ) from e

    if not isinstance(data, dict):
        raise CliError(
            EXIT_CONFIG_INVALID,
            f"Runtime manifest is not a JSON object: {manifest_path}",
            json_data={
                "error": {
                    "code": EXIT_CONFIG_INVALID,
                    "message": f"Runtime manifest is not a JSON object: {manifest_path}",
                }
            },
        )

    return data


def validate_manifest_schema(data: dict[str, Any], manifest_path: str) -> None:
    """Validate parsed manifest against the schema contract.

    Checks: required fields, types, additionalProperties=false,
    INV-1 (base_url consistency), INV-2 (path absoluteness), INV-3 (path containment).

    Raises CliError with EXIT_CONFIG_INVALID on validation failure.
    """
    # Check required fields
    missing = [f for f in REQUIRED_FIELDS if f not in data]
    if missing:
        raise CliError(
            EXIT_CONFIG_INVALID,
            f"Runtime manifest missing required fields: {', '.join(missing)} in {manifest_path}",
            json_data={
                "error": {
                    "code": EXIT_CONFIG_INVALID,
                    "message": f"Runtime manifest missing required fields: {', '.join(missing)}",
                    "path": manifest_path,
                    "missing_fields": missing,
                }
            },
        )

    # Reject extra fields (additionalProperties: false)
    extra = [k for k in data if k not in REQUIRED_FIELDS]
    if extra:
        raise CliError(
            EXIT_CONFIG_INVALID,
            f"Runtime manifest has unknown fields: {', '.join(extra)} in {manifest_path}",
            json_data={
                "error": {
                    "code": EXIT_CONFIG_INVALID,
                    "message": f"Runtime manifest has unknown fields: {', '.join(extra)}",
                    "path": manifest_path,
                    "extra_fields": extra,
                }
            },
        )

    # Type checks
    _assert_type(data, "version", str, manifest_path)
    _assert_type(data, "workspace_root", str, manifest_path)
    _assert_type(data, "mode", str, manifest_path)
    _assert_type(data, "base_url", str, manifest_path)
    _assert_type(data, "host", str, manifest_path)
    _assert_type(data, "port", int, manifest_path)
    _assert_type(data, "pid", int, manifest_path)
    _assert_type(data, "started_at", str, manifest_path)
    _assert_type(data, "config_path", str, manifest_path)
    _assert_type(data, "metadata_path", str, manifest_path)

    if data["version"] != MANIFEST_VERSION:
        raise CliError(
            EXIT_CONFIG_INVALID,
            f"Unsupported runtime manifest version: {data['version']} in {manifest_path}",
            json_data={
                "error": {
                    "code": EXIT_CONFIG_INVALID,
                    "message": f"unsupported manifest version: {data['version']}",
                    "path": manifest_path,
                }
            },
        )

    if data["mode"] != MANIFEST_MODE:
        raise CliError(
            EXIT_CONFIG_INVALID,
            f"Unsupported runtime manifest mode: {data['mode']} in {manifest_path}",
            json_data={
                "error": {
                    "code": EXIT_CONFIG_INVALID,
                    "message": f"unsupported manifest mode: {data['mode']}",
                    "path": manifest_path,
                }
            },
        )

    if data["port"] <= 0 or data["port"] > 65535:
        raise CliError(
            EXIT_CONFIG_INVALID,
            f"Runtime manifest port out of range: {data['port']} in {manifest_path}",
            json_data={
                "error": {
                    "code": EXIT_CONFIG_INVALID,
                    "message": f"port out of range: {data['port']}",
                    "path": manifest_path,
                }
            },
        )

    if data["pid"] <= 0:
        raise CliError(
            EXIT_CONFIG_INVALID,
            f"Runtime manifest pid must be positive: {data['pid']} in {manifest_path}",
            json_data={
                "error": {
                    "code": EXIT_CONFIG_INVALID,
                    "message": f"pid must be positive: {data['pid']}",
                    "path": manifest_path,
                }
            },
        )

    # INV-1: base_url must equal http://{host}:{port}
    expected_base_url = f"http://{data['host']}:{data['port']}"
    if data["base_url"] != expected_base_url:
        raise CliError(
            EXIT_CONFIG_INVALID,
            f"Runtime manifest base_url mismatch: expected {expected_base_url}, got {data['base_url']} in {manifest_path}",
            json_data={
                "error": {
                    "code": EXIT_CONFIG_INVALID,
                    "message": f"base_url invariant violation: expected {expected_base_url}, got {data['base_url']}",
                    "path": manifest_path,
                }
            },
        )

    # INV-2: workspace_root, config_path, metadata_path must be absolute
    for field in ("workspace_root", "config_path", "metadata_path"):
        if not os.path.isabs(str(data[field])):
            raise CliError(
                EXIT_CONFIG_INVALID,
                f"Runtime manifest {field} is not an absolute path: {data[field]} in {manifest_path}",
                json_data={
                    "error": {
                        "code": EXIT_CONFIG_INVALID,
                        "message": f"{field} is not an absolute path: {data[field]}",
                        "path": manifest_path,
                    }
                },
            )

    # INV-3: config_path ends with {workspace_root}/.marivo/marivo.yaml
    ws = str(data["workspace_root"])
    if not str(data["config_path"]).endswith(f"{ws}/.marivo/marivo.yaml") and not str(
        data["config_path"]
    ).endswith(os.path.join(ws, ".marivo", "marivo.yaml")):
        raise CliError(
            EXIT_CONFIG_INVALID,
            f"Runtime manifest config_path does not match workspace_root in {manifest_path}",
            json_data={
                "error": {
                    "code": EXIT_CONFIG_INVALID,
                    "message": "config_path does not match workspace_root/.marivo/marivo.yaml",
                    "path": manifest_path,
                }
            },
        )

    if not str(data["metadata_path"]).endswith(f"{ws}/.marivo/metadata.sqlite") and not str(
        data["metadata_path"]
    ).endswith(os.path.join(ws, ".marivo", "metadata.sqlite")):
        raise CliError(
            EXIT_CONFIG_INVALID,
            f"Runtime manifest metadata_path does not match workspace_root in {manifest_path}",
            json_data={
                "error": {
                    "code": EXIT_CONFIG_INVALID,
                    "message": "metadata_path does not match workspace_root/.marivo/metadata.sqlite",
                    "path": manifest_path,
                }
            },
        )


def _assert_type(data: dict[str, Any], field: str, expected: type, manifest_path: str) -> None:
    if not isinstance(data[field], expected):
        raise CliError(
            EXIT_CONFIG_INVALID,
            f"Runtime manifest field '{field}' has wrong type: expected {expected.__name__}, got {type(data[field]).__name__} in {manifest_path}",
            json_data={
                "error": {
                    "code": EXIT_CONFIG_INVALID,
                    "message": f"field '{field}' has wrong type: expected {expected.__name__}, got {type(data[field]).__name__}",
                    "path": manifest_path,
                }
            },
        )


def delete_manifest(manifest_path: Path) -> None:
    """Delete runtime.json (used by runtime stop)."""
    with contextlib.suppress(FileNotFoundError):
        manifest_path.unlink()
