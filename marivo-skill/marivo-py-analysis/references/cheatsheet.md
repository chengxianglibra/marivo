# marivo-py-analysis cheatsheet

Use this as the compact routing table after loading the skill. For runnable
syntax, prefer `references/examples/*.py`.

## Intents

| Intent | Inputs | Output | Agent rule |
| --- | --- | --- | --- |
| `mv.observe` | registered `metric_id` | `MetricFrame` | Use `window={"start": "...", "end": "..."}` or structured `slice={field: {"op": ..., "value": ...}}`. |
| `mv.compare` | `MetricFrame`, `MetricFrame` | `DeltaFrame` | Both inputs must come from `observe`; never pass a `DeltaFrame` back in. |
| `mv.decompose` | `DeltaFrame` | `AttributionFrame` | Omit `by=` for scalar deltas; use `by=` only for a grouping column already present in the delta. |
| `mv.detect` | `MetricFrame` | anomaly `AttributionFrame` | Not a `CandidateSet`; check `frame.meta.attribution_kind == "anomaly"`. |
| `mv.correlate` | `MetricFrame`, `MetricFrame` | correlation `AttributionFrame` | Not a `CorrelationFrame`; check `frame.meta.attribution_kind == "correlation"`. |

## Frame Flow

| Frame | Created by | Valid next step |
| --- | --- | --- |
| `MetricFrame` | `mv.observe`, manual `MetricFrame.from_dataframe` for local series | `mv.compare`, `mv.detect`, `mv.correlate` |
| `DeltaFrame` | `mv.compare` | `mv.decompose` |
| `AttributionFrame` | `mv.decompose`, `mv.detect`, `mv.correlate` | Usually terminal; inspect with `.summary()` or `.to_pandas()` |

Frames are immutable. Use `frame.summary()` for a cheap read, `frame.head(n)`
for a small preview, and `frame.to_pandas()` when you need a mutable copy.

## Minimal Patterns

```python
import marivo.analysis_py as mv

cur = mv.observe(
    "sales.revenue",
    window={"start": "2026-07-01", "end": "2026-09-30"},
)
base = mv.observe(
    "sales.revenue",
    window={"start": "2025-07-01", "end": "2025-09-30"},
)
delta = mv.compare(cur, base, compare_type="yoy")
attribution = mv.decompose(delta)
print(attribution.summary())
```

```python
series = mv.observe(
    "sales.revenue",
    slice={"created_at": {"op": "between", "value": ["2026-07-01", "2026-09-30"]}},
)
anomalies = mv.detect(series, threshold=1.0)
print(anomalies.meta.attribution_kind)  # "anomaly"
```

## Discovery Helpers

| Need | Call |
| --- | --- |
| Check active session without raising | `mv.session.current()` |
| Read recent jobs without raising | `mv.session.history()` |
| Create, switch, or list sessions | `mv.session.create(name=...)`, `mv.session.switch(name=...)`, `mv.session.list_sessions()` |
| Inspect SDK entrypoints | `mv.help()` or `mv.help("compare")` |
| Confirm metric ids | `import marivo.semantic_py as ms; ms.list_metrics()` |

Metric ids are strings such as `"model.metric"`. Do not guess them from metric
display names; call `ms.list_metrics()` against the loaded semantic project.
