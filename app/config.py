from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class SyncConfig(BaseModel):
    mode: str = "by_select"  # "by_select" | "none"


class SourceConfig(BaseModel):
    name: str
    type: str
    connection: dict[str, Any] = Field(default_factory=dict)
    sync: SyncConfig = Field(default_factory=SyncConfig)


class EngineConfig(BaseModel):
    name: str
    type: str  # 'duckdb', 'trino'
    connection: dict[str, Any] = Field(default_factory=dict)


class BindingConfig(BaseModel):
    source: str  # display_name of source
    engine: str  # display_name of engine
    priority: int = 0
    namespace: dict[str, Any] = Field(default_factory=dict)


class UIConfig(BaseModel):
    enabled: bool = False
    admin_enabled: bool | None = None
    user_enabled: bool | None = None


class GovernancePolicyConfig(BaseModel):
    name: str
    type: str
    definition: dict[str, Any] = Field(default_factory=dict)
    scope: dict[str, Any] = Field(default_factory=dict)


class GovernanceQualityRuleConfig(BaseModel):
    name: str
    type: str
    table: str
    threshold: dict[str, Any] = Field(default_factory=dict)
    severity: str = "warn"


class GovernanceConfig(BaseModel):
    enabled: bool = True
    policies: list[GovernancePolicyConfig] = Field(default_factory=list)
    quality_rules: list[GovernanceQualityRuleConfig] = Field(default_factory=list)


class ObservabilityConfig(BaseModel):
    log_level: str = "INFO"
    metrics_enabled: bool = True


class MetadataConfig(BaseModel):
    engine: Literal["sqlite"]
    path: str


class CalendarSourceBindingConfig(BaseModel):
    source_name: str
    table_fqn: str
    calendar_version: str


class CalendarSnapshotConfig(BaseModel):
    resolved_calendar_source: str
    resolved_calendar_version: str
    region_code: str = "CN"
    effective_start: str
    effective_end: str
    holiday_source: CalendarSourceBindingConfig
    event_source: CalendarSourceBindingConfig | None = None


class CalendarConfig(BaseModel):
    default_region_code: str = "CN"
    snapshots: list[CalendarSnapshotConfig] = Field(default_factory=list)


class MarivoConfig(BaseModel):
    metadata: MetadataConfig | None = None
    sources: list[SourceConfig] = Field(default_factory=list)
    engines: list[EngineConfig] = Field(default_factory=list)
    bindings: list[BindingConfig] = Field(default_factory=list)
    calendar: CalendarConfig = Field(default_factory=CalendarConfig)
    ui: UIConfig = Field(default_factory=UIConfig)
    governance: GovernanceConfig = Field(default_factory=GovernanceConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)


# Backward-compatible alias
OmniDBConfig = MarivoConfig


def resolve_config_path(path: Path | None = None) -> Path:
    if path is not None:
        return path
    env = os.getenv("MARIVO_CONFIG")
    return Path(env) if env else Path("marivo.yaml")


def load_config(path: Path | None = None) -> MarivoConfig:
    """Load and validate the Marivo YAML config file.

    Resolution order:
    1. Explicit *path* argument
    2. ``MARIVO_CONFIG`` environment variable
    3. ``marivo.yaml`` in the current working directory

    Returns an empty config (no sources) when the file does not exist,
    so the application boots normally without a config file.
    """
    path = resolve_config_path(path)

    if not path.is_file():
        logger.debug("Config file not found at %s — using defaults", path)
        return MarivoConfig()

    raw = yaml.safe_load(path.read_text()) or {}
    return MarivoConfig.model_validate(raw)
