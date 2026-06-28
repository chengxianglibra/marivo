"""Pattern: recover a persisted frame across script boundaries without re-querying.

When to use: a follow-up script needs a frame produced by a previous script
in the same session. Use session.get_frame(ref) to reload from disk instead
of re-running observe.
Output shape: same frame type as the original (MetricFrame, DeltaFrame, etc.).
"""

from __future__ import annotations

from _fixtures.tiny_semantic import METRIC_ID, ensure_loaded

# Script A: observe and record the ref.
ensure_loaded()

import marivo.analysis as mv  # noqa: E402

session = mv.session.current()
frame_a = session.observe(
    session.catalog.get(f"metric.{METRIC_ID}"),
    timescope={"start": "2026-07-01", "end": "2026-10-01"},
)
ref = frame_a.ref
print(f"ref={ref!r}")

# --- script boundary ---
# In a real follow-up script, re-attach the same session by name:
#   session = mv.session.get_or_create(name="my_analysis")
# Then discover available frames by metric_id:
summaries = session.frame_summaries()
matched = [s for s in summaries if s.metric_id == METRIC_ID]
print(f"found {len(matched)} frame(s) for {METRIC_ID!r}")

# Load the frame by ref — zero datasource queries.
recovered = session.get_frame(ref)
print(f"recovered ref={recovered.ref!r} kind={recovered.meta.kind!r}")

# The recovered frame is fully functional.
base = session.observe(
    session.catalog.get(f"metric.{METRIC_ID}"),
    timescope={"start": "2025-07-01", "end": "2025-10-01"},
)
delta = session.compare(recovered, base, alignment=mv.window_bucket())
delta.show()

# Expected output:
# ref='art_...'
# found N frame(s) for 'sales.revenue'
# recovered ref='art_...' kind='metric_frame'
# <DeltaFrame ref=... rows=1; call .show() to inspect>
