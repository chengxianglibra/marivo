"""Pattern: roll up a panel frame by dropping one dimension axis.

When to use: you have a (time x segment) panel and need the total time series.
Output shape: dropping the country dimension demotes the frame to time_series.
"""

from __future__ import annotations

import pandas as pd
from _fixtures.tiny_semantic import METRIC_ID, ensure_loaded

# Setup: load the tiny semantic domain and attach an examples session.
ensure_loaded()

import marivo.analysis as mv  # noqa: E402

session = mv.session.current()
panel_frame = session.promote_metric_frame(
    pd.DataFrame(
        {
            "bucket_start": pd.to_datetime(
                ["2026-07-01", "2026-07-01", "2026-07-02", "2026-07-02"]
            ),
            "country": ["US", "CA", "US", "CA"],
            "revenue": [10.0, 30.0, 20.0, 40.0],
        }
    ),
    metric=mv.MetricRef(METRIC_ID),
    axes={"country": mv.DimensionRef("country")},
    time_axis="bucket_start",
    measure_column="revenue",
    semantic_kind="panel",
    semantic_model="sales",
    window={"start": "2026-07-01", "end": "2026-07-03", "grain": "day"},
)
rolled = session.transform.rollup(panel_frame, drop_axes=[mv.DimensionRef("country")])
print(rolled.summary())

# Expected output:
# kind='metric_frame'
# semantic_kind='time_series'
# columns=['bucket_start', 'revenue']
