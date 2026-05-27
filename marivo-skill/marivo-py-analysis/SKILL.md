---
name: marivo-py-analysis
description: Use when the task involves Marivo analysis — observe, compare, decompose, discover, correlate, test, forecast, quality assessment, or evidence-aware investigation over a Marivo semantic model.
---

# marivo-py-analysis

Use this skill when writing or running Python code against `marivo.analysis_py`
(imported as `mv`). Assume `marivo` is installed in the active environment.

Do not use this skill for MCP investigation workflows (use `marivo-analysis`)
or for authoring semantic models (use `marivo-py-semantic`).

## How to start

1. Find the closest runnable example in `references/examples/NN_*.py` and adapt.
2. Confirm metric ids: `import marivo.semantic_py as ms; ms.list_metrics()`.
3. Use `mv.help("discover")` / `mv.help("select")` / `mv.help("transform")` /
   `mv.help("alignment")` for constraint matrices at runtime.
4. On errors, read the structured output — it includes a fix snippet and the
   available ids when applicable.

## 30-second overview

```python
import marivo.analysis_py as mv

mv.session.get_or_create(name="investigation")

mv.observe(mv.MetricRef("model.metric"), window={"start": "...", "end": "..."})  # -> MetricFrame
mv.compare(cur, base, alignment=mv.AlignmentPolicy(kind="calendar_bucket"))      # -> DeltaFrame
mv.decompose(delta, axis=mv.DimensionRef("bucket_start"))                        # -> AttributionFrame
mv.discover(series, objective="point_anomalies", threshold=1.0)                  # -> CandidateSet
mv.correlate(a, b, alignment=mv.AlignmentPolicy(kind="calendar_bucket"))         # -> AssociationResult
mv.test(cur, base)                                                               # -> HypothesisTestResult
mv.forecast(series, horizon=7)                                                   # -> ForecastFrame
mv.assess_quality(series)                                                        # -> QualityReport

mv.session.current()     # safe probe, returns None when no active session
mv.help("discover")      # prints objective x source x required-kwargs matrix
print(frame.summary())   # cheap next-step summary; repr shows next_intents
```

Every intent returns a typed, immutable frame. Stay in frame world until you
call `frame.to_pandas()`. Prefer `frame.summary()` before printing full data.

## Evidence surfaces

Every result exposes flat evidence fields directly:

```python
result.artifact_id
result.subject
result.evidence_status         # "complete" | "partial" | "unavailable"
result.blocking_issues
result.recommended_followups   # C1 dag continuation + C2 quality remediation
result.confidence_scope
result.quality                 # lightweight summary, not assess_quality output
```

There is no `result.evidence.*` wrapper. Read these fields after each step to
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

`result.quality` is a lightweight summary attached automatically.
`mv.assess_quality(result)` is an explicit auditable operator that creates a
`QualityReport` and participates in lineage.

## Decision tree

```text
Value of a metric in one window?           -> observe
Current vs baseline change?                -> observe x2 -> compare
Why the change happened?                   -> compare -> decompose
Spikes, drops, unusual buckets?            -> observe series -> discover
Two metrics move together?                 -> observe both -> correlate
Need auditable quality evidence?           -> assess_quality
Reshape without changing frame family?     -> transform (topk, rollup, slice, ...)
Raw pandas?                                -> frame.to_pandas()
```

## Session

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
import marivo.analysis_py as mv

