---
name: marivo-py-analysis
description: Use when the task involves Marivo analysis — observe, compare, decompose, detect, correlate, or any investigation/diagnosis flow over a Marivo semantic model.
---

# marivo-py-analysis

Use this skill when writing or running Python code against an installed Marivo
Python library: `marivo.analysis_py` as `mv`. Assume the current Python
environment already has `marivo` available from PyPI or an equivalent install;
do not assume access to the Marivo repository source tree.

Do not use this skill for stdio MCP investigation workflows. Use the separate
`marivo-analysis` skill for MCP session tools. Do not use this skill to author
semantic models; use `marivo-py-semantic` or the semantic-layer skill for that
work.

## 30-second overview

```python
import marivo.analysis_py as mv

mv.observe(metric_id, window={"start": "...", "end": "..."})  # -> MetricFrame
mv.compare(cur, base, compare_type="yoy")                     # -> DeltaFrame
mv.decompose(delta)                                           # -> AttributionFrame
mv.detect(series, threshold=1.0)                              # -> AttributionFrame
mv.correlate(a, b, align="sample")                            # -> AttributionFrame

mv.session.current()      # current session summary, or None
mv.session.history()      # recent session/job history
mv.help("compare")        # signature and usage hint
print(frame.summary())    # cheap next-step summary
```

Every intent returns a typed frame. Stay in frame world until you intentionally
materialize a copy with `frame.to_pandas()`. Prefer `frame.summary()` before
printing full data into the agent context.

Important current return types:

- `mv.observe(...)` returns `MetricFrame`.
- `mv.compare(metric_frame, metric_frame, ...)` returns `DeltaFrame`.
- `mv.decompose(delta_frame, ...)` returns `AttributionFrame`.
- `mv.detect(metric_frame, ...)` returns anomaly `AttributionFrame`, not
  `CandidateSet`.
- `mv.correlate(metric_frame, metric_frame, ...)` returns correlation
  `AttributionFrame`, not `CorrelationFrame`.

## Standard workflow

1. Check that the active Python environment can import Marivo, then inspect the
   session and helper surface.

   ```bash
   python -c 'import marivo.analysis_py as mv; print(mv.session.current()); mv.help()'
   ```

2. Confirm metric ids from the semantic layer before calling `observe`. In the
   runnable examples, `references/examples/_fixtures/tiny_semantic.py` builds a
   tiny in-memory semantic model using the installed `marivo` package. Examples
   that use DuckDB require `marivo[duckdb]` or an equivalent environment.

3. Write a small script in the user's project or scratch area. Use one of the
   templates below or adapt a runnable file from `references/examples/*.py`.

4. Run the smallest script with the same Python interpreter/environment where
   `marivo` is installed.

   ```bash
   python my_analysis.py
   ```

   If the surrounding project has stricter command rules, follow those local
   rules. This skill itself does not require the Marivo repository checkout.

5. On Marivo exceptions, read the structured error text. It usually includes a
   `正确写法` block. Apply that fix, then re-run the smallest script.

6. Keep generated analysis outputs compact. Prefer `frame.summary()` and
   `frame.head(n)` before materializing full data with `frame.to_pandas()`.

## Relative Windows And Timezone

- For v1.2 relative windows, prefer explicit `window={"expr": ..., "as_of": ...}`
  and set `grain` only when you need a time series.
- No `grain` keeps `mv.observe(...)` scalar; adding `grain="day"` returns
  `semantic_kind == "time_series"`.
- Set session timezone/default calendar at session creation or attach time so
  relative windows and calendar compare resolve consistently.
- Runnable references:
  `references/examples/window_relative.py`,
  `references/examples/session_timezone.py`.

## Calendar-Aware Compare

- Calendar alignment requires two time-series `MetricFrame`s and
  `align="calendar"` on `mv.compare(...)`.
- Supply `calendar_policy`, for example
  `{"mode": "dow_aligned", "align_period": "month"}`.
- Ensure the session timezone and selected calendar timezone match.
- Runnable reference: `references/examples/compare_calendar.py`.

## Fill-in templates

### Observe one metric window

Use this when you need the value of a known metric inside a fixed time window.
Runnable reference: `references/examples/01_observe_single_window.py`.

```python
import marivo.analysis_py as mv

cur = mv.observe(
    "<metric_id>",
    window={"start": "2026-07-01", "end": "2026-09-30"},
)
print(cur.summary())
```

