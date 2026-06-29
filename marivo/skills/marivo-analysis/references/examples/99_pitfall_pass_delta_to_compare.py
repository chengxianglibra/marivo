"""
Pitfall: passing DeltaFrame back into compare.
When triggered: the agent uses `delta` instead of `base` for the second compare argument.

Expected output:
    SemanticKindMismatchError
    Fix:
    delta = session.compare(cur, base, alignment=mv.window_bucket())
"""

from __future__ import annotations

import marivo.analysis as mv

session = mv.session.get_or_create(
    name="examples",
    default_calendar="cn_holidays",
)
metric = session.catalog.get("metric.sales.revenue")
cur = session.observe(
    metric,
    timescope={"start": "2026-07-01", "end": "2026-10-01"},
)
base = session.observe(
    metric,
    timescope={"start": "2025-07-01", "end": "2025-10-01"},
)
delta = session.compare(cur, base, alignment=mv.window_bucket())

try:
    session.compare(cur, delta)
except mv.errors.SemanticKindMismatchError as e:
    print(e)
