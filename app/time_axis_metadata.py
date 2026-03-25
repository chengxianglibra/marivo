from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from app.storage.metadata import MetadataStore


TimeGrain = Literal["day", "hour"]

PHASE1_TIMEZONE_STRATEGY = "session_consistent_naive"
PHASE1_TIMEZONE_NOTE = (
    "Phase 1 assumes session-consistent naive timestamps only. Hour-grain "
    "time_scope boundaries and metadata-resolved timestamp columns must not "
    "encode timezone offsets."
)


@dataclass(slots=True)
class TimeAxisMetadataContext:
    entity_time_capabilities: dict[str, Any] | None = None
    source_time_capabilities: dict[str, Any] | None = None
    available_columns: list[str] = field(default_factory=list)
    timezone_strategy: str = PHASE1_TIMEZONE_STRATEGY
    timezone_note: str = PHASE1_TIMEZONE_NOTE


def normalize_time_capabilities(
    payload: Mapping[str, Any] | None,
    *,
    label: str = "time_capabilities",
) -> dict[str, Any] | None:
    if payload is None:
        return None
    if not isinstance(payload, Mapping):
        raise ValueError(f"{label} must be an object")

    analysis_time = payload.get("analysis_time")
    partition_time = payload.get("partition_time")
    if analysis_time is not None and not isinstance(analysis_time, Mapping):
        raise ValueError(f"{label}.analysis_time must be an object")
    if partition_time is not None and not isinstance(partition_time, Mapping):
        raise ValueError(f"{label}.partition_time must be an object")

    normalized_analysis = _normalize_analysis_time_section(analysis_time, label=f"{label}.analysis_time")
    normalized_partition = _normalize_partition_time_section(partition_time, label=f"{label}.partition_time")

    default_compare_grain = _optional_str(payload.get("default_compare_grain"))
    if default_compare_grain is not None and default_compare_grain not in {"day", "hour"}:
        raise ValueError(f"{label}.default_compare_grain must be 'day' or 'hour'")

    normalized: dict[str, Any] = {}
    if normalized_analysis:
        normalized["analysis_time"] = normalized_analysis
    if normalized_partition:
        normalized["partition_time"] = normalized_partition
    if default_compare_grain is not None:
        normalized["default_compare_grain"] = default_compare_grain
    return normalized or None


class TimeAxisMetadataProvider:
    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata

    def load_for_windowed_query(
        self,
        *,
        table_name: str,
        metric_name: str | None = None,
    ) -> TimeAxisMetadataContext:
        table_row = self._find_source_table_object(table_name)
        source_time_capabilities: dict[str, Any] | None = None
        available_columns: list[str] = []
        if table_row is not None:
            table_props = _decode_properties_json(table_row.get("properties_json"))
            source_time_capabilities = normalize_time_capabilities(
                table_props.get("time_capabilities"),
                label=f"source object '{table_row.get('fqn') or table_row.get('native_name')}' time_capabilities",
            )
            available_columns = self._load_table_columns(table_row["object_id"])

        entity_time_capabilities: dict[str, Any] | None = None
        if metric_name:
            entity_row = self._find_metric_entity(metric_name)
            if entity_row is not None:
                entity_props = _decode_properties_json(entity_row.get("properties_json"))
                entity_time_capabilities = normalize_time_capabilities(
                    entity_props.get("time_capabilities"),
                    label=f"semantic entity '{entity_row.get('name')}' time_capabilities",
                )

        return TimeAxisMetadataContext(
            entity_time_capabilities=entity_time_capabilities,
            source_time_capabilities=source_time_capabilities,
            available_columns=available_columns,
        )

    def _find_metric_entity(self, metric_name: str) -> dict[str, Any] | None:
        row = self.metadata.query_one(
            """
            SELECT e.entity_id, e.name, e.properties_json
            FROM semantic_metrics m
            JOIN semantic_entities e ON e.entity_id = m.entity_id
            WHERE m.name = ? AND m.status = 'published' AND e.status = 'published'
            """,
            [metric_name],
        )
        return dict(row) if row is not None else None

    def _find_source_table_object(self, table_name: str) -> dict[str, Any] | None:
        short_name = table_name.split(".")[-1]
        row = self.metadata.query_one(
            """
            SELECT object_id, fqn, native_name, properties_json
            FROM source_objects
            WHERE object_type = 'table' AND (fqn = ? OR native_name = ?)
            ORDER BY CASE WHEN fqn = ? THEN 0 ELSE 1 END, updated_at DESC
            """,
            [table_name, short_name, table_name],
        )
        return dict(row) if row is not None else None

    def _load_table_columns(self, table_object_id: str) -> list[str]:
        rows = self.metadata.query_rows(
            """
            SELECT native_name
            FROM source_objects
            WHERE parent_id = ? AND object_type = 'column'
            ORDER BY native_name
            """,
            [table_object_id],
        )
        return [str(row["native_name"]) for row in rows if row.get("native_name")]


def _normalize_analysis_time_section(
    payload: Mapping[str, Any] | None,
    *,
    label: str,
) -> dict[str, str]:
    if not payload:
        return {}
    normalized = {
        key: value
        for key, value in {
            "timestamp_column": _optional_str(payload.get("timestamp_column")),
            "fallback_date_column": _optional_str(payload.get("fallback_date_column")),
            "fallback_hour_column": _optional_str(payload.get("fallback_hour_column")),
        }.items()
        if value is not None
    }
    if normalized.get("fallback_hour_column") and not normalized.get("fallback_date_column"):
        raise ValueError(f"{label}.fallback_hour_column requires fallback_date_column")
    return normalized


def _normalize_partition_time_section(
    payload: Mapping[str, Any] | None,
    *,
    label: str,
) -> dict[str, str]:
    if not payload:
        return {}
    normalized = {
        key: value
        for key, value in {
            "date_column": _optional_str(payload.get("date_column")),
            "date_format": _optional_str(payload.get("date_format")),
            "hour_column": _optional_str(payload.get("hour_column")),
            "hour_format": _optional_str(payload.get("hour_format")),
        }.items()
        if value is not None
    }
    if normalized.get("hour_column") and not normalized.get("date_column"):
        raise ValueError(f"{label}.hour_column requires date_column")
    return normalized


def _decode_properties_json(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
    except Exception:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None
