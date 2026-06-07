"""Resolve report publish target configuration (filesystem now; S3 deferred)."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from marivo.analysis.errors import ReportPublishConfigError

_DEFAULT_PREFIX_TEMPLATE = "marivo/users/{username}"
_ENV_DIR = "MARIVO_PUBLISH_DIR"
_ENV_PREFIX = "MARIVO_PUBLISH_PREFIX"
_LOCAL_CONFIG = ".marivo/publish.local.toml"
_PROJECT_CONFIG = "marivo.publish.toml"


@dataclass(frozen=True)
class PublishConfig:
    base: str
    prefix_template: str = _DEFAULT_PREFIX_TEMPLATE


def _local_storage(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    storage = parsed.get("storage")
    local = storage.get("local") if isinstance(storage, dict) else None
    if not isinstance(local, dict):
        return {}
    return {str(key): value for key, value in local.items() if isinstance(value, str)}


def _config_values(env: Mapping[str, str], root: Path) -> tuple[str | None, str | None]:
    base: str | None = env.get(_ENV_DIR) or None
    prefix: str | None = env.get(_ENV_PREFIX) or None
    for config_name in (_LOCAL_CONFIG, _PROJECT_CONFIG):
        if base is not None and prefix is not None:
            break
        values = _local_storage(root / config_name)
        if base is None:
            base = values.get("dir") or None
        if prefix is None:
            prefix = values.get("prefix") or None
    return base, prefix


def resolve_publish_prefix(
    *,
    env: Mapping[str, str] | None = None,
    project_root: str | Path | None = None,
) -> str:
    """Resolve the publish prefix template (env, then config files, then default)."""
    env_map = os.environ if env is None else env
    root = Path(project_root) if project_root is not None else Path.cwd()
    _, prefix = _config_values(env_map, root)
    return prefix or _DEFAULT_PREFIX_TEMPLATE


def resolve_publish_config(
    target: str | None = None,
    *,
    env: Mapping[str, str] | None = None,
    project_root: str | Path | None = None,
) -> PublishConfig:
    """Resolve the publish base and prefix.

    Base precedence: explicit ``target`` > ``MARIVO_PUBLISH_DIR`` > local config
    > project config. Prefix precedence: ``MARIVO_PUBLISH_PREFIX`` > local config
    > project config > default ``marivo/users/{username}``.
    """
    env_map = os.environ if env is None else env
    root = Path(project_root) if project_root is not None else Path.cwd()
    resolved_base, resolved_prefix = _config_values(env_map, root)
    base = target or resolved_base
    if base is None:
        raise ReportPublishConfigError(
            message=(
                "no publish target resolved; pass target=, set "
                f"{_ENV_DIR}, or set [storage.local] dir in {_PROJECT_CONFIG}"
            ),
            details={"env_dir": _ENV_DIR, "project_config": _PROJECT_CONFIG},
        )
    return PublishConfig(base=base, prefix_template=resolved_prefix or _DEFAULT_PREFIX_TEMPLATE)
