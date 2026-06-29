"""Pattern: observe a single metric for one window.

When to use: you need the value of a known metric inside a fixed time window.
Output shape: a MetricFrame with one row and one metric value column.
"""

from __future__ import annotations

import marivo.analysis as mv

print(mv.help_text("observe").splitlines()[0])

session = mv.session.get_or_create(
    name="examples",
    default_calendar="cn_holidays",
)
metric = session.catalog.get("metric.sales.revenue")
cur = session.observe(
    metric,
    timescope={"start": "2026-07-01", "end": "2026-10-01"},
)
cur.show()
contract = cur.contract()
print(f"contract_kind={contract.kind!r}")
print(f"affordance_count={len(contract.affordances)}")

# Expected output:
# MetricFrame identity line, columns preview, bounded rows, available footer.
