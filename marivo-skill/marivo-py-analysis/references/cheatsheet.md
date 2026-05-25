# marivo-py-analysis cheatsheet

Use this as the compact routing table after loading the skill. For runnable
syntax, prefer `references/examples/*.py`. Assume `marivo` is imported from the
active Python environment, not from a local Marivo source checkout.

## Intents

| Intent | Inputs | Output | Agent rule |
| --- | --- | --- | --- |
| `mv.observe` | `mv.MetricRef("model.metric")` | `MetricFrame` | Use `window={"start": "...", "end": "..."}` or structured `slice={field: {"op": ..., "value": ...}}`. |
| `mv.compare` | `MetricFrame`, `MetricFrame` | `DeltaFrame` | Both inputs must come from `observe`; never pass a `DeltaFrame` back in. |
| `mv.decompose` | `DeltaFrame`, `mv.DimensionRef("column")` | `AttributionFrame` | Always pass `axis=...`; the axis column must already be present in the delta. |
| `mv.discover` | `MetricFrame` | `CandidateSet` | Use `objective="point_anomalies"` for anomaly candidates. |
| `mv.correlate` | `MetricFrame`, `MetricFrame` | `AssociationResult` | Use `alignment=mv.AlignmentPolicy(kind="calendar_bucket")`; default lag is zero. |

## Frame Flow

| Frame | Created by | Valid next step |
| --- | --- | --- |
| `MetricFrame` | `mv.observe`, manual `MetricFrame.from_dataframe` for local series | `mv.compare`, `mv.discover`, `mv.correlate` |
| `DeltaFrame` | `mv.compare` | `mv.decompose` |
| `CandidateSet` | `mv.discover` | Usually terminal; inspect with `.summary()` or `.to_pandas()` |
| `AssociationResult` | `mv.correlate` | Usually terminal; inspect with `.summary()` or `.to_pandas()` |
| `AttributionFrame` | `mv.decompose` | Usually terminal; inspect with `.summary()` or `.to_pandas()` |

Frames are immutable. Use `frame.summary()` for a cheap read, `frame.head(n)`
for a small preview, and `frame.to_pandas()` when you need a mutable copy.

Use `mv.MetricRef(...)`, `mv.DimensionRef(...)`, `mv.CalendarRef(...)`,
`mv.AlignmentPolicy(...)`, and `mv.LagPolicy(...)` at public operator
boundaries. Do not pass bare strings directly to `observe`, `decompose`, or
calendar-backed `compare`.

## Minimal Patterns

```python
import marivo.analysis_py as mv

cur = mv.observe(
    mv.MetricRef("sales.revenue"),
    window={"start": "2026-07-01", "end": "2026-09-30"},
)
base = mv.observe(
    mv.MetricRef("sales.revenue"),
    window={"start": "2025-07-01", "end": "2025-09-30"},
)
delta = mv.compare(cur, base, alignment=mv.AlignmentPolicy(kind="calendar_bucket"))
attribution = mv.decompose(delta, axis=mv.DimensionRef("bucket_start"))
print(attribution.summary())
```

```python
series = mv.observe(
    mv.MetricRef("sales.revenue"),
    slice={"created_at": {"op": "between", "value": ["2026-07-01", "2026-09-30"]}},
)
candidates = mv.discover(series, objective="point_anomalies", threshold=1.0)
print(candidates.meta.objective)  # "point_anomalies"
```

## Discovery Helpers

| Need | Call |
| --- | --- |
| Check active session without raising | `mv.session.current()` |
| Read recent jobs without raising | `mv.session.history()` |
| Create, switch, or list sessions | `mv.session.create(name=...)`, `mv.session.switch(name=...)`, `mv.session.list_sessions()` |
| Inspect SDK entrypoints | `mv.help()` or `mv.help("compare")` |
| Confirm metric ids | `import marivo.semantic_py as ms; ms.list_metrics()` |

Metric refs wrap exact ids such as `mv.MetricRef("model.metric")`. Do not guess
ids from metric display names; call `ms.list_metrics()` against the loaded
semantic project.
