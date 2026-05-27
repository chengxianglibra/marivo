"""Pattern: discover driver axes on a DeltaFrame and feed the top axis to decompose.

When to use: you have a delta and want to know which dimension explains the most.
Output shape: a CandidateSet[driver_axis], then decompose on the rank-1 axis.
"""

from __future__ import annotations

from _fixtures.tiny_semantic import METRIC_ID, ensure_loaded

# Setup: load the tiny semantic model and attach an examples session.
ensure_loaded()

import marivo.analysis_py as mv  # noqa: E402

session = mv.session.active()
current = mv.observe(
    mv.MetricRef(id=METRIC_ID),
    window={"start": "2026-07-01", "end": "2026-09-30"},
    dimensions=[mv.DimensionRef(id="region")],
    session=session,
)
baseline = mv.observe(
    mv.MetricRef(id=METRIC_ID),
    window={"start": "2025-07-01", "end": "2025-09-30"},
    dimensions=[mv.DimensionRef(id="region")],
    session=session,
)
delta = mv.compare(
    current,
    baseline,
    alignment=mv.AlignmentPolicy(kind="calendar_bucket"),
    session=session,
)

axis_candidates = mv.discover.driver_axes(
    delta,
    value="delta",
    search_space=[mv.DimensionRef(id="region")],
    session=session,
)
top_axis = mv.select(axis_candidates, rank=1, attribute="axis")
assert isinstance(top_axis, mv.DimensionRef)
print(f"top_axis={top_axis.id}")

drivers = mv.decompose(delta, axis=top_axis, session=session)
print(f"drivers.kind={drivers.meta.kind!r}")

# Expected output:
# top_axis=region
# drivers.kind='attribution_frame'
