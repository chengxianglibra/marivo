"""Pattern: use scratch exploration when an intent does not cover a step.

When to use: a user analysis needs custom joins, raw table exploration,
feature engineering, or a Python library step that Marivo intents do not model.
Output shape: an ExplorationResult with source_kind="pandas" or "ibis"; promote
only when the scratch result must feed typed intents.
"""

from __future__ import annotations

import pandas as pd
from _fixtures.tiny_semantic import ensure_loaded

import marivo.analysis as mv

session = ensure_loaded()

# Pandas scratch: import a small computed table into the session.
scratch = session.from_pandas(
    pd.DataFrame({"country": ["US", "CA"], "value": [10.0, 5.0]}),
    description="manual cohort scan",
)

# Optional typed re-entry: promote only when downstream intents require it.
metric = session.promote_metric_frame(
    scratch,
    metric=session.catalog.get("sales.revenue"),
    semantic_kind="segmented",
    measure_column="value",
    axes={"country": session.catalog.get("sales.orders.region").ref},
    semantic_model="sales",
)

# Export a frame to pandas for mutable local analysis, then persist the result.
metric_df = metric.to_pandas()
metric_df["share"] = metric_df["value"] / metric_df["value"].sum()
share_scratch = session.from_pandas(
    metric_df[["country", "share"]],
    description="local pandas share calculation",
    sources=[mv.ArtifactRef(id=metric.ref)],
)

# More optional typed re-entry examples: manual delta and attribution frames.
baseline_metric = session.promote_metric_frame(
    pd.DataFrame({"country": ["US", "CA"], "value": [7.0, 4.0]}),
    metric=session.catalog.get("sales.revenue"),
    semantic_kind="segmented",
    measure_column="value",
    axes={"country": session.catalog.get("sales.orders.region").ref},
    semantic_model="sales",
)
delta = session.promote_delta_frame(
    pd.DataFrame(
        {
            "country": ["US", "CA"],
            "current": [10.0, 5.0],
            "baseline": [7.0, 4.0],
            "delta": [3.0, 1.0],
        }
    ),
    current=mv.ArtifactRef(id=metric.ref),
    baseline=mv.ArtifactRef(id=baseline_metric.ref),
    delta_column="delta",
    current_column="current",
    baseline_column="baseline",
)
attribution = session.promote_attribution_frame(
    pd.DataFrame(
        {
            "country": ["US", "CA"],
            "value": [8.0, 2.0],
            "contribution": [8.0, 2.0],
        }
    ),
    source_delta=mv.ArtifactRef(id=delta.ref),
    driver_field="country",
    value_column="value",
    contribution_column="contribution",
    method="manual",
    method_params={"note": "example attribution"},
)

# Ibis scratch: run a clean raw query through the session backend.
ibis_scratch = session.explore_ibis(
    lambda backend: (
        backend.table("orders")
        .filter(lambda t: t.region == "north")
        .aggregate(value=lambda t: t.amount.sum())
    ),
    datasource="tiny_orders",
    description="manual Ibis scan",
)

# Typed time-series re-entry: promote two hand-built series and correlate them.
# time_axis takes a {column: ref} mapping (symmetric with axes), so the dataframe
# column name ("bucket_start") can differ from the catalog time-dimension ref.
created_at_ref = session.catalog.get("sales.orders.created_at").ref
revenue_ts = session.promote_metric_frame(
    pd.DataFrame(
        {
            "bucket_start": ["2026-07-01", "2026-08-01", "2026-09-01"],
            "value": [12.0, 24.0, 60.0],
        }
    ),
    metric=session.catalog.get("sales.revenue"),
    semantic_kind="time_series",
    measure_column="value",
    time_axis={"bucket_start": created_at_ref},
    semantic_model="sales",
)
failures_ts = session.promote_metric_frame(
    pd.DataFrame(
        {
            "bucket_start": ["2026-07-01", "2026-08-01", "2026-09-01"],
            "value": [1.0, 1.0, 0.0],
        }
    ),
    metric=session.catalog.get("sales.failed_count"),
    semantic_kind="time_series",
    measure_column="value",
    time_axis={"bucket_start": created_at_ref},
    semantic_model="sales",
)
association = session.correlate(revenue_ts, failures_ts)

assert revenue_ts.meta.axes["time"] == {
    "role": "time",
    "column": "bucket_start",
    "ref": "sales.orders.created_at",
}
assert isinstance(association, mv.AssociationResult)
assert association.meta.metric_ids == ["sales.revenue", "sales.failed_count"]
assert association.meta.aligned_row_count == 3

assert isinstance(scratch, mv.ExplorationResult)
assert scratch.meta.source_kind == "pandas"
assert scratch.meta.description == "manual cohort scan"
assert isinstance(share_scratch, mv.ExplorationResult)
assert share_scratch.meta.source_kind == "pandas"
assert share_scratch.meta.source_artifact_refs == [metric.ref]
assert share_scratch.to_pandas()["share"].round(6).tolist() == [0.666667, 0.333333]
assert isinstance(metric, mv.MetricFrame)
assert metric.meta.metric_id == "sales.revenue"
assert isinstance(delta, mv.DeltaFrame)
assert delta.meta.metric_id == "sales.revenue"
assert isinstance(attribution, mv.AttributionFrame)
assert attribution.meta.source_refs == [delta.ref]
assert isinstance(ibis_scratch, mv.ExplorationResult)
assert ibis_scratch.meta.source_kind == "ibis"
assert ibis_scratch.meta.source_datasource == "tiny_orders"
assert ibis_scratch.meta.source_query is not None
assert ibis_scratch.to_pandas().iloc[0]["value"] == 112.0
print(scratch.summary())
print(share_scratch.summary())
print(metric.summary())
print(delta.summary())
print(attribution.summary())
print(f"ibis_datasource={ibis_scratch.meta.source_datasource}")
print(f"ibis_has_source_query={ibis_scratch.meta.source_query is not None}")
print(ibis_scratch.summary())
print(f"revenue_ts_kind={revenue_ts.meta.kind}")
print(f"revenue_ts_time_column={revenue_ts.meta.axes['time']['column']}")
print(f"association_kind={association.meta.kind}")
print(f"association_aligned_rows={association.meta.aligned_row_count}")
print(f"association_metrics={association.meta.metric_ids}")

# Expected output:
# kind='exploration_result'
# row_count=2
# columns=['country', 'value']
# kind='exploration_result'
# row_count=2
# columns=['country', 'share']
# kind='metric_frame'
# row_count=2
# columns=['country', 'value']
# kind='delta_frame'
# row_count=2
# columns=['country', 'current', 'baseline', 'delta']
# kind='attribution_frame'
# row_count=2
# columns=['country', 'value', 'contribution']
# ibis_datasource=tiny_orders
# ibis_has_source_query=True
# kind='exploration_result'
# row_count=1
# columns=['value']
# revenue_ts_kind=metric_frame
# revenue_ts_time_column=bucket_start
# association_kind=association_result
# association_aligned_rows=3
# association_metrics=['sales.revenue', 'sales.failed_count']
