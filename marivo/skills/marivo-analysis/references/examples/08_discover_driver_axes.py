"""Pattern: discover driver axes on a DeltaFrame and feed the top axis to decompose.

When to use: you have a delta and want to know which dimension explains the most.
Output shape: a CandidateSet[driver_axis], then decompose on the rank-1 axis.
"""

from __future__ import annotations

from _fixtures.tiny_semantic import METRIC_ID, ensure_loaded

# Setup: load the tiny semantic model and attach an examples session.
ensure_loaded()

import marivo.analysis as mv  # noqa: E402
from marivo.semantic.refs import DimensionRef  # noqa: E402

session = mv.session.current()
region = session.catalog.get("sales.orders.region")
current = session.observe(
    session.catalog.get(METRIC_ID),
    timescope={"start": "2026-07-01", "end": "2026-10-01"},
    dimensions=[region],
)
baseline = session.observe(
    session.catalog.get(METRIC_ID),
    timescope={"start": "2025-07-01", "end": "2025-10-01"},
    dimensions=[region],
)
delta = session.compare(
    current,
    baseline,
    alignment=mv.window_bucket(),
)

axis_candidates = session.discover.driver_axes(
    delta,
    value="delta",
    search_space=[region],
)
top_axis = axis_candidates.select(rank=1, attribute="axis")
assert top_axis == DimensionRef("sales.orders.region")
print(f"top_axis={top_axis.id}")

drivers = session.attribute(delta, axes=[top_axis])
print(f"drivers.kind={drivers.meta.kind!r}")

# Expected output:
# top_axis=sales.orders.region
# drivers.kind='attribution_frame'
