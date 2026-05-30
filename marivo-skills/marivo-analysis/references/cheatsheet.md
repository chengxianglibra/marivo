# marivo-analysis cheatsheet

Use this as the compact routing table after loading the skill. For runnable
syntax, prefer `references/examples/*.py`. Assume `marivo` is imported from the
active Python environment, not from a local Marivo source checkout.

## Intents

| Intent | Inputs | Output | Agent rule |
| --- | --- | --- | --- |
| `session.observe` | `mv.MetricRef("model.metric")` | `MetricFrame` | Use `window={"start": "...", "end": "..."}` or structured `where={field: {"op": ..., "value": ...}}`. |
| `session.compare` | `MetricFrame`, `MetricFrame` | `DeltaFrame` | Both inputs must come from `observe`; never pass a `DeltaFrame` back in. |
| `session.decompose` | `DeltaFrame`, `mv.DimensionRef("column")` | `AttributionFrame` | Always pass `axis=...`; `model.field` refs resolve to the persisted delta column `field`. |
| `session.discover.<objective>` | `MetricFrame` or `DeltaFrame` | `CandidateSet` | Use the typed helper from the table below; tabular row shape follows the `CandidateShape`. |
| `candidates.select(...)` | `CandidateSet` | typed value (`DimensionRef`, `AbsoluteWindow`, selector dict, scalar) | Use `rank=` (1-indexed) and `attribute=` (e.g. `"axis"`, `"window"`, `"selector"`, `"recommended_followups"`, `"keys.<dim>"`). |
| `session.correlate` | `MetricFrame`, `MetricFrame` | `AssociationResult` | Use `alignment=mv.AlignmentPolicy(kind="window_bucket")`; default lag is zero. |
| `session.hypothesis_test(a, b)` | `MetricFrame + MetricFrame` | `HypothesisTestResult` | Paired `mean_changed` test |
| `session.forecast(history, horizon=7)` | `MetricFrame(time_series\|panel)` | `ForecastFrame` | Naive / seasonal naive / drift projection |
| `session.assess_quality(frame)` | `MetricFrame` | `QualityReport` | Row count, null ratio, time coverage, duplicate key checks |

## Frame Flow

| Frame | Created by | Valid next step |
| --- | --- | --- |
| `MetricFrame` | `session.observe`, manual `MetricFrame.from_dataframe` for local series | `session.compare`, `session.discover.<objective>`, `session.correlate` |
| `DeltaFrame` | `session.compare` | `session.decompose` |
| `CandidateSet` | `session.discover.<objective>` | `candidates.select(...)` to pull a typed field; otherwise terminal. Inspect with `.summary()`, `.preview(limit=...)`, or `.to_pandas()` |
| `AssociationResult` | `session.correlate` | Usually terminal; inspect with `.summary()`, `.preview(limit=...)`, or `.to_pandas()` |
| `HypothesisTestResult` | `session.hypothesis_test` | Usually terminal; inspect with `.summary()`, `.preview(limit=...)`, or `.to_pandas()` |
| `ForecastFrame` | `session.forecast` | Usually terminal; inspect with `.summary()`, `.preview(limit=...)`, or `.to_pandas()` |
| `QualityReport` | `session.assess_quality` | Usually terminal; inspect with `.summary()`, `.preview(limit=...)`, or `.to_pandas()` |
| `AttributionFrame` | `session.decompose` | Usually terminal; inspect with `.summary()`, `.preview(limit=...)`, or `.to_pandas()` |

Frames are immutable. Use `frame.summary()` for a cheap read,
`frame.preview(limit=n)` for a bounded row projection, and
`frame.to_pandas()` when you need a mutable copy. Use
`frame.to_pandas().head(n)` only when you explicitly want pandas behavior.

Use `mv.MetricRef(...)`, `mv.DimensionRef(...)`, `mv.CalendarRef(...)`,
`mv.AlignmentPolicy(...)`, and `mv.LagPolicy(...)` at public operator
boundaries. Do not pass bare strings directly to `observe`, `decompose`, or
calendar-backed `compare`.

## Minimal Patterns

```python
import marivo.analysis as mv

cur = session.observe(
    mv.MetricRef("sales.revenue"),
    window={"start": "2026-07-01", "end": "2026-09-30"},
)
base = session.observe(
    mv.MetricRef("sales.revenue"),
    window={"start": "2025-07-01", "end": "2025-09-30"},
)
delta = session.compare(cur, base, alignment=mv.AlignmentPolicy(kind="window_bucket"))
attribution = session.decompose(delta, axis=mv.DimensionRef("bucket_start"))
print(attribution.summary())
```

```python
series = session.observe(
    mv.MetricRef("sales.revenue"),
    where={"created_at": {"op": "between", "value": ["2026-07-01", "2026-09-30"]}},
)
candidates = session.discover.point_anomalies(series, threshold=1.0)
print(candidates.meta.objective)  # "point_anomalies"
```

## Escape Hatch

| Need | Use |
| --- | --- |
| Temporary pandas scratch work | `scratch = session.from_pandas(df)` |
| Temporary Ibis scratch query | `scratch = session.explore_ibis(lambda con: con.table("orders"), datasource="warehouse")` |
| Re-enter canonical metric flow | `session.promote_metric_frame(scratch, metric=mv.MetricRef("sales.revenue"), semantic_kind="segmented", measure_column="value", axes={"country": mv.DimensionRef("country")}, semantic_model="sales")` |
| Re-enter delta flow | `session.promote_delta_frame(scratch, current=mv.ArtifactRef("frame_current"), baseline=mv.ArtifactRef("frame_baseline"), delta_column="delta", current_column="current", baseline_column="baseline")` |
| Re-enter attribution flow | `session.promote_attribution_frame(scratch, source_delta=mv.ArtifactRef("frame_delta"), driver_field="country", contribution_column="contribution")` |

