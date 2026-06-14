"""Datasource secret providers and user-global plaintext cache."""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
import tomllib
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from marivo.datasource.errors import (
    DatasourceEnvVarMissingError,
    DatasourceSecretStorePermissionsError,
)

_INSECURE_SECRET_FILE_BITS = stat.S_IRWXG | stat.S_IRWXO


class SecretProvider(Protocol):
    def get(self, name: str) -> str | None: ...


@dataclass(frozen=True)
class ResolvedSecret:
    name: str
    value: str
    provider: SecretProvider


class EnvProvider:
    def get(self, name: str) -> str | None:
        value = os.environ.get(name)
        if value is None or value == "":
            return None
        return value


@dataclass(frozen=True)
class LocalPlaintextCache:
    path: Path

    @classmethod
    def default(cls) -> LocalPlaintextCache:
        return cls(Path.home() / ".marivo" / "secrets.toml")

    def get(self, name: str) -> str | None:
        values = self._read_all()
        value = values.get(name)
        if not isinstance(value, str) or value == "":
            return None
        return value

    def persist(self, name: str, value: str) -> None:
        if not self.persistence_enabled():
            return
        if value == "":
            return
        self._assert_write_location_is_safe()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path.parent.chmod(0o700)
        values = self._read_all()
        values[name] = value
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=str(self.path.parent),
            text=True,
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(_dump_toml(values))
            tmp_path.chmod(0o600)
            os.replace(tmp_path, self.path)
            self.path.chmod(0o600)
            self._assert_file_permissions_safe()
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    def persistence_enabled(self) -> bool:
        if os.environ.get("MARIVO_PERSIST_SECRETS") == "0":
            return False
        return not os.environ.get("CI")

    def _read_all(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        self._assert_file_permissions_safe()
        parsed = tomllib.loads(self.path.read_text())
        return {str(key): value for key, value in parsed.items() if isinstance(value, str)}

    def _assert_file_permissions_safe(self) -> None:
        mode = stat.S_IMODE(self.path.stat().st_mode)
        if mode & _INSECURE_SECRET_FILE_BITS:
            raise DatasourceSecretStorePermissionsError(
                message=(
                    f"datasource secret store {self.path} has insecure permissions "
                    f"{oct(mode)}; expected 0o600"
                ),
                details={"path": str(self.path), "mode": mode},
            )

    def _assert_write_location_is_safe(self) -> None:
        resolved_parent = self.path.parent.resolve()
        for candidate in (resolved_parent, *resolved_parent.parents):
            if (candidate / ".git").exists():
                mode = stat.S_IMODE(candidate.stat().st_mode)
                raise DatasourceSecretStorePermissionsError(
                    message=(
                        f"refusing to write datasource secret store {self.path} "
                        f"inside a git repository at {candidate}"
                    ),
                    details={"path": str(self.path), "mode": mode},
                )


def _dump_toml(values: dict[str, str]) -> str:
    return "".join(
        f"{json.dumps(name)} = {json.dumps(value)}\n" for name, value in sorted(values.items())
    )


def default_chain() -> tuple[SecretProvider, ...]:
    return (EnvProvider(), LocalPlaintextCache.default())


def conventional_env_var(datasource_name: str, field_stem: str) -> str:
    """Return the conventional env var name for a datasource secret field.

    Convention: ``MARIVO_{DATASOURCE_NAME}_{FIELD_STEM}`` (uppercased,
    non-alphanumeric characters replaced with underscores).

    Args:
        datasource_name: The datasource identifier (e.g. ``"warehouse"``).
        field_stem: The sensitive field stem (e.g. ``"password"``).

    Returns:
        The conventional env var name (e.g. ``"MARIVO_WAREHOUSE_PASSWORD"``).

    Example:
        >>> conventional_env_var("warehouse", "password")
        'MARIVO_WAREHOUSE_PASSWORD'
        >>> conventional_env_var("analytics_db", "token")
        'MARIVO_ANALYTICS_DB_TOKEN'
    """
    sanitize = re.compile(r"[^0-9A-Za-z]+")
    ds_part = sanitize.sub("_", datasource_name).upper()
    field_part = sanitize.sub("_", field_stem).upper()
    return f"MARIVO_{ds_part}_{field_part}"


def resolve(
    name: str,
    *,
    datasource: str | None = None,
    field: str | None = None,
    providers: tuple[SecretProvider, ...] | None = None,
) -> ResolvedSecret:
    for provider in providers or default_chain():
        value = provider.get(name)
        if value is not None and value != "":
            return ResolvedSecret(name=name, value=value, provider=provider)
    raise DatasourceEnvVarMissingError(
        message=(
            f"env var {name!r}"
            + (f" for datasource {datasource!r}" if datasource else "")
            + (f" field {field!r}" if field else "")
            + " is not set and is not present in the datasource secret cache"
        ),
        details={"datasource": datasource or "", "field": field or "", "env_var": name},
    )


def resolve_optional(
    name: str,
    *,
    providers: tuple[SecretProvider, ...] | None = None,
) -> ResolvedSecret | None:
    """Try to resolve a secret, returning ``None`` if not found.

    Like :func:`resolve` but returns ``None`` instead of raising when
    no provider has the value.  Used for conventional env var fallbacks
    where the absence of a value is not an error.
    """
    for provider in providers or default_chain():
        value = provider.get(name)
        if value is not None and value != "":
            return ResolvedSecret(name=name, value=value, provider=provider)
    return None


def persist_env_sourced(resolved: tuple[ResolvedSecret, ...]) -> None:
    cache = LocalPlaintextCache.default()
    for item in resolved:
        if isinstance(item.provider, EnvProvider):
            cache.persist(item.name, item.value)


_ENV_SOURCED_SECRETS_ATTR = "_marivo_env_sourced_secrets"


def remember_env_sourced(backend: object, resolved: tuple[ResolvedSecret, ...]) -> None:
    """Stash env-sourced secret provenance on a live backend object."""
    with suppress(Exception):
        setattr(backend, _ENV_SOURCED_SECRETS_ATTR, resolved)


def persist_backend_env_sourced(backend: object) -> None:
    """Persist env-sourced secrets previously stashed on a backend."""
    resolved = getattr(backend, _ENV_SOURCED_SECRETS_ATTR, ())
    if isinstance(resolved, tuple):
        persist_env_sourced(resolved)
