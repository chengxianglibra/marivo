"""Pattern: create a scratch exploration result from pandas or Ibis.

When to use: you have a small pandas result or an ad hoc Ibis expression from
manual exploration and want to persist it as a session-local analysis frame.
Output shape: an ExplorationResult with source_kind="pandas" or "ibis".
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import ibis
import pandas as pd

import marivo.analysis_py as mv

original_cwd = Path.cwd()
with tempfile.TemporaryDirectory(prefix="marivo-py-analysis-scratch-") as project_root:
    os.chdir(project_root)
    try:
        con = ibis.duckdb.connect(":memory:")
        con.raw_sql("CREATE TABLE orders (country TEXT, revenue DOUBLE)")
        con.raw_sql("INSERT INTO orders VALUES ('US', 10.0), ('CA', 5.0), ('US', 3.0)")
        session = mv.session.get_or_create(name="examples", backends={"warehouse": lambda: con})
        scratch = session.from_pandas(
            pd.DataFrame({"country": ["US", "CA"], "value": [10.0, 5.0]}),
            description="manual cohort scan",
        )
        metric = session.promote_metric_frame(
            scratch,
            metric=mv.MetricRef(id="sales.revenue"),
            semantic_kind="segmented",
            measure_column="value",
            axes={"country": mv.DimensionRef(id="country")},
            semantic_model="sales",
        )
        baseline_metric = session.promote_metric_frame(
            pd.DataFrame({"country": ["US", "CA"], "value": [7.0, 4.0]}),
            metric=mv.MetricRef(id="sales.revenue"),
            semantic_kind="segmented",
            measure_column="value",
            axes={"country": mv.DimensionRef(id="country")},
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
        assert isinstance(metric, mv.MetricFrame)
        assert metric.meta.metric_id == "sales.revenue"
        assert isinstance(delta, mv.DeltaFrame)
        assert delta.meta.metric_id == "sales.revenue"
        assert isinstance(attribution, mv.AttributionFrame)
        assert attribution.meta.source_refs == [delta.ref]
        assert isinstance(ibis_scratch, mv.ExplorationResult)
        assert ibis_scratch.meta.source_kind == "ibis"
        assert ibis_scratch.to_pandas().iloc[0]["value"] == 13.0
        print(scratch.summary())
        print(metric.summary())
        print(delta.summary())
        print(attribution.summary())
        print(ibis_scratch.summary())
    finally:
        os.chdir(original_cwd)

# Expected output:
# kind='exploration_result'
# row_count=2
# columns=['country', 'value']
# kind='metric_frame'
# row_count=2
# columns=['country', 'value']
# kind='delta_frame'
# row_count=2
# columns=['country', 'current', 'baseline', 'delta']
# kind='attribution_frame'
# row_count=2
# columns=['country', 'value', 'contribution']
# kind='exploration_result'
# row_count=1
# columns=['value']
