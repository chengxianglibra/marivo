---
name: marivo-analysis
description: Use for any Marivo metric-centered analysis task: observe, compare, decompose, discover, correlate, test, forecast, quality assessment, evidence-aware investigation, or continuing an analysis session over semantic metrics.
---

# marivo-analysis

Use this skill when writing or running metric-centered workflows with
`marivo.analysis` (imported as `mv`).

The `mv` top level is the core agent surface: constructor refs and policies,
sessions, frames, frame metadata, lineage, and namespace entrypoints. Use
submodules for domain DTOs and errors: `mv.evidence.*`,
and `mv.errors.*`.

Use `marivo-semantic` instead when the task is authoring semantic models.

## Python Environment

Do not use bare `python`, `python3`, `pip`, or `pip3` commands.
In this Marivo source checkout, use these exact entrypoints:

```bash
.venv/bin/python
.venv/bin/pip
```

If this skill is copied into another project, first identify that project's
virtualenv path, then use `<venv>/bin/python` and `<venv>/bin/pip`
consistently for every install, check, and script run.

## How to start

1. For a real project, start from
   `references/examples/00_real_project_template.py`; it shows
   `ms.load()` -> `mv.session.get_or_create(...)`
   with `default_calendar`.
2. For a specific intent pattern, adapt the closest runnable
   `references/examples/NN_*.py`; those examples use a tiny fixture so they
   can run in CI.
3. Confirm metric ids: `import marivo.semantic as ms; catalog = ms.load(); catalog.list(kind="metric").show()`.
4. Use runtime help as the authoritative per-object contract. For the intent,
   frame, policy, or topic you are about to use, inspect
   `mv.help('<name>')`; examples:
   `mv.help('observe')`, `mv.help('discover')`,
   `mv.help('alignment')`, and
   `mv.help('MetricFrame')`. The descriptor exposes `signature`,
   `doc`, bounded `constraints`, runnable `examples`, `methods`,
   `next_intents`, and drill-down ids. Consult it per object when the contract
   matters; do not turn help into a blanket ritual for each call.
5. On errors, read the structured output — it includes a fix snippet and the
   available ids when applicable.

## 30-second overview

```python
import marivo.analysis as mv

mv.session.get_or_create(name="investigation")

session.observe(mv.MetricRef("model.metric"), timescope={"start": "...", "end": "..."})  # -> MetricFrame  (end is exclusive: [start, end))
session.compare(cur, base, alignment=mv.AlignmentPolicy(kind="window_bucket"))      # -> DeltaFrame
session.decompose(delta, axis=mv.DimensionRef("bucket_start"))                        # -> AttributionFrame
session.discover.point_anomalies(series, threshold=1.0)                               # -> CandidateSet
session.correlate(a, b, alignment=mv.AlignmentPolicy(kind="window_bucket"))         # -> AssociationResult
session.hypothesis_test(cur, base)                                                    # -> HypothesisTestResult
session.forecast(series, horizon=7)                                                   # -> ForecastFrame
session.assess_quality(series)                                                        # -> QualityReport

mv.session.current()     # safe probe — returns Session or None; check and continue work
mv.help("discover")      # bounded typed objective helpers and compatibility dispatcher
frame.show()             # bounded result card; repr hints to .show()
```

Every intent returns a typed, immutable frame. Stay in frame world until you
call `frame.to_pandas()`. Use `frame.show()` for bounded inspection.

`AlignmentPolicy(kind="window_bucket")` compares time-series and panel windows
by ordinal bucket position by default. Use
`AlignmentPolicy(kind="window_bucket", mode="calendar_bucket")` only when the
same absolute bucket key should be treated as the same row. Use
`strict_lengths=True` only when unequal window bucket counts must fail.

## When to call show()

Call `show()` at deliberate observation points — not after every API call.
Multi-step scripts are quiet until you explicitly inspect:

```python
session = mv.session.get_or_create(name="revenue_drop")
cur = session.observe(mv.MetricRef("sales.revenue"), timescope="last_7d")
base = session.observe(mv.MetricRef("sales.revenue"), timescope="previous_7d")
delta = session.compare(cur, base)
delta.show()            # deliberate inspection point
```

Bounded `show()` output is a working observation, not the final user answer.
Final reports must still be answer-first and source-backed.

## Derived ratio and weighted-average components

