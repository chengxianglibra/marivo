from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from marivo.storage.metadata import MetadataStore
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
        _ = table_name
        return []

    def load_for_windowed_query(
        self,
        *,
        table_name: str,
        metric_name: str | None = None,
    ) -> TimeAxisMetadataContext:
        _ = (table_name, metric_name)
        return TimeAxisMetadataContext(available_columns=[])


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
