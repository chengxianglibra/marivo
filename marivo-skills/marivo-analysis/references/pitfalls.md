# marivo-analysis pitfalls

Use this file when a Marivo Python analysis script fails with a structured
exception. Fix the smallest script and re-run it with the active Python
environment where `marivo` is installed.

Use `mv.MetricRef(...)`, `mv.DimensionRef(...)`, `mv.CalendarRef(...)`,
`mv.AlignmentPolicy(...)`, and `mv.LagPolicy(...)` at public operator
boundaries. Do not pass bare strings directly to `observe`, `decompose`, or
calendar-backed `compare`.

## Wrong Python environment

**Symptom:** `ModuleNotFoundError: No module named 'marivo'` or script output
that clearly came from the system Python instead of the project virtualenv.

**Action:** do not keep trying `python`, `python3`, `pip`, or `pip3`.
Use the project virtualenv entrypoints directly:

```bash
.venv/bin/python -c 'import marivo.analysis as mv; mv.help()'
```

If this skill is being used outside the Marivo source checkout, replace `.venv`
with that project's actual virtualenv path.

## Passing a DeltaFrame back into compare

**Symptom from `references/examples/99_pitfall_pass_delta_to_compare.py`:**

```text
SemanticKindMismatchError: compare(current, baseline) expected MetricFrame for `baseline`, got DeltaFrame.

Location: session.compare call
Cause: got kind delta_frame, expected metric_frame; this usually means passing a compare result where an observe result is required.

Fix:
  cur  = session.observe(mv.MetricRef("sales.revenue"), timescope={"start": "2026-07-01", "end": "2026-09-30"})
  base = session.observe(mv.MetricRef("sales.revenue"), timescope={"start": "2025-07-01", "end": "2025-09-30"})
  delta = session.compare(cur, base, alignment=mv.AlignmentPolicy(kind="window_bucket"))

Docs: marivo-skills/marivo-analysis/references/pitfalls.md
```

**Action:** pass two `MetricFrame`s into `session.compare`, then pass the resulting
`DeltaFrame` to `session.decompose`.

```python
cur = session.observe(mv.MetricRef("sales.revenue"), timescope={"start": "2026-07-01", "end": "2026-09-30"})
base = session.observe(mv.MetricRef("sales.revenue"), timescope={"start": "2025-07-01", "end": "2025-09-30"})
delta = session.compare(cur, base, alignment=mv.AlignmentPolicy(kind="window_bucket"))
attribution = session.decompose(delta, axis=mv.DimensionRef("bucket_start"))
```

Use explicit dict windows or structured slices in new examples and
agent-authored scripts.

## Mutating a frame directly

**Symptom:**

```text
FrameMutationError: frame is immutable; call .to_pandas() to operate on a copy
```

**Action:** materialize a defensive pandas copy before mutation or arithmetic.

```python
df = frame.to_pandas()
df["adjusted"] = df["value"] * 1.1
```

Keep the original `MetricFrame`, `DeltaFrame`, or `AttributionFrame` unchanged
when feeding Marivo intents.

## PromotionFailedError

**Cause:** A pandas/Ibis scratch result is missing the typed metadata needed to
become a canonical frame.

**Action:** Pass explicit typed refs and column names. For a metric frame, include
`metric=mv.MetricRef("sales.revenue")`, `semantic_kind="segmented"`,
`measure_column="value"`, `axes={"country": mv.DimensionRef("country")}`, and
`semantic_model="sales"`. For delta and attribution promotion, include source
artifact refs such as `mv.ArtifactRef("frame_delta")`.

## No active session

**Symptom:**

```text
NoActiveSessionError: no active session and none set via attach()
```

**Action:** ``mv.session.current()`` returns the active ``Session`` (with all
analysis methods) or ``None``. Check and continue work in one call:

```python
session = mv.session.current()
if session is None:
    session = mv.session.get_or_create(name="my_investigation")
# session now has .observe(), .compare(), etc.
```

Once a session exists, use `session.recent_jobs(limit=5)` for quick context
checks.

## Cross-session frame or missing state

**Symptom:** a `CrossSessionFrameError`, missing artifacts after splitting a
script, empty session knowledge, or followups that disappeared between runs.