Derived ratio and weighted-average observations keep parent frames clean:
`frame.to_pandas()` shows only axis columns plus the final metric value. Use
`frame.components()` when you need the underlying component metric values
(e.g., `failed_count`, `total_count` for a ratio).

```python
rate = session.observe(mv.MetricRef("sales.failure_rate"))
components = rate.components()
components.show()
```

When two compatible component-aware metric frames are compared, the returned
DeltaFrame also supports `delta.components()`. For segmented, time-series, or
panel ratio/weighted-average deltas, `session.decompose(delta, axis=...)` emits
value and mix effects with method `ratio_mix` or `weighted_mix`. Time-series
deltas decompose by `bucket_start`; panel deltas decompose by the requested
dimension within each bucket. Ordinal window-bucket deltas include
`bucket_start_b` for the baseline bucket paired to each current bucket.

## Evidence surfaces

Every result exposes evidence fields on `frame.meta`:

```python
result.meta.artifact_id
result.meta.evidence_status         # "complete" | "partial" | "unavailable"
result.meta.blocking_issues
result.meta.recommended_followups   # C1 dag continuation + C2 quality remediation
result.meta.confidence_scope
result.meta.quality                 # lightweight summary, not assess_quality output
```

There is no `result.evidence.*` wrapper. Read `result.meta` after each step to
decide whether to continue, remediate quality, or inspect session knowledge.

Use session knowledge when you need cross-step reasoning or recovery:

```python
session = mv.session.get_or_create(name="investigation")
knowledge = session.knowledge()
knowledge.facts(kind="change")
knowledge.facts(kind="driver")
knowledge.open_items(kind="anomaly")
knowledge.next_steps(top=5)
knowledge.blocked_followups()
```

Use Surface 3 audit calls only when you need raw evidence objects, all under
the evidence namespace: `session.evidence.findings(...)`,
`session.evidence.propositions(...)`, `session.evidence.assessments(...)`, and
`session.evidence.trace(...)`.

`result.meta.quality` is a lightweight summary attached automatically.
`session.assess_quality(result)` is an explicit auditable operator that creates a
`QualityReport` and participates in lineage.

## Final analysis report

For any non-trivial close-out, read `references/final-report.md` before the
final user response. Do not end with only `frame.show()`, `frame.head(n)`, or
raw tables. Synthesize the answer, scope, evidence, caveats, source details, and
recommended next steps into a clear Markdown report.

## Decision tree

```text
Value of a metric in one window?           -> observe
Current vs baseline change?                -> observe x2 -> compare
Why the change happened?                   -> compare -> decompose
Spikes, drops, unusual buckets?            -> observe series -> discover.<objective>
Two metrics move together?                 -> observe both -> correlate
Need auditable quality evidence?           -> assess_quality
Reshape without changing frame family?     -> transform.<op> (topk, rollup, slice, ...)
Custom joins, feature engineering, or raw table exploration?
                                           -> session.explore_ibis(...) or pandas scratch
Raw pandas from a frame?                   -> frame.to_pandas()
```

Prefer built-in intents first. When Marivo does not directly support an
analysis step, use session-scoped scratch work: `session.explore_ibis(...)` for
clean raw Ibis queries against a registered backend, or `frame.to_pandas()` plus
`session.from_pandas(...)` for pandas/other Python analysis. Scratch outputs are
`ExplorationResult`; keep them terminal unless they must feed typed intents, then
promote explicitly with `session.promote_metric_frame(...)` or related helpers.

## Session

Default to one session per analysis task. Start the first script with
`mv.session.get_or_create(name="<stable_task_name>")`, then reuse the same
stable name or explicitly attach/current the same session in every follow-up
script. Do not create new sessions for script splits, retries, or branch
exploration: artifacts, knowledge, facts, followups, and job history are
session-scoped.

Create a new session only when the user explicitly starts an independent
investigation, or when the existing session is polluted enough that restarting
is the correct recovery. State that reason in the final output.

```python
mv.session.get_or_create(name="my_analysis")  # idempotent entry point; auto-loads project datasources
mv.session.current()                          # None-safe probe; returns Session, not SessionSummary
mv.session.list()                             # list sessions
session.recent_jobs(limit=5)                  # recent job history
```

Do not construct `backend_factory` for normal project analysis. Use it only as
an explicit test/CI or runtime override; its signature is
`backend_factory(datasource_name: str) -> ibis backend`.

## Minimal templates

### Observe + compare + decompose

