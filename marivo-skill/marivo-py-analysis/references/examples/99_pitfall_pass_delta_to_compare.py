"""
Pitfall: passing DeltaFrame back into compare.
When triggered: the agent uses `delta` instead of `base` for the second compare argument.

Expected output:
    SemanticKindMismatchError
    Fix:
    delta = mv.compare(cur, base, alignment=mv.AlignmentPolicy(kind="calendar_bucket"))
"""

from __future__ import annotations

from _fixtures.tiny_semantic import METRIC_ID, ensure_loaded

# Setup: load the tiny semantic model and attach an examples session.
ensure_loaded()

import marivo.analysis_py as mv  # noqa: E402

session = mv.session.active()
cur = mv.observe(
    mv.MetricRef(id=METRIC_ID),
    where={"created_at": {"op": "between", "value": ["2026-07-01", "2026-09-30"]}},
    session=session,
)
base = mv.observe(
    mv.MetricRef(id=METRIC_ID),
    where={"created_at": {"op": "between", "value": ["2025-07-01", "2025-09-30"]}},
    session=session,
)
delta = mv.compare(cur, base, alignment=mv.AlignmentPolicy(kind="calendar_bucket"), session=session)

try:
    mv.compare(cur, delta)  # type: ignore[arg-type]
except mv.errors.SemanticKindMismatchError as e:
    print(e)