**Action:** return to the original task session instead of creating another one.
Use one stable session name for the entire investigation:

```python
session = mv.session.get_or_create(name="my_investigation")
```

When a script is split after reading `summary()` or `next_intents`, the next
script must use the same session. Create a new session only for a genuinely
independent investigation, or when the old session is polluted and the restart
reason will be reported to the user.

## No backend factory

**Symptom:**

```text
NoBackendFactoryError: session has no backend_factory; data-materializing intents need one
```

**Action:** in a real project, register or repair the project datasource, then
create or attach the session without an explicit factory:

```python
session = mv.session.get_or_create(name="analysis")
```

Only tests, CI, or deterministic override scripts should pass explicit
`backends={...}` or `backend_factory=...` before calling `observe`, `compare`,
`decompose`, `discover`, or `correlate`.

```python
import os

import ibis
import marivo.analysis as mv

session = mv.session.get_or_create(
    name="analysis",
    backends={
        "warehouse": lambda: ibis.trino.connect(
            host="<trino_host>",
            port=80,
            user=os.environ["TRINO_USER"],
            database="<catalog>",
            source="<source>",
            client_tags=["standby", "routing_group=bsk_wide"],
        )
    },
    use_datasources=False,
)
```

For Trino JSON from a prompt, convert `catalog` to `database` and
`client-tags` to `client_tags=[...]`. Full mapping:
`references/backend-setup.md`.

## Unknown or guessed metric id

**Symptom:**

```text
MetricNotFoundError: metric 'revenue' is not '<model>.<metric>'
```

or:

```text
MetricNotFoundError: metric 'sales.revenu' not found
```

**Action:** load the semantic project and confirm exact ids.

```python
import marivo.semantic as ms

project = ms.find_project()
assert project is not None
project.load()
project.list_metrics()
cur = session.observe(mv.MetricRef("sales.revenue"), timescope={"start": "2026-07-01", "end": "2026-09-30"})
```

Metric ids are case-sensitive strings in `<model>.<metric>` form.

## Window and slice shape drift

**Symptom:**

```text
WindowInvalidError: ...
```

or a slice validation error while applying filters.

**Action:** use the current explicit shapes.

```python
session.observe(
    mv.MetricRef("sales.revenue"),
    timescope={"start": "2026-07-01", "end": "2026-09-30"},
)

session.observe(
    mv.MetricRef("sales.revenue"),
    where={"created_at": {"op": "between", "value": ["2026-07-01", "2026-09-30"]}},
)
```

Do not default to natural-language periods or bare quarter strings in new skill
content. Wrap metric ids with `mv.MetricRef(...)`.

## Discover returns CandidateSet

**Symptom:** code calls the removed `mv.detect(...)` helper or expects anomaly
results to be an `AttributionFrame`.

**Action:** use `session.discover.point_anomalies(...)` and treat the
result as a `CandidateSet`.

```python
candidates = session.discover.point_anomalies(series, threshold=1.0)
assert candidates.meta.objective == "point_anomalies"
```

## Correlate returns AssociationResult

**Symptom:** code passes the removed `align=` argument or checks for
`AttributionFrame` / `CorrelationFrame` after `session.correlate`.

**Action:** pass an `AlignmentPolicy` and treat correlation as an
`AssociationResult`.

```python
correlation = session.correlate(
    a,
    b,
    alignment=mv.AlignmentPolicy(kind="window_bucket"),
    lag_policy=mv.LagPolicy(mode="single", offset=0),
)
assert correlation.meta.kind == "association_result"
```

Use `.summary()` for compact inspection, `.preview(limit=...)` for bounded row
inspection, and `.to_pandas()` for downstream pandas work.

## Decompose without an explicit axis

**Symptom:**

```text
TypeError: decompose() missing 1 required keyword-only argument: 'axis'
```

**Action:** pass `axis=mv.DimensionRef("<column>")` for a grouping column that
already exists in the `DeltaFrame`.

```python
delta = session.compare(cur, base, alignment=mv.AlignmentPolicy(kind="window_bucket"))
attribution = session.decompose(delta, axis=mv.DimensionRef("bucket_start"))
```

## test / forecast / assess_quality