```python
import marivo.analysis as mv

cur = session.observe(mv.MetricRef("<metric_id>"), timescope={"start": "2026-07-01", "end": "2026-10-01"}, grain="month")
base = session.observe(mv.MetricRef("<metric_id>"), timescope={"start": "2025-07-01", "end": "2025-10-01"}, grain="month")
delta = session.compare(cur, base, alignment=mv.AlignmentPolicy(kind="window_bucket"))
attribution = session.decompose(delta, axis=mv.DimensionRef("bucket_start"))
attribution.show()
```

### Discover + select

```python
series = session.observe(mv.MetricRef("<metric_id>"), timescope={"start": "2026-07-01", "end": "2026-10-01"}, grain="day")
candidates = session.discover.point_anomalies(series, threshold=1.0)
window = candidates.select(rank=1, attribute="window")
```

### Correlate

```python
a = session.observe(mv.MetricRef("<metric_a>"), timescope={"start": "2026-07-01", "end": "2026-09-30"})
b = session.observe(mv.MetricRef("<metric_b>"), timescope={"start": "2026-07-01", "end": "2026-09-30"})
result = session.correlate(a, b, alignment=mv.AlignmentPolicy(kind="window_bucket"))
result.show()
```

### Escape hatch

```python
scratch = session.explore_ibis(lambda con: con.table("orders"), datasource="warehouse")
df = frame.to_pandas()
scratch = session.from_pandas(df)
promoted = session.promote_metric_frame(scratch, metric=mv.MetricRef("sales.revenue"),
                                   semantic_kind="segmented", measure_column="value",
                                   axes={"country": mv.DimensionRef("country")},
                                   semantic_model="sales")
```

## Cross-dataset observe

For cross-dataset base metrics, use the normal `session.observe(...)` surface.
Do not pass join policy or route arguments. If planning fails, read the
structured repair error (`schema_version`, `code`, `candidates`, `repair`).

For derived metrics (ratio, weighted-average), each component is planned
independently. Derived dispatch enforces comparability across components.
If a derived observe fails, the repair code identifies which component and
which check failed:

- `component-axis-unreachable`: a parent dimension is reachable from one
  component but not another. Make every component reach the dimension or
  drop it.
- `component-axis-field-mismatch`: components resolve the same dimension
  to different semantic field ids. Conform the dimension on a single field.
- `component-filter-unreachable`: a parent `where` filter is reachable from
  one component but not another. Make every component reach the field or
  drop the filter.
- `component-filter-field-mismatch`: components resolve the same filter
  key to different semantic field ids.
- `component-version-mismatch`: a versioned dataset has different mode,
  anchor, partition, or mapping digest across components. Make every
  component pin the same version.
- `snapshot-partition-missing`: at least one root anchor has no `p <=
  anchor` partition. Either widen `timescope` so available partitions
  cover all anchors, or backfill missing partitions.
- `nested-derived-unsupported`: a derived component is itself derived.
  Replace it with its base components.

## Standard workflow

1. `.venv/bin/python -c 'import marivo.analysis as mv; mv.help()'` — verify install.
2. Confirm metric ids from the semantic layer.
3. Start or attach the task session with
   `mv.session.get_or_create(name="<stable_task_name>")`.
4. Adapt the nearest `references/examples/NN_*.py` file.
5. Run every follow-up script with the same session; on errors, read the
   structured output and apply the fix.
6. Use `frame.show()` for bounded inspection; `frame.head(n)` / `frame.to_pandas()` when you need full data.

## When to split scripts

Bundle a chain into one script when the path is fixed. Stop and run a new
script when the next intent depends on values you have not seen yet. A split is
only a script boundary; it is not a session boundary. Reuse the same session so
artifacts, knowledge, facts, followups, and job history remain available.

- **Bundle** (one script, end with `frame.show()`):
  observe → compare → decompose with a pre-chosen axis; observe → forecast;
  observe → assess_quality. The shape and the next call are decided before
  you run.
- **Split** (run, read, then write the next script):
  - `discover` → which candidate to `select` and drill into.
  - `correlate` → which of several associations is worth follow-up.
  - `decompose` → which segment from the ranking to observe at finer grain.
  - Any branch where `frame.show()` or `next_intents` is the input to your
    decision.

Rule of thumb: if you cannot write the next `mv.*` call without first reading
the `show()` output, that is a split point. Do not pre-write speculative
downstream steps "in case" — they waste compute and obscure the judgment. After
the split, continue with the original task session instead of starting a fresh
one.

