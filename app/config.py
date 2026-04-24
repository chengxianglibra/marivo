from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


class GovernancePolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: str
    definition: dict[str, object] = Field(default_factory=dict)
    scope: dict[str, object] = Field(default_factory=dict)


class GovernanceQualityRuleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: str
    table: str
    threshold: dict[str, object] = Field(default_factory=dict)
    severity: str = "warn"


class GovernanceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    policies: list[GovernancePolicyConfig] = Field(default_factory=list)
    quality_rules: list[GovernanceQualityRuleConfig] = Field(default_factory=list)


class ObservabilityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    log_level: str = "INFO"
    metrics_enabled: bool = True


class MetadataConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engine: Literal["sqlite"]
    path: str


class CalendarSourceBindingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_name: str
    table_fqn: str
    calendar_version: str


class CalendarSnapshotConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolved_calendar_source: str
    resolved_calendar_version: str
    region_code: str = "CN"
    effective_start: str
    effective_end: str
    holiday_source: CalendarSourceBindingConfig
    event_source: CalendarSourceBindingConfig | None = None


class CalendarConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_region_code: str = "CN"
    snapshots: list[CalendarSnapshotConfig] = Field(default_factory=list)


class MarivoConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metadata: MetadataConfig | None = None
    calendar: CalendarConfig = Field(default_factory=CalendarConfig)
    governance: GovernanceConfig = Field(default_factory=GovernanceConfig)
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
    return MarivoConfig.model_validate(raw)
