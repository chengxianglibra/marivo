"""Shared lightweight fixtures for Python-native analysis tests."""

from __future__ import annotations


def seeded_time_series_metric_frame(
    *,
    session,
    grain: str = "day",
    n_buckets: int = 30,
    segments: list[str] | None = None,
    value_pattern: str = "linear",
    seed: int = 42,
):
    import numpy as np
    import pandas as pd

    from marivo.analysis.frames.metric import MetricFrame

    rng = np.random.default_rng(seed)
    freq_by_grain = {"day": "D", "week": "W-MON"}
    if grain not in freq_by_grain:
        raise ValueError(f"unsupported fixture grain {grain!r}")
    times = pd.date_range("2026-01-01", periods=n_buckets, freq=freq_by_grain[grain])

    def value_at(i: int) -> float:
        if value_pattern == "constant":
            return 10.0
        if value_pattern == "linear":
            return float(10 + i)
        if value_pattern == "seasonal_7":
            return float(100 + (i % 7) * 3)
        if value_pattern == "noisy":
            return float(10 + i + rng.normal(0, 0.1))
        raise ValueError(f"unsupported fixture value_pattern {value_pattern!r}")

    rows: list[dict[str, object]] = []
    if segments is None:
        for idx, bucket in enumerate(times):
            rows.append({"time": bucket, "value": value_at(idx)})
        semantic_kind = "time_series"
        axes = {"time": {"field": "time", "grain": grain}}
    else:
        for segment in segments:
            offset = float(len(rows))
            for idx, bucket in enumerate(times):
                rows.append({"segment": segment, "time": bucket, "value": value_at(idx) + offset})
        semantic_kind = "panel"
        axes = {"time": {"field": "time", "grain": grain}, "dimensions": [{"field": "segment"}]}

    return MetricFrame.from_dataframe(
        pd.DataFrame(rows),
        metric_id="sales.revenue",
        axes=axes,
        measure={"field": "value", "aggregation": "sum"},
        semantic_kind=semantic_kind,
        semantic_model="sales",
        window={
            "start": str(times[0].date()),
            "end": str(times[-1].date()),
            "grain": grain,
            "time_field": "time",
        },
        session=session,
    )
