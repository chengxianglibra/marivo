"""Pattern: discover point-anomaly candidates from a MetricFrame.

When to use: you want z-score anomaly candidates for a known metric series.
Output shape: a CandidateSet with source row references, score, direction, and
threshold.
"""

from __future__ import annotations

from _fixtures.tiny_semantic import METRIC_ID, ensure_loaded

# Setup: load the tiny semantic model and attach an examples session.
ensure_loaded()

import marivo.analysis_py as mv  # noqa: E402

session = mv.session.active()
series = mv.observe(
    mv.MetricRef(id=METRIC_ID),
    window={"start": "2026-07-01", "end": "2026-09-30", "grain": "month"},
    session=session,
)
candidates = mv.discover(
    series,
    objective="point_anomalies",
    strategy="zscore",
    threshold=1.0,
    session=session,
)
summary = candidates.summary()
print(f"kind={summary.kind!r}")
print(f"objective={candidates.meta.objective!r}")
print(f"row_count={summary.row_count}")
print(f"columns={summary.columns!r}")

# Expected output:
# kind='candidate_set'
# objective='point_anomalies'
# row_count=2
# columns=['candidate_id', 'source_ref', 'source_row_index', 'value_column', 'observed_value', 'score', 'direction', 'threshold', 'keys_json']
