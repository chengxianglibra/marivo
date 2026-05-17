from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from marivo.adapters.metadata import MetadataStore
from marivo.time_contracts import normalize_timestamp_format, optional_str

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
    time_field_expressions: dict[str, str] = field(default_factory=dict)
    time_field_data_types: dict[str, str] = field(default_factory=dict)
    timezone_strategy: str = PHASE1_TIMEZONE_STRATEGY
    timezone_note: str = PHASE1_TIMEZONE_NOTE
    has_time_binding: bool = False


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

    normalized_analysis = _normalize_analysis_time_section(
        analysis_time, label=f"{label}.analysis_time"
    )
    normalized_partition = _normalize_partition_time_section(
        partition_time, label=f"{label}.partition_time"
    )

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

    def load_available_columns(self, table_name: str) -> list[str]:
        rows = self.metadata.query_rows(
            "SELECT f.name FROM semantic_fields f "
            "JOIN semantic_datasets d ON f.dataset_id = d.dataset_id "
            "WHERE d.source = ? ORDER BY f.position",
            [table_name],
        )
        return [row["name"] for row in rows]

    def load_for_windowed_query(
        self,
        *,
        table_name: str,
        metric_name: str | None = None,
    ) -> TimeAxisMetadataContext:
        available_rows = self._load_field_rows_for_metric(table_name, metric_name)
        if not available_rows:
            available_rows = self._load_field_rows_by_source(table_name)
        available_columns = [row["name"] for row in available_rows]
        if not available_columns:
            return TimeAxisMetadataContext(available_columns=[])

        time_rows = [row for row in available_rows if int(row.get("is_time") or 0) == 1]
        time_field_names = [row["name"] for row in time_rows]
        time_field_expressions = {
            row["name"]: expression
            for row in time_rows
            if (expression := _extract_ansi_sql(row.get("expression"))) is not None
        }
        time_field_data_types = {
            row["name"]: data_type
            for row in time_rows
            if (data_type := _optional_str(row.get("data_type"))) is not None
        }

        entity_time_capabilities = _infer_time_capabilities(time_field_names)

        return TimeAxisMetadataContext(
            entity_time_capabilities=entity_time_capabilities,
            source_time_capabilities=None,
            available_columns=available_columns,
            time_field_expressions=time_field_expressions,
            time_field_data_types=time_field_data_types,
            has_time_binding=bool(time_field_names),
        )

    def _load_field_rows_for_metric(
        self,
        table_name: str,
        metric_name: str | None,
    ) -> list[dict[str, Any]]:
        normalized_metric_name = _metric_name(metric_name)
        if normalized_metric_name is None:
            return []
        return self.metadata.query_rows(
            "SELECT f.name, f.expression, f.data_type, f.is_time FROM semantic_fields f "
            "JOIN semantic_datasets d ON f.dataset_id = d.dataset_id "
            "JOIN semantic_metrics m ON m.model_id = d.model_id "
            "WHERE d.source = ? AND m.name = ? ORDER BY f.position",
            [table_name, normalized_metric_name],
        )

    def _load_field_rows_by_source(self, table_name: str) -> list[dict[str, Any]]:
        return self.metadata.query_rows(
            "SELECT f.name, f.expression, f.data_type, f.is_time FROM semantic_fields f "
            "JOIN semantic_datasets d ON f.dataset_id = d.dataset_id "
            "WHERE d.source = ? ORDER BY f.position",
            [table_name],
        )


def _infer_time_capabilities(time_fields: list[str]) -> dict[str, Any] | None:
    if not time_fields:
        return None

    analysis_time: dict[str, str] = {}
    partition_time: dict[str, str] = {}

    for name in time_fields:
        lowered = name.lower()
        if lowered in ("ctime", "event_time", "timestamp", "created_at", "updated_at", "time"):
            analysis_time.setdefault("timestamp_column", name)
        elif lowered in ("log_date", "event_date", "dt", "date", "day"):
            if "fallback_date_column" not in analysis_time:
                analysis_time["fallback_date_column"] = name
            partition_time["date_column"] = name
            if lowered in ("log_date", "dt"):
                partition_time["date_format"] = "yyyymmdd"
        elif lowered in ("log_hour", "hour", "event_hour"):
            analysis_time["fallback_hour_column"] = name
            partition_time["hour_column"] = name
            partition_time["hour_format"] = "int"

    if not analysis_time and not partition_time:
        first = time_fields[0]
        analysis_time["timestamp_column"] = first

    result: dict[str, Any] = {}
    if analysis_time:
        result["analysis_time"] = analysis_time
    if partition_time:
        result["partition_time"] = partition_time
    return result or None


def _normalize_analysis_time_section(
    payload: Mapping[str, Any] | None,
    *,
    label: str,
) -> dict[str, str]:
    if not payload:
        return {}
    timestamp_format = _normalize_timestamp_format(payload.get("timestamp_format"))
    normalized = {
        key: value
        for key, value in {
            "timestamp_column": _optional_str(payload.get("timestamp_column")),
            "timestamp_format": timestamp_format,
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


def _normalize_timestamp_format(value: Any) -> str | None:
    return normalize_timestamp_format(value)


def _optional_str(value: Any) -> str | None:
    return optional_str(value)


def _metric_name(metric_name: str | None) -> str | None:
    normalized = _optional_str(metric_name)
    if normalized is None:
        return None
    return normalized.removeprefix("metric.")


def _extract_ansi_sql(expression_json: Any) -> str | None:
    if isinstance(expression_json, str):
        try:
            expression = json.loads(expression_json)
        except json.JSONDecodeError:
            return None
    elif isinstance(expression_json, Mapping):
        expression = expression_json
    else:
        return None
    dialects = expression.get("dialects") if isinstance(expression, Mapping) else None
    if not isinstance(dialects, list):
        return None
    for dialect in dialects:
        if not isinstance(dialect, Mapping):
            continue
        if str(dialect.get("dialect") or "").upper() != "ANSI_SQL":
            continue
        sql = _optional_str(dialect.get("expression"))
        if sql is not None:
            return sql
    for dialect in dialects:
        if not isinstance(dialect, Mapping):
            continue
        sql = _optional_str(dialect.get("expression"))
        if sql is not None:
            return sql
    return None
