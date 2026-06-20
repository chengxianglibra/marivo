"""
Pitfall: passing DeltaFrame back into compare.
When triggered: the agent uses `delta` instead of `base` for the second compare argument.

Expected output:
    SemanticKindMismatchError
    Fix:
    delta = session.compare(cur, base, alignment=mv.window_bucket())
"""

from __future__ import annotations

from _fixtures.tiny_semantic import METRIC_ID, ensure_loaded

# Setup: load the tiny semantic model and attach an examples session.
ensure_loaded()

import marivo.analysis as mv  # noqa: E402

session = mv.session.current()
cur = session.observe(
    session.catalog.get(METRIC_ID),
    where={
        session.catalog.get("sales.orders.created_at"): {
            "op": "between",
            "value": ["2026-07-01", "2026-09-30"],
        }
    },
)
base = session.observe(
    session.catalog.get(METRIC_ID),
    where={
        session.catalog.get("sales.orders.created_at"): {
            "op": "between",
            "value": ["2025-07-01", "2025-09-30"],
        }
    },
)
delta = session.compare(cur, base, alignment=mv.window_bucket())

try:
    session.compare(cur, delta)
except mv.errors.SemanticKindMismatchError as e:
    print(e)