## Discover Objectives

| Helper | Source | Returns CandidateSet[shape] | Default strategy | Required kwargs |
| --- | --- | --- | --- | --- |
| `session.discover.point_anomalies` | `MetricFrame[time_series\|panel]` | `point_anomaly` | `zscore` | – |
| `session.discover.period_shifts` | `DeltaFrame[time_series\|panel]` | `period_shift` | `delta_window_zscore` | At least 4 time buckets in one series |
| `session.discover.driver_axes` | `DeltaFrame[*]` | `driver_axis` | `variance_explained` | `search_space=[DimensionRef(...), ...]` |
| `session.discover.interesting_slices` | `MetricFrame[*]` or `DeltaFrame[*]` | `slice` | `delta_magnitude` | – (defaults to all dimension columns) |
| `session.discover.interesting_windows` | `MetricFrame[time_series\|panel]` or `DeltaFrame[time_series\|panel]` | `window` | `rolling_zscore` | – |
| `session.discover.cross_sectional_outliers` | `MetricFrame[segmented\|panel]` | `cross_sectional_outlier` | `mad` | – |

Pass `value="<column>"` to disambiguate when the source has more than one
numeric column. `select(attribute=...)` accepts `"item_id"`, `"score"`, `"axis"`,
`"window"`, `"baseline_window"`, `"selector"`, `"direction"`,
`"recommended_followups"`, plus dotted `"keys.<dim>"` / `"selector.<dim>"`.

## Discovery Helpers

| Need | Call |
| --- | --- |
| Check active session without raising | `mv.session.current()` |
| Read recent jobs | `session.recent_jobs(limit=5)` |
| Create or attach a session (idempotent) | `mv.session.get_or_create(name=...)` |
| List sessions | `mv.session.list()` |
| Attach live data | `mv.session.get_or_create(name=..., backend_factory=...)` |
| Inspect SDK entrypoints | `mv.help()` or `mv.help("discover")` |
| Inspect calendar file shape | `mv.help("calendar")` |
| Confirm metric ids | `import marivo.semantic as ms; project = ms.find_project(); assert project is not None; project.load(); project.list_metrics()` |

Relative windows and calendar alignment use the Python process system timezone. If a naive warehouse timestamp physically stores UTC, declare it in the semantic layer with `@ms.time_field(..., timezone="UTC")`.

Metric refs wrap exact ids such as `mv.MetricRef("model.metric")`. Do not guess
ids from metric display names; call `project.list_metrics()` after loading the
semantic project.

## Backend Setup

Analysis intents that execute against live semantic datasets need a session
backend. Use `backends={...}` for a small static mapping or
`backend_factory(datasource_name)` when the script must route by datasource.

```python
import os

import ibis
import marivo.analysis as mv

def make_backend(datasource_name: str):
    if datasource_name != "warehouse":
        raise KeyError(datasource_name)
    return ibis.trino.connect(
        host="<trino_host>",
        port=80,
        user=os.environ["TRINO_USER"],
        database="<catalog>",
        source="<source>",
        client_tags=["standby", "routing_group=bsk_wide"],
    )

mv.session.get_or_create(name="analysis", backend_factory=make_backend)
```

For Trino, map prompt `catalog` to Ibis `database`, and map
`client-tags`/`client_tags` to Python `client_tags` as a list. See
`references/backend-setup.md` for the full mapping and guardrails.

Datasource names are global, not model-qualified. Semantic datasets use `.dataset(datasource="warehouse")`; backend factories receive `"warehouse"`, never `"sales.warehouse"`.

When a dataset has multiple time fields, choose one in the observe window:

```python
session.observe(
    mv.MetricRef("sales.revenue"),
    window={"start": "2026-07-01", "end": "2026-07-31", "time_field": "create_date"},
)
```

Valid `AlignmentPolicy.kind` values are `window_bucket`, `dow_aligned`,
`holiday_aligned`, and `holiday_and_dow_aligned`; there is no separate
`ordinal` kind. `AlignmentPolicy(kind="window_bucket")` aligns by shared
`bucket_start` when available. For same-grain WoW/YoY windows with no shared
dates, it builds the expected buckets from each window, pairs them by ordinal
position, preserves the baseline date as `bucket_start_b`, and leaves sparse
observed buckets as `NaN` rather than failing compare.

Calendar-backed compare loads project-local files from
`.marivo/calendar/<name>.json`. Calendar entries are objects with
`date` and optional `holiday_id`; extra fields such as `name` or `label` are
rejected. Use `holiday_id` to match the same business holiday across years:

Calendar-backed `DeltaFrame` rows include `align_key`, `align_quality`,
`bucket_start_a`, and `bucket_start_b`. `align_key` is a compact JSON object
string, not an array. For example, day-of-week matches look like
`{"kind":"dow","iso_weekday":2,"period_week_offset":0}`, holiday matches
look like `{"kind":"holiday","holiday_id":"labor-day","holiday_ordinal":1}`,
workday matches look like `{"kind":"workday","workday_ordinal":1}`, and
nearest-prior-workday fallbacks look like
`{"kind":"fallback_workday","baseline_date":"2026-04-03"}`.

```json
{
  "name": "cn_holidays",
  "holidays": [
    {"date": "2026-05-01", "holiday_id": "labor-day"}
  ],
  "adjusted_workdays": [
    {"date": "2026-05-02"}
  ]
}
```
