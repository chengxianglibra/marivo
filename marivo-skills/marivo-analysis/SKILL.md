---
name: marivo-analysis
description: Use when the task involves Marivo analysis — observe, compare, decompose, discover, correlate, test, forecast, quality assessment, or evidence-aware investigation over a Marivo semantic model.
---

# marivo-analysis

Use this skill when writing or running Python code against `marivo.analysis`
(imported as `mv`).

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
   `ms.find_project()` -> `project.load()` -> `mv.session.get_or_create(...)`
   with `default_calendar`.
2. For a specific intent pattern, adapt the closest runnable
   `references/examples/NN_*.py`; those examples use a tiny fixture so they
   can run in CI.
3. Confirm metric ids: `import marivo.semantic as ms; project = ms.find_project(); assert project is not None; project.load(); project.list_metrics()`.
4. Use `mv.help("discover")` / `mv.help("select")` / `mv.help("transform")` /
   `mv.help("alignment")` / `mv.help("calendar")` for constraint matrices and
   project-local calendar JSON shape at runtime.
5. On errors, read the structured output — it includes a fix snippet and the
   available ids when applicable.

## 30-second overview

```python
import marivo.analysis as mv

mv.session.get_or_create(name="investigation")

session.observe(mv.MetricRef("model.metric"), window={"start": "...", "end": "..."})  # -> MetricFrame
session.compare(cur, base, alignment=mv.AlignmentPolicy(kind="window_bucket"))      # -> DeltaFrame
session.decompose(delta, axis=mv.DimensionRef("bucket_start"))                        # -> AttributionFrame
session.discover.point_anomalies(series, threshold=1.0)                               # -> CandidateSet
session.correlate(a, b, alignment=mv.AlignmentPolicy(kind="window_bucket"))         # -> AssociationResult
session.hypothesis_test(cur, base)                                                    # -> HypothesisTestResult
session.forecast(series, horizon=7)                                                   # -> ForecastFrame
session.assess_quality(series)                                                        # -> QualityReport

mv.session.current()     # safe probe, returns None when no active session
mv.help("discover")      # prints typed objective helpers and compatibility dispatcher
print(frame.summary())   # cheap next-step summary; repr shows next_intents
```

Every intent returns a typed, immutable frame. Stay in frame world until you
call `frame.to_pandas()`. Prefer `frame.summary()` before printing full data.

## Derived ratio and weighted-average components

Derived ratio and weighted-average observations keep parent frames clean:
`frame.to_pandas()` shows only axis columns plus the final metric value. Use
`frame.components()` when you need numerator/denominator or numerator/weight
state.

```python
rate = session.observe(mv.MetricRef("sales.failure_rate"))
components = rate.components()
print(components.summary())
```

When two compatible component-aware metric frames are compared, the returned
DeltaFrame also supports `delta.components()`. For segmented, time-series, or
panel ratio/weighted-average deltas, `session.decompose(delta, axis=...)` emits
value and mix effects with method `ratio_mix` or `weighted_mix`. Time-series
deltas decompose by `bucket_start`; panel deltas decompose by the requested
dimension within each bucket.

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

Use Surface 3 audit calls only when you need raw evidence objects:
`session.findings(...)`, `session.propositions(...)`,
`session.assessments(...)`, and `session.evidence.trace(...)`.

`result.meta.quality` is a lightweight summary attached automatically.
`session.assess_quality(result)` is an explicit auditable operator that creates a
`QualityReport` and participates in lineage.

## Decision tree

```text
Value of a metric in one window?           -> observe
Current vs baseline change?                -> observe x2 -> compare
Why the change happened?                   -> compare -> decompose
Spikes, drops, unusual buckets?            -> observe series -> discover.<objective>
Two metrics move together?                 -> observe both -> correlate
Need auditable quality evidence?           -> assess_quality
Reshape without changing frame family?     -> transform.<op> (topk, rollup, slice, ...)
Raw pandas?                                -> frame.to_pandas()
```

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
mv.session.get_or_create(name="my_analysis")          # idempotent entry point
mv.session.get_or_create(name="x", backend_factory=f) # with live backend
mv.session.current()                                   # None-safe probe
mv.session.list()                                      # list sessions
session.recent_jobs(limit=5)                           # recent job history
```

## Minimal templates

### Observe + compare + decompose

```python
import marivo.analysis as mv

