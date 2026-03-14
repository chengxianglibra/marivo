from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from typing import Sequence


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


def describe_routing_fit(
    profile: EngineCapabilityProfile,
    *,
    table_count: int,
    step_type: str | None = None,
    metric_names: Sequence[str] = (),
    requested_dimensions: Sequence[str] = (),
    compatible_dimensions: Sequence[str] = (),
    policy_hints: Sequence[str] = (),
) -> dict[str, Any]:
    metric_count = len(tuple(metric_names))
    requested_dimension_count = len(tuple(requested_dimensions))
    compatible_dimension_count = len(tuple(compatible_dimensions))
    normalized_policy_hints = tuple(
        policy_hint
        for policy_hint in dict.fromkeys(str(policy_hint).strip() for policy_hint in policy_hints)
        if policy_hint
    )

    step_type_supported = (
        step_type is None
        or not profile.supported_step_types
        or step_type in profile.supported_step_types
    )
    step_score = 6 if step_type_supported else -25

    missing_policy_support = [
        policy_hint
        for policy_hint in normalized_policy_hints
        if policy_hint not in profile.policy_support
    ]
    satisfied_policy_support = [
        policy_hint
        for policy_hint in normalized_policy_hints
        if policy_hint in profile.policy_support
    ]
    policy_score = (len(satisfied_policy_support) * 4) - (len(missing_policy_support) * 6)

    semantic_score = 0
    if (
        table_count > 1
        or compatible_dimension_count >= 3
        or metric_count >= 2
    ):
        if profile.performance_class == "distributed":
            semantic_score += 6
        elif compatible_dimension_count >= 3 or metric_count >= 2:
            semantic_score -= 2
    elif (
        table_count <= 1
        and compatible_dimension_count <= 1
        and metric_count <= 1
        and profile.performance_class == "embedded"
    ):
        semantic_score += 3

    unresolved_dimension_count = max(
        requested_dimension_count - compatible_dimension_count,
        0,
    )
    if unresolved_dimension_count > 0 and profile.performance_class == "distributed":
        semantic_score += 1

    cost_score = 0
    if table_count <= 1:
        if profile.performance_class == "embedded":
            cost_score += 2
        if profile.min_staleness_minutes in (None, 0):
            cost_score += 1
    else:
        if profile.performance_class == "distributed":
            cost_score += 3
        if profile.federation_support != "none":
            cost_score += 2

    reasons: list[str] = []
    if step_type is not None:
        if step_type_supported:
            reasons.append(f"supports step type '{step_type}'")
        else:
            reasons.append(f"does not advertise step type '{step_type}'")
    if satisfied_policy_support:
        reasons.append(
            "supports policies: " + ", ".join(sorted(satisfied_policy_support))
        )
    if missing_policy_support:
        reasons.append(
            "missing policies: " + ", ".join(sorted(missing_policy_support))
        )
    if (
        table_count > 1
        or compatible_dimension_count >= 3
        or metric_count >= 2
    ) and profile.performance_class == "distributed":
        reasons.append("semantic complexity prefers distributed execution")
    elif (
        table_count <= 1
        and compatible_dimension_count <= 1
        and metric_count <= 1
        and profile.performance_class == "embedded"
    ):
        reasons.append("single-table low-latency path prefers embedded execution")
    if table_count > 1 and profile.federation_support != "none":
        reasons.append("multi-table route benefits from federation support")

    return {
        "step_type_supported": step_type_supported,
        "satisfied_policy_support": list(satisfied_policy_support),
        "missing_policy_support": list(missing_policy_support),
        "requested_dimension_count": requested_dimension_count,
        "compatible_dimension_count": compatible_dimension_count,
        "unresolved_dimension_count": unresolved_dimension_count,
        "metric_count": metric_count,
        "step_score": step_score,
        "policy_score": policy_score,
        "semantic_score": semantic_score,
        "cost_score": cost_score,
        "reasons": reasons,
    }