### Compare aligned windows

Use this when you need current, baseline, delta, and percent change for the same
metric. Runnable reference: `references/examples/02_compare_yoy.py`.

```python
import marivo.analysis_py as mv

cur = mv.observe(
    "<metric_id>",
    window={"start": "2026-07-01", "end": "2026-09-30"},
)
base = mv.observe(
    "<metric_id>",
    window={"start": "2025-07-01", "end": "2025-09-30"},
)
delta = mv.compare(cur, base, compare_type="yoy")
print(delta.summary())
```

### Decompose a delta

Use this when you have a `DeltaFrame` and need attribution. Runnable reference:
`references/examples/03_decompose_attribution.py`.

The current runnable example demonstrates v1 scalar total attribution. Pass
`by="<column>"` only when the `DeltaFrame` already contains that grouping column;
do not invent a segment such as `region` unless it exists in the delta.

```python
import marivo.analysis_py as mv

cur = mv.observe(
    "<metric_id>",
    slice={"created_at": {"op": "between", "value": ["2026-07-01", "2026-09-30"]}},
)
base = mv.observe(
    "<metric_id>",
    slice={"created_at": {"op": "between", "value": ["2025-07-01", "2025-09-30"]}},
)
delta = mv.compare(cur, base, compare_type="yoy")
attribution = mv.decompose(delta)
print(attribution.summary())
```

### Detect anomalies

Use this when you have a metric series and need anomaly flags/scores. Runnable
reference: `references/examples/04_detect_anomaly.py`.

`mv.detect` currently returns an anomaly `AttributionFrame` with
`meta.attribution_kind == "anomaly"`.

```python
import marivo.analysis_py as mv

series = mv.observe(
    "<metric_id>",
    slice={"created_at": {"op": "between", "value": ["2026-07-01", "2026-09-30"]}},
)
anomalies = mv.detect(series, threshold=1.0)
print(anomalies.summary())
```

### Correlate two metric frames

Use this when two compatible `MetricFrame`s should be tested for Pearson
correlation. `mv.correlate` currently returns a correlation `AttributionFrame`
with `meta.attribution_kind == "correlation"`.

```python
import marivo.analysis_py as mv

a = mv.observe("<metric_a>", window={"start": "2026-07-01", "end": "2026-09-30"})
b = mv.observe("<metric_b>", window={"start": "2026-07-01", "end": "2026-09-30"})
correlation = mv.correlate(a, b, align="sample")
print(correlation.summary())
```

### Session and help helpers

Use this at the top of exploratory scripts to avoid guessing SDK shape.

```python
import marivo.analysis_py as mv

print(mv.session.current())
print(mv.session.history())
mv.help("observe")
mv.help("SemanticKindMismatchError")
```

## Decision tree

```text
Need the value of a metric in one window?
  -> observe

Need current vs baseline change?
  -> observe current -> observe baseline -> compare

Need why the change happened?
  -> compare first -> decompose

Need spikes, drops, or unusual buckets?
  -> observe a metric series -> detect

Need whether two metric frames move together?
  -> observe both -> correlate

Need raw pandas operations?
  -> call frame.to_pandas() and keep derived data outside the frame contract
```

## Common pitfalls

- Passing a `DeltaFrame` back into `compare` is invalid. `mv.compare(cur, delta)`
  raises `SemanticKindMismatchError`; pass two `MetricFrame`s instead. Runnable
  reference: `references/examples/99_pitfall_pass_delta_to_compare.py`.
- Do not mutate frames directly with `frame["col"] = ...`. Frames are immutable;
  call `frame.to_pandas()` for a defensive copy.
- Do not use window strings such as `"2026Q3"` as the default pattern. Current
  examples use `window={"start": "...", "end": "..."}` or structured `slice`
  filters such as `{"created_at": {"op": "between", "value": [...]}}`.
- Do not pass `by=` to scalar decomposition. Use `by=` only for a grouping column
  already present in a non-scalar `DeltaFrame`.
- Do not assume detect/correlate have dedicated frame classes. Both return
  specialized `AttributionFrame`s today.

## Further reading

- `references/examples/*.py` - runnable templates for installed-library usage
- `references/examples/_fixtures/tiny_semantic.py` - tiny semantic model used by
  the examples; requires DuckDB support in the active Marivo environment
- `references/cheatsheet.md` - compact intent/frame reference
- `references/pitfalls.md` - expanded error recovery notes
