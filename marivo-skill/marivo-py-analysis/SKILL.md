---
name: marivo-py-analysis
description: Use when the task involves Marivo analysis — observe, compare, decompose, discover, correlate, or any investigation/diagnosis flow over a Marivo semantic model.
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

mv.observe(mv.MetricRef(metric_id), window={"start": "...", "end": "..."})  # -> MetricFrame
mv.compare(cur, base, alignment=mv.AlignmentPolicy(kind="calendar_bucket"))  # -> DeltaFrame
mv.decompose(delta, axis=mv.DimensionRef("bucket_start"))     # -> AttributionFrame
mv.discover(series, objective="point_anomalies", threshold=1.0)  # -> CandidateSet
mv.correlate(a, b, alignment=mv.AlignmentPolicy(kind="calendar_bucket"))  # -> AssociationResult

mv.session.current()      # current session summary, or None
mv.session.history()      # recent session/job history
mv.help("compare")        # signature and usage hint
print(frame.summary())    # cheap next-step summary
```

Every intent returns a typed frame. Stay in frame world until you intentionally
materialize a copy with `frame.to_pandas()`. Prefer `frame.summary()` before
printing full data into the agent context.

Use `mv.MetricRef(...)`, `mv.DimensionRef(...)`, `mv.CalendarRef(...)`,
`mv.AlignmentPolicy(...)`, and `mv.LagPolicy(...)` at public operator
boundaries. Do not pass bare strings directly to `observe`, `decompose`, or
calendar-backed `compare`.

Important current return types:

- `mv.observe(...)` returns `MetricFrame`.
- `mv.transform(metric_frame_or_delta_frame, op=...)` returns the same frame
  family. In v1, `normalize` is MetricFrame-only.
- `mv.compare(metric_frame, metric_frame, ...)` returns `DeltaFrame`.
- `mv.decompose(delta_frame, ...)` returns `AttributionFrame`.
- `mv.discover(metric_frame, objective="point_anomalies", ...)` returns
  `CandidateSet`.
- `mv.correlate(metric_frame, metric_frame, ...)` returns
  `AssociationResult`.

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

3. If live data is required, persist the connection once via
   `mv.profiles.set(name, backend_type=..., **kwargs)`; `mv.session.create`
   then resolves the backend automatically. Sensitive fields (password,
   token, api_key, ...) go via `*_env="VAR_NAME"` and are read from
   `os.environ` at backend-build time. For tests or explicit overrides, pass
   `backends=` / `backend_factory=` and set `use_profiles=False`. See
   `references/profiles.md` for the full registry contract and
   `references/backend-setup.md` for templates.

4. Write a small script in the user's project or scratch area. Use one of the
   templates below or adapt a runnable file from `references/examples/*.py`.

5. Run the smallest script with the same Python interpreter/environment where
   `marivo` is installed.

   ```bash
   python my_analysis.py
   ```

   If the surrounding project has stricter command rules, follow those local
   rules. This skill itself does not require the Marivo repository checkout.

6. On Marivo exceptions, read the structured error text. It usually includes a
   `正确写法` block. Apply that fix, then re-run the smallest script.

7. Keep generated analysis outputs compact. Prefer `frame.summary()` and
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
  `alignment=mv.AlignmentPolicy(...)` on `mv.compare(...)`.
- Supply a calendar-backed policy, for example
  `mv.AlignmentPolicy(kind="dow_aligned", calendar=mv.CalendarRef("cn_holidays"), period="month")`.
- Ensure the session timezone and selected calendar timezone match.
- Runnable reference: `references/examples/compare_calendar.py`.

### transform v1 ops

`mv.transform` is the family-preserving reshape entrypoint. v1 supports
`filter`, `slice`, `rollup`, `topk`, `bottomk`, `rank`, and `window` over
`MetricFrame` and `DeltaFrame`; `normalize` is MetricFrame-only. DeltaFrame
normalize is rejected in v1 until current/baseline/delta/pct_change invariants
can be preserved together. See `references/examples/transform_*.py` for
runnable examples. The cleaning ops (`dedupe`, `impute_nulls`, `winsorize`,
`strip_outliers`), `align_time`, and `attribution_frame` inputs are reserved
for follow-up.

## Fill-in templates

### Attach live backend (profile-backed, recommended)

Persist the connection once, then let the session resolve it. The profile
file lives at `~/.marivo/profiles/profiles.json` (override with
`$MARIVO_HOME`) and is user-scope — reused across every Marivo project on
the machine. For Trino prompt-field mapping see `references/backend-setup.md`;
for the full registry contract see `references/profiles.md`.

```python
import marivo.analysis_py as mv

mv.profiles.set(
    "warehouse",
    backend_type="trino",
    host="<trino_host>",
    port=8080,
    user="<user>",
    catalog="<catalog>",
    schema="<schema>",
    http_scheme="https",
    source="<source>",
    client_tags=["standby", "routing_group=wide"],
    password_env="WAREHOUSE_PWD",   # secret read from os.environ
)

mv.session.create(name="analysis")  # backend resolved from the profile
```

### Attach live backend (explicit override)

Use this in tests / CI or when you need full control over the backend. Pair
with `use_profiles=False` so a stray profile cannot mask a misconfigured
fixture.

```python
import ibis
import marivo.analysis_py as mv

def make_backend(datasource_name: str):
    if datasource_name not in {"warehouse", "sales.warehouse"}:
        raise KeyError(datasource_name)
    return ibis.trino.connect(
        host="<trino_host>", port=80, user="<user>",
        database="<catalog>", source="<source>",
        client_tags=["standby", "routing_group=bsk_wide"],
    )

mv.session.create(
    name="analysis",
    backend_factory=make_backend,
    use_profiles=False,
)
```

### Observe one metric window

Use this when you need the value of a known metric inside a fixed time window.
Runnable reference: `references/examples/01_observe_single_window.py`.

```python
import marivo.analysis_py as mv

cur = mv.observe(
    mv.MetricRef("<metric_id>"),
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
    mv.MetricRef("<metric_id>"),
    window={"start": "2026-07-01", "end": "2026-09-30"},
)
base = mv.observe(
    mv.MetricRef("<metric_id>"),
    window={"start": "2025-07-01", "end": "2025-09-30"},
)
delta = mv.compare(cur, base, alignment=mv.AlignmentPolicy(kind="calendar_bucket"))
print(delta.summary())
```

### Decompose a delta

Use this when you have a `DeltaFrame` and need attribution. Runnable reference:
`references/examples/03_decompose_attribution.py`.

Pass `axis=mv.DimensionRef("<column>")` for a column already present in the
`DeltaFrame`; do not invent a segment such as `region` unless it exists in the
delta.

```python
import marivo.analysis_py as mv

cur = mv.observe(
    mv.MetricRef("<metric_id>"),
    window={"start": "2026-07-01", "end": "2026-09-30", "grain": "month"},
)
base = mv.observe(
    mv.MetricRef("<metric_id>"),
    window={"start": "2025-07-01", "end": "2025-09-30", "grain": "month"},
)
delta = mv.compare(cur, base, alignment=mv.AlignmentPolicy(kind="calendar_bucket"))
attribution = mv.decompose(delta, axis=mv.DimensionRef("bucket_start"))
print(attribution.summary())
```

### Discover anomaly candidates

Use this when you have a metric series and need candidate anomaly follow-ups.
Runnable reference: `references/examples/04_detect_anomaly.py`.

`mv.discover` returns a `CandidateSet` with `meta.objective == "point_anomalies"`.

```python
import marivo.analysis_py as mv

series = mv.observe(
    mv.MetricRef("<metric_id>"),
    slice={"created_at": {"op": "between", "value": ["2026-07-01", "2026-09-30"]}},
)
candidates = mv.discover(series, objective="point_anomalies", threshold=1.0)
print(candidates.summary())
```

### Correlate two metric frames

Use this when two compatible `MetricFrame`s should be tested for Pearson
correlation. `mv.correlate` returns an `AssociationResult` with
`meta.method == "pearson"`.

```python
import marivo.analysis_py as mv

a = mv.observe(mv.MetricRef("<metric_a>"), window={"start": "2026-07-01", "end": "2026-09-30"})
b = mv.observe(mv.MetricRef("<metric_b>"), window={"start": "2026-07-01", "end": "2026-09-30"})
correlation = mv.correlate(
    a,
    b,
    alignment=mv.AlignmentPolicy(kind="calendar_bucket"),
    lag_policy=mv.LagPolicy(mode="single", offset=0),
)
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
  -> observe a metric series -> discover

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
- Do not call decomposition without an axis. Use
  `axis=mv.DimensionRef("<column>")` for a grouping column already present in
  the `DeltaFrame`.
- Do not call `mv.detect`; use
  `mv.discover(series, objective="point_anomalies")` for anomaly candidates.
- Do not pass `align=` to correlate. Use
  `alignment=mv.AlignmentPolicy(kind="calendar_bucket")`; correlation returns
  `AssociationResult`.

## Further reading

- `references/examples/*.py` - runnable templates for installed-library usage
- `references/examples/_fixtures/tiny_semantic.py` - tiny semantic model used by
  the examples; requires DuckDB support in the active Marivo environment
- `references/cheatsheet.md` - compact intent/frame reference
- `references/profiles.md` - user-scope datasource profile registry
  (`mv.profiles.set / list / describe / test / remove`), env-var secret
  contract, audit and error reference
- `references/backend-setup.md` - profile-backed and explicit-override
  backend flows, Trino connection-field mapping
- `references/pitfalls.md` - expanded error recovery notes
