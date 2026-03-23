from __future__ import annotations

from typing import Any

from app.evidence_engine.synthesizers.stages import ScopeCluster


class ScopeClusterer:
    """Stage 1: Groups observations into scope clusters by (metric, slice) key.

    Each unique (metric, slice_dict) combination becomes one ScopeCluster.
    Scope is derived from metric_change observations; other observation types
    are attached to the matching cluster's typed bucket.  When no metric_change
    observations are present, all observations collapse into a single fallback
    cluster keyed on the most significant observation's scope.
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

        Returns one cluster per unique (metric, slice) scope found among
        metric_change observations.  Non-metric_change observations whose
        scope exactly matches a cluster's scope are added to the appropriate
        typed bucket.  Unmatched non-metric_change observations are added to
        the closest cluster (first one found) when a match exists, or into a
        single fallback cluster when no metric_change observations exist.

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

        if not metric_change_obs:
            # Fallback: no metric_change observations — one cluster for everything
            all_non_metric = funnel_drop_obs + contribution_shift_obs + anomaly_detection_obs + other_obs
            if not all_non_metric:
                return []
            primary = max(all_non_metric, key=lambda o: o["significance"]["sample_size"])
            metric = primary["subject"].get("metric", "unknown")
            slice_dict = primary["subject"].get("slice", {})
            cluster = ScopeCluster(
                scope_key=self._make_scope_key(metric, slice_dict),
                metric=metric,
                slice_dict=slice_dict,
                metric_change_obs=[],
                funnel_drop_obs=funnel_drop_obs,
                contribution_shift_obs=contribution_shift_obs,
                anomaly_detection_obs=anomaly_detection_obs,
                other_obs=other_obs,
                total_observation_count=len(all_non_metric),
                cluster_reason="non_metric_fallback",
            )
            return [cluster]

        # Build one cluster per unique (metric, slice) from metric_change observations
        cluster_map: dict[str, ScopeCluster] = {}
        for obs in metric_change_obs:
            metric = obs["subject"].get("metric", "unknown")
            slice_dict = obs["subject"].get("slice", {})
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
            cluster_map[key].metric_change_obs.append(obs)

        # Attach non-metric observations: exact scope match first, else first cluster
        first_cluster = next(iter(cluster_map.values()))

        for obs in funnel_drop_obs:
            metric = obs["subject"].get("metric", "")
            slice_dict = obs["subject"].get("slice", {})
            key = self._make_scope_key(metric, slice_dict)
            target = cluster_map.get(key, first_cluster)
            target.funnel_drop_obs.append(obs)

        for obs in contribution_shift_obs:
            metric = obs["subject"].get("metric", "")
            slice_dict = obs["subject"].get("slice", {})
            key = self._make_scope_key(metric, slice_dict)
            target = cluster_map.get(key, first_cluster)
            target.contribution_shift_obs.append(obs)

        for obs in anomaly_detection_obs:
            metric = obs["subject"].get("metric", "")
            slice_dict = obs["subject"].get("slice", {})
            key = self._make_scope_key(metric, slice_dict)
            target = cluster_map.get(key, first_cluster)
            target.anomaly_detection_obs.append(obs)

        for obs in other_obs:
            first_cluster.other_obs.append(obs)

        # Set total counts
        for cluster in cluster_map.values():
            cluster.total_observation_count = (
                len(cluster.metric_change_obs)
                + len(cluster.funnel_drop_obs)
                + len(cluster.contribution_shift_obs)
                + len(cluster.anomaly_detection_obs)
                + len(cluster.other_obs)
            )

        return list(cluster_map.values())
