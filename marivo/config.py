from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qsl, unquote, urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from marivo.redaction import redact_mapping, redact_sensitive_text

logger = logging.getLogger(__name__)


class ObservabilityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    log_level: str = "INFO"
    metrics_enabled: bool = True
    log_dir: str | None = None


class MetadataConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engine: Literal["sqlite", "mysql"]
    path: str | None = None
    dsn: str | None = None
    host: str | None = None
    port: int = 3306
    database: str | None = None
    user: str | None = None
    password: str | None = None
    connect_timeout: int = 10
    pool_size: int = 5
    ssl: dict[str, Any] | bool | None = None

    @model_validator(mode="after")
    def validate_backend_fields(self) -> MetadataConfig:
        if self.engine == "sqlite":
            if self.path is None or not self.path.strip():
                raise ValueError("metadata.path is required when metadata.engine=sqlite")
            forbidden = {
                "dsn": self.dsn,
                "host": self.host,
                "database": self.database,
                "user": self.user,
                "password": self.password,
                "ssl": self.ssl,
            }
            provided = [name for name, value in forbidden.items() if value not in (None, "")]
            if provided:
                raise ValueError(
                    "metadata.engine=sqlite does not accept MySQL fields: "
                    + ", ".join(sorted(provided))
                )
            return self

        if self.connect_timeout <= 0:
            raise ValueError("metadata.connect_timeout must be positive")
        if self.pool_size <= 0:
            raise ValueError("metadata.pool_size must be positive")
        if self.path is not None and self.path.strip():
            raise ValueError("metadata.engine=mysql does not accept sqlite path")

        normalized = self.mysql_connection_config()
        missing = [key for key in ("host", "database", "user") if normalized.get(key) in (None, "")]
        if missing:
            raise ValueError(
                "metadata.engine=mysql requires dsn or explicit fields: " + ", ".join(missing)
            )
        return self

    def mysql_connection_config(self) -> dict[str, Any]:
        """Return normalized MySQL connection fields without mutating the model."""

        config: dict[str, Any] = {
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "user": self.user,
            "password": self.password,
            "connect_timeout": self.connect_timeout,
            "pool_size": self.pool_size,
            "ssl": self.ssl,
            "dsn": self.dsn,
        }
        if self.dsn:
            config.update(_parse_mysql_dsn(self.dsn))
        return config


class MarivoConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: str | None = None
    metadata: MetadataConfig | None = None
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)


# Backward-compatible alias
OmniDBConfig = MarivoConfig


def resolve_config_path(path: Path | None = None) -> Path:
    if path is not None:
        return path
    env = os.getenv("MARIVO_CONFIG")
    return Path(env) if env else Path("marivo.yaml")


def resolve_metadata_path(config_path: Path, configured_path: str) -> Path:
    """Resolve a metadata SQLite path from runtime config.

    Local runtime bootstrap stores config at ``<workspace>/.marivo/marivo.yaml`` and
    freezes ``metadata.path`` as ``.marivo/metadata.sqlite``. In that shape, the
    configured path is intended to remain workspace-root relative rather than
    nesting another ``.marivo`` directory under the config directory.
    """
    metadata_path = Path(configured_path)
    if metadata_path.is_absolute():
        return metadata_path
    if (
        config_path.name == "marivo.yaml"
        and config_path.parent.name == ".marivo"
        and metadata_path.parts
        and metadata_path.parts[0] == ".marivo"
    ):
        return config_path.parent.parent / metadata_path
    return config_path.parent / metadata_path


def _parse_mysql_dsn(dsn: str) -> dict[str, Any]:
    parsed = urlparse(dsn)
    if parsed.scheme not in {"mysql", "mysql+pymysql"}:
        raise ValueError("metadata.dsn must use mysql:// or mysql+pymysql://")
    database = parsed.path.lstrip("/") or None
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    result: dict[str, Any] = {
        "host": parsed.hostname,
        "port": parsed.port or 3306,
        "database": unquote(database) if database is not None else None,
        "user": unquote(parsed.username) if parsed.username is not None else None,
        "password": unquote(parsed.password) if parsed.password is not None else None,
    }
    if "connect_timeout" in query:
        result["connect_timeout"] = int(query["connect_timeout"])
    if "pool_size" in query:
        result["pool_size"] = int(query["pool_size"])
    if "ssl" in query:
        result["ssl"] = query["ssl"].lower() in {"1", "true", "yes", "on"}
    return result


def load_config(path: Path | None = None) -> MarivoConfig:
    """Load and validate the Marivo YAML config file.

    Resolution order:
    1. Explicit *path* argument
    2. ``MARIVO_CONFIG`` environment variable
    3. ``marivo.yaml`` in the current working directory

    Returns an empty runtime config when the file does not exist,
    so the application boots normally without a config file.
    """
    path = resolve_config_path(path)

    if not path.is_file():
        logger.debug("Config file not found at %s — using defaults", path)
        return MarivoConfig()

    raw = yaml.safe_load(path.read_text()) or {}
    try:
        return MarivoConfig.model_validate(raw)
    except Exception as exc:
        safe_input: object = redact_mapping(raw) if isinstance(raw, dict) else raw
        raise ValueError(
            f"Invalid Marivo config at {path}: {redact_sensitive_text(exc)}; input={safe_input}"
        ) from exc