- `session.hypothesis_test(cur, base)` v1 supports only `hypothesis="mean_changed"` and paired MetricFrames with matching `semantic_kind` and `semantic_model`; scalar MetricFrames are rejected because they do not contain paired samples.
- `SamplingPolicy.pairing` must match the frame shape: use `window_bucket` for `time_series` / `panel`, and `segment_key` for `segmented`.
- `session.forecast(history, horizon=7)` v1 supports only `MetricFrame(time_series|panel)` with continuous time buckets and no NaN value rows; impute or re-observe before forecasting.
- `seasonal_naive` needs at least `seasonality_period + 1` training rows for a time series.
- `session.assess_quality(frame)` v1 accepts only `MetricFrame`; reports for delta, candidate, forecast, and attribution frames are planned for later.

## `select(attribute=...)` shape mismatch

`candidates.select(attribute="axis")` only works on `CandidateSet[driver_axis]`.
Calling it on a `point_anomaly` set raises `SemanticKindMismatchError` with
`details["shape"]` and `details["field"]`. Inspect `candidates.meta.shape` first
or call `candidates.as_<shape>()` to assert.

## `discover.driver_axes(...)` requires `search_space`

`driver_axes` is the only objective that needs
`search_space=[DimensionRef(...), ...]`. Without it, `session.discover.driver_axes`
raises
`SemanticKindMismatchError` with `details["missing"] = "search_space"`.

## `select(rank=...)` out of range

`rank` is 1-indexed. If a candidate set has fewer rows than the requested rank,
`CandidateSet.select` raises `SemanticKindMismatchError` with `details["row_count"]` and
`details["requested_rank"]`. Check `candidates.meta.row_count` before calling
`select(rank=N)` with N > 1.

## Cross-dataset observe planning errors

**Symptom:** `session.observe(...)` raises a structured planning error with a
`repair` block when a base metric spans multiple datasets but the planner
cannot find a unique join path.

**Action:** read `schema_version`, `code`, `candidates`, and `repair` on the
error. The repair payload tells you exactly which relationship, root, or
dimension is missing or ambiguous. Do not pass join policy or route arguments
to `observe`; fix the semantic declaration instead. Common fixes:

- Add `additivity="additive"` (or another explicit additivity) on the base
  metric.
- Add `root_dataset=<dataset>` on a multi-dataset base metric.
- Add a unique `ms.relationship(...)` between the joined datasets, or remove
  ambiguous duplicates.
- Use a dimension or filter field that the planner can reach from the root
  through a many-to-one or one-to-one relationship.

## Derived observe comparability errors

**Symptom:** `session.observe(...)` on a derived metric (ratio, weighted-average)
raises a structured planning error with one of the component comparability codes.

**Action:** read the `code` field and apply the matching fix.

- `component-axis-unreachable`: a parent dimension is reachable from one
  component but not another. Make every component reach the dimension or
  drop it from the `dimensions=` list.
- `component-axis-field-mismatch`: components resolve the same dimension
  to different semantic field ids. Conform the dimension on a single field
  shared by all components.
- `component-filter-unreachable`: a parent `where` filter is reachable from
  one component but not another. Make every component reach the field or
  drop the filter.
- `component-filter-field-mismatch`: components resolve the same filter
  key to different semantic field ids.
- `component-version-mismatch`: a versioned dataset has different mode,
  anchor, partition, or mapping digest across components. Make every
  component pin the same version by ensuring they share the same root time
  field and timescope.
- `snapshot-partition-missing`: at least one root anchor has no `p <=
  anchor` partition. Either widen `timescope` so available partitions
  cover all anchors, or backfill missing partitions.
- `nested-derived-unsupported`: a derived component is itself derived.
  Replace it with its base components directly.

## Unsafe fan-out: re-root vs `aggregate_then_join`

When `observe(...)` raises `unsafe-fanout`, the repair payload lists two
modeling decisions:

1. `set_metric_root` (preferred when the substantive measure lives on the
   many side — re-rooting makes the metric definition match its measure space).
2. `set_fanout_policy="aggregate_then_join"` (preferred when the metric must
   stay rooted on the one side and the merge grain has clear business
   meaning — e.g. "GMV per item category").

`aggregate_then_join` does not fix `non_additive` metrics (distinct counts,
ratios, averages). For those, change the root, remodel the dataset key, or
introduce a derived component.
