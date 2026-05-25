"""
Pitfall: passing DeltaFrame back into compare.
When triggered: the agent uses `delta` instead of `base` for the second compare argument.

Expected output:
    SemanticKindMismatchError
    正确写法:
    delta = mv.compare(cur, base)
"""

from __future__ import annotations

from _fixtures.tiny_semantic import METRIC_ID, ensure_loaded

# Setup: load the tiny semantic model and attach an examples session.
ensure_loaded()

import marivo.analysis_py as mv  # noqa: E402

cur = mv.observe(
    METRIC_ID,
    slice={"created_at": {"op": "between", "value": ["2026-07-01", "2026-09-30"]}},
)
base = mv.observe(
    METRIC_ID,
    slice={"created_at": {"op": "between", "value": ["2025-07-01", "2025-09-30"]}},
)
delta = mv.compare(cur, base, compare_type="yoy")

try:
    mv.compare(cur, delta)  # type: ignore[arg-type]
except mv.errors.SemanticKindMismatchError as e:
    print(e)
