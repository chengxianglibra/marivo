# marivo-analysis cheatsheet

Use this as the compact routing table after loading the skill. For runnable
syntax, prefer `references/examples/*.py`. Assume `marivo` is imported from the
active Python environment, not from a local Marivo source checkout.

## Runtime Help

Use the uniform runtime help contract for exact callable, frame, policy, and
topic details:

```python
mv.help('discover')                      # objective compatibility and required kwargs
mv.help('alignment')                     # AlignmentPolicy variants
mv.help('MetricFrame')                   # methods and next_intents
mv.help('MetricFrame.components')        # method signature and doc
```

## Intents

| Intent | Inputs | Output | Agent rule |
| --- | --- | --- | --- |
| `session.observe` | `mv.MetricRef("domain.metric")` | `MetricFrame` | Use `timescope={"start": "...", "end": "..."}` (end is exclusive: `[start, end)`) or structured `where={dimension: {"op": ..., "value": ...}}`. |
| `session.compare` | `MetricFrame`, `MetricFrame` | `DeltaFrame` | Both inputs must come from `observe`; never pass a `DeltaFrame` back in. |
| `session.decompose` | `DeltaFrame`, `mv.DimensionRef("column")` | `AttributionFrame` | Always pass `axis=...`; `domain.dimension` refs resolve to the persisted delta column `dimension`. |
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
    timescope={"start": "2026-07-01", "end": "2026-10-01"},
)
base = session.observe(
    mv.MetricRef("sales.revenue"),
    timescope={"start": "2025-07-01", "end": "2025-10-01"},
)
delta = session.compare(cur, base, alignment=mv.AlignmentPolicy(kind="window_bucket"))
attribution = session.decompose(delta, axis=mv.DimensionRef("bucket_start"))
print(attribution.summary())
```

```python
series = session.observe(
    mv.MetricRef("sales.revenue"),
    where={mv.DimensionRef("created_at"): {"op": "between", "value": ["2026-07-01", "2026-09-30"]}},
)
candidates = session.discover.point_anomalies(series, threshold=1.0)
print(candidates.meta.objective)  # "point_anomalies"
```

## Escape Hatch

Use escape hatches only after checking the built-in intents. They are for
session-scoped scratch work when a step needs custom joins, raw table scans,
feature engineering, bespoke statistics, or library-specific processing that
Marivo does not model directly.

| Need | Use |
| --- | --- |
| Raw Ibis query against a registered backend | `scratch = session.explore_ibis(lambda con: con.table("orders"), datasource="warehouse", description="manual scan")` |
| Export a Marivo frame for mutable local analysis | `df = frame.to_pandas()` |
| Import pandas or library output into the session | `scratch = session.from_pandas(df, description="feature engineering output")` |
| Inspect scratch provenance | `scratch.meta.source_kind`, `scratch.meta.source_query`, `scratch.meta.source_datasource` |
| Re-enter canonical metric flow only when a typed intent needs it | `session.promote_metric_frame(scratch, metric=mv.MetricRef("sales.revenue"), semantic_kind="segmented", measure_column="value", axes={"country": mv.DimensionRef("country")}, semantic_model="sales")` |
| Re-enter delta flow only for typed change analysis | `session.promote_delta_frame(scratch, current=mv.ArtifactRef("frame_current"), baseline=mv.ArtifactRef("frame_baseline"), delta_column="delta", current_column="current", baseline_column="baseline")` |
| Re-enter attribution flow only for typed driver output | `session.promote_attribution_frame(scratch, source_delta=mv.ArtifactRef("frame_delta"), driver_field="country", contribution_column="contribution")` |

`session.explore_ibis(...)` calls the builder with the session backend
connection and requires an Ibis expression. It executes immediately and returns
an `ExplorationResult`, preserving source query and datasource metadata when
available.

```python
scratch = session.explore_ibis(
    lambda con: (
        con.table("orders")
        .filter(lambda t: t.country == "US")
        .aggregate(value=lambda t: t.revenue.sum())
    ),
    datasource="warehouse",
    description="US revenue raw scan",
)
print(scratch.meta.source_query)
```

Pandas output stays local until you import it. Imported scratch frames are
session artifacts but are not valid inputs to typed intents until promoted.

```python
df = frame.to_pandas()
df["share"] = df["value"] / df["value"].sum()
scratch = session.from_pandas(df, description="share calculation")
```

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
| Check active session without raising; returns Session or None | `mv.session.current()` |
| Read recent jobs | `session.recent_jobs(limit=5)` |
| Create or attach a session (idempotent) | `mv.session.get_or_create(name=...)` |
| List sessions | `mv.session.list()` |
| Attach live project data | `mv.session.get_or_create(name=...)` |
| Override backend resolution for tests/CI | `mv.session.get_or_create(name=..., backend_factory=..., use_datasources=False)` |
| Inspect SDK entrypoints | `mv.help()` or `mv.help("discover")` |
| Inspect calendar file shape | `mv.help("calendar")` |
| Confirm metric ids | `import marivo.semantic as ms; project = ms.find_project(); assert project is not None; project.load(); project.list_metrics()` |
| Recover a frame across scripts (no re-query) | `session.get_frame(ref)` |
| List persisted frame refs | `session.frames()` |
| Find frame ref by metric_id | `session.frame_summaries()` |
| Module-level frame load | `mv.load_frame(ref, session=session)` |

Calendar alignment and timestamp bucketing use the Python process system timezone. If a naive warehouse timestamp physically stores UTC, declare it in the semantic layer with `@ms.time_dimension(..., timezone="UTC")`.

Metric refs wrap exact ids such as `mv.MetricRef("model.metric")`. Do not guess
ids from metric display names; call `project.list_metrics()` after loading the
semantic project.

For cross-dataset base metrics (datasets cover multiple datasets with an explicit
`root_dataset`), call `session.observe(...)` with the normal arguments.
`dimensions=` and `where=` may target joined datasets; root predicates push
before widening, joined predicates apply after. Do not pass join policy or
route arguments. If planning fails, the repair error includes
`schema_version`, `code`, `candidates`, and `repair`.

For derived metrics (ratio, weighted-average), each component is planned
independently. If a derived observe fails, the repair code identifies which
component and which comparability check failed:

- `component-axis-unreachable`: a parent dimension is reachable from one
  component but not another. Make every component reach the dimension or drop it.
- `component-axis-field-mismatch`: components resolve the same dimension to
  different semantic field ids. Conform the dimension on a single field.
- `component-filter-unreachable`: a parent `where` filter is reachable from one
  component but not another. Make every component reach the field or drop the filter.
- `component-filter-field-mismatch`: components resolve the same filter key to
  different semantic field ids.
- `component-version-mismatch`: a versioned dataset has different mode, anchor,
  partition, or mapping digest across components. Make every component pin the
  same version.
- `snapshot-partition-missing`: at least one root anchor has no `p <= anchor`
  partition. Either widen `timescope` so available partitions cover all anchors,
  or backfill missing partitions.
- `nested-derived-unsupported`: a derived component is itself derived. Replace it
  with its base components.

## Backend Setup

Analysis intents that execute against live semantic datasets need a session
backend. In a real project, register `.marivo/datasource/*.py` definitions and
use the default session entrypoint:

```python
session = mv.session.get_or_create(name="analysis")
```

Use `backends={...}` for a small static mapping or
`backend_factory(datasource_name)` only when tests/CI or deterministic scripts
must override project datasource lookup.

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

mv.session.get_or_create(
    name="analysis",
    backend_factory=make_backend,
    use_datasources=False,
)
```

For Trino, map prompt `catalog` to Ibis `database`, and map
`client-tags`/`client_tags` to Python `client_tags` as a list. See
`references/backend-setup.md` for the full mapping and guardrails.

Datasource names are global, not model-qualified. Semantic datasets use `.dataset(datasource="warehouse")`; backend factories receive `"warehouse"`, never `"sales.warehouse"`.

When a dataset has multiple time dimensions, choose one with top-level `time_dimension`:

```python
session.observe(
    mv.MetricRef("sales.revenue"),
    timescope={"start": "2026-07-01", "end": "2026-08-01"},
    time_field=mv.DimensionRef("create_date"),
)
```

Valid `AlignmentPolicy.kind` values are `window_bucket`, `dow_aligned`,
`holiday_aligned`, and `holiday_and_dow_aligned`; there is no separate
`ordinal` kind. `AlignmentPolicy(kind="window_bucket")` aligns by shared
`bucket_start` when available. For same-grain WoW/YoY windows with no shared
dates, it builds the expected buckets from each window, pairs them by ordinal
position, preserves the baseline date as `bucket_start_b`, and treats sparse
observed buckets as one-sided rows rather than failing compare. One-sided
segmented and panel rows set the missing side to `0.0` for `delta` math and
mark the row with `presence_status` (`matched`, `new`, or `churned`).

Calendar-backed compare loads project-local files from
`.marivo/calendar/<name>.json`. Calendar entries are objects with
`date` and optional `holiday_id`; extra fields such as `name` or `label` are
rejected. Use `holiday_id` to match the same business holiday across years:
multi-period windows pair current and baseline periods by ordinal order.

Calendar-backed `DeltaFrame` rows include `presence_status`, `align_key`,
`align_quality`, `bucket_start_a`, and `bucket_start_b`. `align_key` is a compact JSON object
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
