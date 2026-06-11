"""Pattern: forecast a time-series metric forward."""

from __future__ import annotations

from _fixtures.tiny_semantic import METRIC_ID, ensure_loaded

ensure_loaded()

import marivo.analysis as mv  # noqa: E402

session = mv.session.current()
history = session.observe(
    mv.MetricRef(METRIC_ID),
    timescope={"start": "2026-07-01", "end": "2026-10-01"},
    grain="month",
)
forecast = session.forecast(
    history,
    horizon=2,
    model="naive",
)

assert forecast.meta.kind == "forecast_frame"
assert forecast.meta.horizon == 2
assert {"time", "predicted", "lower", "upper", "reason_code"}.issubset(forecast.columns)
print(forecast.summary())
