"""Pattern: test whether a metric mean changed between compatible samples."""

from __future__ import annotations

from _fixtures.tiny_semantic import METRIC_ID, ensure_loaded

ensure_loaded()

import marivo.analysis as mv  # noqa: E402

session = mv.session.current()
cur = session.observe(
    mv.MetricRef(METRIC_ID),
    timescope={"start": "2026-07-01", "end": "2026-10-01"},
    grain="month",
)
base = session.observe(
    mv.MetricRef(METRIC_ID),
    timescope={"start": "2026-07-01", "end": "2026-10-01"},
    grain="month",
)
result = session.hypothesis_test(cur, base)

assert result.meta.kind == "hypothesis_test_result"
assert result.meta.hypothesis == "mean_changed"
assert {"p_value", "reason_code", "rejected"}.issubset(result.columns)
print(result.summary())
