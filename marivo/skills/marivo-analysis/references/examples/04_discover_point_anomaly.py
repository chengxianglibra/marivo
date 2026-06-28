"""Pattern: discover point-anomaly candidates from a MetricFrame.

When to use: you want z-score anomaly candidates for a known metric series.
Output shape: a CandidateSet whose rows are item-shaped (item_id, score,
direction, reason_codes_json, source_refs_json, keys_json, window_start /
window_end, affordances_json) in the union-of-columns layout.
"""

from __future__ import annotations

from _fixtures.tiny_semantic import METRIC_ID, ensure_loaded

# Setup: load the tiny semantic model and attach an examples session.
ensure_loaded()

import marivo.analysis as mv  # noqa: E402

session = mv.session.current()
series = session.observe(
    session.catalog.get(f"metric.{METRIC_ID}"),
    timescope={"start": "2026-07-01", "end": "2026-10-01"},
    grain="month",
)
candidates = session.discover.point_anomalies(series, threshold=1.0)
candidates.show()
print(f"kind={candidates.kind!r}")
print(f"objective={candidates.meta.objective!r}")
print(f"row_count={len(candidates)}")
print(f"columns={candidates.columns!r}")

# Expected output:
# CandidateSet show() card, then printed kind/objective/row_count/columns lines.
