from __future__ import annotations

import logging
from typing import Any

from app.evidence_engine.synthesizers.stages import ScopeCluster

logger = logging.getLogger(__name__)


class ScopeClusterer:
    """Stage 1: Groups observations into scope clusters by (metric, slice) key.

    Each unique (metric, slice_dict) combination becomes one ScopeCluster.
    Scope is derived from observation subject metadata. Non-metric observations
    must already carry a stable `(metric, slice)` subject in order to
    participate in claim synthesis; the clusterer no longer invents a shared
    fallback cluster for unrelated observations.
    """

    @staticmethod
    def _make_scope_key(metric: str, slice_dict: dict[str, Any]) -> str:
        """Return a canonical string key for a (metric, slice) pair.

        Keys are deterministic regardless of insertion order of slice keys.
        """
        if slice_dict:
            slice_part = ",".join(f"{k}={v}" for k, v in sorted(slice_dict.items()))
        else:
            slice_part = ""
        return f"{metric}/{slice_part}"

    def cluster(self, observations: list[dict[str, Any]]) -> list[ScopeCluster]:
        """Group observations into ScopeClusters.

        Returns one cluster per unique (metric, slice) scope found across
        supported observation types. Observations without a stable subject scope
        are ignored instead of being collapsed into a synthetic fallback
        cluster.

        Audit contract: every returned ScopeCluster has
        - total_observation_count set
        - cluster_reason set
        """
        if not observations:
            return []

        metric_change_obs = [o for o in observations if o["type"] == "metric_change"]
        funnel_drop_obs = [o for o in observations if o["type"] == "funnel_drop"]
        contribution_shift_obs = [o for o in observations if o["type"] == "contribution_shift"]
        anomaly_detection_obs = [o for o in observations if o["type"] == "anomaly_detection"]
        other_obs = [o for o in observations
                     if o["type"] not in {"metric_change", "funnel_drop",
                                          "contribution_shift", "anomaly_detection"}]

        cluster_map: dict[str, ScopeCluster] = {}

        def _get_or_create_cluster(obs: dict[str, Any]) -> ScopeCluster | None:
            subject = obs.get("subject", {})
            metric = subject.get("metric")
            slice_dict = subject.get("slice")
            if not metric or not isinstance(slice_dict, dict):
                return None
            key = self._make_scope_key(metric, slice_dict)
            if key not in cluster_map:
                cluster_map[key] = ScopeCluster(
                    scope_key=key,
                    metric=metric,
                    slice_dict=slice_dict,
                    metric_change_obs=[],
                    funnel_drop_obs=[],
                    contribution_shift_obs=[],
                    anomaly_detection_obs=[],
                    other_obs=[],
                    cluster_reason="exact_scope_match",
                )
            return cluster_map[key]

        for obs in metric_change_obs:
            cluster = _get_or_create_cluster(obs)
            if cluster is not None:
                cluster.metric_change_obs.append(obs)

        for obs in funnel_drop_obs:
            cluster = _get_or_create_cluster(obs)
            if cluster is not None:
                cluster.funnel_drop_obs.append(obs)

        for obs in contribution_shift_obs:
            cluster = _get_or_create_cluster(obs)
            if cluster is not None:
                cluster.contribution_shift_obs.append(obs)

        for obs in anomaly_detection_obs:
            cluster = _get_or_create_cluster(obs)
            if cluster is not None:
                cluster.anomaly_detection_obs.append(obs)

        for obs in other_obs:
            cluster = _get_or_create_cluster(obs)
            if cluster is not None:
                cluster.other_obs.append(obs)

        # Set total counts
        for cluster in cluster_map.values():
            cluster.total_observation_count = (
                len(cluster.metric_change_obs)
                + len(cluster.funnel_drop_obs)
                + len(cluster.contribution_shift_obs)
                + len(cluster.anomaly_detection_obs)
                + len(cluster.other_obs)
            )

        clusters = list(cluster_map.values())
        dropped = len(observations) - sum(cluster.total_observation_count for cluster in clusters)
        if dropped:
            logger.debug(
                "Dropped %d observations without stable subject scope during scope clustering",
                dropped,
            )
        return clusters
