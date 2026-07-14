# Cumulative Metrics V2 Design

Date: 2026-07-08
Status: implemented; amended 2026-07-14 by the breaking derived-compare completion
Prerequisite: the v1 design
(`2026-07-08-cumulative-metrics-design.md`) implemented as specified. V2
never reshapes v1 contracts; every change below is an anchor-kind addition,
a stage parameterization, or a gate relaxation, per the v1
forward-compatibility section.

The 2026-07-14 completion deliberately replaces the persisted
`derived_contains_cumulative` marker and artifact identity. It does not read,
migrate, or fall back to the old derived marker.

## Summary

V2 extends cumulative metrics from the single all-history anchor to the full
window vocabulary — grain-to-date resets (MTD/QTD/YTD) and trailing windows
(rolling N) — and opens the two consumption paths v1 deliberately blocked:
rollup re-aggregation to coarser grains (period-end semantics) and
cross-period comparison (to-date alignment). Capability benchmark is
MetricFlow's cumulative surface (`window`, `grain_to_date`, `period_agg`),
kept on the v1 cost model: no range-join expansion for all-history, plain
GROUP BY everywhere except the one genuinely new plan (trailing distinct).

## Settled Decisions

1. **Scope**: grain_to_date, trailing windows, rollup re-aggregation, AND
   compare to-date alignment. Without to-date compare, MTD metrics can be
   observed but not compared to the prior period, losing their main business
   value.
2. **Partial trailing windows** (window reaches before the data start):
   show the actual partial accumulation AND mark it via coverage —
   MetricFlow-compatible values, but never silent.
3. **Authoring**: one kind-dispatched `anchor` parameter taking closed value
   objects, not MetricFlow-style mutually exclusive keywords.
4. **Trailing-distinct spine**: an inline ibis memtable built from the
   display buckets — backend-agnostic, no warehouse spine table.
5. **Compare to-date**: reuse `window_bucket` ordinal alignment (bucket i of
   a single-period to-date series IS period-position i); no new
   AlignmentPolicy kind.
6. **Rollup fold**: `last` only. MetricFlow's `first`/`average` are caliber
   forks we deliberately do not open (same philosophy as locked
   carry-forward in v1).
7. **Trailing empty windows are true zero**, not carry-forward: no activity
   in the last 7 days means 0. Carry-forward remains an
   all_history/grain_to_date semantic.
8. **Trailing units are fixed-size only** — any fixed-size unit from the
   existing grain vocabulary (second through week; day and week in
   practice). Calendar-variable trailing ("sliding 3 months") is
   rejected with a teaching error pointing to grain_to_date or a fixed-day
   window; deferred to v3.

## Authoring and Semantic Layer

### Value objects

Two new public constructors returning frozen value objects (precedent:
`ms.semi_additive`):

```python
ms.grain_to_date(grain="month")   # grain: week | month | quarter | year
ms.trailing(count=7, unit="day")  # count >= 1; unit: fixed-size only
```

Construction-time validation with teaching errors (unknown grain, calendar-
variable trailing unit, non-positive count).

### Constructor growth

```python
ms.cumulative(
    *,
    name: str,
    base: MetricRef,
    over: TimeDimensionRef | None = None,
    anchor: GrainToDate | Trailing | None = None,   # None = all history
    unit: str | None = None,
    domain: DomainRef | None = None,
    ai_context: AiContextValue | None = None,
) -> MetricRef
```

Additive signature change. No `ms.all_history()` object (YAGNI; the default
and its semantics live in the docstring). `GrainToDate` / `Trailing` type
names stay out of the top-level help index; the constructors are the public
surface.

### IR and hash

`CumulativeComposition.anchor` widens from `Literal["all_history"]` to
`"all_history" | ("grain_to_date", grain) | ("trailing", count, unit)` —
exactly the closed-kind growth the v1 anchor-in-hash commitment reserved.
The v1 hash text for `"all_history"` objects is byte-identical before and
after V2 (regression-tested).

### Constraints across anchor kinds

