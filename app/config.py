from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class SyncConfig(BaseModel):
    mode: str = "all"  # "all" | "by_select" | "none"


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


class FactumConfig(BaseModel):
    sources: list[SourceConfig] = Field(default_factory=list)
    engines: list[EngineConfig] = Field(default_factory=list)
    bindings: list[BindingConfig] = Field(default_factory=list)
    ui: UIConfig = Field(default_factory=UIConfig)
    governance: GovernanceConfig = Field(default_factory=GovernanceConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)


# Backward-compatible alias
OmniDBConfig = FactumConfig


def load_config(path: Path | None = None) -> FactumConfig:
    """Load and validate the Factum YAML config file.

    Resolution order:
    1. Explicit *path* argument
    2. ``FACTUM_CONFIG`` environment variable
    3. ``factum.yaml`` in the current working directory

    Returns an empty config (no sources) when the file does not exist,
    so the application boots normally without a config file.
    """
    if path is None:
        env = os.getenv("FACTUM_CONFIG")
        path = Path(env) if env else Path("factum.yaml")

    if not path.is_file():
        logger.debug("Config file not found at %s — using defaults", path)
        return FactumConfig()

    raw = yaml.safe_load(path.read_text()) or {}
    return FactumConfig.model_validate(raw)
