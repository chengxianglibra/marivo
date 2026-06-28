"""Pattern: slice a segmented frame to one dimension value.

When to use: you already have a by-dimension MetricFrame and need one segment.
Output shape: slicing one dimension value demotes the frame to scalar.

transform.slice where values use shorthand forms:
  - scalar  -> equality (==)
  - list    -> membership (in)
  - tuple   -> range (between, inclusive both ends)
For structured predicates (op + value), use observe(where=...) instead.
"""

from __future__ import annotations

from _fixtures.tiny_semantic import METRIC_ID, ensure_loaded

# Setup: load the tiny semantic model and attach an examples session.
ensure_loaded()

import marivo.analysis as mv  # noqa: E402

session = mv.session.current()
region = session.catalog.get("dimension.sales.orders.region")
revenue_by_region = session.observe(
    session.catalog.get(f"metric.{METRIC_ID}"),
    timescope={"start": "2026-07-01", "end": "2026-10-01"},
    dimensions=[region],
)

# Scalar shorthand: filter to one exact value (equality).
sliced = session.transform.slice(
    revenue_by_region,
    where={region: "north"},
)
sliced.show()

# List shorthand: keep multiple values (membership).
sliced_multi = session.transform.slice(
    revenue_by_region,
    where={region: ["north", "south"]},
)
sliced_multi.show()

# Expected output (two summaries printed):
# [1] kind='metric_frame' row_count=1 columns=['value']
#     semantic_shape='scalar' lineage_oneliner='observe -> transform'
# [2] kind='metric_frame' row_count=2 columns=['region', 'value']
#     semantic_shape='segmented' lineage_oneliner='observe -> transform'