- Base whitelist unchanged: tier-1 `sum` / `count` / `count_distinct` for
  all three anchors.
- `over` omission rule unchanged (single-time-dimension entities only).
- Additivity resolves to `"non_additive"` for all anchors; the three-bucket
  public additivity contract stays untouched. Discriminator remains the
  composition kind plus its anchor.
- Nesting: ratio/weighted/linear over cumulative components allowed for all
  anchors; cumulative-over-derived still rejected. Compare also accepts an
  arity-1 derived wrapper when every outer component is cumulative and every
  component has the same eligible `trailing` or `grain_to_date` anchor.
  All-history, mixed-anchor, unresolved-anchor, and cumulative/non-cumulative
  wrappers stay gated.

### Surface obligations

`ms.grain_to_date` and `ms.trailing` join `__all__` (snapshot update) with
full docstrings and `describe` coverage. `ms.help('cumulative')` gains an
anchor section with two runnable examples (MTD revenue, rolling-7d active
users). The `ms.help('metric')` decision-order "when" text widens to cover
all three accumulation shapes. Site docs EN/CN in sync.

## Execution Plans (anchor kind x base aggregation)

V1's seed + flow + post-process skeleton parameterizes now that the second
variant exists (v1 wrote the plain branch; the abstraction arrives with the
test pressure, as the v1 spec committed). "Seed" generalizes v1's baseline
query.

### grain_to_date x sum/count

- **Grain compatibility rule** (plan-time teaching error): every display
  bucket must lie entirely within one reset period. Week grain under
  month/quarter/year resets is illegal (week buckets straddle month
  boundaries); day/hour are legal; month grain under month reset is legal
  and meaningful (each bucket = full-period total, the period-end value).
- Seed query: only when `window_start` is not on a reset boundary —
  aggregate over `[period_start(window_start), window_start)`, a bounded
  scan feeding ONLY the first period's buckets. Later periods reset to zero
  at their boundaries and need no seed. Windows starting on a boundary run
  one query, not two.
- Flow query unchanged. Post-process: spine densify -> fill 0 -> cumsum
  partitioned by (dims x reset-period key derived from `bucket_start` via
  report_tz date-trunc) -> add seed to first-period buckets.
- Scalar / segmented (no grain): single query over
  `[period_start(end - epsilon), end)`, where `end - epsilon` is the final
  INCLUDED instant under the half-open `[start, end)` window convention —
  observing a full July (`end="2026-08-01"`, exclusive) must aggregate
  July, not an empty August MTD. This rule applies wherever V2 derives a
  reset period from a window END (rollup tail-period detection included);
  derivation from window STARTS uses the raw inclusive bound.

### grain_to_date x count_distinct

First-seen gains the period dimension: dedup subquery
`GROUP BY (distinct key, slice dims, period_trunc(over)) -> min(over) AS
first_ts` — first qualifying event *within its period*. An entity counts
once per period and resets naturally at boundaries. Seed/flow count
`first_ts` rows as in v1. All other v1 first-seen rules (filters before
dedup, NULL keys dropped, per-slice keys) carry over unchanged.

### trailing x sum/count

- **Integer-multiple rule** (teaching error): the window span must be an
  integer multiple of the query grain; `W_buckets = span / grain`
  (trailing 7 day at day grain -> 7 buckets; at hour -> 168).
- No seed. The flow fetch window extends to `[window_start - span,
  window_end)`. Post-process: densify over the extended range -> fill 0 ->
  rolling sum with `min_periods=1` (partial windows produce actual values)
  -> clip back to the display window.
- Empty windows are 0, not carried forward (Settled Decision 7); the spec
  test suite contrasts this against all_history explicitly.
- Coverage: one extra tiny scalar query — `min(over)` under the same
  filters (the data-start query) — labels buckets whose window reaches
  before the data start as `partial`. Values still shown (Settled
  Decision 2).

### trailing x count_distinct — the one new plan shape

