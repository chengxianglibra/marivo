"""Pattern: slice a segmented frame to one dimension value.

When to use: you already have a by-dimension MetricFrame and need one segment.
Output shape: slicing one dimension value demotes the frame to scalar.
"""

from __future__ import annotations

import pandas as pd
from _fixtures.tiny_semantic import METRIC_ID, ensure_loaded

# Setup: load the tiny semantic model and attach an examples session.
ensure_loaded()

import marivo.analysis as mv  # noqa: E402

session = mv.session.current()
region = session.catalog.get("sales.orders.region").ref
revenue_by_country = session.promote_metric_frame(
    pd.DataFrame(
        {
            "country": ["US", "CA", "MX"],
            "revenue": [120.0, 80.0, 40.0],
        }
    ),
    metric=session.catalog.get(METRIC_ID),
    axes={"country": region},
    measure_column="revenue",
    semantic_kind="segmented",
    semantic_model="sales",
)
sliced = session.transform.slice(
    revenue_by_country,
    where={region: "US"},
)
print(sliced.summary())

# Expected output:
# kind='metric_frame'
# semantic_kind='scalar'
# columns=['revenue']
