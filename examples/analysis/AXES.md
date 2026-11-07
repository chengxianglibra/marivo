# Axis correspondence and paired observations

Use these examples when the task needs a shared composed-Metric condition,
complete-axis comparison, or paired association. First follow the
[neutral fixture setup](README.md#reproduce-on-a-separate-synthetic-project), then
run from that copied project:

```sh
"$example_python" axes.py
```

`axes.py` supplies runnable synthetic inputs for the snippets below. In another
project, choose its exact governed inputs, boundaries and correspondence.

## Apply one source condition to a composed Metric

For one condition shared by eligible child reductions, pass `where=` to the
observation. Put a condition on an individual child Metric before composition
when only that child should be filtered. Choose `start`, `end` and
`selected_value` from the task, then capture a scoped result directly:

```python
group = catalog.get("dimension:sample.readings.group")
assert group.kind == "dimension"
scope = session.time_scope(start=start, end=end)
day = mv.grain("day")
conditioned = session.observe(
    {"combined": metric + metric},
    by=(scope.by(day), group),
    where=group == selected_value,
).compute()
conditioned.show()
```

The day and group are explicit output axes. The row predicate limits
contributions throughout the composed observation. `axes.py` supplies runnable synthetic
inputs for this pattern. In an existing project, select its governed field path.

## Bind every axis before comparing independent Frames

For Frames with a calendar day axis followed by a group axis, declare both complete
axis pairs. `on` states coordinate correspondence; `match` separately states the
time rule. Axis display names, similar values and row positions do not establish
correspondence. Inputs must belong to the same Session. A composite identity is
one complete axis, rather than several independently matchable key columns.

```python
aligned = left.align(
    right,
    on=((left.axes[0], right.axes[0]), (left.axes[1], right.axes[1])),
    match=mv.ordinal(),
).compute()

change = right.compare(
    {
        "reference": mv.baseline(
            left,
            on=((right.axes[0], left.axes[0]), (right.axes[1], left.axes[1])),
            match=mv.ordinal(),
        )
    }
).compute()

association = aligned.correlate(
    aligned["left.value"], aligned["right.value"], method="pearson"
).compute()
association.show()
```

Reacquire Columns from `aligned`: Columns belong to one exact Frame input.
Correlation uses common non-null pairs, including real zero and duplicate
values, and returns `coefficient` and `n_pairs`. Presence is not an implicit
pair filter. Fewer than two pairs or a constant endpoint gives a null coefficient
while retaining the count; a correlation does not establish causation.

Support association claims with paired observations at a justified common grain,
and report usable pair counts and missingness. For composition, inspect the
relevant joint axes, check that named components reconcile to the stated total
or change, and explain any residual.

Alignment and comparison retain the outer union. Inspect the `presence` columns
alongside the `value` columns in `frame.to_pandas()`: an existing row with a null
value has presence true; an unmatched side has presence false; a real zero is a
numeric value. Neither null nor absence becomes zero. Alignment uses
`left_present` and `right_present`; comparison uses `current.present` and
`reference.present`. Comparison keeps current values and adds
`reference.baseline.value`, `reference.absolute.value` and
`reference.relative.value`. Relative change divides the difference by the
absolute baseline; a zero baseline yields null.

Construct time scopes with `session.time_scope(start=start, end=end)`. Pass
each scope in `where` and select the report Grain and Dimension in
`by=(day, group)`. The scope includes its start and excludes its end.
`mv.ordinal()` explicitly pairs compatible complete calendar buckets in the
two declared domains. Use `align` for a general time
union: `compare` rejects baseline time rows outside its declared current time
domain. Select time boundaries and the matching policy for the task.

Read `analysis.analysis_frame.align`, `analysis.analysis_frame.compare`,
`analysis.analysis_frame.correlate`, `analysis.inputs.time`, and
`analysis.ordinal` for exact contracts.

## Pair repeated observations on a shared time grid

Each one-day observation above provides one point per spatial group.
Their overall correlation describes variation across those groups. Association
over time needs intentionally repeated time coordinates. The final block in
`axes.py` uses the fixture's two daily buckets and correlates within each group:

```python
scope = session.time_scope(start=start, end=end)
day = mv.grain("day")
series = session.observe({"value": metric}, by=(scope.by(day), group)).compute()
composed = session.observe({"value": metric + metric}, by=(scope.by(day), group)).compute()
paired = series.align(
    composed,
    on=((series.axes[0], composed.axes[0]), (series.axes[1], composed.axes[1])),
    match=None,
).compute()
temporal_association = paired.correlate(
    paired["left.value"],
    paired["right.value"],
    method="pearson",
    by=(paired.axes[1],),
).compute()
temporal_association.show()
```

Both observations use the same complete grid and field axis. `match=None`
declares coordinate equality; the complete `on` mapping includes both axes.
Reacquire both Columns from `paired`. Choose the grain and grouping axes for
the task's analytical unit; this fixture's explicit day grain is a syntax
choice. Its composed Metric is a linear transform of the original, so the
example establishes no independent business association or causal conclusion.

Each group's result reports `coefficient` and `n_pairs` over common non-null
bucket pairs. A null value is excluded from a pair; a real zero remains. Inspect
the aligned values and presence separately: presence describes row existence,
not the correlation sample mask. Fewer than two common pairs or a constant
endpoint gives a null coefficient while retaining `n_pairs`.

An ISO starting date can be supplied as the first argument to both `seed.py`
and `axes.py` to reproduce the same intervals later. Each analysis uses a fresh
Session name. `axes.py` executes the complete time-plus-group mappings,
comparison and aligned-column correlation above, followed by the shared-grid
example. Source observations are independent reads, with no shared transaction
snapshot guarantee.
