"""Pattern: discover point-anomaly candidates from a MetricFrame.

When to use: you want z-score anomaly candidates for a known metric series.
Output shape: a CandidateSet whose rows are item-shaped (item_id, score,
direction, reason_codes_json, source_refs_json, keys_json, window_start /
window_end, recommended_followups_json) in the union-of-columns layout.
"""

from __future__ import annotations

from _fixtures.tiny_semantic import METRIC_ID, ensure_loaded

# Setup: load the tiny semantic model and attach an examples session.
ensure_loaded()

import marivo.analysis as mv  # noqa: E402

session = mv.session.active()
series = session.observe(
    mv.MetricRef(METRIC_ID),
    timescope={"start": "2026-07-01", "end": "2026-10-01"},
    grain="month",
)
candidates = session.discover.point_anomalies(series, threshold=1.0)
summary = candidates.summary()
print(f"kind={summary.kind!r}")
print(f"objective={candidates.meta.objective!r}")
print(f"row_count={summary.row_count}")
print(f"columns={summary.columns!r}")

# Expected output:
# kind='candidate_set'
# objective='point_anomalies'
# row_count=2
# columns=['item_id', 'score', 'direction', 'reason_codes_json', 'source_refs_json', 'selector_json', 'keys_json', 'window_start', 'window_end', 'baseline_window_start', 'baseline_window_end', 'axis', 'peer_scope_json', 'recommended_followups_json']
