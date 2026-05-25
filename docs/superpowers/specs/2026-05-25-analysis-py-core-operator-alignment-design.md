# analysis_py Core Operator Alignment Design

Date: 2026-05-25
Status: approved design for implementation planning

## Goal

Align `marivo.analysis_py` core operators with
`docs/specs/analysis/python-analysis-operator-design.md` for the Python-native
analysis track.

This is a breaking cleanup. The implementation should prefer the target-state
operator contract over compatibility with the current v1/v1.2 API.

Covered operators:

- `observe`
- `compare`
- `decompose`
- `discover` replacing public `detect`
- `correlate`

## Target Public Surface

The public core surface should expose fixed output families:

| Operator | Input | Output family |
| --- | --- | --- |
| `observe` | `MetricRef` plus scope | `metric_frame` |
| `compare` | two compatible `MetricFrame` inputs plus `AlignmentPolicy` | `delta_frame` |
| `decompose` | `DeltaFrame` plus one `DimensionRef` axis | `attribution_frame` |
| `discover` | compatible artifact plus objective and strategy | `candidate_set` |
| `correlate` | two compatible `MetricFrame` inputs plus alignment and lag policy | `association_result` |

`detect` is not a public core operator after this change. Its current z-score
anomaly behavior moves to:

```python
mv.discover(series, objective="point_anomalies", strategy="zscore", threshold=3.0)
```

`correlate` must not return `AttributionFrame`. It returns a new
`AssociationResult` family.

## Typed Refs And Policies

Add the minimum typed objects needed for these operators:

```python
mv.MetricRef("sales.revenue")
mv.DimensionRef("region")
mv.CalendarRef("cn_holidays")
mv.AlignmentPolicy(kind="calendar_bucket")
mv.AlignmentPolicy(kind="dow_aligned", calendar=mv.CalendarRef("cn_holidays"))
mv.AlignmentPolicy(kind="holiday_aligned", calendar=mv.CalendarRef("cn_holidays"))
mv.AlignmentPolicy(kind="holiday_and_dow_aligned", calendar=mv.CalendarRef("cn_holidays"))
mv.LagPolicy(mode="single", offset=0)
```

Rules:

- `observe` accepts `MetricRef`, not a bare string.
- `decompose` accepts `axis=DimensionRef(...)`, not `by=...`.
- `compare` accepts `alignment=AlignmentPolicy(...)`, not loose
  `align`, `calendar`, or `calendar_policy` parameters.
- `correlate` accepts `alignment=AlignmentPolicy(...)` and
  `lag_policy=LagPolicy(...)`.
- Provider fields use typed refs. For example, calendar alignment uses
  `CalendarRef`, not a bare calendar name.
- Constructors may internally store string ids, but operators should reject
  direct string parameters.

Unsupported policy combinations should fail closed with structured
`AnalysisError` subclasses.

## Operator Behavior

### `observe`

`observe(metric=MetricRef(...), ...)` keeps the current semantic_py execution
path:

1. Resolve the metric from the session semantic project.
2. Materialize referenced datasets through the session backend cache.
3. Apply slice and window filters at dataset level before metric evaluation.
4. Produce and persist a `MetricFrame`.

Current implementation support is limited to scalar and time-series observe.
The `MetricFrame` type can still represent `segmented` and `panel`, and
`MetricFrame.from_dataframe(...)` may keep supporting those shapes for tests and
promotion-style entry points, but this change does not add
`observe(dimensions=...)`.

### `compare`

`compare(a, b, alignment=AlignmentPolicy(...))` consumes only `MetricFrame`
inputs and produces a `DeltaFrame`.

Required validation:

- both inputs are `metric_frame`
- both inputs belong to the active session
- both inputs have the same metric id
- both inputs have the same `semantic_kind`
- alignment policy is supported for that shape

Policy mapping for this slice:

- `calendar_bucket`: ordinary bucket/sample alignment using existing local
  frame data
- `dow_aligned`: existing calendar alignment helper with day-of-week logic
- `holiday_aligned`: existing calendar alignment helper with holiday logic
- `holiday_and_dow_aligned`: align holiday buckets first, then align
  non-holiday buckets by day of week

The delta metadata and job params should store the normalized alignment policy
dump, not a mix of loose `align`, `calendar`, and `calendar_policy` fields.

### `decompose`

`decompose(frame, axis=DimensionRef(...))` consumes only `DeltaFrame` and
produces `AttributionFrame`.

Required validation:

