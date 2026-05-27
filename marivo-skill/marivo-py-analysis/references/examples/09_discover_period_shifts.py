"""Pattern: surface a period shift on a DeltaFrame[time_series].

When to use: you have a YoY/WoW delta and want to highlight contiguous
windows where the metric drifted from its overall baseline.
Output shape: CandidateSet[period_shift]; tiny fixtures may surface zero rows
because the rolling-window heuristic needs at least four buckets.
"""

from __future__ import annotations

from _fixtures.tiny_semantic import METRIC_ID, ensure_loaded

# Setup: load the tiny semantic model and attach an examples session.
ensure_loaded()

import marivo.analysis_py as mv  # noqa: E402

session = mv.session.active()
current = mv.observe(
    mv.MetricRef(id=METRIC_ID),
    window={"start": "2026-07-01", "end": "2026-09-30", "grain": "month"},
    session=session,
)
baseline = mv.observe(
    mv.MetricRef(id=METRIC_ID),
    window={"start": "2025-07-01", "end": "2025-09-30", "grain": "month"},
    session=session,
)
delta = mv.compare(
    current,
    baseline,
    alignment=mv.AlignmentPolicy(kind="calendar_bucket"),
    session=session,
)
shifts = mv.discover.period_shifts(delta, value="delta", session=session)
print(f"shifts.objective={shifts.meta.objective!r}")
print(f"shifts.row_count={shifts.meta.row_count}")
if shifts.meta.row_count:
    window = mv.select(shifts, rank=1, field="window")
    print(f"first_shift_window={window.start}..{window.end}")

# Expected output:
# shifts.objective='period_shifts'
# shifts.row_count=0
