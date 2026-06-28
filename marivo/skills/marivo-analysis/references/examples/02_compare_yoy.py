"""Pattern: compare one metric across aligned year-over-year windows.

When to use: you need the current value, prior-year baseline, absolute delta,
and percent change for the same metric over matching calendar windows.
Output shape: a DeltaFrame with one row containing current, baseline, delta,
pct_change, and pct_change_status columns.
"""

from __future__ import annotations

from _fixtures.tiny_semantic import METRIC_ID, ensure_loaded

# Setup: load the tiny semantic model and attach an examples session.
ensure_loaded()

import marivo.analysis as mv  # noqa: E402

session = mv.session.current()
cur = session.observe(
    session.catalog.get(f"metric.{METRIC_ID}"),
    timescope={"start": "2026-07-01", "end": "2026-10-01"},
)
base = session.observe(
    session.catalog.get(f"metric.{METRIC_ID}"),
    timescope={"start": "2025-07-01", "end": "2025-10-01"},
)
delta = session.compare(cur, base, alignment=mv.window_bucket())
delta.show()
print(f"kind={delta.kind!r}")
print(f"row_count={len(delta)}")
print(f"columns={delta.columns!r}")

# Expected output:
# DeltaFrame show() card, then printed kind/row_count/columns lines from the
# explicit print() calls above.
