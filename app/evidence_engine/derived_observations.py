from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from app.evidence_engine.schemas import Claim, ClaimRelation, Observation


class DefaultDerivedObservationBuilder:
    """Build session-level derived observations after claim/relation synthesis.

    These observations are deterministic, persisted, and intended for evidence
    graph explanation/debugging. They do not participate in claim synthesis.
    """

    MIN_CROSS_METRIC_COMPONENT_SIZE = 3
    MIN_TEMPORAL_PATTERN_BUCKETS = 3

    def build(
        self,
        observations: list[Observation],
        claims: list[Claim],
        relations: list[ClaimRelation],
    ) -> list[Observation]:
        observation_by_id = {
            str(obs.get("observation_id")): obs for obs in observations if obs.get("observation_id")
        }
        derived: list[Observation] = []
        derived.extend(
            self._build_cross_metric_correlations(
                claims,
                relations,
                observation_by_id,
            )
        )
        derived.extend(self._build_temporal_patterns(claims, observation_by_id))
        return derived

    def _build_cross_metric_correlations(
        self,
        claims: list[Claim],
        relations: list[ClaimRelation],
        observation_by_id: dict[str, Observation],
    ) -> list[Observation]:
        confirmed_claims = {
            str(claim.get("claim_id")): claim
            for claim in claims
            if claim.get("status") == "confirmed" and claim.get("claim_id")
        }
        adjacency: dict[str, set[str]] = defaultdict(set)
        for relation in relations:
            if relation.get("relation_type") != "correlates_with":
                continue
            match_basis = relation.get("match_basis", {})
            if str(match_basis.get("category", "")) not in {"exact_match", "subset_or_overlap"}:
                continue
            from_claim_id = str(relation.get("from_claim_id", ""))
            to_claim_id = str(relation.get("to_claim_id", ""))
            if from_claim_id not in confirmed_claims or to_claim_id not in confirmed_claims:
                continue
            direction = str(match_basis.get("direction", ""))
            if direction not in {"up", "down"}:
                continue
            adjacency[from_claim_id].add(to_claim_id)
            adjacency[to_claim_id].add(from_claim_id)

        derived: list[Observation] = []
        for component in _connected_components(adjacency):
            component_claims = [
                confirmed_claims[claim_id] for claim_id in component if claim_id in confirmed_claims
            ]
            metrics = sorted(
                {
                    str((claim.get("scope", {}) or {}).get("metric", ""))
                    for claim in component_claims
                    if (claim.get("scope", {}) or {}).get("metric")
                }
            )
            if len(metrics) < self.MIN_CROSS_METRIC_COMPONENT_SIZE:
                continue

            directions = {
                claim["claim_id"]: _claim_direction(claim, observation_by_id)
                for claim in component_claims
            }
            if any(direction is None for direction in directions.values()):
                continue
            unique_directions = set(directions.values())
            if len(unique_directions) != 1:
                continue
            group_direction = "group_up" if "up" in unique_directions else "group_down"

            shared_slice = _shared_slice(
                [
                    (claim.get("scope", {}) or {}).get("slice", {}) or {}
                    for claim in component_claims
                ]
            )
            component_relations = [
                relation
                for relation in relations
                if str(relation.get("from_claim_id", "")) in component
                and str(relation.get("to_claim_id", "")) in component
                and relation.get("relation_type") == "correlates_with"
            ]
            scope_categories = sorted(
                {
                    str((relation.get("match_basis", {}) or {}).get("category", ""))
                    for relation in component_relations
                    if (relation.get("match_basis", {}) or {}).get("category")
                }
            )
            supporting_observation_ids = sorted(
                {
                    str(observation_id)
                    for claim in component_claims
                    for observation_id in claim.get("supporting_observations", [])
                }
            )
            sample_sizes = {
                claim["claim_id"]: sum(
                    int(observation_by_id[key]["significance"].get("sample_size", 0))
                    for obs_id in claim.get("supporting_observations", [])
                    for key in [str(obs_id)]
                    if key in observation_by_id
                )
                for claim in component_claims
            }
            component_claim_ids = sorted(claim["claim_id"] for claim in component_claims)
            observation_id = _stable_observation_id(
                "cross_metric_correlation",
                "|".join(component_claim_ids),
            )
            derived.append(
                {
                    "observation_id": observation_id,
                    "type": "cross_metric_correlation",
                    "subject": {
                        "metric": "__multi_metric__",
                        "slice": shared_slice,
                    },
                    "payload": {
                        "metrics": metrics,
                        "group_direction": group_direction,
                        "shared_slice": shared_slice,
                        "scope_overlap_category": (
                            scope_categories[0] if len(scope_categories) == 1 else "mixed"
                        ),
                        "component_size": len(component_claim_ids),
                        "supporting_claim_ids": component_claim_ids,
                        "supporting_observation_ids": supporting_observation_ids,
                        "sample_sizes": sample_sizes,
                    },
                    "significance": {
                        "sample_size": sum(sample_sizes.values()),
                        "practical_significance": True,
                    },
                    "quality": {
                        "freshness_ok": True,
                        "sample_size_ok": True,
                    },
                }
            )
        return derived

    def _build_temporal_patterns(
        self,
        claims: list[Claim],
        observation_by_id: dict[str, Observation],
    ) -> list[Observation]:
        derived: list[Observation] = []
        for claim in claims:
            if claim.get("status") != "confirmed":
                continue
            supporting = [
                observation_by_id[str(observation_id)]
                for observation_id in claim.get("supporting_observations", [])
                if str(observation_id) in observation_by_id
            ]
            buckets = []
            for obs in supporting:
                temporal_group_by_columns = [
                    str(column) for column in obs["subject"].get("temporal_group_by_columns") or []
                ]
                if not temporal_group_by_columns:
                    continue
                window = obs.get("observed_window")
                if not window or window.get("granularity") != "hour" or not window.get("start"):
                    continue
                value = _temporal_signal_value(obs)
                if value is None:
                    continue
                start = _parse_hour_start(str(window["start"]))
                if start is None:
                    continue
                buckets.append((start, value, str(obs.get("observation_id"))))
            buckets.sort(key=lambda item: item[0])
            if len(buckets) < self.MIN_TEMPORAL_PATTERN_BUCKETS:
                continue

            pattern = self._detect_spike_and_decay(buckets)
            if pattern is None:
                pattern = self._detect_sustained_pattern(buckets)
            if pattern is None:
                continue

            claim_id = str(claim.get("claim_id", ""))
            observation_id = _stable_observation_id(
                "temporal_pattern",
                claim_id,
                pattern["pattern_type"],
                pattern["peak_window"]["start"],
            )
            derived.append(
                {
                    "observation_id": observation_id,
                    "type": "temporal_pattern",
                    "subject": {
                        "metric": str((claim.get("scope", {}) or {}).get("metric", "")),
                        "slice": (claim.get("scope", {}) or {}).get("slice", {}) or {},
                    },
                    "payload": {
                        "pattern_type": pattern["pattern_type"],
                        "granularity": "hour",
                        "pattern_windows": pattern["pattern_windows"],
                        "peak_window": pattern["peak_window"],
                        "baseline_value": pattern["baseline_value"],
                        "peak_value": pattern["peak_value"],
                        "magnitude": pattern["magnitude"],
                        "supporting_observation_ids": pattern["supporting_observation_ids"],
                        "supporting_claim_id": claim_id,
                        **(
                            {"decay_value": pattern["decay_value"]}
                            if pattern.get("decay_value") is not None
                            else {}
                        ),
                    },
                    "significance": {
                        "sample_size": len(pattern["supporting_observation_ids"]),
                        "practical_significance": True,
                    },
                    "quality": {
                        "freshness_ok": True,
                        "sample_size_ok": True,
                    },
                    "observed_window": {
                        "start": pattern["pattern_windows"][0]["start"],
                        "end": pattern["pattern_windows"][-1]["end"],
                        "granularity": "hour",
                    },
                }
            )
        return derived

    def _detect_spike_and_decay(
        self,
        buckets: list[tuple[datetime, float, str]],
    ) -> dict[str, Any] | None:
        for index in range(1, len(buckets) - 1):
            prev_start, prev_value, prev_obs_id = buckets[index - 1]
            peak_start, peak_value, peak_obs_id = buckets[index]
            next_start, next_value, next_obs_id = buckets[index + 1]
            if int((peak_start - prev_start).total_seconds() / 3600) != 1:
                continue
            if int((next_start - peak_start).total_seconds() / 3600) != 1:
                continue
            if not (prev_value < peak_value and next_value < peak_value):
                continue
            return {
                "pattern_type": "spike_and_decay",
                "pattern_windows": [
                    _hour_window(prev_start),
                    _hour_window(peak_start),
                    _hour_window(next_start),
                ],
                "peak_window": _hour_window(peak_start),
                "baseline_value": prev_value,
                "peak_value": peak_value,
                "decay_value": next_value,
                "magnitude": round(peak_value - prev_value, 4),
                "supporting_observation_ids": [prev_obs_id, peak_obs_id, next_obs_id],
            }
        return None

    def _detect_sustained_pattern(
        self,
        buckets: list[tuple[datetime, float, str]],
    ) -> dict[str, Any] | None:
        consecutive = True
        for index in range(1, len(buckets)):
            if int((buckets[index][0] - buckets[index - 1][0]).total_seconds() / 3600) != 1:
                consecutive = False
                break
        if not consecutive:
            return None

        values = [value for _, value, _ in buckets]
        increasing = all(values[index] < values[index + 1] for index in range(len(values) - 1))
        decreasing = all(values[index] > values[index + 1] for index in range(len(values) - 1))
        if not increasing and not decreasing:
            return None
        magnitude = abs(values[-1] - values[0])
        if magnitude <= 0:
            return None
        pattern_type = "sustained_increase" if increasing else "sustained_decrease"
        return {
            "pattern_type": pattern_type,
            "pattern_windows": [_hour_window(start) for start, _, _ in buckets],
            "peak_window": _hour_window(buckets[-1][0] if increasing else buckets[0][0]),
            "baseline_value": values[0],
            "peak_value": max(values) if increasing else values[0],
            "magnitude": round(magnitude, 4),
            "supporting_observation_ids": [obs_id for _, _, obs_id in buckets],
        }


