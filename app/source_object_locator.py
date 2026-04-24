from __future__ import annotations

import json
from typing import Any

from app.storage.metadata import MetadataStore


def optional_str(value: Any) -> str | None:
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return None


def normalize_source_object_authority_locator(
    metadata: MetadataStore,
    source_object: dict[str, Any],
    *,
    synthetic_catalog_cache: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    _ = metadata
    _ = synthetic_catalog_cache
    locator = source_object.get("authority_locator")
    if isinstance(locator, dict) and locator.get("table"):
        return dict(locator)

    raw_locator = source_object.get("authority_locator_json")
    if raw_locator is not None:
        try:
            decoded = json.loads(str(raw_locator))
        except json.JSONDecodeError:
            decoded = {}
        if isinstance(decoded, dict) and decoded.get("table"):
            return dict(decoded)
    return {}


def has_explicit_authority_locator(source_object: dict[str, Any]) -> bool:
    raw_locator = source_object.get("authority_locator_json")
    if raw_locator is None:
        return False
    try:
        decoded = json.loads(str(raw_locator))
    except json.JSONDecodeError:
        return False
    return isinstance(decoded, dict) and bool(decoded.get("table"))


def qualify_execution_locator(
    execution_locator: dict[str, Any],
    *,
    engine_type: str | None = None,
) -> str:
    parts = [
        str(value)
        for key in ("catalog", "schema", "table")
        for value in [execution_locator.get(key)]
        if isinstance(value, str) and value
    ]
    qualified = ".".join(parts)
    if engine_type == "duckdb" and qualified.startswith("main."):
        return qualified.removeprefix("main.")
    return qualified
