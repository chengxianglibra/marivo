"""Pattern: slice a segmented frame to one dimension value.

When to use: you already have a by-dimension MetricFrame and need one segment.
Output shape: slicing one dimension value demotes the frame to scalar.

transform.slice where values use shorthand forms:
  - scalar  → equality (==)
  - list    → membership (in)
  - tuple   → range (between, inclusive both ends)
For structured predicates (op + value), use observe(where=...) instead.
"""

from __future__ import annotations

import pandas as pd
from _fixtures.tiny_semantic import METRIC_ID, ensure_loaded

# Setup: load the tiny semantic model and attach an examples session.
ensure_loaded()

import marivo.analysis as mv  # noqa: E402

session = mv.session.current()
region = session.catalog.get("sales.orders.region")
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

# Scalar shorthand: filter to one exact value (equality).
sliced = session.transform.slice(
    revenue_by_country,
    where={region: "US"},
)
print(sliced.summary())

# List shorthand: keep multiple values (membership).
sliced_multi = session.transform.slice(
    revenue_by_country,
    where={region: ["US", "CA"]},
)
print(sliced_multi.summary())

# Expected output:
# kind='metric_frame'
# semantic_kind='scalar'
# columns=['revenue']