In the follow-up script, recover previously produced frames from disk instead
of re-running observe:

```python
session = mv.session.get_or_create(name="my_analysis")
# Discover available frames by metric_id:
summaries = session.frame_summaries()
# Load a frame by ref — zero datasource queries:
prev = session.get_frame("<ref>")
```

## Walkthrough

```python
import marivo.analysis as ap

session = ap.session.get_or_create(name="sales_weekly_revenue")

current = session.observe(
    metric=ap.MetricRef("sales.revenue"),
    timescope={"start": "2026-05-01", "end": "2026-05-08"}, grain="day",
    dimensions=[ap.DimensionRef("region")],
)
baseline = session.observe(
    metric=ap.MetricRef("sales.revenue"),
    timescope={"start": "2026-04-24", "end": "2026-05-01"}, grain="day",
    dimensions=[ap.DimensionRef("region")],
)
delta = session.compare(current, baseline, alignment=ap.AlignmentPolicy(kind="window_bucket"))
delta.show()

for issue in delta.meta.blocking_issues:
    print(issue.kind, issue.message)

for followup in delta.meta.recommended_followups:
    print(followup.category, followup.operator)
```

## Further reading

- `references/examples/*.py` — runnable templates (primary reference)
- `references/final-report.md` — final user-facing report structure and QA
- `references/cheatsheet.md` — intent/frame/discover/transform matrices
- `references/pitfalls.md` — error recovery and common mistakes
- `references/backend-setup.md` — datasource and backend wiring
- `references/upload-html-report.md` — `marivo-upload-report` command for S3 publishing
- `../marivo-semantic/references/datasource.md` — datasource definition

## Error → example reference

When an intent raises one of the structured errors below, open the listed
example to see the correct pattern.

| Error kind | What it means | See |
|---|---|---|
| `MetricNotFound` | Unknown metric id, missing `<model>.<metric>`, or semantic project not loaded | `references/examples/01_observe_single_window.py` |
| Wrong Python environment | `marivo` import is missing because the system interpreter was used | `references/pitfalls.md` (Wrong Python environment) |
| `SemanticKindMismatch` (compare) | Passed a `DeltaFrame` where a `MetricFrame` was expected | `references/examples/99_pitfall_pass_delta_to_compare.py` |
| `SegmentDimensionMismatch` | `compare` got two `segmented` frames with different segment columns | `references/examples/compare_segmented.py` |
| `PanelGrainMismatch` | `compare` got two `panel` frames with different time grain | `references/examples/compare_panel.py` |
| `AlignmentPolicyNotApplicable` | Alignment kind not allowed for the frame's semantic kind | `references/examples/compare_segmented.py` |
| `CrossSessionFrame` | A frame was produced in another session; return to the original task session | `references/examples/session_timezone.py`, `references/pitfalls.md` |
| `FrameRefNotFound` | No persisted frame with this ref in the current session; check `session.frame_summaries()` for available refs | `references/examples/session_frame_recovery.py` |
| `AxisNotInPanelDimensions` | `decompose(axis=...)` axis is not a segment column of the panel | `references/examples/03_decompose_attribution.py` |
| `ForecastShapeUnsupported` / `ForecastInsufficientHistory` | Bad shape, NaN values, or too little history | `references/examples/06_forecast_horizon.py` |
| `TestPolicyError` / `TestShapeNotTestable` | Unsupported hypothesis, bad alpha, or scalar frame | `references/examples/05_test_hypothesis.py` |
| `QualityShapeUnsupported` | Passed a non-MetricFrame to `assess_quality` | `references/examples/07_assess_metric_quality.py` |
| `SemanticKindMismatch` (discover missing `search_space`) | `driver_axes` objective without a `search_space` | `references/examples/08_discover_driver_axes.py` |
| `TransformOpUnsupported` / `TransformArgError` | Unknown op or missing op-specific kwargs | `references/examples/transform_slice.py`, `transform_rollup_panel.py` |
| `WindowInvalid` | Malformed timescope/window dict | `references/examples/observe_timescope.py` |
| `NoBackendFactory` | No usable project datasource or explicit backend override | `references/backend-setup.md` |
| `FrameMutation` | Tried to mutate a frame in place | `references/pitfalls.md` (Mutating a frame directly) |
