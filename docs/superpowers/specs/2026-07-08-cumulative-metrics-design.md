# Cumulative Metrics Design

Date: 2026-07-08
Status: approved (design review complete; implementation not started)

## Summary

Add cumulative metrics as a first-class semantic metric kind. A cumulative
metric answers "how much accumulated up to bucket t": the value at each time
bucket is the base metric aggregated from the beginning of history through
that bucket. Canonical examples: cumulative registered users, cumulative GMV,
cumulative active users (distinct).

The design follows the groove proven by semi-additive folds: authoring
constructor -> composition IR -> load-time additivity resolution -> dedicated
observe execution path -> frame metadata -> intent gating.

## Settled Decisions

1. **Anchor = all history.** Accumulation always starts at the beginning of
   the data. The observe window start only clips the displayed rows; it never
   changes values. This matches user intent semantics for "cumulative X".
2. **Empty buckets carry forward.** A bucket with no base activity shows the
   previous cumulative value, not a gap or NaN.
3. **`count_distinct` bases are first-class.** Cumulative distinct counts
   (e.g. cumulative active users) are a core use case, supported exactly via
   the first-seen rewrite (below), not rejected and not approximated.
4. **No SQL window functions in v1.** Accumulation happens in pandas
   post-processing at the existing derived-composition merge locus. All
   backend queries stay plain GROUP BY aggregations, so there is no new
   backend compatibility matrix.
5. **Deferred (v2+):** grain-to-date resets (MTD/YTD), trailing windows,
   sketch-based approximate distinct, SQL-side window functions,
   cross-component query fusion, incremental caching of cumulative frames.

## Semantic Layer

### Composition variant

`CumulativeComposition(base: str, over: str, anchor: Literal["all_history"])`
joins `RatioComposition` / `WeightedAverageComposition` / `LinearComposition`
as the fourth composition kind. `metric_type` stays `"derived"`; `MetricIR`
is otherwise untouched.

`anchor` is stored explicitly in the IR and included in the composition hash
from day one, even though `"all_history"` is the only v1 value. Future
variants (`("grain_to_date", grain)`, `("trailing", n, unit)`) then land as
closed-kind extensions without changing the hash of existing objects.

### Constructor

```python
ms.cumulative(
    *,
    name: str,
    base: MetricRef,
    over: TimeDimensionRef | None = None,
    unit: str | None = None,
    domain: DomainRef | None = None,
    ai_context: AiContextValue | None = None,
) -> MetricRef
```

`anchor` is not exposed on the v1 surface (single allowed value; YAGNI). The
docstring states the all-history semantics. `over` defaults to the base
metric's root-entity default time dimension, resolved at load; when
unresolvable or ambiguous, the loader error lists the real time-dimension
candidates for that entity.

### Base constraints

Validated at authoring and load with teaching errors built from real state:

- `base` must resolve to a tier-1 simple aggregate (`metric_type="simple"`,
  `aggregation` set).
- Aggregation whitelist: `sum`, `count`, `count_distinct`.
- `mean` / `median` / `percentile` bases: rejected. Cumulative of a mean is
  ill-defined; the error suggests composing cumulative sum over cumulative
  count as a ratio.
- Derived bases (cumulative-over-derived): rejected. The error suggests
  composing in the other direction (e.g. a ratio of two cumulative metrics).
- Tier-2 body metrics: rejected. Opaque expression bodies cannot be rewritten
  (first-seen) or baseline-split.

### Additivity and nesting

- The additivity algebra gains a `"cumulative"` nature. Any derived
  composition containing a cumulative component produces frames with
  `reaggregatable=False`.
- Nesting rule: `ratio` / `weighted_average` / `linear` MAY reference
  cumulative components (e.g. cumulative conversion rate = cumulative payers
  / cumulative actives). The planner's `nested-derived-unsupported` check is
  refined: components may be simple or cumulative; ratio/weighted/linear
  components remain rejected. Cumulative may NOT reference derived bases.

### Surface obligations

New symbol in `__all__` plus snapshot-test update; `ms.help('cumulative')`
topic; a new line in the `ms.help('metric')` constructor decision order;
`mv.help(ref)` consumption briefing covers cumulative frames.

## Execution Layer (observe)

### Planner

Observe planning gains a third dispatch branch producing
`CumulativeObservePlan { base_plan: BaseObservePlan, over, window }` — the
embedded base plan reuses all existing dimension/where/timezone resolution.
`ComponentPlan` gains a component-kind marker (`simple | cumulative`) so
derived compositions can carry cumulative components; each cumulative
component executes through the cumulative executor and yields a standard
component-shaped DataFrame into the existing pandas merge. Ratio/linear
per-bucket combination code is unchanged.

### Execution plan by shape

Time-series / panel (grain set) — exactly two backend queries per cumulative
metric:

