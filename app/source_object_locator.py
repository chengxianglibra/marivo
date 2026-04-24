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

    fqn = optional_str(source_object.get("fqn")) or optional_str(source_object.get("native_name"))
    if fqn is None:
        return {}

    parts = [part.strip() for part in fqn.split(".") if part.strip()]
    if len(parts) >= 3:
        return {"catalog": parts[-3], "schema": parts[-2], "table": parts[-1]}

    synthetic_catalog = _source_synthetic_catalog(
        metadata,
        optional_str(source_object.get("source_id")),
        synthetic_catalog_cache=synthetic_catalog_cache,
    )
    if len(parts) == 2:
        return {"catalog": synthetic_catalog, "schema": parts[0], "table": parts[1]}
    return {"catalog": synthetic_catalog, "schema": None, "table": parts[0]}


def has_explicit_authority_locator(source_object: dict[str, Any]) -> bool:
    raw_locator = source_object.get("authority_locator_json")
    if raw_locator is None:
        return False
    try:
        decoded = json.loads(str(raw_locator))
    except json.JSONDecodeError:
        return False
    return isinstance(decoded, dict) and bool(decoded.get("table"))


def execution_locator_from_source_fqn(source_object: dict[str, Any]) -> dict[str, Any] | None:
    fqn = optional_str(source_object.get("fqn")) or optional_str(source_object.get("native_name"))
    if fqn is None:
        return None

    parts = [part.strip() for part in fqn.split(".") if part.strip()]
    if len(parts) >= 3:
        return {"catalog": parts[-3], "schema": parts[-2], "table": parts[-1]}
    if len(parts) == 2:
        return {"catalog": None, "schema": parts[0], "table": parts[1]}
    if len(parts) == 1:
        return {"catalog": None, "schema": None, "table": parts[0]}
    return None


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


def _source_synthetic_catalog(
    metadata: MetadataStore,
    source_id: str | None,
    *,
    synthetic_catalog_cache: dict[str, str | None] | None = None,
) -> str | None:
    if source_id is None:
        return None
    if synthetic_catalog_cache is not None and source_id in synthetic_catalog_cache:
        return synthetic_catalog_cache[source_id]

    row = metadata.query_one(
        "SELECT authority_json FROM sources WHERE source_id = ?",
        [source_id],
    )
    synthetic_catalog: str | None = None
    if row is not None:
        try:
            authority = json.loads(str(row["authority_json"]))
        except json.JSONDecodeError:
            authority = {}
        if isinstance(authority, dict):
            synthetic_catalog = optional_str(authority.get("synthetic_catalog"))

    if synthetic_catalog_cache is not None:
        synthetic_catalog_cache[source_id] = synthetic_catalog
    return synthetic_catalog
