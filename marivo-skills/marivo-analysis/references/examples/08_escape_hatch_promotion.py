"""Pattern: use scratch exploration when an intent does not cover a step.

When to use: a user analysis needs custom joins, raw table exploration,
feature engineering, or a Python library step that Marivo intents do not model.
Output shape: an ExplorationResult with source_kind="pandas" or "ibis"; promote
only when the scratch result must feed typed intents.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import ibis
import pandas as pd

import marivo.analysis as mv

original_cwd = Path.cwd()
with tempfile.TemporaryDirectory(prefix="marivo-analysis-scratch-") as project_root:
    os.chdir(project_root)
    try:
        con = ibis.duckdb.connect(":memory:")
        con.raw_sql("CREATE TABLE orders (country TEXT, revenue DOUBLE)")
        con.raw_sql("INSERT INTO orders VALUES ('US', 10.0), ('CA', 5.0), ('US', 3.0)")
        session = mv.session.get_or_create(name="examples", backends={"warehouse": lambda: con})

        # Pandas scratch: import a small computed table into the session.
        scratch = session.from_pandas(
            pd.DataFrame({"country": ["US", "CA"], "value": [10.0, 5.0]}),
            description="manual cohort scan",
        )

        # Optional typed re-entry: promote only when downstream intents require it.
        metric = session.promote_metric_frame(
            scratch,
            metric=mv.MetricRef("sales.revenue"),
            semantic_kind="segmented",
            measure_column="value",
            axes={"country": mv.DimensionRef("country")},
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
            metric=mv.MetricRef("sales.revenue"),
            semantic_kind="segmented",
            measure_column="value",
            axes={"country": mv.DimensionRef("country")},
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
                .filter(lambda t: t.country == "US")
                .aggregate(value=lambda t: t.revenue.sum())
            ),
            datasource="warehouse",
            description="manual Ibis scan",
        )

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
        assert ibis_scratch.meta.source_datasource == "warehouse"
        assert ibis_scratch.meta.source_query is not None
        assert ibis_scratch.to_pandas().iloc[0]["value"] == 13.0
        print(scratch.summary())
        print(share_scratch.summary())
        print(metric.summary())
        print(delta.summary())
        print(attribution.summary())
        print(f"ibis_datasource={ibis_scratch.meta.source_datasource}")
        print(f"ibis_has_source_query={ibis_scratch.meta.source_query is not None}")
        print(ibis_scratch.summary())
    finally:
        os.chdir(original_cwd)

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
# ibis_datasource=warehouse
# ibis_has_source_query=True
# kind='exploration_result'
# row_count=1
# columns=['value']
