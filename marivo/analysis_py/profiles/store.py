"""Persistent storage for user-scope datasource profiles.

Layout: ``$MARIVO_HOME/profiles/profiles.json`` where ``MARIVO_HOME`` falls
back to ``~/.marivo``. The on-disk format is intentionally simple JSON so we
do not pull in a TOML serializer dependency. Files are created with mode
``0600`` and the parent directory with mode ``0700``.

Sensitive credentials must not be persisted as literals. Callers pass them via
``<field>_env`` references that are resolved against ``os.environ`` at
backend-build time. The store layer enforces the whitelist on every write.
"""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from marivo.analysis_py.errors import (
    ProfileFieldInvalidError,
    ProfileSchemaVersionError,
    ProfileSecretInPlaintextError,
)

SCHEMA_VERSION: Final[int] = 1

SENSITIVE_FIELD_STEMS: Final[frozenset[str]] = frozenset(
    {
        "password",
        "token",
        "secret",
        "secret_key",
        "access_key",
        "private_key",
        "passphrase",
        "api_key",
    }
)


@dataclass(frozen=True)
class StoredProfile:
    """A single profile entry as persisted on disk (no env_ref resolution)."""

    name: str
    backend_type: str
    fields: Mapping[str, Any]


def _home_root() -> Path:
    override = os.environ.get("MARIVO_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".marivo"


def profiles_path() -> Path:
    return _home_root() / "profiles" / "profiles.json"


def _ensure_parent(path: Path) -> None:
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    with suppress(PermissionError, NotImplementedError):
        os.chmod(parent, stat.S_IRWXU)  # 0700


def _is_env_ref_key(key: str) -> bool:
    return key.endswith("_env") and len(key) > len("_env")


def _stem_for_env_key(key: str) -> str:
    return key[: -len("_env")]


def _validate_field(name: str, datasource: str, field_name: str, value: Any) -> None:
    if not isinstance(field_name, str) or not field_name:
        raise ProfileFieldInvalidError(
            message=f"profile {name!r} has an empty field name",
            details={"datasource": datasource, "field": field_name, "reason": "empty field name"},
        )
    if _is_env_ref_key(field_name):
        if not isinstance(value, str) or not value:
            raise ProfileFieldInvalidError(
                message=(
                    f"profile {datasource!r} field {field_name!r} must be a non-empty "
                    "env var name (string)"
                ),
                details={
                    "datasource": datasource,
                    "field": field_name,
                    "reason": "env_ref must reference an env var name as a string",
                },
            )
        return
    if field_name in SENSITIVE_FIELD_STEMS:
        raise ProfileSecretInPlaintextError(
            message=(
                f"profile {datasource!r} field {field_name!r} is sensitive and must not "
                "be stored as a literal"
            ),
            details={"datasource": datasource, "field": field_name},
        )
    if not _is_jsonable_scalar(value):
        raise ProfileFieldInvalidError(
            message=(
                f"profile {datasource!r} field {field_name!r} has unsupported value type "
                f"{type(value).__name__}"
            ),
            details={
                "datasource": datasource,
                "field": field_name,
                "reason": (
                    "profile fields must be primitive JSON values (str, int, float, bool, None) "
                    "or lists of those"
                ),
            },
        )


def _is_jsonable_scalar(value: Any) -> bool:
    if isinstance(value, str | int | float | bool) or value is None:
        return True
    if isinstance(value, list):
        return all(_is_jsonable_scalar(item) for item in value)
    return False


def _validate_entry(name: str, entry: Mapping[str, Any]) -> None:
    if not isinstance(name, str) or not name:
        raise ProfileFieldInvalidError(
            message="profile name must be a non-empty string",
            details={"datasource": name, "field": "<name>", "reason": "empty profile name"},
        )
    backend_type = entry.get("backend_type")
    if not isinstance(backend_type, str) or not backend_type:
        raise ProfileFieldInvalidError(
            message=f"profile {name!r} missing required 'backend_type' string",
            details={
                "datasource": name,
                "field": "backend_type",
                "reason": "backend_type is required and must be a non-empty string",
            },
        )
    for field_name, value in entry.items():
        if field_name == "backend_type":
            continue
        _validate_field(name, name, field_name, value)


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    _ensure_parent(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    data = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(data)
    except Exception:
        try:
            os.unlink(tmp)
        finally:
            raise
    os.replace(tmp, path)
    with suppress(PermissionError, NotImplementedError):
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600


def _empty_registry() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "profiles": {}}


def _read_registry() -> dict[str, Any]:
    path = profiles_path()
    if not path.is_file():
        return _empty_registry()
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ProfileFieldInvalidError(
            message=f"profile registry at {path} is not valid JSON",
            details={
                "datasource": "<registry>",
                "field": "<file>",
                "reason": f"json decode error: {exc.msg}",
            },
        ) from exc
    if not isinstance(raw, dict):
        raise ProfileFieldInvalidError(
            message=f"profile registry at {path} must be a JSON object",
            details={
                "datasource": "<registry>",
                "field": "<file>",
                "reason": f"got top-level {type(raw).__name__}",
            },
        )
    version = raw.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ProfileSchemaVersionError(
            message=(
                f"profile registry schema_version={version!r} is not supported "
                f"by this marivo.analysis_py (expected {SCHEMA_VERSION})"
            ),
            details={
                "got": version,
                "expected": SCHEMA_VERSION,
                "path": str(path),
            },
        )
    profiles = raw.get("profiles")
    if not isinstance(profiles, dict):
        raise ProfileFieldInvalidError(
            message=f"profile registry at {path} missing 'profiles' object",
            details={
                "datasource": "<registry>",
                "field": "profiles",
                "reason": "profiles must be a JSON object",
            },
        )
    for name, entry in profiles.items():
        if not isinstance(entry, dict):
            raise ProfileFieldInvalidError(
                message=f"profile {name!r} must be a JSON object",
                details={
                    "datasource": name,
                    "field": "<entry>",
                    "reason": f"got {type(entry).__name__}",
                },
            )
        _validate_entry(name, entry)
    return raw


def load_all() -> dict[str, StoredProfile]:
    registry = _read_registry()
    return {
        name: StoredProfile(
            name=name,
            backend_type=str(entry["backend_type"]),
            fields={k: v for k, v in entry.items() if k != "backend_type"},
        )
        for name, entry in registry["profiles"].items()
    }


def load_one(name: str) -> StoredProfile | None:
    return load_all().get(name)


def save_one(name: str, backend_type: str, fields: Mapping[str, Any]) -> StoredProfile:
    entry: dict[str, Any] = {"backend_type": backend_type, **fields}
    _validate_entry(name, entry)
    registry = _read_registry()
    registry["profiles"][name] = entry
    _atomic_write_json(profiles_path(), registry)
    return StoredProfile(name=name, backend_type=backend_type, fields=dict(fields))


def delete_one(name: str) -> bool:
    registry = _read_registry()
    if name not in registry["profiles"]:
        return False
    del registry["profiles"][name]
    _atomic_write_json(profiles_path(), registry)
    return True


def list_names() -> list[str]:
    return sorted(load_all().keys())