def _connected_components(adjacency: dict[str, set[str]]) -> list[set[str]]:
    components: list[set[str]] = []
    seen: set[str] = set()
    for node in adjacency:
        if node in seen:
            continue
        stack = [node]
        component: set[str] = set()
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            component.add(current)
            stack.extend(
                neighbor for neighbor in adjacency.get(current, set()) if neighbor not in seen
            )
        if component:
            components.append(component)
    return components


def _claim_direction(
    claim: Claim,
    observation_by_id: dict[str, Observation],
) -> str | None:
    deltas = []
    for observation_id in claim.get("supporting_observations", []):
        observation = observation_by_id.get(str(observation_id))
        if observation is None:
            continue
        delta = observation.get("payload", {}).get("delta_pct")
        if delta is None:
            continue
        deltas.append(float(delta))
    if not deltas:
        return None
    positive = sum(1 for delta in deltas if delta > 0)
    negative = sum(1 for delta in deltas if delta < 0)
    if positive == negative:
        return None
    return "up" if positive > negative else "down"


def _shared_slice(slices: list[dict[str, Any]]) -> dict[str, Any]:
    if not slices:
        return {}
    keys = set(slices[0].keys())
    for slice_dict in slices[1:]:
        keys.intersection_update(slice_dict.keys())
    return {
        key: slices[0][key]
        for key in sorted(keys)
        if all(slice_dict.get(key) == slices[0].get(key) for slice_dict in slices)
    }


def _stable_observation_id(*parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"obs_{digest}"


def _parse_hour_start(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M")
    except ValueError:
        return None


def _hour_window(start: datetime) -> dict[str, str]:
    return {
        "start": start.strftime("%Y-%m-%dT%H:%M"),
        "end": (start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
    }


def _temporal_signal_value(observation: Observation) -> float | None:
    payload = observation.get("payload", {})
    current_value = payload.get("current_value")
    if isinstance(current_value, (int, float)):
        return float(current_value)
    delta_pct = payload.get("delta_pct")
    if isinstance(delta_pct, (int, float)):
        return float(delta_pct)
    return None
