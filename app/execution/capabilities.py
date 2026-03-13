from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class EngineCapabilityProfile:
    engine_type: str
    supported_sql_features: tuple[str, ...] = ()
    supported_step_types: tuple[str, ...] = ()
    materialization_support: str = "unknown"
    policy_support: tuple[str, ...] = ()
    performance_class: str = "general_purpose"
    min_staleness_minutes: int | None = None
    federation_support: str = "none"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_DEFAULT_STEP_TYPES = (
    "sample_rows",
    "profile_table",
    "compare_metric",
    "compare_watch_time",
    "compare_watch_time_top_slices",
    "compare_watch_time_overall",
    "analyze_qoe",
    "analyze_ads",
    "analyze_recommendation",
    "synthesize_findings",
)

_DEFAULT_PROFILES: dict[str, EngineCapabilityProfile] = {
    "duckdb": EngineCapabilityProfile(
        engine_type="duckdb",
        supported_sql_features=("window_functions", "temporary_tables", "local_file_scan"),
        supported_step_types=_DEFAULT_STEP_TYPES,
        materialization_support="temporary_table",
        policy_support=("aggregate_only",),
        performance_class="embedded",
        min_staleness_minutes=0,
        federation_support="none",
        metadata={"locality": "embedded"},
    ),
    "trino": EngineCapabilityProfile(
        engine_type="trino",
        supported_sql_features=("window_functions", "connector_pushdown", "federated_reads"),
        supported_step_types=_DEFAULT_STEP_TYPES,
        materialization_support="catalog_table",
        policy_support=("aggregate_only", "catalog_governed"),
        performance_class="distributed",
        min_staleness_minutes=5,
        federation_support="connector",
        metadata={"locality": "remote_cluster"},
    ),
    "spark_connect": EngineCapabilityProfile(
        engine_type="spark_connect",
        supported_sql_features=("window_functions", "distributed_joins", "temporary_views"),
        supported_step_types=_DEFAULT_STEP_TYPES,
        materialization_support="temporary_view",
        policy_support=("aggregate_only",),
        performance_class="distributed",
        min_staleness_minutes=5,
        federation_support="staged",
        metadata={"locality": "remote_cluster"},
    ),
    "spark_thrift": EngineCapabilityProfile(
        engine_type="spark_thrift",
        supported_sql_features=("window_functions", "distributed_joins", "temporary_views"),
        supported_step_types=_DEFAULT_STEP_TYPES,
        materialization_support="temporary_view",
        policy_support=("aggregate_only",),
        performance_class="distributed",
        min_staleness_minutes=5,
        federation_support="staged",
        metadata={"locality": "remote_cluster"},
    ),
}


def build_engine_capability_profile(
    engine_type: str,
    overrides: dict[str, Any] | None = None,
) -> EngineCapabilityProfile:
    base = _DEFAULT_PROFILES.get(engine_type, EngineCapabilityProfile(engine_type=engine_type))
    payload = base.to_dict()
    if overrides:
        payload.update(overrides)
    for key in ("supported_sql_features", "supported_step_types", "policy_support"):
        payload[key] = tuple(payload.get(key, ()))
    payload["metadata"] = dict(payload.get("metadata", {}))
    return EngineCapabilityProfile(**payload)


def score_capability_profile(
    profile: EngineCapabilityProfile,
    *,
    table_count: int,
) -> int:
    score = 0
    if table_count <= 1 and profile.performance_class == "embedded":
        score += 5
    if table_count > 1 and profile.performance_class == "distributed":
        score += 10
    if profile.federation_support != "none":
        score += 3
    if "aggregate_only" in profile.policy_support:
        score += 1
    if "temporary_tables" in profile.supported_sql_features:
        score += 1
    return score
