# marivo-py-analysis pitfalls

Use this file when a Marivo Python analysis script fails with a structured
exception. Fix the smallest script and re-run it with the active Python
environment where `marivo` is installed.

## Passing a DeltaFrame back into compare

**Symptom from `references/examples/99_pitfall_pass_delta_to_compare.py`:**

```text
SemanticKindMismatchError: compare(a, b) expected MetricFrame for `b`, got DeltaFrame.

发生位置: mv.compare call
原因: got kind delta_frame, expected metric_frame; this usually means passing a compare result where an observe result is required.

正确写法:
  cur  = mv.observe(mv.MetricRef("sales.revenue"), window="2026Q3")
  base = mv.observe(mv.MetricRef("sales.revenue"), window="2025Q3")
  delta = mv.compare(cur, base)

相关文档: marivo-skill/marivo-py-analysis/references/pitfalls.md
```

**Action:** pass two `MetricFrame`s into `mv.compare`, then pass the resulting
`DeltaFrame` to `mv.decompose`.

```python
cur = mv.observe(mv.MetricRef("sales.revenue"), window={"start": "2026-07-01", "end": "2026-09-30"})
base = mv.observe(mv.MetricRef("sales.revenue"), window={"start": "2025-07-01", "end": "2025-09-30"})
delta = mv.compare(cur, base, compare_type="yoy")
attribution = mv.decompose(delta)
```

The exception template still shows compact quarter strings. For new examples and
agent-authored scripts, prefer explicit dict windows or structured slices.

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
`decompose`, `detect`, or `correlate`.

```python
if mv.session.current() is None:
    mv.session.create(name="my_investigation")
```

`mv.session.history()` returns an empty list when there is no active session, so
it is safe for quick context checks.

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
content. Use bare strings only when you are matching an existing error template.

## Detect and correlate return AttributionFrame

**Symptom:** code checks for `CandidateSet` after `mv.detect` or
`CorrelationFrame` after `mv.correlate`.

**Action:** treat both as specialized `AttributionFrame`s.

```python
anomalies = mv.detect(series, threshold=1.0)
assert anomalies.meta.attribution_kind == "anomaly"

correlation = mv.correlate(a, b, align="sample")
assert correlation.meta.attribution_kind == "correlation"
```

Use `.summary()` for compact inspection and `.to_pandas()` for downstream pandas
work.

## Decompose with by on scalar deltas

**Symptom:**

```text
SemanticKindMismatchError: scalar decompose does not accept by
```

**Action:** omit `by=` for scalar deltas. Add `by=` only when the `DeltaFrame`
already contains that grouping column.

```python
delta = mv.compare(cur, base, compare_type="yoy")
attribution = mv.decompose(delta)
```