```text
baseline query: GROUP BY dims, aggregate base over (-inf, window_start)
flow query:     existing per-bucket aggregation over [window_start, window_end)
post-process:   spine densify (all display buckets x dim combos from
                baseline UNION flow) -> fill missing flow with 0
                -> per-dim-combo cumsum ordered by bucket_start
                -> add baseline
```

Scalar / segmented (no grain): a single query aggregating the base over
everything up to the window end — using the same end-boundary convention as
existing observe windows — or over all data when no window is given
("cumulative as of now").

Dimension combos for the spine come from the union of baseline and flow
results: a combo with history but zero in-window flow must still appear,
carried forward from its baseline value.

### count_distinct: first-seen rewrite

Cumulative distinct cannot be computed by integrating per-bucket distinct
counts (returning entities would double-count). The executor rewrites:

- Dedup subquery: `GROUP BY (distinct key[, slice dims]) -> min(over) AS
  first_ts`. Where-filters apply BEFORE dedup (first qualifying event under
  the filter). NULL distinct keys are dropped to preserve `nunique`
  semantics.
- Baseline counts rows with `first_ts < window_start`; flow buckets and
  counts `first_ts` per bucket. Returning entities are never recounted.
- The distinct key is the base measure's column expression (available via
  the materializer).
- Per-slice semantics: the same entity active in two slices counts once per
  slice (dedup key includes slice dims).

### Semantics guaranteed by the plan

- Carry-forward emerges mechanically from fill-0 + cumsum.
- Leading empty buckets equal the baseline; with no history at all the value
  is 0, not NaN.
- Bucket boundaries and the baseline boundary share the existing
  `bucket_time_expression` / `report_tz` machinery — no second time-zone
  conversion path.

### Multi-metric observe

The arity-N rejection list extends from {derived, folded} to {derived,
folded, cumulative}, using the same teaching-error shape. Arity-1 is
unrestricted.

### Frame result

`semantic_kind` as usual. `reaggregatable=False`. Meta gains an arity-1
sidecar `cumulative={base, over, anchor}` (same pattern as
`composition`/`component_ref`). The frame is dense (spine-synthesized);
params record the spine-synthesis fact and attribute the baseline/flow
queries (both recorded as ordinary `QueryExecution`s). Cost is visible, not
sampled: the baseline is a full-history scan (for `count_distinct`, the
dedup is a full-table GROUP BY).

## Intent Gating

Four explicit teaching errors, each built from real state:

- **compare**: rejected at metric resolution. The delta between two windows
  of an all-history cumulative is identically the flow between them; the
  error names the base ref to compare instead.
- **attribute / decompose**: rejected via the frame meta marker. Per-bucket
  attribution of a running total is ill-defined (history is re-attributed in
  every bucket); the error points to attributing the base flow, noting that
  cumulative delta over a window equals the base total over that window.
- **forecast**: rejected; cumulative series are non-stationary. The error
  points to forecasting the base flow.
- **rollup**: blocked by the existing `reaggregatable` gate
  (`transform.py:1769`) — zero new code.

`transform.window` (display-window clipping) stays allowed: values are
anchored to all history, so clipping is safe. The meta marker propagates
through transforms via the existing meta-propagation machinery. All other
intents (correlate, discover, quality, derive, hypothesis_test) consume
cumulative frames as ordinary data with no gates.

## V2 Capabilities and Forward Compatibility

V2 targets capability parity with MetricFlow's cumulative surface (`window`,
`grain_to_date`, `period_agg`) while keeping the v1 cost model (no range-join
data expansion for the all-history case). Each item states the mechanism and
exactly where it lands on the v1 structure.

### V2-1: grain-to-date resets (MTD / QTD / YTD)

- Authoring: anchor variant `("grain_to_date", grain)` — a closed-kind
  extension of the anchor field already stored in the IR and composition
  hash; existing objects keep their hashes. The constructor grows an optional
  parameter (additive signature change).
- Execution: the same two-query skeleton. The seed query's lower bound
  changes from "beginning of history" to "start of the reset period
  containing window_start" (a bounded scan, cheaper than all_history). The
  post-process cumsum additionally partitions by a reset-period key derived
  from `bucket_start` (pandas date-trunc; `report_tz` machinery already
  provides period boundaries).
- `count_distinct`: the first-seen dedup subquery adds the reset period to
  its GROUP BY key (`min(over)` per key per period). Still a plain GROUP BY
  rewrite.

### V2-2: trailing windows (rolling N)

- Authoring: anchor variant `("trailing", n, unit)`.
- The scalar form ("distinct actives in the last 7 days, one number") works
  today with no cumulative support: a plain `count_distinct` metric observed
  over a 7-day window.
- Additive bases (sum/count): no seed query; the flow query's fetch window
  extends W before the display start, and the post-process operator becomes
  a rolling sum over the dense flow instead of cumsum (equivalently:
  `cumulative(t) - cumulative(t-W)`).
