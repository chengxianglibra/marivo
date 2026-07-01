"""Pattern: discover point-anomaly candidates from a MetricFrame.

When to use: you want z-score anomaly candidates for a known metric series.
Output shape: a CandidateSet whose rows are item-shaped (item_id, score,
direction, reason_codes_json, source_refs_json, keys_json, window_start /
window_end, affordances_json) in the union-of-columns layout.
"""

from __future__ import annotations

import marivo.analysis as mv

print(mv.help_text("discover").splitlines()[0])

session = mv.session.get_or_create(
    name="examples",
    default_calendar="cn_holidays",
)
series = session.observe(
    session.catalog.get("metric.sales.revenue"),
    time_scope={"start": "2026-04-01", "end": "2026-10-01"},
    grain="month",
)
candidates = session.discover.point_anomalies(series, threshold=1.0)
candidates.show()
contract = candidates.contract()
print(f"kind={candidates.kind!r}")
print(f"objective={candidates.meta.objective!r}")
print(f"row_count={len(candidates)}")
print(f"columns={candidates.columns!r}")
print(f"contract_kind={contract.kind!r}")

# Expected output:
# CandidateSet show() card, then printed kind/objective/row_count/columns lines.
