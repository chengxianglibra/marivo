"""Pattern: pick out the highest-impact region slice and re-slice the delta.

When to use: you have a segmented delta and want to focus on the segment
with the strongest signal, then drill into that segment with transform.
Output shape: CandidateSet[slice]; selector is a DimensionRef -> value map
that feeds straight into transform.slice(...).
"""

from __future__ import annotations

from _fixtures.tiny_semantic import METRIC_ID, ensure_loaded

# Setup: load the tiny semantic model and attach an examples session.
ensure_loaded()

import marivo.analysis as mv  # noqa: E402

session = mv.session.active()
current = session.observe(
    mv.MetricRef(id=METRIC_ID),
    window={"start": "2026-07-01", "end": "2026-09-30"},
    dimensions=[mv.DimensionRef(id="region")],
)
baseline = session.observe(
    mv.MetricRef(id=METRIC_ID),
    window={"start": "2025-07-01", "end": "2025-09-30"},
    dimensions=[mv.DimensionRef(id="region")],
)
delta = session.compare(
    current,
    baseline,
    alignment=mv.AlignmentPolicy(kind="window_bucket"),
)
slice_cands = session.discover.interesting_slices(
    delta,
    value="delta",
    search_space=[mv.DimensionRef(id="region")],
    threshold=0.5,
)
print(f"slices.row_count={slice_cands.meta.row_count}")
if slice_cands.meta.row_count:
    selector = slice_cands.select(rank=1, attribute="selector")
    rendered = {ref.id: value for ref, value in selector.items()}
    print(f"selector={rendered}")
    focus = session.transform.slice(delta, where=selector)
    print(f"focus.kind={focus.meta.kind!r}")
else:
    print("no slice candidates")

# Expected output:
# slices.row_count=
# selector=
# focus.kind='delta_frame'
