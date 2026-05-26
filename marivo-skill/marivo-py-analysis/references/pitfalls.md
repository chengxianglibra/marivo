# marivo-py-analysis pitfalls

Use this file when a Marivo Python analysis script fails with a structured
exception. Fix the smallest script and re-run it with the active Python
environment where `marivo` is installed.

Use `mv.MetricRef(...)`, `mv.DimensionRef(...)`, `mv.CalendarRef(...)`,
`mv.AlignmentPolicy(...)`, and `mv.LagPolicy(...)` at public operator
boundaries. Do not pass bare strings directly to `observe`, `decompose`, or
calendar-backed `compare`.

## Passing a DeltaFrame back into compare

**Symptom from `references/examples/99_pitfall_pass_delta_to_compare.py`:**

```text
SemanticKindMismatchError: compare(a, b) expected MetricFrame for `b`, got DeltaFrame.

发生位置: mv.compare call
原因: got kind delta_frame, expected metric_frame; this usually means passing a compare result where an observe result is required.

正确写法:
  cur  = mv.observe(mv.MetricRef("sales.revenue"), window={"start": "2026-07-01", "end": "2026-09-30"})
  base = mv.observe(mv.MetricRef("sales.revenue"), window={"start": "2025-07-01", "end": "2025-09-30"})
  delta = mv.compare(cur, base, alignment=mv.AlignmentPolicy(kind="calendar_bucket"))

相关文档: marivo-skill/marivo-py-analysis/references/pitfalls.md
```

**Action:** pass two `MetricFrame`s into `mv.compare`, then pass the resulting
`DeltaFrame` to `mv.decompose`.

```python
cur = mv.observe(mv.MetricRef("sales.revenue"), window={"start": "2026-07-01", "end": "2026-09-30"})
base = mv.observe(mv.MetricRef("sales.revenue"), window={"start": "2025-07-01", "end": "2025-09-30"})
delta = mv.compare(cur, base, alignment=mv.AlignmentPolicy(kind="calendar_bucket"))
attribution = mv.decompose(delta, axis=mv.DimensionRef("bucket_start"))
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

## No active session

**Symptom:**

```text
NoActiveSessionError: no active session and none set via attach()
```

**Action:** check cheaply, then create or attach before `observe`, `compare`,
`decompose`, `discover`, or `correlate`.

```python
if mv.session.current() is None:
    mv.session.create(name="my_investigation")
```

`mv.session.history()` returns an empty list when there is no active session, so
it is safe for quick context checks.

## No backend factory

**Symptom:**

```text
NoBackendFactoryError: session has no backend_factory; data-materializing intents need one
```

**Action:** create or attach the session with a live backend before calling
`observe`, `compare`, `decompose`, `discover`, or `correlate`.

```python
import ibis
import marivo.analysis_py as mv

session = mv.session.create(
    name="analysis",
    backends={
        "warehouse": lambda: ibis.trino.connect(
            host="<trino_host>",
            port=80,
            user="<user>",
            database="<catalog>",
            source="<source>",
            client_tags=["standby", "routing_group=bsk_wide"],
        )
    },
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
import marivo.semantic_py as ms

print(ms.list_metrics())
cur = mv.observe(mv.MetricRef("sales.revenue"), window={"start": "2026-07-01", "end": "2026-09-30"})
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
mv.observe(
    mv.MetricRef("sales.revenue"),
    window={"start": "2026-07-01", "end": "2026-09-30"},
)

mv.observe(
    mv.MetricRef("sales.revenue"),
    slice={"created_at": {"op": "between", "value": ["2026-07-01", "2026-09-30"]}},
)
```

Do not default to natural-language periods or bare quarter strings in new skill
content. Wrap metric ids with `mv.MetricRef(...)`.

## Discover returns CandidateSet

**Symptom:** code calls the removed `mv.detect(...)` helper or expects anomaly
results to be an `AttributionFrame`.

**Action:** use `mv.discover(..., objective="point_anomalies")` and treat the
result as a `CandidateSet`.

```python
candidates = mv.discover(series, objective="point_anomalies", threshold=1.0)
assert candidates.meta.objective == "point_anomalies"
```

## Correlate returns AssociationResult

**Symptom:** code passes the removed `align=` argument or checks for
`AttributionFrame` / `CorrelationFrame` after `mv.correlate`.

**Action:** pass an `AlignmentPolicy` and treat correlation as an
`AssociationResult`.

```python
correlation = mv.correlate(
    a,
    b,
    alignment=mv.AlignmentPolicy(kind="calendar_bucket"),
    lag_policy=mv.LagPolicy(mode="single", offset=0),
)
assert correlation.meta.kind == "association_result"
```

Use `.summary()` for compact inspection and `.to_pandas()` for downstream pandas
work.

## Decompose without an explicit axis

**Symptom:**

```text
TypeError: decompose() missing 1 required keyword-only argument: 'axis'
```

**Action:** pass `axis=mv.DimensionRef("<column>")` for a grouping column that
already exists in the `DeltaFrame`.

```python
delta = mv.compare(cur, base, alignment=mv.AlignmentPolicy(kind="calendar_bucket"))
attribution = mv.decompose(delta, axis=mv.DimensionRef("bucket_start"))
```