- `count_distinct` bases: rolling distinct cannot be derived from any
  arithmetic of per-bucket or cumulative counts. The executor gains a third
  plan shape — spine-expansion join: each event row joins to the <= W buckets
  where it is visible, then per-bucket `count_distinct`. Exact, plain
  GROUP BY + range join, no window functions; cost is an explicit
  window-width-times data expansion. This is the same strategy MetricFlow
  compiles for its `window` mode (its weekly-active-users example), i.e. the
  industry exact plan, not a bet.
- Trailing series require a time grain; trailing with no grain is rejected
  with a teaching error pointing at the plain windowed observe above.

### V2-3: re-aggregation to coarser grains (period_agg parity)

- v1 blocks rollup via the boolean `reaggregatable` gate. v2 upgrades frame
  meta with an explicit rollup fold (`rollup_fold="last"` for cumulative;
  MetricFlow's `period_agg: first/last/average` is the reference surface) and
  teaches the existing rollup gate to honor it.
- Because cumulative frames are dense and values anchor to history,
  rollup-by-period-end is a pure frame operation (group buckets by the
  coarser grain, take the last bucket) — no re-query. A gate relaxation plus
  a pandas group-take-last; frames without `rollup_fold` stay blocked,
  which is exactly v1 behavior, so no migration.

### Later (unscheduled)

- Approximate distinct sketches (HLL state merge) as an alternative executor
  strategy behind the same dispatch; frame meta must then record exact vs
  approximate.
- Cross-component query fusion arrives with multi-metric observe fusion;
  cumulative components already emit standard component-shaped frames, so
  fusion applies orthogonally.
- compare with to-date alignment replaces the v1 compare gate; gate removal
  is additive.
- Incremental caching: the seed/flow split is the natural incremental unit
  (seed reusable, flow append-only per bucket).

### Why v2 does not restructure v1

The extension surface is a dispatch matrix: anchor kind x base aggregation.
v1 implements the `all_history` column for {sum, count, count_distinct};
each v2 item adds an anchor kind or an executor strategy inside a cell —
never a reshape of `MetricIR`, `CumulativeComposition`, planner interfaces,
or frame meta. The v1 implementation MUST honor these commitments, which is
what makes the claim true:

1. `anchor` lives in the IR and the composition hash from v1. New kinds are
   additive and never re-hash existing objects.
2. The baseline query is implemented as one instance of a parameterizable
   "seed" stage (all_history: unbounded lower bound; grain_to_date:
   period-bounded; trailing: absent), not a hardcoded special case.
3. The pandas post-process exposes one accumulation-operator slot (v1:
   cumsum; v2 adds period-partitioned cumsum and rolling sum).
4. Spine synthesis lives in a single helper with the render target kept
   separable: v1 renders pandas; the v2 expansion join needs the same spine
   SQL-side (e.g. an ibis memtable). One source of truth for spine logic,
   two render targets.
5. Intent gates (compare/attribute/decompose/forecast) and the multi-metric
   arity restriction are teaching errors; later support means relaxing or
   deleting a gate, which is additive.

The only genuinely new v2 executor code is the spine-expansion join for
trailing distinct — a new branch in the existing dispatch, alongside the
untouched v1 branches.

## Testing

Narrowest first; DuckDB fixture golden tests as the core.

- Semantic layer: base whitelist and each teaching error; additivity
  resolves to `"cumulative"`; ratio-over-cumulative loads;
  cumulative-over-derived rejected; `__all__` snapshot; `ms.help` topics.
- Execution correctness (core):
  - returning users are NOT double-counted (the first-seen keystone test);
  - missing buckets carry forward; leading buckets equal baseline;
  - where-filters apply before dedup;
  - per-slice first-seen (one user active in two regions counts once per
    region);
  - `report_tz` bucket-boundary behavior;
  - all four shapes (scalar / time_series / segmented / panel);
  - ratio-over-cumulative end-to-end;
  - multi-metric arity-N rejection.
- Intent gates: compare / attribute / decompose / forecast teaching errors.
- No window functions means no new backend-matrix risk; existing
  trino/clickhouse integration suites are unchanged.
- New examples are executed in-process by `test_semantic_agent_tightening`;
  examples are tests.

## Documentation Obligations (same change)

- `ms.help('cumulative')` topic + decision-order line in `ms.help('metric')`.
- `docs/specs/semantic/python-semantic-layer.md` and
  `docs/specs/analysis/python-analysis-design.md` sections.
- `site/` example code, English and Chinese editions in sync.
- Skill-side references documents (grill points: over-axis choice,
  count_distinct dedup-key caliber) under the skills' `references/`
  directories — not in the always-loaded SKILL.md.

## Out of Scope (v1)

Grain-to-date resets, trailing windows, rollup re-aggregation of cumulative
frames, approximate distinct sketches, SQL window functions, cross-component
query fusion, incremental caching of cumulative frames. See "V2 Capabilities
and Forward Compatibility" for how each lands on the v1 structure without
restructuring it.