- Spine-expansion join: an inline ibis memtable of the display buckets
  joins the filtered, dimension-projected source on "event time falls in
  the span ending at this bucket's end boundary" (same end-boundary
  convention as observe windows), then per-(bucket, dims) `count_distinct`.
- Exact, plain GROUP BY + range join, no window functions. Cost is an
  explicit rows x W_buckets expansion; the planner guards with a
  bucket-count cap teaching error (cap value settled at implementation).
- Empty buckets fill 0. Coverage via the same data-start query.
- NULL keys dropped; nunique parity as in v1.

### Common execution facts

- Backend queries per metric: all_history 2 (v1); grain_to_date 1–2 (seed
  skippable); trailing 2 (flow or join, plus data-start). All recorded as
  ordinary `QueryExecution`s.
- The frame meta `cumulative` payload carries the anchor. All three anchors
  set `reaggregatable=False`; the rollup path below is the sanctioned
  relaxation.
- Multi-metric observe arity-N exclusion unchanged from v1.
- Trailing with no grain is still rejected (teaching error points to a
  plain windowed observe — one path per capability).
- **Coverage contract widening**: today's CoverageFrame is pinned to
  sampled time-slot coverage (`coverage_kind: Literal["time_slot"]` with a
  required `sample_interval`). Trailing data-start partiality is a
  different quality signal, so `coverage_kind` widens to the closed set
  `"time_slot" | "window_coverage"` and `sample_interval` becomes optional
  (None for window_coverage). window_coverage rows carry (bucket_start,
  expected_span, covered_span, coverage_ratio, coverage_status); producers
  are trailing observe (data-start) and grain rollup (tail period).
  `MetricFrame.coverage()` documentation becomes kind-dispatched; the two
  signal kinds never share one summary payload.
- **Fixed-duration arithmetic** (span / grain, `W_buckets`) uses one shared
  helper promoted from the sampled-fold private seconds table into the
  windows module: `Grain.width_seconds()` is sub-day-only today and
  day/week widths live only in `sampled_fold`, so observe, coverage, and
  compare would otherwise each grow their own conversion. Day/week are
  fixed 86400/604800-second widths, matching the existing sampled coverage
  math (DST-transition buckets share that approximation).

## Rollup Re-aggregation