- input is `delta_frame`
- input belongs to the active session
- `axis.id` exists as a column in the delta data
- selected value column is numeric
- panel delta is allowed only when the axis column exists and grouping is
  unambiguous

Remove target-state violations:

- no `by=None` auto-inference
- no first non-numeric-column guessing
- no scalar total-row fallback when no semantic axis is present

If a caller does not know the axis, it should use
`discover(delta, objective="driver_axes", ...)` or a future
`composites.auto_decompose(...)`; core `decompose` must not mix candidate
discovery with attribution.

### `discover`

Add `discover(...) -> CandidateSet`.

This slice implements the current `detect` capability as the first objective:

```python
mv.discover(
    source,
    objective="point_anomalies",
    strategy="zscore",
    threshold=3.0,
)
```

Required validation:

- `point_anomalies` input is `MetricFrame[time_series | panel]`
- `strategy="zscore"` is the only supported strategy in this slice
- threshold is a positive finite number
- output column names must not collide with input fields in a way that changes
  candidate semantics

`CandidateSet` is a candidate artifact, not a judgment or attribution result.
Items should include:

- source frame ref
- candidate score
- direction
- threshold
- keys or window-like fields derived from non-numeric source columns when
  available
- source row reference details sufficient for follow-up

`CandidateSet` persistence, metadata, `load_frame`, and session frame listing
must use the `candidate_set` family.

### `correlate`

`correlate(...) -> AssociationResult`.

Required validation:

- both inputs are `MetricFrame`
- both inputs belong to the active session
- both inputs have the same `semantic_kind`
- alignment policy is supported
- lag policy is supported
- aligned data has enough non-null, non-constant numeric pairs

This slice keeps the current Pearson computation but changes the output family.
Supported behavior:

- `method="pearson"`
- `LagPolicy(mode="single", offset=0)`
- `AlignmentPolicy(kind="calendar_bucket")` over sample or shared-key aligned
  data, using current local frame data

Unsupported behavior should fail closed:

- non-zero lag
- lag sweep
- unsupported correlation methods
- unsupported alignment kinds

`AssociationResult` metadata should include source refs, metric ids, method,
alignment policy, lag policy, aligned row count, dropped row count, and
correlation value.

## Persistence And Loading

Add frame/result classes and metadata for:

- `CandidateSet`
- `AssociationResult`

Update persistence and loading so these families round-trip through
`mv.load_frame(...)`. `Session.frames()` should list them by family name.

`AttributionFrame` should no longer carry `attribution_kind="anomaly"` or
`attribution_kind="correlation"` from public core operators. It remains the
output of `decompose`.

## Public Docs And Skill Examples

Update executable public examples under
`marivo-skill/marivo-py-analysis/references/examples/` and the skill README:

- use `MetricRef` in `observe`
- use `AlignmentPolicy` and `CalendarRef` in `compare`
- use `DimensionRef` in `decompose`
- replace `detect` examples with `discover(point_anomalies)`
- describe `correlate` as returning `AssociationResult`

`mv.help()` should list `discover` and should not list `detect`.

## Tests

Update or add focused coverage:

- `observe` rejects bare strings and accepts `MetricRef`
- `compare` rejects loose `align`/calendar parameters and accepts
  `AlignmentPolicy`
- calendar compare uses `CalendarRef`
- `decompose` rejects missing `axis`, rejects string axis, and does not infer a
  grouping column
- scalar delta without an axis column fails closed
- `discover(point_anomalies)` returns `CandidateSet`
- public `detect` export is gone
- `correlate` returns `AssociationResult`
- `load_frame` round-trips `candidate_set` and `association_result`
- skill examples execute with the new API

Recommended verification:

```bash
make test TESTS='tests/test_analysis_py_observe.py tests/test_analysis_py_compare.py tests/test_analysis_py_compare_calendar.py tests/test_analysis_py_decompose.py tests/test_analysis_py_discover.py tests/test_analysis_py_correlate.py tests/test_analysis_py_load_frame_v1_2.py tests/test_analysis_py_examples_v1_2.py'
make lint
make typecheck
```

If the Makefile exposes an examples gate, run that repository entrypoint as
well.

## Non-Goals

This change does not implement the full future operator registry.

Out of scope:

- `transform`
- `forecast`
- `test`
- `assess_quality`
- full `discover(driver_axes)` implementation
- `observe(dimensions=...)`
- non-zero lag and lag sweep
- new transport surfaces
- MCP or HTTP wrappers for `analysis_py`
