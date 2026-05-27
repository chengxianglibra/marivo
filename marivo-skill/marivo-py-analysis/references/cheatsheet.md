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
| `mv.test(a, b)` | `MetricFrame + MetricFrame` | `HypothesisTestResult` | Paired `mean_changed` test |
| `mv.forecast(history, horizon=7)` | `MetricFrame(time_series\|panel)` | `ForecastFrame` | Naive / seasonal naive / drift projection |
| `mv.assess_quality(frame)` | `MetricFrame` | `QualityReport` | Row count, null ratio, time coverage, duplicate key checks |

## Frame Flow

| Frame | Created by | Valid next step |
| --- | --- | --- |
| `MetricFrame` | `mv.observe`, manual `MetricFrame.from_dataframe` for local series | `mv.compare`, `mv.discover`, `mv.correlate` |
| `DeltaFrame` | `mv.compare` | `mv.decompose` |
| `CandidateSet` | `mv.discover` | Usually terminal; inspect with `.summary()` or `.to_pandas()` |
| `AssociationResult` | `mv.correlate` | Usually terminal; inspect with `.summary()` or `.to_pandas()` |
| `HypothesisTestResult` | `mv.test` | Usually terminal; inspect with `.summary()` or `.to_pandas()` |
| `ForecastFrame` | `mv.forecast` | Usually terminal; inspect with `.summary()` or `.to_pandas()` |
| `QualityReport` | `mv.assess_quality` | Usually terminal; inspect with `.summary()` or `.to_pandas()` |
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

## Escape Hatch

| Need | Use |
| --- | --- |
| Temporary pandas scratch work | `scratch = mv.from_pandas(df, session=session)` |
| Temporary Ibis scratch query | `scratch = mv.explore_ibis(lambda con: con.table("orders"), datasource="warehouse", session=session)` |
| Re-enter canonical metric flow | `mv.promote_metric_frame(scratch, metric=mv.MetricRef("sales.revenue"), semantic_kind="segmented", measure_column="value", axes={"country": mv.DimensionRef("country")}, semantic_model="sales")` |
| Re-enter delta flow | `mv.promote_delta_frame(scratch, current=mv.ArtifactRef("frame_current"), baseline=mv.ArtifactRef("frame_baseline"), delta_column="delta", current_column="current", baseline_column="baseline")` |
| Re-enter attribution flow | `mv.promote_attribution_frame(scratch, source_delta=mv.ArtifactRef("frame_delta"), driver_field="country", contribution_column="contribution")` |

## Discovery Helpers

| Need | Call |
| --- | --- |
| Check active session without raising | `mv.session.current()` |
| Read recent jobs without raising | `mv.session.history()` |
| Create, switch, or list sessions | `mv.session.create(name=...)`, `mv.session.switch(name=...)`, `mv.session.list_sessions()` |
| Attach live data | `mv.session.create(name=..., backends=...)` or `mv.session.create(name=..., backend_factory=...)` |
| Inspect SDK entrypoints | `mv.help()` or `mv.help("compare")` |
| Confirm metric ids | `import marivo.semantic_py as ms; ms.list_metrics()` |

Metric refs wrap exact ids such as `mv.MetricRef("model.metric")`. Do not guess
ids from metric display names; call `ms.list_metrics()` against the loaded
semantic project.

## Backend Setup

Analysis intents that execute against live semantic datasets need a session
backend. Use `backends={...}` for a small static mapping or
`backend_factory(datasource_name)` when the script must route by datasource.

```python
import ibis
import marivo.analysis_py as mv

def make_backend(datasource_name: str):
    if datasource_name not in {"warehouse", "sales.warehouse"}:
        raise KeyError(datasource_name)
    return ibis.trino.connect(
        host="<trino_host>",
        port=80,
        user="<user>",
        database="<catalog>",
        source="<source>",
        client_tags=["standby", "routing_group=bsk_wide"],
    )

mv.session.create(name="analysis", backend_factory=make_backend)
```

For Trino, map prompt `catalog` to Ibis `database`, and map
`client-tags`/`client_tags` to Python `client_tags` as a list. See
`references/backend-setup.md` for the full mapping and guardrails.
