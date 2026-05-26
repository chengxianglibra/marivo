"""Pattern: test whether a metric mean changed between compatible samples."""

from __future__ import annotations

from _fixtures.tiny_semantic import METRIC_ID, ensure_loaded

ensure_loaded()

import marivo.analysis_py as mv  # noqa: E402

session = mv.session.active()
cur = mv.observe(
    mv.MetricRef(id=METRIC_ID),
    window={"start": "2026-07-01", "end": "2026-09-30", "grain": "month"},
    session=session,
)
base = mv.observe(
    mv.MetricRef(id=METRIC_ID),
    window={"start": "2026-07-01", "end": "2026-09-30", "grain": "month"},
    session=session,
)
result = mv.test(cur, base, session=session)

assert result.meta.kind == "hypothesis_test_result"
assert result.meta.hypothesis == "mean_changed"
assert {"p_value", "reason_code", "rejected"}.issubset(result.columns)
print(result.summary())
