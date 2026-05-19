from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from marivo.adapters.metadata import MetadataStore
from marivo.time_contracts import optional_str

TimeGrain = Literal["day", "hour"]

PHASE1_TIMEZONE_STRATEGY = "session_consistent_naive"
PHASE1_TIMEZONE_NOTE = (
    "Phase 1 assumes session-consistent naive timestamps only. Hour-grain "
    "time_scope boundaries and metadata-resolved timestamp columns must not "
    "encode timezone offsets."
)


@dataclass(slots=True)
class TimeAxisMetadataContext:
    available_columns: list[str] = field(default_factory=list)
    time_field_expressions: dict[str, str] = field(default_factory=dict)
    time_field_data_types: dict[str, str] = field(default_factory=dict)
    time_field_formats: dict[str, str] = field(default_factory=dict)
    time_field_required_prefixes: dict[str, str] = field(default_factory=dict)
    time_field_support_min_granularities: dict[str, str] = field(default_factory=dict)
    timezone_strategy: str = PHASE1_TIMEZONE_STRATEGY
    timezone_note: str = PHASE1_TIMEZONE_NOTE
    has_time_binding: bool = False


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
        engine_type: str = "duckdb",
    ) -> TimeAxisMetadataContext:
        available_rows = self._load_field_rows_for_metric(table_name, metric_name)
        if not available_rows:
            available_rows = self._load_field_rows_by_source(table_name)
        available_columns = [row["name"] for row in available_rows]
        if not available_columns:
            return TimeAxisMetadataContext(available_columns=[])

        time_rows = [row for row in available_rows if int(row.get("is_time") or 0) == 1]
        time_field_expressions = {
            row["name"]: expression
            for row in time_rows
            if (
                expression := _select_dialect_expression(
                    row.get("expression"),
                    engine_type=engine_type,
                )
            )
            is not None
        }
        time_field_data_types = {
            row["name"]: data_type
            for row in time_rows
            if (data_type := _optional_str(row.get("data_type"))) is not None
        }
        time_field_formats = {
            row["name"]: format_value
            for row in time_rows
            if (format_value := _optional_str(row.get("format"))) is not None
        }
        time_field_support_min_granularities = {
            row["name"]: support_min_granularity
            for row in time_rows
            if (support_min_granularity := _optional_str(row.get("support_min_granularity")))
            is not None
        }
        time_field_required_prefixes = {
            row["name"]: required_prefix
            for row in time_rows
            if (required_prefix := _optional_str(row.get("required_prefix"))) is not None
        }

        return TimeAxisMetadataContext(
            available_columns=available_columns,
            time_field_expressions=time_field_expressions,
            time_field_data_types=time_field_data_types,
            time_field_formats=time_field_formats,
            time_field_required_prefixes=time_field_required_prefixes,
            time_field_support_min_granularities=time_field_support_min_granularities,
            has_time_binding=bool(time_rows),
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
            "SELECT f.name, f.expression, f.data_type, f.format, f.is_time, f.support_min_granularity, f.required_prefix "
            "FROM semantic_fields f "
            "JOIN semantic_datasets d ON f.dataset_id = d.dataset_id "
            "JOIN semantic_metrics m ON m.model_id = d.model_id "
            "WHERE d.source = ? AND m.name = ? ORDER BY f.position",
            [table_name, normalized_metric_name],
        )

    def _load_field_rows_by_source(self, table_name: str) -> list[dict[str, Any]]:
        return self.metadata.query_rows(
            "SELECT f.name, f.expression, f.data_type, f.format, f.is_time, f.support_min_granularity, f.required_prefix "
            "FROM semantic_fields f "
            "JOIN semantic_datasets d ON f.dataset_id = d.dataset_id "
            "WHERE d.source = ? ORDER BY f.position",
            [table_name],
        )


def _optional_str(value: Any) -> str | None:
    return optional_str(value)


def _metric_name(metric_name: str | None) -> str | None:
    normalized = _optional_str(metric_name)
    if normalized is None:
        return None
    return normalized.removeprefix("metric.")


def _select_dialect_expression(expression_json: Any, *, engine_type: str) -> str | None:
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
    preferences = ("TRINO", "ANSI_SQL") if engine_type.strip().lower() == "trino" else ("ANSI_SQL",)
    for preferred in preferences:
        sql = _first_expression_for_dialect(dialects, preferred)
        if sql is not None:
            return sql
    return _first_valid_expression(dialects)


def _first_expression_for_dialect(dialects: list[Any], dialect_name: str) -> str | None:
    for dialect in dialects:
        if not isinstance(dialect, Mapping):
            continue
        if str(dialect.get("dialect") or "").strip().upper() != dialect_name:
            continue
        sql = _optional_str(dialect.get("expression"))
        if sql is not None:
            return sql
    return None


def _first_valid_expression(dialects: list[Any]) -> str | None:
    for dialect in dialects:
        if not isinstance(dialect, Mapping):
            continue
        sql = _optional_str(dialect.get("expression"))
        if sql is not None:
            return sql
    return None
