"""Pattern: surface a period shift on a DeltaFrame[time_series].

When to use: you have a YoY/WoW delta and want to highlight contiguous
windows where the metric drifted from its overall baseline.
Output shape: CandidateSet[period_shift]; tiny fixtures may surface zero rows
when no shift crosses the threshold. Inputs with fewer than four buckets raise
DiscoverInsufficientDataError.
"""

from __future__ import annotations

from _fixtures.tiny_semantic import METRIC_ID, ensure_loaded

# Setup: load the tiny semantic model and attach an examples session.
ensure_loaded()

import marivo.analysis as mv  # noqa: E402

session = mv.session.active()
current = session.observe(
    mv.MetricRef(METRIC_ID),
    timescope={"start": "2026-07-01", "end": "2026-10-01"},
    grain="month",
)
baseline = session.observe(
    mv.MetricRef(METRIC_ID),
    timescope={"start": "2025-07-01", "end": "2025-10-01"},
    grain="month",
)
delta = session.compare(
    current,
    baseline,
    alignment=mv.AlignmentPolicy(kind="window_bucket"),
)
try:
    shifts = session.discover.period_shifts(delta, value="delta")
except mv.errors.DiscoverInsufficientDataError as exc:
    print(f"period_shifts_error={exc.kind}")
    print(f"minimum_buckets={exc.details['minimum']}")
else:
    print(f"shifts.objective={shifts.meta.objective!r}")
    print(f"shifts.row_count={shifts.meta.row_count}")
    if shifts.meta.row_count:
        window = shifts.select(rank=1, attribute="window")
        print(f"first_shift_window={window.start}..{window.end}")

# Expected output:
# period_shifts_error=DiscoverInsufficientData
# minimum_buckets=4