- **Public API change** (today's `session.transform.rollup(frame, *,
  drop_axes)` accepts nothing else, forbids dropping the time axis, and
  aggregates with `.sum()` only — `rollup_fold` alone would have no
  callable entry point): `rollup` gains an optional
  `grain: str | None = None` target time-grain token, and at least one of
  `drop_axes` / `grain` must be given. `grain` re-buckets the time axis
  (report_tz date-trunc) with one uniform value-aggregation dispatch:
  reaggregatable frames sum (the existing additive semantics extended to
  the time axis); frames with `rollup_fold="last"` take period ends;
  non-reaggregatable frames without `rollup_fold` keep the v1 rejection
  verbatim. Target-grain validation (grain-compatibility rule, must be
  coarser than the current grain) teaches.
- `MetricFrameMeta` gains a top-level optional field
  `rollup_fold: Literal["last"] | None = None` (an additive schema change
  to an `extra="forbid"` model, called out as in v1). V2 sets it only on
  cumulative frames.
- Semantics per anchor: all_history -> period-end running total;
  grain_to_date -> rolling up to the reset grain yields per-period totals
  (day-MTD to month = full-month totals); trailing -> "rolling value as of
  period end" sampling. The rollup TARGET grain must satisfy the same
  grain-compatibility rule as observe (week targets under month resets are
  illegal).
- Mechanics: pure pandas — group buckets by the coarser grain (report_tz
  date-trunc), take the last bucket per group per dims. Dense frames make
  "last" well-defined. A trailing display window that ends mid-period
  marks that final period's rollup row `partial` in coverage.
- The result keeps the cumulative marker and `rollup_fold`, so rollups
  chain correctly (day -> month -> quarter keeps taking period ends).

## Compare: To-Date Alignment

The v1 blanket compare gate becomes anchor-dispatched for directly observed
arity-1 cumulative frames and compatible derived cumulative wrappers:

- **all_history: still rejected.** The delta between two windows is
  identically the flow between them; the teaching error names the base
  ref.
- **trailing: allowed** under ordinary window_bucket rules plus one new
  validation: both frames' anchor payloads must match exactly — comparing
  rolling-7d against rolling-30d is a category error (teaching error).
- **grain_to_date: allowed via ordinal alignment plus three validations**:
  1. both frames share the reset grain and the query grain;
  2. each frame's window starts exactly on a reset-period boundary
     (checked from window meta + report_tz truncation);
  3. each frame's window spans at most one reset period — in multi-period
     windows, ordinal position i is ambiguous; the teaching error suggests
     single-period observes (this month so far vs the full prior month).
  Bucket i then pairs with bucket i — period-position alignment. The
  baseline tail beyond the current frame's length produces no delta rows
  and is recorded in
  `alignment_dump.to_date = {reset_grain, matched_buckets,
  baseline_tail_buckets}` — truncation is visible, never silent. Visible
  means the agent-facing surface, not raw meta: whenever the tail is
  non-empty, the DeltaFrame's `show()` card and `contract()` state
  `matched_buckets` and `baseline_tail_buckets` — agents read cards, they
  do not dig `alignment_dump`.
- **Scalars**: grain_to_date scalars ("this period so far") compare when
  both frames' elapsed-within-period spans are equal (derived from window
  meta); on mismatch the teaching error states the exact baseline window to
  observe.
- **Derived wrappers**: the required `derived_contains_cumulative` marker is
  `{kind, anchor, compare_blocker, components}`. `anchor` is the common outer
  component anchor or `None`; `compare_blocker` is
  `non_cumulative_component`, `mixed_component_anchors`,
  `unresolved_component_anchor`, or `None`. The current and baseline marker
  kind and effective anchor must match. The same trailing or grain-to-date
  validation then runs as for direct cumulative frames. Marker version 2 is
  included in observe artifact identity; old persisted markers are unsupported.
- **DeltaFrame inherits the cumulative marker** (`DeltaFrameMeta` gains the
  same additive field), so attribute/decompose on an MTD delta stay gated
  in V2 — per-bucket attribution of to-date deltas is computable but too
  easy to misread; the teaching error points to attributing the base flow
  over the matched elapsed windows. Relaxation waits for evidence.

## Intent Gating Delta (v1 -> v2)

| Surface | v1 | v2 |
|---|---|---|
| compare | reject all cumulative | anchor-dispatched: all_history reject; grain_to_date to-date; trailing same-anchor; homogeneous same-anchor derived wrappers use the same paths |
| rollup | reject (`reaggregatable=False`) | allowed with `rollup_fold="last"`, else v1 rejection |
| attribute / decompose / forecast | reject | unchanged (relaxation needs evidence) |
| ungated intents caveat | running-total wording | anchor-aware wording (trailing: rolling-series autocorrelation pollutes hypothesis tests) |
| multi-metric observe | exclude cumulative at arity N | unchanged |

### Anchor-aware dynamic guidance

V1 shipped a fixed `running_total_caveat` on every affordance of a
cumulative frame's `contract()` (all-history wording). V2 makes the dynamic
next-step surface anchor-aware — `frame.contract()`, `frame.show()`, and the
`mv.help(ref)` briefing all dispatch on the anchor in `meta.cumulative`:

- **all_history**: compare stays gated with the compare-the-base teaching
  text; the running-total caveat keeps its current wording.
- **grain_to_date**: compare becomes a conditional affordance stating its
  preconditions (single-period boundary-anchored windows, same reset and
  query grain); the caveat keeps non-stationarity wording.
- **trailing**: compare becomes a conditional affordance requiring an
  identical-anchor baseline; the caveat swaps to rolling-window
  autocorrelation wording (correlation / hypothesis tests).
- **derived wrappers**: `show()` and `contract()` surface the common anchor or
  the exact persisted `compare_blocker`; no legacy marker is inferred.
- **rollup**: frames carrying `rollup_fold` surface rollup as an available
  affordance with the target-grain rules; frames without it keep the
  re-observe hint.

## Testing

DuckDB golden tests as the core, plus two new regression classes:

- **Hash stability regression**: existing v1 all_history objects hash
  identically under v2 code — the forward-compatibility promise, tested
  directly.
- grain_to_date: resets at period boundaries; seed only for the first
  partial period (boundary-started windows run zero seed queries);
  within-period returning user counts once, next period counts again
  (keystone); grain-compatibility teaching error (week under month);
  month-at-month = period totals; report_tz period boundaries; scalar
  boundary regression — a full-July window (`end="2026-08-01"`, exclusive)
  yields the July total, not an empty August MTD (keystone).
- trailing: integer-multiple rule error; partial windows show actual values
  with `partial` coverage (data-start query); empty window = 0 with an
  explicit contrast test against all_history carry-forward; expansion-join
  distinct correctness (a user active on day 1 and day 5 counts once in any
  7d window containing both, and drops out after the window passes —
  keystone); bucket-cap teaching error; display-window clipping.
- rollup: the new `grain` parameter's aggregation dispatch (sum for
  reaggregatable / last for `rollup_fold` / reject otherwise) and the
  at-least-one-of drop_axes/grain argument error; last per period per
  dims; chains (day -> month -> quarter); partial tail-period coverage
  (window_coverage kind); still rejected without `rollup_fold`;
  target-grain compatibility rule.
- compare: matched-prefix deltas with tail truncation recorded in
  `alignment_dump.to_date`; the four teaching errors (boundary,
  multi-period, grain mismatch, anchor mismatch); scalar elapsed-span
  check; all_history, mixed-anchor, and mixed cumulative/non-cumulative derived
  wrappers rejected; same-anchor MTD and trailing derived wrappers accepted;
  marker survives JSON reload; DeltaFrame carries the marker and component
  sidecar while attribute stays gated.
- Dialect compile tests for the three new SQL shapes — period-scoped
  first-seen, period-bounded seed, memtable-spine expansion join — on
  duckdb/trino/clickhouse, plus at least one cumulative-v2 case in each
  live integration suite.
- Agent surface: `describe(ms.grain_to_date)` / `describe(ms.trailing)`
  resolve; `ms.help('cumulative')` anchor examples run; the cumulative
  marker and `rollup_fold` survive `transform.window` and rollup;
  `contract()` / `show()` wording dispatches per anchor (all_history
  compare gated, grain_to_date / trailing conditional compare affordances,
  trailing autocorrelation caveat, rollup affordance present iff
  `rollup_fold`); `mv.help('transform')` reflects the rollup argument
  change; DeltaFrame `show()` / `contract()` state matched and tail bucket
  counts whenever to-date truncation occurred.

## Documentation Obligations (same change)

- `ms.help('cumulative')` anchor section + MTD / rolling-7d runnable
  examples; decision-order "when" text update.
- `docs/specs/semantic/python-semantic-layer.md` and
  `docs/specs/analysis/python-analysis-design.md` sections.
- `site/` example code, English and Chinese editions in sync.
- Rollup entry-point discoverability (entry-level change, same-change
  acceptance items): the `mv.help('transform')` matrix updates rollup's
  argument contract from required `drop_axes` to at-least-one-of
  `drop_axes` / `grain`; `SessionTransformNamespace.rollup`'s signature,
  docstring, and `describe` coverage gain the `grain` parameter and its
  aggregation dispatch; a grain-rollup example lands in the analysis
  examples (executed by pytest).
- Skill references: new grill points (reset-grain choice, window-span
  caliber, partial-window explanation) under `references/`, not SKILL.md.
- One-line pointer from the v1 design's V2 section to this spec.

## Out of Scope (v3+)

Calendar-variable trailing windows (sliding months), approximate distinct
sketches, cross-component query fusion, incremental caching, `first` /
`average` rollup folds, and attribute on to-date deltas.