cur = session.observe(mv.MetricRef("<metric_id>"), window={"start": "2026-07-01", "end": "2026-09-30", "grain": "month"})
base = session.observe(mv.MetricRef("<metric_id>"), window={"start": "2025-07-01", "end": "2025-09-30", "grain": "month"})
delta = session.compare(cur, base, alignment=mv.AlignmentPolicy(kind="window_bucket"))
attribution = session.decompose(delta, axis=mv.DimensionRef("bucket_start"))
print(attribution.summary())
```

### Discover + select

```python
series = session.observe(mv.MetricRef("<metric_id>"), window={"start": "2026-07-01", "end": "2026-09-30", "grain": "day"})
candidates = session.discover.point_anomalies(series, threshold=1.0)
window = candidates.select(rank=1, attribute="window")
```

### Correlate

```python
a = session.observe(mv.MetricRef("<metric_a>"), window={"start": "2026-07-01", "end": "2026-09-30"})
b = session.observe(mv.MetricRef("<metric_b>"), window={"start": "2026-07-01", "end": "2026-09-30"})
result = session.correlate(a, b, alignment=mv.AlignmentPolicy(kind="window_bucket"))
print(result.summary())
```

### Escape hatch

```python
scratch = session.from_pandas(df)
promoted = session.promote_metric_frame(scratch, metric=mv.MetricRef("sales.revenue"),
                                   semantic_kind="segmented", measure_column="value",
                                   axes={"country": mv.DimensionRef("country")},
                                   semantic_model="sales")
```

## Standard workflow

1. `.venv/bin/python -c 'import marivo.analysis as mv; mv.help()'` — verify install.
2. Confirm metric ids from the semantic layer.
3. Start or attach the task session with
   `mv.session.get_or_create(name="<stable_task_name>")`.
4. Adapt the nearest `references/examples/NN_*.py` file.
5. Run every follow-up script with the same session; on errors, read the
   structured output and apply the fix.
6. Use `frame.summary()` / `frame.head(n)` before materializing full data.

## When to split scripts

Bundle a chain into one script when the path is fixed. Stop and run a new
script when the next intent depends on values you have not seen yet. A split is
only a script boundary; it is not a session boundary. Reuse the same session so
artifacts, knowledge, facts, followups, and job history remain available.

- **Bundle** (one script, end with `print(frame.summary())`):
  observe → compare → decompose with a pre-chosen axis; observe → forecast;
  observe → assess_quality. The shape and the next call are decided before
  you run.
- **Split** (run, read, then write the next script):
  - `discover` → which candidate to `select` and drill into.
  - `correlate` → which of several associations is worth follow-up.
  - `decompose` → which segment from the ranking to observe at finer grain.
  - Any branch where `frame.summary()` or `next_intents` is the input to your
    decision.

Rule of thumb: if you cannot write the next `mv.*` call without first reading
the printed `summary()`, that is a split point. Do not pre-write speculative
downstream steps "in case" — they waste compute and obscure the judgment. After
the split, continue with the original task session instead of starting a fresh
one.

## Walkthrough

```python
import marivo.analysis as ap

session = ap.session.get_or_create(name="sales_weekly_revenue")

current = session.observe(
    metric=ap.MetricRef("sales.revenue"),
    window={"start": "2026-05-01", "end": "2026-05-07", "grain": "day"},
    dimensions=[ap.DimensionRef("region")],
)
baseline = session.observe(
    metric=ap.MetricRef("sales.revenue"),
    window={"start": "2026-04-24", "end": "2026-04-30", "grain": "day"},
    dimensions=[ap.DimensionRef("region")],
)
delta = session.compare(current, baseline, alignment=ap.AlignmentPolicy(kind="window_bucket"))
print(delta.summary())

for issue in delta.meta.blocking_issues:
    print(issue.kind, issue.message)

for followup in delta.meta.recommended_followups:
    print(followup.category, followup.operator)
```

## Further reading

- `references/examples/*.py` — runnable templates (primary reference)
- `references/cheatsheet.md` — intent/frame/discover/transform matrices
- `references/pitfalls.md` — error recovery and common mistakes
- `references/backend-setup.md` — datasource and backend wiring
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
| `AxisNotInPanelDimensions` | `decompose(axis=...)` axis is not a segment column of the panel | `references/examples/03_decompose_attribution.py` |
| `ForecastShapeUnsupported` / `ForecastInsufficientHistory` | Bad shape, NaN values, or too little history | `references/examples/06_forecast_horizon.py` |
| `TestPolicyError` / `TestShapeNotTestable` | Unsupported hypothesis, bad alpha, or scalar frame | `references/examples/05_test_hypothesis.py` |
| `QualityShapeUnsupported` | Passed a non-MetricFrame to `assess_quality` | `references/examples/07_assess_metric_quality.py` |
| `SemanticKindMismatch` (discover missing `search_space`) | `driver_axes` objective without a `search_space` | `references/examples/08_discover_driver_axes.py` |
| `TransformOpUnsupported` / `TransformArgError` | Unknown op or missing op-specific kwargs | `references/examples/transform_slice.py`, `transform_rollup_panel.py` |
| `WindowInvalid` | Malformed window dict or unsupported relative spec | `references/examples/window_relative.py` |
| `NoBackendFactory` | Session attached without a backend factory | `references/backend-setup.md` |
| `FrameMutation` | Tried to mutate a frame in place | `references/pitfalls.md` (Mutating a frame directly) |