cur = mv.observe(mv.MetricRef("<metric_id>"), window={"start": "2026-07-01", "end": "2026-09-30", "grain": "month"})
base = mv.observe(mv.MetricRef("<metric_id>"), window={"start": "2025-07-01", "end": "2025-09-30", "grain": "month"})
delta = mv.compare(cur, base, alignment=mv.AlignmentPolicy(kind="calendar_bucket"))
attribution = mv.decompose(delta, axis=mv.DimensionRef("bucket_start"))
print(attribution.summary())
```

### Discover + select

```python
series = mv.observe(mv.MetricRef("<metric_id>"), window={"start": "2026-07-01", "end": "2026-09-30", "grain": "day"})
candidates = mv.discover(series, objective="point_anomalies", threshold=1.0)
window = mv.select(candidates, rank=1, field="window")
```

### Correlate

```python
a = mv.observe(mv.MetricRef("<metric_a>"), window={"start": "2026-07-01", "end": "2026-09-30"})
b = mv.observe(mv.MetricRef("<metric_b>"), window={"start": "2026-07-01", "end": "2026-09-30"})
result = mv.correlate(a, b, alignment=mv.AlignmentPolicy(kind="calendar_bucket"))
print(result.summary())
```

### Escape hatch

```python
scratch = mv.from_pandas(df, session=session)
promoted = mv.promote_metric_frame(scratch, metric=mv.MetricRef("sales.revenue"),
                                   semantic_kind="segmented", measure_column="value",
                                   axes={"country": mv.DimensionRef("country")},
                                   semantic_model="sales")
```

## Standard workflow

1. `python -c 'import marivo.analysis_py as mv; mv.help()'` — verify install.
2. Confirm metric ids from the semantic layer.
3. Adapt the nearest `references/examples/NN_*.py` file.
4. Run the script; on errors, read the structured output and apply the fix.
5. Use `frame.summary()` / `frame.head(n)` before materializing full data.

## Further reading

- `references/examples/*.py` — runnable templates (primary reference)
- `references/cheatsheet.md` — intent/frame/discover/transform matrices
- `references/pitfalls.md` — error recovery and common mistakes
- `references/backend-setup.md` — datasource and backend wiring
- `../marivo-py-semantic/references/datasource.md` — datasource definition

## Error → example reference

When an intent raises one of the structured errors below, open the listed
example to see the correct pattern.

| Error kind | What it means | See |
|---|---|---|
| `MetricNotFound` | Unknown metric id, missing `<model>.<metric>`, or semantic project not loaded | `references/examples/01_observe_single_window.py` |
| `SemanticKindMismatch` (compare) | Passed a `DeltaFrame` where a `MetricFrame` was expected | `references/examples/99_pitfall_pass_delta_to_compare.py` |
| `SegmentDimensionMismatch` | `compare` got two `segmented` frames with different segment columns | `references/examples/compare_segmented.py` |
| `PanelGrainMismatch` | `compare` got two `panel` frames with different time grain | `references/examples/compare_panel.py` |
| `AlignmentPolicyNotApplicable` | Alignment kind not allowed for the frame's semantic kind | `references/examples/compare_segmented.py` |
| `CrossSessionFrame` | A frame was produced in another session | `references/examples/session_timezone.py` |
| `AxisNotInPanelDimensions` | `decompose(axis=...)` axis is not a segment column of the panel | `references/examples/03_decompose_attribution.py` |
| `ForecastShapeUnsupported` / `ForecastInsufficientHistory` | Bad shape, NaN values, or too little history | `references/examples/06_forecast_horizon.py` |
| `TestPolicyError` / `TestShapeNotTestable` | Unsupported hypothesis, bad alpha, or scalar frame | `references/examples/05_test_hypothesis.py` |
| `QualityShapeUnsupported` | Passed a non-MetricFrame to `assess_quality` | `references/examples/07_assess_metric_quality.py` |
| `SemanticKindMismatch` (discover missing `search_space`) | `driver_axes` objective without a `search_space` | `references/examples/08_discover_driver_axes.py` |
| `TransformOpUnsupported` / `TransformArgError` | Unknown op or missing op-specific kwargs | `references/examples/transform_slice.py`, `transform_rollup_panel.py` |
| `WindowInvalid` | Malformed window dict or unsupported relative spec | `references/examples/window_relative.py` |
| `NoBackendFactory` | Session attached without a backend factory | `references/backend-setup.md` |
| `FrameMutation` | Tried to mutate a frame in place | `references/pitfalls.md` (Mutating a frame directly) |
