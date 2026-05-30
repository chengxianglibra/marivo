"""Pattern: discover driver axes on a DeltaFrame and feed the top axis to decompose.

When to use: you have a delta and want to know which dimension explains the most.
Output shape: a CandidateSet[driver_axis], then decompose on the rank-1 axis.
"""

from __future__ import annotations

from _fixtures.tiny_semantic import METRIC_ID, ensure_loaded

# Setup: load the tiny semantic model and attach an examples session.
ensure_loaded()

import marivo.analysis as mv  # noqa: E402

session = mv.session.active()
current = session.observe(
    mv.MetricRef(id=METRIC_ID),
    timescope={"start": "2026-07-01", "end": "2026-09-30"},
    dimensions=[mv.DimensionRef(id="region")],
)
baseline = session.observe(
    mv.MetricRef(id=METRIC_ID),
    timescope={"start": "2025-07-01", "end": "2025-09-30"},
    dimensions=[mv.DimensionRef(id="region")],
)
delta = session.compare(
    current,
    baseline,
    alignment=mv.AlignmentPolicy(kind="window_bucket"),
)

axis_candidates = session.discover.driver_axes(
    delta,
    value="delta",
    search_space=[mv.DimensionRef(id="region")],
)
top_axis = axis_candidates.select(rank=1, attribute="axis")
assert isinstance(top_axis, mv.DimensionRef)
print(f"top_axis={top_axis.id}")

drivers = session.decompose(delta, axis=top_axis)
print(f"drivers.kind={drivers.meta.kind!r}")

# Expected output:
# top_axis=region
# drivers.kind='attribution_frame'
